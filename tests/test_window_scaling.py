"""Main-window controls remain reachable on a small logical desktop."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings
from PySide6.QtWidgets import QMainWindow, QPushButton, QWidget

from nwologger import i18n
from nwologger.core.db import LogDB
from nwologger.ui import theme
from nwologger.ui.main_window import MainWindow
from nwologger.ui.prefs import Prefs
from nwologger.ui.widgets import ScrollArea, fit_to_screen


@pytest.fixture(params=["en", "zh_CN"])
def make_window(qapp, qtbot, tmp_path, request):
    language, old_theme = i18n.lang(), theme.mode()
    i18n.load(request.param)
    theme.apply(qapp, "light")
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("auto_lookup", False)
    prefs.set("map_provider", "none")
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "is_default": True})
    windows = []

    def make(geometry=None):
        if geometry is not None:
            prefs.set_value("window/geometry", geometry)
        window = MainWindow(db, prefs, qapp, first_run=False)
        qtbot.addWidget(window)
        windows.append(window)
        return window

    yield make
    for window in windows:
        window.close()
        window.shutdown()
    db.close()
    i18n.load(language)
    theme.apply(qapp, old_theme)


def _rect_in(widget, ancestor):
    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


def _assert_reachable(scroll, control, qapp):
    scroll.ensureWidgetVisible(control, 0, 0)
    qapp.processEvents()
    assert control.isVisibleTo(scroll)
    assert scroll.viewport().rect().contains(_rect_in(control, scroll.viewport()))


def test_main_window_first_show_fits_available_desktop(make_window, qtbot):
    window = make_window()
    window.show()
    qtbot.waitUntil(lambda: window.screen().availableGeometry().contains(window.frameGeometry()))
    assert not window.isMaximized()


@pytest.mark.parametrize("size", [(780, 500), (800, 600)])
def test_main_window_small_resize_keeps_entry_and_all_tabs_reachable(make_window, qapp, size):
    window = make_window()
    window.show()
    qapp.processEvents()
    window.resize(*size)
    qapp.processEvents()
    assert window.width() <= size[0] and window.height() <= size[1]
    small_size = window.size()
    assert isinstance(window.entry_scroll, ScrollArea)
    _assert_reachable(window.entry_scroll, window.dx.call, qapp)
    assert window.entry_scroll.verticalScrollBar().maximum() > 0
    # The button row sits under the scroll area, so Log QSO is in view without scrolling (0.1.9c).
    assert window.dx.log_btn.isVisible()
    assert window.entry_pane.rect().contains(_rect_in(window.dx.log_btn, window.entry_pane))

    controls = (
        (window.filter.call, window.filter.clear_btn),
        (window.paper.view_combo, window.paper.findChildren(QPushButton)[-1]),
        tuple(window.tabs.widget(2).findChildren(QPushButton)),
        (window.stats.band, window.stats.region,
         next(button for button in window.stats.findChildren(QPushButton)
              if button.text() == i18n.tr("Save as PNG"))),
    )
    assert window.tabs.count() == len(controls)
    for index, page_controls in enumerate(controls):
        window.tabs.setCurrentIndex(index)
        qapp.processEvents()
        page = window.tabs.currentWidget()
        assert isinstance(page, ScrollArea)
        assert page_controls
        for control in page_controls:
            _assert_reachable(page, control, qapp)
        assert window.size() == small_size, window.tabs.tabText(index)


def test_main_window_restored_oversized_geometry_fits_available_desktop(make_window, qtbot, qapp):
    old_window = QMainWindow()
    qtbot.addWidget(old_window)
    bounds = qapp.primaryScreen().availableGeometry()
    old_window.setGeometry(bounds.right() + 200, bounds.bottom() + 200,
                           bounds.width() * 2, bounds.height() * 2)
    window = make_window(old_window.saveGeometry())
    window.show()
    qtbot.waitUntil(lambda: window.screen().availableGeometry().contains(window.frameGeometry()))
    assert not window.isMaximized()


def test_fit_to_screen_preserves_valid_normal_geometry(qtbot, qapp):
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    qapp.processEvents()
    bounds = window.screen().availableGeometry()
    window.setGeometry(bounds.x() + 40, bounds.y() + 40,
                       min(400, bounds.width() // 2), min(300, bounds.height() // 2))
    qapp.processEvents()
    before = window.geometry()
    assert bounds.contains(window.frameGeometry())
    fit_to_screen(window)
    assert window.geometry() == before


def test_fit_to_screen_preserves_maximized_state(qtbot, qapp):
    window = QWidget()
    qtbot.addWidget(window)
    window.showMaximized()
    qapp.processEvents()
    before = window.geometry()
    fit_to_screen(window)
    assert window.isMaximized()
    assert window.geometry() == before
