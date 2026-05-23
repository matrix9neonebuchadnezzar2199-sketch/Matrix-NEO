@echo off
REM 拡張の「サーバー起動」ボタン (matrixneo://) 用 — フォルダ移動後はこれを再実行
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "LAUNCHER_DIR=%LOCALAPPDATA%\matrix-neo"
set "LAUNCHER=%LAUNCHER_DIR%\launch-server.cmd"

if not exist "%LAUNCHER_DIR%" mkdir "%LAUNCHER_DIR%"
copy /Y "%~dp0tools\launch-server.cmd" "%LAUNCHER%" >nul
if errorlevel 1 (
    echo Failed to copy launch-server.cmd to "%LAUNCHER%"
    exit /b 1
)
> "%LAUNCHER_DIR%\home.txt" echo %ROOT%

set "LAUNCHER_REG=%LAUNCHER:\=\\%"
set "REG_FILE=%TEMP%\matrix-neo-protocol.reg"
(
echo Windows Registry Editor Version 5.00
echo.
echo [HKEY_CURRENT_USER\Software\Classes\matrixneo]
echo @="URL:MATRIX NEO Server"
echo "URL Protocol"=""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\matrixneo\shell\open\command]
echo @="\"%LAUNCHER_REG%\" \"%%1\""
) > "%REG_FILE%"

reg import "%REG_FILE%"
del "%REG_FILE%" 2>nul
echo.
echo OK: matrixneo:// -^> %LAUNCHER%
echo     project home: %ROOT%
echo.
if /i not "%~1"=="/silent" pause
