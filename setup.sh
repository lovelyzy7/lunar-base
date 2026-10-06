#!/usr/bin/env bash
# Lunar Base setup — layout-agnostic, safe to re-run.
#
# Works both standalone (this repo next to a game-server checkout) and
# integrated (this repo lives at <game-server>/panel). The game-server checkout
# is auto-detected; see --print-paths.
#
# Usage:
#   ./setup.sh                 full setup (venv, deps, master data, names, shim)
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
        setup_done
        return
    fi

    if [ ! -f "$SERVER/go.mod" ]; then
        echo "Skipping shim build: game server not found at $SERVER"
        echo "Re-run setup.sh once the game-server checkout is in place."
        setup_done
        return
    fi

    if [ ! -f "$PANEL/tools/grant/src/main.go" ]; then
        echo "Skipping shim build: tools/grant/src/main.go missing."
        setup_done
        return
    fi

    echo "Copying shim sources into server/cmd/lunar-base-grant/ ..."
    mkdir -p "$SERVER/cmd/lunar-base-grant"
    if ! cp -f "$PANEL"/tools/grant/src/*.go "$SERVER/cmd/lunar-base-grant/"; then
        echo "Failed to copy shim sources. Write operations will not work."
        setup_done
        return
    fi

    echo "Building tools/grant/grant ..."
    (cd "$SERVER" && go build -o "$PANEL/tools/grant/grant" ./cmd/lunar-base-grant/)
    BUILD_RC=$?

    if [ "$BUILD_RC" -ne 0 ]; then
        echo
        echo "grant build failed (exit code $BUILD_RC). Write operations will not work."
        echo "Check that the game server compiles cleanly: cd server && go build ./..."
        setup_done
        return
    fi
    echo "Built: tools/grant/grant"

    setup_done
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
