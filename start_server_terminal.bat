@echo off
title MATRIX NEO Server
cd /d "%~dp0"
set "PORT=6850"
set "HEALTH=http://127.0.0.1:%PORT%/health"

REM 既に起動済みなら二重起動しない（WinError 10048 防止）
powershell -NoProfile -Command ^
  "try { $r = Invoke-WebRequest -Uri '%HEALTH%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if %errorlevel%==0 (
    echo.
    echo [OK] MATRIX-NEO server is already running on %HEALTH%
    echo      Close the other server window first if you need to restart.
    echo.
    pause
    exit /b 0
)

echo Starting MATRIX-NEO on http://127.0.0.1:%PORT% ...
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_server.py
) else (
    python run_server.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Server failed to start.
    echo   - Port %PORT% may be in use. Run:  netstat -ano ^| findstr :%PORT%
    echo   - Or stop the old python window and try again.
    echo.
    pause
)
