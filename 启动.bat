@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1
echo [Live2D-Suzu] 正在检查运行环境...
call "%~dp0setup_env.cmd" --ensure --console --start
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo 启动失败。可先双击「安装环境.bat」，或查看 logs\setup_env.log 与 logs\launcher.log。
  pause
)
exit /b %ERR%
