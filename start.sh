#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 server.py
else
  echo "需要 Python 3.9 或更新版本。请通过系统软件包管理器安装 Python 3。"
  exit 1
fi
