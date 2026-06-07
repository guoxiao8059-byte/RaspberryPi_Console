#!/bin/bash
# 树莓派上一键部署后执行的安装脚本（由 Windows 端「工具 → 一键部署」调用）
set -euo pipefail

cd "$(dirname "$0")"
echo "==> 工作目录: $(pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未找到 python3，请先安装 Python 3。"
  exit 1
fi

if [ -f requirements.txt ]; then
  echo "==> 安装 Python 依赖 (requirements.txt)"
  python3 -m pip install --user -r requirements.txt || pip3 install --user -r requirements.txt
else
  echo "==> 未找到 requirements.txt，跳过 pip 安装"
fi

# 可选：同目录下 install.local.sh 放项目专属步骤（重启服务等）
if [ -f install.local.sh ]; then
  echo "==> 执行 install.local.sh"
  bash install.local.sh
fi

echo "==> install.sh 完成"
