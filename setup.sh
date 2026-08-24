#!/usr/bin/env bash
# Lunar Base setup (Linux/macOS). Windows users: run setup.bat instead.
set -u
cd "$(dirname "$0")"

# Pick a Python launcher: prefer python3, fall back to python.
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

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    if ! "$PY" -m venv .venv; then
        echo
        echo "Failed to create virtual environment. Make sure Python 3.10+ is installed."
        exit 1
    fi
else
    echo "Virtual environment already exists."
fi

# Use the venv's interpreter directly (absolute path) rather than 'activate' so
# it keeps working after we cd into sibling repos and across any shell.
VENV_PY="$(pwd)/.venv/bin/python"

echo "Installing / updating app dependencies ..."
"$VENV_PY" -m pip install --upgrade pip
if ! "$VENV_PY" -m pip install -r web/requirements.txt; then
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

if compgen -G "data/names/*.json" >/dev/null; then
    echo "Names already extracted at data/names/ -- skipping."
    shim_section
    return
fi

if ! compgen -G "data/masterdata/*.json" >/dev/null; then
    echo "Skipping names extraction: master data dump is missing or empty."
    echo "Re-run setup.sh after the master-data dump succeeds."
    shim_section
    return
fi

REVISIONS_DIR="../lunar-tear/server/assets/revisions"
if [ ! -d "$REVISIONS_DIR" ]; then
    echo "Skipping names extraction: lunar-tear revisions tree not found at:"
    echo "  $REVISIONS_DIR"
    echo "Stage 1+ will fall back to raw IDs without display names."
    shim_section
    return
fi

echo "Extracting English names from text bundles ..."
if ! "$VENV_PY" tools/extract_names.py; then
    echo
    echo "Names extraction failed. Setup will continue."
    echo "Stages 1+ may show raw IDs instead of display names."
fi

shim_section
}

shim_section() {

echo
echo "=== Grant shim build ==="
echo

if ! command -v go >/dev/null 2>&1; then
    echo "Go is not on PATH. Skipping grant shim build."
    echo "Stage 1+ needs Go (1.25+). Install it and re-run setup.sh."
    setup_done
    return
fi

if [ ! -f "../lunar-tear/server/go.mod" ]; then
    echo "Skipping shim build: lunar-tear/server not found at ../lunar-tear/server/"
    echo "Re-run setup.sh once lunar-tear is in place."
    setup_done
    return
fi

if [ ! -f "tools/grant/src/main.go" ]; then
    echo "Skipping shim build: tools/grant/src/main.go missing."
    setup_done
    return
fi

echo "Copying shim sources into lunar-tear/server/cmd/lunar-base-grant/ ..."
mkdir -p "../lunar-tear/server/cmd/lunar-base-grant"
if ! cp -f tools/grant/src/*.go "../lunar-tear/server/cmd/lunar-base-grant/"; then
    echo "Failed to copy shim sources. Stage 1+ will not work."
    setup_done
    return
fi

# Absolute output path so 'go build' (run from lunar-tear/server) writes back here.
OUT="$(pwd)/tools/grant/grant"
echo "Building tools/grant/grant ..."
(cd "../lunar-tear/server" && go build -o "$OUT" ./cmd/lunar-base-grant/)
BUILD_RC=$?

if [ "$BUILD_RC" -ne 0 ]; then
    echo
    echo "grant build failed (exit code $BUILD_RC). Stage 1+ will not work."
    echo "Check that lunar-tear/server compiles cleanly: cd to it and run 'go build ./...'."
    setup_done
    return
fi
echo "Built: tools/grant/grant"

setup_done
}

setup_done() {
echo
echo "Setup complete. Run ./run-lunar-base.sh to start the app."
}

# --- Master-data dump ---

if compgen -G "data/masterdata/*.json" >/dev/null; then
    echo "Master data already dumped at data/masterdata/ -- skipping."
    names_section
    exit 0
fi

MD_SCRIPT="../lunar-scripts/dump_masterdata.py"
MD_INPUT="../lunar-tear/server/assets/release/20240404193219.bin.e"

if [ ! -f "$MD_SCRIPT" ]; then
    echo "Skipping master-data dump: lunar-scripts not found at ../lunar-scripts/"
    echo "Stages 1+ need the dump. To dump later, see README.md and re-run setup.sh."
    names_section
    exit 0
fi

if [ ! -f "$MD_INPUT" ]; then
    echo "Skipping master-data dump: master data binary not found at:"
    echo "  $MD_INPUT"
    echo "Populate ../lunar-tear/server/assets/ first, then re-run setup.sh."
    names_section
    exit 0
fi

echo "Installing master-data dump dependencies (one-time, into .venv) ..."
if ! "$VENV_PY" -m pip install pycryptodome msgpack lz4; then
    echo
    echo "Failed to install dump dependencies. Setup will continue without master data."
    echo "Stages 1+ may not work until you re-run setup.sh or dump manually."
    names_section
    exit 0
fi

echo
echo "Dumping master data to data/masterdata/ ..."
(cd "../lunar-scripts" && "$VENV_PY" dump_masterdata.py \
    --input "../lunar-tear/server/assets/release/20240404193219.bin.e" \
    --output "../lunar-base/data/masterdata")
DUMP_RC=$?

if [ "$DUMP_RC" -ne 0 ]; then
    echo
    echo "Master data dump failed (exit code $DUMP_RC). Setup will continue."
    echo "Stages 1+ may not work until the dump succeeds."
fi

names_section
