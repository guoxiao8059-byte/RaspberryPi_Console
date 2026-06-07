# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from PySide6.QtCore import QSettings

from raspberry_pi_console.constants import (
    APP_NAME,
    APP_ORG,
    HOST_SOURCE_MANUAL,
    HOST_SOURCE_REMOTE_WIFI,
    NETWORK_MODE_WIRELESS,
    NETWORK_MODE_WIRED,
    normalize_network_mode,
)
from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.credentials import delete_profile_secrets, get_secret, migrate_legacy_secrets
from raspberry_pi_console.core.network import normalize_mac_address

_PROFILES_KEY = "profiles/list"
_ACTIVE_KEY = "profiles/active_id"


@dataclass
class ConnectionProfile:
    id: str
    name: str
    host: str = "192.168.1.5"
    port: int = 22
    username: str = "pi"
    mac_address: str = ""
    mac_address_wired: str = ""
    mac_address_wireless: str = ""
    network_mode: str = NETWORK_MODE_WIRED
    host_wired: str = ""
    host_wireless: str = ""
    host_source: str = HOST_SOURCE_MANUAL
    host_source_wired: str = HOST_SOURCE_MANUAL
    host_source_wireless: str = ""
    auth_method: str = "password"  # password | key
    key_path: str = ""

    def active_host(self) -> str:
        mode = normalize_network_mode(self.network_mode)
        if mode == NETWORK_MODE_WIRELESS:
            return (self.host_wireless or "").strip()
        return (self.host_wired or "").strip()

    def active_host_source(self) -> str:
        mode = normalize_network_mode(self.network_mode)
        if mode == NETWORK_MODE_WIRELESS:
            return self.host_source_wireless or HOST_SOURCE_MANUAL
        return self.host_source_wired or HOST_SOURCE_MANUAL

    def apply_host_for_mode(self, mode: str, host: str, source: str) -> None:
        mode = normalize_network_mode(mode)
        host = str(host or "").strip()
        source = str(source or HOST_SOURCE_MANUAL)
        if mode == NETWORK_MODE_WIRELESS:
            self.host_wireless = host
            self.host_source_wireless = source
        else:
            self.host_wired = host
            self.host_source_wired = source
        self.sync_legacy_fields()

    def sync_legacy_fields(self) -> None:
        self.host = self.active_host()
        self.host_source = self.active_host_source()

    @classmethod
    def from_dict(cls, data: dict) -> ConnectionProfile:
        legacy_mac = normalize_mac_address(str(data.get("mac_address") or ""))
        wired_mac = normalize_mac_address(str(data.get("mac_address_wired") or "")) or legacy_mac
        wireless_mac = normalize_mac_address(str(data.get("mac_address_wireless") or ""))
        if wireless_mac and wired_mac and wireless_mac == wired_mac:
            wireless_mac = ""
        network_mode = normalize_network_mode(str(data.get("network_mode") or NETWORK_MODE_WIRED))
        legacy_host = str(data.get("host") or "")
        legacy_source = str(data.get("host_source") or HOST_SOURCE_MANUAL)
        host_wired = str(data.get("host_wired") or "")
        host_wireless = str(data.get("host_wireless") or "")
        host_source_wired = str(data.get("host_source_wired") or "")
        host_source_wireless = str(data.get("host_source_wireless") or "")
        if not host_wired and not host_wireless:
            if legacy_source == HOST_SOURCE_REMOTE_WIFI:
                host_wireless = legacy_host
                host_source_wireless = legacy_source
            else:
                host_wired = legacy_host
                host_source_wired = legacy_source or HOST_SOURCE_MANUAL
        if not host_source_wired:
            host_source_wired = HOST_SOURCE_MANUAL
        profile = cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name") or "默认设备"),
            host=legacy_host,
            port=int(data.get("port") or 22),
            username=str(data.get("username") or "pi"),
            mac_address=legacy_mac,
            mac_address_wired=wired_mac,
            mac_address_wireless=wireless_mac,
            network_mode=network_mode,
            host_wired=host_wired,
            host_wireless=host_wireless,
            host_source=legacy_source,
            host_source_wired=host_source_wired,
            host_source_wireless=host_source_wireless,
            auth_method=str(data.get("auth_method") or "password"),
            key_path=str(data.get("key_path") or ""),
        )
        profile.sync_legacy_fields()
        return profile

    def to_dict(self) -> dict:
        return asdict(self)


class ProfileStore:
    def __init__(self, settings: QSettings | None = None):
        self.settings = settings or QSettings(APP_ORG, APP_NAME)
        migrate_legacy_secrets()
        self._ensure_default_profile()

    def _ensure_default_profile(self) -> None:
        profiles = self.list_profiles()
        if profiles:
            if not self.settings.value(_ACTIVE_KEY):
                self.settings.setValue(_ACTIVE_KEY, profiles[0].id)
            return
        profile = ConnectionProfile(
            id="default",
            name="默认设备",
            host=str(self.settings.value("ssh/host", "192.168.1.5")),
            port=int(self.settings.value("ssh/port", 22)),
            username=str(self.settings.value("ssh/user", "pi")),
            mac_address=normalize_mac_address(self.settings.value("ssh/mac_address", "")),
            mac_address_wired=normalize_mac_address(self.settings.value("ssh/mac_address_wired", ""))
            or normalize_mac_address(self.settings.value("ssh/mac_address", "")),
            mac_address_wireless=normalize_mac_address(self.settings.value("ssh/mac_address_wireless", "")),
            network_mode=normalize_network_mode(str(self.settings.value("ssh/network_mode", NETWORK_MODE_WIRED))),
            auth_method=str(self.settings.value("ssh/auth_method", "password")),
            key_path=str(self.settings.value("ssh/key_path", "")),
        )
        self._save_all([profile])
        self.settings.setValue(_ACTIVE_KEY, profile.id)

    def list_profiles(self) -> list[ConnectionProfile]:
        raw = self.settings.value(_PROFILES_KEY, "[]")
        try:
            items = json.loads(raw) if isinstance(raw, str) else []
        except json.JSONDecodeError:
            items = []
        return [ConnectionProfile.from_dict(item) for item in items if isinstance(item, dict)]

    def _save_all(self, profiles: list[ConnectionProfile]) -> None:
        payload = json.dumps([profile.to_dict() for profile in profiles], ensure_ascii=False)
        self.settings.setValue(_PROFILES_KEY, payload)

    def get_active_id(self) -> str:
        profiles = self.list_profiles()
        active_id = str(self.settings.value(_ACTIVE_KEY, ""))
        if active_id and any(profile.id == active_id for profile in profiles):
            return active_id
        return profiles[0].id if profiles else "default"

    def get_active(self) -> ConnectionProfile:
        active_id = self.get_active_id()
        for profile in self.list_profiles():
            if profile.id == active_id:
                return profile
        self._ensure_default_profile()
        return self.list_profiles()[0]

    def set_active(self, profile_id: str) -> None:
        if any(profile.id == profile_id for profile in self.list_profiles()):
            self.settings.setValue(_ACTIVE_KEY, profile_id)
            self._sync_legacy_ssh_keys(self.get_active())

    def save_profile(self, profile: ConnectionProfile) -> None:
        profile.sync_legacy_fields()
        profiles = self.list_profiles()
        replaced = False
        for index, item in enumerate(profiles):
            if item.id == profile.id:
                profiles[index] = profile
                replaced = True
                break
        if not replaced:
            profiles.append(profile)
        self._save_all(profiles)
        if profile.id == self.get_active_id():
            self._sync_legacy_ssh_keys(profile)

    def delete_profile(self, profile_id: str) -> bool:
        profiles = [profile for profile in self.list_profiles() if profile.id != profile_id]
        if len(profiles) == len(self.list_profiles()):
            return False
        if not profiles:
            return False
        delete_profile_secrets(profile_id)
        self._save_all(profiles)
        if self.get_active_id() == profile_id:
            self.set_active(profiles[0].id)
        return True

    def add_profile(self, name: str) -> ConnectionProfile:
        profile = ConnectionProfile(id=uuid.uuid4().hex[:8], name=name or "新设备")
        self.save_profile(profile)
        return profile

    def _sync_legacy_ssh_keys(self, profile: ConnectionProfile) -> None:
        """兼容仍直接读 QSettings ssh/* 的代码路径。"""
        self.settings.setValue("ssh/host", profile.host)
        self.settings.setValue("ssh/port", profile.port)
        self.settings.setValue("ssh/user", profile.username)
        self.settings.setValue("ssh/mac_address", profile.mac_address or profile.mac_address_wired)
        self.settings.setValue("ssh/mac_address_wired", profile.mac_address_wired)
        self.settings.setValue("ssh/mac_address_wireless", profile.mac_address_wireless)
        self.settings.setValue("ssh/network_mode", profile.network_mode)
        self.settings.setValue("ssh/auth_method", profile.auth_method)
        self.settings.setValue("ssh/key_path", profile.key_path)
        self.settings.remove("ssh/password")
        self.settings.remove("ssh/key_passphrase")

    def to_ssh_config(self, profile: ConnectionProfile | None = None) -> SshConfig:
        profile = profile or self.get_active()
        password = ""
        key_passphrase = ""
        if profile.auth_method == "password":
            password = get_secret(profile.id, "password")
        else:
            key_passphrase = get_secret(profile.id, "key_passphrase")
        return SshConfig(
            host=profile.active_host(),
            port=int(profile.port),
            username=profile.username.strip() or "pi",
            password=password,
            mac_address=profile.mac_address or profile.mac_address_wired,
            mac_address_wired=profile.mac_address_wired,
            mac_address_wireless=profile.mac_address_wireless,
            network_mode=profile.network_mode,
            auth_method=profile.auth_method,
            key_path=profile.key_path.strip(),
            key_passphrase=key_passphrase,
            profile_id=profile.id,
        )
