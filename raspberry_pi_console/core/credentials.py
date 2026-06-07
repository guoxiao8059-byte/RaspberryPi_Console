# -*- coding: utf-8 -*-
"""SSH 凭据存储：优先 Windows Credential Manager（keyring），兼容旧版 QSettings 明文迁移。"""

from __future__ import annotations

import json

try:
    import keyring
    import keyring.errors
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore

from PySide6.QtCore import QSettings

from raspberry_pi_console.constants import APP_NAME, APP_ORG

SERVICE_NAME = f"{APP_ORG}/{APP_NAME}"
_LEGACY_PASSWORD_KEY = "ssh/password"
_LEGACY_KEY_PASSPHRASE_KEY = "ssh/key_passphrase"


def _credential_key(profile_id: str, kind: str) -> str:
    return f"{profile_id}:{kind}"


def _fallback_settings() -> QSettings:
    return QSettings(APP_ORG, APP_NAME)


def _fallback_secret_key(profile_id: str, kind: str) -> str:
    return f"secrets/{profile_id}/{kind}"


def get_secret(profile_id: str, kind: str) -> str:
    key = _credential_key(profile_id, kind)
    if keyring is not None:
        try:
            value = keyring.get_password(SERVICE_NAME, key)
            if value is not None:
                return value
        except keyring.errors.KeyringError:
            pass
    return str(_fallback_settings().value(_fallback_secret_key(profile_id, kind), ""))


def set_secret(profile_id: str, kind: str, value: str) -> None:
    key = _credential_key(profile_id, kind)
    if keyring is not None:
        try:
            if value:
                keyring.set_password(SERVICE_NAME, key, value)
            else:
                try:
                    keyring.delete_password(SERVICE_NAME, key)
                except keyring.errors.PasswordDeleteError:
                    pass
            _fallback_settings().remove(_fallback_secret_key(profile_id, kind))
            return
        except keyring.errors.KeyringError:
            pass
    settings = _fallback_settings()
    if value:
        settings.setValue(_fallback_secret_key(profile_id, kind), value)
    else:
        settings.remove(_fallback_secret_key(profile_id, kind))


def delete_profile_secrets(profile_id: str) -> None:
    for kind in ("password", "key_passphrase"):
        set_secret(profile_id, kind, "")


def migrate_legacy_secrets(profile_id: str = "default") -> None:
    settings = _fallback_settings()
    legacy_password = str(settings.value(_LEGACY_PASSWORD_KEY, ""))
    if legacy_password and not get_secret(profile_id, "password"):
        set_secret(profile_id, "password", legacy_password)
        settings.remove(_LEGACY_PASSWORD_KEY)
    legacy_passphrase = str(settings.value(_LEGACY_KEY_PASSPHRASE_KEY, ""))
    if legacy_passphrase and not get_secret(profile_id, "key_passphrase"):
        set_secret(profile_id, "key_passphrase", legacy_passphrase)
        settings.remove(_LEGACY_KEY_PASSPHRASE_KEY)


def using_keyring() -> bool:
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        return backend.__class__.__module__ != "keyring.backends.fail"
    except Exception:
        return False
