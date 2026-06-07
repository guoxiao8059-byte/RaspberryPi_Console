# -*- coding: utf-8 -*-
import base64
import os
import re
import shlex
import subprocess
import sys

from PySide6.QtCore import QSettings, QThread, QTimer, Qt
from PySide6.QtGui import QAction, QFont, QTextCursor
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from raspberry_pi_console.app.deploy_dialog import DeployDialog
from raspberry_pi_console.app.mac_discovery_dialog import MacDiscoveryDialog
from raspberry_pi_console.app.settings_dialog import SettingsDialog
from raspberry_pi_console.constants import (
    APP_NAME,
    APP_ORG,
    HOST_SOURCE_MAC_SCAN,
    HOST_SOURCE_MANUAL,
    HOST_SOURCE_REMOTE_WIFI,
    NETWORK_MODE_LABELS,
    NETWORK_MODE_WIRED,
    NETWORK_MODE_WIRELESS,
    NETWORK_MODES,
    normalize_network_mode,
)
from raspberry_pi_console.core.config import SshConfig
from raspberry_pi_console.core.metrics_history import record_snapshot
from raspberry_pi_console.core.network import format_connection_detail, normalize_mac_address
from raspberry_pi_console.core.profiles import ProfileStore
from raspberry_pi_console.core.ssh_pool import SshSessionPool
from raspberry_pi_console.remote.dashboard import parse_process_rows
from raspberry_pi_console.ui.styles import load_stylesheet
from raspberry_pi_console.ui.widgets.file_transfer_pane import FileTransferPane
from raspberry_pi_console.ui.widgets.metrics_chart import MetricsChartWidget
from raspberry_pi_console.ui.widgets.remote_browser import RemoteBrowserDialog
from raspberry_pi_console.workers.log_tail_worker import LogTailWorker
from raspberry_pi_console.workers.worker import Worker

_UNIT_NAME_RE = re.compile(r"^[a-zA-Z0-9@._-]+(\.[a-zA-Z0-9@._-]+)*$")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(APP_ORG, APP_NAME)
        self.profile_store = ProfileStore(self.settings)
        self.thread = None
        self.worker = None
        self.log_tail_thread = None
        self.log_tail_worker = None
        self.busy = False
        self.active_task = ""
        self.active_payload = {}
        self.service_rows = []
        self.docker_rows = []
        self.package_data = {"apt": [], "pip": [], "upgradable": []}
        self._cpu_process_rows = []
        self._mem_process_rows = []

        self.setWindowTitle("树莓派远程运维台")
        self.resize(960, 672)
        self.setMinimumSize(960, 672)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)

        self._build_ui()
        self._build_menu()
        self._apply_style()
        self._refresh_header()
        self._setup_timer()
        self._system("客户端已启动，正在后台测试连接并刷新仪表盘…")
        QTimer.singleShot(400, self._startup_auto_connect)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        self.header = QFrame()
        self.header.setObjectName("Header")
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(8)

        header_top = QHBoxLayout()

        title_box = QVBoxLayout()
        self.title_label = QLabel("RASPBERRY PI REMOTE CONSOLE")
        self.title_label.setObjectName("Title")
        title_box.addWidget(self.title_label)

        status_box = QVBoxLayout()
        self.status_text = QLabel()
        self.status_text.setObjectName("StatusText")
        status_box.addWidget(self.status_text, alignment=Qt.AlignRight)

        header_top.addLayout(title_box, 1)
        header_top.addLayout(status_box)
        header_layout.addLayout(header_top)

        metric_row = QHBoxLayout()
        metric_row.setSpacing(8)
        self.usage_widgets = {}
        header_metrics = [
            ("cpu_percent", "CPU"),
            ("mem_percent", "内存"),
            ("disk_percent", "磁盘"),
            ("swap_percent", "Swap"),
            ("temp_value", "温度"),
        ]
        for key, title in header_metrics:
            card = QFrame()
            card.setObjectName("HeaderMetricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(3)

            label_row = QHBoxLayout()
            label_row.setContentsMargins(0, 0, 0, 0)
            title_label = QLabel(title)
            title_label.setObjectName("HeaderMetricTitle")
            value_label = QLabel("--")
            value_label.setObjectName("HeaderMetricValue")
            label_row.addWidget(title_label)
            label_row.addStretch(1)
            label_row.addWidget(value_label)

            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setTextVisible(False)
            progress.setObjectName("HeaderUsageBar")

            hint_label = QLabel("等待刷新")
            hint_label.setObjectName("HeaderMetricHint")

            card_layout.addLayout(label_row)
            card_layout.addWidget(progress)
            card_layout.addWidget(hint_label)
            self.usage_widgets[key] = {"value": value_label, "progress": progress, "hint": hint_label, "card": card}
            metric_row.addWidget(card, 1)
        header_layout.addLayout(metric_row)

        self.splitter = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")

        self.dashboard_tab = self._build_dashboard_tab()
        self.cpu_process_tab = self._build_process_tab("高 CPU 进程")
        self.mem_process_tab = self._build_process_tab("高内存进程")
        self.services_tab = self._build_services_tab()
        self.logs_tab = self._build_logs_tab()
        self.packages_tab = self._build_packages_tab()
        self.docker_tab = self._build_docker_tab()
        self.trends_tab = self._build_trends_tab()
        self.transfer_tab = self._build_transfer_tab()
        self.terminal_tab = self._build_terminal_tab()

        self.tabs.addTab(self.dashboard_tab, "仪表盘")
        self.tabs.addTab(self.transfer_tab, "文件传输")
        self.tabs.addTab(self.terminal_tab, "PowerShell")
        self.tabs.addTab(self.docker_tab, "Docker")
        self.tabs.addTab(self.trends_tab, "趋势")
        self.tabs.addTab(self.cpu_process_tab, "CPU 进程")
        self.tabs.addTab(self.mem_process_tab, "内存进程")
        self.tabs.addTab(self.packages_tab, "系统软件")
        self.tabs.addTab(self.services_tab, "服务")
        self.tabs.addTab(self.logs_tab, "日志")

        self.output_view = QTextBrowser()
        self.output_view.setObjectName("OutputView")
        self.output_view.setMinimumHeight(120)  # 单位像素
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(self.output_view)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 10)

        outer.addWidget(self.header)
        outer.addWidget(self.splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def _build_dashboard_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        stats = QGridLayout()
        stats.setHorizontalSpacing(10)
        stats.setVerticalSpacing(10)
        self.dashboard_labels = {}
        cards = [
            ("host", "主机名"),
            ("model", "设备型号"),
            ("kernel", "内核版本"),
            ("arch", "系统架构"),
            ("uptime", "在线时长"),
            ("load", "系统负载"),
            ("temp", "CPU 温度"),
        ]
        for index, (key, title) in enumerate(cards):
            card = QFrame()
            card.setObjectName("StatCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            title_label = QLabel(title)
            title_label.setObjectName("StatTitle")
            value_label = QLabel("--")
            value_label.setObjectName("StatValue")
            value_label.setWordWrap(True)
            card_layout.addWidget(title_label)
            card_layout.addWidget(value_label)
            self.dashboard_labels[key] = value_label
            stats.addWidget(card, index // 3, index % 3)
        layout.addLayout(stats)
        layout.addStretch(1)
        return page

    def _build_process_tab(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["PID", "进程", "CPU %", "MEM %"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        group_layout.addWidget(table)
        layout.addWidget(group, 1)

        action_row = QHBoxLayout()
        kill_btn = QPushButton("结束进程 (SIGTERM)")
        force_kill_btn = QPushButton("强制结束 (SIGKILL)")
        if "CPU" in title:
            kill_btn.clicked.connect(lambda: self.kill_selected_process(15, "cpu"))
            force_kill_btn.clicked.connect(lambda: self.kill_selected_process(9, "cpu"))
        else:
            kill_btn.clicked.connect(lambda: self.kill_selected_process(15, "mem"))
            force_kill_btn.clicked.connect(lambda: self.kill_selected_process(9, "mem"))
        action_row.addWidget(kill_btn)
        action_row.addWidget(force_kill_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        if "CPU" in title:
            self.top_cpu_view = table
        else:
            self.top_mem_view = table

        return page

    def _build_services_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        control_row = QHBoxLayout()
        self.service_scope_combo = QComboBox()
        self.service_scope_combo.addItem("系统服务", "system")
        self.service_scope_combo.addItem("用户服务", "user")
        self.service_scope_combo.currentIndexChanged.connect(self.refresh_services)
        self.service_filter_edit = QLineEdit()
        self.service_filter_edit.setPlaceholderText("过滤服务名或描述")
        self.service_filter_edit.textChanged.connect(self.apply_service_filter)
        self.service_refresh_btn = QPushButton("刷新服务")
        self.service_refresh_btn.clicked.connect(self.refresh_services)
        control_row.addWidget(QLabel("范围"))
        control_row.addWidget(self.service_scope_combo)
        control_row.addWidget(self.service_filter_edit, 1)
        control_row.addWidget(self.service_refresh_btn)
        layout.addLayout(control_row)

        self.service_table = QTableWidget(0, 5)
        self.service_table.setHorizontalHeaderLabels(["服务名", "Load", "Active", "Sub", "说明"])
        self.service_table.verticalHeader().setVisible(False)
        self.service_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.service_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.service_table.setAlternatingRowColors(True)
        self.service_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.service_table, 1)

        action_row = QHBoxLayout()
        self.service_status_btn = QPushButton("查看状态")
        self.service_logs_btn = QPushButton("查看日志")
        self.service_start_btn = QPushButton("启动")
        self.service_restart_btn = QPushButton("重启")
        self.service_stop_btn = QPushButton("停止")
        self.service_status_btn.clicked.connect(self.show_service_status)
        self.service_logs_btn.clicked.connect(self.open_service_logs)
        self.service_start_btn.clicked.connect(lambda: self.service_action("start"))
        self.service_restart_btn.clicked.connect(lambda: self.service_action("restart"))
        self.service_stop_btn.clicked.connect(lambda: self.service_action("stop"))
        for button in (
            self.service_status_btn,
            self.service_logs_btn,
            self.service_start_btn,
            self.service_restart_btn,
            self.service_stop_btn,
        ):
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        return page

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        control_row = QHBoxLayout()
        self.logs_mode_combo = QComboBox()
        self.logs_mode_combo.addItem("系统日志", "system")
        self.logs_mode_combo.addItem("服务日志", "service")
        self.logs_mode_combo.addItem("内核日志", "kernel")
        self.logs_service_edit = QLineEdit()
        self.logs_service_edit.setPlaceholderText("服务名，例如 ssh.service")
        self.logs_lines_spin = QSpinBox()
        self.logs_lines_spin.setRange(20, 5000)
        self.logs_lines_spin.setValue(200)
        self.logs_scope_combo = QComboBox()
        self.logs_scope_combo.addItem("system", "system")
        self.logs_scope_combo.addItem("user", "user")
        self.logs_refresh_btn = QPushButton("加载日志")
        self.logs_refresh_btn.clicked.connect(self.load_logs)
        control_row.addWidget(QLabel("类型"))
        control_row.addWidget(self.logs_mode_combo)
        control_row.addWidget(QLabel("服务"))
        control_row.addWidget(self.logs_service_edit, 1)
        control_row.addWidget(QLabel("范围"))
        control_row.addWidget(self.logs_scope_combo)
        control_row.addWidget(QLabel("行数"))
        control_row.addWidget(self.logs_lines_spin)
        self.logs_export_btn = QPushButton("导出")
        self.logs_tail_btn = QPushButton("实时跟踪")
        self.logs_stop_tail_btn = QPushButton("停止跟踪")
        self.logs_export_btn.clicked.connect(self.export_logs)
        self.logs_tail_btn.clicked.connect(self.start_log_tail)
        self.logs_stop_tail_btn.clicked.connect(self.stop_log_tail)
        control_row.addWidget(self.logs_refresh_btn)
        control_row.addWidget(self.logs_export_btn)
        control_row.addWidget(self.logs_tail_btn)
        control_row.addWidget(self.logs_stop_tail_btn)
        layout.addLayout(control_row)

        self.logs_view = QTextBrowser()
        self.logs_view.setObjectName("LogsView")
        layout.addWidget(self.logs_view, 1)
        return page

    def _build_packages_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        top_row = QHBoxLayout()
        self.package_source_combo = QComboBox()
        self.package_source_combo.addItem("APT 已安装", "apt")
        self.package_source_combo.addItem("PIP 已安装", "pip")
        self.package_source_combo.addItem("可升级软件", "upgradable")
        self.package_source_combo.currentIndexChanged.connect(self.apply_package_filter)
        self.package_filter_edit = QLineEdit()
        self.package_filter_edit.setPlaceholderText("过滤软件名或版本")
        self.package_filter_edit.textChanged.connect(self.apply_package_filter)
        self.package_refresh_btn = QPushButton("刷新软件清单")
        self.package_refresh_btn.clicked.connect(self.refresh_packages)
        top_row.addWidget(QLabel("来源"))
        top_row.addWidget(self.package_source_combo)
        top_row.addWidget(self.package_filter_edit, 1)
        top_row.addWidget(self.package_refresh_btn)
        self.package_upgrade_all_btn = QPushButton("升级全部")
        self.package_upgrade_sel_btn = QPushButton("升级选中")
        self.package_upgrade_all_btn.clicked.connect(self.upgrade_all_packages)
        self.package_upgrade_sel_btn.clicked.connect(self.upgrade_selected_packages)
        top_row.addWidget(self.package_upgrade_all_btn)
        top_row.addWidget(self.package_upgrade_sel_btn)
        layout.addLayout(top_row)

        stats_row = QHBoxLayout()
        self.apt_count_label = QLabel("APT: 0")
        self.pip_count_label = QLabel("PIP: 0")
        self.upgradable_count_label = QLabel("可升级: 0")
        stats_row.addWidget(self.apt_count_label)
        stats_row.addWidget(self.pip_count_label)
        stats_row.addWidget(self.upgradable_count_label)
        stats_row.addStretch(1)
        layout.addLayout(stats_row)

        self.package_table = QTableWidget(0, 3)
        self.package_table.setHorizontalHeaderLabels(["名称", "版本", "附加信息"])
        self.package_table.verticalHeader().setVisible(False)
        self.package_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.package_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.package_table.setAlternatingRowColors(True)
        self.package_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.package_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.package_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.package_table, 1)
        return page

    def _build_docker_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        top_row = QHBoxLayout()
        self.docker_refresh_btn = QPushButton("刷新容器")
        self.docker_refresh_btn.clicked.connect(self.refresh_docker)
        self.docker_start_btn = QPushButton("启动")
        self.docker_stop_btn = QPushButton("停止")
        self.docker_restart_btn = QPushButton("重启")
        self.docker_logs_btn = QPushButton("查看日志")
        self.docker_start_btn.clicked.connect(lambda: self.docker_action("start"))
        self.docker_stop_btn.clicked.connect(lambda: self.docker_action("stop"))
        self.docker_restart_btn.clicked.connect(lambda: self.docker_action("restart"))
        self.docker_logs_btn.clicked.connect(lambda: self.docker_action("logs"))
        top_row.addWidget(self.docker_refresh_btn)
        top_row.addStretch(1)
        top_row.addWidget(self.docker_start_btn)
        top_row.addWidget(self.docker_stop_btn)
        top_row.addWidget(self.docker_restart_btn)
        top_row.addWidget(self.docker_logs_btn)
        layout.addLayout(top_row)

        self.docker_table = QTableWidget(0, 5)
        self.docker_table.setHorizontalHeaderLabels(["ID", "名称", "状态", "镜像", "端口"])
        self.docker_table.verticalHeader().setVisible(False)
        self.docker_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.docker_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.docker_table.setAlternatingRowColors(True)
        self.docker_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.docker_table, 1)
        return page

    def _build_trends_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.metrics_chart = MetricsChartWidget()
        layout.addWidget(self.metrics_chart, 1)
        return page

    def _build_transfer_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.transfer_pane = FileTransferPane(self.settings)
        self.transfer_pane.transfer_requested.connect(self.start_transfer)
        self.transfer_pane.remote_browse_requested.connect(self.open_remote_browser)
        layout.addWidget(self.transfer_pane)
        layout.addStretch(1)
        return page

    def _build_terminal_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.ssh_command_view = QLineEdit()
        self.ssh_command_view.setReadOnly(True)
        self.ssh_command_view.setPlaceholderText("连接信息保存后，会在这里生成 SSH 命令。")
        self.copy_ssh_btn = QPushButton("复制 SSH 命令")
        self.copy_ssh_btn.clicked.connect(self.copy_ssh_command)
        self.open_ps_btn = QPushButton("打开 PowerShell")
        self.open_ps_btn.setObjectName("PowerShellButton")
        self.open_ps_btn.clicked.connect(self.open_powershell)
        self.open_wt_btn = QPushButton("打开 Terminal")
        self.open_wt_btn.clicked.connect(self.open_windows_terminal)

        command_group = QGroupBox("远程命令")
        command_layout = QVBoxLayout(command_group)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.ssh_command_view, 1)
        preset_row.addWidget(self.copy_ssh_btn)
        preset_row.addWidget(self.open_ps_btn)
        preset_row.addWidget(self.open_wt_btn)
        self.command_preset_combo = QComboBox()
        self.command_preset_combo.addItem("选择常用命令", "")
        self.command_preset_combo.addItem("系统摘要", "uname -a && cat /etc/os-release | head")
        self.command_preset_combo.addItem("磁盘使用情况", "df -h")
        self.command_preset_combo.addItem("内存使用情况", "free -m")
        self.command_preset_combo.addItem("CPU 信息", "lscpu")
        self.command_preset_combo.addItem("进程概览", "ps aux --sort=-%cpu | head -20")
        self.command_preset_combo.addItem("内存占用前 20", "ps aux --sort=-%mem | head -20")
        self.command_preset_combo.addItem("网络端口", "ss -tulpn")
        self.command_preset_combo.addItem("IP 与路由", "ip addr && echo && ip route")
        self.command_preset_combo.addItem("网络连通性", "ping -c 4 8.8.8.8")
        self.command_preset_combo.addItem("USB 设备", "lsusb")
        self.command_preset_combo.addItem("磁盘挂载", "lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINT")
        self.command_preset_combo.addItem("温度与频率", "vcgencmd measure_temp && vcgencmd measure_clock arm")
        self.command_preset_combo.addItem("最近系统日志", "journalctl -n 80 --no-pager")
        self.command_preset_combo.addItem("开机启动服务失败项", "systemctl --failed --no-pager")
        self.command_preset_combo.addItem("Docker 容器", "docker ps -a")
        self.command_preset_combo.addItem("Docker 日志后 100 行", "docker ps --format '{{.Names}}' | head -1 | xargs -r docker logs --tail 100")
        self.command_preset_combo.currentIndexChanged.connect(self.apply_command_preset)
        self.command_run_btn = QPushButton("执行命令")
        self.command_run_btn.clicked.connect(self.run_custom_command)
        preset_row.addWidget(self.command_preset_combo, 1)
        preset_row.addWidget(self.command_run_btn)
        command_layout.addLayout(preset_row)

        self.command_edit = QPlainTextEdit()
        self.command_edit.setPlaceholderText("输入要在树莓派执行的命令，例如：vcgencmd measure_temp")
        self.command_edit.setFixedHeight(120)
        command_layout.addWidget(self.command_edit)

        layout.addWidget(command_group)
        layout.addStretch(1)
        return page

    def _build_menu(self):
        menu_container = QWidget()
        menu_container.setObjectName("MenuWidgetBar")
        row = QHBoxLayout(menu_container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        menu = QMenuBar(menu_container)
        menu.setObjectName("MainMenuBar")

        file_menu = menu.addMenu("文件")
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        connection_menu = menu.addMenu("连接")
        config_action = QAction("连接设置", self)
        test_action = QAction("测试连接", self)
        locate_action = QAction("按 MAC 查找 IP", self)
        enable_wifi_action = QAction("启用无线网络", self)
        config_action.triggered.connect(self.open_settings)
        test_action.triggered.connect(self.test_connection)
        locate_action.triggered.connect(self.locate_host_by_mac)
        enable_wifi_action.triggered.connect(self.enable_wireless_network)
        connection_menu.addAction(config_action)
        connection_menu.addAction(test_action)
        connection_menu.addAction(locate_action)
        connection_menu.addAction(enable_wifi_action)

        dashboard_action = QAction("刷新", self)
        dashboard_action.triggered.connect(self.refresh_dashboard)
        menu.addAction(dashboard_action)

        tools_menu = menu.addMenu("工具")
        summary_action = QAction("读取系统摘要", self)
        deploy_action = QAction("一键部署", self)
        summary_action.triggered.connect(self.run_system_summary)
        deploy_action.triggered.connect(self.open_deploy_dialog)
        tools_menu.addAction(summary_action)
        tools_menu.addAction(deploy_action)

        maintenance_menu = menu.addMenu("维护")
        reboot_action = QAction("重启树莓派", self)
        shutdown_action = QAction("关闭树莓派", self)
        reboot_action.triggered.connect(lambda: self.power_action("reboot"))
        shutdown_action.triggered.connect(lambda: self.power_action("shutdown"))
        maintenance_menu.addAction(reboot_action)
        maintenance_menu.addAction(shutdown_action)

        help_menu = menu.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.about)
        help_menu.addAction(about_action)

        row.addWidget(menu, 0)
        row.addStretch(1)
        controls = QWidget(menu_container)
        controls.setObjectName("MenuBarControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 10, 0)
        controls_layout.setSpacing(12)

        self.profile_combo = QComboBox(controls)
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.currentIndexChanged.connect(self._switch_profile_from_menu)

        self.network_mode_group = QButtonGroup(self)
        self.network_mode_group.setExclusive(True)
        self.network_mode_buttons: dict[str, QPushButton] = {}
        network_switch = QFrame(controls)
        network_switch.setObjectName("NetworkModeSwitch")
        network_layout = QHBoxLayout(network_switch)
        network_layout.setContentsMargins(2, 2, 2, 2)
        network_layout.setSpacing(0)
        for index, mode in enumerate(NETWORK_MODES):
            button = QPushButton(NETWORK_MODE_LABELS[mode], network_switch)
            button.setCheckable(True)
            button.setProperty("network_mode", mode)
            button.setObjectName("NetworkModeButtonLeft" if index == 0 else "NetworkModeButtonRight")
            button.clicked.connect(self._on_network_mode_button_clicked)
            self.network_mode_group.addButton(button)
            self.network_mode_buttons[mode] = button
            network_layout.addWidget(button)

        device_label = QLabel("当前设备", controls)
        device_label.setObjectName("MenuDeviceLabel")
        network_label = QLabel("联网", controls)
        network_label.setObjectName("MenuNetworkLabel")
        controls_layout.addWidget(device_label)
        controls_layout.addWidget(self.profile_combo)
        controls_layout.addSpacing(8)
        controls_layout.addWidget(network_label)
        controls_layout.addWidget(network_switch)
        row.addWidget(controls, 0)

        self.setMenuWidget(menu_container)

        self.menu_actions = [
            config_action,
            test_action,
            dashboard_action,
            summary_action,
            deploy_action,
            locate_action,
            enable_wifi_action,
            reboot_action,
            shutdown_action,
        ]

    def _apply_style(self):
        self.setFont(QFont("Microsoft YaHei", 10))
        self.setStyleSheet(load_stylesheet())

    def _setup_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_dashboard_if_idle)
        self._apply_refresh_interval()

    def _apply_refresh_interval(self):
        interval = int(self.settings.value("app/refresh_interval", 30))
        self.refresh_timer.setInterval(max(10, interval) * 1000)
        self.refresh_timer.start()

    def _config(self) -> SshConfig:
        return self.profile_store.to_ssh_config()

    def _reload_profile_combo(self) -> None:
        combo = getattr(self, "profile_combo", None)
        if combo is None or not isValid(combo):
            return
        combo.blockSignals(True)
        combo.clear()
        active_id = self.profile_store.get_active_id()
        for profile in self.profile_store.list_profiles():
            label = f"{profile.name} ({profile.username}@{profile.host or 'MAC'})"
            combo.addItem(label, profile.id)
            if profile.id == active_id:
                combo.setCurrentIndex(combo.count() - 1)
        combo.blockSignals(False)

    def _switch_profile_from_menu(self, _index: int) -> None:
        combo = getattr(self, "profile_combo", None)
        if combo is None or not isValid(combo):
            return
        profile_id = combo.currentData()
        if not profile_id or profile_id == self.profile_store.get_active_id():
            return
        self.profile_store.set_active(str(profile_id))
        self._refresh_header()
        self.transfer_pane.reload_paths_from_settings()
        self._system(f"已切换到设备：{combo.currentText()}")

    def _refresh_header(self):
        self._reload_profile_combo()
        profile = self.profile_store.get_active()
        cfg = self._config()
        mode = cfg.normalized_network_mode()
        for network_mode, button in self.network_mode_buttons.items():
            button.blockSignals(True)
            button.setChecked(network_mode == mode)
            button.blockSignals(False)
        host_text = cfg.host or "等待 MAC 锁定 IP"
        mode_text = NETWORK_MODE_LABELS.get(mode, mode)
        if mode == NETWORK_MODE_WIRELESS:
            mac = cfg.mac_address_wireless
        else:
            mac = cfg.mac_address_wired or cfg.mac_address
        mac_text = f" | MAC {mac}" if mac else ""
        self.status_text.setText(f"{cfg.username}@{host_text}:{cfg.port} | {mode_text}{mac_text}")
        self.ssh_command_view.setText(f"ssh {cfg.username}@{cfg.host} -p {cfg.port}" if cfg.host else "")

    def _on_network_mode_button_clicked(self) -> None:
        sender = self.sender()
        if sender is None or not sender.isChecked():
            return
        mode = str(sender.property("network_mode") or "")
        profile = self.profile_store.get_active()
        if profile.network_mode == mode:
            return
        profile.network_mode = mode
        profile.sync_legacy_fields()
        self.profile_store.save_profile(profile)
        self._refresh_header()
        self._system(f"联网模式已切换为：{NETWORK_MODE_LABELS.get(mode, mode)}")
        if mode == NETWORK_MODE_WIRELESS:
            cfg = self._config()
            if cfg.host and cfg.auth_ready():
                reply = QMessageBox.question(
                    self,
                    "启用无线网络",
                    "是否通过当前 SSH 连接在树莓派上启用无线网络？\n"
                    "启用后可在“按 MAC 查找 IP”窗口扫描无线 IP。",
                )
                if reply == QMessageBox.Yes:
                    self._run_task("enable_wireless")

    def _connection_summary(self, source: str | None = None) -> str:
        profile = self.profile_store.get_active()
        cfg = self._config()
        return format_connection_detail(
            cfg.host,
            cfg.normalized_network_mode(),
            mac_wired=cfg.mac_address_wired or cfg.mac_address,
            mac_wireless=cfg.mac_address_wireless,
            source=source or profile.active_host_source(),
        )

    def _mac_scan_targets_text(self) -> str:
        cfg = self._config()
        mode = cfg.normalized_network_mode()
        if mode == NETWORK_MODE_WIRELESS:
            return f"{NETWORK_MODE_LABELS[mode]} / MAC {cfg.mac_address_wireless or '未配置'}"
        mac = cfg.mac_address_wired or cfg.mac_address
        return f"{NETWORK_MODE_LABELS[NETWORK_MODE_WIRED]} / MAC {mac or '未配置'}"

    def _check_config(self, task: str = "", *, quiet: bool = False) -> bool:
        cfg = self._config()
        if task == "discover_host":
            if not cfg.has_mac_for_mode():
                if not quiet:
                    QMessageBox.warning(
                        self,
                        "MAC 未配置",
                        "请先在“连接设置”或“按 MAC 查找 IP”中填写对应的有线/无线 MAC 地址。",
                    )
                return False
            return True
        if not cfg.username or not cfg.auth_ready() or (not cfg.host and not cfg.has_mac_for_mode()):
            if not quiet:
                QMessageBox.warning(
                    self,
                    "连接信息不完整",
                    "请在“连接 > 连接设置”中填写用户名、密码或 SSH 私钥，以及 IP 或 MAC 地址。",
                )
            return False
        return True

    def _startup_auto_connect(self) -> None:
        if self.busy:
            QTimer.singleShot(500, self._startup_auto_connect)
            return
        if not self._check_config(quiet=True):
            self._system("连接信息不完整，已跳过启动时的自动连接测试。")
            return
        self._schedule_task("startup_refresh", {})

    def _schedule_task(self, task: str, payload: dict | None = None) -> None:
        """在当前 Worker 线程完全结束后再启动下一任务，避免线程引用被覆盖导致卡死。"""
        QTimer.singleShot(0, lambda: self._run_task(task, dict(payload or {})))

    def _start_mac_discovery(self, retry_task: str = "", retry_payload: dict | None = None):
        cfg = self._config()
        payload = {
            "mac_address": cfg.mac_address,
            "mac_address_wired": cfg.mac_address_wired,
            "mac_address_wireless": cfg.mac_address_wireless,
            "network_mode": cfg.normalized_network_mode(),
            "current_host": cfg.host,
        }
        if retry_task:
            payload["retry_task"] = retry_task
            payload["retry_payload"] = dict(retry_payload or {})
        self._run_task("discover_host", payload)

    def _should_retry_with_mac(self, task: str, error_text: str) -> bool:
        if task == "discover_host":
            return False
        if self.active_payload.get("mac_retry_done"):
            return False
        if not self._config().has_mac_for_mode():
            return False
        lowered = error_text.lower()
        markers = (
            "novalidconnectionserror",
            "timed out",
            "timeout",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "name or service not known",
            "noroutetohost",
            "socket.gaierror",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    def _append_output(self, role: str, text: str, color: str):
        html = f"""
        <div style="margin:10px 0;">
            <div style="color:{color}; font-weight:700;">{role}</div>
            <div style="background:#ffffff; border:1px solid #d5dde4; border-left:4px solid {color}; padding:8px 10px; margin-top:4px; color:#24303a;">
                {self._escape(text)}
            </div>
        </div>
        """
        self.output_view.append(html)
        self.output_view.moveCursor(QTextCursor.End)

    def _system(self, text: str):
        self._append_output("SYSTEM", text, "#f59e0b")

    def _remote(self, text: str):
        self._append_output("REMOTE", text, "#22c55e")

    def _error(self, text: str):
        self._append_output("ERROR", text, "#ef4444")

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.tabs.setDisabled(busy)
        combo = getattr(self, "profile_combo", None)
        if combo is not None and isValid(combo):
            combo.setEnabled(not busy)
        for button in getattr(self, "network_mode_buttons", {}).values():
            if isValid(button):
                button.setEnabled(not busy)
        for action in self.menu_actions:
            action.setEnabled(not busy)
        self.status_bar.showMessage("执行中..." if busy else "就绪")

    def _run_task(self, task: str, payload: dict | None = None):
        if not self._check_config(task):
            return
        if self.busy:
            QMessageBox.information(self, "任务执行中", "当前已有任务在执行，请等待完成后再操作。")
            return

        if task != "discover_host":
            cfg = self._config()
            if not cfg.host and cfg.has_mac_for_mode():
                self._system(f"未配置 IP，正在 MAC 扫描：{self._mac_scan_targets_text()}")
                self._start_mac_discovery(task, payload)
                return

        self.active_task = task
        self.active_payload = payload or {}
        self._set_busy(True)

        self.thread = QThread(self)
        self.worker = Worker(self._config(), task, self.active_payload)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._task_done)
        if task in {"transfer", "deploy"}:
            self.worker.progress.connect(self._on_transfer_progress)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._task_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _on_transfer_progress(self, done: int, total: int, label: str) -> None:
        self.transfer_pane.set_transfer_progress(done, total, label)

    def _task_done(self, ok: bool, task: str, text: str, data: object):
        if task in {"transfer", "deploy"}:
            self.transfer_pane.reset_transfer_progress()
        self._set_busy(False)

        if not ok:
            if task == "startup_refresh":
                if self._should_retry_with_mac(task, text):
                    retry_payload = {"mac_retry_done": True}
                    self._system(f"启动连接失败，正在 MAC 扫描：{self._mac_scan_targets_text()}")
                    self._start_mac_discovery(task, retry_payload)
                    return
                self._system("启动时 SSH 连接失败，请检查连接设置。")
                return
            if self._should_retry_with_mac(task, text):
                retry_payload = dict(self.active_payload)
                retry_payload["mac_retry_done"] = True
                self._system(f"SSH 连接失败，正在 MAC 扫描：{self._mac_scan_targets_text()}")
                self._start_mac_discovery(task, retry_payload)
                return
            self._error(text)
            return

        silent = bool(self.active_payload.get("silent", False))

        if task == "discover_host":
            host = ""
            matched_mode = ""
            matched_mac = ""
            if isinstance(data, dict):
                host = str(data.get("host", "")).strip()
                matched_mode = str(data.get("matched_mode", "")).strip()
                matched_mac = str(data.get("matched_mac", "")).strip()
            if host:
                profile = self.profile_store.get_active()
                mode = matched_mode if matched_mode in NETWORK_MODES else profile.network_mode
                profile.apply_host_for_mode(mode, host, HOST_SOURCE_MAC_SCAN)
                if matched_mode in NETWORK_MODES:
                    profile.network_mode = matched_mode
                self.profile_store.save_profile(profile)
                self._refresh_header()
            if not silent:
                self._remote(text)
            retry_task = self.active_payload.get("retry_task", "")
            retry_payload = self.active_payload.get("retry_payload")
            if retry_task:
                next_payload = dict(retry_payload or {})
                next_payload["mac_retry_done"] = True
                scan_summary = format_connection_detail(
                    host,
                    matched_mode or self._config().normalized_network_mode(),
                    mac_wired=self._config().mac_address_wired,
                    mac_wireless=self._config().mac_address_wireless,
                    mac_matched=matched_mac,
                    source=HOST_SOURCE_MAC_SCAN,
                )
                self._system(f"MAC 扫描完成：{scan_summary}，继续执行原任务。")
                self._schedule_task(retry_task, next_payload)
            return

        if task == "enable_wireless":
            if isinstance(data, dict):
                profile = self.profile_store.get_active()
                wireless = data.get("wireless") or {}
                wired = data.get("wired") or {}
                wired_mac = normalize_mac_address(str(wired.get("mac", ""))) if wired.get("mac") else profile.mac_address_wired
                wireless_mac = normalize_mac_address(str(wireless.get("mac", ""))) if wireless.get("mac") else ""
                if wired_mac:
                    profile.mac_address_wired = wired_mac
                if wireless_mac and wireless_mac != wired_mac:
                    profile.mac_address_wireless = wireless_mac
                elif wireless_mac and wireless_mac == wired_mac:
                    self._system("未检测到独立的无线 MAC，请先在窗体中手动填写 wlan0 的 MAC。")
                wireless_ip = str(wireless.get("ip", "")).strip()
                if wireless_ip:
                    if normalize_network_mode(profile.network_mode) != NETWORK_MODE_WIRELESS:
                        wired_ip = profile.active_host()
                        if wired_ip and not profile.host_wired:
                            profile.host_wired = wired_ip
                            profile.host_source_wired = profile.active_host_source()
                    profile.apply_host_for_mode(NETWORK_MODE_WIRELESS, wireless_ip, HOST_SOURCE_REMOTE_WIFI)
                    profile.network_mode = NETWORK_MODE_WIRELESS
                profile.mac_address = profile.mac_address_wired
                self.profile_store.save_profile(profile)
                self._refresh_header()
                if wireless_ip:
                    summary = format_connection_detail(
                        wireless_ip,
                        NETWORK_MODE_WIRELESS,
                        mac_wireless=wireless_mac,
                        mac_matched=wireless_mac,
                        source=HOST_SOURCE_REMOTE_WIFI,
                    )
                    self._remote(f"{text}\n{summary}")
                    return
            self._remote(text)
            return

        if task == "startup_refresh":
            payload = data if isinstance(data, dict) else {}
            self._update_dashboard(payload)
            record_snapshot(self._config().host, payload)
            if hasattr(self, "metrics_chart"):
                self.metrics_chart.set_host(self._config().host)
            self._system(f"连接成功，仪表盘已自动刷新。\n{self._connection_summary()}")
            return

        if task == "dashboard":
            payload = data if isinstance(data, dict) else {}
            self._update_dashboard(payload)
            record_snapshot(self._config().host, payload)
            if hasattr(self, "metrics_chart"):
                self.metrics_chart.set_host(self._config().host)
            if not silent:
                self._remote(f"{text}\n{self._connection_summary()}")
            return

        if task == "services":
            self.service_rows = data if isinstance(data, list) else []
            self.apply_service_filter()
            if not silent:
                self._remote(text)
            return

        if task == "docker":
            if isinstance(data, list):
                self.docker_rows = data
                self._fill_docker_table()
            if not silent:
                self._remote(text if text else "Docker 操作完成。")
            if self.active_payload.get("post_refresh") == "docker":
                self._schedule_task("docker", {"silent": True})
            return

        if task == "packages":
            if isinstance(data, dict):
                self.package_data = data
                self._update_package_counts()
                self.apply_package_filter()
            if not silent:
                self._remote(text if text else "软件操作完成。")
            if self.active_payload.get("post_refresh") == "packages":
                self._schedule_task("packages", {"silent": True})
            return

        if task == "logs":
            self.logs_view.setPlainText(text)
            if not silent:
                self._remote("日志已加载。")
            return

        self._remote(text)

        if self.active_payload.get("post_refresh") == "services":
            self._schedule_task("services", {"scope": self.active_payload.get("refresh_scope", "system"), "silent": True})
            return

        if self.active_payload.get("post_refresh") == "packages":
            self._schedule_task("packages", {"silent": True})
            return

        if self.active_payload.get("post_refresh") == "docker":
            self._schedule_task("docker", {"silent": True})
            return

        if self.active_payload.get("post_refresh") == "dashboard":
            self._schedule_task("dashboard", {"silent": True})
            return

    def _task_thread_finished(self):
        thread = self.sender()
        if thread is not self.thread:
            return
        self.worker = None
        self.thread = None

    def _refresh_dashboard_if_idle(self):
        if self.busy:
            return
        if self.tabs.currentWidget() in {self.dashboard_tab, self.cpu_process_tab, self.mem_process_tab}:
            self._run_task("dashboard", {"silent": True})

    @staticmethod
    def _to_int(text: str, default: int = 0) -> int:
        try:
            return int(float(str(text).strip()))
        except (TypeError, ValueError):
            return default

    def _set_usage_visual(self, key: str, percent: int, detail: str):
        widget = self.usage_widgets[key]
        value_text = f"{percent}C" if key == "temp_value" else f"{percent}%"
        widget["value"].setText(value_text)
        widget["progress"].setValue(max(0, min(100, percent)))
        widget["hint"].setText(detail)
        if key == "temp_value":
            if percent >= 80:
                chunk = "#d94841"
            elif percent >= 65:
                chunk = "#ea8b2d"
            else:
                chunk = "#3c98d8"
        elif percent >= 85:
            chunk = "#d94841"
        elif percent >= 65:
            chunk = "#ea8b2d"
        else:
            chunk = "#3c98d8"
        widget["progress"].setStyleSheet(
            f"QProgressBar {{ background:#d7dde4; border:1px solid #c2c9d0; border-radius:3px; min-height:8px; max-height:8px; }}"
            f"QProgressBar::chunk {{ background:{chunk}; border-radius:2px; }}"
        )

    def _update_dashboard(self, data: dict):
        cpu_percent = self._to_int(data.get("cpu_percent", 0))
        mem_percent = self._to_int(data.get("mem_percent", 0))
        disk_percent = self._to_int(data.get("disk_percent", 0))
        swap_percent = self._to_int(data.get("swap_percent", 0))
        temp_value = self._to_int(data.get("temp_value", 0))

        self._set_usage_visual("cpu_percent", cpu_percent, f"系统负载 {data.get('load', '--')}")
        self._set_usage_visual("mem_percent", mem_percent, data.get("mem", "--"))
        self._set_usage_visual("disk_percent", disk_percent, data.get("disk", "--"))
        self._set_usage_visual("swap_percent", swap_percent, data.get("swap", "--"))
        self._set_usage_visual("temp_value", temp_value, data.get("temp", "--"))

        for key, label in self.dashboard_labels.items():
            label.setText(data.get(key, "--") or "--")
        self._fill_process_table(self.top_cpu_view, data.get("top_cpu", []))
        self._fill_process_table(self.top_mem_view, data.get("top_mem", []))

    def _fill_process_table(self, table: QTableWidget, rows: list[dict]):
        if isinstance(rows, str):
            rows = parse_process_rows(rows)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self._set_table_items(
                table,
                index,
                [
                    row.get("pid", ""),
                    row.get("command", ""),
                    row.get("cpu", ""),
                    row.get("mem", ""),
                ],
            )
        table.resizeRowsToContents()

    def _set_table_items(self, table: QTableWidget, row_index: int, values: list[str]):
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            table.setItem(row_index, column, item)

    def _selected_service(self) -> str:
        row = self.service_table.currentRow()
        if row < 0:
            return ""
        item = self.service_table.item(row, 0)
        return item.text().strip() if item else ""

    def apply_service_filter(self):
        keyword = self.service_filter_edit.text().strip().lower()
        rows = []
        for row in self.service_rows:
            haystack = " ".join(
                [row.get("unit", ""), row.get("description", ""), row.get("active", ""), row.get("sub", "")]
            ).lower()
            if keyword and keyword not in haystack:
                continue
            rows.append(row)

        self.service_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self._set_table_items(
                self.service_table,
                index,
                [
                    row.get("unit", ""),
                    row.get("load", ""),
                    row.get("active", ""),
                    row.get("sub", ""),
                    row.get("description", ""),
                ],
            )
        self.service_table.resizeRowsToContents()

    def _update_package_counts(self):
        self.apt_count_label.setText(f"APT: {len(self.package_data.get('apt', []))}")
        self.pip_count_label.setText(f"PIP: {len(self.package_data.get('pip', []))}")
        self.upgradable_count_label.setText(f"可升级: {len(self.package_data.get('upgradable', []))}")

    def apply_package_filter(self):
        source = self.package_source_combo.currentData()
        keyword = self.package_filter_edit.text().strip().lower()
        rows = list(self.package_data.get(source, []))
        filtered = []
        for row in rows:
            haystack = " ".join([row.get("name", ""), row.get("version", ""), row.get("extra", "")]).lower()
            if keyword and keyword not in haystack:
                continue
            filtered.append(row)

        self.package_table.setRowCount(len(filtered))
        for index, row in enumerate(filtered):
            self._set_table_items(
                self.package_table,
                index,
                [row.get("name", ""), row.get("version", ""), row.get("extra", "")],
            )
        self.package_table.resizeRowsToContents()

    def _selected_package_name(self) -> str:
        row = self.package_table.currentRow()
        if row < 0:
            return ""
        item = self.package_table.item(row, 0)
        return item.text().strip() if item else ""

    def upgrade_all_packages(self) -> None:
        source = self.package_source_combo.currentData()
        if source not in {"upgradable", "pip"}:
            QMessageBox.information(self, "无法升级", "请在“可升级软件”或“PIP 已安装”列表中使用升级功能。")
            return
        if source == "upgradable" and not self.package_data.get("upgradable"):
            QMessageBox.information(self, "无可升级项", "当前没有可升级的 APT 软件包。")
            return
        reply = QMessageBox.question(
            self,
            "确认升级",
            "确认升级全部可升级软件吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._system("开始升级全部软件。")
        self._run_task(
            "packages",
            {"action": "upgrade", "source": source, "packages": [], "post_refresh": "packages"},
        )

    def upgrade_selected_packages(self) -> None:
        source = self.package_source_combo.currentData()
        name = self._selected_package_name()
        if not name:
            QMessageBox.information(self, "未选择软件", "请先选择一个软件包。")
            return
        if source not in {"upgradable", "pip", "apt"}:
            QMessageBox.information(self, "无法升级", "当前列表不支持升级。")
            return
        reply = QMessageBox.question(
            self,
            "确认升级",
            f"确认升级 {name} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._system(f"开始升级软件：{name}")
        self._run_task(
            "packages",
            {"action": "upgrade", "source": source if source != "apt" else "upgradable", "packages": [name], "post_refresh": "packages"},
        )

    def _fill_docker_table(self) -> None:
        self.docker_table.setRowCount(len(self.docker_rows))
        for index, row in enumerate(self.docker_rows):
            self._set_table_items(
                self.docker_table,
                index,
                [row.get("id", ""), row.get("name", ""), row.get("status", ""), row.get("image", ""), row.get("ports", "")],
            )
        self.docker_table.resizeRowsToContents()

    def _selected_docker_id(self) -> str:
        row = self.docker_table.currentRow()
        if row < 0:
            return ""
        item = self.docker_table.item(row, 0)
        return item.text().strip() if item else ""

    def refresh_docker(self) -> None:
        self._system("刷新 Docker 容器列表。")
        self._run_task("docker")

    def docker_action(self, action: str) -> None:
        container_id = self._selected_docker_id()
        if not container_id:
            QMessageBox.information(self, "未选择容器", "请先选择一个 Docker 容器。")
            return
        if action in {"start", "stop", "restart"}:
            reply = QMessageBox.question(
                self,
                "确认操作",
                f"确认对容器 {container_id[:12]} 执行 {action} 吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        payload = {"action": action, "container_id": container_id, "post_refresh": "docker"}
        if action == "logs":
            payload["lines"] = 200
            payload.pop("post_refresh", None)
        self._system(f"Docker {action}：{container_id[:12]}")
        self._run_task("docker", payload)

    def _selected_process_pid(self, table: QTableWidget) -> str:
        row = table.currentRow()
        if row < 0:
            return ""
        item = table.item(row, 0)
        return item.text().strip() if item else ""

    def kill_selected_process(self, signal: int, which: str) -> None:
        table = self.top_cpu_view if which == "cpu" else self.top_mem_view
        pid = self._selected_process_pid(table)
        if not pid or not pid.isdigit():
            QMessageBox.information(self, "未选择进程", "请先选择一个进程。")
            return
        action = "强制结束" if signal == 9 else "结束"
        reply = QMessageBox.question(
            self,
            "确认操作",
            f"确认{action} PID {pid} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._system(f"{action}进程 PID {pid}")
        self._run_task("kill_process", {"pid": pid, "signal": signal, "post_refresh": "dashboard"})

    def export_logs(self) -> None:
        text = self.logs_view.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "无日志", "当前没有可导出的日志内容。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "raspberry_pi.log", "Log Files (*.log);;Text Files (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        self._system(f"日志已导出到：{path}")

    def start_log_tail(self) -> None:
        if self.log_tail_thread is not None and self.log_tail_thread.isRunning():
            QMessageBox.information(self, "跟踪中", "日志实时跟踪已在运行。")
            return
        if not self._check_config():
            return
        mode = self.logs_mode_combo.currentData()
        service = self.logs_service_edit.text().strip() if mode == "service" else ""
        if mode == "service" and not service:
            QMessageBox.warning(self, "服务名为空", "请填写要跟踪的服务名。")
            return
        payload = {"mode": mode, "service": service, "scope": self.logs_scope_combo.currentData()}
        self.logs_view.clear()
        self._system("开始实时跟踪日志。")
        self.log_tail_thread = QThread(self)
        self.log_tail_worker = LogTailWorker(self._config(), payload)
        self.log_tail_worker.moveToThread(self.log_tail_thread)
        self.log_tail_thread.started.connect(self.log_tail_worker.run)
        self.log_tail_worker.line.connect(self._append_log_tail)
        self.log_tail_worker.finished.connect(self._log_tail_finished)
        self.log_tail_worker.finished.connect(self.log_tail_thread.quit)
        self.log_tail_worker.finished.connect(self.log_tail_worker.deleteLater)
        self.log_tail_thread.finished.connect(self._log_tail_thread_finished)
        self.log_tail_thread.finished.connect(self.log_tail_thread.deleteLater)
        self.log_tail_thread.start()

    def _append_log_tail(self, chunk: str) -> None:
        cursor = self.logs_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(chunk)
        self.logs_view.setTextCursor(cursor)

    def _log_tail_finished(self, ok: bool, text: str) -> None:
        if ok:
            self._remote(text)
        else:
            self._error(text)

    def _log_tail_thread_finished(self) -> None:
        self.log_tail_worker = None
        self.log_tail_thread = None

    def stop_log_tail(self) -> None:
        if self.log_tail_worker is not None:
            self.log_tail_worker.stop()
        if self.log_tail_thread is not None and self.log_tail_thread.isRunning():
            self.log_tail_thread.quit()
            self.log_tail_thread.wait(3000)
        self._system("已请求停止日志跟踪。")

    def open_deploy_dialog(self) -> None:
        if not self._check_config():
            return
        dialog = DeployDialog(
            self._config(),
            self.settings.value("transfer/remote", f"/home/{self._config().username}/projects/"),
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._system("开始一键部署。")
        self._run_task("deploy", dialog.payload())

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.profile_store = ProfileStore(self.settings)
            self._refresh_header()
            self._apply_refresh_interval()
            self.transfer_pane.reload_paths_from_settings()
            self._system("连接设置已更新。")

    def test_connection(self):
        self._system("测试 SSH 连接。")
        self._run_task("command", {"command": "hostname && whoami && date", "timeout": 60})

    def locate_host_by_mac(self):
        dialog = MacDiscoveryDialog(self._config(), self.profile_store, self)
        if dialog.exec() == QDialog.Accepted:
            self._refresh_header()
            self._system("MAC 查找结果已保存。")

    def enable_wireless_network(self):
        cfg = self._config()
        if not cfg.host or not cfg.auth_ready():
            QMessageBox.warning(
                self,
                "无法启用",
                "启用无线网络需要先通过有线网连接树莓派（填写 IP 与 SSH 凭据）。",
            )
            return
        reply = QMessageBox.question(
            self,
            "启用无线网络",
            "将通过当前 SSH 连接在树莓派上启用无线网卡。\n"
            "请确保树莓派已配置 WiFi，且当前为有线连接。\n\n是否继续？",
        )
        if reply != QMessageBox.Yes:
            return
        self._system("正在远程启用无线网络…")
        self._run_task("enable_wireless")

    def run_system_summary(self):
        self._system("读取系统摘要。")
        self._run_task(
            "command",
            {"command": "uname -a && echo && cat /etc/os-release 2>/dev/null | sed -n '1,6p' && echo && uptime", "timeout": 60},
        )

    def refresh_dashboard(self):
        self._system(f"刷新运行信息。\n当前连接：{self._connection_summary()}")
        self._run_task("dashboard")

    def refresh_services(self):
        self._system("刷新服务列表。")
        self._run_task("services", {"scope": self.service_scope_combo.currentData()})

    def show_service_status(self):
        service = self._selected_service()
        if not service:
            QMessageBox.information(self, "未选择服务", "请先选择一个服务。")
            return
        scope = self.service_scope_combo.currentData()
        prefix = "systemctl --user" if scope == "user" else "systemctl"
        if not _UNIT_NAME_RE.match(service):
            QMessageBox.warning(self, "无效服务名", "服务名格式无效。")
            return
        self._system(f"查看服务状态：{service}")
        self._run_task("command", {"command": f"{prefix} status {shlex.quote(service)} --no-pager", "timeout": 120})

    def open_service_logs(self):
        service = self._selected_service()
        if not service:
            QMessageBox.information(self, "未选择服务", "请先选择一个服务。")
            return
        self.tabs.setCurrentWidget(self.logs_tab)
        self.logs_mode_combo.setCurrentIndex(1)
        self.logs_service_edit.setText(service)
        self.logs_scope_combo.setCurrentIndex(self.service_scope_combo.currentIndex())
        self.load_logs()

    def service_action(self, action: str):
        service = self._selected_service()
        if not service:
            QMessageBox.information(self, "未选择服务", "请先选择一个服务。")
            return
        scope = self.service_scope_combo.currentData()
        prefix = "systemctl --user" if scope == "user" else "systemctl"
        action_text = {"start": "启动", "restart": "重启", "stop": "停止"}.get(action, action)
        reply = QMessageBox.question(
            self,
            "确认操作",
            f"确认{action_text}服务 {service} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not _UNIT_NAME_RE.match(service):
            QMessageBox.warning(self, "无效服务名", "服务名格式无效。")
            return
        self._system(f"{action_text}服务：{service}")
        self._run_task(
            "command",
            {
                "command": f"{prefix} {action} {shlex.quote(service)}",
                "timeout": 120,
                "post_refresh": "services",
                "refresh_scope": scope,
            },
        )

    def power_action(self, action: str):
        action_text = {"reboot": "重启", "shutdown": "关机"}.get(action, action)
        command = {
            "reboot": "sudo -n shutdown -r now || shutdown -r now || sudo -n reboot || reboot",
            "shutdown": "sudo -n shutdown -h now || shutdown -h now || sudo -n poweroff || poweroff",
        }.get(action)
        if not command:
            return

        reply = QMessageBox.question(
            self,
            "确认操作",
            f"确认要{action_text}树莓派吗？\n执行后 SSH 连接可能会立刻中断。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._system(f"发送{action_text}指令。")
        self._run_task("command", {"command": command, "timeout": 30})

    def load_logs(self):
        mode = self.logs_mode_combo.currentData()
        service = self.logs_service_edit.text().strip() if mode == "service" else ""
        if mode == "service" and not service:
            QMessageBox.warning(self, "服务名为空", "请填写要查看的服务名，例如 ssh.service。")
            return
        payload = {
            "mode": mode,
            "service": service,
            "scope": self.logs_scope_combo.currentData(),
            "lines": self.logs_lines_spin.value(),
        }
        self._system("加载日志。")
        self._run_task("logs", payload)

    def refresh_packages(self):
        self._system("刷新软件清单。")
        self._run_task("packages")

    def start_transfer(self, payload: dict):
        username = self._config().username
        local_paths = payload.get("local_paths") or []
        if payload.get("direction") == "upload" and not is_under_user_home(payload.get("remote_path", ""), username):
            reply = QMessageBox.question(
                self,
                "路径权限提醒",
                "当前树莓派目标路径不在当前用户 home 目录下，可能没有写入权限。\n\n"
                f"建议使用：/home/{username}/uploads/\n\n是否仍要继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        if payload.get("direction") == "upload" and payload.get("mode") == "file" and len(local_paths) > 1:
            reply = QMessageBox.question(
                self,
                "多文件上传提醒",
                "多个文件上传时，树莓派路径会按目录处理，每个文件保留原文件名。\n\n"
                f"建议使用：/home/{username}/uploads/\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        direction_text = "上传" if payload.get("direction") == "upload" else "下载"
        mode_text = "文件" if payload.get("mode") == "file" else "文件夹"
        if payload.get("direction") == "upload" and payload.get("mode") == "file" and len(local_paths) > 1:
            mode_text = f"{len(local_paths)} 个文件"
        self._system(f"开始{direction_text}{mode_text}。")
        self._run_task("transfer", payload)

    def open_remote_browser(self, payload: dict):
        cfg = self._config()
        if not cfg.host or not cfg.username or not cfg.auth_ready():
            QMessageBox.warning(self, "连接信息不完整", "请先配置树莓派 IP、用户名和认证信息。")
            return
        dialog = RemoteBrowserDialog(
            cfg,
            payload.get("start_path", ""),
            payload.get("select_mode", "folder"),
            self,
        )
        if dialog.exec() == QDialog.Accepted and dialog.selected_path:
            self.transfer_pane.set_remote_path(dialog.selected_path)

    def apply_command_preset(self):
        command = self.command_preset_combo.currentData()
        if command:
            self.command_edit.setPlainText(command)

    def run_custom_command(self):
        command = self.command_edit.toPlainText().strip()
        if not command:
            QMessageBox.information(self, "命令为空", "请输入要执行的命令。")
            return
        self._system("执行远程命令。")
        self._run_task("command", {"command": command, "timeout": 180})

    def copy_ssh_command(self):
        command = self.ssh_command_view.text().strip()
        if not command:
            return
        QApplication.clipboard().setText(command)
        self._system("SSH 命令已复制到剪贴板。")

    def _launch_local_process(self, args: list[str], title: str):
        try:
            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(args, **kwargs)
            self._system(f"{title} 已启动。")
        except Exception as exc:
            QMessageBox.critical(self, "启动失败", f"{title} 启动失败：\n{exc}")

    @staticmethod
    def _ps_single_quote(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    @staticmethod
    def _ps_encoded_command(command: str) -> str:
        return base64.b64encode(command.encode("utf-16le")).decode("ascii")

    def _helper_python(self) -> str:
        candidates = [
            os.path.join(app_base_dir(), ".venv", "Scripts", "python.exe"),
            sys.executable,
        ]
        for candidate in candidates:
            if not candidate or not os.path.isfile(candidate):
                continue
            if os.path.basename(candidate).lower().startswith("python"):
                return candidate
        return ""

    def _ssh_console_launcher(self) -> tuple[str, list[str]] | None:
        cfg = self._config()
        common_args = [
            "--host",
            cfg.host,
            "--port",
            str(cfg.port),
            "--user",
            cfg.username,
            "--title",
            f"Raspberry Pi Remote Console -> {cfg.username}@{cfg.host}:{cfg.port}",
        ]
        if cfg.uses_key():
            common_args.extend(["--key-path", cfg.key_path])
        if getattr(sys, "frozen", False):
            return sys.executable, ["--ssh-console", *common_args]

        helper_python = self._helper_python()
        helper_script = os.path.join(app_base_dir(), "ssh_console.py")
        if not helper_python or not os.path.isfile(helper_script):
            return None
        return helper_python, [helper_script, *common_args]

    def _build_console_bridge_command(self) -> tuple[str, str] | None:
        cfg = self._config()
        launcher = self._ssh_console_launcher()
        if launcher is None:
            return None
        executable, args = launcher
        title = f"Raspberry Pi Remote Console -> {cfg.username}@{cfg.host}:{cfg.port}"
        args_text = " ".join(self._ps_single_quote(arg) for arg in args)
        if cfg.uses_key():
            command = f"& {self._ps_single_quote(executable)} {args_text}"
            if cfg.key_passphrase:
                command = (
                    f"$env:RP_SSH_KEY_PASSPHRASE={self._ps_single_quote(cfg.key_passphrase)}; "
                    f"{command}; "
                    "Remove-Item Env:RP_SSH_KEY_PASSPHRASE -ErrorAction SilentlyContinue"
                )
        else:
            command = (
                f"$env:RP_SSH_PASSWORD={self._ps_single_quote(cfg.password)}; "
                f"& {self._ps_single_quote(executable)} {args_text}; "
                "Remove-Item Env:RP_SSH_PASSWORD -ErrorAction SilentlyContinue"
            )
        return command, title

    def open_powershell(self):
        if not self._check_config():
            return
        cfg = self._config()
        bridge = self._build_console_bridge_command()
        if bridge:
            command, _ = bridge
            encoded = self._ps_encoded_command(command)
            self._launch_local_process(["powershell.exe", "-NoExit", "-EncodedCommand", encoded], "PowerShell")
            return
        command = f"ssh {cfg.username}@{cfg.host} -p {cfg.port}"
        encoded = self._ps_encoded_command(command)
        self._launch_local_process(["powershell.exe", "-NoExit", "-EncodedCommand", encoded], "PowerShell")

    def open_windows_terminal(self):
        if not self._check_config():
            return
        cfg = self._config()
        bridge = self._build_console_bridge_command()
        if bridge:
            command, _ = bridge
            encoded = self._ps_encoded_command(command)
            self._launch_local_process(
                ["wt.exe", "new-tab", "powershell.exe", "-NoExit", "-EncodedCommand", encoded],
                "Windows Terminal",
            )
            return
        command = f"ssh {cfg.username}@{cfg.host} -p {cfg.port}"
        encoded = self._ps_encoded_command(command)
        self._launch_local_process(
            ["wt.exe", "new-tab", "powershell.exe", "-NoExit", "-EncodedCommand", encoded],
            "Windows Terminal",
        )

    def about(self):
        QMessageBox.information(
            self,
            "关于",
            "树莓派远程运维台\n\n"
            "用途：通过 SSH / SFTP 监控和管理树莓派。\n"
            "功能：仪表盘、服务管理、日志查看、软件升级、Docker、文件传输、设备配置、趋势图、一键部署。\n"
            "定位：局域网内树莓派远程管理工具。",
        )

    def closeEvent(self, event):
        self.stop_log_tail()
        SshSessionPool.shared().close_all()
        if self.thread is not None and self.thread.isRunning():
            self.thread.quit()
            if not self.thread.wait(5000):
                QMessageBox.information(self, "任务未结束", "后台任务仍在结束中，请稍后再关闭窗口。")
                event.ignore()
                return
        super().closeEvent(event)
