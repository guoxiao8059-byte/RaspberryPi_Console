# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.ui.widgets.remote_browser import RemoteBrowserDialog


class DeployDialog(QDialog):
    def __init__(self, ssh_config: SshConfig, default_remote: str, parent=None):
        super().__init__(parent)
        self.ssh_config = ssh_config
        self.setWindowTitle("一键部署")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.local_dir_edit = QLineEdit()
        local_browse_btn = QPushButton("浏览")
        local_browse_btn.clicked.connect(self._browse_local)
        local_row = QHBoxLayout()
        local_row.addWidget(self.local_dir_edit, 1)
        local_row.addWidget(local_browse_btn)

        default_remote = default_remote or f"/home/{ssh_config.username}/projects/"
        self.remote_dir_edit = QLineEdit(default_remote)
        remote_browse_btn = QPushButton("浏览")
        remote_browse_btn.clicked.connect(self._browse_remote)
        remote_row = QHBoxLayout()
        remote_row.addWidget(self.remote_dir_edit, 1)
        remote_row.addWidget(remote_browse_btn)

        self.command_edit = QPlainTextEdit()
        self.command_edit.setPlaceholderText(
            "上传完成后在远程项目目录执行的命令；若使用 install.sh，请确保本地项目根目录下有该文件"
        )
        self.command_edit.setFixedHeight(100)
        self._sync_default_command(self.local_dir_edit.text().strip())
        self.local_dir_edit.textChanged.connect(self._sync_default_command_from_edit)

        form.addRow("本地项目目录", local_row)
        form.addRow("远程目录", remote_row)
        form.addRow("部署命令", self.command_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync_default_command_from_edit(self, text: str) -> None:
        self._sync_default_command(text.strip())

    def _sync_default_command(self, local_dir: str) -> None:
        if self.command_edit.toPlainText().strip():
            return
        if local_dir and os.path.isfile(os.path.join(local_dir, "install.sh")):
            self.command_edit.setPlainText("bash install.sh")
        elif local_dir and os.path.isfile(os.path.join(local_dir, "requirements.txt")):
            self.command_edit.setPlainText("python3 -m pip install --user -r requirements.txt")

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地项目目录", os.path.expanduser("~"))
        if path:
            self.local_dir_edit.setText(path)
            if not self.command_edit.toPlainText().strip():
                self._sync_default_command(path)

    def _browse_remote(self) -> None:
        dialog = RemoteBrowserDialog(
            self.ssh_config,
            self.remote_dir_edit.text().strip(),
            "folder",
            self,
        )
        if dialog.exec() == QDialog.Accepted and dialog.selected_path:
            self.remote_dir_edit.setText(dialog.selected_path)

    def payload(self) -> dict:
        return {
            "local_dir": self.local_dir_edit.text().strip(),
            "remote_dir": self.remote_dir_edit.text().strip(),
            "command": self.command_edit.toPlainText().strip(),
            "timeout": 900,
        }
