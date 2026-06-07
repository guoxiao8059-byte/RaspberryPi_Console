# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result

_PID_RE = re.compile(r"^\d+$")


def kill_process(ssh: SshClient, payload: dict) -> str:
    pid_text = str(payload.get("pid", "")).strip()
    if not _PID_RE.match(pid_text):
        raise ValueError("无效的进程 PID。")
    signal = int(payload.get("signal", 15))
    if signal not in {9, 15}:
        raise ValueError("仅支持 SIGTERM(15) 或 SIGKILL(9)。")
    code, out, err = ssh.run(f"kill -{signal} {pid_text}", timeout=30)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    return (out + err).strip() or f"已发送信号 {signal} 到 PID {pid_text}。"
