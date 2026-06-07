# -*- coding: utf-8 -*-
"""SSH 连接复用：同一设备配置下复用 Paramiko 会话，减少频繁握手。"""

from __future__ import annotations

import threading
import time

from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.ssh_client import SshClient

_IDLE_SECONDS = 90


class SshSessionPool:
    _instance: SshSessionPool | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._clients: dict[str, SshClient] = {}
        self._last_used: dict[str, float] = {}

    @classmethod
    def shared(cls) -> SshSessionPool:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @staticmethod
    def fingerprint(config: SshConfig) -> str:
        return "|".join(
            [
                config.host,
                str(config.port),
                config.username,
                config.auth_method,
                config.key_path,
                config.profile_id,
            ]
        )

    def _is_alive(self, client: SshClient) -> bool:
        transport = getattr(client.client, "get_transport", lambda: None)()
        return bool(transport and transport.is_active())

    def acquire(self, config: SshConfig) -> SshClient:
        key = self.fingerprint(config)
        now = time.monotonic()
        with self._lock:
            self._evict_idle(now)
            client = self._clients.get(key)
            if client and self._is_alive(client):
                self._last_used[key] = now
                return client
            client = SshClient(config)
            client.connect()
            self._clients[key] = client
            self._last_used[key] = now
            return client

    def invalidate(self, config: SshConfig) -> None:
        key = self.fingerprint(config)
        with self._lock:
            client = self._clients.pop(key, None)
            self._last_used.pop(key, None)
        if client is not None:
            client.close()

    def release_task(self, config: SshConfig, client: SshClient) -> None:
        key = self.fingerprint(config)
        with self._lock:
            if self._clients.get(key) is client and self._is_alive(client):
                self._last_used[key] = time.monotonic()
                return
        client.close()

    def close_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._last_used.clear()
        for client in clients:
            client.close()

    def _evict_idle(self, now: float) -> None:
        expired = [key for key, ts in self._last_used.items() if now - ts > _IDLE_SECONDS]
        for key in expired:
            client = self._clients.pop(key, None)
            self._last_used.pop(key, None)
            if client is not None:
                client.close()
