"""Frequency units, every ADIF band, and lightweight entry panels (no MainWindow)."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialogButtonBox, QMainWindow

from nwologger import i18n
from nwologger.core import bands, qso
from nwologger.core.db import LogDB
from nwologger.core.operators import OperatorSession
from nwologger.ui.prefs import Prefs

# An interior frequency for every ADIF band, from LF through submillimetre.
BAND_EXAMPLES = [
    ("2190m", 0.136), ("630m", 0.475), ("560m", 0.502), ("160m", 1.9), ("80m", 3.6),
    ("60m", 5.357), ("40m", 7.074), ("30m", 10.136), ("20m", 14.074), ("17m", 18.1),
    ("15m", 21.074), ("12m", 24.915), ("10m", 28.074), ("8m", 40.68), ("6m", 50.313),
    ("5m", 60.0), ("4m", 70.2), ("2m", 145.5), ("1.25m", 223.5), ("70cm", 436.795),
    ("33cm", 915.0), ("23cm", 1296.0), ("13cm", 2400.0), ("9cm", 3400.0), ("6cm", 5760.0),
    ("3cm", 10368.0), ("1.25cm", 24192.0), ("6mm", 47088.0), ("4mm", 76032.0),
    ("2.5mm", 122250.0), ("2mm", 134928.0), ("1mm", 241192.0), ("submm", 300100.0),
]


@pytest.mark.parametrize("band,mhz", BAND_EXAMPLES)
def test_all_adif_bands_normalize_and_export(band, mhz):
    normalized = qso.normalize({"freq": mhz, "freq_rx": f"{mhz / 1000:.12g} GHz"})
    assert normalized["band"] == normalized["band_rx"] == band
    record = qso.to_adif(normalized)
    assert record["BAND"] == record["BAND_RX"] == band.upper()
    assert float(record["FREQ"]) == pytest.approx(mhz)
    assert qso.from_adif(record)["freq_rx"] == pytest.approx(mhz)


@pytest.mark.parametrize("band,lo,hi", bands.BANDS)
def test_adif_band_boundaries(band, lo, hi):
    assert bands.band_for_freq(lo) == band
    assert bands.band_for_freq(hi) == band
    # Values just outside the band must not still match it, including adjacent 6m / 5m boundaries.
    assert bands.band_for_freq(math.nextafter(lo, -math.inf)) != band
    assert bands.band_for_freq(math.nextafter(hi, math.inf)) != band


@pytest.mark.parametrize("band,lo,hi", bands.BANDS)
@pytest.mark.parametrize("unit,multiplier", [("Hz", 1e6), ("kHz", 1e3), ("GHz", 1e-3)])
def test_unit_conversion_keeps_exact_adif_band_boundaries(band, lo, hi, unit, multiplier):
    for edge in (lo, hi):
        parsed = bands.parse_frequency(f"{edge * multiplier:.12g} {unit}")
        assert parsed == edge
        assert bands.band_for_freq(parsed) == band


@pytest.mark.parametrize("value,unit,mhz", [
    ("10.136", "MHz", 10.136), ("18,100", "MHz", 18.1), (" 24,915 MHz ", "MHz", 24.915),
    ("10136kHz", "MHz", 10.136), ("10.136 mHz", "MHz", 10.136),
    ("10136000 Hz", "MHz", 10.136), ("1.296ghz", "MHz", 1296),
    ("10,368 GHz", "MHz", 10368), (10000, "MHz", 10000),
    ("14025", "kHz", 14.025), ("475", "kHz", 0.475), ("144000", "kHz", 144),
    ("10.136 MHz", "kHz", 10.136), ("0.475MHz", "kHz", 0.475),
    ("1e4 MHz", "MHz", 10000), (".136", "MHz", 0.136), ("+14.025", "MHz", 14.025),
])
def test_parse_frequency_units(value, unit, mhz):
    assert bands.parse_frequency(value, default_unit=unit) == pytest.approx(mhz)


@pytest.mark.parametrize("value", [
    None, "", " ", "zero", "14 MHz extra", "1,234.567", "1.234,567", "14 THz", "14 074",
    "-14", "0", 0, -1, True, False, "NaN", "inf", float("inf"), float("nan"), "1e999 GHz",
    "1e1000000GHz", "1e10000000000000000000000GHz",
])
def test_parse_frequency_rejects_invalid_values(value):
    assert bands.parse_frequency(value) is None


def test_clean_frequency_agrees_with_live_parser_and_keeps_rx_independent():
    q = qso.normalize({"freq": "10136 kHz", "freq_rx": "24,915 MHz"})
    assert q["freq"] == pytest.approx(10.136)
    assert q["freq_rx"] == pytest.approx(24.915)
    assert (q["band"], q["band_rx"]) == ("30m", "12m")
    assert qso.normalize({"freq": 10368})["band"] == "3cm"
    assert bands.parse_frequency("1", default_unit="unsupported") is None
    for col in ("freq", "freq_rx"):
        with pytest.raises(ValueError):
            qso.clean(col, "-14 MHz")


@pytest.fixture
def log_and_prefs(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("auto_lookup", False)
    prefs.set("allow_dupes", True)
    prefs.set("rx_sync", False)
    yield db, prefs
    db.close()


@pytest.fixture
def dx_panel(qtbot, log_and_prefs):
    from nwologger.ui.entry.dx_panel import DXPanel

    db, prefs = log_and_prefs
    panel = DXPanel(db, prefs)
    qtbot.addWidget(panel)
    yield panel
    for timer in (panel._clock, panel._check_timer, panel._lookup_timer, panel._index_timer):
        timer.stop()
    db.remove_listener(panel._on_db_change)


def test_dx_live_labels_and_saved_qsos_cover_all_bands(dx_panel):
    panel = dx_panel
    assert len(BAND_EXAMPLES) == len(bands.BAND_NAMES) == 33
    for i, (band, mhz) in enumerate(BAND_EXAMPLES):
        rx_band, rx_mhz = BAND_EXAMPLES[(i + 1) % len(BAND_EXAMPLES)]
        panel.call.setText(f"BA1F{i}")
        panel.w["freq"].setText(str(mhz).replace(".", ","))
        panel.w["freq_rx"].setText(f"{rx_mhz * 1000:.12g} kHz")
        assert panel.band_lab.text() == band.upper()
        assert panel.band_rx_lab.text() == rx_band.upper()
        panel.commit()
        saved = panel.db.get(i + 1)
        assert saved["band"] == band
        assert saved["band_rx"] == rx_band
        assert saved["freq"] == pytest.approx(mhz)
        assert saved["freq_rx"] == pytest.approx(rx_mhz)


def test_dx_rx_sync_uses_the_same_frequency_parser(dx_panel):
    panel = dx_panel
    panel.rx_sync.setChecked(True)
    panel.w["freq"].setText("10136 kHz")
    assert panel.w["freq_rx"].text() == "10136 kHz"
    assert panel.band_lab.text() == panel.band_rx_lab.text() == "30M"
    normalized = qso.normalize(panel.values())
    assert normalized["freq"] == normalized["freq_rx"] == pytest.approx(10.136)
    panel.rx_sync.setChecked(False)
    panel.w["freq_rx"].setText("18,100 MHz")
    assert panel.band_lab.text() == "30M"
    assert panel.band_rx_lab.text() == "17M"


def test_dx_edit_preserves_six_decimal_frequency_boundaries(dx_panel):
    panel = dx_panel
    qid = panel.db.insert({"call": "BA1AAA", "freq": 54.000001, "freq_rx": 0.479, "mode": "CW",
                           "time_on": "2026-10-18T00:00:00Z"})
    panel.load_qso(qid)
    assert panel.w["freq"].text() == "54.000001"
    assert panel.band_lab.text() == "5M"
    assert panel.band_rx_lab.text() == "630M"
    panel.commit()
    saved = panel.db.get(qid)
    assert saved["freq"] == 54.000001 and saved["band"] == "5m"
    assert saved["freq_rx"] == 0.479 and saved["band_rx"] == "630m"


@pytest.fixture
def contest_panel(qtbot, log_and_prefs, tmp_path):
    from nwologger.core.contest.engine import ContestDef, load_all, save_user_def
    from nwologger.ui.contest.contest_panel import ContestPanel

    db, prefs = log_and_prefs
    definition = load_all()["DX-SERIAL"].to_dict()
    definition.update(id="FREQ-ALL", bands=list(bands.BAND_NAMES))
    save_user_def(ContestDef.from_dict(definition, user=True), tmp_path / "contests")
    sid = db.create_contest_session("FREQ-ALL", "Frequency test", "2026-10-18T00:00:00Z",
                                   {"band": "20m", "freq": "14025", "freq_unit": "kHz"}, None)
    prefs.set("current_contest", sid)
    window = QMainWindow()
    window.ops = OperatorSession()
    qtbot.addWidget(window)
    panel = ContestPanel(db, prefs, window)
    yield panel
    panel.shutdown()


def test_contest_frequency_defaults_and_entry_cover_all_bands(contest_panel):
    panel = contest_panel
    for band, mhz in BAND_EXAMPLES:
        panel.band.setCurrentIndex(panel.band.findData(band))
        assert bands.band_for_freq(panel.freq_mhz()) == band
        panel.freq.setText(f"{mhz * 1000:.12g}".replace(".", ","))
        panel._freq_set()
        assert panel.freq_mhz() == pytest.approx(mhz)
        assert panel._frequency_valid()
    panel.reload_sessions(select=panel.session["id"])
    assert panel.session["data"]["freq_unit"] == "kHz"
    assert panel.freq_mhz() == pytest.approx(BAND_EXAMPLES[-1][1])


def test_contest_call_frequency_command_supports_explicit_units(contest_panel):
    panel = contest_panel
    for text, band, mhz in (("14025", "20m", 14.025), ("10,136 MHz", "30m", 10.136),
                            ("1.296 GHz", "23cm", 1296), ("475", "630m", 0.475)):
        panel.call.setText(text)
        panel.enter(panel.call)
        assert not panel.call.text()
        assert panel.band.currentData() == band
        assert panel.freq_mhz() == pytest.approx(mhz)


def test_contest_rejects_invalid_and_ineligible_frequencies_without_replacing_them(contest_panel):
    panel = contest_panel
    panel.cdef.bands = ["20m", "40m"]
    panel._setup_entry()
    panel.call.setText("BA1AAA")
    panel.fields["rst"].setText("599")
    panel.fields["serial"].setText("1")
    messages = []
    panel.message.connect(messages.append)
    for text in ("10136", "99 MHz", "bad", "0", "-14 MHz"):
        panel.freq.setText(text)
        panel._freq_set()
        assert panel.freq.text() == text
        assert not panel._frequency_valid()
        panel.refresh_checks()
        assert not panel.log_btn.isEnabled()
        panel.commit(force=True)
        assert panel.db.count() == 0
        assert messages
    panel.freq.setText("7005")
    panel.commit()
    saved = panel.db.get(1)
    assert saved["band"] == "40m"
    assert saved["freq"] == pytest.approx(7.005)


def test_contest_legacy_session_units_and_editing_preserve_frequency(contest_panel):
    panel = contest_panel
    for text, band, expected in (("14025", "20m", 14.025), ("14.025", "20m", 14.025),
                                 ("144", "2m", 144), ("475", "630m", 0.475),
                                 ("10000", "3cm", 10000), ("144000", "2m", 144)):
        restored = panel._restored_frequency({"freq": text, "band": band})
        assert bands.parse_frequency(restored, default_unit="kHz") == pytest.approx(expected)
    qid = panel.db.insert({"call": "BA1AAA", "freq": 10.136, "band": "30m", "mode": "CW",
                           "rst_sent": "599", "rst_rcvd": "599", "srx": 1,
                           "time_on": "2026-10-18T00:00:00Z", "contest_session_id": panel.session["id"]})
    panel.edit_qso(qid)
    assert panel.band.currentData() == "30m"
    assert panel.freq_mhz() == pytest.approx(10.136)
    panel.fields["serial"].setText("1")
    panel.commit()
    assert panel.db.get(qid)["freq"] == pytest.approx(10.136)


@pytest.mark.parametrize("language", ["en", "zh_CN"])
def test_custom_contest_keeps_every_band_and_fits_small_window(qtbot, tmp_path, language):
    from nwologger.ui.contest.udc_dialog import UdcDialog
    from nwologger.ui.widgets import ScrollArea

    previous_language = i18n.lang()
    i18n.load(language)
    try:
        dialog = UdcDialog({}, tmp_path)
        qtbot.addWidget(dialog)
        dialog._new()
        assert list(dialog.f_bands) == bands.BAND_NAMES
        selected = {"2190m", "560m", "30m", "17m", "12m", "1.25cm", "submm"}
        for band, checkbox in dialog.f_bands.items():
            checkbox.setChecked(band in selected)
        assert set(dialog._form_to_json()["bands"]) == selected
        dialog._json_to_form(dialog._form_to_json())
        assert {b for b, cb in dialog.f_bands.items() if cb.isChecked()} == selected
        dialog.resize(780, 500)
        dialog.show()
        dialog.layout().activate()
        assert dialog.width() <= 780 and dialog.height() <= 500
        assert dialog.minimumSizeHint().width() < 780
        scroll = dialog.tabs.widget(0)
        assert isinstance(scroll, ScrollArea)
        scroll.ensureWidgetVisible(dialog.f_bands["submm"])
        assert scroll.viewport().rect().intersects(
            dialog.f_bands["submm"].rect().translated(dialog.f_bands["submm"].mapTo(scroll.viewport(),
                                                                                   dialog.f_bands["submm"].rect().topLeft())))
        buttons = dialog.findChild(QDialogButtonBox)
        assert not buttons.isHidden()
        dialog.tabs.setCurrentIndex(1)
        dialog.layout().activate()
        assert dialog.width() <= 780 and dialog.height() <= 500
    finally:
        i18n.load(previous_language)
