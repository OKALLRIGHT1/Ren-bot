@echo off
setlocal
cd /d "%~dp0"
title Ren-bot

if exist "%~dp0ren-bot.exe" (
    start "" "%~dp0ren-bot.exe" %*
    exit /b 0
)

if exist "%~dp0Live2D-Suzu.exe" (
    start "" "%~dp0Live2D-Suzu.exe" %*
    exit /b 0
)

if exist "%~dp0launcher.py" (
    py -3 "%~dp0launcher.py" %*
    if errorlevel 1 python "%~dp0launcher.py" %*
    exit /b %ERRORLEVEL%
)

python "%~dp0boot.py" %*
exit /b %ERRORLEVEL%
