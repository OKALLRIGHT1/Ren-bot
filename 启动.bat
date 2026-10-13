@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1
echo [Live2D-Suzu] 正在启动本仓库后端 GUI…
if exist "%~dp0launcher.py" (
  py -3 "%~dp0launcher.py" %*
  if errorlevel 1 python "%~dp0launcher.py" %*
) else (
  python "%~dp0boot.py" %*
)
exit /b %ERRORLEVEL%
