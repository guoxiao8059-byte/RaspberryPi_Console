# -*- coding: utf-8 -*-
import os
import sys


def load_stylesheet() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        path = os.path.join(base, "raspberry_pi_console", "ui", "styles.qss")
    else:
        path = os.path.join(os.path.dirname(__file__), "styles.qss")
    with open(path, encoding="utf-8") as handle:
        return handle.read()
