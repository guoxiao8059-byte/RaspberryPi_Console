# -*- coding: utf-8 -*-
import os
import traceback

from PySide6.QtCore import QObject, Signal

from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.ssh_client import SshClient

class RemoteBrowserWorker(QObject):
    finished = Signal(bool, str, object)

    def __init__(self, config: SshConfig, operation: str, payload: dict | None = None):
        super().__init__()
        self.config = config
        self.operation = operation
        self.payload = payload or {}

    def run(self):
        ssh = SshClient(self.config)
        try:
            ssh.connect()
            if self.operation == "load_root":
                target = SshClient.normalize_remote_path(self.payload.get("path", "/"))
                if not ssh.remote_path_is_dir(target):
                    target = SshClient.normalize_remote_path(os.path.dirname(target.rstrip("/")) or "/")
                entries = ssh.list_remote_dir(target)
                self.finished.emit(True, self.operation, {"path": target, "entries": entries})
                return
            if self.operation == "list_children":
                path = str(self.payload.get("path", "/"))
                entries = ssh.list_remote_dir(path)
                self.finished.emit(True, self.operation, {"path": path, "entries": entries})
                return
            raise ValueError(f"未知操作：{self.operation}")
        except Exception:
            self.finished.emit(False, self.operation, traceback.format_exc())
        finally:
            ssh.close()
