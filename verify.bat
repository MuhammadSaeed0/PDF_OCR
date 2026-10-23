@echo off
rem PDF OCR - check the installation and run a real OCR test.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo PDF OCR is not installed yet. Run install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" verify_install.py %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
