# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.core.network import normalize_mac_address
from raspberry_pi_console.remote.common import format_command_result, parse_block

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\].*?(?:\x07|\x1b\\)|\r")


def _clean_remote_output(text: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", text or "")
    return cleaned.replace("\r", "")


def _interface_scan_command() -> str:
    return r"""
printf '%s\n' '__IF__'
for iface in $(ls /sys/class/net 2>/dev/null | grep -Ev '^(lo|docker|br-|veth|tailscale|zt)'); do
  mac=$(cat /sys/class/net/$iface/address 2>/dev/null)
  state=$(cat /sys/class/net/$iface/operstate 2>/dev/null)
  ip=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
  kind=wired
  case "$iface" in
    wlan*|wl*) kind=wireless ;;
  esac
  printf 'iface=%s\n' "$iface"
  printf 'mac=%s\n' "$mac"
  printf 'ip=%s\n' "$ip"
  printf 'state=%s\n' "$state"
  printf 'kind=%s\n' "$kind"
  printf '%s\n' '---'
done
printf '%s\n' '__END__'
"""


def _enable_wireless_command() -> str:
    return r"""
sudo rfkill unblock wifi 2>/dev/null || true
WIFI_IF=$(ip -o link 2>/dev/null | awk -F': ' '{print $2}' | grep -E '^wlan|^wl' | head -1)
if [ -z "$WIFI_IF" ]; then
  WIFI_IF=wlan0
fi
if [ ! -e "/sys/class/net/$WIFI_IF" ]; then
  echo "未找到无线网卡接口。"
  exit 1
fi
sudo ip link set "$WIFI_IF" up 2>/dev/null || true
if command -v nmcli >/dev/null 2>&1; then
  sudo nmcli radio wifi on 2>/dev/null || true
  sudo nmcli device connect "$WIFI_IF" 2>/dev/null || true
elif command -v wpa_cli >/dev/null 2>&1; then
  sudo wpa_cli -i "$WIFI_IF" reconfigure 2>/dev/null || true
fi
if systemctl is-active --quiet dhcpcd 2>/dev/null; then
  sudo dhcpcd "$WIFI_IF" 2>/dev/null || true
fi
sleep 3
""" + _interface_scan_command()


def _interface_kind(iface: str) -> str:
    name = str(iface or "").lower()
    if name.startswith(("wlan", "wl")):
        return "wireless"
    return "wired"


def _interface_score(entry: dict, kind: str) -> tuple:
    iface = str(entry.get("iface", "")).lower()
    has_ip = 1 if entry.get("ip") else 0
    is_up = 1 if str(entry.get("state", "")).lower() == "up" else 0
    if kind == "wired":
        name_rank = {"eth0": 0}.get(iface, 1 if iface.startswith("en") else 2)
    else:
        name_rank = {"wlan0": 0}.get(iface, 1 if iface.startswith("wl") else 2)
    return (-has_ip, -is_up, name_rank, iface)


def _pick_interface(entries: list[dict], kind: str) -> dict | None:
    candidates = [entry for entry in entries if _interface_kind(str(entry.get("iface", ""))) == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda entry: _interface_score(entry, kind))


def _parse_interfaces(output: str) -> dict:
    result = {"wired": None, "wireless": None, "interfaces": []}
    cleaned = _clean_remote_output(output)
    block = parse_block(cleaned, "__IF__\n", "__END__")
    chunks = [chunk.strip() for chunk in block.split("---") if chunk.strip()]
    for chunk in chunks:
        entry = {}
        for line in chunk.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            entry[key.strip()] = value.strip()
        iface = entry.get("iface")
        if not iface:
            continue
        entry["kind"] = _interface_kind(str(iface))
        if entry.get("mac"):
            entry["mac"] = normalize_mac_address(entry["mac"])
        result["interfaces"].append(entry)

    result["wired"] = _pick_interface(result["interfaces"], "wired")
    result["wireless"] = _pick_interface(result["interfaces"], "wireless")
    return result


def fetch_network_interfaces(ssh: SshClient) -> tuple[str, dict]:
    code, out, err = ssh.run(_interface_scan_command(), timeout=30)
    cleaned_out = _clean_remote_output(out)
    cleaned_err = _clean_remote_output(err)
    data = _parse_interfaces(cleaned_out)
    if code != 0 and not data.get("interfaces"):
        raise RuntimeError(format_command_result(code, cleaned_out, cleaned_err))
    return "已读取树莓派网络接口信息。", data


def enable_wireless_network(ssh: SshClient) -> tuple[str, dict]:
    code, out, err = ssh.run(_enable_wireless_command(), timeout=90)
    cleaned_out = _clean_remote_output(out)
    cleaned_err = _clean_remote_output(err)
    combined = f"{cleaned_out}\n{cleaned_err}".lower()
    data = _parse_interfaces(cleaned_out)
    wifi_activated = "successfully activated" in combined or "已连接" in combined
    if code != 0 and not wifi_activated and not data.get("wireless"):
        raise RuntimeError(format_command_result(code, cleaned_out, cleaned_err))

    wireless = data.get("wireless") or {}
    wired = data.get("wired") or {}
    ip_text = str(wireless.get("ip", "")).strip()
    iface = str(wireless.get("iface", "")).strip()
    mac = str(wireless.get("mac", "")).strip()
    wired_mac = str(wired.get("mac", "")).strip()

    if mac and wired_mac and mac == wired_mac:
        wireless = dict(wireless)
        wireless["mac"] = ""
        mac = ""
        data = dict(data)
        data["wireless"] = wireless

    if ip_text:
        text = f"无线网络已启用：{iface} -> {ip_text}"
        if mac:
            text += f" (MAC {mac})"
    elif wifi_activated:
        text = f"无线网络已启用（{iface or 'wlan0'}），但尚未获取到 IP，请稍后重新扫描无线 MAC。"
    else:
        text = f"已尝试启用无线网络（{iface or 'wlan0'}），但尚未获取到 IP，请稍后重新扫描。"
    return text, data
