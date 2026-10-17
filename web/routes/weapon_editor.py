"""Routes for the Weapon Editor (Stage 3, granting only).

Same shape as the Costume Editor, but for weapons. Grants R30 + R40 + R50
playable weapons through the lunar-base-grant Go shim, which calls
lunar-tear's GrantWeapon inside one UpdateUser transaction.

Two key differences from the Costume Editor:
- GrantWeapon does NOT self-skip already-owned weapons (each call inserts a
  fresh UUID). The service filters owned ids before invoking the shim.
- The game enforces a hard 999-row inventory cap. Every grant is pre-flighted
  and the whole batch is refused if it would push the user over.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import userdata_service, weapon_service

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


@router.get("/weapons", response_class=HTMLResponse)
def weapon_editor_index(request: Request) -> RedirectResponse:
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/edit/weapons", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/edit/weapons", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


@router.get("/users/{user_id}/edit/weapons", response_class=HTMLResponse)
WEAPON_PAGE = 200   # rows per page; the catalog is ~520 rows


def weapon_editor_view(
    request: Request,
    user_id: int,
    offset: int = 0,
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
        owned = userdata_service.get_owned_weapon_ids(user_id)
        inventory_count = userdata_service.get_weapon_inventory_count(user_id)
        groups = weapon_service.grouped_catalog(owned)
    except weapon_service.WeaponError as e:
        return _redirect("/", error=str(e))

    total_owned = sum(g["owned_count"] for g in groups)
    total_count = sum(g["total_count"] for g in groups)
    missing_count = total_count - total_owned

    return templates.TemplateResponse(
        request,
        "user_weapons.html",
        {
            "active": "weapons",
            "message": message,
            "error": error,
            "user_id": user_id,
            "user_name": user_match.name,
            "groups": groups,
            "offset": max(0, int(offset or 0)),
            "page_size": WEAPON_PAGE,
            "total_owned": total_owned,
            "total_count": total_count,
            "missing_count": missing_count,
            "inventory_count": inventory_count,
            "inventory_cap": weapon_service.WEAPON_INVENTORY_CAP,
        },
    )


def _ok(applied: int, duration_ms: int, granted_ids: list[int], inventory_count: int) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": applied,
        "duration_ms": duration_ms,
        "inventory_count": inventory_count,
        "results": [{"weapon_id": wid} for wid in granted_ids],
    })


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.post("/users/{user_id}/edit/weapons/grant_batch")
def grant_batch_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    raw = payload.get("weapon_ids")
    if not isinstance(raw, list) or not raw:
        return _err("weapon_ids must be a non-empty list")
    try:
        weapon_ids = [int(wid) for wid in raw]
    except (TypeError, ValueError):
        return _err("weapon_ids must be integers")

    try:
        owned = userdata_service.get_owned_weapon_ids(user_id)
        inventory_count = userdata_service.get_weapon_inventory_count(user_id)
        outcome = weapon_service.grant_weapons(user_id, weapon_ids, owned, inventory_count)
    except weapon_service.WeaponError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    new_count = userdata_service.get_weapon_inventory_count(user_id)
    return _ok(outcome.succeeded, outcome.duration_ms, outcome.granted_ids, new_count)


@router.post("/users/{user_id}/edit/weapons/grant_all_missing")
def grant_all_missing_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_weapon_ids(user_id)
        inventory_count = userdata_service.get_weapon_inventory_count(user_id)
        outcome = weapon_service.grant_all_missing(user_id, owned, inventory_count)
    except weapon_service.WeaponError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    new_count = userdata_service.get_weapon_inventory_count(user_id)
    return _ok(outcome.succeeded, outcome.duration_ms, outcome.granted_ids, new_count)
