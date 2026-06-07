# -*- coding: utf-8 -*-
import os

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.workers.remote_browser_worker import RemoteBrowserWorker


class RemoteBrowserDialog(QDialog):
    def __init__(self, config: SshConfig, start_path: str, select_mode: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.select_mode = select_mode
        self.selected_path = ""
        self.current_root = SshClient.normalize_remote_path(start_path or f"/home/{config.username}/")
        self.thread = None
        self.worker = None
        self._pending_child_item = None

        self.setWindowTitle("浏览树莓派文件")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.path_label = QLabel(self.current_root)
        self.up_btn = QPushButton("上级")
        self.home_btn = QPushButton("用户目录")
        self.refresh_btn = QPushButton("刷新")
        toolbar.addWidget(QLabel("当前位置"))
        toolbar.addWidget(self.path_label, 1)
        toolbar.addWidget(self.up_btn)
        toolbar.addWidget(self.home_btn)
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.hint_label = QLabel("请选择树莓派上的文件夹。" if select_mode == "folder" else "请选择树莓派上的文件。")
        layout.addWidget(self.hint_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "类型", "大小"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemExpanded.connect(self._load_children_if_needed)
        self.tree.itemSelectionChanged.connect(self._update_selected_hint)
        self.tree.itemDoubleClicked.connect(self._handle_double_click)
        layout.addWidget(self.tree, 1)

        self.selected_label = QLabel("未选择")
        layout.addWidget(self.selected_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.up_btn.clicked.connect(self.go_up)
        self.home_btn.clicked.connect(self.go_home)
        self.refresh_btn.clicked.connect(self.reload_root)

        self._set_busy(True)
        self._request_root(self.current_root)

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def _stop_worker(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)

    def _set_busy(self, busy: bool):
        self.tree.setEnabled(not busy)
        self.up_btn.setEnabled(not busy)
        self.home_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)

    def _start_worker(self, operation: str, payload: dict | None = None):
        if self.thread is not None and self.thread.isRunning():
            return
        self._set_busy(True)
        self.thread = QThread(self)
        self.worker = RemoteBrowserWorker(self.config, operation, payload)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._worker_done)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _thread_finished(self):
        self.worker = None
        self.thread = None

    def _worker_done(self, ok: bool, operation: str, data: object):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "读取失败", str(data))
            if operation == "load_root":
                self.tree.clear()
            elif operation == "list_children" and self._pending_child_item is not None:
                self._pending_child_item.takeChildren()
            self._pending_child_item = None
            return

        if operation == "load_root" and isinstance(data, dict):
            self._apply_root(data.get("path", "/"), data.get("entries", []))
            return

        if operation == "list_children" and isinstance(data, dict):
            item = self._pending_child_item
            self._pending_child_item = None
            if item is not None:
                self._apply_children(item, data.get("entries", []))

    @staticmethod
    def _format_remote_size(size: int) -> str:
        if size <= 0:
            return "-"
        value = float(size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
            value /= 1024
        return f"{int(size)} B"

    def _add_placeholder(self, item: QTreeWidgetItem):
        placeholder = QTreeWidgetItem(["加载中..."])
        placeholder.setData(0, Qt.UserRole, "__placeholder__")
        item.addChild(placeholder)

    def _create_item(self, entry: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [
                entry["name"],
                "文件夹" if entry["is_dir"] else "文件",
                "-" if entry["is_dir"] else self._format_remote_size(entry["size"]),
            ]
        )
        item.setData(0, Qt.UserRole, entry["path"])
        item.setData(0, Qt.UserRole + 1, entry["is_dir"])
        item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon if entry["is_dir"] else QStyle.SP_FileIcon))
        if entry["is_dir"]:
            self._add_placeholder(item)
        return item

    def _apply_children(self, parent_item: QTreeWidgetItem, entries: list[dict]):
        parent_item.takeChildren()
        for entry in entries:
            parent_item.addChild(self._create_item(entry))

    def _apply_root(self, path: str, entries: list[dict]):
        normalized = SshClient.normalize_remote_path(path)
        self.current_root = normalized
        self.path_label.setText(normalized)
        self.tree.clear()
        root_name = normalized if normalized == "/" else os.path.basename(normalized)
        root_item = QTreeWidgetItem([root_name or normalized, "文件夹", "-"])
        root_item.setData(0, Qt.UserRole, normalized)
        root_item.setData(0, Qt.UserRole + 1, True)
        root_item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
        self.tree.addTopLevelItem(root_item)
        self._apply_children(root_item, entries)
        root_item.setExpanded(True)
        self.tree.setCurrentItem(root_item)
        self._update_selected_hint()

    def _request_root(self, path: str):
        self._start_worker("load_root", {"path": path})

    def _load_children_if_needed(self, item: QTreeWidgetItem):
        if item.childCount() != 1:
            return
        first_child = item.child(0)
        if first_child.data(0, Qt.UserRole) != "__placeholder__":
            return
        self._pending_child_item = item
        self._start_worker("list_children", {"path": str(item.data(0, Qt.UserRole) or "/")})

    def _selected_item(self) -> QTreeWidgetItem | None:
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _update_selected_hint(self):
        item = self._selected_item()
        if item is None:
            self.selected_label.setText("未选择")
            return
        remote_path = str(item.data(0, Qt.UserRole) or "")
        if not remote_path or remote_path == "__placeholder__":
            self.selected_label.setText("未选择")
            return
        self.selected_label.setText(f"当前选择：{remote_path}")

    def _handle_double_click(self, item: QTreeWidgetItem, _column: int):
        is_dir = bool(item.data(0, Qt.UserRole + 1))
        if self.select_mode == "file" and not is_dir:
            self.accept_selection()

    def go_home(self):
        self._request_root(f"/home/{self.config.username}/")

    def go_up(self):
        if self.current_root == "/":
            return
        parent = os.path.dirname(self.current_root.rstrip("/")) or "/"
        self._request_root(parent)

    def reload_root(self):
        self._request_root(self.current_root)

    def accept_selection(self):
        item = self._selected_item()
        if item is None:
            QMessageBox.information(self, "未选择", "请先选择一个目标。")
            return
        remote_path = str(item.data(0, Qt.UserRole) or "")
        is_dir = bool(item.data(0, Qt.UserRole + 1))
        if not remote_path or remote_path == "__placeholder__":
            QMessageBox.information(self, "未选择", "请先选择一个目标。")
            return
        if self.select_mode == "folder" and not is_dir:
            QMessageBox.information(self, "选择错误", "当前模式需要选择文件夹。")
            return
        if self.select_mode == "file" and is_dir:
            QMessageBox.information(self, "选择错误", "当前模式需要选择文件。")
            return
        self.selected_path = remote_path
        self.accept()
