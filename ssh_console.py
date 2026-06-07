# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import shlex
import socket
import sys
import threading
import time

import paramiko

if os.name == "nt":
    import msvcrt
else:
    msvcrt = None


SPECIAL_KEY_MAP = {
    "H": "\x1b[A",
    "P": "\x1b[B",
    "K": "\x1b[D",
    "M": "\x1b[C",
    "G": "\x1b[H",
    "O": "\x1b[F",
    "I": "\x1b[5~",
    "Q": "\x1b[6~",
    "S": "\x1b[3~",
    "R": "\x1b[2~",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SSH console bridge")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-env", default="RP_SSH_PASSWORD")
    parser.add_argument("--key-path", default="")
    parser.add_argument("--key-passphrase-env", default="RP_SSH_KEY_PASSPHRASE")
    parser.add_argument("--title", default="")
    return parser.parse_args()


def get_password(env_name: str) -> str:
    password = os.environ.get(env_name, "")
    if not password:
        raise RuntimeError(f"环境变量 {env_name} 中没有 SSH 密码。")
    return password


def load_private_key(key_path: str, passphrase: str = ""):
    key_path = os.path.expanduser(key_path)
    loaders = (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)
    last_error: Exception | None = None
    for loader in loaders:
        try:
            return loader.from_private_key_file(key_path, password=passphrase or None)
        except paramiko.PasswordRequiredException as exc:
            raise RuntimeError("SSH 私钥需要口令，请通过 RP_SSH_KEY_PASSPHRASE 提供。") from exc
        except paramiko.SSHException as exc:
            last_error = exc
    raise RuntimeError(f"无法加载 SSH 私钥：{key_path}\n{last_error}")


def write_text(text: str):
    sys.stdout.write(text)
    sys.stdout.flush()


def write_bytes(data: bytes):
    sys.stdout.buffer.write(data)
    sys.stdout.flush()


def output_worker(channel: paramiko.Channel, stop_event: threading.Event):
    try:
        while not stop_event.is_set():
            if channel.recv_ready():
                data = channel.recv(4096)
                if not data:
                    break
                write_bytes(data)
                continue
            if channel.closed or channel.exit_status_ready():
                break
            time.sleep(0.01)
    finally:
        stop_event.set()


def read_key() -> str:
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        special = msvcrt.getwch()
        return SPECIAL_KEY_MAP.get(special, "")
    if key == "\r":
        return "\n"
    if key == "\x08":
        return "\x7f"
    return key


def input_worker(channel: paramiko.Channel, stop_event: threading.Event):
    while not stop_event.is_set():
        if msvcrt is None:
            break
        if not msvcrt.kbhit():
            time.sleep(0.01)
            continue
        text = read_key()
        if not text:
            continue
        try:
            channel.send(text)
        except (EOFError, OSError, socket.error):
            stop_event.set()
            break


def connect_shell(
    host: str,
    port: int,
    user: str,
    password: str,
    title: str,
    key_path: str = "",
    key_passphrase: str = "",
) -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    stop_event = threading.Event()

    try:
        if title:
            write_text(f"{title}\n")
        write_text(f"正在连接 {user}@{host}:{port} ...\n")
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": 10,
            "banner_timeout": 10,
            "auth_timeout": 10,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if key_path:
            connect_kwargs["pkey"] = load_private_key(key_path, key_passphrase)
        else:
            connect_kwargs["password"] = password
        client.connect(**connect_kwargs)
        channel = client.invoke_shell(term="xterm", width=140, height=40)
        client.get_transport().set_keepalive(30)
        write_text("连接成功，已进入远程终端。\n\n")

        out_thread = threading.Thread(target=output_worker, args=(channel, stop_event), daemon=True)
        in_thread = threading.Thread(target=input_worker, args=(channel, stop_event), daemon=True)
        out_thread.start()
        in_thread.start()

        while not stop_event.is_set():
            if channel.closed or channel.exit_status_ready():
                stop_event.set()
                break
            time.sleep(0.05)

        out_thread.join(timeout=1)
        in_thread.join(timeout=1)
        return 0
    except Exception as exc:
        write_text(f"\n连接失败：{exc}\n")
        return 1
    finally:
        stop_event.set()
        try:
            client.close()
        except Exception:
            pass


def main() -> int:
    args = parse_args()
    key_path = args.key_path.strip()
    key_passphrase = os.environ.get(args.key_passphrase_env, "")
    password = ""
    if not key_path:
        try:
            password = get_password(args.password_env)
        except RuntimeError as exc:
            write_text(f"{exc}\n")
            return 1
    return connect_shell(
        args.host,
        args.port,
        args.user,
        password,
        args.title,
        key_path=key_path,
        key_passphrase=key_passphrase,
    )


if __name__ == "__main__":
    raise SystemExit(main())
