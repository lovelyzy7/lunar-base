@echo off
rem Install the dependencies used by the panel's /patch page (best effort).
rem Lives in panel\: called by setup.bat and the /settings INITIALIZATION
rem section, and can be run manually.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Panel virtualenv missing -- running setup.bat first ...
    call "setup.bat"
    if errorlevel 1 exit /b 1
)

set "PY=.venv\Scripts\python.exe"

echo [patch deps] Python: protobuf ^(needed for list.bin patching^) ...
"%PY%" -m pip install --upgrade protobuf
if errorlevel 1 echo [patch deps] protobuf install failed - the /patch page will report it.

where java >nul 2>&1
if errorlevel 1 echo [patch deps] Java not found - install a JDK ^(keytool comes with it^).
where zipalign >nul 2>&1
if errorlevel 1 echo [patch deps] zipalign not found - install Android SDK build-tools.
where apksigner >nul 2>&1
if errorlevel 1 echo [patch deps] apksigner not found - install Android SDK build-tools.

if not exist "tools\apktool\apktool.jar" (
    if not exist "tools\apktool" mkdir "tools\apktool"
    echo [patch deps] Downloading apktool ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $r = Invoke-RestMethod -Headers @{ 'User-Agent'='lunar-base-patch-deps' } -Uri 'https://api.github.com/repos/iBotPeaches/Apktool/releases/latest'; $a = $r.assets | Where-Object { $_.name -like '*.jar' } | Select-Object -First 1; if (-not $a) { throw 'no jar asset' }; $u = $a.browser_download_url } catch { $u = 'https://github.com/iBotPeaches/Apktool/releases/download/v2.11.1/apktool_2.11.1.jar' }; Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile 'tools\apktool\apktool.jar.part'; if ((Get-Item 'tools\apktool\apktool.jar.part').Length -gt 100000) { Move-Item -Force 'tools\apktool\apktool.jar.part' 'tools\apktool\apktool.jar' } else { Remove-Item -Force 'tools\apktool\apktool.jar.part'; throw 'download too small' }"
    if errorlevel 1 echo [patch deps] apktool download failed.
)

echo [patch deps] Tool status:
for %%T in (java keytool zipalign apksigner) do (
    where %%T >nul 2>&1
    if errorlevel 1 (echo   %%T: MISSING) else (echo   %%T: found)
)
if exist "tools\apktool\apktool.jar" (
    echo   apktool.jar: tools\apktool\apktool.jar
) else (
    echo   apktool.jar: MISSING
)
endlocal
