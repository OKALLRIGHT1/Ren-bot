@echo off
setlocal
cd /d "%~dp0"
title Ren-bot 环境安装

echo ========================================================
echo               Ren-bot 运行环境安装向导
echo ========================================================
echo.
echo 正在检查并配置运行环境，首次安装可能需要几分钟，请耐心等待...
echo.

call "%~dp0setup_env.cmd" --ensure --console
set "ERR=%ERRORLEVEL%"
echo.
if "%ERR%"=="0" (
    echo [成功] Ren-bot 运行环境已就绪！
    echo 接下来您可以直接双击 "ren-bot.exe" 或 "启动.bat" 启动程序。
) else (
    echo [失败] 环境安装遇到问题，退出码：%ERR%
    echo 请查看日志文件：logs\setup_env.log
    echo 提示：若未安装 Python，请前往 https://www.python.org/ 下载安装 Python 3.10+，并勾选 "Add python.exe to PATH"。
)
echo.
pause
exit /b %ERR%
