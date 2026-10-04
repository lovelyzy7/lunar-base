"""Self-restart helper for the /settings page.

The settings page saves host/port to data/settings.json, answers the browser,
and then re-execs the current Python process so the new bind address takes
effect without any external supervisor. Old sockets are released by execv
replacing the process image (this also releases the old server resources).
"""

from __future__ import annotations

import os
import sys
import threading


def restart_argv() -> list[str]:
    """Rebuild the argv used to launch this panel."""
    extra = getattr(sys, "_lunar_web_argv", None)
    return [sys.executable, "-m", "web", *(extra or [])]


def schedule_restart(delay: float = 1.0) -> None:
    """Re-exec the panel after `delay` seconds (lets the HTTP response flush)."""
    argv = restart_argv()

    def _do_restart() -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001 - never let flushing block restart
                pass
        os.execv(sys.executable, argv)

    threading.Timer(delay, _do_restart).start()
