# -*- coding: utf-8 -*-
from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result


def services_command(scope: str) -> str:
    prefix = "systemctl --user" if scope == "user" else "systemctl"
    return f"{prefix} list-units --type=service --all --plain --no-legend --no-pager"


def fetch_services(ssh: SshClient, scope: str) -> tuple[str, list[dict]]:
    code, out, err = ssh.run(services_command(scope), timeout=120)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    rows = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 4)
        if len(parts) < 5:
            continue
        rows.append(
            {
                "unit": parts[0],
                "load": parts[1],
                "active": parts[2],
                "sub": parts[3],
                "description": parts[4],
            }
        )
    return "服务列表已刷新。", rows
