@echo off
rem Start the Lunar Base web app - layout-agnostic.
rem Works standalone and integrated; initializes automatically on first run /
rem when the venv, master data or grant shim is missing.
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem MANUAL BIND ADDRESS (optional)
rem Leave empty to auto-detect this PC's LAN IP. Fill in "host" or "host:port",
rem or leave empty and type the address at the prompt below:
rem   set "LUNAR_BASE_ADDR=192.168.1.100:8888"
rem   set "LUNAR_BASE_ADDR=127.0.0.1"          (this PC only)
rem This sets LUNAR_BASE_HOST / LUNAR_BASE_PORT for the panel; a value saved
rem on the Settings page (data\settings.json) still takes precedence.
rem ---------------------------------------------------------------------------
set "LUNAR_BASE_ADDR="

set "ADDR_IN=%LUNAR_BASE_ADDR%"
if not "%ADDR_IN%"=="" goto :addr_parse
if not "%LUNAR_BASE_NO_PROMPT%"=="1" (
    echo.
    echo Panel bind address - Enter = auto-detect this PC's LAN IP.
    echo Examples: 192.168.1.100:8888  ^|   127.0.0.1   ^|   0.0.0.0:8888
    set /p "ADDR_IN=Address: "
)
:addr_parse
set "LUNAR_BASE_HOST="
set "LUNAR_BASE_PORT="
if "%ADDR_IN%"=="" goto :addr_done
set "LUNAR_BASE_HOST=%ADDR_IN%"
for /f "tokens=1,* delims=:" %%A in ("%ADDR_IN%") do (
    set "LUNAR_BASE_HOST=%%A"
    if not "%%B"=="" set "LUNAR_BASE_PORT=%%B"
)
:addr_done
if not defined LUNAR_BASE_PORT set "LUNAR_BASE_PORT=8888"

rem --- pre-start resource check: is the listen port already occupied? -------
rem Uses Get-NetTCPConnection (locale-independent, unlike localized netstat
rem states). OwningProcess list comes back comma-separated.
set "CHECK_PORT=%LUNAR_BASE_PORT%"
set "PORT_PIDS="
for /f "delims=" %%L in ('powershell -NoProfile -Command "((Get-NetTCPConnection -LocalPort %CHECK_PORT% -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique) -join ','"') do set "PORT_PIDS=%%L"
if not defined PORT_PIDS goto :port_free
set "PORT_PIDS=%PORT_PIDS:,= %"
echo.
echo Port %CHECK_PORT% is already in use by:
for %%P in (%PORT_PIDS%) do (
    for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %%P" /FO CSV /NH 2^>nul') do echo    PID %%P  %%~N
)
set "KILL_ANS="
if "%LUNAR_BASE_NO_PROMPT%"=="1" goto :port_busy
echo   y = kill them and continue   ^|   Enter/n = keep them and continue anyway
set /p "KILL_ANS=Kill these process(es)? [y/N]: "
if /i "%KILL_ANS%"=="y" (
    for %%P in (%PORT_PIDS%) do taskkill /F /PID %%P >nul 2>&1
    echo Killed. Port %CHECK_PORT% freed.
    timeout /t 1 /nobreak >nul
    goto :port_free
)
:port_busy
echo Port still busy -- the panel may fail to bind. Continuing anyway.
:port_free

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
