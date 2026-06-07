# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shlex

from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result

_UNIT_NAME_RE = re.compile(r"^[a-zA-Z0-9@._-]+(\.[a-zA-Z0-9@._-]+)*$")


def _validate_service_name(service: str) -> str:
    text = service.strip()
    if not text or not _UNIT_NAME_RE.match(text):
        raise ValueError("无效的服务名，例如 ssh.service。")
    return text


def fetch_logs(ssh: SshClient, payload: dict) -> str:
    service = payload.get("service", "").strip()
    lines = max(20, int(payload.get("lines", 200)))
    mode = payload.get("mode", "system")
    if mode == "kernel":
        command = f"journalctl -k -n {lines} --no-pager"
    elif mode == "service":
        service = _validate_service_name(service)
        scope = payload.get("scope", "system")
        journal_prefix = "--user " if scope == "user" else ""
        command = f"journalctl {journal_prefix}-u {shlex.quote(service)} -n {lines} --no-pager"
    else:
        command = f"journalctl -n {lines} --no-pager"
    code, out, err = ssh.run(command, timeout=120)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    return out.strip() or "没有日志输出。"
