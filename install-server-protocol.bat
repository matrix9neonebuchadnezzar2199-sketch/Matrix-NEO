@echo off
REM 拡張の「サーバー起動」ボタン (matrixneo://) 用 — フォルダ移動後はこれを実行
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "BAT=%ROOT%\start_server_terminal.bat"
set "BAT=%BAT:\=\\%"

set "REG_FILE=%TEMP%\matrix-neo-protocol.reg"
(
echo Windows Registry Editor Version 5.00
echo.
echo [HKEY_CURRENT_USER\Software\Classes\matrixneo]
echo @="URL:MATRIX NEO Server"
echo "URL Protocol"=""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\matrixneo\shell\open\command]
echo @="cmd.exe /c start \"MATRIX NEO Server\" cmd.exe /k \"%BAT%\""
) > "%REG_FILE%"

reg import "%REG_FILE%"
del "%REG_FILE%" 2>nul
echo.
echo Registered matrixneo:// -^> %ROOT%\start_server_terminal.bat
echo.
pause
