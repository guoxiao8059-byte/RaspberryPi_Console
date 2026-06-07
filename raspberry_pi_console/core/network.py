# -*- coding: utf-8 -*-
import ipaddress
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field

from raspberry_pi_console.constants import (
    NETWORK_MODE_LABELS,
    NETWORK_MODE_WIRED,
    NETWORK_MODE_WIRELESS,
    normalize_network_mode,
)


def normalize_mac_address(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", str(text or ""))
    if len(cleaned) != 12:
        return ""
    return ":".join(cleaned[index : index + 2].lower() for index in range(0, 12, 2))


def _is_usable_ipv4(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return (
        ip.version == 4
        and not ip.is_loopback
        and not ip.is_multicast
        and not ip.is_unspecified
        and not ip.is_link_local
    )


def _local_ipv4_candidates() -> list[str]:
    candidates = set()

    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip_text = str(sockaddr[0]).strip()
            if _is_usable_ipv4(ip_text):
                candidates.add(ip_text)
    except socket.gaierror:
        pass

    probe_socket = None
    try:
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe_socket.connect(("192.0.2.1", 1))
        ip_text = probe_socket.getsockname()[0]
        if _is_usable_ipv4(ip_text):
            candidates.add(ip_text)
    except OSError:
        pass
    finally:
        if probe_socket is not None:
            probe_socket.close()

    return sorted(candidates)


def _is_scan_subnet(network: ipaddress.IPv4Network) -> bool:
    if network.version != 4:
        return False
    if network.network_address.is_link_local:
        return False
    if network.subnet_of(ipaddress.ip_network("198.18.0.0/15")):
        return False
    if network.is_private:
        return True
    return network.num_addresses <= 256


def _candidate_subnets(current_host: str = "") -> list[ipaddress.IPv4Network]:
    seen = set()
    networks = []

    def add_network(ip_text: str):
        if not _is_usable_ipv4(ip_text):
            return
        network = ipaddress.ip_network(f"{ip_text}/24", strict=False)
        if str(network) in seen or not _is_scan_subnet(network):
            return
        seen.add(str(network))
        networks.append(network)

    add_network(current_host)
    for ip_text in _local_ipv4_candidates():
        add_network(ip_text)
    return networks


def _scan_subnets(current_host: str, network_mode: str) -> list[ipaddress.IPv4Network]:
    candidates = _candidate_subnets(current_host)
    if not candidates:
        return []

    mode = normalize_network_mode(network_mode)
    if current_host and _is_usable_ipv4(current_host):
        preferred = ipaddress.ip_network(f"{current_host}/24", strict=False)
        if preferred in candidates:
            if mode == NETWORK_MODE_WIRED:
                return [preferred]
            rest = [network for network in candidates if network != preferred]
            return [preferred, *rest[:1]]

    return candidates[:2]


def _ping_host(ip_text: str, timeout_ms: int = 500) -> bool:
    try:
        completed = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip_text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(2, int(timeout_ms / 1000) + 2),
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _resolve_hostname_ping(ip_text: str, timeout_ms: int = 500) -> str:
    try:
        completed = subprocess.run(
            ["ping", "-a", "-n", "1", "-w", str(timeout_ms), ip_text],
            capture_output=True,
            text=True,
            encoding=_subprocess_text_encoding(),
            errors="replace",
            timeout=max(2, int(timeout_ms / 1000) + 2),
            check=False,
        )
        if completed.returncode != 0:
            return ""
        return _parse_ping_hostname(completed.stdout, ip_text)
    except (OSError, subprocess.SubprocessError):
        return ""


def _subprocess_text_encoding() -> str:
    return "gbk" if sys.platform == "win32" else "utf-8"


def _parse_ping_hostname(output: str, ip_text: str) -> str:
    patterns = (
        rf"Ping(?:ing)?\s+(.+?)\s+\[{re.escape(ip_text)}\]",
        rf"正在\s+Ping\s+(.+?)\s+\[{re.escape(ip_text)}\]",
    )
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if not match:
            continue
        hostname = match.group(1).strip()
        if hostname and hostname != ip_text and not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", hostname):
            return hostname
    return ""


def _resolve_discovery_hostname(item: ArpDiscovery) -> None:
    if item.hostname:
        return
    if item.online:
        hostname = _resolve_hostname_ping(item.ip, timeout_ms=400)
        if hostname:
            item.hostname = hostname


@dataclass
class ArpEntry:
    ip: str
    mac: str
    hostname: str = ""


def _arp_table() -> dict[str, ArpEntry]:
    try:
        completed = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            encoding=_subprocess_text_encoding(),
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}

    named_pattern = re.compile(
        r"(?P<host>[^\s\[]+)\s+\[(?P<ip>\d+\.\d+\.\d+\.\d+)\]\s+"
        r"(?P<mac>(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2})"
    )
    plain_pattern = re.compile(
        r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2})"
    )
    result: dict[str, ArpEntry] = {}
    for line in completed.stdout.splitlines():
        named_match = named_pattern.search(line)
        if named_match:
            ip_text = named_match.group("ip")
            mac_text = normalize_mac_address(named_match.group("mac"))
            if not mac_text:
                continue
            host_text = named_match.group("host").strip()
            hostname = "" if host_text == ip_text else host_text
            result[ip_text] = ArpEntry(ip=ip_text, mac=mac_text, hostname=hostname)
            continue

        plain_match = plain_pattern.search(line)
        if not plain_match:
            continue
        ip_text = plain_match.group("ip")
        mac_text = normalize_mac_address(plain_match.group("mac"))
        if not mac_text:
            continue
        previous = result.get(ip_text)
        if previous is None:
            result[ip_text] = ArpEntry(ip=ip_text, mac=mac_text)
        elif previous.mac == mac_text and not previous.hostname:
            result[ip_text] = ArpEntry(ip=ip_text, mac=mac_text, hostname=previous.hostname)
    return result


def resolve_target_macs(
    network_mode: str = NETWORK_MODE_WIRED,
    mac_address: str = "",
    mac_address_wired: str = "",
    mac_address_wireless: str = "",
) -> list[tuple[str, str]]:
    wired = normalize_mac_address(mac_address_wired) or normalize_mac_address(mac_address)
    wireless = normalize_mac_address(mac_address_wireless)
    mode = normalize_network_mode(network_mode)

    if mode == NETWORK_MODE_WIRED:
        if not wired:
            raise ValueError("未配置有线网 MAC 地址，请在连接设置中填写。")
        return [(NETWORK_MODE_WIRED, wired)]

    if mode == NETWORK_MODE_WIRELESS:
        if not wireless:
            raise ValueError("未配置无线网 MAC 地址。请先通过有线连接，在“按 MAC 查找 IP”里点击“从设备读取 MAC”。")
        if wired and wireless == wired:
            raise ValueError(
                f"无线 MAC 与有线 MAC 相同（{wireless}）。\n"
                "请填写无线网卡 wlan0 的真实 MAC，不要使用 eth0 的地址。"
            )
        return [(NETWORK_MODE_WIRELESS, wireless)]

    raise ValueError(f"未知联网模式：{mode}")


@dataclass
class ArpDiscovery:
    ip: str
    mac: str
    online: bool
    stage: str
    hostname: str = ""
    is_target: bool = False
    target_mode: str = ""


@dataclass
class MacScanResult:
    ip: str
    mac: str
    mode: str
    online: bool
    stage: str = "arp"

    @property
    def mode_label(self) -> str:
        return NETWORK_MODE_LABELS.get(self.mode, self.mode)


@dataclass
class MacScanReport:
    host: str = ""
    matched_mode: str = ""
    matched_mac: str = ""
    attempts: list[str] = field(default_factory=list)
    results: list[MacScanResult] = field(default_factory=list)
    discoveries: list[ArpDiscovery] = field(default_factory=list)
    subnets: list[str] = field(default_factory=list)
    target_macs: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "matched_mode": self.matched_mode,
            "matched_mac": self.matched_mac,
            "attempts": list(self.attempts),
            "results": [asdict(item) for item in self.results],
            "discoveries": [asdict(item) for item in self.discoveries],
            "subnets": list(self.subnets),
            "target_macs": list(self.target_macs),
        }


def _ip_in_subnet(ip_text: str, subnet: ipaddress.IPv4Network | None) -> bool:
    if subnet is None:
        return True
    try:
        return ipaddress.ip_address(ip_text) in subnet
    except ValueError:
        return False


def _collect_arp_discoveries(
    target_macs: dict[str, str],
    stage: str,
    *,
    subnet: ipaddress.IPv4Network | None = None,
    assume_online: bool = False,
    arp_entries: dict[str, ArpEntry] | None = None,
) -> list[ArpDiscovery]:
    discoveries: list[ArpDiscovery] = []
    entries = arp_entries if arp_entries is not None else _arp_table()
    for ip_text, entry in entries.items():
        if not _ip_in_subnet(ip_text, subnet):
            continue
        is_target = entry.mac in target_macs
        discoveries.append(
            ArpDiscovery(
                ip=ip_text,
                mac=entry.mac,
                online=assume_online,
                stage=stage,
                hostname=entry.hostname,
                is_target=is_target,
                target_mode=target_macs.get(entry.mac, ""),
            )
        )
    return discoveries


def _merge_discoveries(existing: list[ArpDiscovery], new_items: list[ArpDiscovery]) -> list[ArpDiscovery]:
    merged: dict[str, ArpDiscovery] = {item.ip: item for item in existing}
    for item in new_items:
        previous = merged.get(item.ip)
        if previous is None:
            merged[item.ip] = item
            continue
        stage = item.stage if item.stage.startswith("网段") else previous.stage
        merged[item.ip] = ArpDiscovery(
            ip=item.ip,
            mac=item.mac,
            online=item.online or previous.online,
            stage=stage,
            hostname=item.hostname or previous.hostname,
            is_target=item.is_target or previous.is_target,
            target_mode=item.target_mode or previous.target_mode,
        )
    return sorted(merged.values(), key=lambda item: ipaddress.ip_address(item.ip))


def _discoveries_to_target_results(discoveries: list[ArpDiscovery]) -> list[MacScanResult]:
    results: list[MacScanResult] = []
    for item in discoveries:
        if not item.is_target or not item.target_mode:
            continue
        results.append(
            MacScanResult(
                ip=item.ip,
                mac=item.mac,
                mode=item.target_mode,
                online=item.online,
                stage=item.stage,
            )
        )
    return results


def _refresh_target_online(discoveries: list[ArpDiscovery], *, timeout_ms: int = 800) -> None:
    targets = [item for item in discoveries if item.is_target]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as executor:
        futures = {executor.submit(_ping_host, item.ip, timeout_ms): item for item in targets}
        for future in as_completed(futures):
            item = futures[future]
            try:
                item.online = bool(future.result())
            except Exception:
                item.online = False


def _refresh_discovery_online(discoveries: list[ArpDiscovery], *, timeout_ms: int = 300) -> None:
    if not discoveries:
        return
    with ThreadPoolExecutor(max_workers=min(16, len(discoveries))) as executor:
        futures = {executor.submit(_ping_host, item.ip, timeout_ms): item for item in discoveries}
        for future in as_completed(futures):
            item = futures[future]
            try:
                item.online = bool(future.result())
            except Exception:
                item.online = False


def _probe_subnet_hosts(network: ipaddress.IPv4Network, *, timeout_ms: int = 200, deadline: float) -> None:
    hosts = [str(host) for host in network.hosts()]
    if not hosts:
        return
    with ThreadPoolExecutor(max_workers=min(32, len(hosts))) as executor:
        futures = [executor.submit(_ping_host, host, timeout_ms) for host in hosts]
        for future in as_completed(futures):
            if time.monotonic() >= deadline:
                break
            try:
                future.result()
            except Exception:
                continue


def _format_discovery_name(hostname: str) -> str:
    hostname = str(hostname or "").strip()
    return f"名称 {hostname} | " if hostname else ""


def _matched_discovery(report: MacScanReport) -> ArpDiscovery | None:
    if not report.host:
        return None
    for item in report.discoveries:
        if item.ip == report.host and item.mac == report.matched_mac:
            return item
    return None


def _merge_scan_results(existing: list[MacScanResult], new_items: list[MacScanResult]) -> list[MacScanResult]:
    merged = {item.ip: item for item in existing}
    for item in new_items:
        previous = merged.get(item.ip)
        if previous is None or (item.online and not previous.online):
            merged[item.ip] = item
    return sorted(merged.values(), key=lambda item: (item.mode, item.ip))


def _pick_best_result(results: list[MacScanResult], preferred_mode: str) -> MacScanResult | None:
    online = [item for item in results if item.online]
    if not online:
        return None
    preferred_mode = normalize_network_mode(preferred_mode)
    for item in online:
        if item.mode == preferred_mode:
            return item
    return online[0]


def format_mac_scan_report(report: MacScanReport) -> str:
    lines = ["扫描结果汇总", "--------------"]
    if report.target_macs:
        target_text = "，".join(
            f"{NETWORK_MODE_LABELS.get(mode, mode)} MAC {mac}" for mode, mac in report.target_macs
        )
        lines.append(f"扫描目标：{target_text}")
    if report.subnets:
        lines.append(f"扫描网段：{', '.join(report.subnets)}")
    lines.append("")
    if report.discoveries:
        lines.append(f"共发现 {len(report.discoveries)} 台设备（IP / 名称 / MAC）：")
        for index, item in enumerate(report.discoveries, start=1):
            status = "在线" if item.online else "离线"
            marker = " ★目标" if item.is_target else ""
            lines.append(
                f"{index}. IP {item.ip} | {_format_discovery_name(item.hostname)}"
                f"MAC {item.mac} | {status} | {item.stage}{marker}"
            )
    else:
        lines.append("未发现任何 ARP 设备。")

    lines.extend(["", "命中结果："])
    if report.host:
        mode_label = NETWORK_MODE_LABELS.get(report.matched_mode, report.matched_mode)
        matched = _matched_discovery(report)
        lines.append(f"已命中目标 MAC {report.matched_mac}")
        lines.append(
            f"IP {report.host} | {_format_discovery_name(matched.hostname if matched else '')}"
            f"{mode_label} | MAC {report.matched_mac} | 在线"
        )
    else:
        if report.target_macs:
            target_text = "，".join(
                f"{NETWORK_MODE_LABELS.get(mode, mode)} MAC {mac}" for mode, mac in report.target_macs
            )
            lines.append(f"未命中。未找到在线的目标设备：{target_text}")
        else:
            lines.append("未命中。未配置扫描目标 MAC。")
    return "\n".join(lines)


def _snapshot_discoveries(
    report: MacScanReport,
    target_macs: dict[str, str],
    stage: str,
    *,
    subnet: ipaddress.IPv4Network | None = None,
    assume_online: bool = False,
    arp_entries: dict[str, ArpEntry] | None = None,
) -> None:
    items = _collect_arp_discoveries(
        target_macs,
        stage,
        subnet=subnet,
        assume_online=assume_online,
        arp_entries=arp_entries,
    )
    report.discoveries = _merge_discoveries(report.discoveries, items)
    report.results = _merge_scan_results(report.results, _discoveries_to_target_results(items))


def format_connection_detail(
    host: str,
    network_mode: str,
    *,
    mac_wired: str = "",
    mac_wireless: str = "",
    mac_matched: str = "",
    source: str = "配置 IP",
) -> str:
    mode = normalize_network_mode(network_mode)
    mode_label = NETWORK_MODE_LABELS.get(mode, mode)
    ip_text = host or "未设置"

    if mac_matched:
        mac_info = f"MAC {mac_matched}（{mode_label} 扫描命中）"
    elif mode == NETWORK_MODE_WIRED:
        mac = normalize_mac_address(mac_wired)
        mac_info = f"MAC {mac}（有线）" if mac else "MAC 未配置"
    else:
        mac = normalize_mac_address(mac_wireless)
        mac_info = f"MAC {mac}（无线）" if mac else "MAC 未配置"

    return f"IP {ip_text} | {mode_label} | {mac_info} | 来源：{source}"


def discover_host_by_mac(
    mac_address: str,
    current_host: str = "",
    *,
    network_mode: str = NETWORK_MODE_WIRED,
    mac_address_wired: str = "",
    mac_address_wireless: str = "",
    progress: Callable[[int, int, str], None] | None = None,
) -> MacScanReport:
    def step(label: str) -> None:
        if progress:
            progress(0, 0, label)

    targets = resolve_target_macs(network_mode, mac_address, mac_address_wired, mac_address_wireless)
    target_macs = {mac: mode for mode, mac in targets}
    attempts: list[str] = []
    report = MacScanReport(target_macs=list(targets))

    arp_subnets = _candidate_subnets(current_host)
    scan_subnets = _scan_subnets(current_host, network_mode)
    report.subnets = [str(network) for network in arp_subnets]
    if not arp_subnets:
        attempts.append("未识别到当前电脑的可用 IPv4 网段，无法按 MAC 扫描。")
        report.attempts = attempts
        return report

    step("正在读取 ARP 缓存…")
    arp_entries = _arp_table()
    for network in arp_subnets:
        _snapshot_discoveries(report, target_macs, "ARP 缓存", subnet=network, arp_entries=arp_entries)

    step(f"已发现 {len(report.discoveries)} 台设备，正在验证目标 MAC…")
    _refresh_target_online(report.discoveries)
    report.results = _discoveries_to_target_results([item for item in report.discoveries if item.is_target])
    best = _pick_best_result(report.results, network_mode)
    if best:
        report.host = best.ip
        report.matched_mode = best.mode
        report.matched_mac = best.mac
        matched = _matched_discovery(report) or next(
            (item for item in report.discoveries if item.ip == best.ip),
            None,
        )
        if matched:
            _resolve_discovery_hostname(matched)
        step("命中目标，正在汇总结果…")
        _refresh_discovery_online(report.discoveries, timeout_ms=300)
        report.attempts = attempts
        return report

    sweep_deadline = time.monotonic() + 45
    for network in scan_subnets:
        if time.monotonic() >= sweep_deadline:
            attempts.append("网段扫描已达到时间上限，停止继续扫描。")
            break

        network_text = str(network)
        step(f"正在扫描网段 {network_text}…")
        attempts.append(f"开始扫描网段 {network_text}")
        _probe_subnet_hosts(network, timeout_ms=200, deadline=sweep_deadline)

        stage = f"网段 {network_text}"
        _snapshot_discoveries(
            report,
            target_macs,
            stage,
            subnet=network,
            assume_online=True,
            arp_entries=_arp_table(),
        )
        _refresh_target_online(report.discoveries, timeout_ms=800)
        report.results = _discoveries_to_target_results(
            [item for item in report.discoveries if item.is_target]
        )
        best = _pick_best_result(report.results, network_mode)
        if best:
            report.host = best.ip
            report.matched_mode = best.mode
            report.matched_mac = best.mac
            matched = _matched_discovery(report) or next(
                (item for item in report.discoveries if item.ip == best.ip),
                None,
            )
            if matched:
                _resolve_discovery_hostname(matched)
            step("命中目标，正在汇总结果…")
            _refresh_discovery_online(report.discoveries, timeout_ms=300)
            report.attempts = attempts
            return report

    step("扫描完成，正在汇总结果…")
    _refresh_discovery_online(report.discoveries, timeout_ms=300)
    report.attempts = attempts
    return report
