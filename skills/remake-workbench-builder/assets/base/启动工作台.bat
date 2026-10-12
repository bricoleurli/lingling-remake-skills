@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
  if errorlevel 1 goto missing
  py -3 server.py
  goto end
)
where python >nul 2>nul
if %errorlevel% neq 0 goto missing
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto missing
python server.py
goto end
:missing
echo 需要 Python 3.10 或以上。请让搭建 Agent 帮你配置，或从 python.org 安装。
:end
pause
