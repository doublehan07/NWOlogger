"""QApplication setup: preferences, language, theme, log file, main window."""

from __future__ import annotations

import gc
import logging
import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QLibraryInfo, QLocale, QTimer, QTranslator, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__, applog, i18n, instance
from .core import db as dbm
from .core import lease, logcopy, mirror, station

RESTART = 1000
RELEASES_URL = "https://github.com/doublehan07/NWOlogger/releases"
log = logging.getLogger("nwologger.app")


def run(argv: list[str], *, db_path: str | None = None, lang: str | None = None, theme_mode: str | None = None,
        debug: bool = False) -> int:
    station.configure(db_path)  # the station folder decides where the diagnostic log goes
    applog.setup(debug=debug or bool(os.environ.get("NWOLOGGER_DEBUG")))
    applog.install_hooks()
    try:
        return _run(argv, db_path=db_path, lang=lang, theme_mode=theme_mode)
    except Exception:
        log.exception("NWOlogger stopped with an error")
        raise
    finally:
        applog.shutdown()


def installer_mounts(info: dict, running_from: Path) -> list[str]:
    """Mount points of NWOlogger disk images (``NWOlogger….dmg`` holding NWOlogger.app) in ``hdiutil info -plist``
    output, unless the app runs from one of them."""
    out = []
    for image in info.get("images", []):
        name = Path(str(image.get("image-path", ""))).name
        if not (name.startswith(APP_NAME) and name.lower().endswith(".dmg")):
            continue
        for entity in image.get("system-entities", []):
            mount = entity.get("mount-point")
            if not mount or not (Path(mount) / f"{APP_NAME}.app").is_dir():
                continue
            if running_from == Path(mount) or Path(mount) in running_from.parents:
                continue
            out.append(mount)
    return out


def eject_installer_image() -> None:
    """macOS: once the app has been dragged to Applications and started from there, eject the disk image it came
    from (closing the image's window does not). Nothing happens when it runs from the image itself."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return
    running_from = Path(sys.executable).resolve()
    if "/AppTranslocation/" in str(running_from):
        return  # started from the image, through Gatekeeper's copy
    try:
        info = plistlib.loads(subprocess.run(["/usr/bin/hdiutil", "info", "-plist"], capture_output=True,
                                             timeout=20, check=True).stdout)
        for mount in installer_mounts(info, running_from):
            done = subprocess.run(["/usr/bin/hdiutil", "detach", mount], capture_output=True, timeout=60)
            if done.returncode == 0:
                log.info("Ejected the installer disk image at %s", mount)
            else:
                log.info("Could not eject the installer disk image at %s: %s", mount,
                         done.stderr.decode(errors="replace").strip())
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
        log.debug("Looking for the installer disk image failed", exc_info=True)


def _qt_translator(app: QApplication, language: str) -> QTranslator:
    """Qt's own texts (OK, Cancel, the colour and file dialogs) in ``language``."""
    qt_tr = QTranslator(app)
    if language != "en" and qt_tr.load(QLocale(language), "qtbase", "_",
                                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt_tr)
    return qt_tr


def _run(argv: list[str], *, db_path: str | None, lang: str | None, theme_mode: str | None) -> int:
    # One running NWOlogger per user: a second launch asks the first to come to the front and quits (instance.py),
    # before any of Qt is set up, so it is gone within a second.
    server = instance.claim(db_path)
    if server is None:
        return 0
    app = QApplication.instance() or QApplication(argv)
    applog.install_qt_messages()
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = Path(__file__).resolve().parent / "resources" / "brand" / "by1qh.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    from .ui import migrate, theme  # noqa: PLC0415
    from .ui.main_window import MainWindow  # noqa: PLC0415
    from .ui.prefs import Prefs  # noqa: PLC0415

    theme.watch_system(app)
    threading.Thread(target=eject_installer_image, name="eject", daemon=True).start()
    shown: dict = {}  # the window that answers a "come to the front" request; none between restarts
    server.raise_requested.connect(lambda path: shown["win"].bring_to_front(path) if shown.get("win") else None)
    heartbeat = QTimer()
    heartbeat.setInterval(60_000)
    heartbeat.timeout.connect(lease.heartbeat_all)  # the lock file's time, for the other-computer hint
    heartbeat.start()
    migrated: Path | None = None
    try:
        logcopy.sweep_partials(station.folder())
        # The one-time move from Documents/NWOlogger (0.1.x) into the station folder; its language is the old one's.
        if db_path is None:
            old = migrate.needed()
            if old is not None:
                i18n.load(lang or str(Prefs.native_store().value("language") or "en"))
                migrated = migrate.run(None, old)
                if migrated is None:
                    return 0
            elif not station.settings_path().exists():
                Prefs.migrate_native()
        first_window = True  # the first-run checks (country file, call lists) run once per process, not after a restart
        while True:
            prefs = Prefs()
            language = lang or prefs.get("language")
            i18n.load(language)
            qt_tr = _qt_translator(app, language)
            theme.apply(app, theme_mode or prefs.get("theme"))
            path = str(Path(db_path)) if db_path else str(station.folder() / (prefs.get("current_log") or station.LOG_NAME))
            applog.startup_banner(path)
            other = lease.other_computer(Path(path).parent)
            if other is not None and not _continue_despite(other):
                return 0
            try:
                logdb = dbm.LogDB(path, backup_keep=prefs.get("backup_keep"), scheduled_keep=prefs.get("scheduled_keep"))
            except dbm.LeaseHeld as e:
                QMessageBox.critical(None, APP_NAME, i18n.tr("This log is in use by another NWOlogger") +
                                     lease.describe(e.holder) + f"\n{e.folder}\n\n" +
                                     i18n.tr("Close it first, or bring its window to the front."))
                return 1
            except dbm.NewerLog as e:
                _newer_log_dialog(e)
                return 1
            except Exception as e:  # noqa: BLE001
                log.exception("Could not open the log file %s", applog.tidy_path(path))
                QMessageBox.critical(None, APP_NAME, f"Could not open the log file:\n{path}\n\n{e}")
                return 1
            with applog.timed("Window ready", at_least=0):
                win = MainWindow(logdb, prefs, app, first_run=first_window and not os.environ.get("NWOLOGGER_SELF_TEST"))
                win.show()
            shown["win"] = win
            first_window = False
            if migrated is not None:
                copy = mirror.write_if_changed(logdb)  # the first backup copy, into Documents, at once
                migrate.tell(win, logdb.path, copy)
                migrated = None
            current = {"win": win, "qt_tr": qt_tr}

            def rebuild(old: MainWindow, prefs=prefs, current=current) -> None:
                """The language changed: the same log file and state, in a window built in the new language."""
                app.removeTranslator(current["qt_tr"])
                current["qt_tr"] = _qt_translator(app, prefs.get("language"))
                new = old.rebuilt()
                new.rebuild_requested.connect(lambda: rebuild(new))
                current["win"] = new
                shown["win"] = new  # a second launch must raise the new window, not the closed one

            win.rebuild_requested.connect(lambda w=win: rebuild(w))
            if os.environ.get("NWOLOGGER_SELF_TEST"):
                _self_test(win, os.environ["NWOLOGGER_SELF_TEST"])
            applog.start_watchdog()
            code = app.exec()
            applog.stop_watchdog()
            shown["win"] = None
            _tear_down(app, current)
            try:
                if db_path is None:
                    mirror.write_if_changed(logdb)  # the backup copy in Documents, before the log closes
                logdb.close()
            except Exception:  # noqa: BLE001 - already closed when switching files
                pass
            if code != RESTART:
                return code
            db_path = None  # take the newly chosen file from prefs

    finally:
        heartbeat.stop()
        server.close()


def _continue_despite(other: dict) -> bool:
    """A cloud-synced folder whose lock file says another computer used it minutes ago: ask before going on."""
    box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME,
                      i18n.tr("This log seems to be in use on another computer") + lease.describe(other) + ".\n\n" +
                      i18n.tr("Using one log from two computers at once through a synced folder can lose QSOs. "
                              "Continue anyway?"))
    go = box.addButton(i18n.tr("Continue"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(i18n.tr("Quit"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is go


def _newer_log_dialog(e: dbm.NewerLog) -> None:
    """The version gate: a log written by a newer NWOlogger is not opened; update instead."""
    box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME,
                      i18n.tr("This log was written by a newer NWOlogger") + f" ({e.version or '?'}).\n{e.path}\n\n" +
                      i18n.tr("This version") + f" ({__version__}) " +
                      i18n.tr("does not know its structure and has not touched it. Please update NWOlogger."))
    download = box.addButton(i18n.tr("Open the download page"), QMessageBox.ButtonRole.ActionRole)
    box.addButton(i18n.tr("Quit"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is download:
        QDesktopServices.openUrl(QUrl(RELEASES_URL))


def _tear_down(app: QApplication, current: dict) -> None:
    """Destroy the window, its dialogs and the worker pools while the application object still exists. Left to the
    interpreter's exit, they went in whatever order the garbage collector chose, after the QApplication was gone,
    which is how a run that had opened Preferences could end in a crash after its last log line."""
    from .ui import widgets  # noqa: PLC0415
    from .ui.stats import globeraster  # noqa: PLC0415

    win = current.pop("win", None)
    if win is not None:
        if win.isVisible() and not getattr(win, "_shut", False):
            win.close()  # the layout is saved and the listeners stopped by its closeEvent
        win.hide()
        win.deleteLater()
    qt_tr = current.pop("qt_tr", None)
    if qt_tr is not None:
        app.removeTranslator(qt_tr)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # the window and everything parented to it go now
    widgets.drop_tasks()  # background work still running keeps no Qt object alive
    globeraster.shutdown()
    del win
    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _self_test(win, mode: str) -> None:
    """``NWOLOGGER_SELF_TEST=close`` (the packaged smoke test and a test): open Preferences, close it, then close the
    window a few seconds after the start. The process must then exit with code 0 and leave no crash file.
    ``linger``: keep the window open for 20 s, then close it (the test that starts a second NWOlogger meanwhile)."""
    from PySide6.QtCore import QTimer  # noqa: PLC0415

    if mode == "linger":
        QTimer.singleShot(20000, win, lambda: (log.info("Self-test: closing the window"), win.close()))
        return
    if mode != "close":
        return
    from .ui.prefs_dialog import PrefsDialog  # noqa: PLC0415

    state: dict = {}

    def open_prefs() -> None:
        state["dlg"] = dlg = PrefsDialog(win.db, win.prefs, win)
        dlg.show()
        log.info("Self-test: Preferences opened")

    def close_prefs() -> None:
        state["dlg"].reject()
        log.info("Self-test: Preferences closed")

    def close_window() -> None:
        log.info("Self-test: closing the window")
        win.close()

    QTimer.singleShot(2000, win, open_prefs)
    QTimer.singleShot(3500, win, close_prefs)
    QTimer.singleShot(5000, win, close_window)


def main_gui() -> int:
    return run(sys.argv)
