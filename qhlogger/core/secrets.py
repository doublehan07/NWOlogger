"""Passwords and API keys, kept only in the OS keychain (macOS Keychain / Windows Credential Manager)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SERVICE_PREFIX = "QHlogger"


def _keyring():
    try:
        import keyring  # noqa: PLC0415

        return keyring
    except Exception:  # noqa: BLE001
        return None


def get(service: str, account: str) -> str | None:
    kr = _keyring()
    if kr is None or not account:
        return None
    try:
        return kr.get_password(f"{SERVICE_PREFIX}:{service}", account)
    except Exception as e:  # noqa: BLE001
        log.warning("keyring read failed: %s", e)
        return None


def set(service: str, account: str, secret: str | None) -> bool:  # noqa: A001 - mirrors keyring API
    kr = _keyring()
    if kr is None or not account:
        return False
    try:
        if secret:
            kr.set_password(f"{SERVICE_PREFIX}:{service}", account, secret)
        else:
            try:
                kr.delete_password(f"{SERVICE_PREFIX}:{service}", account)
            except Exception:  # noqa: BLE001 - nothing stored
                pass
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("keyring write failed: %s", e)
        return False
