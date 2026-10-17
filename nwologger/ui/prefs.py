"""Application preferences (QSettings). Secrets are never stored here; see core.secrets."""

from __future__ import annotations

import json
import os
from typing import Any

from PySide6.QtCore import QSettings

from ..core import db as dbm
from ..core import secrets

DEFAULTS: dict[str, Any] = {
    "language": "en",
    "theme": "system",
    "db_path": str(dbm.DEFAULT_PATH),
    "backup_keep": 20,
    "block_seconds": 30,
    "warn_hours": 24,
    "allow_dupes": False,
    "sticky": ["freq", "mode", "sat", "device"],
    "auto_lookup": True,
    "lookup_ttl_days": 30,
    "qrz_user": "",
    "hamqth_user": "",
    "lotw_user": "",
    "tqsl_path": "",
    "eqsl_user": "",
    "hrdlog_call": "",
    "upload_on_log": {"qrz": False, "eqsl": False, "hrdlog": False},
    "sync_gap_minutes": 5,  # the wait between two downloads (or two uploads) with the same service
    "sat_names": {},
    "user_sats": [],
    "sat_list": [],
    "mode_list": [],
    "rigs": [],
    "antennas": [],
    "setups": [],
    "device": {},  # the entry form's rig, antenna and power, kept between runs
    "rx_sync": True,
    "mode_exact": False,
    "current_contest": None,
}

SECRET_KEYS = {
    "qrz_password": ("qrz_xml", "qrz_user"),
    "hamqth_password": ("hamqth", "hamqth_user"),
    "lotw_password": ("lotw", "lotw_user"),
    "eqsl_password": ("eqsl", "eqsl_user"),
    "hrdlog_code": ("hrdlog", "hrdlog_call"),
}


class Prefs:
    def __init__(self, settings: QSettings | None = None):
        # NWOLOGGER_SETTINGS=<file.ini> keeps test and screenshot runs away from your real preferences.
        path = os.environ.get("NWOLOGGER_SETTINGS")
        if settings is None and path:
            settings = QSettings(path, QSettings.Format.IniFormat)
        self.s = settings or QSettings("NWOlogger", "NWOlogger")

    def get(self, key: str) -> Any:
        default = DEFAULTS.get(key)
        v = self.s.value(key)
        if v is None:
            return default
        if isinstance(default, bool):
            return v in (True, "true", "1", 1)
        if isinstance(default, int):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        if isinstance(default, (list, dict)):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except ValueError:
                    return default
            return v
        return v

    def set(self, key: str, value: Any) -> None:
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        self.s.setValue(key, value)

    # Secrets live in the keychain, keyed by the matching user name.
    def secret(self, key: str) -> str | None:
        service, user_key = SECRET_KEYS[key]
        return secrets.get(service, self.get(user_key) or "")

    def set_secret(self, key: str, value: str | None) -> bool:
        service, user_key = SECRET_KEYS[key]
        return secrets.set(service, self.get(user_key) or "", value)

    def has_secret(self, key: str) -> bool:
        return bool(self.secret(key))

    def account_renamed(self, key: str, old_user: str, new_user: str) -> bool:
        """The account name changed, so the keychain entry under the old name can never be read again: delete it.
        When only the case or spacing changed (the same account), the secret moves to the new name."""
        service, _ = SECRET_KEYS[key]
        if not old_user or old_user == new_user:
            return True
        old = secrets.get(service, old_user)
        if old is None:
            return True
        ok = True
        if new_user and old_user.strip().lower() == new_user.strip().lower() and not secrets.get(service, new_user):
            ok = secrets.set(service, new_user, old)
        return secrets.delete(service, old_user) and ok

    def qrz_api_key(self, station_call: str) -> str | None:
        return secrets.get("qrz_logbook", station_call.upper()) if station_call else None

    def set_qrz_api_key(self, station_call: str, key: str | None) -> bool:
        return secrets.set("qrz_logbook", station_call.upper(), key)

    # UI state
    def value(self, key: str, default: Any = None) -> Any:
        v = self.s.value(key)
        return default if v is None else v

    def set_value(self, key: str, value: Any) -> None:
        self.s.setValue(key, value)
