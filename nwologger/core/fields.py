"""Field registry: one entry per QSO column.

Each field knows its SQLite column, ADIF tag, value kind, English label and group. The DB schema, ADIF
conversion, log table columns, bulk edit and search all read from here.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kinds: text, int, real, time (ISO datetime), date (ISO date), flag (ADIF enum letter)


@dataclass(frozen=True)
class Field:
    col: str
    adif: str | None
    kind: str
    label: str
    group: str
    width: int = 80
    bulk: bool = True  # offered in bulk edit
    internal: bool = False  # not exported, not shown by default


F = Field

FIELDS: list[Field] = [
    # Identity
    F("id", None, "int", "ID", "Identity", 50, bulk=False, internal=True),
    F("uuid", None, "text", "UUID", "Identity", 120, bulk=False, internal=True),
    F("created_at", None, "time", "Created", "Identity", 150, bulk=False, internal=True),
    F("updated_at", None, "time", "Updated", "Identity", 150, bulk=False, internal=True),
    # Core
    F("call", "CALL", "text", "Call", "Contact", 90),
    F("time_on", None, "time", "Time On", "Contact", 150),
    F("time_off", None, "time", "Time Off", "Contact", 150),
    F("freq", "FREQ", "real", "TX MHz", "Contact", 85),
    F("band", "BAND", "text", "Band", "Contact", 55),
    F("freq_rx", "FREQ_RX", "real", "RX MHz", "Contact", 85),
    F("band_rx", "BAND_RX", "text", "Band RX", "Contact", 60),
    F("mode", "MODE", "text", "Mode", "Contact", 60),
    F("submode", "SUBMODE", "text", "Submode", "Contact", 60),
    F("mode_group", None, "text", "Mode group", "Contact", 70, bulk=False, internal=True),
    F("rst_sent", "RST_SENT", "text", "RST S", "Contact", 50),
    F("rst_rcvd", "RST_RCVD", "text", "RST R", "Contact", 50),
    F("tx_pwr", "TX_PWR", "real", "Power", "Contact", 50),
    # Satellite / propagation
    F("prop_mode", "PROP_MODE", "text", "Prop", "Contact", 50),
    F("sat_name", "SAT_NAME", "text", "Sat", "Contact", 70),
    F("sat_mode", "SAT_MODE", "text", "Sat mode", "Contact", 60),
    # Worked station
    F("first_name", None, "text", "First", "Station", 90),
    F("last_name", None, "text", "Last", "Station", 90),
    F("qth", "QTH", "text", "City", "Location", 110),
    F("address", "ADDRESS", "text", "Address", "Location", 160),
    F("state", "STATE", "text", "Province", "Location", 60),
    F("cnty", "CNTY", "text", "County", "Location", 80),
    F("country", "COUNTRY", "text", "Country", "Location", 110),
    F("dxcc", "DXCC", "int", "DXCC", "Location", 50),
    F("cqz", "CQZ", "int", "CQ", "Location", 40),
    F("ituz", "ITUZ", "int", "ITU", "Location", 40),
    F("cont", "CONT", "text", "Cont", "Location", 40),
    F("gridsquare", "GRIDSQUARE", "text", "Grid", "Location", 65),
    F("vucc_grids", "VUCC_GRIDS", "text", "VUCC grids", "Location", 100),
    F("lat", "LAT", "text", "Lat", "Location", 90),
    F("lon", "LON", "text", "Lon", "Location", 90),
    F("iota", "IOTA", "text", "IOTA", "References", 60),
    F("sota_ref", "SOTA_REF", "text", "SOTA", "References", 80),
    F("pota_ref", "POTA_REF", "text", "POTA", "References", 80),
    F("wwff_ref", "WWFF_REF", "text", "WWFF", "References", 80),
    F("sig", "SIG", "text", "SIG", "References", 60),
    F("sig_info", "SIG_INFO", "text", "SIG info", "References", 80),
    F("skcc", "SKCC", "text", "SKCC", "References", 60),
    F("email", "EMAIL", "text", "Email", "Station", 140),
    F("web", "WEB", "text", "Web", "Station", 140),
    F("qsl_via", "QSL_VIA", "text", "QSL Via", "Station", 120),
    # My station
    F("station_callsign", "STATION_CALLSIGN", "text", "Station call", "My station", 90),
    F("operator", "OPERATOR", "text", "Operator", "My station", 80),
    F("my_gridsquare", "MY_GRIDSQUARE", "text", "My grid", "My station", 65),
    F("my_vucc_grids", "MY_VUCC_GRIDS", "text", "My VUCC grids", "My station", 100),
    F("my_dxcc", "MY_DXCC", "int", "My DXCC", "My station", 50),
    F("my_cq_zone", "MY_CQ_ZONE", "int", "My CQ", "My station", 50),
    F("my_itu_zone", "MY_ITU_ZONE", "int", "My ITU", "My station", 50),
    F("my_state", "MY_STATE", "text", "My province", "My station", 60),
    F("my_cnty", "MY_CNTY", "text", "My county", "My station", 70),
    F("my_pota_ref", "MY_POTA_REF", "text", "My POTA", "My station", 80),
    F("my_sota_ref", "MY_SOTA_REF", "text", "My SOTA", "My station", 80),
    F("my_wwff_ref", "MY_WWFF_REF", "text", "My WWFF", "My station", 80),
    F("my_rig", "MY_RIG", "text", "My rig", "My station", 100),
    F("my_antenna", "MY_ANTENNA", "text", "My antenna", "My station", 100),
    F("station_profile_id", None, "int", "Profile", "My station", 50, bulk=False, internal=True),
    # Notes
    F("comment", "COMMENT", "text", "Comment", "Notes", 160),
    F("notes", "NOTES", "text", "Notes", "Notes", 160),
    # Contest
    F("contest_id", "CONTEST_ID", "text", "Contest", "Contest", 90),
    F("contest_session_id", None, "int", "Session", "Contest", 50, bulk=False, internal=True),
    F("stx", "STX", "int", "STX", "Contest", 45),
    F("srx", "SRX", "int", "SRX", "Contest", 45),
    F("stx_string", "STX_STRING", "text", "Sent exch", "Contest", 80),
    F("srx_string", "SRX_STRING", "text", "Rcvd exch", "Contest", 80),
    # Dupe marking
    F("dupe_flag", None, "text", "Dupe", "Dupe", 60, bulk=False),
    F("dupe_of", None, "int", "Dupe of", "Dupe", 60, bulk=False, internal=True),
    # Paper QSL
    F("qsl_sent", "QSL_SENT", "flag", "Paper sent", "Paper QSL", 45),
    F("qslsdate", "QSLSDATE", "date", "Paper sent date", "Paper QSL", 95),
    F("qsl_sent_via", "QSL_SENT_VIA", "flag", "Paper sent via", "Paper QSL", 45),
    F("qsl_rcvd", "QSL_RCVD", "flag", "Paper rcvd", "Paper QSL", 45),
    F("qslrdate", "QSLRDATE", "date", "Paper rcvd date", "Paper QSL", 95),
    F("qsl_rcvd_via", "QSL_RCVD_VIA", "flag", "Paper rcvd via", "Paper QSL", 45),
    F("qslmsg", "QSLMSG", "text", "QSL message", "Paper QSL", 120),
    # LoTW
    F("lotw_qsl_sent", "LOTW_QSL_SENT", "flag", "LoTW sent", "LoTW", 45),
    F("lotw_qslsdate", "LOTW_QSLSDATE", "date", "LoTW ↑", "LoTW", 90),
    F("lotw_qsl_rcvd", "LOTW_QSL_RCVD", "flag", "LoTW rcvd", "LoTW", 45),
    F("lotw_qslrdate", "LOTW_QSLRDATE", "date", "LoTW ↓", "LoTW", 90),
    # eQSL
    F("eqsl_qsl_sent", "EQSL_QSL_SENT", "flag", "eQSL sent", "eQSL", 45),
    F("eqsl_qslsdate", "EQSL_QSLSDATE", "date", "eQSL ↑", "eQSL", 90),
    F("eqsl_qsl_rcvd", "EQSL_QSL_RCVD", "flag", "eQSL rcvd", "eQSL", 45),
    F("eqsl_qslrdate", "EQSL_QSLRDATE", "date", "eQSL ↓", "eQSL", 90),
    F("eqsl_ag", "APP_EQSL_AG", "flag", "eQSL AG", "eQSL", 45),
    # QRZ.com
    F("qrzcom_qso_upload_status", "QRZCOM_QSO_UPLOAD_STATUS", "flag", "QRZ sent", "QRZ", 45),
    F("qrzcom_qso_upload_date", "QRZCOM_QSO_UPLOAD_DATE", "date", "QRZ ↑", "QRZ", 90),
    F("qrzcom_qso_download_status", "QRZCOM_QSO_DOWNLOAD_STATUS", "flag", "QRZ downloaded", "QRZ", 45),
    F("qrzcom_qso_download_date", "QRZCOM_QSO_DOWNLOAD_DATE", "date", "QRZ downloaded on", "QRZ", 90),
    F("qrz_logid", "APP_QRZLOG_LOGID", "text", "QRZ log id", "QRZ", 80, bulk=False),
    F("qrz_qsl_rcvd", None, "flag", "QRZ confirmed", "QRZ", 45),
    F("qrz_qslrdate", "APP_QRZLOG_QSLDATE", "date", "QRZ ↓", "QRZ", 90),
    # HRDLog
    F("hrdlog_qso_upload_status", "HRDLOG_QSO_UPLOAD_STATUS", "flag", "HRDLog sent", "HRDLog", 45),
    F("hrdlog_qso_upload_date", "HRDLOG_QSO_UPLOAD_DATE", "date", "HRDLog ↑", "HRDLog", 90),
    # Everything else
    F("extra", None, "text", "Extra", "Other", 120, bulk=False, internal=True),
]

BY_COL: dict[str, Field] = {f.col: f for f in FIELDS}
BY_ADIF: dict[str, Field] = {f.adif: f for f in FIELDS if f.adif}
COLUMNS: list[str] = [f.col for f in FIELDS]

# Columns written by the program, never typed by the user.
DERIVED = {"id", "uuid", "created_at", "updated_at", "mode_group", "extra"}

# Fields that, when edited after upload, require a re-upload to QRZ / HRDLog.
SERVICE_KEY_FIELDS = {
    "call", "time_on", "time_off", "freq", "band", "freq_rx", "band_rx", "mode", "submode", "rst_sent",
    "rst_rcvd", "tx_pwr", "prop_mode", "sat_name", "sat_mode", "gridsquare", "first_name", "last_name", "qth",
    "state", "country", "dxcc", "cqz", "ituz", "comment", "station_callsign", "operator", "my_gridsquare",
    "iota", "sota_ref", "pota_ref", "wwff_ref",
}

# Default visible log columns, in order.
DEFAULT_LOG_COLUMNS = [
    "call", "time_on", "time_off", "freq_rx", "band_rx", "freq", "band", "mode", "sat_name", "tx_pwr",
    "rst_sent", "rst_rcvd", "country", "state", "gridsquare", "cqz", "ituz", "operator", "lotw_qslsdate", "lotw_qslrdate",
    "eqsl_qslsdate", "eqsl_qslrdate", "qrzcom_qso_upload_date", "qrz_qslrdate", "hrdlog_qso_upload_date",
    "qslsdate", "qslrdate", "dupe_flag", "comment",
]

# Group order for dialogs.
GROUP_ORDER = ["Contact", "Station", "Location", "References", "My station", "Notes", "Contest", "Paper QSL",
               "LoTW", "eQSL", "QRZ", "HRDLog", "Dupe", "Identity", "Other"]

QSL_FLAG_VALUES = {
    "qsl_sent": ["Y", "N", "R", "Q", "I"],
    "qsl_rcvd": ["Y", "N", "R", "I", "V"],
    "qsl_sent_via": ["B", "D", "E", "M"],
    "qsl_rcvd_via": ["B", "D", "E", "M"],
}

VIA_LABELS = {"B": "Bureau", "D": "Direct", "E": "Electronic", "M": "Manager"}

# Every QSO field of ADIF 3.1.5 (adif.org field table). Uploads to a service send only these, plus the few
# APP_ tags that service reads, so another program's APP_* / USERDEF fields never leave the log.
ADIF_STANDARD = frozenset("""
ADDRESS ADDRESS_INTL AGE ALTITUDE ANT_AZ ANT_EL ANT_PATH ARRL_SECT AWARD_GRANTED AWARD_SUBMITTED A_INDEX BAND BAND_RX
CALL CHECK CLASS CLUBLOG_QSO_UPLOAD_DATE CLUBLOG_QSO_UPLOAD_STATUS CNTY CNTY_ALT COMMENT COMMENT_INTL CONT
CONTACTED_OP CONTEST_ID COUNTRY COUNTRY_INTL CQZ CREDIT_SUBMITTED CREDIT_GRANTED DARC_DOK DCL_QSLRDATE DCL_QSLSDATE
DCL_QSL_RCVD DCL_QSL_SENT DISTANCE DXCC EMAIL EQ_CALL EQSL_QSLRDATE EQSL_QSLSDATE EQSL_QSL_RCVD EQSL_QSL_SENT FISTS
FISTS_CC FORCE_INIT FREQ FREQ_RX GRIDSQUARE GRIDSQUARE_EXT GUEST_OP HAMLOGEU_QSO_UPLOAD_DATE
HAMLOGEU_QSO_UPLOAD_STATUS HAMQTH_QSO_UPLOAD_DATE HAMQTH_QSO_UPLOAD_STATUS HRDLOG_QSO_UPLOAD_DATE
HRDLOG_QSO_UPLOAD_STATUS IOTA IOTA_ISLAND_ID ITUZ K_INDEX LAT LON LOTW_QSLRDATE LOTW_QSLSDATE LOTW_QSL_RCVD
LOTW_QSL_SENT MAX_BURSTS MODE MORSE_KEY_INFO MORSE_KEY_TYPE MS_SHOWER MY_ALTITUDE MY_ANTENNA MY_ANTENNA_INTL
MY_ARRL_SECT MY_CITY MY_CITY_INTL MY_CNTY MY_CNTY_ALT MY_COUNTRY MY_COUNTRY_INTL MY_CQ_ZONE MY_DARC_DOK MY_DXCC
MY_FISTS MY_GRIDSQUARE MY_GRIDSQUARE_EXT MY_IOTA MY_IOTA_ISLAND_ID MY_ITU_ZONE MY_LAT MY_LON MY_MORSE_KEY_INFO
MY_MORSE_KEY_TYPE MY_NAME MY_NAME_INTL MY_POSTAL_CODE MY_POSTAL_CODE_INTL MY_POTA_REF MY_RIG MY_RIG_INTL MY_SIG
MY_SIG_INTL MY_SIG_INFO MY_SIG_INFO_INTL MY_SOTA_REF MY_STATE MY_STREET MY_STREET_INTL MY_USACA_COUNTIES
MY_VUCC_GRIDS MY_WWFF_REF NAME NAME_INTL NOTES NOTES_INTL NR_BURSTS NR_PINGS OPERATOR OWNER_CALLSIGN PFX POTA_REF
PRECEDENCE PROP_MODE PUBLIC_KEY QRZCOM_QSO_DOWNLOAD_DATE QRZCOM_QSO_DOWNLOAD_STATUS QRZCOM_QSO_UPLOAD_DATE
QRZCOM_QSO_UPLOAD_STATUS QSLMSG QSLMSG_INTL QSLMSG_RCVD QSLRDATE QSLSDATE QSL_RCVD QSL_RCVD_VIA QSL_SENT
QSL_SENT_VIA QSL_VIA QSO_COMPLETE QSO_DATE QSO_DATE_OFF QSO_RANDOM QTH QTH_INTL REGION RIG RIG_INTL RST_RCVD RST_SENT
RX_PWR SAT_MODE SAT_NAME SFI SIG SIG_INTL SIG_INFO SIG_INFO_INTL SILENT_KEY SKCC SOTA_REF SRX SRX_STRING STATE
STATION_CALLSIGN STX STX_STRING SUBMODE SWL TEN_TEN TIME_OFF TIME_ON TX_PWR UKSMG USACA_COUNTIES VE_PROV VUCC_GRIDS
WEB WWFF_REF
""".split())


def sql_type(kind: str) -> str:
    return {"int": "INTEGER", "real": "REAL"}.get(kind, "TEXT")


def user_fields() -> list[Field]:
    return [f for f in FIELDS if not f.internal]


def bulk_fields() -> list[Field]:
    return [f for f in FIELDS if f.bulk and not f.internal]
