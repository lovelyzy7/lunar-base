#!/usr/bin/env bash
# Start Lunar Base (Linux/macOS). Windows users: run run-lunar-base.bat instead.
set -u
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

# Bind address / port are resolved in Python (web/config.py): by default it
# auto-detects this PC's LAN IP so the app is reachable from other devices and
# is NOT served on 127.0.0.1. Override with LUNAR_BASE_HOST / LUNAR_BASE_PORT
# (e.g. LUNAR_BASE_HOST=127.0.0.1 for this-PC-only). See README.
# Extra args are forwarded to python -m web (e.g. --auth to require login).

export LUNAR_BASE_HOST=0.0.0.0

exec .venv/bin/python -m web "$@"
