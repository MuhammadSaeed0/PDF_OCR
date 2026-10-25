@echo off
rem ======================================================================
rem  PDF OCR - installer for Windows 10 / 11
rem  1. finds (or installs with winget) Python 3.11-3.13
rem  2. creates the virtual environment .venv and installs requirements.txt
rem  3. finds (or installs with winget) Tesseract OCR
rem  4. adds missing OCR language data (Arabic, English) to .\tessdata
rem  5. creates the source/destination folders and runs verify_install.py
rem  Safe to run again at any time.
rem ======================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title PDF OCR - installation

echo.
echo ==================== PDF OCR installation ====================
echo.

rem ---------------------------------------------------------------- Python
set "PYEXE="
call :find_python
if defined PYEXE goto :python_ok
echo Python 3.11 - 3.13 was not found.
call :need_winget || goto :manual_python
echo Installing Python 3.12 with winget...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call :find_python
if defined PYEXE goto :python_ok
:manual_python
echo.
echo Please install Python 3.12 64-bit from https://www.python.org/downloads/windows/
echo Tick "Add python.exe to PATH" in the installer, then run install.bat again.
goto :fail

:python_ok
echo Using Python: %PYEXE%

rem ---------------------------------------------------------------- virtual environment
if exist ".venv\Scripts\python.exe" goto :venv_ok
echo Creating the virtual environment .venv ...
"%PYEXE%" -m venv .venv
if errorlevel 1 goto :fail
:venv_ok
set "VPY=%~dp0.venv\Scripts\python.exe"

echo Installing Python packages (this can take a few minutes)...
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo pip could not install the packages. Check your internet connection and try again.
    goto :fail
)

rem ---------------------------------------------------------------- Tesseract
"%VPY%" tools\install_helper.py find tesseract >nul 2>nul
if not errorlevel 1 goto :tesseract_ok
echo Tesseract OCR was not found.
call :need_winget || goto :manual_tesseract
echo Installing Tesseract OCR (UB Mannheim build) with winget. Accept the Windows prompt if asked...
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
"%VPY%" tools\install_helper.py find tesseract >nul 2>nul
if not errorlevel 1 goto :tesseract_ok
:manual_tesseract
echo.
echo Please install Tesseract OCR 5 from https://github.com/UB-Mannheim/tesseract/wiki
echo In the installer, under "Additional language data", tick Arabic.
echo Then run install.bat again. See README.md, "Installing Tesseract".
goto :fail
:tesseract_ok
echo Tesseract found:
"%VPY%" tools\install_helper.py find tesseract

rem ---------------------------------------------------------------- language data
echo.
echo Checking OCR language data...
"%VPY%" tools\setup_tessdata.py
if errorlevel 1 (
    echo Language data is incomplete - see README.md, "OCR language data".
    goto :fail
)

rem ---------------------------------------------------------------- folders + verification
echo.
"%VPY%" tools\install_helper.py make-folders
echo.
"%VPY%" verify_install.py
if errorlevel 1 goto :fail

echo.
echo ==================== Installation complete ====================
echo  Start monitoring:   run.bat        (command line)
echo  Graphical window:   run_gui.bat
echo  Settings:           config.ini
echo.
pause
exit /b 0

:fail
echo.
echo ==================== Installation NOT complete ====================
echo Fix the problem shown above and run install.bat again.
pause
exit /b 1

rem ---------------------------------------------------------------- helpers
:need_winget
where winget >nul 2>nul
if errorlevel 1 (
    echo winget is not available on this PC, so this cannot be installed automatically.
    exit /b 1
)
exit /b 0

:find_python
for %%V in (3.12 3.11 3.13) do call :try_py_launcher %%V
if defined PYEXE exit /b 0
for %%D in ("%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python311" "%LOCALAPPDATA%\Programs\Python\Python313" "%ProgramFiles%\Python312" "%ProgramFiles%\Python311" "%ProgramFiles%\Python313") do (
    if not defined PYEXE if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
)
if defined PYEXE exit /b 0
rem "python" on PATH, if it is a real interpreter of a supported version (not the Store shortcut)
python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
exit /b 0

:try_py_launcher
if defined PYEXE exit /b 0
py -%1 -c "import sys" >nul 2>nul
if errorlevel 1 exit /b 0
for /f "delims=" %%P in ('py -%1 -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
exit /b 0
