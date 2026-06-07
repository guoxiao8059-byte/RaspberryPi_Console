# -*- coding: utf-8 -*-
import os

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QStyle, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from raspberry_pi_console.constants import MULTI_PATH_SEPARATOR

class FileTransferPane(QWidget):
    transfer_requested = Signal(dict)
    remote_browse_requested = Signal(dict)

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.direction_group = QButtonGroup(self)
        self.mode_group = QButtonGroup(self)

        direction_row = QHBoxLayout()
        self.upload_btn = self._make_option_button(
            "🖥️ 》》》 🍓",
        )
        self.download_btn = self._make_option_button(
            "🍓 》》》 🖥️",
        )
        self.direction_group.addButton(self.upload_btn)
        self.direction_group.addButton(self.download_btn)
        self.upload_btn.setChecked(True)
        direction_row.addWidget(self.upload_btn)
        direction_row.addWidget(self.download_btn)

        mode_row = QHBoxLayout()
        self.file_btn = self._make_option_button(self.style().standardIcon(QStyle.SP_FileIcon), "文件")
        self.folder_btn = self._make_option_button(self.style().standardIcon(QStyle.SP_DirIcon), "文件夹")
        self.mode_group.addButton(self.file_btn)
        self.mode_group.addButton(self.folder_btn)
        self.file_btn.setChecked(True)
        mode_row.addWidget(self.file_btn)
        mode_row.addWidget(self.folder_btn)
        mode_row.addStretch(1)

        self.local_edit = QLineEdit(self.settings.value("transfer/local", os.path.expanduser("~")))
        self.remote_edit = QLineEdit(
            self.settings.value("transfer/remote", f"/home/{self.settings.value('ssh/user', 'pi')}/uploads/")
        )

        local_row = QHBoxLayout()
        self.local_browse_btn = QPushButton("选择")
        self.local_browse_btn.clicked.connect(self.choose_local)
        self.transfer_btn = QPushButton("开始传输")
        self.transfer_btn.clicked.connect(self.submit)
        self.local_browse_btn.setFixedWidth(116)
        self.transfer_btn.setFixedWidth(104)
        local_row.addWidget(self.local_edit)
        local_row.addWidget(self.local_browse_btn)
        local_row.addWidget(self.transfer_btn)

        remote_row = QHBoxLayout()
        self.remote_browse_btn = QPushButton("选择文件")
        self.remote_shortcut_combo = QComboBox()
        self.remote_shortcut_combo.addItem("快捷目录", "")
        self.remote_shortcut_combo.addItem("用户目录", "home")
        self.remote_shortcut_combo.addItem("uploads", "uploads")
        self.remote_shortcut_combo.addItem("projects", "projects")
        self.remote_browse_btn.clicked.connect(self.choose_remote)
        self.remote_shortcut_combo.currentIndexChanged.connect(self.apply_remote_shortcut)
        self.remote_browse_btn.setFixedWidth(116)
        self.remote_shortcut_combo.setFixedWidth(104)
        remote_row.addWidget(self.remote_edit, 1)
        remote_row.addWidget(self.remote_browse_btn)
        remote_row.addWidget(self.remote_shortcut_combo)

        option_row = QHBoxLayout()
        option_row.addWidget(QLabel("方向"))
        option_row.addLayout(direction_row, 1)
        option_row.addSpacing(16)
        option_row.addWidget(QLabel("类型"))
        option_row.addLayout(mode_row, 1)
        form.addRow(option_row)
        form.addRow("电脑路径", local_row)
        form.addRow("树莓派路径", remote_row)
        layout.addLayout(form)

        self.transfer_progress_label = QLabel("")
        self.transfer_progress_label.setObjectName("TransferHint")
        self.transfer_progress_bar = QProgressBar()
        self.transfer_progress_bar.setRange(0, 100)
        self.transfer_progress_bar.setValue(0)
        self.transfer_progress_bar.setTextVisible(True)
        self.transfer_progress_bar.setObjectName("TransferProgressBar")
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.transfer_progress_label, 1)
        progress_row.addWidget(self.transfer_progress_bar, 1)
        self._transfer_progress_widget = QWidget()
        self._transfer_progress_widget.setLayout(progress_row)
        self._transfer_progress_widget.setVisible(False)
        layout.addWidget(self._transfer_progress_widget)

        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)
        self.transfer_preview = QTextBrowser()
        self.transfer_preview.setObjectName("TransferPreview")
        preview_layout.addWidget(self.transfer_preview)
        layout.addWidget(preview_group, 1)

        self.upload_btn.toggled.connect(self.update_placeholders)
        self.download_btn.toggled.connect(self.update_placeholders)
        self.file_btn.toggled.connect(self.update_placeholders)
        self.folder_btn.toggled.connect(self.update_placeholders)
        self.local_edit.textChanged.connect(self.update_preview)
        self.remote_edit.textChanged.connect(self._remember_remote_path)
        self.update_placeholders()
        self.update_preview()

    @staticmethod
    def _make_option_button(icon: QIcon | str, text: str = "") -> QToolButton:
        button = QToolButton()
        button.setCheckable(True)
        if isinstance(icon, QIcon):
            button.setIcon(icon)
            button.setText(text)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            button.setText(str(icon))
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setObjectName("TransferOptionButton")
        return button

    def current_direction(self) -> str:
        return "upload" if self.upload_btn.isChecked() else "download"

    def current_mode(self) -> str:
        return "file" if self.file_btn.isChecked() else "folder"

    def username(self) -> str:
        return str(self.settings.value("ssh/user", "pi")).strip() or "pi"

    def fill_remote_home(self):
        self.remote_edit.setText(f"/home/{self.username()}/")

    def fill_remote_uploads(self):
        self.remote_edit.setText(f"/home/{self.username()}/uploads/")

    def fill_remote_projects(self):
        self.remote_edit.setText(f"/home/{self.username()}/projects/")

    def apply_remote_shortcut(self):
        shortcut = self.remote_shortcut_combo.currentData()
        if shortcut == "home":
            self.fill_remote_home()
        elif shortcut == "uploads":
            self.fill_remote_uploads()
        elif shortcut == "projects":
            self.fill_remote_projects()
        else:
            return
        self.remote_shortcut_combo.setCurrentIndex(0)

    @staticmethod
    def _split_local_paths(text: str) -> list[str]:
        return [part.strip() for part in str(text).split(MULTI_PATH_SEPARATOR) if part.strip()]

    @staticmethod
    def _join_local_paths(paths: list[str]) -> str:
        return MULTI_PATH_SEPARATOR.join(paths)

    def _browse_root(self) -> str:
        paths = self._split_local_paths(self.local_edit.text().strip())
        if not paths:
            return os.path.expanduser("~")
        first = paths[0]
        if os.path.isdir(first):
            return first
        parent = os.path.dirname(first)
        return parent or os.path.expanduser("~")

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
            value /= 1024
        return f"{int(size)} B"

    def _build_multi_file_preview(self, paths: list[str]) -> str:
        lines = [f"已选择 {len(paths)} 个文件：", ""]
        for path in paths[:20]:
            try:
                size_text = self._format_size(os.path.getsize(path))
            except OSError:
                size_text = "大小未知"
            lines.append(f"{os.path.basename(path)}  ({size_text})")
            lines.append(path)
        if len(paths) > 20:
            lines.append("")
            lines.append(f"... 其余 {len(paths) - 20} 个文件未展开")
        return "\n".join(lines)

    def update_placeholders(self):
        direction = self.current_direction()
        mode = self.current_mode()
        if direction == "upload":
            self.remote_edit.setPlaceholderText(f"例如：/home/{self.username()}/uploads/")
            self.local_edit.setPlaceholderText("选择本地文件或文件夹")
        elif mode == "file":
            self.remote_edit.setPlaceholderText(f"例如：/home/{self.username()}/test.txt")
            self.local_edit.setPlaceholderText("选择本地保存目录，自动保留树莓派原文件名")
        else:
            self.remote_edit.setPlaceholderText(f"例如：/home/{self.username()}/project 或 /home/{self.username()}/test.txt")
            self.local_edit.setPlaceholderText("选择本地保存目录")
        self.local_browse_btn.setText("选择文件" if direction == "upload" and mode == "file" else "选择目录")
        self.local_browse_btn.setToolTip("可一次选择多个文件" if direction == "upload" and mode == "file" else "")
        self.update_preview()

    @staticmethod
    def _looks_binary(path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                sample = handle.read(2048)
            return b"\x00" in sample
        except OSError:
            return False

    @staticmethod
    def _format_folder_tree(path: str, max_lines: int = 200) -> str:
        lines = [os.path.basename(path.rstrip("/\\")) or path]
        count = 0

        def walk(current_path: str, depth: int):
            nonlocal count
            try:
                entries = sorted(os.scandir(current_path), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError as exc:
                lines.append(f"{'  ' * depth}[读取失败] {exc}")
                return

            for entry in entries:
                if count >= max_lines:
                    lines.append(f"{'  ' * depth}...")
                    return
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{'  ' * depth}{entry.name}{suffix}")
                count += 1
                if entry.is_dir():
                    walk(entry.path, depth + 1)
                    if count >= max_lines:
                        return

        walk(path, 1)
        return "\n".join(lines)

    def update_preview(self):
        direction = self.current_direction()
        mode = self.current_mode()
        raw_local_path = self.local_edit.text().strip()
        local_paths = self._split_local_paths(raw_local_path) if direction == "upload" and mode == "file" else []
        local_path = local_paths[0] if len(local_paths) == 1 else raw_local_path

        if not local_path:
            self.transfer_preview.clear()
            return

        if len(local_paths) > 1:
            self.transfer_preview.setPlainText(self._build_multi_file_preview(local_paths))
            return

        if mode == "folder":
            if os.path.isdir(local_path):
                self.transfer_preview.setPlainText(self._format_folder_tree(local_path))
            else:
                self.transfer_preview.clear()
            return

        if os.path.isfile(local_path):
            if self._looks_binary(local_path):
                self.transfer_preview.clear()
                return
            try:
                with open(local_path, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read(12000)
                if len(content) == 12000:
                    content += "\n\n...[内容过长，已截断]"
                self.transfer_preview.setPlainText(content)
            except OSError as exc:
                self.transfer_preview.setPlainText(f"读取文件失败：{exc}")
            return

        self.transfer_preview.clear()

    def choose_local(self):
        direction = self.current_direction()
        mode = self.current_mode()
        browse_root = self._browse_root()

        if direction == "upload" and mode == "file":
            paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件", browse_root)
            if paths:
                self.local_edit.setText(self._join_local_paths(paths))
            return
        elif direction == "upload" and mode == "folder":
            path = QFileDialog.getExistingDirectory(self, "选择要上传的文件夹", browse_root)
        elif direction == "download" and mode == "file":
            path = QFileDialog.getExistingDirectory(self, "选择保存目录", browse_root)
        else:
            path = QFileDialog.getExistingDirectory(self, "选择保存目录", browse_root)

        if path:
            self.local_edit.setText(path)

    def choose_remote(self):
        select_mode = "folder" if self.current_direction() == "upload" else self.current_mode()
        self.remote_browse_requested.emit(
            {
                "select_mode": select_mode,
                "start_path": self.remote_edit.text().strip() or f"/home/{self.username()}/",
            }
        )

    def set_remote_path(self, remote_path: str):
        self.remote_edit.setText(str(remote_path or "").strip())

    def set_transfer_progress(self, done: int, total: int, label: str) -> None:
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        percent = int(round(done * 100 / total))
        self.transfer_progress_bar.setValue(percent)
        name = str(label or "").strip()
        self.transfer_progress_label.setText(f"正在传输：{name}" if name else "正在传输…")
        self._transfer_progress_widget.setVisible(True)
        self.transfer_btn.setText("传输中…")

    def reset_transfer_progress(self) -> None:
        self.transfer_progress_bar.setValue(0)
        self.transfer_progress_label.clear()
        self._transfer_progress_widget.setVisible(False)
        self.transfer_btn.setText("开始传输")

    def reload_paths_from_settings(self):
        self.local_edit.setText(self.settings.value("transfer/local", os.path.expanduser("~")))
        self.remote_edit.setText(
            self.settings.value("transfer/remote", f"/home/{self.username()}/uploads/")
        )
        self.update_placeholders()

    def _remember_remote_path(self):
        remote_path = self.remote_edit.text().strip()
        if remote_path:
            self.settings.setValue("transfer/remote", remote_path)

    def submit(self):
        local_text = self.local_edit.text().strip()
        local_paths = self._split_local_paths(local_text) if self.current_direction() == "upload" and self.current_mode() == "file" else []
        payload = {
            "direction": self.current_direction(),
            "mode": self.current_mode(),
            "local_path": local_text,
            "remote_path": self.remote_edit.text().strip(),
        }
        if local_paths:
            payload["local_paths"] = local_paths
            if len(local_paths) == 1:
                payload["local_path"] = local_paths[0]
        if not payload["local_path"] or not payload["remote_path"]:
            QMessageBox.warning(self, "路径不完整", "请填写电脑路径和树莓派路径。")
            return

        if payload["direction"] == "upload" and payload["mode"] == "file":
            paths = payload.get("local_paths") or [payload["local_path"]]
            missing = [path for path in paths if not os.path.isfile(path)]
            if missing:
                QMessageBox.warning(
                    self,
                    "类型选择错误",
                    "以下电脑路径不是文件或已经不存在：\n" + "\n".join(missing[:10]),
                )
                return

        if payload["direction"] == "upload" and payload["mode"] == "folder" and not os.path.isdir(payload["local_path"]):
            QMessageBox.warning(self, "类型选择错误", "当前选择的电脑路径不是文件夹。")
            return

        self.settings.setValue("transfer/local", payload["local_path"])
        self.settings.setValue("transfer/remote", payload["remote_path"])
        self.transfer_requested.emit(payload)
