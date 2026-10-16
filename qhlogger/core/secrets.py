"""Passwords and API keys, kept only in the OS keychain (macOS Keychain / Windows Credential Manager).

Only the platform's secure stores are used. A plaintext or "fail" keyring backend (for example ``keyrings.alt``
picked through ``PYTHON_KEYRING_BACKEND``) is refused, so secrets are never written anywhere else.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SERVICE_PREFIX = "QHlogger"

SECURE_BACKENDS = {"keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService",
                   "keyring.backends.libsecret", "keyring.backends.kwallet"}


def _secure(backend) -> bool:
    chained = getattr(backend, "backends", None)
    if chained is not None and type(backend).__module__ == "keyring.backends.chainer":
        return bool(chained) and all(_secure(b) for b in chained)
    return type(backend).__module__ in SECURE_BACKENDS


def _keyring():
    try:
        import keyring  # noqa: PLC0415

        return keyring if _secure(keyring.get_keyring()) else None
    except Exception:  # noqa: BLE001
        return None


def backend_name() -> str:
    """The keyring backend in use, e.g. ``macOS Keyring``; empty when none is available."""
    try:
        import keyring  # noqa: PLC0415

        b = keyring.get_keyring()
        return f"{type(b).__module__.rsplit('.', 1)[-1]} {type(b).__name__}"
    except Exception:  # noqa: BLE001
        return ""


def available() -> bool:
    return _keyring() is not None


def get(service: str, account: str) -> str | None:
    kr = _keyring()
    if kr is None or not account:
        return None
    try:
        return kr.get_password(f"{SERVICE_PREFIX}:{service}", account)
    except Exception as e:  # noqa: BLE001
        log.warning("keyring read failed (%s)", type(e).__name__)
        return None


def set(service: str, account: str, secret: str | None) -> bool:  # noqa: A001 - mirrors keyring API
    """Store ``secret``, or delete the entry when it is empty. False when the keychain refused."""
    kr = _keyring()
    if kr is None or not account:
        return False
    name = f"{SERVICE_PREFIX}:{service}"
    try:
        if secret:
            kr.set_password(name, account, secret)
        else:
            try:
                kr.delete_password(name, account)
            except kr.errors.PasswordDeleteError:
                if kr.get_password(name, account) is not None:
                    return False  # still there: the keychain refused
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("keyring write failed (%s)", type(e).__name__)
        return False


def delete(service: str, account: str) -> bool:
    return set(service, account, None)
