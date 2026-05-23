@echo off
setlocal EnableExtensions
set "HOME="
if exist "%LOCALAPPDATA%\matrix-neo\home.txt" set /p HOME=<"%LOCALAPPDATA%\matrix-neo\home.txt"
if not defined HOME (
    msg * "MATRIX-NEO: home.txt がありません。プロジェクト直下の install-server-protocol.bat を実行してください。"
    exit /b 1
)
if not exist "%HOME%\start_server_terminal.bat" (
    msg * "MATRIX-NEO: start_server_terminal.bat が見つかりません。フォルダ移動後は install-server-protocol.bat を再実行してください。^
%HOME%"
    exit /b 1
)
start "MATRIX NEO Server" cmd /k "%HOME%\start_server_terminal.bat"
exit /b 0
