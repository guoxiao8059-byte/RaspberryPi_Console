# -*- coding: utf-8 -*-
APP_ORG = "MagicCube"
APP_NAME = "RaspberryPiRemoteConsole"
MULTI_PATH_SEPARATOR = " | "

NETWORK_MODE_AUTO = "auto"  # 旧配置兼容，UI 已不再提供
NETWORK_MODE_WIRED = "wired"
NETWORK_MODE_WIRELESS = "wireless"
NETWORK_MODES = (NETWORK_MODE_WIRED, NETWORK_MODE_WIRELESS)
NETWORK_MODE_LABELS = {
    NETWORK_MODE_WIRED: "有线网",
    NETWORK_MODE_WIRELESS: "无线网",
}


def normalize_network_mode(mode: str | None) -> str:
    value = str(mode or NETWORK_MODE_WIRED).strip().lower()
    if value == NETWORK_MODE_AUTO:
        return NETWORK_MODE_WIRED
    if value in NETWORK_MODES:
        return value
    return NETWORK_MODE_WIRED

HOST_SOURCE_MANUAL = "配置 IP"
HOST_SOURCE_MAC_SCAN = "MAC 扫描"
HOST_SOURCE_REMOTE_WIFI = "远程启用无线"
HOST_SOURCES = (HOST_SOURCE_MANUAL, HOST_SOURCE_MAC_SCAN, HOST_SOURCE_REMOTE_WIFI)
