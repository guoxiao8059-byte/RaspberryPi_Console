# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import shlex

from raspberry_pi_console.core.ssh_client import ProgressCallback, SshClient
from raspberry_pi_console.remote.common import format_command_result

_SCRIPT_PATTERN = re.compile(r"(?<![\w./-])([\w.-]+\.sh)(?![\w.-])")


def _local_dir_name(local_dir: str) -> str:
    return os.path.basename(os.path.abspath(local_dir).rstrip("/\\"))


def _should_merge_into_target(local_dir: str, remote_dir: str) -> bool:
    """远程目录名与本地文件夹同名时，直接上传到该目录，避免多嵌套一层。"""
    local_name = _local_dir_name(local_dir)
    remote_clean = remote_dir.strip().replace("\\", "/").rstrip("/") or "/"
    if remote_clean == "/":
        return False
    return os.path.basename(remote_clean) == local_name


def _validate_deploy_command(local_dir: str, command: str) -> None:
    for match in _SCRIPT_PATTERN.finditer(command):
        script_name = match.group(1)
        script_path = os.path.join(local_dir, script_name)
        if not os.path.isfile(script_path):
            raise FileNotFoundError(
                f"本地项目目录中找不到 {script_name}：\n{local_dir}\n"
                f"请添加该脚本，或修改部署命令。"
            )


def run_deploy(ssh: SshClient, payload: dict, progress: ProgressCallback | None = None) -> str:
    local_dir = os.path.abspath(str(payload.get("local_dir", "")).strip())
    remote_dir = str(payload.get("remote_dir", "")).strip().replace("\\", "/")
    command = str(payload.get("command", "")).strip()
    if not local_dir or not os.path.isdir(local_dir):
        raise NotADirectoryError("请选择有效的本地项目目录。")
    if not remote_dir:
        raise ValueError("请填写远程部署目录。")
    if not command:
        raise ValueError("请填写部署后执行的远程命令。")

    _validate_deploy_command(local_dir, command)

    merge_into_target = _should_merge_into_target(local_dir, remote_dir)
    uploaded = ssh.upload_dir(
        local_dir,
        remote_dir,
        progress=progress,
        merge_into_target=merge_into_target,
    )
    remote_command = f"cd {shlex.quote(uploaded)} && {command}"
    code, out, err = ssh.run(remote_command, timeout=int(payload.get("timeout", 600)))
    if code != 0:
        hint = ""
        if "install.sh" in command and "No such file or directory" in (err or out):
            hint = (
                f"\n\n提示：远程工作目录为 {uploaded}。"
                "若刚浏览选择了与本地同名的远程文件夹，请确认该目录下已有 install.sh，"
                "或改用父目录作为远程目录（例如 /home/pi/projects/）。"
            )
        raise RuntimeError(format_command_result(code, out, err) + hint)
    body = (out + err).strip()
    mode = "（合并到所选远程目录）" if merge_into_target else ""
    return f"部署完成{mode}：\n本地 {local_dir}\n→ 远程 {uploaded}\n\n{body}".strip()
