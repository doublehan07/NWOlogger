"""Satellite imagery tiles behind the grid map: the providers, a disk cache and a background fetcher.

Tiles are Web Mercator (256 px, zoom 0 = the whole world in one tile). Only imagery is used: the grid squares are the
map's information. A tile is asked for once; it is kept in the cache folder (oldest removed first above
:data:`CACHE_MAX`), so a view seen before opens without the network. Requests go through :mod:`net`, so they only reach
the provider's own HTTPS hosts. With ``NWOLOGGER_OFFLINE`` set nothing is fetched.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import net

log = logging.getLogger("nwologger.map")

CACHE_MAX = 300 * 1024 * 1024
CACHE_KEEP = 270 * 1024 * 1024  # what a prune goes down to
WORKERS = 6
GIVE_UP = 12  # this many failures in a row: stop asking this provider for the session


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    service: str  # net.HOSTS key
    min_zoom: int
    max_zoom: int
    credit: str
    needs_key: bool = False

    def url(self, z: int, x: int, y: int, api_key: str | None = None) -> str | None:
        """The tile's address, or None when the provider needs a key that isn't set."""
        if self.key == "nasa":
            return ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/"
                    f"GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg")
        if self.key == "tianditu":
            if not api_key:
                return None
            return (f"https://t{(x + y) % 8}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                    f"&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                    f"&tk={api_key}")
        return None


# NASA's zoom 0 tile is blank (a GIBS quirk), so both start at 1. Imagery beyond zoom 12 adds nothing for squares.
NASA = Provider("nasa", "NASA Blue Marble", "gibs", 1, 8,
                "Imagery: NASA Earth Observatory Blue Marble Next Generation, served through NASA GIBS")
TIANDITU = Provider("tianditu", "Tianditu", "tianditu", 1, 12, "Imagery © 天地图 Tianditu", needs_key=True)
PROVIDERS = {p.key: p for p in (NASA, TIANDITU)}


def choose(setting: str, *, language: str, region: str, has_key: bool) -> Provider | None:
    """The provider for the Map setting: ``auto``, ``nasa``, ``tianditu`` or ``none``.

    ``auto`` uses Tianditu when a key is set and either the UI language starts with ``zh`` or the system region is
    ``CN``; otherwise it uses NASA, which needs no key.
    """
    if setting == "none":
        return None
    if setting in PROVIDERS:
        return PROVIDERS[setting]
    return TIANDITU if has_key and (language.startswith("zh") or region == "CN") else NASA


# ------------------------------------------------------------------ cache
def cache_dir() -> Path:
    env = os.environ.get("NWOLOGGER_CACHE_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "NWOlogger"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "NWOlogger" / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "nwologger"


def tile_path(provider: str, z: int, x: int, y: int) -> Path:
    return cache_dir() / "tiles" / provider / str(z) / str(x) / f"{y}.jpg"


def cached(provider: str, z: int, x: int, y: int) -> bytes | None:
    p = tile_path(provider, z, x, y)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    try:
        os.utime(p)  # recently used: pruned last
    except OSError:
        pass
    return data or None


def cache_size() -> int:
    root = cache_dir() / "tiles"
    return sum(f.stat().st_size for f in root.rglob("*.jpg")) if root.exists() else 0


def prune(limit: int = CACHE_MAX, keep: int = CACHE_KEEP) -> int:
    """Remove the least recently used tiles while the cache is over ``limit``. Returns the files removed."""
    root = cache_dir() / "tiles"
    if not root.exists():
        return 0
    files = [(f.stat().st_mtime, f.stat().st_size, f) for f in root.rglob("*.jpg")]
    total = sum(s for _m, s, _f in files)
    if total <= limit:
        return 0
    n = 0
    for _m, size, f in sorted(files):
        if total <= keep:
            break
        try:
            f.unlink()
            total -= size
            n += 1
        except OSError:
            pass
    return n


def clear() -> int:
    """Remove every cached tile. Returns the bytes freed."""
    root = cache_dir() / "tiles"
    freed = 0
    if root.exists():
        for f in root.rglob("*.jpg"):
            try:
                freed += f.stat().st_size
                f.unlink()
            except OSError:
                pass
    return freed


# ------------------------------------------------------------------ fetching
class Fetcher:
    """Fetches tiles on daemon worker threads (quitting never waits for them); ``on_tile(provider, z, x, y, data)``
    runs on a worker thread, and the UI hands it to its own. Each tile is asked for once per session, and only while
    the provider keeps answering. The newest requests go first, so the view you stopped at fills in first."""

    def __init__(self, on_tile: Callable[[str, int, int, int, bytes], None]):
        self.on_tile = on_tile
        self._queue: queue.LifoQueue = queue.LifoQueue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._asked: set[tuple] = set()
        self._pending = 0  # tiles asked for and not yet answered (loaded, from the cache, failed or skipped)
        self._local = threading.local()
        self.failures_in_row: dict[str, int] = {}
        self.stopped: dict[str, str] = {}  # provider → why it is no longer asked
        self.stats = {"loaded": 0, "cached": 0, "failed": 0, "bytes": 0}
        self._since_prune = 0
        self.offline = bool(os.environ.get("NWOLOGGER_OFFLINE"))

    def want(self, provider: Provider, z: int, x: int, y: int, api_key: str | None = None) -> None:
        """Ask for a tile (from the cache first). Asking again for the same tile does nothing."""
        k = (provider.key, z, x, y)
        with self._lock:
            if k in self._asked or provider.key in self.stopped:
                return
            self._asked.add(k)
            self._pending += 1
            if len(self._threads) < WORKERS:
                t = threading.Thread(target=self._work, name="tiles", daemon=True)
                self._threads.append(t)
                t.start()
        self._queue.put((provider, z, x, y, api_key))

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._get(*job)
            except Exception:  # noqa: BLE001 - one tile must never stop the worker
                log.debug("Map tile failed", exc_info=True)
            finally:
                with self._lock:
                    self._pending -= 1

    def pending(self) -> int:
        """Tiles asked for that have no answer yet."""
        with self._lock:
            return self._pending

    def _client(self, provider: Provider):
        clients = getattr(self._local, "clients", None)
        if clients is None:
            clients = self._local.clients = {}
        if provider.key not in clients:
            clients[provider.key] = net.client(provider.service, timeout=20.0)
        return clients[provider.key]

    def _get(self, provider: Provider, z: int, x: int, y: int, api_key: str | None) -> None:
        data = cached(provider.key, z, x, y)
        if data:
            with self._lock:
                self.stats["cached"] += 1
            self.on_tile(provider.key, z, x, y, data)
            return
        url = provider.url(z, x, y, api_key)
        if url is None or self.offline:
            return
        try:
            with net.in_flight([api_key]):
                r = self._client(provider).get(url)
                r.raise_for_status()
            data = r.content
            if not data or not r.headers.get("content-type", "").startswith("image/"):
                raise ValueError("not an image")
        except Exception as e:  # noqa: BLE001 - a missing tile only leaves the grid on its own
            msg = (f"{provider.name}: the answer was not an image" if isinstance(e, ValueError)
                   else net.describe(provider.service, e))
            with self._lock:
                self.stats["failed"] += 1
                n = self.failures_in_row[provider.key] = self.failures_in_row.get(provider.key, 0) + 1
                if n >= GIVE_UP and provider.key not in self.stopped:
                    self.stopped[provider.key] = msg
                    log.warning("Map imagery from %s stopped after %d failures in a row: %s", provider.name, n, msg)
            return
        path = tile_path(provider.key, z, x, y)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError:
            pass
        with self._lock:
            self.failures_in_row[provider.key] = 0
            self.stats["loaded"] += 1
            self.stats["bytes"] += len(data)
            self._since_prune += 1
            prune_now = self._since_prune >= 200
            if prune_now:
                self._since_prune = 0
        if prune_now:
            prune()
        self.on_tile(provider.key, z, x, y, data)

    def summary(self) -> str:
        s = self.stats
        return (f"{s['loaded']} loaded ({s['bytes'] / 1e6:.1f} MB), {s['cached']} from the cache, {s['failed']} failed")

    def log_summary(self, provider: Provider | None) -> None:
        """One line per map session, written when the map is left (only when something was asked for)."""
        s = self.stats
        if provider is not None and (s["loaded"] or s["cached"] or s["failed"]):
            log.info("Map tiles (%s): %s", provider.name, self.summary())
        self.stats = {"loaded": 0, "cached": 0, "failed": 0, "bytes": 0}

    def forget(self) -> None:
        """Ask again for tiles that failed (after a key or provider change)."""
        with self._lock:
            self._asked.clear()
            self.stopped.clear()
            self.failures_in_row.clear()

    def shutdown(self) -> None:
        """Drop the tiles still waiting; the ones being fetched end on their own."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        for _t in self._threads:
            self._queue.put(None)
