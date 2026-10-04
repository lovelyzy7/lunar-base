#!/usr/bin/env bash
# Start the Lunar Base web app — layout-agnostic, safe to run from anywhere.
#
# Works standalone (this repo next to a game-server checkout) and integrated
# (this repo lives at <game-server>/panel). On first run, or whenever the
# venv / master data / grant shim is missing, it runs setup.sh automatically.
# Extra args are forwarded to python -m web (e.g. --auth).
set -u

PANEL="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$PANEL/web/app.py" ]; then
    echo "web/app.py not found next to this script ($PANEL)."
    exit 1
fi

NEED_SETUP=0
if [ ! -x "$PANEL/.venv/bin/python" ] && [ ! -x "$PANEL/.venv/Scripts/python.exe" ]; then
    NEED_SETUP=1
fi
if [ ! -x "$PANEL/tools/grant/grant" ] && [ ! -x "$PANEL/tools/grant/grant.exe" ]; then
    NEED_SETUP=1
fi
compgen -G "$PANEL/data/masterdata/*.json" >/dev/null || NEED_SETUP=1

if [ "$NEED_SETUP" -ne 0 ]; then
    echo "Lunar Base is not fully initialized -- running setup.sh first ..."
    bash "$PANEL/setup.sh" || exit 1
fi

# Bind address / port are resolved in Python (web/config.py) with the
# precedence: data/settings.json > LUNAR_BASE_HOST/PORT > auto-detected LAN IP.
cd "$PANEL"
if [ -x "$PANEL/.venv/bin/python" ]; then
    VENV_PY="$PANEL/.venv/bin/python"
else
    VENV_PY="$PANEL/.venv/Scripts/python.exe"
fi
exec "$VENV_PY" -m web "$@"
