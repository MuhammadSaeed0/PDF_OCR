@echo off
rem PDF OCR - open the graphical window (no console window stays open).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo PDF OCR is not installed yet. Run install.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py --gui
