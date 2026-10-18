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

from PySide6.QtCore import QEvent, QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__, applog, i18n
from .core import db as dbm

RESTART = 1000
log = logging.getLogger("nwologger.app")


def run(argv: list[str], *, db_path: str | None = None, lang: str | None = None, theme_mode: str | None = None,
        debug: bool = False) -> int:
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
    app = QApplication.instance() or QApplication(argv)
    applog.install_qt_messages()
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = Path(__file__).resolve().parent / "resources" / "brand" / "by1qh.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    from .ui import theme  # noqa: PLC0415
    from .ui.main_window import MainWindow  # noqa: PLC0415
    from .ui.prefs import Prefs  # noqa: PLC0415

    theme.watch_system(app)
    threading.Thread(target=eject_installer_image, name="eject", daemon=True).start()
    first_window = True  # the first-run checks (country file, call lists) run once per process, not after a restart
    while True:
        prefs = Prefs()
        language = lang or prefs.get("language")
        i18n.load(language)
        qt_tr = _qt_translator(app, language)
        theme.apply(app, theme_mode or prefs.get("theme"))
        path = db_path or prefs.get("db_path") or str(dbm.DEFAULT_PATH)
        applog.startup_banner(path)
        try:
            logdb = dbm.LogDB(path, backup_keep=prefs.get("backup_keep"), scheduled_keep=prefs.get("scheduled_keep"))
        except Exception as e:  # noqa: BLE001
            log.exception("Could not open the log file %s", applog.tidy_path(path))
            QMessageBox.critical(None, APP_NAME, f"Could not open the log file:\n{path}\n\n{e}")
            return 1
        with applog.timed("Window ready", at_least=0):
            win = MainWindow(logdb, prefs, app, first_run=first_window and not os.environ.get("NWOLOGGER_SELF_TEST"))
            win.show()
        first_window = False
        if logdb.newer_version:
            QMessageBox.warning(win, APP_NAME, i18n.tr("This log was last written by a newer NWOlogger") +
                                f" ({logdb.newer_version}). " +
                                i18n.tr("Some of its data may not show in this version, and what it writes stays "
                                        "readable to the newer one."))
        current = {"win": win, "qt_tr": qt_tr}

        def rebuild(old: MainWindow, prefs=prefs, current=current) -> None:
            """The language changed: the same log file and state, in a window built in the new language."""
            app.removeTranslator(current["qt_tr"])
            current["qt_tr"] = _qt_translator(app, prefs.get("language"))
            new = old.rebuilt()
            new.rebuild_requested.connect(lambda: rebuild(new))
            current["win"] = new

        win.rebuild_requested.connect(lambda w=win: rebuild(w))
        if os.environ.get("NWOLOGGER_SELF_TEST"):
            _self_test(win, os.environ["NWOLOGGER_SELF_TEST"])
        applog.start_watchdog()
        code = app.exec()
        applog.stop_watchdog()
        _tear_down(app, current)
        try:
            logdb.close()
        except Exception:  # noqa: BLE001 - already closed when switching files
            pass
        if code != RESTART:
            return code
        db_path = None  # take the newly chosen file from prefs


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
    window a few seconds after the start. The process must then exit with code 0 and leave no crash file."""
    if mode != "close":
        return
    from PySide6.QtCore import QTimer  # noqa: PLC0415

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
