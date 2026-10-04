@echo off
rem Start the Lunar Base web app - layout-agnostic.
rem Works standalone and integrated; initializes automatically on first run /
rem when the venv, master data or grant shim is missing.
setlocal
cd /d "%~dp0"

if not exist "web\app.py" (
    echo web\app.py not found next to this script.
    exit /b 1
)

set "NEED_SETUP=0"
if not exist ".venv\Scripts\python.exe" set "NEED_SETUP=1"
if not exist "tools\grant\grant.exe" set "NEED_SETUP=1"
if not exist "data\masterdata\*.json" set "NEED_SETUP=1"
if "%NEED_SETUP%"=="1" (
    echo Lunar Base is not fully initialized -- running setup.bat first ...
    call "setup.bat"
    if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m web %*
endlocal
