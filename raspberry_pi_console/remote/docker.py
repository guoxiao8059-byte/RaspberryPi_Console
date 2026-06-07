# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shlex

from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result

_CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{12,64}$")


def fetch_docker_containers(ssh: SshClient) -> tuple[str, list[dict]]:
    command = (
        "docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null"
    )
    code, out, err = ssh.run(command, timeout=120)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 4:
            continue
        rows.append(
            {
                "id": parts[0].strip(),
                "name": parts[1].strip(),
                "status": parts[2].strip(),
                "image": parts[3].strip(),
                "ports": parts[4].strip() if len(parts) > 4 else "",
            }
        )
    return "Docker 容器列表已刷新。", rows


def run_docker_action(ssh: SshClient, payload: dict) -> str:
    action = payload.get("action", "")
    container_id = str(payload.get("container_id", "")).strip()
    if not _CONTAINER_ID_RE.match(container_id):
        raise ValueError("无效的容器 ID。")
    if action == "logs":
        lines = max(20, min(int(payload.get("lines", 100)), 2000))
        command = f"docker logs --tail {lines} {shlex.quote(container_id)}"
    elif action in {"start", "stop", "restart"}:
        command = f"docker {action} {shlex.quote(container_id)}"
    else:
        raise ValueError(f"未知 Docker 操作：{action}")
    code, out, err = ssh.run(command, timeout=180)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    return (out + err).strip() or f"Docker {action} 完成。"
