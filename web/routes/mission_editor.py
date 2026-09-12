"""Routes for the Mission ("quest") Editor.

Lists every mission grouped by category with its status/progress, and lets the
operator complete missions or edit a single mission's status + progress value.
Mutation endpoints return JSON so the page updates in place (same convention as
the Item Editor). Each write takes one auto-backup first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import backup_service, mission_service, userdata_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _user_name(user_id: int) -> str:
    try:
        for u in userdata_service.list_users():
            if u.user_id == user_id:
                return u.name
    except FileNotFoundError:
        pass
    return ""


def _ok(outcome: mission_service.WriteOutcome) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": outcome.applied,
        "duration_ms": outcome.duration_ms,
        "rows": outcome.rows,
    })


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


# --- Nav entry point -------------------------------------------------------


@router.get("/missions", response_class=HTMLResponse)
def mission_editor_index(request: Request) -> RedirectResponse:
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return RedirectResponse(url=f"/?error={e}", status_code=303)
    remembered = session.remembered_redirect(request, "/edit/missions", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/edit/missions", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


# --- Editor page ----------------------------------------------------------


@router.get("/users/{user_id}/edit/missions", response_class=HTMLResponse)
def mission_editor_view(
    request: Request,
    user_id: int,
    show: str = "active",
    message: str | None = None,
    error: str | None = None,
):
    show_all = show == "all"
    try:
        if not mission_service.user_exists(user_id):
            return RedirectResponse(url=f"/users?error=User {user_id} not found.", status_code=303)
        groups = mission_service.get_user_missions(user_id, active_only=not show_all)
    except FileNotFoundError as e:
        return RedirectResponse(url=f"/?error={e}", status_code=303)

    server_running = backup_service.detect_lunar_tear_running()

    return templates.TemplateResponse(
        request,
        "user_missions.html",
        {
            "active": "missions",
            "message": message,
            "error": error,
            "user_id": user_id,
            "user_name": _user_name(user_id),
            "groups": groups,
            "show_all": show_all,
            "server_running": server_running,
            "settable_statuses": [
                {"value": s, "label": mission_service.STATUS_LABELS[s]}
                for s in mission_service.SETTABLE_STATUSES
            ],
            "STATUS_CLEAR": mission_service.STATUS_CLEAR,
            "STATUS_REWARD_RECEIVED": mission_service.STATUS_REWARD_RECEIVED,
            "event_category": mission_service.EVENT_CATEGORY_TYPE,
        },
    )


# --- JSON mutation endpoints ----------------------------------------------


@router.post("/users/{user_id}/edit/missions/set")
def set_mission_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        mission_id = int(payload["mission_id"])
        status = int(payload["status"])
        progress = int(payload["progress"])
    except (KeyError, TypeError, ValueError):
        return _err("mission_id, status, and progress are required integers")
    try:
        outcome = mission_service.set_mission(user_id, mission_id, status, progress)
    except ValueError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup or DB failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/edit/missions/complete_category")
def complete_category_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        category = int(payload["category"])
        status = int(payload.get("status", mission_service.STATUS_CLEAR))
    except (KeyError, TypeError, ValueError):
        return _err("category is a required integer")
    active_only = bool(payload.get("active_only", True))
    try:
        outcome = mission_service.complete_category(user_id, category, status, active_only=active_only)
    except ValueError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup or DB failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/edit/missions/complete_all")
def complete_all_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        status = int(payload.get("status", mission_service.STATUS_CLEAR))
    except (TypeError, ValueError):
        return _err("status must be an integer")
    include_events = bool(payload.get("include_events", False))
    active_only = bool(payload.get("active_only", True))
    try:
        outcome = mission_service.complete_all(
            user_id, status, include_events=include_events, active_only=active_only
        )
    except ValueError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup or DB failed: {e}", status=500)
    return _ok(outcome)
