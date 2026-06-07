# -*- coding: utf-8 -*-
from dataclasses import dataclass

from raspberry_pi_console.constants import NETWORK_MODE_WIRELESS, NETWORK_MODE_WIRED, normalize_network_mode


@dataclass
class SshConfig:
    host: str
    port: int
    username: str
    password: str = ""
    mac_address: str = ""
    mac_address_wired: str = ""
    mac_address_wireless: str = ""
    network_mode: str = NETWORK_MODE_WIRED
    auth_method: str = "password"  # password | key
    key_path: str = ""
    key_passphrase: str = ""
    profile_id: str = "default"

    def uses_key(self) -> bool:
        return self.auth_method == "key" and bool(self.key_path)

    def auth_ready(self) -> bool:
        if not self.username:
            return False
        if self.uses_key():
            return bool(self.key_path)
        return bool(self.password)

    def normalized_network_mode(self) -> str:
        return normalize_network_mode(self.network_mode)

    def has_mac_for_mode(self, mode: str | None = None) -> bool:
        mode = normalize_network_mode(mode or self.network_mode)
        if mode == NETWORK_MODE_WIRELESS:
            return bool(self.mac_address_wireless)
        return bool(self.mac_address_wired or self.mac_address)
