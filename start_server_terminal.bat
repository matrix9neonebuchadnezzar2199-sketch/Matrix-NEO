@echo off
title MATRIX NEO Server
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_server.py
) else (
    python run_server.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Server failed to start. Check Python / .venv / requirements.txt
    pause
)
