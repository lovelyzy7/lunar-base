"""Routes for the Item Editor (Stage 1).

All grant endpoints return JSON; the front-end uses fetch() so the page never
fully reloads — successful rows turn green and update their displayed counts
in place. Each batch endpoint takes a single auto-backup at the top of the
operation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import grant_service, names_service, userdata_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


# Display order for tabs. Each entry: (tab_key, label, possession_type, names_category).
_ITEM_TABS: tuple[tuple[str, str, int, str], ...] = (
    ("consumables", "Consumables", grant_service.POSSESSION_CONSUMABLE, "consumables"),
    ("materials", "Materials", grant_service.POSSESSION_MATERIAL, "materials"),
    ("important", "Important Items", grant_service.POSSESSION_IMPORTANT, "important_items"),
)
# Gems are not part of any item catalog: the tab is two fixed rows (paid/free)
# rendered from user_gem. It is still a tab key so ?tab=gems can select it and
# the initial pane never falls back to a catalog tab.
_TAB_KEYS: frozenset[str] = frozenset({key for key, *_ in _ITEM_TABS} | {"gems"})


def _redirect(target: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    qs = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{target}{qs}", status_code=303)


def _build_item_rows(names_category: str, owned: dict[int, int]) -> list[dict]:
    """One dict per item in the category. Owned rows sort first."""
    name_map = names_service.get_names(names_category)
    rows: list[dict] = []
    for item_id, name in name_map.items():
        count = owned.get(item_id, 0)
        rows.append({
            "id": item_id,
            "name": name,
            "count": count,
            "owned": count > 0,
        })
    rows.sort(key=lambda r: (not r["owned"], r["name"].lower()))
    return rows


# --- Nav entry point -------------------------------------------------------


@router.get("/items", response_class=HTMLResponse)
def item_editor_index(request: Request) -> RedirectResponse:
    """Smart entry from the global nav.

    If a user is already remembered (or exactly one user exists in the save),
    jump straight to their editor. Otherwise send to the user list to pick.
    """
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/edit/items", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/edit/items", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


# --- Editor page ----------------------------------------------------------


@router.get("/users/{user_id}/edit/items", response_class=HTMLResponse)
def item_editor_view(
    request: Request,
    user_id: int,
    tab: str | None = None,
    q: str = "",
    message: str | None = None,
    error: str | None = None,
):
    try:
        state = userdata_service.get_item_state(user_id)
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    if state is None:
        return _redirect("/users", error=f"User {user_id} not found.")

    # Every tab ships its full catalog. user_items.html switches tabs with a
    # local class toggle and never re-requests, so any server-side window leaves
    # the non-current tabs blank (the ab9a5ed regression this replaces: only the
    # active tab got a 200-row window, the others were assigned []). Main
    # renders all rows for every tab; per-tab search filters rows client-side.
    # `tab` only selects which pane is open on load (default Gems, as before).
    current = tab if tab in _TAB_KEYS else "gems"
    needle = (q or "").strip().lower()
    tabs = []
    for key, label, ptype, names_cat in _ITEM_TABS:
        owned_map = {
            "consumables": state.consumables,
            "materials": state.materials,
            "important": state.important_items,
        }[key]
        every = _build_item_rows(names_cat, owned_map)
        if needle:
            every = [r for r in every
                     if needle in str(r["name"]).lower() or needle in str(r["id"])]
        tabs.append({
            "key": key,
            "label": label,
            "possession_type": ptype,
            "rows": every,
            "owned_count": sum(1 for r in every if r["owned"]),
            "total_count": len(every),
        })

    return templates.TemplateResponse(
        request,
        "user_items.html",
        {
            "active": "items",
            "message": message,
            "error": error,
            "user_id": user_id,
            "user_name": state.name,
            "paid_gem": state.paid_gem,
            "free_gem": state.free_gem,
            "tabs": tabs,
            "current_tab": current,
            "query": q or "",
            "ptype_paid_gem": grant_service.POSSESSION_PAID_GEM,
            "ptype_free_gem": grant_service.POSSESSION_FREE_GEM,
            "ptype_consumable": grant_service.POSSESSION_CONSUMABLE,
            "ptype_material": grant_service.POSSESSION_MATERIAL,
            "ptype_important": grant_service.POSSESSION_IMPORTANT,
        },
    )


# --- JSON mutation endpoints ----------------------------------------------
#
# All four endpoints return the same envelope shape:
#   {"ok": true,  "applied": N, "duration_ms": N, "results": [{"possession_id":..., "amount":...}, ...]}
#   {"ok": false, "error": "..."}
# Front-end maps `results` back to rows by possession_id and turns them green.


def _ok(applied: int, duration_ms: int, results: list[dict]) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": applied,
        "duration_ms": duration_ms,
        "results": results,
    })


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _results_from_plan(plan: list[grant_service.GrantPlanItem]) -> list[dict]:
    return [
        {"possession_type": g.possession_type, "possession_id": g.possession_id, "amount": g.count}
        for g in plan
    ]


@router.post("/users/{user_id}/edit/items/grant")
def grant_one_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        ptype = int(payload["possession_type"])
        pid = int(payload["possession_id"])
        count = int(payload["count"])
    except (KeyError, TypeError, ValueError):
        return _err("possession_type, possession_id, and count are required integers")

    plan = [grant_service.GrantPlanItem(ptype, pid, count)]
    try:
        outcome = grant_service.grant_batch(user_id, plan)
    except grant_service.GrantError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.succeeded, outcome.duration_ms, _results_from_plan(plan))


@router.post("/users/{user_id}/edit/items/grant_batch")
def grant_batch_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    raw = payload.get("grants")
    if not isinstance(raw, list) or not raw:
        return _err("grants must be a non-empty list")
    try:
        plan = [
            grant_service.GrantPlanItem(
                int(g["possession_type"]),
                int(g["possession_id"]),
                int(g["count"]),
            )
            for g in raw
        ]
    except (KeyError, TypeError, ValueError):
        return _err("each grant needs integer possession_type, possession_id, count")

    try:
        outcome = grant_service.grant_batch(user_id, plan)
    except grant_service.GrantError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)

    return _ok(outcome.succeeded, outcome.duration_ms, _results_from_plan(plan))


@router.post("/users/{user_id}/edit/items/max_consumables")
def max_consumables_endpoint(user_id: int) -> JSONResponse:
    plan = grant_service.build_max_consumables_plan()
    if not plan:
        return _ok(0, 0, [])
    try:
        outcome = grant_service.grant_batch(user_id, plan)
    except grant_service.GrantError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome.succeeded, outcome.duration_ms, _results_from_plan(plan))


@router.post("/users/{user_id}/edit/items/max_materials")
def max_materials_endpoint(user_id: int) -> JSONResponse:
    plan = grant_service.build_max_materials_plan()
    if not plan:
        return _ok(0, 0, [])
    try:
        outcome = grant_service.grant_batch(user_id, plan)
    except grant_service.GrantError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome.succeeded, outcome.duration_ms, _results_from_plan(plan))


