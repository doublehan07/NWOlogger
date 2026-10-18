"""Preferences must resolve unsaved station edits before either closing action."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from nwologger import i18n
from nwologger.core.db import LogDB
from nwologger.ui import prefs_dialog, theme
from nwologger.ui.prefs import Prefs
from nwologger.ui.prefs_dialog import PrefsDialog


@pytest.fixture
def dialog(qapp, qtbot, tmp_path, monkeypatch):
    old_language, old_theme = i18n.lang(), theme.mode()
    i18n.load("en")
    theme.apply(qapp, "light")
    monkeypatch.setattr(prefs_dialog.tqsl, "station_locations", lambda: [])
    monkeypatch.setattr(prefs_dialog.tqsl, "find_tqsl", lambda: None)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("theme", "light")
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "Home", "station_callsign": "BI1ZZZ", "operator": "BI1ZZZ",
                     "station_type": "personal", "is_default": True})
    widget = PrefsDialog(db, prefs, page="Station profiles")
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    # Tests that cancel the confirmation intentionally leave pending edits in the dialog.
    QDialog.reject(widget)
    db.close()
    i18n.load(old_language)
    theme.apply(qapp, old_theme)


def _click(dialog, action):
    dialog.findChild(QDialogButtonBox).button(getattr(QDialogButtonBox.StandardButton, action)).click()


def _unexpected_prompt(*_args):
    raise AssertionError("Unchanged station profiles should not prompt")


@pytest.mark.parametrize("action", ["Save", "Cancel"])
@pytest.mark.parametrize("choice", [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Discard,
                                   QMessageBox.StandardButton.Cancel])
def test_preferences_buttons_resolve_pending_station_changes(dialog, monkeypatch, action, choice):
    profile = dialog.profiles_page
    profile.edits["my_gridsquare"].setText("OM89AA")
    old_block = dialog.prefs.get("block_seconds")
    dialog.block.setValue(old_block + 1)
    prompts = []

    def answer(*args):
        prompts.append(args)
        return choice

    monkeypatch.setattr(QMessageBox, "question", answer)
    _click(dialog, action)
    assert len(prompts) == 1
    assert "modified but not saved" in prompts[0][2]
    assert dialog.db.default_profile().get("my_gridsquare") == (
        "OM89aa" if choice == QMessageBox.StandardButton.Save else None)  # saved as ADIF writes a grid
    proceeded = choice != QMessageBox.StandardButton.Cancel
    assert dialog.isVisible() == (not proceeded)
    assert dialog.prefs.get("block_seconds") == old_block + int(proceeded and action == "Save")
    if proceeded:
        assert dialog.result() == (QDialog.DialogCode.Accepted if action == "Save" else QDialog.DialogCode.Rejected)
    else:
        assert profile.edits["my_gridsquare"].text() == "OM89AA"


@pytest.mark.parametrize("action", ["Save", "Cancel"])
def test_unchanged_profile_does_not_prompt(dialog, monkeypatch, action):
    monkeypatch.setattr(QMessageBox, "question", _unexpected_prompt)
    _click(dialog, action)
    assert not dialog.isVisible()


@pytest.mark.parametrize("action", ["Save", "Cancel"])
def test_explicitly_saved_profile_does_not_prompt_again(dialog, monkeypatch, action):
    profile = dialog.profiles_page
    profile.edits["my_gridsquare"].setText("OM89AA")
    assert profile._save()
    assert not profile.has_unsaved_changes()
    monkeypatch.setattr(QMessageBox, "question", _unexpected_prompt)
    _click(dialog, action)
    assert not dialog.isVisible()
    assert dialog.db.default_profile()["my_gridsquare"] == "OM89aa"


@pytest.mark.parametrize("action", ["Save", "Cancel"])
def test_invalid_profile_stays_open_after_save_choice(dialog, monkeypatch, action):
    old_profile = dialog.db.default_profile()
    old_block = dialog.prefs.get("block_seconds")
    dialog.profiles_page.edits["station_callsign"].clear()
    dialog.block.setValue(old_block + 1)
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    _click(dialog, action)
    assert dialog.isVisible()
    assert dialog.profiles_page.has_unsaved_changes()
    assert dialog.db.default_profile() == old_profile
    assert dialog.prefs.get("block_seconds") == old_block
    assert len(warnings) == 1
    assert warnings[0][2] == "Enter the station callsign."


def test_restored_profile_text_does_not_prompt(dialog, monkeypatch):
    profile = dialog.profiles_page
    profile.edits["name"].setText("Portable")
    assert profile.has_unsaved_changes()
    profile.edits["name"].setText("  Home  ")
    assert not profile.has_unsaved_changes()
    monkeypatch.setattr(QMessageBox, "question", _unexpected_prompt)
    _click(dialog, "Cancel")


@pytest.mark.parametrize("field", ["station_type", "default"])
def test_non_text_profile_changes_are_prompted(dialog, monkeypatch, field):
    profile = dialog.profiles_page
    if field == "station_type":
        profile.station_type.setCurrentIndex(profile.station_type.findData("club"))
    else:
        profile.default.setChecked(False)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    _click(dialog, "Cancel")
    assert dialog.isVisible()
    assert profile.has_unsaved_changes()


@pytest.mark.parametrize("close_method", ["escape", "window"])
def test_other_close_paths_also_protect_pending_edits(dialog, monkeypatch, qtbot, close_method):
    dialog.profiles_page.edits["name"].setText("Portable")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    if close_method == "escape":
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        assert not dialog.close()
    assert dialog.isVisible()


def test_confirmation_cancel_returns_to_station_tab(dialog, monkeypatch):
    dialog.profiles_page.edits["name"].setText("Portable")
    dialog.tabs.setCurrentIndex(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    _click(dialog, "Save")
    assert dialog.tabs.currentWidget().widget() is dialog.profiles_page
    assert dialog.isVisible()


def test_cancelled_close_keeps_theme_preview(dialog, monkeypatch):
    dialog.profiles_page.edits["name"].setText("Portable")
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    _click(dialog, "Cancel")
    assert dialog.isVisible()
    assert theme.tokens().name == "dark"
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)
    _click(dialog, "Cancel")
    assert not dialog.isVisible()
    assert dialog.db.default_profile()["name"] == "Portable"
    assert theme.tokens().name == "light"


def test_new_blank_profile_only_prompts_after_editing(dialog, monkeypatch):
    profile = dialog.profiles_page
    profile._new()
    assert not profile.has_unsaved_changes()
    profile.edits["station_callsign"].setText("BY1QQ")
    profile.station_type.setCurrentIndex(profile.station_type.findData("club"))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)
    _click(dialog, "Save")
    profiles = dialog.db.profiles()
    assert len(profiles) == 2
    new = next(p for p in profiles if p["station_callsign"] == "BY1QQ")
    assert new["station_type"] == "club"
    assert new["operator"] is None
