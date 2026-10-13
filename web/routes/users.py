"""Routes for the read-only user viewer (Stage 0b) plus tutorial prep."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import tutorial_service, userdata_service

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


@router.get("/users", response_class=HTMLResponse)
def list_users_view(request: Request, message: str | None = None, error: str | None = None):
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "active": "users",
            "message": message,
            "error": error,
            "users": users,
        },
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail_view(
    request: Request,
    user_id: int,
    message: str | None = None,
    error: str | None = None,
):
    try:
        detail = userdata_service.get_user_detail(user_id)
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    if detail is None:
        return _redirect("/users", error=f"User {user_id} not found.")
    try:
        tutorial_groups = tutorial_service.preset_groups(user_id)
        tutorial_error = None
    except tutorial_service.TutorialError as e:
        tutorial_groups, tutorial_error = [], str(e)
    return templates.TemplateResponse(
        request,
        "user_detail.html",
        {
            "active": "users",
            "message": message,
            "error": error,
            "u": detail,
            "tutorial_groups": tutorial_groups,
            "tutorial_error": tutorial_error,
            "tutorial_choice": tutorial_service.DEFAULT_COMPANION_CHOICE,
            "tutorial_choices": tutorial_service.COMPANION_CHOICES,
        },
    )


# --- tutorial prep -----------------------------------------------------------

@router.get("/users/{user_id}/detail.json")
def user_detail_json(user_id: int) -> JSONResponse:
    """Live data for Ajax in-place refresh of the user detail page."""
    detail = userdata_service.get_user_detail(user_id)
    if detail is None:
        return JSONResponse({"ok": False, "error": f"User {user_id} not found."}, status_code=404)
    try:
        groups = tutorial_service.preset_groups(user_id)
        tutorials = [
            {"type": row["type"], "phase": row["current"], "done": row["done"]}
            for grp in groups for row in grp["rows"]
        ]
    except tutorial_service.TutorialError:
        tutorials = []
    return JSONResponse({
        "ok": True,
        "user": {
            "player_id": detail.player_id,
            "uuid": detail.uuid,
            "name": detail.name,
            "message": detail.message,
            "level": detail.level,
            "exp": detail.exp,
            "paid_gem": detail.paid_gem,
            "free_gem": detail.free_gem,
            "registered": detail.register_at.strftime("%Y-%m-%d %H:%M:%S") if detail.register_at else "",
            "last_login": detail.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if detail.last_login_at else "",
            "total_logins": detail.total_login_count,
        },
        "inventory": [
            {"label": x.label, "table": x.table, "count": x.count}
            for x in detail.inventory_counts
        ],
        "stackables": [
            {"label": x.label, "table": x.table, "distinct": x.distinct, "total": x.total}
            for x in detail.stackables
        ],
        "tutorials": tutorials,
    })


@router.post("/users/{user_id}/tutorials/prep")
def tutorials_prep(user_id: int, payload: dict = Body(...)) -> JSONResponse:
    """Mark selected tutorials completed (+ default deck + companion)."""
    types = payload.get("types") or []
    if not isinstance(types, list):
        return JSONResponse({"ok": False, "error": "types must be a list"}, status_code=400)
    try:
        choice = int(payload.get("choice_id", tutorial_service.DEFAULT_COMPANION_CHOICE))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "choice_id must be an integer"}, status_code=400)
    create_deck = bool(payload.get("create_deck", True))
    overrides = payload.get("phase_overrides") or {}
    if not isinstance(overrides, dict):
        return JSONResponse({"ok": False, "error": "phase_overrides must be an object"}, status_code=400)
    try:
        phase_overrides = {int(k): int(v) for k, v in overrides.items()}
        outcome = tutorial_service.prep(
            user_id, [int(t) for t in types], choice, create_deck,
            phase_overrides=phase_overrides,
        )
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "types/phase_overrides must be integers"}, status_code=400)
    except tutorial_service.TutorialError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse({
        "ok": True,
        "written": list(outcome.written),
        "skipped": list(outcome.skipped),
        "deck_created": outcome.deck_created,
        "companion_id": outcome.companion_id,
        "duration_ms": outcome.duration_ms,
    })


@router.post("/users/{user_id}/tutorials/preview")
def tutorials_preview(user_id: int, payload: dict = Body(...)) -> JSONResponse:
    types = payload.get("types") or []
    if not isinstance(types, list):
        return JSONResponse({"ok": False, "error": "types must be a list"}, status_code=400)
    try:
        rows = tutorial_service.preview(user_id, [int(t) for t in types])
    except tutorial_service.TutorialError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "rows": rows})


@router.post("/users/{user_id}/tutorials/clear")
def tutorials_clear(user_id: int, payload: dict = Body(...)) -> JSONResponse:
    types = payload.get("types") or []
    if not isinstance(types, list):
        return JSONResponse({"ok": False, "error": "types must be a list"}, status_code=400)
    try:
        removed = tutorial_service.clear(user_id, [int(t) for t in types])
    except tutorial_service.TutorialError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "removed": list(removed)})
