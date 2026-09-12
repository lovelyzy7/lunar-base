"""Routes for the Upgrade Manager.

Single page at /upgrades. Each section is an independent JSON endpoint so
the buttons can run independently without leaving the page. A backup is
taken automatically before every action (reason `upgrade-manager`).

Implemented:
  - Inventory: Add All Missing Companions, All Missing Remnants, Add All
    Missing Debris.
  - Characters: Exalt All Available Characters, Fill Mythic Slab Pages of
    all Available Characters.
  - Mass Upgrades: Upgrade All Companions (level 50), Upgrade All Weapons
    (multi-step ascend/evolve/refine/enhance/skills), Upgrade All Costumes
    (awaken/ascend/enhance/active-skill/karma slots), Fill All Karma Slots
    (rarest pick per unlocked-but-empty slot).
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import karma_service, upgrade_service, userdata_service

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


def _ok(outcome: upgrade_service.UpgradeOutcome) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": outcome.succeeded,
        "duration_ms": outcome.duration_ms,
        "detail": outcome.detail,
    })


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.get("/upgrades", response_class=HTMLResponse)
def upgrade_manager_index(request: Request) -> HTMLResponse:
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/upgrades", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/upgrades", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


@router.get("/users/{user_id}/upgrades", response_class=HTMLResponse)
def upgrade_manager_view(request: Request, user_id: int):
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    user_match = next((u for u in users if u.user_id == user_id), None)
    if user_match is None:
        return _redirect("/users", error=f"User {user_id} not found.")

    # Snapshot counts so the page can show "X owned / Y total" for each
    # action's preview number.
    owned_chars = userdata_service.get_owned_character_ids(user_id)
    owned_comps = userdata_service.get_owned_companion_ids(user_id)
    owned_remnants = userdata_service.get_owned_important_item_ids(user_id)
    owned_thoughts = userdata_service.get_owned_thought_ids(user_id)
    rebirths = userdata_service.get_character_rebirths(user_id)
    companion_levels = userdata_service.get_companion_levels(user_id)
    weapon_count = userdata_service.get_weapon_inventory_count(user_id)
    costume_count = userdata_service.get_costume_count(user_id)
    empty_karma_slots = userdata_service.get_empty_karma_slot_count(user_id)
    try:
        karma_options = karma_service.get_karma_options()
        karma_defaults = karma_service.compute_default_preferences()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))

    try:
        comp_catalog = upgrade_service._load_companion_catalog()
        remnant_catalog = upgrade_service._load_remnant_catalog()
        thought_catalog = upgrade_service._load_thought_catalog()
        panels_by_char = upgrade_service._load_panels_by_character()
    except upgrade_service.UpgradeError as e:
        return _redirect("/", error=str(e))

    missing_companions = sum(1 for cid in comp_catalog if cid not in owned_comps)
    missing_remnants = sum(1 for (rid, _n) in remnant_catalog if rid not in owned_remnants)
    missing_thoughts = sum(1 for tid in thought_catalog if tid not in owned_thoughts)
    exalt_targets = sum(
        1 for cid in owned_chars if rebirths.get(cid, 0) < upgrade_service.EXALT_MAX
    )
    panel_total = sum(len(panels_by_char.get(cid, [])) for cid in owned_chars)
    companions_to_upgrade = sum(
        1 for lvl in companion_levels if lvl < upgrade_service.COMPANION_MAX_LEVEL
    )

    return templates.TemplateResponse(
        request,
        "upgrade_manager.html",
        {
            "active": "upgrades",
            "user_id": user_id,
            "user_name": user_match.name,
            "owned_character_count": len(owned_chars),
            "exalt_targets": exalt_targets,
            "missing_companions": missing_companions,
            "companion_total": len(comp_catalog),
            "missing_remnants": missing_remnants,
            "remnant_total": len(remnant_catalog),
            "missing_thoughts": missing_thoughts,
            "thought_total": len(thought_catalog),
            "panel_total": panel_total,
            "owned_companion_count": len(companion_levels),
            "companions_to_upgrade": companions_to_upgrade,
            "owned_weapon_count": weapon_count,
            "owned_costume_count": costume_count,
            "empty_karma_slots": empty_karma_slots,
            "karma_options": karma_options,
            "karma_defaults": karma_defaults,
        },
    )


def _stats(user_id: int) -> dict:
    """The numbers shown next to each action row (also refreshable over Ajax)."""
    owned_chars = userdata_service.get_owned_character_ids(user_id)
    owned_comps = userdata_service.get_owned_companion_ids(user_id)
    owned_remnants = userdata_service.get_owned_important_item_ids(user_id)
    owned_thoughts = userdata_service.get_owned_thought_ids(user_id)
    rebirths = userdata_service.get_character_rebirths(user_id)
    companion_levels = userdata_service.get_companion_levels(user_id)
    try:
        comp_catalog = upgrade_service._load_companion_catalog()
        remnant_catalog = upgrade_service._load_remnant_catalog()
        thought_catalog = upgrade_service._load_thought_catalog()
        panels_by_char = upgrade_service._load_panels_by_character()
    except upgrade_service.UpgradeError:
        comp_catalog, remnant_catalog, thought_catalog, panels_by_char = [], [], [], {}
    return {
        "owned_character_count": len(owned_chars),
        "exalt_targets": sum(1 for cid in owned_chars if rebirths.get(cid, 0) < upgrade_service.EXALT_MAX),
        "panel_total": sum(len(panels_by_char.get(cid, [])) for cid in owned_chars),
        "missing_companions": sum(1 for cid in comp_catalog if cid not in owned_comps),
        "companion_total": len(comp_catalog),
        "missing_remnants": sum(1 for (rid, _n) in remnant_catalog if rid not in owned_remnants),
        "remnant_total": len(remnant_catalog),
        "missing_thoughts": sum(1 for tid in thought_catalog if tid not in owned_thoughts),
        "thought_total": len(thought_catalog),
        "owned_companion_count": len(companion_levels),
        "companions_to_upgrade": sum(1 for lvl in companion_levels if lvl < upgrade_service.COMPANION_MAX_LEVEL),
        "owned_weapon_count": userdata_service.get_weapon_inventory_count(user_id),
        "owned_costume_count": userdata_service.get_costume_count(user_id),
        "empty_karma_slots": userdata_service.get_empty_karma_slot_count(user_id),
    }


@router.get("/users/{user_id}/upgrades/state")
def upgrade_state(user_id: int) -> JSONResponse:
    """Counts for the action rows, for in-place Ajax refresh after a run."""
    try:
        return JSONResponse({"ok": True, **_stats(user_id)})
    except (FileNotFoundError, upgrade_service.UpgradeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ----------------- JSON endpoints ----------------------------------------

@router.post("/users/{user_id}/upgrades/exalt_all")
def exalt_all_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_character_ids(user_id)
        rebirths = userdata_service.get_character_rebirths(user_id)
        outcome = upgrade_service.exalt_all_available(user_id, owned, rebirths)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/fill_mythic_slabs")
def fill_mythic_slabs_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_character_ids(user_id)
        outcome = upgrade_service.fill_mythic_slabs(user_id, owned)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/grant_missing_companions")
def grant_missing_companions_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_companion_ids(user_id)
        outcome = upgrade_service.grant_missing_companions(user_id, owned)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/grant_missing_remnants")
def grant_missing_remnants_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_important_item_ids(user_id)
        outcome = upgrade_service.grant_missing_remnants(user_id, owned)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/grant_missing_thoughts")
def grant_missing_thoughts_endpoint(user_id: int) -> JSONResponse:
    try:
        owned = userdata_service.get_owned_thought_ids(user_id)
        outcome = upgrade_service.grant_missing_thoughts(user_id, owned)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/upgrade_all_companions")
def upgrade_all_companions_endpoint(user_id: int) -> JSONResponse:
    try:
        outcome = upgrade_service.upgrade_all_companions(user_id)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/upgrade_all_weapons")
def upgrade_all_weapons_endpoint(user_id: int) -> JSONResponse:
    try:
        outcome = upgrade_service.upgrade_all_weapons(user_id)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/upgrade_all_costumes")
def upgrade_all_costumes_endpoint(user_id: int) -> JSONResponse:
    try:
        outcome = upgrade_service.upgrade_all_costumes(user_id)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/fill_karma_slots")
async def fill_karma_slots_endpoint(user_id: int, request: Request) -> JSONResponse:
    """Body shape:
        { "preferences": { "1": [[2, 100353]], "2": [...], "3": [...] } }
    Empty body / missing preferences = pure rarest fallback.
    """
    preferences: dict[int, list[tuple[int, int]]] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_prefs = (body or {}).get("preferences") or {}
    for slot_key, items in raw_prefs.items():
        try:
            slot = int(slot_key)
        except (TypeError, ValueError):
            continue
        parsed: list[tuple[int, int]] = []
        for entry in items or []:
            try:
                et, tid = int(entry[0]), int(entry[1])
            except (TypeError, ValueError, IndexError):
                continue
            parsed.append((et, tid))
        if parsed:
            preferences[slot] = parsed
    try:
        outcome = upgrade_service.fill_karma_slots(user_id, preferences=preferences or None)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/upgrades/skip_dark_memory_cutscenes")
def skip_dark_memory_cutscenes_endpoint(user_id: int) -> JSONResponse:
    try:
        outcome = upgrade_service.skip_dark_memory_cutscenes(user_id)
    except upgrade_service.UpgradeError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)
