#!/bin/sh
# 输入：Finder双击或Terminal执行；输出：后台服务及默认浏览器首页。
# 数据位置由runtime_paths.py决定，代码更新不替换数据库。
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "请先安装Python 3.10以上，再运行本文件。"
  exit 2
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || exit 2
exec python3 launcher.py
