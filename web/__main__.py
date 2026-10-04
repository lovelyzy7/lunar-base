"""Run Lunar Base as a module: ``python -m web``.

Resolves the bind address from web.config (auto-detected LAN IP by default,
overridable via LUNAR_BASE_HOST / LUNAR_BASE_PORT), prints a short banner with
the URL other devices should use, then starts uvicorn.
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn


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
