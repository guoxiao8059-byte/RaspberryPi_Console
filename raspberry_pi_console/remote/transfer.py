# -*- coding: utf-8 -*-
from __future__ import annotations

from raspberry_pi_console.core.ssh_client import ProgressCallback, SshClient


def run_transfer(ssh: SshClient, payload: dict, progress: ProgressCallback | None = None) -> str:
    direction = payload.get("direction")
    mode = payload.get("mode")
    local_path = payload.get("local_path", "")
    local_paths = payload.get("local_paths") or []
    remote_path = payload.get("remote_path", "")

    if direction == "upload" and mode == "file":
        if len(local_paths) > 1:
            results = ssh.upload_files(local_paths, remote_path, progress=progress)
            return "上传文件完成：\n" + "\n".join(
                f"{local} -> {remote}" for local, remote in zip(local_paths, results)
            )
        result = ssh.upload_file(local_path, remote_path, progress=progress)
        return f"上传文件完成：\n{local_path}\n→\n{result}"

    if direction == "upload" and mode == "folder":
        result = ssh.upload_dir(local_path, remote_path, progress=progress)
        return f"上传文件夹完成：\n{local_path}\n→\n{result}"

    if direction == "download" and mode == "file":
        result = ssh.download_file(remote_path, local_path, progress=progress)
        return f"下载文件完成：\n{remote_path}\n→\n{result}"

    if direction == "download" and mode == "folder":
        result = ssh.download_dir(remote_path, local_path, progress=progress)
        return f"下载文件夹完成：\n{remote_path}\n→\n{result}"

    raise ValueError("未知传输模式。")
