# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from raspberry_pi_console.constants import APP_NAME, APP_ORG, HOST_SOURCE_MANUAL
from raspberry_pi_console.core.credentials import get_secret, set_secret, using_keyring
from raspberry_pi_console.core.network import normalize_mac_address
from raspberry_pi_console.core.profiles import ConnectionProfile, ProfileStore


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings(APP_ORG, APP_NAME)
        self.store = ProfileStore(self.settings)
        self.current_profile = ConnectionProfile.from_dict(self.store.get_active().to_dict())
        self.setWindowTitle("连接与设备设置")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        self.connection_tab = QWidget()
        self.profiles_tab = QWidget()
        self._build_connection_tab()
        self._build_profiles_tab()
        self.tabs.addTab(self.connection_tab, "当前连接")
        self.tabs.addTab(self.profiles_tab, "设备列表")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_profile_into_form(self.current_profile)

    def _build_connection_tab(self) -> None:
        layout = QVBoxLayout(self.connection_tab)
        form = QFormLayout()

        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.user_edit = QLineEdit()
        self.wired_mac_edit = QLineEdit()
        self.wired_mac_edit.setPlaceholderText("例如 DC:A6:32:12:34:56")
        self.wireless_mac_edit = QLineEdit()
        self.wireless_mac_edit.setPlaceholderText("例如 E4:5F:01:12:34:56")

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("密码登录", "password")
        self.auth_combo.addItem("SSH 私钥", "key")
        self.auth_combo.currentIndexChanged.connect(self._sync_auth_fields)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("留空表示不修改已保存的密码")

        self.key_path_edit = QLineEdit()
        self.key_browse_btn = QPushButton("浏览")
        self.key_browse_btn.clicked.connect(self._browse_key_file)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_path_edit, 1)
        key_row.addWidget(self.key_browse_btn)

        self.key_passphrase_edit = QLineEdit()
        self.key_passphrase_edit.setEchoMode(QLineEdit.Password)
        self.key_passphrase_edit.setPlaceholderText("私钥口令（可选，留空表示不修改）")

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(10, 600)
        self.refresh_spin.setSuffix(" 秒")

        self.default_remote_edit = QLineEdit()
        self.default_local_edit = QLineEdit()

        cred_hint = "凭据保存在 Windows Credential Manager。" if using_keyring() else "未检测到 keyring，凭据将保存在本地配置中。"
        self.cred_hint_label = QLabel(cred_hint)

        form.addRow("树莓派 IP", self.host_edit)
        form.addRow("SSH 端口", self.port_spin)
        form.addRow("用户名", self.user_edit)
        form.addRow("认证方式", self.auth_combo)
        form.addRow("密码", self.password_edit)
        form.addRow("私钥文件", key_row)
        form.addRow("私钥口令", self.key_passphrase_edit)
        form.addRow("有线 MAC", self.wired_mac_edit)
        form.addRow("无线 MAC", self.wireless_mac_edit)
        form.addRow("自动刷新", self.refresh_spin)
        form.addRow("默认远程目录", self.default_remote_edit)
        form.addRow("默认本地目录", self.default_local_edit)
        form.addRow("凭据存储", self.cred_hint_label)
        layout.addLayout(form)

        self.refresh_spin.setValue(int(self.settings.value("app/refresh_interval", 30)))
        self.default_local_edit.setText(self.settings.value("transfer/local", os.path.expanduser("~")))

    def _build_profiles_tab(self) -> None:
        layout = QVBoxLayout(self.profiles_tab)
        self.profile_list = QListWidget()
        self.profile_list.currentItemChanged.connect(self._on_profile_selected)
        layout.addWidget(self.profile_list, 1)

        row = QHBoxLayout()
        self.profile_add_btn = QPushButton("新增")
        self.profile_delete_btn = QPushButton("删除")
        self.profile_switch_btn = QPushButton("切换为当前")
        self.profile_add_btn.clicked.connect(self._add_profile)
        self.profile_delete_btn.clicked.connect(self._delete_profile)
        self.profile_switch_btn.clicked.connect(self._switch_profile)
        row.addWidget(self.profile_add_btn)
        row.addWidget(self.profile_delete_btn)
        row.addWidget(self.profile_switch_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._reload_profile_list()

    def _reload_profile_list(self) -> None:
        self.profile_list.clear()
        active_id = self.store.get_active_id()
        for profile in self.store.list_profiles():
            item = QListWidgetItem(f"{profile.name}  ({profile.username}@{profile.host or 'MAC'})")
            item.setData(256, profile.id)
            if profile.id == active_id:
                item.setText(f"★ {item.text()}")
            self.profile_list.addItem(item)

    def _load_profile_into_form(self, profile: ConnectionProfile) -> None:
        self.current_profile = ConnectionProfile.from_dict(profile.to_dict())
        self.host_edit.setText(profile.active_host())
        self.port_spin.setValue(int(profile.port))
        self.user_edit.setText(profile.username)
        self.wired_mac_edit.setText(profile.mac_address_wired)
        self.wireless_mac_edit.setText(profile.mac_address_wireless)
        self.key_path_edit.setText(profile.key_path)
        auth_index = max(0, self.auth_combo.findData(profile.auth_method))
        self.auth_combo.setCurrentIndex(auth_index)
        self.password_edit.clear()
        self.key_passphrase_edit.clear()
        username = profile.username or "pi"
        self.default_remote_edit.setText(
            self.settings.value("transfer/remote", f"/home/{username}/uploads/")
        )
        self._sync_auth_fields()

    def _sync_auth_fields(self) -> None:
        use_key = self.auth_combo.currentData() == "key"
        self.password_edit.setEnabled(not use_key)
        self.key_path_edit.setEnabled(use_key)
        self.key_browse_btn.setEnabled(use_key)
        self.key_passphrase_edit.setEnabled(use_key)

    def _browse_key_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 SSH 私钥", os.path.expanduser("~"), "All Files (*)")
        if path:
            self.key_path_edit.setText(path)

    def _on_profile_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        profile_id = current.data(256)
        for profile in self.store.list_profiles():
            if profile.id == profile_id:
                self._load_profile_into_form(profile)
                break

    def _add_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "新增设备", "设备名称：", text="新设备")
        if not ok or not name.strip():
            return
        profile = self.store.add_profile(name.strip())
        self._reload_profile_list()
        self._load_profile_into_form(profile)

    def _delete_profile(self) -> None:
        if len(self.store.list_profiles()) <= 1:
            QMessageBox.warning(self, "无法删除", "至少需要保留一个设备配置。")
            return
        item = self.profile_list.currentItem()
        if item is None:
            return
        profile_id = item.data(256)
        if QMessageBox.question(self, "确认删除", "确定删除选中的设备配置吗？") != QMessageBox.Yes:
            return
        self.store.delete_profile(profile_id)
        self._reload_profile_list()
        self._load_profile_into_form(self.store.get_active())

    def _switch_profile(self) -> None:
        item = self.profile_list.currentItem()
        if item is None:
            return
        if not self._validate_and_apply_profile(save_secrets=False):
            return
        self.store.set_active(item.data(256))
        self._reload_profile_list()
        QMessageBox.information(self, "已切换", "当前设备已切换。")

    def _validate_and_apply_profile(self, save_secrets: bool = True) -> bool:
        username = self.user_edit.text().strip() or "pi"
        wired_mac = normalize_mac_address(self.wired_mac_edit.text().strip())
        wireless_mac = normalize_mac_address(self.wireless_mac_edit.text().strip())
        if self.wired_mac_edit.text().strip() and not wired_mac:
            QMessageBox.warning(self, "MAC 格式错误", "请输入正确的有线 MAC 地址。")
            return False
        if self.wireless_mac_edit.text().strip() and not wireless_mac:
            QMessageBox.warning(self, "MAC 格式错误", "请输入正确的无线 MAC 地址。")
            return False

        auth_method = self.auth_combo.currentData()
        key_path = self.key_path_edit.text().strip()
        if auth_method == "key" and not key_path:
            QMessageBox.warning(self, "私钥未设置", "请选择 SSH 私钥文件。")
            return False

        profile = ConnectionProfile.from_dict(self.current_profile.to_dict())
        profile.port = self.port_spin.value()
        profile.username = username
        profile.mac_address = wired_mac
        profile.mac_address_wired = wired_mac
        profile.mac_address_wireless = wireless_mac
        profile.auth_method = auth_method
        profile.key_path = key_path
        profile.apply_host_for_mode(profile.network_mode, self.host_edit.text().strip(), HOST_SOURCE_MANUAL)
        self.store.save_profile(profile)
        self.current_profile = profile

        if save_secrets:
            if auth_method == "password":
                if self.password_edit.text():
                    set_secret(profile.id, "password", self.password_edit.text())
                set_secret(profile.id, "key_passphrase", "")
            else:
                set_secret(profile.id, "password", "")
                if self.key_passphrase_edit.text():
                    set_secret(profile.id, "key_passphrase", self.key_passphrase_edit.text())

        self.settings.setValue("app/refresh_interval", self.refresh_spin.value())
        remote_dir = self.default_remote_edit.text().strip() or f"/home/{username}/uploads/"
        self.settings.setValue("transfer/remote", remote_dir)
        self.settings.setValue("transfer/local", self.default_local_edit.text().strip() or os.path.expanduser("~"))
        return True

    def save(self) -> None:
        if not self._validate_and_apply_profile(save_secrets=True):
            return
        if self.auth_combo.currentData() == "password" and not get_secret(self.current_profile.id, "password"):
            if not self.password_edit.text():
                QMessageBox.warning(self, "密码未设置", "请填写 SSH 密码，或改用私钥登录。")
                return
        self.accept()
