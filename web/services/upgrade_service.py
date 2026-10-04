"""Upgrade Manager service.

This module powers the /upgrades page. Each public function returns a
(plan, summary) tuple so the route can preview-without-executing if needed,
and so the AJAX endpoints can report what was applied.

Implemented:
  - Add All Missing Companions (62-companion master list, skip 54-62)
  - All Missing Remnants (Important Items whose name starts with "Remnant")
  - Add All Missing Debris (Thought items the user doesn't have; the
    debris catalog is the union of every PossessionId where PossessionType
    is Thought in EntityMCostumeAwakenItemAcquireTable.json)
  - Exalt All Available Characters (CharacterRebirths -> 5 for owned chars)
  - Fill Mythic Slab Pages of all Available Characters
  - Upgrade All Companions (every owned companion -> level 50)
  - Upgrade All Weapons (per owned weapon: evolve to chain end, ascend to
    LB cap, refine if eligible, enhance to level cap, all skill + ability
    slots to lv15; cost-bypassing)
  - Upgrade All Costumes (per owned costume: awaken to 5 with status-up +
    debris grants, ascend to LB cap, enhance to level cap, active skill to
    lv15, unlock 3 lottery slots for SSR)
  - Fill All Karma Slots (per unlocked-but-empty slot: write the rarest
    OddsNumber from the slot's odds pool, ties broken by lowest OddsNumber)
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Final, Iterable

from web import config
from web.services import backup_service


BACKUP_REASON: Final[str] = "upgrade-manager"

# Per game-system memory: 33 playable characters total. Exaltation goes to 5.
EXALT_MAX: Final[int] = 5

# Companions: ids 1-53 + 8000001-8000009 (62 total). Inserting 54-62 freezes
# the game per the user's note in the project memory.
_KNOWN_BAD_COMPANION_IDS: Final[frozenset[int]] = frozenset(range(54, 63))


class UpgradeError(Exception):
    """Raised when an upgrade-manager operation fails."""


@dataclass(frozen=True)
class UpgradeOutcome:
    succeeded: int
    duration_ms: int
    detail: dict


# -----------------------------------------------------------------------------
# Catalog loaders (all read from data/masterdata/ JSON dumps)
# -----------------------------------------------------------------------------

_companion_catalog: list[int] | None = None
_remnant_catalog: list[tuple[int, str]] | None = None
_panels_by_character: dict[int, list[int]] | None = None
_owned_character_filter: list[int] | None = None
_thought_catalog: list[int] | None = None
_dark_memory_cutscene_ids: list[int] | None = None

# Companion / costume / weapon level + ascension caps. These mirror the
# m_config table values (verified 2026-05-02:
# WEAPON_LIMIT_BREAK_AVAILABLE_COUNT=4,
# COSTUME_LIMIT_BREAK_AVAILABLE_COUNT=4,
# COSTUME_AWAKEN_AVAILABLE_COUNT=5) and lunar-tear's hard-coded
# companionMaxLevel=50.
COMPANION_MAX_LEVEL: Final[int] = 50


def _load_companion_catalog() -> list[int]:
    """Return the full list of grantable companion ids."""
    global _companion_catalog
    if _companion_catalog is not None:
        return _companion_catalog
    path = config.MASTERDATA_DIR / "EntityMCompanionTable.json"
    if not path.exists():
        raise UpgradeError(f"EntityMCompanionTable.json not found at {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    ids = sorted({int(r["CompanionId"]) for r in rows} - _KNOWN_BAD_COMPANION_IDS)
    _companion_catalog = ids
    return ids


def _load_remnant_catalog() -> list[tuple[int, str]]:
    """Return [(important_item_id, name)] for every Remnant in the game."""
    global _remnant_catalog
    if _remnant_catalog is not None:
        return _remnant_catalog
    path = config.NAMES_DIR / "important_items.json"
    if not path.exists():
        raise UpgradeError(f"important_items.json not found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str]] = []
    for r in data.get("records", []):
        name = str(r.get("name", ""))
        if name.lower().startswith("remnant"):
            out.append((int(r["id"]), name))
    out.sort(key=lambda p: p[1].lower())
    _remnant_catalog = out
    return out


def _load_thought_catalog() -> list[int]:
    """Return every thought (Debris) id the game defines.

    Source: `EntityMThoughtTable.json` (233 unique entries — matches the
    user's spec). The awaken-acquire table only covers the ~196 thoughts
    grantable via costume awakening 5; using the canonical thought table
    here lets "Add All Missing Debris" plug any holes that Upgrade All
    Costumes leaves behind (e.g. event-only or duplicate-bonus debris).
    """
    global _thought_catalog
    if _thought_catalog is not None:
        return _thought_catalog
    path = config.MASTERDATA_DIR / "EntityMThoughtTable.json"
    if not path.exists():
        raise UpgradeError(f"EntityMThoughtTable.json not found at {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    ids = sorted({int(r["ThoughtId"]) for r in rows})
    _thought_catalog = ids
    return ids


def _load_dark_memory_cutscene_ids() -> list[int]:
    """Return every ContentsStoryId tied to a Dark Memory weapon grant.

    The game queues a forced cutscene per Dark Memory weapon's first
    acquisition; only one plays per launch and undrained ones soft-lock
    progression. EntityMContentsStoryTable currently holds 84 entries,
    all with IsForcedPlay=true and ContentsStoryUnlockConditionType=1
    (weapon-owned). Filtering on those flags keeps us future-proof if
    new non-Dark-Memory cutscenes get added later (we'd skip those).
    """
    global _dark_memory_cutscene_ids
    if _dark_memory_cutscene_ids is not None:
        return _dark_memory_cutscene_ids
    path = config.MASTERDATA_DIR / "EntityMContentsStoryTable.json"
    if not path.exists():
        raise UpgradeError(f"EntityMContentsStoryTable.json not found at {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    ids = sorted({
        int(r["ContentsStoryId"])
        for r in rows
        if r.get("IsForcedPlay") and int(r.get("ContentsStoryUnlockConditionType", 0)) == 1
    })
    _dark_memory_cutscene_ids = ids
    return ids


def _load_panels_by_character() -> dict[int, list[int]]:
    """Map character_id -> list of all monument-board panel ids that belong
    to that character (across both monuments × 3 ranks each).

    The mapping flow per the master data:
      assignment.CharacterId -> assignment.CharacterBoardCategoryId
      group.CharacterBoardCategoryId -> group.CharacterBoardGroupId
      board.CharacterBoardGroupId -> board.CharacterBoardId
      panel.CharacterBoardId -> panel.CharacterBoardPanelId
    """
    global _panels_by_character
    if _panels_by_character is not None:
        return _panels_by_character

    md = config.MASTERDATA_DIR
    paths = {
        "assignments": md / "EntityMCharacterBoardAssignmentTable.json",
        "groups": md / "EntityMCharacterBoardGroupTable.json",
        "boards": md / "EntityMCharacterBoardTable.json",
        "panels": md / "EntityMCharacterBoardPanelTable.json",
    }
    for label, path in paths.items():
        if not path.exists():
            raise UpgradeError(f"missing {label} table at {path}")

    assignments = json.loads(paths["assignments"].read_text(encoding="utf-8"))
    groups = json.loads(paths["groups"].read_text(encoding="utf-8"))
    boards = json.loads(paths["boards"].read_text(encoding="utf-8"))
    panels = json.loads(paths["panels"].read_text(encoding="utf-8"))

    # category -> [group_ids]
    groups_by_cat: dict[int, list[int]] = {}
    for g in groups:
        cat = int(g["CharacterBoardCategoryId"])
        groups_by_cat.setdefault(cat, []).append(int(g["CharacterBoardGroupId"]))

    # group -> [board_ids]
    boards_by_group: dict[int, list[int]] = {}
    for b in boards:
        g = int(b["CharacterBoardGroupId"])
        boards_by_group.setdefault(g, []).append(int(b["CharacterBoardId"]))

    # board -> [panel_ids]
    panels_by_board: dict[int, list[int]] = {}
    for p in panels:
        b = int(p["CharacterBoardId"])
        panels_by_board.setdefault(b, []).append(int(p["CharacterBoardPanelId"]))

    out: dict[int, list[int]] = {}
    for a in assignments:
        char_id = int(a["CharacterId"])
        cat = int(a["CharacterBoardCategoryId"])
        panel_ids: list[int] = []
        for gid in groups_by_cat.get(cat, []):
            for bid in boards_by_group.get(gid, []):
                panel_ids.extend(panels_by_board.get(bid, []))
        # Dedup while preserving order (rank ordering matters for in-game
        # animation but the actual state setting is order-independent).
        seen: set[int] = set()
        unique: list[int] = []
        for pid in panel_ids:
            if pid not in seen:
                seen.add(pid)
                unique.append(pid)
        out.setdefault(char_id, []).extend(unique)

    _panels_by_character = out
    return out


# -----------------------------------------------------------------------------
# Shim invocation
# -----------------------------------------------------------------------------

def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise UpgradeError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT} to build it (Go must be on PATH)."
        )


def _ensure_master_data() -> str:
    bin_path = config.find_master_data_bin()
    if bin_path is None:
        raise UpgradeError(
            "Master-data binary not found. Upgrade actions need lunar-tear's "
            "encrypted master data."
        )
    return str(bin_path)


def _invoke_shim(payload: dict, *, timeout: int = 300) -> dict:
    proc = subprocess.run(
        [str(config.GRANT_EXE_PATH)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=timeout,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    try:
        result = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        raise UpgradeError(
            f"shim returned non-JSON (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise UpgradeError(msg)
    return result


# -----------------------------------------------------------------------------
# Upgrade actions
# -----------------------------------------------------------------------------

def grant_missing_companions(user_id: int, owned_companion_ids: Iterable[int]) -> UpgradeOutcome:
    """Grant every companion the user doesn't already have, skipping the
    known game-breaking ids 54-62.
    """
    catalog = _load_companion_catalog()
    owned = set(owned_companion_ids)
    missing = [cid for cid in catalog if cid not in owned]
    if not missing:
        return UpgradeOutcome(succeeded=0, duration_ms=0, detail={"missing": 0})

    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "grant_companion_batch",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "companion_ids": missing,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=len(missing),
        duration_ms=duration_ms,
        detail={"granted_ids": missing},
    )


def grant_missing_remnants(user_id: int, owned_important_item_ids: Iterable[int]) -> UpgradeOutcome:
    """Grant 1 of every Remnant Important Item the user doesn't have. Reuses
    the existing GrantPossession/grant_batch path.
    """
    catalog = _load_remnant_catalog()
    owned = set(owned_important_item_ids)
    missing = [(rid, name) for (rid, name) in catalog if rid not in owned]
    if not missing:
        return UpgradeOutcome(succeeded=0, duration_ms=0, detail={"missing": 0})

    from web.services import grant_service
    grants = [
        {
            "possession_type": grant_service.POSSESSION_IMPORTANT,
            "possession_id": rid,
            "count": 1,
        }
        for (rid, _name) in missing
    ]

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "grant_batch",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "grants": grants,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=len(missing),
        duration_ms=duration_ms,
        detail={"granted": [{"id": r, "name": n} for (r, n) in missing]},
    )


def exalt_all_available(user_id: int, owned_character_ids: Iterable[int], current_rebirths: dict[int, int]) -> UpgradeOutcome:
    """Set CharacterRebirths to EXALT_MAX (5) for every owned character that
    isn't already there. Skips characters not in user_characters.
    """
    owned = sorted(set(owned_character_ids))
    plan: list[dict] = []
    for cid in owned:
        cur = current_rebirths.get(cid, 0)
        if cur >= EXALT_MAX:
            continue
        plan.append({"character_id": cid, "rebirth_count": EXALT_MAX})
    if not plan:
        return UpgradeOutcome(succeeded=0, duration_ms=0, detail={"already_max": True})

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "exalt_characters",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "exaltations": plan,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=len(plan),
        duration_ms=duration_ms,
        detail={"exalted": [p["character_id"] for p in plan]},
    )


def grant_missing_thoughts(user_id: int, owned_thought_ids: Iterable[int]) -> UpgradeOutcome:
    """Insert one ThoughtState row per missing Debris id via the shim."""
    catalog = _load_thought_catalog()
    owned = set(owned_thought_ids)
    missing = [tid for tid in catalog if tid not in owned]
    if not missing:
        return UpgradeOutcome(succeeded=0, duration_ms=0, detail={"missing": 0})

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "grant_thought_batch",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "thought_ids": missing,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", len(missing))),
        duration_ms=duration_ms,
        detail={"granted_ids": missing},
    )


def upgrade_all_companions(user_id: int) -> UpgradeOutcome:
    """Set every owned companion to max level (50) via the shim."""
    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "upgrade_all_companions",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={"max_level": COMPANION_MAX_LEVEL},
    )


def upgrade_all_weapons(user_id: int) -> UpgradeOutcome:
    """Mass-upgrade every owned weapon: evolve to the chain's final id, set
    LimitBreakCount to the cap, refine if eligible, enhance to the level
    cap, and set every weapon-skill + ability slot to its max level. Story
    unlocks 2-4 fire from the inlined checkWeaponStoryUnlocks. Cost-bypassing
    — no gold or materials are consumed.

    Per-class behavior falls out of the master data:
      - Recollections of Dusk + Dark Memory: granted at chain end already,
        so no evolution happens; level cap = 90 (RoD non-refinable) or
        whatever m_weapon_specific_enhance returns for Dark Memory's R50.
      - Subjugation: granted at R50 refinable already; awaken applies and
        level cap rises to 100.
      - Other 4-Star (R40 base): evolves once to R50; refines if eligible.
      - 3-Star (R30 base): evolves once to R40; usually no refine.

    Any Dark Memory acquisition cutscene queued by an evolution is marked
    played in the same transaction (see skip_dark_memory_cutscenes).
    """
    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "upgrade_all_weapons",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "contents_story_ids": _load_dark_memory_cutscene_ids(),
    }, timeout=600)
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={"limit_break": 4, "skill_max": 15},
    )


def upgrade_all_costumes(user_id: int) -> UpgradeOutcome:
    """Mass-upgrade every owned costume: awaken to 5 (each step's status-up
    rows accumulate; step-5 grants the costume's Debris/Thought via the
    inlined ItemAcquire path), set LimitBreakCount to 4, enhance to the
    rarity-driven level cap, set the active skill to its rarity max, and —
    for SSR (rarity 40) — unlock all 3 lottery (karma) slots with
    OddsNumber=0 so the player rolls them in-game. Cost-bypassing.
    """
    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "upgrade_all_costumes",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
    }, timeout=600)
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={"awaken_max": 5, "limit_break": 4, "lottery_slots": 3},
    )


def fill_karma_slots(
    user_id: int,
    preferences: dict[int, list[tuple[int, int]]] | None = None,
) -> UpgradeOutcome:
    """Set every already-unlocked karma slot's OddsNumber.

    `preferences` maps slot number (1-3) to a priority list of
    (effect_type, target_id) tuples. For each costume's slot the shim
    walks the list and picks the first entry present in that costume's
    odds pool; if none match, it falls back to the rarest pool entry
    (highest RarityType, ties broken by lowest OddsNumber).

    Always overwrites existing rolls — pass `preferences=None` (or an
    empty dict) to get the pure rarest-fallback behavior. The slot must
    be unlocked already; run Upgrade All Costumes first if needed.
    """
    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    payload: dict = {
        "action": "fill_karma_slots",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
    }
    if preferences:
        payload["karma_preferences"] = {
            str(slot): [{"effect_type": et, "target_id": tid} for et, tid in prefs]
            for slot, prefs in preferences.items()
            if prefs
        }
    result = _invoke_shim(payload)
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={"preferences_sent": bool(preferences)},
    )


def skip_dark_memory_cutscenes(user_id: int) -> UpgradeOutcome:
    """Mark every Dark Memory cutscene as already played.

    Use after mass-granting Dark Memory weapons to clear the cutscene
    queue. The shim writes user.ContentsStories[id]=now for each id
    in EntityMContentsStoryTable that has IsForcedPlay=true and
    ConditionType=1 (weapon-owned). Already-marked ids are skipped.
    """
    ids = _load_dark_memory_cutscene_ids()
    if not ids:
        return UpgradeOutcome(succeeded=0, duration_ms=0, detail={"reason": "no cutscenes in catalog"})
    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "mark_contents_stories_played",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "contents_story_ids": ids,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={"total_ids": len(ids)},
    )


def fill_mythic_slabs(user_id: int, owned_character_ids: Iterable[int]) -> UpgradeOutcome:
    """Release every monument-board panel for every owned character.

    Uses both monuments per character (typically Stone Tower Monument plus a
    second monument the user could not name from memory). All ranks (3 boards
    per monument) are filled in one batch.
    """
    panels_by_char = _load_panels_by_character()
    owned = sorted(set(owned_character_ids))
    panel_ids: list[int] = []
    chars_with_panels = 0
    for cid in owned:
        cps = panels_by_char.get(cid, [])
        if cps:
            chars_with_panels += 1
            panel_ids.extend(cps)

    if not panel_ids:
        return UpgradeOutcome(succeeded=0, duration_ms=0, detail={"reason": "no panels for owned characters"})

    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "release_panels",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "panel_ids": panel_ids,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return UpgradeOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={
            "panels_in_request": len(panel_ids),
            "characters": chars_with_panels,
        },
    )
