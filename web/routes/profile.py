"""User Profile page — every per-user operation in one console.

`/users/{user_id}/profile` (aliased by `/profile`) shows the account's identity
and currencies, then a grouped list of one-click operations for that user:

  * resources   — MAX ALL consumables / materials, quick gem grants
  * completion  — grant every missing costume / weapon / companion / remnant / debris
  * bulk upgrades — weapons, costumes, companions, memoirs, exalt, mythic slabs,
                    skip DM cutscenes, fill karma slots
  * missions    — complete all active missions
  * quests      — complete every quest / restore every cleared quest
  * data        — take a backup

All actions reuse the existing JSON endpoints (and the same in-page confirm
modal / banner / grant-row markup as the other editors); only the bulk quest
actions and the backup get a small profile-scoped endpoint here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import (
    backup_service,
    costume_service,
    grant_service,
    mission_service,
    profile_service,
    quest_service,
    upgrade_service,
    userdata_service,
    weapon_service,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _redirect(target: str, *, error: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode

    qs = f"?{urlencode({'error': error})}" if error else ""
    return RedirectResponse(url=f"{target}{qs}", status_code=303)


def _remaining(user_id: int) -> dict:
    """How much work each one-click operation still has left, so the page can
    disable a RUN button once there is nothing to do (e.g. after "grant all
    missing X" the button goes grey)."""
    out: dict[str, int] = {}
    try:
        owned_costumes = userdata_service.get_owned_costume_ids(user_id)
        out["missing_costumes"] = len(costume_service.all_catalog_ids() - owned_costumes)
    except (costume_service.CostumeError, FileNotFoundError):
        out["missing_costumes"] = 0
    try:
        owned_weapons = userdata_service.get_owned_weapon_ids(user_id)
        out["missing_weapons"] = len(weapon_service.all_catalog_ids() - owned_weapons)
    except (weapon_service.WeaponError, FileNotFoundError):
        out["missing_weapons"] = 0
    try:
        owned_comps = userdata_service.get_owned_companion_ids(user_id)
        out["missing_companions"] = sum(
            1 for cid in upgrade_service._load_companion_catalog() if cid not in owned_comps)
    except (upgrade_service.UpgradeError, FileNotFoundError):
        out["missing_companions"] = 0
    try:
        owned_remnants = userdata_service.get_owned_important_item_ids(user_id)
        out["missing_remnants"] = sum(
            1 for (rid, _n) in upgrade_service._load_remnant_catalog() if rid not in owned_remnants)
    except (upgrade_service.UpgradeError, FileNotFoundError):
        out["missing_remnants"] = 0
    try:
        owned_thoughts = userdata_service.get_owned_thought_ids(user_id)
        out["missing_thoughts"] = sum(
            1 for tid in upgrade_service._load_thought_catalog() if tid not in owned_thoughts)
    except (upgrade_service.UpgradeError, FileNotFoundError):
        out["missing_thoughts"] = 0
    # bulk-upgrade targets (same numbers the Upgrade Manager shows)
    try:
        owned_chars = userdata_service.get_owned_character_ids(user_id)
        rebirths = userdata_service.get_character_rebirths(user_id)
        out["exalt_targets"] = sum(
            1 for cid in owned_chars if rebirths.get(cid, 0) < upgrade_service.EXALT_MAX)
        panels = upgrade_service._load_panels_by_character()
        out["panel_total"] = sum(len(panels.get(cid, [])) for cid in owned_chars)
    except (upgrade_service.UpgradeError, FileNotFoundError):
        out["exalt_targets"] = 0
        out["panel_total"] = 0
    try:
        levels = userdata_service.get_companion_levels(user_id)
        out["companions_to_upgrade"] = sum(
            1 for lvl in levels if lvl < upgrade_service.COMPANION_MAX_LEVEL)
    except (upgrade_service.UpgradeError, FileNotFoundError):
        out["companions_to_upgrade"] = 0
    return out


def _exp_curve() -> list[int]:
    """Shared EXP curve (see profile_service.exp_curve)."""
    return profile_service.exp_curve()


def _stats(user_id: int) -> dict:
    """Cheap counters rendered next to each operation row (also refreshable over
    Ajax after an action)."""
    detail = userdata_service.get_user_detail(user_id)
    cleared = quest_service.cleared_quest_ids(user_id)
    try:
        total_quests = len(set(quest_service.all_quest_ids()))
    except quest_service.QuestError:
        total_quests = 0
    stats = {
        "name": detail.name if detail else "",
        "message": detail.message if detail else "",
        "paid_gem": detail.paid_gem if detail else 0,
        "free_gem": detail.free_gem if detail else 0,
        "level": detail.level if detail else 0,
        "exp": detail.exp if detail else 0,
        "weapons": userdata_service.get_weapon_inventory_count(user_id),
        "costumes": userdata_service.get_costume_count(user_id),
        "companions": userdata_service.get_companion_count(user_id),
        "memoirs": userdata_service.get_memoir_count(user_id),
        "cleared_quests": len(cleared),
        "total_quests": total_quests,
        "quests_remaining": max(0, total_quests - len(cleared)),
    }
    stats.update(_remaining(user_id))
    return stats


@router.get("/profile", response_class=HTMLResponse)
def profile_index(request: Request) -> RedirectResponse:
    """Entry point: jump to the remembered (or only) user's profile."""
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/profile", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/profile", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


@router.get("/users/{user_id}/profile", response_class=HTMLResponse)
def profile_view(request: Request, user_id: int):
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    user_match = next((u for u in users if u.user_id == user_id), None)
    if user_match is None:
        return _redirect("/users", error=f"User {user_id} not found.")

    try:
        detail = userdata_service.get_user_detail(user_id)
        stats = _stats(user_id)
    except quest_service.QuestError:
        detail, stats = None, {
            "paid_gem": 0, "free_gem": 0, "level": 0, "exp": 0,
            "weapons": 0, "costumes": 0, "companions": 0, "memoirs": 0,
            "cleared_quests": 0, "total_quests": 0,
        }

    return templates.TemplateResponse(
        request,
        "user_profile.html",
        {
            "active": "profile",
            "user_id": user_id,
            "user_name": user_match.name,
            "u": detail,
            "stats": stats,
            "ptype_paid_gem": grant_service.POSSESSION_PAID_GEM,
            "ptype_free_gem": grant_service.POSSESSION_FREE_GEM,
            "exp_curve": _exp_curve(),
            "status_clear": mission_service.STATUS_CLEAR,
            "status_received": mission_service.STATUS_REWARD_RECEIVED,
        },
    )


@router.get("/users/{user_id}/profile/stats")
def profile_stats(user_id: int) -> JSONResponse:
    """Ajax refresh of the counters shown next to each operation."""
    try:
        return JSONResponse({"ok": True, **_stats(user_id)})
    except (FileNotFoundError, quest_service.QuestError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _quest_outcome(outcome: quest_service.WriteOutcome) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": outcome.applied,
        "duration_ms": outcome.duration_ms,
        "applied_ids": list(outcome.applied_ids),
    })


@router.post("/users/{user_id}/profile/quests/clear_all")
def profile_clear_all_quests(user_id: int) -> JSONResponse:
    """Complete every quest in the catalog (the server skips cleared ones)."""
    try:
        outcome = quest_service.clear_quests(user_id, quest_service.all_quest_ids())
    except quest_service.QuestError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": f"Backup failed: {e}"}, status_code=500)
    return _quest_outcome(outcome)


@router.post("/users/{user_id}/profile/quests/revert_all")
def profile_revert_all_quests(user_id: int) -> JSONResponse:
    """Reopen every quest the user has cleared."""
    try:
        ids = sorted(quest_service.cleared_quest_ids(user_id))
    except quest_service.QuestError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    if not ids:
        return JSONResponse({"ok": True, "applied": 0, "duration_ms": 0, "applied_ids": []})
    try:
        outcome = quest_service.revert_quests(user_id, ids)
    except quest_service.QuestError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": f"Backup failed: {e}"}, status_code=500)
    return _quest_outcome(outcome)


@router.post("/users/{user_id}/profile/update")
async def profile_update(user_id: int, request: Request) -> JSONResponse:
    """Update the account fields sent in the body (name / message / level / exp /
    paid_gem / free_gem); omitted fields stay unchanged."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "object body required"}, status_code=400)

    def as_str(key: str):
        if key not in body or body[key] is None:
            return None
        if not isinstance(body[key], str):
            raise ValueError(f"{key} must be a string")
        return body[key].strip() if key != "message" else body[key]

    def as_int(key: str):
        if key not in body or body[key] is None or body[key] == "":
            return None
        return int(body[key])

    try:
        outcome = profile_service.set_user_info(
            user_id,
            name=as_str("name"),
            message=as_str("message"),
            level=as_int("level"),
            exp=as_int("exp"),
            paid_gem=as_int("paid_gem"),
            free_gem=as_int("free_gem"),
        )
    except (ValueError, TypeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except profile_service.ProfileError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": f"Backup failed: {e}"}, status_code=500)
    return JSONResponse({"ok": True, "applied": outcome.applied, "duration_ms": outcome.duration_ms})


@router.post("/users/{user_id}/profile/backup")
def profile_backup(user_id: int) -> JSONResponse:
    """Snapshot the game database (same rolling pool as the Save Data page)."""
    try:
        info = backup_service.create_backup(reason="manual")
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "filename": info.filename, "size": info.size_human})
