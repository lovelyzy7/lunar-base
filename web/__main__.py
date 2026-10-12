"""Run Lunar Base as a module: ``python -m web``.

Resolves the bind address from web.config (auto-detected LAN IP by default,
overridable via LUNAR_BASE_HOST / LUNAR_BASE_PORT), prints a short banner with
the URL other devices should use, then starts uvicorn.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

import uvicorn


def _probe_bind(host: str, port: int) -> None:
    """Check the bind address before uvicorn starts.

    Uvicorn logs raw errno messages and returns (exit code 0) when the address
    cannot be resolved or the port is taken; this prints an actionable message
    and exits non-zero, which the start scripts can report.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        print(f"ERROR: cannot resolve bind address {host!r}: {exc}")
        print("  Check the address for typos, or clear data/settings.json to auto-detect.")
        sys.exit(1)
    if not infos:
        print(f"ERROR: no usable address for bind host {host!r}.")
        sys.exit(1)
    probe = socket.socket(infos[0][0], infos[0][1])
    try:
        probe.bind(infos[0][4][:2])
    except OSError as exc:
        print(f"ERROR: cannot bind {host}:{port}: {exc}")
        print(f"  Port {port} is already in use — close the other instance and retry")
        print("  (start.bat / start.sh now replace a running Lunar Base panel automatically).")
        sys.exit(1)
    finally:
        probe.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m web", description="Run Lunar Base.")
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Require login and restrict each user to their own record "
        "(admin sees all). Default: open access, no login.",
    )
    args = parser.parse_args()

    # Remember how we were launched so /settings can re-exec the same command
    # (host/port changes restart the panel in place).
    sys._lunar_web_argv = list(sys.argv[1:])  # type: ignore[attr-defined]

    # Set the env var BEFORE importing config so its auth state is consistent.
    if args.auth:
        os.environ["LUNAR_BASE_AUTH"] = "1"

    from web import config

    host = config.HOST
    port = config.PORT
    auth_on = config.auth_enabled()
    _probe_bind(host, port)

    print()
    print("=== Lunar Base ===")
    if host == "0.0.0.0":
        # Couldn't pin a single LAN IP (or the user asked for all interfaces).
        print(f"Listening on all network interfaces, port {port}.")
        print(f"  On this PC:      http://127.0.0.1:{port}")
        print(f"  From another PC: http://<this-PC-LAN-IP>:{port}")
    elif host == "127.0.0.1":
        print(f"Open http://127.0.0.1:{port} in your browser (this PC only).")
    else:
        print(f"Reachable on your network at:  http://{host}:{port}")
        print("  (use that address from this PC and from other devices)")
    if auth_on:
        print("Login REQUIRED — each account sees only its own record; admin sees all.")
        print("  (set up the admin with: python tools/set_admin_password.py)")
    else:
        print("WARNING: no login (open mode) — anyone who can reach this PC can edit the save.")
        print("  Start with --auth to require login and per-user restriction.")
    print("Ctrl+C to stop.")
    print()

    uvicorn.run("web.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
