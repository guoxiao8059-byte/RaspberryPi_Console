# -*- coding: utf-8 -*-
from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result

def run_command(ssh: SshClient, payload: dict) -> str:
        code, out, err = ssh.run(payload.get("command", ""), timeout=int(payload.get("timeout", 240)))
        if code != 0:
            raise RuntimeError(format_command_result(code, out, err))
        return format_command_result(code, out, err)
