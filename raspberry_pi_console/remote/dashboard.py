# -*- coding: utf-8 -*-
from raspberry_pi_console.core.ssh_client import SshClient
from raspberry_pi_console.remote.common import format_command_result, parse_block

def dashboard_command() -> str:
        return r"""
HOST=$(hostname 2>/dev/null)
MODEL=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || awk -F': ' '/Model/{print $2; exit}' /proc/cpuinfo 2>/dev/null)
KERNEL=$(uname -r 2>/dev/null)
ARCH=$(uname -m 2>/dev/null)
UPTIME=$(uptime -p 2>/dev/null || echo "N/A")
LOAD=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)
IP=$(hostname -I 2>/dev/null | xargs)
CPU1=($(grep '^cpu ' /proc/stat))
IDLE1=${CPU1[4]}
TOTAL1=0
for VALUE in "${CPU1[@]:1}"; do
  TOTAL1=$((TOTAL1 + VALUE))
done
sleep 0.4
CPU2=($(grep '^cpu ' /proc/stat))
IDLE2=${CPU2[4]}
TOTAL2=0
for VALUE in "${CPU2[@]:1}"; do
  TOTAL2=$((TOTAL2 + VALUE))
done
DIFF_IDLE=$((IDLE2 - IDLE1))
DIFF_TOTAL=$((TOTAL2 - TOTAL1))
CPU_PERCENT=0
if [ "$DIFF_TOTAL" -gt 0 ]; then
  CPU_PERCENT=$(awk "BEGIN {printf \"%.0f\", (1 - $DIFF_IDLE / $DIFF_TOTAL) * 100}")
fi
MEM=$(free -m 2>/dev/null | awk '/Mem:/{print $3 "/" $2 " MB"}')
MEM_PERCENT=$(free -m 2>/dev/null | awk '/Mem:/{if ($2>0) printf "%.0f", ($3/$2)*100; else print "0"}')
SWAP=$(free -m 2>/dev/null | awk '/Swap:/{print $3 "/" $2 " MB"}')
SWAP_PERCENT=$(free -m 2>/dev/null | awk '/Swap:/{if ($2>0) printf "%.0f", ($3/$2)*100; else print "0"}')
DISK=$(df -h / 2>/dev/null | awk 'NR==2{print $3 "/" $2 " (" $5 ")"}')
DISK_PERCENT=$(df -h / 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
TEMP_RAW=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
TEMP="N/A"
TEMP_VALUE=0
if [ -n "$TEMP_RAW" ]; then
  TEMP=$(awk "BEGIN {printf \"%.1f C\", $TEMP_RAW/1000}")
  TEMP_VALUE=$(awk "BEGIN {printf \"%.0f\", $TEMP_RAW/1000}")
fi
TOP_CPU=$(ps -eo pid,comm,%cpu,%mem --no-headers --sort=-%cpu 2>/dev/null | head -n 12)
TOP_MEM=$(ps -eo pid,comm,%cpu,%mem --no-headers --sort=-%mem 2>/dev/null | head -n 12)
if [ -z "$TOP_CPU" ]; then
  TOP_CPU=$(ps -eo pid,comm,pcpu,pmem 2>/dev/null | sed '1d' | sort -k3,3nr | head -n 12)
fi
if [ -z "$TOP_MEM" ]; then
  TOP_MEM=$(ps -eo pid,comm,pcpu,pmem 2>/dev/null | sed '1d' | sort -k4,4nr | head -n 12)
fi
if [ -z "$TOP_CPU" ]; then
  TOP_CPU=$(ps aux 2>/dev/null | awk 'NR>1 {print $2, $11, $3, $4}' | sort -k3,3nr | head -n 12)
fi
if [ -z "$TOP_MEM" ]; then
  TOP_MEM=$(ps aux 2>/dev/null | awk 'NR>1 {print $2, $11, $3, $4}' | sort -k4,4nr | head -n 12)
fi
printf "__KV__\n"
printf "host=%s\n" "$HOST"
printf "model=%s\n" "$MODEL"
printf "kernel=%s\n" "$KERNEL"
printf "arch=%s\n" "$ARCH"
printf "uptime=%s\n" "$UPTIME"
printf "load=%s\n" "$LOAD"
printf "ip=%s\n" "$IP"
printf "cpu_percent=%s\n" "$CPU_PERCENT"
printf "mem=%s\n" "$MEM"
printf "mem_percent=%s\n" "$MEM_PERCENT"
printf "swap=%s\n" "$SWAP"
printf "swap_percent=%s\n" "$SWAP_PERCENT"
printf "disk=%s\n" "$DISK"
printf "disk_percent=%s\n" "$DISK_PERCENT"
printf "temp=%s\n" "$TEMP"
printf "temp_value=%s\n" "$TEMP_VALUE"
printf "__END_KV__\n"
printf "__TOP_CPU__\n%s\n__END_TOP_CPU__\n" "$TOP_CPU"
printf "__TOP_MEM__\n%s\n__END_TOP_MEM__\n" "$TOP_MEM"
"""
def parse_dashboard(output: str) -> dict:
        data = {}
        kv_block = parse_block(output, "__KV__\n", "__END_KV__")
        for line in kv_block.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        data["top_cpu"] = parse_process_rows(parse_block(output, "__TOP_CPU__\n", "__END_TOP_CPU__"))
        data["top_mem"] = parse_process_rows(parse_block(output, "__TOP_MEM__\n", "__END_TOP_MEM__"))
        return data
def parse_process_rows(block: str) -> list[dict]:
        rows = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(None, 3)
            if len(parts) < 4:
                continue
            rows.append(
                {
                    "pid": parts[0],
                    "command": parts[1],
                    "cpu": parts[2],
                    "mem": parts[3],
                }
            )
        return rows


def fetch_dashboard(ssh: SshClient) -> tuple[str, dict]:
    code, out, err = ssh.run(dashboard_command(), timeout=60)
    if code != 0:
        raise RuntimeError(format_command_result(code, out, err))
    data = parse_dashboard(out)
    return "运行信息已刷新。", data
