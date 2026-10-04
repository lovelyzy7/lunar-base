"""/settings page: panel service settings, admin account and game-server control.

Saving host/port writes data/settings.json and schedules a self-restart; the
browser then redirects to the new address. Auth, the admin account and the
game-server section apply without a restart.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config, restart
from web.services import auth_service, server_control_service, settings_service, setup_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _page_data() -> dict[str, Any]:
    return {
        "active": "settings",
        "settings": settings_service.load(),
        "control_mode": settings_service.server_control_config()["mode"],
        "lan_choices": settings_service.lan_ip_choices(),
        "wizard": settings_service.wizard_config(),
        "admin": auth_service.admin_status(),
        "server": server_control_service.status(),
        "effective_host": config.HOST,
        "effective_port": config.PORT,
        "env_host": bool(os.environ.get("LUNAR_BASE_HOST")),
        "env_port": bool(os.environ.get("LUNAR_BASE_PORT")),
        "platform": sys.platform,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request):
    return templates.TemplateResponse(request, "settings.html", _page_data())


@router.get("/settings/state")
def settings_state() -> JSONResponse:
    return JSONResponse({"ok": True, **_page_data()})


@router.post("/settings/detect-tools")
def settings_detect_tools() -> JSONResponse:
    return JSONResponse({"ok": True, "tools": settings_service.detect_tools(force=True)})


@router.post("/settings/save")
def settings_save(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Save service settings (host/port/auth). Host/port are optional so other
    pages can save their own slices without touching them."""
    has_host = "host" in payload
    has_port = "port" in payload
    try:
        new_host = settings_service.validate_host(str(payload.get("host", ""))) if has_host else None
        new_port = settings_service.validate_port(payload.get("port")) if has_port else None

        auth = payload.get("auth")
        if auth is not None and not isinstance(auth, bool):
            return _err("auth must be true or false")
    except ValueError as exc:
        return _err(str(exc))

    check_host = new_host if new_host is not None else config.HOST
    check_port = new_port if new_port is not None else config.PORT
    restart_required = (new_host is not None or new_port is not None) and (
        (check_host, check_port) != (config.HOST, config.PORT)
    )
    if restart_required and check_port != config.PORT:
        if not settings_service.port_available(check_host, check_port):
            return _err(f"port {check_port} is already in use on {check_host or '0.0.0.0'}")

    admin_missing = False

    values: dict[str, Any] = {}
    if new_host is not None:
        values["host"] = new_host
    if new_port is not None:
        values["port"] = new_port
    if auth is not None:
        values["auth"] = auth
    if not values:
        return _err("nothing to save")

    settings_service.save(values)

    if auth is True and not auth_service.admin_configured():
        # Bootstrap safety: the operator who just enabled login keeps an admin
        # session so the account can be created on this same page. Without
        # this, enabling the gate before creating an admin would lock everyone
        # out (there would be no way to authenticate).
        admin_missing = True
        request.session["role"] = "admin"
        request.session["username"] = "bootstrap"

    if restart_required:
        restart.schedule_restart(1.0)
    return JSONResponse({
        "ok": True,
        "restart": restart_required,
        "admin_missing": admin_missing,
        "host": check_host,
        "port": check_port,
        "current_host": config.HOST,
        "current_port": config.PORT,
    })


# --- admin account -----------------------------------------------------------

@router.get("/settings/admin")
def settings_admin_status() -> JSONResponse:
    return JSONResponse({"ok": True, **auth_service.admin_status()})


@router.post("/settings/admin")
def settings_admin_save(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Create/reset the panel admin account from the UI (no CLI needed)."""
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    confirm = str(payload.get("confirm") or "")
    if password != confirm:
        return _err("The two passwords do not match.")
    try:
        saved = auth_service.set_admin_credentials(username, password)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"cannot write the admin file: {exc}", 500)
    return JSONResponse({"ok": True, "configured": True, "username": saved})


# --- initialization (panel owns the setup scripts) --------------------------

@router.get("/settings/setup/state")
def settings_setup_state() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "status": setup_service.status(),
        "jobs": setup_service.list_jobs()[:10],
    })


@router.post("/settings/setup/run")
def settings_setup_run(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    action = str(payload.get("action") or "")
    try:
        job = setup_service.run_action(action)
    except setup_service.SetupError as exc:
        return _err(str(exc))
    return JSONResponse({"ok": True, "job": job})


@router.get("/settings/setup/jobs/{job_id}")
def settings_setup_job(job_id: str, tail: int = 200) -> JSONResponse:
    job = setup_service.get_job(job_id)
    if not job:
        return _err("job not found", 404)
    return JSONResponse({"ok": True, "job": job, "log": setup_service.job_log(job_id, tail)})


@router.post("/settings/setup/jobs/{job_id}/cancel")
def settings_setup_cancel(job_id: str) -> JSONResponse:
    try:
        setup_service.cancel_job(job_id)
    except setup_service.SetupError as exc:
        return _err(str(exc))
    return JSONResponse({"ok": True})


@router.post("/settings/setup/restart")
def settings_setup_restart() -> JSONResponse:
    """Restart the panel itself (e.g. after installing new dependencies)."""
    restart.schedule_restart(1.0)
    return JSONResponse({"ok": True, "restart": True})


# --- game server (best-effort control) --------------------------------------

@router.get("/settings/server/status")
def settings_server_status(tail: int = 200) -> JSONResponse:
    data = server_control_service.status()
    data["log"] = server_control_service.log_tail(tail)
    return JSONResponse({"ok": True, **data})


def _control(action) -> JSONResponse:
    try:
        result = action()
    except server_control_service.ServerControlError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"process error: {exc}", 500)
    data = server_control_service.status()
    data["log"] = server_control_service.log_tail(200)
    return JSONResponse({"ok": True, "result": result, **data})


@router.post("/settings/server/start")
def settings_server_start() -> JSONResponse:
    return _control(server_control_service.start)


@router.post("/settings/server/stop")
def settings_server_stop() -> JSONResponse:
    return _control(server_control_service.stop)


@router.post("/settings/server/restart")
def settings_server_restart() -> JSONResponse:
    return _control(server_control_service.restart)


@router.post("/settings/server/wizard")
def settings_server_wizard(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Save server/.wizard.json so `server --prefer-saved` runs without prompts."""
    try:
        cfg = settings_service.save_wizard_config(payload)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"cannot write the wizard config: {exc}", 500)
    return JSONResponse({
        "ok": True,
        "wizard": cfg,
        "restart_required": True,
        "message": "Saved. Restart the game server for the new ports to take effect.",
    })
