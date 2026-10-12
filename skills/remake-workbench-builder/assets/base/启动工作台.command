#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo '需要 Python 3.10 或以上。请让搭建 Agent 帮你配置，或从 python.org 安装。'
  read -r -p '按回车关闭…'
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo '请升级到 Python 3.10 或以上。'; exit 1; }
python3 server.py
