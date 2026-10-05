#!/usr/bin/env bash
# Start the Lunar Base web app — layout-agnostic, safe to run from anywhere.
#
# Works standalone (this repo next to a game-server checkout) and integrated
# (this repo lives at <game-server>/panel). On first run, or whenever the
# venv / master data / grant shim is missing, it runs setup.sh automatically.
# Extra args are forwarded to python -m web (e.g. --auth).
set -u

PANEL="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# MANUAL BIND ADDRESS (optional)
# Leave empty to auto-detect this machine's LAN IP; you can also type the
# address at the prompt below. Or hard-code it here:
#   LUNAR_BASE_ADDR="192.168.2.6:8888"
#   LUNAR_BASE_ADDR="127.0.0.1"          # this machine only
# This exports LUNAR_BASE_HOST / LUNAR_BASE_PORT for the panel; a value saved
# on the Settings page (data/settings.json) still takes precedence.
# ---------------------------------------------------------------------------
LUNAR_BASE_ADDR=""
ADDR_IN="$LUNAR_BASE_ADDR"
if [ -z "$ADDR_IN" ] && [ -t 0 ] && [ "${LUNAR_BASE_NO_PROMPT:-0}" != "1" ]; then
    printf "Panel bind address - Enter = auto-detect this machine's LAN IP.\n"
    printf "Examples: 192.168.2.6:8888 | 127.0.0.1 | 0.0.0.0:8888\n"
    printf "Address: "
    read -r ADDR_IN || ADDR_IN=""
fi
if [ -n "$ADDR_IN" ]; then
    export LUNAR_BASE_HOST="${ADDR_IN%%:*}"
    if [ "${ADDR_IN#*:}" != "$ADDR_IN" ]; then
        export LUNAR_BASE_PORT="${ADDR_IN##*:}"
    fi
fi

# --- pre-start resource check: is the listen port already occupied? ---------
PORT="${LUNAR_BASE_PORT:-8888}"
port_pids() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | awk -v p=":${PORT}$" '$4 ~ p {print $6}' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p'
    elif command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltnp 2>/dev/null | awk -v p=":${PORT}$" '$4 ~ p {print $7}' | sed 's#.*/##'
    fi
}
PIDS="$(port_pids 2>/dev/null | sort -u | tr '\n' ' ')"
if [ -n "${PIDS// /}" ]; then
    echo "Port ${PORT} is already in use by:"
    for pid in $PIDS; do
        ps -p "$pid" -o pid=,comm=,args= 2>/dev/null | sed 's/^/  /' || echo "  PID ${pid}"
    done
    KILL_ANS=""
    if [ -t 0 ] && [ "${LUNAR_BASE_NO_PROMPT:-0}" != "1" ]; then
        printf "  y = kill them and continue | Enter/n = keep them and continue anyway\n"
        printf "Kill these process(es)? [y/N] "
        read -r KILL_ANS || KILL_ANS=""
    fi
    case "$KILL_ANS" in
        y|Y|yes|YES)
            for pid in $PIDS; do kill -9 "$pid" 2>/dev/null && echo "  killed ${pid}"; done
            sleep 1
            ;;
        *)
            echo "Port still busy -- the panel may fail to bind. Continuing anyway."
            ;;
    esac
fi

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
# precedence: data/settings.json > LUNAR_BASE_HOST/PORT (set here by
# LUNAR_BASE_ADDR, or in the environment) > auto-detected LAN IP.
cd "$PANEL"
if [ -x "$PANEL/.venv/bin/python" ]; then
    VENV_PY="$PANEL/.venv/bin/python"
else
    VENV_PY="$PANEL/.venv/Scripts/python.exe"
fi
exec "$VENV_PY" -m web "$@"
