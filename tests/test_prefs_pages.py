"""Preferences lists its pages down the left side, so every page stays in view at any window width or display
scaling; a tab bar hid Map and Sync positions behind scroll arrows."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from nwologger.core.db import LogDB
from nwologger.ui import prefs_dialog
from nwologger.ui.prefs import Prefs
from nwologger.ui.prefs_dialog import PrefsDialog
from nwologger.ui.widgets import SideTabs


def test_every_page_is_listed_and_reachable_in_a_narrow_window(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(prefs_dialog.tqsl, "station_locations", lambda: [])
    monkeypatch.setattr(prefs_dialog.tqsl, "find_tqsl", lambda: None)
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    try:
        dialog = PrefsDialog(db, prefs, page="Sync positions")
        qtbot.addWidget(dialog)
        dialog.resize(640, 480)
        dialog.show()
        qapp.processEvents()
        tabs = dialog.tabs
        assert isinstance(tabs, SideTabs) and tabs.count() >= 11
        titles = [tabs.tabText(i) for i in range(tabs.count())]
        assert titles[:2] == ["General", "Station profiles"] and titles[-2:] == ["Map", "Sync positions"]
        assert tabs.tabText(tabs.currentIndex()) == "Sync positions"  # page= opened it
        rows = tabs.list
        assert rows.horizontalScrollBar().maximum() == 0  # every title fits its width
        assert all(rows.visualItemRect(rows.item(i)).bottom() <= rows.viewport().height()
                   for i in range(rows.count()))  # and every page is in view without scrolling
        tabs.setCurrentIndex(0)
        assert tabs.currentWidget() is tabs.widget(0) and tabs.currentIndex() == 0
        tabs.setCurrentWidget(tabs.widget(3))
        assert tabs.currentIndex() == 3 and rows.currentRow() == 3
        rows.setCurrentRow(tabs.count() - 1)  # the list drives the pages
        assert tabs.currentWidget() is tabs.widget(tabs.count() - 1)
        dialog.reject()  # before the log closes: closing the dialog later would reach for it
    finally:
        db.close()
