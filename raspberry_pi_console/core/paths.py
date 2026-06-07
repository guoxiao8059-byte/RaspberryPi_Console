# -*- coding: utf-8 -*-
import os
import sys


def app_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_under_user_home(remote_path: str, username: str) -> bool:
    path = str(remote_path or "").strip().replace("\\", "/")
    if not path:
        return False
    home = f"/home/{username}"
    return path == home or path.startswith(f"{home}/")
