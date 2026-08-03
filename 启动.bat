@echo off
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set FLASK_ENV=development
set FLASK_DEBUG=1
call conda activate live2d-llm
python main.py
pause