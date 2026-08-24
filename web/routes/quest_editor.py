"""Routes for the Quest Editor.

Lists every quest (grouped by chapter and difficulty, from data/names) with its
cleared status, and lets the operator mark specific quests cleared. Clearing
goes through the lunar-base-grant Go shim, which replays lunar-tear's real
HandleQuestFinish / HandleEventQuestFinish inside one UpdateUser transaction --
so a cleared quest grants its rewards, unlocks the next quest/difficulty, and
records its side-story scenario exactly like playing it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import quest_service, userdata_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _redirect(target: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    qs = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{target}{qs}", status_code=303)


@router.get("/quests", response_class=HTMLResponse)
def quest_editor_index(request: Request) -> RedirectResponse:
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/edit/quests", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/edit/quests", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


@router.get("/users/{user_id}/edit/quests", response_class=HTMLResponse)
def quest_editor_view(
    request: Request,
    user_id: int,
    message: str | None = None,
    error: str | None = None,
):
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    user_match = next((u for u in users if u.user_id == user_id), None)
    if user_match is None:
        return _redirect("/users", error=f"User {user_id} not found.")

    try:
        data = quest_service.grouped_quests(user_id)
    except quest_service.QuestError as e:
        return _redirect("/", error=str(e))

    return templates.TemplateResponse(
        request,
        "user_quests.html",
        {
            "active": "quests",
            "message": message,
            "error": error,
            "user_id": user_id,
            "user_name": user_match.name,
            "main_chapters": data["main_chapters"],
            "event_groups": data["event_groups"],
            "cleared_count": data["cleared_count"],
            "total_count": data["total_count"],
        },
    )


def _ok(applied: int, duration_ms: int, applied_ids: list[int] | None = None) -> JSONResponse:
    data: dict = {"ok": True, "applied": applied, "duration_ms": duration_ms}
    if applied_ids:
        data["applied_ids"] = list(applied_ids)
    return JSONResponse(data)


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.post("/users/{user_id}/edit/quests/revert")
def revert_quests_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    raw = payload.get("quest_ids")
    if not isinstance(raw, list) or not raw:
        return _err("quest_ids must be a non-empty list")
    try:
        quest_ids = [int(q) for q in raw]
    except (TypeError, ValueError):
        return _err("quest_ids must be integers")

    try:
        outcome = quest_service.revert_quests(user_id, quest_ids)
    except quest_service.QuestError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.applied, outcome.duration_ms, list(outcome.applied_ids))


@router.post("/users/{user_id}/edit/quests/clear")
def clear_quests_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    raw = payload.get("quest_ids")
    if not isinstance(raw, list) or not raw:
        return _err("quest_ids must be a non-empty list")
    try:
        quest_ids = [int(q) for q in raw]
    except (TypeError, ValueError):
        return _err("quest_ids must be integers")

    try:
        outcome = quest_service.clear_quests(user_id, quest_ids)
    except quest_service.QuestError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.applied, outcome.duration_ms, list(outcome.applied_ids))
