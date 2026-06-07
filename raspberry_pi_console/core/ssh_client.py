# -*- coding: utf-8 -*-
import os
import stat
from collections.abc import Callable

import paramiko

from raspberry_pi_console.core.config import SshConfig

ProgressCallback = Callable[[int, int, str], None]


class SshClient:
    def __init__(self, config: SshConfig):
        self.config = config
        self.client = None

    def connect(self):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs = {
            "hostname": self.config.host,
            "port": self.config.port,
            "username": self.config.username,
            "timeout": 10,
            "banner_timeout": 10,
            "auth_timeout": 10,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if self.config.uses_key():
            connect_kwargs["pkey"] = self._load_private_key()
        else:
            connect_kwargs["password"] = self.config.password
        client.connect(**connect_kwargs)
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        self.client = client

    @staticmethod
    def _load_private_key_file(key_path: str, passphrase: str = ""):
        key_path = os.path.expanduser(key_path)
        loaders = (
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.RSAKey,
        )
        last_error: Exception | None = None
        for loader in loaders:
            try:
                return loader.from_private_key_file(key_path, password=passphrase or None)
            except paramiko.PasswordRequiredException as exc:
                raise ValueError("SSH 私钥需要口令，请在连接设置中填写私钥口令。") from exc
            except paramiko.SSHException as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise ValueError(f"无法加载 SSH 私钥：{key_path}\n{last_error}") from last_error
        raise ValueError(f"无法加载 SSH 私钥：{key_path}")

    def _load_private_key(self):
        return self._load_private_key_file(self.config.key_path, self.config.key_passphrase)

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def run(self, command: str, timeout: int = 240):
        if self.client is None:
            self.connect()
        _, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return code, out, err

    def open_sftp(self):
        if self.client is None:
            self.connect()
        return self.client.open_sftp()

    @staticmethod
    def normalize_remote_path(remote_path: str) -> str:
        text = str(remote_path or "").strip().replace("\\", "/")
        if not text:
            return "/"
        if len(text) > 1 and text.endswith("/"):
            text = text.rstrip("/")
        return text or "/"

    @staticmethod
    def _remote_join(*parts: str) -> str:
        cleaned = []
        for part in parts:
            if not part:
                continue
            cleaned.append(str(part).strip("/"))
        if not cleaned:
            return "/"
        if str(parts[0]).startswith("/"):
            return "/" + "/".join(cleaned)
        return "/".join(cleaned)

    def _sftp_exists(self, sftp, remote_path: str) -> bool:
        try:
            sftp.stat(remote_path)
            return True
        except IOError:
            return False

    def _sftp_is_dir(self, sftp, remote_path: str) -> bool:
        try:
            return stat.S_ISDIR(sftp.stat(remote_path).st_mode)
        except IOError:
            return False

    def list_remote_dir(self, remote_path: str) -> list[dict]:
        sftp = self.open_sftp()
        try:
            normalized = self.normalize_remote_path(remote_path)
            items = []
            for entry in sftp.listdir_attr(normalized):
                child_path = self._remote_join(normalized, entry.filename)
                is_dir = stat.S_ISDIR(entry.st_mode)
                items.append(
                    {
                        "name": entry.filename,
                        "path": child_path,
                        "is_dir": is_dir,
                        "size": int(getattr(entry, "st_size", 0) or 0),
                    }
                )
            items.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
            return items
        finally:
            sftp.close()

    def remote_path_is_dir(self, remote_path: str) -> bool:
        sftp = self.open_sftp()
        try:
            return self._sftp_is_dir(sftp, self.normalize_remote_path(remote_path))
        finally:
            sftp.close()

    def _sftp_mkdirs(self, sftp, remote_dir: str):
        remote_dir = remote_dir.replace("\\", "/")
        if not remote_dir or remote_dir == "/":
            return
        parts = remote_dir.strip("/").split("/")
        current = "" if not remote_dir.startswith("/") else "/"
        for part in parts:
            current = self._remote_join(current, part)
            if not self._sftp_exists(sftp, current):
                try:
                    sftp.mkdir(current)
                except PermissionError as exc:
                    raise PermissionError(
                        f"树莓派路径无写入权限：{current}\n"
                        f"建议使用 /home/{self.config.username}/uploads/ 或 /home/{self.config.username}/projects/。"
                    ) from exc

    def upload_file(self, local_file: str, remote_path: str, progress: ProgressCallback | None = None) -> str:
        if not os.path.isfile(local_file):
            raise FileNotFoundError(
                f"本地文件不存在或不是文件：{local_file}\n"
                "如果要上传整个文件夹，请在“类型”中选择“文件夹”。"
            )
        sftp = self.open_sftp()
        try:
            remote_path = remote_path.replace("\\", "/")
            if remote_path.endswith("/") or self._sftp_is_dir(sftp, remote_path):
                remote_file = self._remote_join(remote_path, os.path.basename(local_file))
            else:
                remote_file = remote_path
            remote_dir = remote_file.rsplit("/", 1)[0] if "/" in remote_file else "."
            self._sftp_mkdirs(sftp, remote_dir)
            self._sftp_put(sftp, local_file, remote_file, progress)
            return remote_file
        finally:
            sftp.close()

    def upload_files(self, local_files: list[str], remote_path: str, progress: ProgressCallback | None = None) -> list[str]:
        files = [os.path.abspath(path) for path in local_files if str(path).strip()]
        if not files:
            raise FileNotFoundError("未选择要上传的文件。")

        missing = [path for path in files if not os.path.isfile(path)]
        if missing:
            raise FileNotFoundError("以下本地文件不存在或不是文件：\n" + "\n".join(missing[:10]))

        sftp = self.open_sftp()
        try:
            remote_dir = remote_path.replace("\\", "/").rstrip("/") or "/"
            if remote_dir != "/" and self._sftp_exists(sftp, remote_dir) and not self._sftp_is_dir(sftp, remote_dir):
                raise NotADirectoryError(
                    f"树莓派路径不是文件夹：{remote_dir}\n"
                    "多文件上传需要指定目录，例如 /home/pi/uploads/。"
                )
            self._sftp_mkdirs(sftp, remote_dir)

            uploaded = []
            for local_file in files:
                remote_file = self._remote_join(remote_dir, os.path.basename(local_file))
                self._sftp_put(sftp, local_file, remote_file, progress)
                uploaded.append(remote_file)
            return uploaded
        finally:
            sftp.close()

    def upload_dir(
        self,
        local_dir: str,
        remote_dir: str,
        progress: ProgressCallback | None = None,
        *,
        merge_into_target: bool = False,
    ) -> str:
        if not os.path.isdir(local_dir):
            raise NotADirectoryError(
                f"本地文件夹不存在或不是文件夹：{local_dir}\n"
                "如果只上传单个文件，请在“类型”中选择“文件”。"
            )
        sftp = self.open_sftp()
        try:
            local_dir = os.path.abspath(local_dir)
            remote_base = remote_dir.replace("\\", "/").rstrip("/") or "/"
            if merge_into_target:
                target_root = remote_base
            else:
                root_name = os.path.basename(local_dir.rstrip("/\\"))
                target_root = self._remote_join(remote_base, root_name)
            self._sftp_mkdirs(sftp, target_root)

            for current_dir, dir_names, file_names in os.walk(local_dir):
                rel = os.path.relpath(current_dir, local_dir)
                remote_current = target_root if rel == "." else self._remote_join(target_root, rel.replace("\\", "/"))
                self._sftp_mkdirs(sftp, remote_current)

                for dir_name in dir_names:
                    self._sftp_mkdirs(sftp, self._remote_join(remote_current, dir_name))

                for file_name in file_names:
                    local_file = os.path.join(current_dir, file_name)
                    remote_file = self._remote_join(remote_current, file_name)
                    self._sftp_put(sftp, local_file, remote_file, progress)
            return target_root
        finally:
            sftp.close()

    def download_file(self, remote_file: str, local_path: str, progress: ProgressCallback | None = None) -> str:
        sftp = self.open_sftp()
        try:
            if os.path.isdir(local_path) or local_path.endswith(os.sep):
                local_file = os.path.join(local_path, os.path.basename(remote_file.rstrip("/")))
            else:
                local_file = local_path
            os.makedirs(os.path.dirname(os.path.abspath(local_file)), exist_ok=True)
            self._sftp_get(sftp, remote_file, local_file, progress)
            return local_file
        finally:
            sftp.close()

    def download_dir(self, remote_dir: str, local_parent_dir: str, progress: ProgressCallback | None = None) -> str:
        sftp = self.open_sftp()
        try:
            remote_dir = remote_dir.rstrip("/")
            root_name = remote_dir.split("/")[-1] or "remote_folder"
            local_root = os.path.join(local_parent_dir, root_name)
            os.makedirs(local_root, exist_ok=True)
            self._download_dir_recursive(sftp, remote_dir, local_root, progress)
            return local_root
        finally:
            sftp.close()

    def _download_dir_recursive(
        self,
        sftp,
        remote_dir: str,
        local_dir: str,
        progress: ProgressCallback | None = None,
    ):
        os.makedirs(local_dir, exist_ok=True)
        for item in sftp.listdir_attr(remote_dir):
            remote_item = self._remote_join(remote_dir, item.filename)
            local_item = os.path.join(local_dir, item.filename)
            if stat.S_ISDIR(item.st_mode):
                self._download_dir_recursive(sftp, remote_item, local_item, progress)
            else:
                os.makedirs(os.path.dirname(local_item), exist_ok=True)
                self._sftp_get(sftp, remote_item, local_item, progress)

    @staticmethod
    def _sftp_put(sftp, local_file: str, remote_file: str, progress: ProgressCallback | None = None) -> None:
        if progress is None:
            sftp.put(local_file, remote_file)
            return
        total = max(1, os.path.getsize(local_file))
        label = os.path.basename(local_file)

        def callback(done: int, _total: int) -> None:
            progress(min(done, total), total, label)

        sftp.put(local_file, remote_file, callback=callback)

    @staticmethod
    def _sftp_get(sftp, remote_file: str, local_file: str, progress: ProgressCallback | None = None) -> None:
        if progress is None:
            sftp.get(remote_file, local_file)
            return
        try:
            total = max(1, sftp.stat(remote_file).st_size)
        except IOError:
            total = 1
        label = os.path.basename(remote_file.rstrip("/"))

        def callback(done: int, _total: int) -> None:
            progress(min(done, total), total, label)

        sftp.get(remote_file, local_file, callback=callback)

