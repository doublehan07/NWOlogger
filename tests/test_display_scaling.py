"""Controls remain reachable when display scaling leaves little logical screen space."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtWidgets import QDialogButtonBox, QPushButton

from nwologger import i18n
from nwologger.core.db import LogDB
from nwologger.ui import prefs_dialog, theme
from nwologger.ui.prefs import Prefs
from nwologger.ui.prefs_dialog import PrefsDialog


@pytest.fixture(params=["en", "zh_CN"])
def preferences_data(qapp, tmp_path, request, monkeypatch):
    language, old_theme = i18n.lang(), theme.mode()
    i18n.load(request.param)
    theme.apply(qapp, "light")
    monkeypatch.setattr(prefs_dialog.tqsl, "station_locations", lambda: [])
    monkeypatch.setattr(prefs_dialog.tqsl, "find_tqsl", lambda: None)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("language", request.param)
    prefs.set("theme", "light")
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "is_default": True})
    yield db, prefs
    db.close()
    i18n.load(language)
    theme.apply(qapp, old_theme)


def _rect_in(widget, ancestor):
    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


def _button(page, label):
    return next(button for button in page.findChildren(QPushButton) if button.text() == i18n.tr(label))


def _scroll_to_control(page, control):
    # Scroll the whole rectangle: ensureWidgetVisible intentionally prioritizes the caret for text fields.
    rect = _rect_in(control, page.widget())
    page.ensureVisible(rect.left(), rect.top(), 0, 0)
    page.ensureVisible(rect.right() + 1, rect.bottom() + 1, 0, 0)


def test_preferences_all_pages_fit_and_scroll(preferences_data, qtbot, qapp):
    dialog = PrefsDialog(*preferences_data)
    qtbot.addWidget(dialog)
    dialog.show()
    # A 1920-pixel-wide display at 250% scaling has roughly this much usable logical space.
    dialog.resize(780, 500)
    qapp.processEvents()
    assert dialog.width() <= 780 and dialog.height() <= 500

    bottom_controls = {
        "General": dialog.theme,
        "Station profiles": _button(dialog.profiles_page, "Save profile"),
        "Devices": _button(dialog.devices_page, "Remove setup"),
        "Logging": dialog.auto_lookup,
        "Lookup": dialog.ttl,
        "Services": dialog.gap,
        "Modes": dialog.mode_list,
        "Satellites": dialog.satmap,
        "Database": _button(dialog, "Show backups folder"),
        "Map": _button(dialog, "Clear map cache"),
        "Sync positions": dialog.positions_page.lock_btn,
    }
    button_box = dialog.findChild(QDialogButtonBox)
    for index in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(index)
        qapp.processEvents()
        page = dialog.tabs.currentWidget()
        title = dialog.tabs.tabText(index)
        control = next(control for name, control in bottom_controls.items() if i18n.tr(name) == title)
        _scroll_to_control(page, control)
        qapp.processEvents()
        assert control.isVisibleTo(dialog), title
        rect, view = _rect_in(control, page.viewport()), page.viewport().rect()
        # Scrolled into view: fully in height; in width it may exceed a viewport narrowed by the page list when
        # the font draws every glyph one em wide (Windows off screen), so it must only reach into the viewport.
        assert view.top() <= rect.top() and rect.bottom() <= view.bottom() and view.intersects(rect), title
        assert dialog.width() <= 780 and dialog.height() <= 500, title
        for standard in (QDialogButtonBox.StandardButton.Save, QDialogButtonBox.StandardButton.Cancel):
            button = button_box.button(standard)
            assert button.isVisibleTo(dialog), title
            assert dialog.rect().contains(_rect_in(button, dialog)), title

    # The longest forms must scroll rather than making every tab force a taller window.
    for name in ("Station profiles", "Services"):
        index = next(i for i in range(dialog.tabs.count()) if dialog.tabs.tabText(i) == i18n.tr(name))
        dialog.tabs.setCurrentIndex(index)
        qapp.processEvents()
        assert dialog.tabs.currentWidget().verticalScrollBar().maximum() > 0


def test_preferences_tab_focus_scrolls_to_last_profile_field(preferences_data, qtbot, qapp):
    dialog = PrefsDialog(*preferences_data, page="Station profiles")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.resize(780, 500)
    qapp.processEvents()
    page = dialog.tabs.currentWidget()
    previous = dialog.profiles_page.edits["lotw_location"]
    last = dialog.profiles_page.edits["eqsl_qth_nickname"]
    previous.setFocus()
    page.verticalScrollBar().setValue(0)
    qtbot.keyClick(previous, Qt.Key.Key_Tab)
    qapp.processEvents()
    assert last.hasFocus()
    caret = last.cursorRect()
    caret.moveTopLeft(last.mapTo(page.viewport(), caret.topLeft()))
    assert page.viewport().rect().contains(caret)


@pytest.mark.parametrize("page", ["Station profiles", "Services", "Database"])
def test_preferences_requested_page(preferences_data, qtbot, page):
    dialog = PrefsDialog(*preferences_data, page=page)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == i18n.tr(page)
