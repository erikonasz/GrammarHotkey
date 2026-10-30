@echo off
rem Builds dist\QuickFixAI.exe. Needs Python 3.12+ on PATH.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv || exit /b 1
.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt || exit /b 1
.venv\Scripts\python.exe -m pytest tests -q || exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name QuickFixAI --icon assets\icon.ico run.pyw || exit /b 1
echo Built dist\QuickFixAI.exe
