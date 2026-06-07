# -*- coding: utf-8 -*-
from __future__ import annotations

import shlex
import traceback

from PySide6.QtCore import QObject, Signal

from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.logs import _validate_service_name


class LogTailWorker(QObject):
    line = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, config: SshConfig, payload: dict):
        super().__init__()
        self.config = config
        self.payload = payload
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        ssh = None
        try:
            mode = self.payload.get("mode", "system")
            service = self.payload.get("service", "").strip()
            scope = self.payload.get("scope", "system")
            if mode == "service":
                service = _validate_service_name(service)
                prefix = "--user " if scope == "user" else ""
                command = f"journalctl {prefix}-f -u {shlex.quote(service)} --no-pager"
            elif mode == "kernel":
                command = "journalctl -k -f --no-pager"
            else:
                command = "journalctl -f --no-pager"

            ssh = SshClient(self.config)
            ssh.connect()
            _, stdout, _ = ssh.client.exec_command(command, timeout=None)
            channel = stdout.channel
            while not self._stop:
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    if chunk:
                        self.line.emit(chunk)
                    continue
                if channel.exit_status_ready():
                    break
            self.finished.emit(True, "日志跟踪已停止。")
        except Exception:
            self.finished.emit(False, traceback.format_exc())
        finally:
            if ssh is not None:
                ssh.close()
