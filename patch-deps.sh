#!/usr/bin/env bash
# Install the dependencies used by the panel's /patch page (best effort).
# Lives in panel/: called by setup.sh and the /settings INITIALIZATION section,
# and can be run manually:  bash panel/patch-deps.sh
set -u

PANEL="$(cd "$(dirname "$0")" && pwd)"
# Resolve the venv interpreter for both POSIX and Windows layouts.
if [ -x "$PANEL/.venv/bin/python" ]; then
    VENV_PY="$PANEL/.venv/bin/python"
else
    VENV_PY="$PANEL/.venv/Scripts/python.exe"
fi

if [ ! -x "$VENV_PY" ]; then
    echo "Panel virtualenv missing -- running setup.sh first ..."
    exec bash "$PANEL/setup.sh"
fi

echo "[patch deps] Python: protobuf (needed for list.bin patching) ..."
"$VENV_PY" -m pip install --upgrade protobuf

# --- system tools (best effort, never fatal) --------------------------------

install_apt() {
    command -v apt-get >/dev/null 2>&1 || return 1
    SUDO=""
    if [ "$(id -u)" -ne 0 ]; then
        command -v sudo >/dev/null 2>&1 || return 1
        SUDO="sudo"
    fi
    $SUDO apt-get update -qq || true
    $SUDO apt-get install -y "$@"
}

need() { command -v "$1" >/dev/null 2>&1; }

if ! need java || ! need keytool; then
    echo "[patch deps] Java/JDK not found -- attempting install ..."
    install_apt default-jre-headless default-jdk-headless \
        || echo "[patch deps] Could not auto-install Java; install a JDK manually."
fi

if ! need zipalign || ! need apksigner; then
    echo "[patch deps] Android build-tools (zipalign/apksigner) not found -- attempting install ..."
    install_apt android-sdk-build-tools \
        || echo "[patch deps] Could not auto-install Android build-tools; install them manually."
fi

# --- apktool (jar; used for APK decode/rebuild) -----------------------------

APKTOOL_DIR="$PANEL/tools/apktool"
APKTOOL_JAR="$APKTOOL_DIR/apktool.jar"

if [ -f "$APKTOOL_JAR" ]; then
    echo "[patch deps] apktool already present: $APKTOOL_JAR"
else
    mkdir -p "$APKTOOL_DIR"
    echo "[patch deps] Downloading apktool ..."
    URL="$("$VENV_PY" - <<'PY'
import json
import urllib.request

try:
    req = urllib.request.Request(
        "https://api.github.com/repos/iBotPeaches/Apktool/releases/latest",
        headers={"User-Agent": "lunar-base-patch-deps"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=20))
    for asset in data.get("assets", []):
        if str(asset.get("name", "")).endswith(".jar"):
            print(asset["browser_download_url"])
            break
except Exception:
    pass
PY
)"
    [ -n "$URL" ] || URL="https://github.com/iBotPeaches/Apktool/releases/download/v2.11.1/apktool_2.11.1.jar"

    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$APKTOOL_JAR.part" "$URL" || true
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$APKTOOL_JAR.part" "$URL" || true
    else
        echo "[patch deps] curl/wget not found -- cannot download apktool."
        rm -f "$APKTOOL_JAR.part"
        exit 1
    fi

    # A jar is a zip: verify the magic bytes before accepting the download.
    if [ "$(head -c 2 "$APKTOOL_JAR.part" 2>/dev/null)" = "PK" ]; then
        mv -f "$APKTOOL_JAR.part" "$APKTOOL_JAR"
        echo "[patch deps] apktool installed: $APKTOOL_JAR"
    else
        rm -f "$APKTOOL_JAR.part"
        echo "[patch deps] apktool download failed (unexpected file content)."
        exit 1
    fi
fi

# --- summary ----------------------------------------------------------------

echo "[patch deps] Tool status:"
for tool in java keytool zipalign apksigner; do
    if command -v "$tool" >/dev/null 2>&1; then
        echo "  $tool: $(command -v "$tool")"
    else
        echo "  $tool: MISSING"
    fi
done
echo "  apktool.jar: $APKTOOL_JAR"
