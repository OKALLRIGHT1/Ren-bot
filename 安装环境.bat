@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Ren-bot 环境安装向导

echo ========================================================
echo               Ren-bot 环境安装与检查向导
echo ========================================================
echo.
echo 正在检查并准备 Python 运行时环境，请稍候...
echo.

if exist "%~dp0setup_env.cmd" (
    call "%~dp0setup_env.cmd" --ensure --console
    set "ERR=%ERRORLEVEL%"
) else (
    py -3 "%~dp0setup_env.py" --ensure --console
    if errorlevel 1 python "%~dp0setup_env.py" --ensure --console
    set "ERR=%ERRORLEVEL%"
)

echo.
if "%ERR%"=="0" (
    echo [成功] Ren-bot 运行环境已准备就绪！
    echo 接下来您可以直接双击 "启动.bat" 或 "ren-bot.exe" 启动程序。
) else (
    echo [提示] 环境初始化遇到问题（退出码：%ERR%）。
    echo 1. 若尚未安装 Python，请访问 https://www.python.org/ 下载 Python 3.10 或 3.11。
    echo    安装时务必勾选 "Add python.exe to PATH"。
    echo 2. 您也可以查看 logs\setup_env.log 获取详细日志。
)
echo.
pause
exit /b %ERR%
