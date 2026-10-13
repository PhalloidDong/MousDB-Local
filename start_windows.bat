@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 使用任意满足3.10以上的Python；不绑定作者安装位置或指定某个小版本。
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
  py -3 launcher.py
  exit /b
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
  python launcher.py
  exit /b
)
echo 请先安装Python 3.10以上，或请Codex定位可用的Python运行时。
pause
exit /b 2
