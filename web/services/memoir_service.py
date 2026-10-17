"""Memoir Editor service (Stage 5b).

Memoirs are stored in `user_parts`. Each memoir name has 20 master rows
(4 rarities x 5 PartsInitialLotteryId variants); we only care about R40.
The 5 lottery variants share PartsStatusMainLotteryGroupId=41 and are
functionally identical from lunar-tear's perspective (helpers.go's
defaultPartsStatusMainByLotteryGroup ignores PartsInitialLotteryId), so
we always pick lottery=1 (the lowest part_id of the R40 block) and
override PartsStatusMainId to the user's chosen primary stat.

Stat encoding (from the user's enhance log: "kind=6 calc=2 val=30" =
"+3%"):
    KIND  1=Agility 2=Attack 3=CritDmg 4=CritRate 6=HP 7=Defense
    CALC  1=flat, 2=percent (percentages stored x10)

There are 36 EntityMPartsStatusMain rows: 9 stat categories x 4 tiers.
We always use tier 4 for R40 memoirs.
"""

from __future__ import annotations

import platform
import sys

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Final

from web import config
from web.services import backup_service


BACKUP_REASON: Final[str] = "memoir-editor"
MEMOIR_INVENTORY_CAP: Final[int] = 999
MEMOIR_MAX_LEVEL: Final[int] = 15

# 18 sets, ordered to match EntityMPartsSeries 1..18. Each set has three
# memoirs whose PartsGroupId matches the order in EntityMPartsGroup
# (verified: groups 1-3 -> series 1, groups 4-6 -> series 2, etc).
#
# `priority` flags the three sets the user pinned at the top.
SETS: Final[list[dict]] = [
    {
        "id": 1, "series_id": 1, "name": "The Young Lord's Studies",
        "bonus_2": "HP up by 15%.",
        "bonus_3": "HP up by 25%.",
        "memoirs": [
            {"group_id": 1, "name": "Solemnity"},
            {"group_id": 2, "name": "Devotion"},
            {"group_id": 3, "name": "Purity"},
        ],
    },
    {
        "id": 2, "series_id": 2, "name": "Musings of a Hunter",
        "bonus_2": "Defense up by 10%.",
        "bonus_3": "Defense up by 15%.",
        "memoirs": [
            {"group_id": 4, "name": "The Hunt Begins"},
            {"group_id": 5, "name": "Reoccurring Dream"},
            {"group_id": 6, "name": "The Kingdom's Fate"},
        ],
    },
    {
        "id": 3, "series_id": 3, "name": "Of Kings and Soldiers",
        "priority": True,
        "bonus_2": "Attack up by 10%.",
        "bonus_3": "Attack up by 15%.",
        "memoirs": [
            {"group_id": 7, "name": "A King and His People"},
            {"group_id": 8, "name": "Soldiers and Weapons"},
            {"group_id": 9, "name": "Heart to Heart"},
        ],
    },
    {
        "id": 4, "series_id": 4, "name": "The Wanted Man",
        "priority": True,
        "bonus_2": "Critical hit damage up by 25%.",
        "bonus_3": "Critical hit damage up by 40%.",
        "memoirs": [
            {"group_id": 10, "name": "Meetings"},
            {"group_id": 11, "name": "Prey"},
            {"group_id": 12, "name": "Destiny"},
        ],
    },
    {
        "id": 5, "series_id": 5, "name": "Dreams of Colchis",
        "priority": True,
        "bonus_2": "Critical rate up by 10%.",
        "bonus_3": "Critical rate up by 15%.",
        "memoirs": [
            {"group_id": 13, "name": "Alpha"},
            {"group_id": 14, "name": "Mythos"},
            {"group_id": 15, "name": "Hubris"},
        ],
    },
    {
        "id": 6, "series_id": 6, "name": "Baal's Letters",
        "bonus_2": "Agility up by 10%.",
        "bonus_3": "Agility up by 20%.",
        "memoirs": [
            {"group_id": 16, "name": "True Dreams"},
            {"group_id": 17, "name": "Prophetic Dreams"},
            {"group_id": 18, "name": "Waking Dreams"},
        ],
    },
    {
        "id": 7, "series_id": 7, "name": "A Chance for Conflict",
        "bonus_2": "Attack +20%, Defense -20% for 60 sec.",
        "bonus_3": "Attack +30%, Defense -30% for 60 sec.",
        "memoirs": [
            {"group_id": 19, "name": "An Offering of Flowers"},
            {"group_id": 20, "name": "Division and Strife"},
            {"group_id": 21, "name": "The Rallying Beacon"},
        ],
    },
    {
        "id": 8, "series_id": 8, "name": "A Future Retrospective",
        "bonus_2": "Defense +20%, Attack -20% for 60 sec.",
        "bonus_3": "Defense +30%, Attack -30% for 60 sec.",
        "memoirs": [
            {"group_id": 22, "name": "Life in Ages Past"},
            {"group_id": 23, "name": "The Way of Death"},
            {"group_id": 24, "name": "Shopping Malls"},
        ],
    },
    {
        "id": 9, "series_id": 9, "name": "The Ambitious Land",
        "bonus_2": "Damage taken reduced 10% 3x at start of wave.",
        "bonus_3": "Damage taken reduced 15% 3x at start of wave.",
        "memoirs": [
            {"group_id": 25, "name": "Dawn of Thought"},
            {"group_id": 26, "name": "Ray of Thought"},
            {"group_id": 27, "name": "Clouded Thought"},
        ],
    },
    {
        "id": 10, "series_id": 10, "name": "Blighted Flowers",
        "bonus_2": "Weapon-skill cooldown -15% at start of battle.",
        "bonus_3": "Weapon-skill cooldown -30% at start of battle.",
        "memoirs": [
            {"group_id": 28, "name": "Laurel"},
            {"group_id": 29, "name": "Geranium"},
            {"group_id": 30, "name": "Marigold"},
        ],
    },
    {
        "id": 11, "series_id": 11, "name": "Ephemeral Memories",
        "bonus_2": "Chance normal atk is a 3-chain or more +5%.",
        "bonus_3": "Chance normal atk is a 3-chain or more +10%.",
        "memoirs": [
            {"group_id": 31, "name": "A Fleeting Voice"},
            {"group_id": 32, "name": "A Fleeting Love"},
            {"group_id": 33, "name": "A Fleeting Dream"},
        ],
    },
    {
        "id": 12, "series_id": 12, "name": "The Worker's Foundation",
        "bonus_2": "Atk/Def +15% for 30s when HP <50%. Once only.",
        "bonus_3": "Atk/Def +30% for 30s when HP <50%. Once only.",
        "memoirs": [
            {"group_id": 34, "name": "My Place"},
            {"group_id": 35, "name": "My Future"},
            {"group_id": 36, "name": "My Heart"},
        ],
    },
    {
        "id": 13, "series_id": 13, "name": "Magical Pharmacology",
        "bonus_2": "All allies' Attack +5% for 60 sec.",
        "bonus_3": "All allies' Attack +10% for 60 sec.",
        "memoirs": [
            {"group_id": 37, "name": "Beauty Potion"},
            {"group_id": 38, "name": "Love Potion"},
            {"group_id": 39, "name": "Knowledge Potion"},
        ],
    },
    {
        "id": 14, "series_id": 14, "name": "Forbidden Tomes of Thaumaturgy",
        "bonus_2": "All allies' HP +5%.",
        "bonus_3": "All allies' HP +10%.",
        "memoirs": [
            {"group_id": 40, "name": "Spell for the Eyes"},
            {"group_id": 41, "name": "Spell for the Limbs"},
            {"group_id": 42, "name": "Spell for the Heart"},
        ],
    },
    {
        "id": 15, "series_id": 15, "name": "Boundaries Melted in Song",
        "bonus_2": "All allies' Agility +5% for 60 sec.",
        "bonus_3": "All allies' Agility +10% for 60 sec.",
        "memoirs": [
            {"group_id": 43, "name": "Verse of Prayer"},
            {"group_id": 44, "name": "Chant of Desire"},
            {"group_id": 45, "name": "Song of Avowal"},
        ],
    },
    {
        "id": 16, "series_id": 16, "name": "The Scientific Apex",
        "bonus_2": "All allies' Defense +5% for 60 sec.",
        "bonus_3": "All allies' Defense +10% for 60 sec.",
        "memoirs": [
            {"group_id": 46, "name": "Gunshots in a Ruin"},
            {"group_id": 47, "name": "The Desolate Wastes"},
            {"group_id": 48, "name": "Signs of Oblivion"},
        ],
    },
    {
        "id": 17, "series_id": 17, "name": "Seafaring Cradle Tales",
        "bonus_2": "All allies' Attack +5%.",
        "bonus_3": "All allies' Attack +10%.",
        "memoirs": [
            {"group_id": 49, "name": "A Night of Meetings"},
            {"group_id": 50, "name": "A Morning of Surprise"},
            {"group_id": 51, "name": "An Afternoon of Ambition"},
        ],
    },
    {
        "id": 18, "series_id": 18, "name": "Desert Tales",
        "bonus_2": "Chance of 3/4/5-chain +3% each (all allies).",
        "bonus_3": "Chance of 3/4/5-chain +5% each (all allies).",
        "memoirs": [
            {"group_id": 52, "name": "Coin of Another"},
            {"group_id": 53, "name": "Unfermented Alcohol"},
            {"group_id": 54, "name": "Hooked Fish"},
        ],
    },
]


# R40 (rarity 40) primary stat options. The 6 percent-or-Agility tier-4
# entries from EntityMPartsStatusMain. `display` is what the user picks
# from in the dropdown, "max" is the wiki-stated cap (the in-game value
# is computed by the client from kind/calc + level + main_id, so we
# don't write a value for primary stats; we only set the main_id).
PRIMARY_OPTIONS: Final[list[dict]] = [
    {"key": "crit_rate", "main_id": 28, "kind": 4, "calc": 1,
     "label": "Crit Rate (max +20%)"},
    {"key": "crit_dmg",  "main_id": 32, "kind": 3, "calc": 1,
     "label": "Crit Damage (max +25%)"},
    {"key": "atk_pct",   "main_id": 12, "kind": 2, "calc": 2,
     "label": "ATK% (max +20%)"},
    {"key": "hp_pct",    "main_id": 20, "kind": 6, "calc": 2,
     "label": "HP% (max +20%)"},
    {"key": "def_pct",   "main_id": 16, "kind": 7, "calc": 2,
     "label": "DEF% (max +20%)"},
    {"key": "agility",   "main_id": 36, "kind": 1, "calc": 1,
     "label": "Agility (max +120)"},
]

PRIMARY_BY_KEY: Final[dict[str, dict]] = {p["key"]: p for p in PRIMARY_OPTIONS}


# Sub-stat options for slots 1-4 at tier-4 (perfect-roll lv15 cap).
# `lottery_id` is the matching PartsStatusMainId (tier 4) — stored in
# PartsStatusSubLotteryId for game-data fidelity. `value` is the pre-
# filled "max perfect" value; the user can edit it in the form.
#
# Percent values stored x10 (12.5% -> 125, 25% -> 250, 36% -> 360).
# Flat values stored as-is. Defaults derived from the user's spec.
SUB_OPTIONS: Final[list[dict]] = [
    {"key": "crit_rate", "lottery_id": 28, "kind": 4, "calc": 1,
     "value": 250, "label": "Crit Rate +25%"},
    {"key": "crit_dmg",  "lottery_id": 32, "kind": 3, "calc": 1,
     "value": 360, "label": "Crit Damage +36%"},
    {"key": "atk_pct",   "lottery_id": 12, "kind": 2, "calc": 2,
     "value": 125, "label": "ATK +12.5%"},
    {"key": "atk_flat",  "lottery_id": 4,  "kind": 2, "calc": 1,
     "value": 600, "label": "ATK flat (+600)"},
    {"key": "hp_pct",    "lottery_id": 20, "kind": 6, "calc": 2,
     "value": 125, "label": "HP +12.5%"},
    {"key": "hp_flat",   "lottery_id": 24, "kind": 6, "calc": 1,
     "value": 6500, "label": "HP flat (+6500)"},
    {"key": "def_pct",   "lottery_id": 16, "kind": 7, "calc": 2,
     "value": 125, "label": "DEF +12.5%"},
    {"key": "def_flat",  "lottery_id": 8,  "kind": 7, "calc": 1,
     "value": 600, "label": "DEF flat (+600)"},
    {"key": "agility",   "lottery_id": 36, "kind": 1, "calc": 1,
     "value": 72, "label": "Agility +72"},
]

SUB_BY_KEY: Final[dict[str, dict]] = {s["key"]: s for s in SUB_OPTIONS}

# Default order for "auto-fill" of slots 1-4 across the priority sets:
# Crit Rate, Crit Damage, ATK%, ATK flat. The user explicitly asked for
# this combo in the example. Slot 1 is the first granted (at lv 3 in
# the real flow) and slot 4 is the last (lv 12).
DEFAULT_SUB_ORDER: Final[list[str]] = ["crit_rate", "crit_dmg", "atk_pct", "atk_flat"]


# -----------------------------------------------------------------------------

class MemoirError(Exception):
    """Raised when a memoir operation fails."""


@dataclass(frozen=True)
class MemoirOutcome:
    succeeded: int
    duration_ms: int
    detail: dict


# -----------------------------------------------------------------------------
# Catalog helpers
# -----------------------------------------------------------------------------

def r40_part_id(group_id: int) -> int:
    """Return the R40 lottery=1 master part_id for a given parts_group_id.

    Layout in EntityMPartsTable.json: each group has 20 rows (4 rarities x
    5 lottery variants), grouped contiguously by group_id. R40 (rarity=40)
    occupies the last 5 slots, lottery=1 is the first of those.
    """
    return (group_id - 1) * 20 + 16


def list_sets() -> list[dict]:
    """Return SETS sorted with priority sets first, by id within each band."""
    return sorted(
        SETS,
        key=lambda s: (0 if s.get("priority") else 1, s["id"]),
    )


def get_set(set_id: int) -> dict | None:
    for s in SETS:
        if s["id"] == set_id:
            return s
    return None


# -----------------------------------------------------------------------------
# Shim invocation
# -----------------------------------------------------------------------------

def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise MemoirError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT}, or drop a prebuilt into tools/grant/prebuilt/"
            f"{sys.platform}-{platform.machine().lower()} (see tools/grant/README.md)."
        )


def _invoke_shim(payload: dict, *, timeout: int = 60) -> dict:
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
        raise MemoirError(
            f"shim returned non-JSON (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise MemoirError(msg)
    return result


# -----------------------------------------------------------------------------
# Operations
# -----------------------------------------------------------------------------

def _validate_memoir_payload(memoir: dict) -> None:
    """Raise MemoirError if a per-memoir spec is malformed.

    Expected shape:
      {
        "group_id": int,
        "primary_key": str,        # one of PRIMARY_BY_KEY
        "subs": [                  # 0..4 entries; slot is 1..4
            {"slot": int, "sub_key": str, "value": int (optional)},
            ...
        ],
      }
    """
    try:
        group_id = int(memoir["group_id"])
    except (KeyError, TypeError, ValueError):
        raise MemoirError("group_id missing/invalid in memoir spec")
    if group_id <= 0:
        raise MemoirError(f"group_id must be positive: {group_id}")
    primary_key = memoir.get("primary_key")
    if primary_key not in PRIMARY_BY_KEY:
        raise MemoirError(f"unknown primary_key: {primary_key!r}")
    subs = memoir.get("subs") or []
    if not isinstance(subs, list):
        raise MemoirError("subs must be a list")
    seen_slots: set[int] = set()
    for s in subs:
        try:
            slot = int(s["slot"])
        except (KeyError, TypeError, ValueError):
            raise MemoirError("sub.slot missing/invalid")
        if slot < 1 or slot > 4:
            raise MemoirError(f"sub.slot out of range: {slot}")
        if slot in seen_slots:
            raise MemoirError(f"duplicate slot in subs: {slot}")
        seen_slots.add(slot)
        if s.get("sub_key") not in SUB_BY_KEY:
            raise MemoirError(f"unknown sub_key: {s.get('sub_key')!r}")


def _build_subs_for_shim(subs: list[dict]) -> list[dict]:
    """Translate UI subs (slot + sub_key + optional value) into shim subs."""
    out: list[dict] = []
    for s in subs:
        opt = SUB_BY_KEY[s["sub_key"]]
        value = s.get("value")
        if value is None or value == "":
            value = opt["value"]
        out.append({
            "slot": int(s["slot"]),
            "lottery_id": int(opt["lottery_id"]),
            "kind_type": int(opt["kind"]),
            "calc_type": int(opt["calc"]),
            "value": int(value),
        })
    return out


def grant_set(
    user_id: int,
    set_id: int,
    memoirs: list[dict],
    current_inventory: int,
) -> MemoirOutcome:
    """Grant 3 memoirs from a set at lv15 with chosen primary + subs.

    `memoirs` order is the 3 group entries in the set; each carries
    `primary_key`, `subs`. Pre-flights against the 999-row cap before
    invoking the shim.
    """
    if user_id <= 0:
        raise MemoirError("user_id must be positive")
    set_def = get_set(set_id)
    if set_def is None:
        raise MemoirError(f"unknown set_id: {set_id}")
    if len(memoirs) != len(set_def["memoirs"]):
        raise MemoirError(
            f"set {set_id} expects {len(set_def['memoirs'])} memoirs, "
            f"got {len(memoirs)}"
        )
    if current_inventory + len(memoirs) > MEMOIR_INVENTORY_CAP:
        raise MemoirError(
            f"inventory cap: {current_inventory} owned + {len(memoirs)} "
            f"new would exceed {MEMOIR_INVENTORY_CAP}"
        )

    payload_memoirs: list[dict] = []
    for mem_def, mem_in in zip(set_def["memoirs"], memoirs):
        _validate_memoir_payload(mem_in)
        if int(mem_in["group_id"]) != mem_def["group_id"]:
            raise MemoirError(
                f"memoir group_id mismatch: expected {mem_def['group_id']}, "
                f"got {mem_in['group_id']}"
            )
        primary = PRIMARY_BY_KEY[mem_in["primary_key"]]
        payload_memoirs.append({
            "parts_id": r40_part_id(mem_def["group_id"]),
            "parts_group_id": mem_def["group_id"],
            "parts_status_main_id": int(primary["main_id"]),
            "level": MEMOIR_MAX_LEVEL,
            "subs": _build_subs_for_shim(mem_in.get("subs") or []),
        })

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "grant_memoir_batch",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "memoirs": payload_memoirs,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return MemoirOutcome(
        succeeded=int(result.get("applied", len(payload_memoirs))),
        duration_ms=duration_ms,
        detail={
            "set_id": set_id,
            "set_name": set_def["name"],
            "granted_part_ids": [m["parts_id"] for m in payload_memoirs],
        },
    )


def upgrade_all(user_id: int) -> MemoirOutcome:
    """Set every owned memoir's Level to 15."""
    if user_id <= 0:
        raise MemoirError("user_id must be positive")
    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "upgrade_all_memoirs",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return MemoirOutcome(
        succeeded=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        detail={"max_level": MEMOIR_MAX_LEVEL},
    )


def fix_slots(user_id: int, user_parts_uuid: str, subs: list[dict]) -> MemoirOutcome:
    """Overwrite the sub-status rows on one existing memoir."""
    if user_id <= 0:
        raise MemoirError("user_id must be positive")
    if not user_parts_uuid:
        raise MemoirError("user_parts_uuid required")
    if not subs:
        raise MemoirError("subs must be non-empty")
    seen: set[int] = set()
    for s in subs:
        try:
            slot = int(s["slot"])
        except (KeyError, TypeError, ValueError):
            raise MemoirError("sub.slot missing/invalid")
        if slot < 1 or slot > 4 or slot in seen:
            raise MemoirError(f"invalid slot: {slot}")
        seen.add(slot)
        if s.get("sub_key") not in SUB_BY_KEY:
            raise MemoirError(f"unknown sub_key: {s.get('sub_key')!r}")

    payload = [{
        "user_parts_uuid": user_parts_uuid,
        "subs": _build_subs_for_shim(subs),
    }]

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "set_memoir_subs_batch",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "memoir_slots": payload,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return MemoirOutcome(
        succeeded=int(result.get("applied", 1)),
        duration_ms=duration_ms,
        detail={"user_parts_uuid": user_parts_uuid},
    )
