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
rem states). A running Lunar Base panel (its own python running "-m web") is
rem replaced automatically; other processes are listed and may be killed after
rem confirmation. If the port is still busy we abort with a clear message
rem instead of letting python -m web die on a bind error.
set "CHECK_PORT=%LUNAR_BASE_PORT%"
set "OWN_PIDS="
set "OTHER_PIDS="
rem PowerShell writes its verdict to a temp file: running it inside `for /f`
rem made cmd's quote parser choke on the nested single quotes. The PID lists
rem come back space-separated (no %VAR:,= % substitution, which cmd expands
rem before an `if defined` block is even evaluated).
set "LB_PORT_TMP=%TEMP%\lunar-base-port-check.txt"
powershell -NoProfile -Command "$p=Get-NetTCPConnection -LocalPort %CHECK_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if($p){$own=@();$other=@();foreach($id in $p){$c=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id) -ErrorAction SilentlyContinue).CommandLine; if($c -and ($c -match '-m web' -or $c -match 'uvicorn')){$own+=$id}else{$other+=$id}}; if($own){'OWN ' + ($own -join [char]32)}; if($other){'OTHER ' + ($other -join [char]32)}}" > "%LB_PORT_TMP%" 2>nul
if exist "%LB_PORT_TMP%" (
    for /f "tokens=1,*" %%A in ('type "%LB_PORT_TMP%"') do (
        if /i "%%A"=="OWN" set "OWN_PIDS=%%B"
        if /i "%%A"=="OTHER" set "OTHER_PIDS=%%B"
    )
    del "%LB_PORT_TMP%" >nul 2>&1
)
if not defined OWN_PIDS goto :no_own
rem Replace our own panel: running start again is meant to restart it.
echo Replacing the running Lunar Base panel ^(PID %OWN_PIDS%^) ...
for %%P in (%OWN_PIDS%) do taskkill /F /PID %%P >nul 2>&1
timeout /t 1 /nobreak >nul
:no_own
if not defined OTHER_PIDS goto :port_free
echo.
echo Port %CHECK_PORT% is already in use by:
for %%P in (%OTHER_PIDS%) do (
    for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %%P" /FO CSV /NH 2^>nul') do echo    PID %%P  %%~N
)
set "KILL_ANS="
if "%LUNAR_BASE_NO_PROMPT%"=="1" goto :port_busy
echo   y = kill them and continue   ^|   Enter/n = keep them and continue anyway
set /p "KILL_ANS=Kill these process(es)? [y/N]: "
if /i not "%KILL_ANS%"=="y" goto :port_busy
for %%P in (%OTHER_PIDS%) do taskkill /F /PID %%P >nul 2>&1
echo Killed. Port %CHECK_PORT% freed.
timeout /t 1 /nobreak >nul
goto :port_free
:port_busy
set "STILL_PIDS="
for /f "delims=" %%L in ('powershell -NoProfile -Command "((Get-NetTCPConnection -LocalPort %CHECK_PORT% -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique) -join ','"') do set "STILL_PIDS=%%L"
if defined STILL_PIDS (
    echo.
    echo ERROR: port %CHECK_PORT% is still busy ^(PID %STILL_PIDS%^).
    echo Close that process ^(or answer y next time^) and run start again.
    exit /b 1
)
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
