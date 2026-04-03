@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating venv...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements-build.txt

echo.
echo Running PyInstaller...
pyinstaller --noconfirm matrix-neo.spec

echo.
echo Done. Output: dist\MATRIX-NEO-Server\
echo Copy tools\, extension\, output\, temp\ next to the exe for distribution.
pause
