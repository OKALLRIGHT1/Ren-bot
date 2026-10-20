@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "SETUP_PY=%~dp0setup_env.py"
if not exist "%SETUP_PY%" (
    echo [Ren-bot] 找不到 setup_env.py
    exit /b 1
)

if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if errorlevel 1 goto :try_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :try_python
py -3 "%SETUP_PY%" %*
exit /b %ERRORLEVEL%

:try_python
where python >nul 2>&1
if errorlevel 1 goto :try_local
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :try_local
python "%SETUP_PY%" %*
exit /b %ERRORLEVEL%

:try_local
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)

echo [Ren-bot] 未检测到 Python 3.10+，尝试通过 winget 安装 Python 3.12...
where winget >nul 2>&1
if errorlevel 1 goto :no_python

winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if not errorlevel 1 (
    python "%SETUP_PY%" %*
    exit /b %ERRORLEVEL%
)

echo [Ren-bot] Python 已安装但当前窗口环境变量未刷新。请重新双击「安装环境.bat」。
echo 若仍无法使用，请前往 https://www.python.org/downloads/ 安装 Python 3.10+，并务必勾选 "Add python.exe to PATH"。
exit /b 1

:no_python
echo [Ren-bot] 系统未安装 winget。请前往 https://www.python.org/downloads/ 下载并安装 Python 3.10 或更高版本。
echo 安装时务必勾选 "Add python.exe to PATH"。
exit /b 1
