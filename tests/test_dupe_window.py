"""The dupe warning window is one minute (0.1.8: 24 hours); an old setting is carried over in minutes."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from nwologger.core import dupes
from nwologger.ui.prefs import Prefs


def _prefs(tmp_path, **stored):
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    for k, v in stored.items():
        s.setValue(k, v)
    s.sync()
    return Prefs(s)


def test_default_window_is_one_minute(tmp_path):
    assert dupes.WARN_SECONDS == 60
    assert _prefs(tmp_path).get("warn_minutes") == 1 and _prefs(tmp_path).warn_seconds() == 60.0


def test_the_old_default_of_24_hours_becomes_one_minute_and_other_values_are_kept_in_minutes(tmp_path):
    p = _prefs(tmp_path, warn_hours=24)
    assert p.get("warn_minutes") == 1 and not p.s.contains("warn_hours")
    p = _prefs(tmp_path / "b", warn_hours=2)
    assert p.get("warn_minutes") == 120
    p = _prefs(tmp_path / "c", warn_hours="junk")
    assert p.get("warn_minutes") == 1
    p = _prefs(tmp_path / "d", warn_hours=24, warn_minutes=5)  # already migrated: untouched
    assert p.get("warn_minutes") == 5


def test_forty_seconds_apart_is_suspicious_and_three_hours_is_a_second_contact():
    a = {"call": "BI1XYZ", "band": "2m", "mode": "FM", "time_on": "2026-10-18T10:00:00Z"}
    near = {**a, "time_on": "2026-10-18T10:00:40Z"}
    later = {**a, "time_on": "2026-10-18T13:00:00Z"}
    assert dupes.classify(a, near)[0] == "SUSPECT"
    assert dupes.classify(a, {**near, "mode": "SSB", "submode": "USB"})[0] == "SUSPECT"  # related: both phone
    assert dupes.classify(a, {**near, "mode": "FT8", "submode": None}) is None  # unrelated modes
    assert dupes.classify(a, later) is None
    assert dupes.classify(a, {**a, "time_on": "2026-10-18T10:00:20Z"})[0] == "DUPE"
