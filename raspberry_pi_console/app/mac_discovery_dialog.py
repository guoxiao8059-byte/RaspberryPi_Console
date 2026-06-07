# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from raspberry_pi_console.constants import (
    HOST_SOURCE_MAC_SCAN,
    NETWORK_MODE_LABELS,
    NETWORK_MODE_WIRED,
    NETWORK_MODE_WIRELESS,
    NETWORK_MODES,
    normalize_network_mode,
)
from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.network import normalize_mac_address
from raspberry_pi_console.core.profiles import ConnectionProfile, ProfileStore
from raspberry_pi_console.workers.worker import Worker


class MacDiscoveryDialog(QDialog):
    def __init__(self, config: SshConfig, profile_store: ProfileStore, parent=None):
        super().__init__(parent)
        self.config = config
        self.profile_store = profile_store
        self.profile = ConnectionProfile.from_dict(self.profile_store.get_active().to_dict())
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.found_host = ""
        self.found_mode = ""
        self._busy = False

        self.setWindowTitle("按 MAC 查找 IP")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)

        layout = QVBoxLayout(self)

        mode_group_box = QGroupBox("扫描模式")
        mode_layout = QHBoxLayout(mode_group_box)
        self.mode_buttons: dict[str, QRadioButton] = {}
        self.mode_group = QButtonGroup(self)
        for index, mode in enumerate(NETWORK_MODES):
            button = QRadioButton(NETWORK_MODE_LABELS[mode])
            button.setProperty("network_mode", mode)
            self.mode_buttons[mode] = button
            self.mode_group.addButton(button, index)
            mode_layout.addWidget(button)
        mode_layout.addStretch(1)
        layout.addWidget(mode_group_box)

        form = QFormLayout()
        self.wired_mac_edit = QLineEdit()
        self.wired_mac_edit.setPlaceholderText("有线网卡 MAC，例如 DC:A6:32:12:34:56")
        self.wireless_mac_edit = QLineEdit()
        self.wireless_mac_edit.setPlaceholderText("无线网卡 MAC，例如 E4:5F:01:12:34:56")
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("扫描成功后自动填入")
        self.host_edit.setReadOnly(True)
        form.addRow("有线 MAC", self.wired_mac_edit)
        form.addRow("无线 MAC", self.wireless_mac_edit)
        form.addRow("找到的 IP", self.host_edit)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        self.fetch_mac_btn = QPushButton("从设备读取 MAC")
        self.enable_wifi_btn = QPushButton("启用无线网络")
        self.scan_btn = QPushButton("开始扫描")
        self.fetch_mac_btn.clicked.connect(self._fetch_macs_from_device)
        self.enable_wifi_btn.clicked.connect(self._enable_wireless)
        self.scan_btn.clicked.connect(self._start_scan)
        action_row.addWidget(self.fetch_mac_btn)
        action_row.addWidget(self.enable_wifi_btn)
        action_row.addWidget(self.scan_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("扫描日志将显示在这里…")
        layout.addWidget(self.log_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._apply_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_profile()
        self._sync_mode_selection(self.profile.network_mode)

    def _load_profile(self) -> None:
        profile = self.profile_store.get_active()
        self.profile = ConnectionProfile.from_dict(profile.to_dict())
        self.wired_mac_edit.setText(self.profile.mac_address_wired)
        self.wireless_mac_edit.setText(self.profile.mac_address_wireless)
        self.host_edit.setText(self.profile.active_host())

    def _sync_mode_selection(self, mode: str) -> None:
        mode = normalize_network_mode(mode)
        button = self.mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    def selected_mode(self) -> str:
        checked = self.mode_group.checkedButton()
        if checked is None:
            return NETWORK_MODE_WIRED
        return normalize_network_mode(str(checked.property("network_mode") or NETWORK_MODE_WIRED))

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _on_scan_progress(self, _done: int, _total: int, label: str) -> None:
        text = str(label or "").strip()
        if text:
            self._append_log(text)

    def _validate_macs(self) -> bool:
        wired = normalize_mac_address(self.wired_mac_edit.text().strip())
        wireless = normalize_mac_address(self.wireless_mac_edit.text().strip())
        if self.wired_mac_edit.text().strip() and not wired:
            QMessageBox.warning(self, "MAC 格式错误", "有线 MAC 格式无效，请输入类似 DC:A6:32:12:34:56 的值。")
            return False
        if self.wireless_mac_edit.text().strip() and not wireless:
            QMessageBox.warning(self, "MAC 格式错误", "无线 MAC 格式无效，请输入类似 E4:5F:01:12:34:56 的值。")
            return False
        mode = self.selected_mode()
        if mode == NETWORK_MODE_WIRED and not wired:
            QMessageBox.warning(self, "MAC 未配置", "扫描有线网需要先填写有线 MAC。")
            return False
        if mode == NETWORK_MODE_WIRELESS and not wireless:
            QMessageBox.warning(self, "MAC 未配置", "扫描无线网需要先填写无线 MAC。")
            return False
        if mode == NETWORK_MODE_WIRELESS and wired and wireless == wired:
            QMessageBox.warning(
                self,
                "MAC 配置错误",
                f"无线 MAC 与有线 MAC 相同（{wireless}）。\n请填写 wlan0 的真实 MAC，不要使用 eth0 地址。",
            )
            return False
        return True

    def _build_config(self) -> SshConfig:
        profile = self.profile_store.get_active()
        config = self.profile_store.to_ssh_config(profile)
        config.mac_address_wired = normalize_mac_address(self.wired_mac_edit.text().strip())
        config.mac_address_wireless = normalize_mac_address(self.wireless_mac_edit.text().strip())
        config.mac_address = config.mac_address_wired
        config.network_mode = self.selected_mode()
        return config

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self.fetch_mac_btn,
            self.enable_wifi_btn,
            self.scan_btn,
            self.wired_mac_edit,
            self.wireless_mac_edit,
        ):
            widget.setEnabled(not busy)
        for button in self.mode_buttons.values():
            button.setEnabled(not busy)

    def _start_worker(self, task: str, payload: dict | None = None) -> None:
        if self._busy:
            QMessageBox.information(self, "任务执行中", "请等待当前操作完成。")
            return
        config = self._build_config()
        if task == "discover_host" and not self._validate_macs():
            return
        if task in {"fetch_network_interfaces", "enable_wireless"}:
            if not config.host or not config.auth_ready():
                QMessageBox.warning(
                    self,
                    "无法连接",
                    "从设备读取 MAC 或启用无线网络需要先通过有线网连接树莓派（填写 IP 与凭据）。",
                )
                return

        self._set_busy(True)
        self.thread = QThread(self)
        self.worker = Worker(config, task, payload or {})
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        if task == "discover_host":
            self.worker.progress.connect(self._on_scan_progress)
        self.worker.finished.connect(self._worker_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._worker_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _worker_thread_finished(self) -> None:
        self.worker = None
        self.thread = None

    def _render_scan_log(self, body: str) -> None:
        header = f"开始扫描（{NETWORK_MODE_LABELS.get(self.selected_mode(), '有线网')}）…"
        self.log_view.setPlainText(f"{header}\n\n{body.strip()}")

    def _worker_finished(self, ok: bool, task: str, text: str, data: object) -> None:
        self._set_busy(False)
        if task == "discover_host":
            if ok and isinstance(data, dict):
                host = str(data.get("host", "")).strip()
                matched_mode = str(data.get("matched_mode", "")).strip()
                if host:
                    self.found_host = host
                    self.found_mode = matched_mode
                    self.host_edit.setText(host)
            self._render_scan_log(text)
            if not ok:
                QMessageBox.warning(self, "扫描未完成", "未找到在线的目标设备，请查看下方完整扫描结果。")
            return

        if not ok:
            self._append_log(f"[失败] {text.strip()}")
            QMessageBox.warning(self, "操作失败", text.strip()[-2000:])
            return

        self._append_log(text.strip())
        if task in {"fetch_network_interfaces", "enable_wireless"} and isinstance(data, dict):
            wired = data.get("wired") or {}
            wireless = data.get("wireless") or {}
            wired_mac = normalize_mac_address(str(wired.get("mac", ""))) if wired.get("mac") else ""
            wireless_mac = normalize_mac_address(str(wireless.get("mac", ""))) if wireless.get("mac") else ""
            if wired_mac:
                self.wired_mac_edit.setText(wired_mac)
            if wireless_mac and wireless_mac != wired_mac:
                self.wireless_mac_edit.setText(wireless_mac)
            elif wireless_mac and wireless_mac == wired_mac:
                self._append_log("警告：读取到的无线 MAC 与有线相同，未自动填入无线 MAC。")
            if task == "enable_wireless":
                wireless_ip = str((wireless or {}).get("ip", "")).strip()
                if wireless_ip:
                    self.found_host = wireless_ip
                    self.found_mode = NETWORK_MODE_WIRELESS
                    self.host_edit.setText(wireless_ip)
                    self.mode_buttons[NETWORK_MODE_WIRELESS].setChecked(True)

    def _start_scan(self) -> None:
        self.log_view.clear()
        self._append_log(f"开始扫描（{NETWORK_MODE_LABELS.get(self.selected_mode(), '有线网')}）…")
        payload = {
            "network_mode": self.selected_mode(),
            "mac_address_wired": normalize_mac_address(self.wired_mac_edit.text().strip()),
            "mac_address_wireless": normalize_mac_address(self.wireless_mac_edit.text().strip()),
            "current_host": self.config.host,
        }
        self._start_worker("discover_host", payload)

    def _fetch_macs_from_device(self) -> None:
        self._append_log("正在通过 SSH 读取树莓派网卡 MAC…")
        self._start_worker("fetch_network_interfaces")

    def _enable_wireless(self) -> None:
        reply = QMessageBox.question(
            self,
            "启用无线网络",
            "将通过当前 SSH 连接在树莓派上启用无线网卡。\n"
            "请确保树莓派已配置 WiFi，且当前为有线连接。\n\n是否继续？",
        )
        if reply != QMessageBox.Yes:
            return
        self._append_log("正在远程启用无线网络…")
        self._start_worker("enable_wireless")

    def _apply_and_close(self) -> None:
        wired = normalize_mac_address(self.wired_mac_edit.text().strip())
        wireless = normalize_mac_address(self.wireless_mac_edit.text().strip())
        if self.wired_mac_edit.text().strip() and not wired:
            QMessageBox.warning(self, "MAC 格式错误", "有线 MAC 格式无效。")
            return
        if self.wireless_mac_edit.text().strip() and not wireless:
            QMessageBox.warning(self, "MAC 格式错误", "无线 MAC 格式无效。")
            return
        if wireless and wired and wireless == wired:
            QMessageBox.warning(
                self,
                "MAC 配置错误",
                f"无线 MAC 与有线 MAC 不能相同（{wireless}）。\n请填写 wlan0 的真实 MAC。",
            )
            return

        profile = ConnectionProfile.from_dict(self.profile_store.get_active().to_dict())
        profile.mac_address_wired = wired
        profile.mac_address_wireless = wireless
        profile.mac_address = wired
        profile.network_mode = self.selected_mode()
        host = self.host_edit.text().strip() or self.found_host
        if host:
            profile.apply_host_for_mode(self.selected_mode(), host, HOST_SOURCE_MAC_SCAN)
        self.profile_store.save_profile(profile)
        self.accept()
