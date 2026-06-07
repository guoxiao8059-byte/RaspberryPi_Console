# -*- coding: utf-8 -*-
"""
Raspberry Pi Remote Console
运行位置：Windows 电脑
作用：通过 SSH / SFTP 远程监控和管理树莓派
依赖：pip install PySide6 paramiko
"""

import os
import sys


def main():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from raspberry_pi_console.app.main_window import MainWindow
    from raspberry_pi_console.constants import APP_NAME, APP_ORG

    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon("app.ico"))
    app.setWindowIcon(QIcon("app.ico"))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--ssh-console":
        sys.argv.pop(1)
        if getattr(sys, "frozen", False):
            import runpy

            from raspberry_pi_console.core.paths import app_base_dir

            script = os.path.join(getattr(sys, "_MEIPASS", app_base_dir()), "ssh_console.py")
            if not os.path.isfile(script):
                script = os.path.join(app_base_dir(), "ssh_console.py")
            if not os.path.isfile(script):
                print(f"找不到 ssh_console.py：{script}", file=sys.stderr)
                raise SystemExit(1)
            runpy.run_path(script, run_name="__main__")
            raise SystemExit(0)
        from ssh_console import main as ssh_console_main

        raise SystemExit(ssh_console_main())
    main()
