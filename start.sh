#!/usr/bin/env bash
# Start the Lunar Base web app - layout-agnostic, safe to run from anywhere.
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
#   LUNAR_BASE_ADDR="192.168.1.100:8888"
#   LUNAR_BASE_ADDR="127.0.0.1"          # this machine only
# This exports LUNAR_BASE_HOST / LUNAR_BASE_PORT for the panel; a value saved
# on the Settings page (data/settings.json) still takes precedence.
# ---------------------------------------------------------------------------
LUNAR_BASE_ADDR=""
ADDR_IN="$LUNAR_BASE_ADDR"
if [ -z "$ADDR_IN" ] && [ -t 0 ] && [ "${LUNAR_BASE_NO_PROMPT:-0}" != "1" ]; then
    printf "Panel bind address - Enter = auto-detect this machine's LAN IP.\n"
    printf "Examples: 192.168.1.100:8888 | 127.0.0.1 | 0.0.0.0:8888\n"
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
# A running Lunar Base panel is replaced automatically; foreign processes are
# listed and only killed after confirmation. If the port is still busy we abort
# with a clear message instead of letting python -m web fail on a bind error.
PORT="${LUNAR_BASE_PORT:-8888}"
LINUX_PORT_PIDS=""
linux_port_pids() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | awk -v p=":${PORT}$" '$4 ~ p {print $6}' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p'
    elif command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltnp 2>/dev/null | awk -v p=":${PORT}$" '$4 ~ p {print $7}' | sed 's#.*/##'
    fi
}
# Windows-side check (WSL): the panel usually runs the Windows python, which a
# WSL-side ss/lsof cannot see. PowerShell classifies the listeners, kills its
# own panel and prints "OWN id,id" / "OTHER id,id".
win_port_check() {
    powershell.exe -NoProfile -Command '$p=Get-NetTCPConnection -LocalPort '"${PORT}"' -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if($p){$own=@();$other=@();foreach($id in $p){$c=(Get-CimInstance Win32_Process -Filter ("ProcessId=" + $id) -ErrorAction SilentlyContinue).CommandLine; if($c -and ($c -match "-m web" -or $c -match "uvicorn")){$own+=$id}else{$other+=$id}}; if($own){Stop-Process -Id $own -Force -ErrorAction SilentlyContinue; "OWN " + ($own -join ",")}; if($other){"OTHER " + ($other -join ",")}}' 2>/dev/null | tr -d '\r'
}

if [ -n "${WSL_DISTRO_NAME:-}" ] && command -v powershell.exe >/dev/null 2>&1; then
    OUT="$(win_port_check)"
    OWN_PIDS="$(printf '%s\n' "$OUT" | sed -n 's/^OWN //p')"
    OTHER_PIDS="$(printf '%s\n' "$OUT" | sed -n 's/^OTHER //p')"
    if [ -n "$OWN_PIDS" ]; then
        echo "Replacing the running Lunar Base panel (PID ${OWN_PIDS//,/ }) ..."
        sleep 1
    fi
    if [ -n "$OTHER_PIDS" ]; then
        echo "Port ${PORT} is already in use by:"
        for pid in ${OTHER_PIDS//,/ }; do
            name="$(powershell.exe -NoProfile -Command "(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).ProcessName" 2>/dev/null | tr -d '\r')"
            echo "  PID ${pid}  ${name}"
        done
        KILL_ANS=""
        if [ -t 0 ] && [ "${LUNAR_BASE_NO_PROMPT:-0}" != "1" ]; then
            printf "  y = kill them and continue | Enter/n = keep them and continue anyway\n"
            printf "Kill these process(es)? [y/N] "
            read -r KILL_ANS || KILL_ANS=""
        fi
        case "$KILL_ANS" in
            y|Y|yes|YES)
                for pid in ${OTHER_PIDS//,/ }; do taskkill.exe /F /PID "$pid" >/dev/null 2>&1 && echo "  killed ${pid}"; done
                sleep 1
                ;;
        esac
        REMAIN="$(win_port_check | sed -n 's/^OWN //p; s/^OTHER //p' | tr ',' ' ')"
        if [ -n "${REMAIN// /}" ]; then
            echo
            echo "ERROR: port ${PORT} is still busy (PID ${REMAIN})."
            echo "Close that process (or answer y next time) and run start again."
            exit 1
        fi
    fi
else
    PIDS="$(linux_port_pids 2>/dev/null | sort -u | tr '\n' ' ')"
    if [ -n "${PIDS// /}" ]; then
        OWN=""
        OTHER=""
        for pid in $PIDS; do
            cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
            case "$cmd" in
                *"-m web"*|*uvicorn*) OWN="$OWN $pid" ;;
                *) OTHER="$OTHER $pid" ;;
            esac
        done
        if [ -n "${OWN// /}" ]; then
            echo "Replacing the running Lunar Base panel (PID ${OWN# }) ..."
            for pid in $OWN; do kill -9 "$pid" 2>/dev/null && echo "  killed ${pid}"; done
            sleep 1
        fi
        if [ -n "${OTHER// /}" ]; then
            echo "Port ${PORT} is already in use by:"
            for pid in $OTHER; do
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
                    for pid in $OTHER; do kill -9 "$pid" 2>/dev/null && echo "  killed ${pid}"; done
                    sleep 1
                    ;;
            esac
            REMAIN="$(linux_port_pids 2>/dev/null | sort -u | tr '\n' ' ')"
            if [ -n "${REMAIN// /}" ]; then
                echo
                echo "ERROR: port ${PORT} is still busy (PID ${REMAIN# })."
                echo "Close that process (or answer y next time) and run start again."
                exit 1
            fi
        fi
    fi
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
