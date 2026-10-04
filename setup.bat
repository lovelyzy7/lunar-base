@echo off
rem Lunar Base setup - layout-agnostic, safe to re-run.
rem
rem Works both standalone (this repo next to a game-server checkout) and
rem integrated (this repo lives at <game-server>\panel). The game-server
rem checkout is auto-detected; see --print-paths.
rem
rem Usage:
rem   setup.bat                 full setup
rem   setup.bat patch-deps      only the /patch dependencies
rem   setup.bat --print-paths   print the resolved paths and exit
setlocal
cd /d "%~dp0"
set "PANEL=%~dp0"
if "%PANEL:~-1%"=="\" set "PANEL=%PANEL:~0,-1%"

rem --- resolve the game-server checkout --------------------------------------
set "TEAR_DIR="
if exist "%PANEL%\..\server\go.mod" for %%I in ("%PANEL%\..") do set "TEAR_DIR=%%~fI"
if not defined TEAR_DIR if exist "%PANEL%\..\lunar-server\server\go.mod" for %%I in ("%PANEL%\..\lunar-server") do set "TEAR_DIR=%%~fI"
if not defined TEAR_DIR if exist "%PANEL%\..\lunar-tear\server\go.mod" for %%I in ("%PANEL%\..\lunar-tear") do set "TEAR_DIR=%%~fI"
if not defined TEAR_DIR if defined LUNAR_SERVER_DIR if exist "%LUNAR_SERVER_DIR%\server\go.mod" for %%I in ("%LUNAR_SERVER_DIR%") do set "TEAR_DIR=%%~fI"
if not defined TEAR_DIR for %%I in ("%PANEL%\..") do set "TEAR_DIR=%%~fI"
set "SERVER=%TEAR_DIR%\server"

if "%~1"=="--print-paths" (
    echo PANEL=%PANEL%
    echo TEAR_DIR=%TEAR_DIR%
    echo SERVER=%SERVER%
    exit /b 0
)

if /i "%~1"=="patch-deps" (
    if not exist "%PANEL%\.venv\Scripts\python.exe" (
        echo Virtual environment missing -- running full setup first ...
        call "%~f0"
        if errorlevel 1 exit /b 1
    )
    call :patch_deps
    exit /b %errorlevel%
)

if not exist "%PANEL%\web\app.py" (
    echo web\app.py not found next to this script.
    exit /b 1
)

echo.
echo === Lunar Base setup ===
echo.
echo Panel:  %PANEL%
echo Server: %SERVER%

if not exist "%PANEL%\.venv" (
    echo Creating virtual environment in .venv ...
    py -m venv "%PANEL%\.venv"
    if not exist "%PANEL%\.venv\Scripts\python.exe" python -m venv "%PANEL%\.venv"
    if not exist "%PANEL%\.venv\Scripts\python.exe" (
        echo Failed to create virtual environment. Make sure Python 3.10+ is installed.
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)
set "VENV_PY=%PANEL%\.venv\Scripts\python.exe"

echo Installing / updating app dependencies ...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%PANEL%\web\requirements.txt"
if errorlevel 1 (
    echo Dependency install failed. Check the messages above.
    exit /b 1
)

echo.
echo === Master data ===
echo.

if exist "%PANEL%\data\masterdata\*.json" (
    echo Master data already dumped at data\masterdata\ -- skipping.
    goto :names_section
)

set "MD_SCRIPT=%PANEL%\scripts\dump_masterdata.py"
set "MD_INPUT=%SERVER%\assets\release\20240404193219.bin.e"

if not exist "%MD_SCRIPT%" (
    echo Skipping master-data dump: %MD_SCRIPT% not found.
    echo Write features need the dump. Re-run setup.bat once scripts\ is restored.
    goto :names_section
)

if not exist "%MD_INPUT%" (
    echo Skipping master-data dump: master data binary not found at:
    echo   %MD_INPUT%
    echo Populate the game server's assets\release\ first, then re-run setup.bat.
    goto :names_section
)

echo Installing master-data dump dependencies (one-time, into .venv) ...
"%VENV_PY%" -m pip install pycryptodome msgpack lz4
if errorlevel 1 (
    echo Failed to install dump dependencies. Setup will continue without master data.
    goto :names_section
)

echo.
echo Dumping master data to data\masterdata\ ...
"%VENV_PY%" -X utf8 "%MD_SCRIPT%" --input "%MD_INPUT%" --output "%PANEL%\data\masterdata"
if errorlevel 1 (
    echo Master data dump failed. Setup will continue.
)

:names_section
echo.
echo === Names extraction ===
echo.

if exist "%PANEL%\data\names\*.json" (
    echo Names already extracted at data\names\ -- skipping.
    goto :shim_section
)

if not exist "%PANEL%\data\masterdata\*.json" (
    echo Skipping names extraction: master data dump is missing or empty.
    echo Re-run setup.bat after the master-data dump succeeds.
    goto :shim_section
)

set "REVISIONS_DIR=%SERVER%\assets\revisions"
if not exist "%REVISIONS_DIR%\" (
    echo Skipping names extraction: game-server revisions tree not found at:
    echo   %REVISIONS_DIR%
    echo The panel will fall back to raw IDs without display names.
    goto :shim_section
)

echo Extracting English names from text bundles ...
"%VENV_PY%" "%PANEL%\tools\extract_names.py" --revisions-dir "%REVISIONS_DIR%"
if errorlevel 1 (
    echo Names extraction failed. Setup will continue.
)

:shim_section
echo.
echo === Grant shim build ===
echo.

where go >nul 2>&1
if errorlevel 1 (
    echo Go is not on PATH. Skipping grant shim build.
    echo All write operations need Go ^(1.25+^). Install it and re-run setup.bat.
    goto :patch_deps_section
)

if not exist "%SERVER%\go.mod" (
    echo Skipping shim build: game server not found at %SERVER%
    echo Re-run setup.bat once the game-server checkout is in place.
    goto :patch_deps_section
)

if not exist "%PANEL%\tools\grant\src\main.go" (
    echo Skipping shim build: tools\grant\src\main.go missing.
    goto :patch_deps_section
)

echo Copying shim sources into server\cmd\lunar-base-grant\ ...
if not exist "%SERVER%\cmd\lunar-base-grant\" mkdir "%SERVER%\cmd\lunar-base-grant"
copy /Y "%PANEL%\tools\grant\src\*.go" "%SERVER%\cmd\lunar-base-grant\" >nul
if errorlevel 1 (
    echo Failed to copy shim sources. Write operations will not work.
    goto :patch_deps_section
)

echo Building tools\grant\grant.exe ...
pushd "%SERVER%"
go build -o "%PANEL%\tools\grant\grant.exe" .\cmd\lunar-base-grant\
set "BUILD_RC=%errorlevel%"
popd

if not "%BUILD_RC%"=="0" (
    echo grant.exe build failed ^(exit code %BUILD_RC%^). Write operations will not work.
    echo Check that the game server compiles cleanly: cd server ^&^& go build .\...
    goto :patch_deps_section
)
echo Built: tools\grant\grant.exe

:patch_deps_section
echo.
echo === Patch dependencies ===
echo.
call :patch_deps
if errorlevel 1 (
    echo Patch dependency install failed or was incomplete. Setup will continue.
    echo The /patch page will show which tools are missing; re-run setup.bat patch-deps later.
)

echo.
echo Setup complete. Start the panel with start.bat (or the repo-root launcher).
endlocal
exit /b 0

rem ---------------------------------------------------------------------------
:patch_deps
echo [patch deps] Python: protobuf ^(needed for list.bin patching^) ...
"%VENV_PY%" -m pip install --upgrade protobuf
if errorlevel 1 echo [patch deps] protobuf install failed - the /patch page will report it.

where java >nul 2>&1
if errorlevel 1 echo [patch deps] Java not found - install a JDK ^(keytool comes with it^).
where zipalign >nul 2>&1
if errorlevel 1 echo [patch deps] zipalign not found - install Android SDK build-tools.
where apksigner >nul 2>&1
if errorlevel 1 echo [patch deps] apksigner not found - install Android SDK build-tools.

if not exist "%PANEL%\tools\apktool\apktool.jar" (
    if not exist "%PANEL%\tools\apktool" mkdir "%PANEL%\tools\apktool"
    echo [patch deps] Downloading apktool ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $r = Invoke-RestMethod -Headers @{ 'User-Agent'='lunar-base-patch-deps' } -Uri 'https://api.github.com/repos/iBotPeaches/Apktool/releases/latest'; $a = $r.assets | Where-Object { $_.name -like '*.jar' } | Select-Object -First 1; if (-not $a) { throw 'no jar asset' }; $u = $a.browser_download_url } catch { $u = 'https://github.com/iBotPeaches/Apktool/releases/download/v2.11.1/apktool_2.11.1.jar' }; Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile '%PANEL%\tools\apktool\apktool.jar.part'; if ((Get-Item '%PANEL%\tools\apktool\apktool.jar.part').Length -gt 100000) { Move-Item -Force '%PANEL%\tools\apktool\apktool.jar.part' '%PANEL%\tools\apktool\apktool.jar' } else { Remove-Item -Force '%PANEL%\tools\apktool\apktool.jar.part'; throw 'download too small' }"
    if errorlevel 1 echo [patch deps] apktool download failed.
)

echo [patch deps] Tool status:
for %%T in (java keytool zipalign apksigner) do (
    where %%T >nul 2>&1
    if errorlevel 1 (echo   %%T: MISSING) else (echo   %%T: found)
)
if exist "%PANEL%\tools\apktool\apktool.jar" (
    echo   apktool.jar: tools\apktool\apktool.jar
) else (
    echo   apktool.jar: MISSING
)
exit /b 0
