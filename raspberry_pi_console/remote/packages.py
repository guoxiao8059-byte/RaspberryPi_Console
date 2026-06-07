# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shlex

from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result, parse_block


def packages_command() -> str:
    return r"""
printf "__APT__\n"
dpkg-query -W -f='${binary:Package}\t${Version}\n' 2>/dev/null | sort
printf "__END_APT__\n"
printf "__UPGRADABLE__\n"
apt list --upgradable 2>/dev/null | tail -n +2
printf "__END_UPGRADABLE__\n"
printf "__PIP__\n"
(python3 -m pip list --format=freeze 2>/dev/null || pip3 list --format=freeze 2>/dev/null || true)
printf "__END_PIP__\n"
"""


def parse_packages(output: str) -> dict:
    apt_rows = []
    pip_rows = []
    upgradable_rows = []

    for line in parse_block(output, "__APT__\n", "__END_APT__").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        name = parts[0].strip()
        version = parts[1].strip() if len(parts) > 1 else ""
        apt_rows.append({"name": name, "version": version, "extra": ""})

    for line in parse_block(output, "__PIP__\n", "__END_PIP__").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "==" in stripped:
            name, version = stripped.split("==", 1)
        else:
            name, version = stripped, ""
        pip_rows.append({"name": name, "version": version, "extra": ""})

    for line in parse_block(output, "__UPGRADABLE__\n", "__END_UPGRADABLE__").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "/" in stripped:
            name, rest = stripped.split("/", 1)
        else:
            name, rest = stripped, ""
        upgradable_rows.append({"name": name.strip(), "version": "", "extra": rest.strip()})

    return {"apt": apt_rows, "pip": pip_rows, "upgradable": upgradable_rows}


def fetch_packages(ssh: SshClient) -> tuple[str, dict]:
    code, out, err = ssh.run(packages_command(), timeout=180)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    data = parse_packages(out)
    return "软件清单已刷新。", data


def _validate_package_names(names: list[str]) -> list[str]:
    cleaned = []
    for name in names:
        text = str(name).strip()
        if not text or not _PACKAGE_NAME_RE.match(text):
            raise ValueError(f"无效的软件包名：{text}")
        cleaned.append(text)
    return cleaned


def run_package_upgrade(ssh: SshClient, payload: dict) -> str:
    source = payload.get("source", "upgradable")
    packages = _validate_package_names(payload.get("packages") or [])

    if source == "pip":
        if not packages:
            raise ValueError("请选择要升级的 PIP 包。")
        command = "python3 -m pip install --upgrade " + " ".join(shlex.quote(name) for name in packages)
        code, out, err = ssh.run(command, timeout=600)
        if code != 0:
            raise RuntimeError(format_command_result(code, out, err))
        return (out + err).strip() or "PIP 升级完成。"

    if source == "upgradable" and not packages:
        command = "sudo DEBIAN_FRONTEND=noninteractive apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y"
    else:
        if not packages:
            raise ValueError("请选择要升级的软件包。")
        joined = " ".join(shlex.quote(name) for name in packages)
        command = (
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update && "
            f"sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {joined}"
        )
    code, out, err = ssh.run(command, timeout=900)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    return (out + err).strip() or "APT 升级完成。"
