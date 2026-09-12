@echo off
setlocal
cd /d "%~dp0"

echo.
echo === Lunar Base setup ===
echo.

if not exist .venv (
    echo Creating virtual environment in .venv ...
    py -m venv .venv
    if errorlevel 1 (
        echo.
        echo Failed to create virtual environment. Make sure Python 3.10+ is installed and accessible as "py".
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)

echo Installing / updating app dependencies ...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r web\requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency install failed. Check the messages above.
    exit /b 1
)

echo.
echo === Master data ===
echo.

if exist "data\masterdata\*.json" (
    echo Master data already dumped at data\masterdata\ -- skipping.
    goto :names_section
)

set "MD_SCRIPT=..\lunar-scripts\dump_masterdata.py"
set "MD_INPUT=..\lunar-tear\server\assets\release\20240404193219.bin.e"

if not exist "%MD_SCRIPT%" (
    echo Skipping master-data dump: lunar-scripts not found at ..\lunar-scripts\
    echo Stages 1+ need the dump. To dump later, see README.md and re-run setup.bat.
    goto :names_section
)

if not exist "%MD_INPUT%" (
    echo Skipping master-data dump: master data binary not found at:
    echo   %MD_INPUT%
    echo Populate ..\lunar-tear\server\assets\ first, then re-run setup.bat.
    goto :names_section
)

echo Installing master-data dump dependencies (one-time, into .venv) ...
python -m pip install pycryptodome msgpack lz4
if errorlevel 1 (
    echo.
    echo Failed to install dump dependencies. Setup will continue without master data.
    echo Stages 1+ may not work until you re-run setup.bat or dump manually.
    goto :names_section
)

echo.
echo Dumping master data to data\masterdata\ ...
pushd ..\lunar-scripts
python -X utf8 dump_masterdata.py --input "..\lunar-tear\server\assets\release\20240404193219.bin.e" --output "..\lunar-base\data\masterdata"
set "DUMP_RC=%errorlevel%"
popd

if not "%DUMP_RC%"=="0" (
    echo.
    echo Master data dump failed (exit code %DUMP_RC%). Setup will continue.
    echo Stages 1+ may not work until the dump succeeds.
)

:names_section
echo.
echo === Names extraction ===
echo.

if exist "data\names\*.json" (
    echo Names already extracted at data\names\ -- skipping.
    goto :shim_section
)

if not exist "data\masterdata\*.json" (
    echo Skipping names extraction: master data dump is missing or empty.
    echo Re-run setup.bat after the master-data dump succeeds.
    goto :shim_section
)

set "REVISIONS_DIR=..\lunar-tear\server\assets\revisions"
if not exist "%REVISIONS_DIR%\" (
    echo Skipping names extraction: lunar-tear revisions tree not found at:
    echo   %REVISIONS_DIR%
    echo Stage 1+ will fall back to raw IDs without display names.
    goto :shim_section
)

echo Extracting English names from text bundles ...
python tools\extract_names.py
if errorlevel 1 (
    echo.
    echo Names extraction failed. Setup will continue.
    echo Stages 1+ may show raw IDs instead of display names.
)

:shim_section
echo.
echo === Grant shim build ===
echo.

where go >nul 2>&1
if errorlevel 1 (
    echo Go is not on PATH. Skipping grant shim build.
    echo Stage 1+ needs Go ^(1.25+^). Install it and re-run setup.bat.
    goto :setup_done
)

if not exist "..\lunar-tear\server\go.mod" (
    echo Skipping shim build: lunar-tear/server not found at ..\lunar-tear\server\
    echo Re-run setup.bat once lunar-tear is in place.
    goto :setup_done
)

if not exist "tools\grant\src\main.go" (
    echo Skipping shim build: tools\grant\src\main.go missing.
    goto :setup_done
)

echo Copying shim sources into lunar-tear\server\cmd\lunar-base-grant\ ...
if not exist "..\lunar-tear\server\cmd\lunar-base-grant\" mkdir "..\lunar-tear\server\cmd\lunar-base-grant"
copy /Y "tools\grant\src\*.go" "..\lunar-tear\server\cmd\lunar-base-grant\" >nul
if errorlevel 1 (
    echo Failed to copy shim sources. Stage 1+ will not work.
    goto :setup_done
)

echo Building tools\grant\grant.exe ...
pushd ..\lunar-tear\server
go build -o "%~dp0tools\grant\grant.exe" .\cmd\lunar-base-grant\
set "BUILD_RC=%errorlevel%"
popd

if not "%BUILD_RC%"=="0" (
    echo.
    echo grant.exe build failed ^(exit code %BUILD_RC%^). Stage 1+ will not work.
    echo Check that lunar-tear\server compiles cleanly: cd to it and run "go build .\...".
    goto :setup_done
)
echo Built: tools\grant\grant.exe

:setup_done
echo.
echo Setup complete. Run run-lunar-base.bat to start the app.
endlocal
