"""Routes for the Costume Editor (Stage 2).

A separate page from the Item Editor. Grants R30 + R40 playable costumes via
the lunar-base-grant Go shim, which in turn calls lunar-tear's GrantCostume
inside one UpdateUser transaction. lunar-tear's GrantCostume self-skips
already-owned costumes and creates a level-1 character if missing, so we
don't have to.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import costume_service, karma_service, userdata_service

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


@router.get("/costumes", response_class=HTMLResponse)
def costume_editor_index(request: Request) -> RedirectResponse:
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/edit/costumes", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/edit/costumes", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


COSTUME_PAGE = 120   # rows per page; the catalog is ~260 rows of heavy markup


@router.get("/users/{user_id}/edit/costumes", response_class=HTMLResponse)
def costume_editor_view(
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
        owned = userdata_service.get_owned_costume_ids(user_id)
        groups = costume_service.grouped_catalog(owned)
    except costume_service.CostumeError as e:
        return _redirect("/", error=str(e))

    total_owned = sum(g["owned_count"] for g in groups)
    total_count = sum(g["total_count"] for g in groups)
    missing_count = total_count - total_owned

    # Karma data for per-costume editor: pool entries shared by group_id,
    # costume_id -> slot -> group_id mapping, and current rolled state.
    try:
        pools = karma_service.get_pools()
        costume_slot_groups = karma_service.get_costume_slot_groups()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    karma_state = userdata_service.get_costume_karma_state(user_id)

    # JSON-friendly serialization for the template's <script> embed.
    karma_pools_json = {
        gid: [
            {
                "value": f"{e.effect_type}:{e.target_id}",
                "odds_number": e.odds_number,
                "rarity": e.rarity,
                "label": e.label,
            }
            for e in entries
        ]
        for gid, entries in pools.items()
    }
    karma_costume_slots_json = {
        cid: slot_to_gid for cid, slot_to_gid in costume_slot_groups.items()
    }
    karma_state_json = {
        cid: slot_to_odds for cid, slot_to_odds in karma_state.items()
    }
    unlocked_karma_costume_count = len(karma_state)

    return templates.TemplateResponse(
        request,
        "user_costumes.html",
        {
            "active": "costumes",
            "message": message,
            "error": error,
            "user_id": user_id,
            "user_name": user_match.name,
            "groups": groups,
            "offset": max(0, int(offset or 0)),
            "page_size": COSTUME_PAGE,
            "total_owned": total_owned,
            "total_count": total_count,
            "missing_count": missing_count,
            "karma_pools_json": karma_pools_json,
            "karma_costume_slots_json": karma_costume_slots_json,
            "karma_state_json": karma_state_json,
            "unlocked_karma_costume_count": unlocked_karma_costume_count,
        },
    )


def _ok(applied: int, duration_ms: int, granted_ids: list[int]) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": applied,
        "duration_ms": duration_ms,
        "results": [{"costume_id": cid} for cid in granted_ids],
    })


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.get("/users/{user_id}/karma/pools")
def karma_pools(user_id: int) -> JSONResponse:
    """Karma pools and slot→group map, loaded on demand by the costume editor.

    They are ~340 KB of JSON; embedding them in the page delayed every row's
    first paint on a phone, and most visits never open a karma select.
    """
    pools = karma_service.get_pools()
    slots = karma_service.get_costume_slot_groups()
    state = userdata_service.get_costume_karma_state(user_id)
    return JSONResponse({
        "ok": True,
        "pools": {
            gid: [{"value": f"{e.effect_type}:{e.target_id}", "odds_number": e.odds_number,
                   "rarity": e.rarity, "label": e.label} for e in entries]
            for gid, entries in pools.items()
        },
        "slots": slots,
        "current": state,
    })


@router.post("/users/{user_id}/edit/costumes/grant_batch")
def grant_batch_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    raw = payload.get("costume_ids")
    if not isinstance(raw, list) or not raw:
        return _err("costume_ids must be a non-empty list")
    try:
        costume_ids = [int(cid) for cid in raw]
    except (TypeError, ValueError):
        return _err("costume_ids must be integers")

    try:
        outcome = costume_service.grant_costumes(user_id, costume_ids)
    except costume_service.CostumeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.succeeded, outcome.duration_ms, outcome.granted_ids)


@router.post("/users/{user_id}/edit/costumes/grant_all_missing")
def grant_all_missing_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_costume_ids(user_id)
    except FileNotFoundError as e:
        return _err(str(e), status=500)
    try:
        outcome = costume_service.grant_all_missing(user_id, owned)
    except costume_service.CostumeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.succeeded, outcome.duration_ms, outcome.granted_ids)


@router.post("/users/{user_id}/edit/costumes/update_karma_batch")
def update_karma_batch_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Body shape:
        {"costumes": [{"costume_id": 12345, "karma": {"1": "2:100353", "2": "1:800513", "3": "1:800543"}}]}

    Each `karma` value is "<effect_type>:<target_id>" — the dropdown's value
    string. The route resolves it to a concrete OddsNumber via
    karma_service before invoking the shim. Slots not yet unlocked on the
    server are silently skipped.
    """
    raw = payload.get("costumes")
    if not isinstance(raw, list) or not raw:
        return _err("costumes must be a non-empty list")

    costume_karma: dict[int, dict[int, int]] = {}
    for item in raw:
        try:
            cid = int(item["costume_id"])
        except (TypeError, ValueError, KeyError):
            continue
        karma_map = item.get("karma") or {}
        slots: dict[int, int] = {}
        for slot_key, value in karma_map.items():
            try:
                slot = int(slot_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, str) or ":" not in value:
                continue
            try:
                et_str, tid_str = value.split(":", 1)
                et, tid = int(et_str), int(tid_str)
            except (TypeError, ValueError):
                continue
            odds = karma_service.resolve_odds_number(cid, slot, et, tid)
            if odds is None:
                continue
            slots[slot] = odds
        if slots:
            costume_karma[cid] = slots

    if not costume_karma:
        return _err("no valid karma updates in request")

    try:
        outcome = costume_service.update_costume_karma(user_id, costume_karma)
    except costume_service.CostumeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.succeeded, outcome.duration_ms, outcome.granted_ids)
