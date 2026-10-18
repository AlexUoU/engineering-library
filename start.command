#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 launcher.py
else
  echo "需要 Python 3.9 或更新版本。请从 https://www.python.org/downloads/ 安装。"
  read -r answer
fi
