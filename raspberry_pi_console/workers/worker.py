# -*- coding: utf-8 -*-
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal

from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.network import discover_host_by_mac, format_mac_scan_report
from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.core.ssh_pool import SshSessionPool
from raspberry_pi_console.remote.command import run_command
from raspberry_pi_console.remote.common import format_command_result
from raspberry_pi_console.remote.dashboard import fetch_dashboard
from raspberry_pi_console.remote.deploy import run_deploy
from raspberry_pi_console.remote.docker import fetch_docker_containers, run_docker_action
from raspberry_pi_console.remote.logs import fetch_logs
from raspberry_pi_console.remote.packages import fetch_packages, run_package_upgrade
from raspberry_pi_console.remote.process import kill_process
from raspberry_pi_console.remote.services import fetch_services
from raspberry_pi_console.remote.network_mgmt import enable_wireless_network, fetch_network_interfaces
from raspberry_pi_console.remote.transfer import run_transfer


class Worker(QObject):
    finished = Signal(bool, str, str, object)
    progress = Signal(int, int, str)

    def __init__(self, config: SshConfig, task: str, payload: dict | None = None):
        super().__init__()
        self.config = config
        self.task = task
        self.payload = payload or {}
        self._pool = SshSessionPool.shared()
        self._pooled = False

    def _progress_callback(self, done: int, total: int, label: str) -> None:
        self.progress.emit(done, total, label)

    def _acquire_ssh(self) -> SshClient:
        if self.task == "discover_host":
            raise RuntimeError("discover_host 不使用 SSH。")
        self._pooled = True
        return self._pool.acquire(self.config)

    def _release_ssh(self, ssh: SshClient | None) -> None:
        if ssh is None:
            return
        if self._pooled:
            self._pool.release_task(self.config, ssh)
        else:
            ssh.close()

    def run(self):
        ssh = None
        try:
            if self.task == "discover_host":
                report = discover_host_by_mac(
                    self.payload.get("mac_address", self.config.mac_address),
                    self.payload.get("current_host", self.config.host),
                    network_mode=self.payload.get("network_mode", self.config.normalized_network_mode()),
                    mac_address_wired=self.payload.get("mac_address_wired", self.config.mac_address_wired),
                    mac_address_wireless=self.payload.get("mac_address_wireless", self.config.mac_address_wireless),
                    progress=self._progress_callback,
                )
                body = format_mac_scan_report(report)
                data = report.to_dict()
                if not report.host:
                    self.finished.emit(False, self.task, body, data)
                    return
                self.finished.emit(True, self.task, body, data)
                return

            if self.task == "fetch_network_interfaces":
                ssh = self._acquire_ssh()
                text, data = fetch_network_interfaces(ssh)
                self.finished.emit(True, self.task, text, data)
                return

            if self.task == "enable_wireless":
                ssh = self._acquire_ssh()
                text, data = enable_wireless_network(ssh)
                self.finished.emit(True, self.task, text, data)
                return

            ssh = self._acquire_ssh()

            if self.task == "startup_refresh":
                code, out, err = ssh.run("hostname && whoami", timeout=30)
                if code != 0:
                    raise RuntimeError(format_command_result(code, out, err))
                text, data = fetch_dashboard(ssh)
                self.finished.emit(True, self.task, text, data)
                return

            if self.task == "dashboard":
                text, data = fetch_dashboard(ssh)
                self.finished.emit(True, self.task, text, data)
                return

            if self.task == "services":
                text, data = fetch_services(ssh, self.payload.get("scope", "system"))
                self.finished.emit(True, self.task, text, data)
                return

            if self.task == "packages":
                if self.payload.get("action") == "upgrade":
                    text = run_package_upgrade(ssh, self.payload)
                    self.finished.emit(True, self.task, text, None)
                    return
                text, data = fetch_packages(ssh)
                self.finished.emit(True, self.task, text, data)
                return

            if self.task == "docker":
                if self.payload.get("action"):
                    text = run_docker_action(ssh, self.payload)
                    self.finished.emit(True, self.task, text, None)
                    return
                text, data = fetch_docker_containers(ssh)
                self.finished.emit(True, self.task, text, data)
                return

            if self.task == "logs":
                text = fetch_logs(ssh, self.payload)
                self.finished.emit(True, self.task, text, None)
                return

            if self.task == "transfer":
                text = run_transfer(ssh, self.payload, progress=self._progress_callback)
                self.finished.emit(True, self.task, text, None)
                return

            if self.task == "deploy":
                text = run_deploy(ssh, self.payload, progress=self._progress_callback)
                self.finished.emit(True, self.task, text, None)
                return

            if self.task == "kill_process":
                text = kill_process(ssh, self.payload)
                self.finished.emit(True, self.task, text, None)
                return

            if self.task == "command":
                text = run_command(ssh, self.payload)
                self.finished.emit(True, self.task, text, None)
                return

            raise ValueError(f"未知任务：{self.task}")
        except Exception:
            self._pool.invalidate(self.config)
            self.finished.emit(False, self.task, traceback.format_exc(), None)
        finally:
            self._release_ssh(ssh)
