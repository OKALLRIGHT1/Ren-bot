@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1
echo [Live2D-Suzu] 开始安装运行环境，首次可能需要几分钟。
echo.
call "%~dp0setup_env.cmd" --ensure --console
set "ERR=%ERRORLEVEL%"
echo.
if "%ERR%"=="0" (
  echo 环境已就绪。接下来可以双击「启动.bat」或「启动-无窗口.vbs」。
  echo 如尚未配置，请编辑 .env 填入 API Key。
) else (
  echo 安装失败。请查看 logs\setup_env.log。
  echo 需要 Python 3.10 或更高版本：https://www.python.org/downloads/
)
echo.
pause
exit /b %ERR%
