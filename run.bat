@echo off
rem PDF OCR - start monitoring in this console window. Press Ctrl+C to stop.
rem Extra options are passed on, e.g.:  run.bat --once   run.bat --retry-failed   run.bat --help
setlocal
cd /d "%~dp0"
title PDF OCR - monitoring
if not exist ".venv\Scripts\python.exe" (
    echo PDF OCR is not installed yet. Run install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
