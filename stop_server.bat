@echo off
REM 6850 で LISTEN しているプロセスを終了（MATRIX-NEO の古いインスタンス用）
setlocal
set "PORT=6850"
echo Stopping process listening on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    echo Killing PID %%a
    taskkill /PID %%a /F 2>nul
)
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 1) | Out-Null; Write-Host 'Port still in use' } catch { Write-Host 'Port %PORT% is free' }"
pause
