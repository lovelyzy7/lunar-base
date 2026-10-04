#!/usr/bin/env bash
# Lunar Base setup — layout-agnostic, safe to re-run.
#
# Works both standalone (this repo next to a game-server checkout) and
# integrated (this repo lives at <game-server>/panel). The game-server checkout
# is auto-detected; see --print-paths.
#
# Usage:
#   ./setup.sh                 full setup (venv, deps, master data, names, shim, patch deps)
#   ./setup.sh patch-deps      only the /patch dependencies (protobuf, apktool, Java, build-tools)
#   ./setup.sh --print-paths   print the resolved paths and exit
#
# Windows: setup.bat (same options).
set -u

PANEL="$(cd "$(dirname "$0")" && pwd)"

# --- resolve the game-server checkout ---------------------------------------
# Priority: integrated (<checkout>/panel) > sibling lunar-server > sibling
# lunar-tear (legacy name) > $LUNAR_SERVER_DIR > integrated default.
resolve_tear_dir() {
    if [ -f "$PANEL/../server/go.mod" ]; then
        (cd "$PANEL/.." && pwd)
    elif [ -f "$PANEL/../lunar-server/server/go.mod" ]; then
        (cd "$PANEL/../lunar-server" && pwd)
    elif [ -f "$PANEL/../lunar-tear/server/go.mod" ]; then
        (cd "$PANEL/../lunar-tear" && pwd)
    elif [ -n "${LUNAR_SERVER_DIR:-}" ] && [ -f "${LUNAR_SERVER_DIR}/server/go.mod" ]; then
        (cd "$LUNAR_SERVER_DIR" && pwd)
    else
        (cd "$PANEL/.." && pwd)
    fi
}
TEAR_DIR="$(resolve_tear_dir)"
SERVER="$TEAR_DIR/server"

case "${1:-}" in
    --print-paths)
        echo "PANEL=$PANEL"
        echo "TEAR_DIR=$TEAR_DIR"
        echo "SERVER=$SERVER"
        exit 0
        ;;
esac

# --- venv interpreter (POSIX or Windows layout) -----------------------------
if [ -x "$PANEL/.venv/bin/python" ]; then
    VENV_PY="$PANEL/.venv/bin/python"
elif [ -x "$PANEL/.venv/Scripts/python.exe" ]; then
    VENV_PY="$PANEL/.venv/Scripts/python.exe"
else
    VENV_PY=""
fi

# --- /patch dependencies only ------------------------------------------------
patch_deps() {
    echo "[patch deps] Python: protobuf (needed for list.bin patching) ..."
    "$VENV_PY" -m pip install --upgrade protobuf

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
            return 1
        fi
        if [ "$(head -c 2 "$APKTOOL_JAR.part" 2>/dev/null)" = "PK" ]; then
            mv -f "$APKTOOL_JAR.part" "$APKTOOL_JAR"
            echo "[patch deps] apktool installed: $APKTOOL_JAR"
        else
            rm -f "$APKTOOL_JAR.part"
            echo "[patch deps] apktool download failed (unexpected file content)."
            return 1
        fi
    fi

    echo "[patch deps] Tool status:"
    for tool in java keytool zipalign apksigner; do
        if need "$tool"; then
            echo "  $tool: $(command -v "$tool")"
        else
            echo "  $tool: MISSING"
        fi
    done
    echo "  apktool.jar: $APKTOOL_JAR"
}

if [ "${1:-}" = "patch-deps" ]; then
    if [ ! -x "$VENV_PY" ]; then
        echo "Virtual environment missing -- running full setup first ..."
        exec bash "$PANEL/setup.sh"
    fi
    patch_deps
    exit $?
fi

# --- full setup ---------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo
    echo "Python 3.10+ not found. Install it and make sure 'python3' is on PATH."
    exit 1
fi

echo
echo "=== Lunar Base setup ==="
echo
echo "Panel:  $PANEL"
echo "Server: $SERVER"

if [ ! -d "$PANEL/.venv" ]; then
    echo "Creating virtual environment in .venv ..."
    if ! "$PY" -m venv "$PANEL/.venv"; then
        echo
        echo "Failed to create virtual environment. Make sure Python 3.10+ is installed."
        exit 1
    fi
else
    echo "Virtual environment already exists."
fi

if [ -x "$PANEL/.venv/bin/python" ]; then
    VENV_PY="$PANEL/.venv/bin/python"
elif [ -x "$PANEL/.venv/Scripts/python.exe" ]; then
    VENV_PY="$PANEL/.venv/Scripts/python.exe"
else
    echo
    echo "Virtual environment is broken (no interpreter under .venv)."
    echo "Delete .venv and re-run setup."
    exit 1
fi

echo "Installing / updating app dependencies ..."
"$VENV_PY" -m pip install --upgrade pip
if ! "$VENV_PY" -m pip install -r "$PANEL/web/requirements.txt"; then
    echo
    echo "Dependency install failed. Check the messages above."
    exit 1
fi

echo
echo "=== Master data ==="
echo

patch_deps_section() {
    echo
    echo "=== Patch dependencies ==="
    echo
    if ! patch_deps; then
        echo
        echo "Patch dependency install failed or was incomplete. Setup will continue."
        echo "The /patch page will show which tools are missing; re-run setup.sh patch-deps later."
    fi
    setup_done
}

names_section() {
    echo
    echo "=== Names extraction ==="
    echo

    if compgen -G "$PANEL/data/names/*.json" >/dev/null; then
        echo "Names already extracted at data/names/ -- skipping."
        shim_section
        return
    fi

    if ! compgen -G "$PANEL/data/masterdata/*.json" >/dev/null; then
        echo "Skipping names extraction: master data dump is missing or empty."
        echo "Re-run setup.sh after the master-data dump succeeds."
        shim_section
        return
    fi

    REVISIONS_DIR="$SERVER/assets/revisions"
    if [ ! -d "$REVISIONS_DIR" ]; then
        echo "Skipping names extraction: game-server revisions tree not found at:"
        echo "  $REVISIONS_DIR"
        echo "The panel will fall back to raw IDs without display names."
        shim_section
        return
    fi

    echo "Extracting English names from text bundles ..."
    if ! "$VENV_PY" "$PANEL/tools/extract_names.py" --revisions-dir "$REVISIONS_DIR"; then
        echo
        echo "Names extraction failed. Setup will continue."
        echo "The panel may show raw IDs instead of display names."
    fi

    shim_section
}

shim_section() {
    echo
    echo "=== Grant shim build ==="
    echo

    if ! command -v go >/dev/null 2>&1; then
        echo "Go is not on PATH. Skipping grant shim build."
        echo "All write operations need Go (1.25+). Install it and re-run setup.sh."
        patch_deps_section
        return
    fi

    if [ ! -f "$SERVER/go.mod" ]; then
        echo "Skipping shim build: game server not found at $SERVER"
        echo "Re-run setup.sh once the game-server checkout is in place."
        patch_deps_section
        return
    fi

    if [ ! -f "$PANEL/tools/grant/src/main.go" ]; then
        echo "Skipping shim build: tools/grant/src/main.go missing."
        patch_deps_section
        return
    fi

    echo "Copying shim sources into server/cmd/lunar-base-grant/ ..."
    mkdir -p "$SERVER/cmd/lunar-base-grant"
    if ! cp -f "$PANEL"/tools/grant/src/*.go "$SERVER/cmd/lunar-base-grant/"; then
        echo "Failed to copy shim sources. Write operations will not work."
        patch_deps_section
        return
    fi

    echo "Building tools/grant/grant ..."
    (cd "$SERVER" && go build -o "$PANEL/tools/grant/grant" ./cmd/lunar-base-grant/)
    BUILD_RC=$?

    if [ "$BUILD_RC" -ne 0 ]; then
        echo
        echo "grant build failed (exit code $BUILD_RC). Write operations will not work."
        echo "Check that the game server compiles cleanly: cd server && go build ./..."
        patch_deps_section
        return
    fi
    echo "Built: tools/grant/grant"

    patch_deps_section
}

setup_done() {
    echo
    echo "Setup complete. Start the panel with ./start.sh (or the repo-root launcher)."
}

# --- Master-data dump ---

if compgen -G "$PANEL/data/masterdata/*.json" >/dev/null; then
    echo "Master data already dumped at data/masterdata/ -- skipping."
    names_section
    exit 0
fi

MD_SCRIPT="$PANEL/scripts/dump_masterdata.py"
MD_INPUT="$SERVER/assets/release/20240404193219.bin.e"

if [ ! -f "$MD_SCRIPT" ]; then
    echo "Skipping master-data dump: $MD_SCRIPT not found."
    echo "Write features need the dump. Re-run setup.sh once scripts/ is restored."
    names_section
    exit 0
fi

if [ ! -f "$MD_INPUT" ]; then
    echo "Skipping master-data dump: master data binary not found at:"
    echo "  $MD_INPUT"
    echo "Populate the game server's assets/release/ first, then re-run setup.sh."
    names_section
    exit 0
fi

echo "Installing master-data dump dependencies (one-time, into .venv) ..."
if ! "$VENV_PY" -m pip install pycryptodome msgpack lz4; then
    echo
    echo "Failed to install dump dependencies. Setup will continue without master data."
    echo "Write features may not work until you re-run setup.sh or dump manually."
    names_section
    exit 0
fi

echo
echo "Dumping master data to data/masterdata/ ..."
"$VENV_PY" -X utf8 "$MD_SCRIPT" \
    --input "$MD_INPUT" \
    --output "$PANEL/data/masterdata"
DUMP_RC=$?

if [ "$DUMP_RC" -ne 0 ]; then
    echo
    echo "Master data dump failed (exit code $DUMP_RC). Setup will continue."
    echo "Write features may not work until the dump succeeds."
fi

names_section
