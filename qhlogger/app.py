"""QApplication setup: preferences, language, theme, log file, main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__, i18n
from .core import db as dbm

RESTART = 1000


def run(argv: list[str], *, db_path: str | None = None, lang: str | None = None, theme_mode: str | None = None
        ) -> int:
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = Path(__file__).resolve().parent / "resources" / "icons" / "radio.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    from .ui import theme  # noqa: PLC0415
    from .ui.main_window import MainWindow  # noqa: PLC0415
    from .ui.prefs import Prefs  # noqa: PLC0415

    while True:
        prefs = Prefs()
        language = lang or prefs.get("language")
        i18n.load(language)
        qt_tr = QTranslator(app)
        if language != "en" and qt_tr.load(QLocale(language), "qtbase", "_",
                                           QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(qt_tr)
        theme.apply(app, theme_mode or prefs.get("theme"))
        theme.watch_system(app)
        path = db_path or prefs.get("db_path") or str(dbm.DEFAULT_PATH)
        try:
            log = dbm.LogDB(path, backup_keep=prefs.get("backup_keep"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(None, APP_NAME, f"Could not open the log file:\n{path}\n\n{e}")
            return 1
        win = MainWindow(log, prefs, app)
        win.show()
        code = app.exec()
        win.deleteLater()
        app.removeTranslator(qt_tr)
        try:
            log.close()
        except Exception:  # noqa: BLE001 - already closed when switching files
            pass
        if code != RESTART:
            return code
        db_path = None  # take the newly chosen file from prefs


def main_gui() -> int:
    return run(sys.argv)
