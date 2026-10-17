"""Mutate the game database via the lunar-base-grant Go shim.

The shim wraps lunar-tear's UpdateUser + GrantPossession so we go through the
exact code paths the game server uses (transactional save, diff system, WAL).

Every public function here takes one auto-backup before invoking the shim.
Batch operations call the shim once with many grants packed into a single
UpdateUser transaction, so MAX ALL completes in seconds rather than minutes.
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
from web.services import backup_service, names_service


# lunar-tear's model.PossessionType enum values. Keep in sync with
# lunar-tear/server/internal/model/possession.go.
POSSESSION_MATERIAL: Final[int] = 5
POSSESSION_CONSUMABLE: Final[int] = 6
POSSESSION_PAID_GEM: Final[int] = 11
POSSESSION_FREE_GEM: Final[int] = 12
POSSESSION_IMPORTANT: Final[int] = 13

STACKABLE_TYPES: Final[frozenset[int]] = frozenset({
    POSSESSION_MATERIAL,
    POSSESSION_CONSUMABLE,
    POSSESSION_PAID_GEM,
    POSSESSION_FREE_GEM,
    POSSESSION_IMPORTANT,
})

BACKUP_REASON: Final[str] = "item-editor"

# Cap individual grant counts at int32-max-ish so the shim never overflows.
_MAX_GRANT_COUNT: Final[int] = 2_000_000_000


class GrantError(Exception):
    """Raised when the shim refuses or fails a grant."""


@dataclass(frozen=True)
class GrantPlanItem:
    possession_type: int
    possession_id: int
    count: int


@dataclass(frozen=True)
class BatchOutcome:
    succeeded: int
    failed: int
    duration_ms: int
    grants: list[GrantPlanItem]


def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise GrantError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT}, or drop a prebuilt into tools/grant/prebuilt/"
            f"{sys.platform}-{platform.machine().lower()} (see tools/grant/README.md)."
        )


def _validate_grant(g: GrantPlanItem) -> None:
    if g.possession_type not in STACKABLE_TYPES:
        raise GrantError(f"unsupported possession_type {g.possession_type}")
    if g.count <= 0:
        raise GrantError("count must be positive")
    if g.count > _MAX_GRANT_COUNT:
        raise GrantError(f"count exceeds max {_MAX_GRANT_COUNT}")


def _invoke_shim(payload: dict) -> None:
    proc = subprocess.run(
        [str(config.GRANT_EXE_PATH)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()

    try:
        result = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        raise GrantError(
            f"grant shim returned non-JSON output (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )

    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise GrantError(msg)


def grant_one(user_id: int, possession_type: int, possession_id: int, count: int) -> None:
    """Grant a single possession item. Takes one auto-backup."""
    if user_id <= 0:
        raise GrantError("user_id must be positive")
    g = GrantPlanItem(possession_type, possession_id, count)
    _validate_grant(g)

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    _invoke_shim({
        "action": "grant_possession",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "possession_type": g.possession_type,
        "possession_id": g.possession_id,
        "count": g.count,
    })


def grant_batch(user_id: int, grants: list[GrantPlanItem]) -> BatchOutcome:
    """Apply many grants in a single UpdateUser transaction. One auto-backup at the top."""
    if user_id <= 0:
        raise GrantError("user_id must be positive")
    if not grants:
        return BatchOutcome(succeeded=0, failed=0, duration_ms=0, grants=[])

    for g in grants:
        _validate_grant(g)

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "grant_batch",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "grants": [
            {"possession_type": g.possession_type, "possession_id": g.possession_id, "count": g.count}
            for g in grants
        ],
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return BatchOutcome(
        succeeded=len(grants),
        failed=0,
        duration_ms=duration_ms,
        grants=list(grants),
    )


# --- MAX ALL plan computation ---------------------------------------------
#
# These build a list of GrantPlanItem entries. The actual write happens via
# grant_batch above. Rules are evaluated in order and first-match-wins for
# name patterns; explicit ID rules take precedence over name patterns.


def _consumable_max_amount(item_id: int, name: str) -> int:
    """Return the count to grant for one consumable in MAX ALL, or 0 to skip."""
    n = name.lower()
    # Explicit IDs first.
    if item_id == 1:
        return 50_000_000  # Gold
    if item_id in (2, 3, 24):
        return 5_000        # Medal, Rare Medal, Bookmark (override the "medal" rule)
    if item_id == 9001:
        return 50_000       # Mama Points (Mom Points enum)
    # Name patterns, first match wins.
    if "ticket" in n or "medal" in n or "coin" in n:
        return 50_000
    if "fragment" in n or "boost" in n:
        return 5_000
    if "shard" in n:
        return 5_000
    return 0


def _material_max_amount(item_id: int, name: str) -> int:
    """Return the count to grant for one material in MAX ALL, or 0 to skip."""
    # Hard exclusions first.
    if item_id in (999001, 999002, 999003, 999004):
        return 0
    n = name.lower()
    if "longing flicker" in n:
        return 0
    if "recalling light" in n:
        return 0
    # Specific patterns, first match wins.
    if "awakening stone" in n or "a. stone" in n:
        return 5
    if any(p in n for p in ("battle text", "b. text", "peaceful text", "warfare text", "w. text")):
        return 100
    if "enhancement" in n:
        return 50_000
    if "slab fragment" in n or "antler bit" in n:
        return 20_000
    # Catch-all
    return 5_000


def build_max_consumables_plan() -> list[GrantPlanItem]:
    plan: list[GrantPlanItem] = []
    for item_id, name in names_service.get_names("consumables").items():
        amount = _consumable_max_amount(item_id, name)
        if amount > 0:
            plan.append(GrantPlanItem(POSSESSION_CONSUMABLE, item_id, amount))
    return plan


def build_max_materials_plan() -> list[GrantPlanItem]:
    plan: list[GrantPlanItem] = []
    for item_id, name in names_service.get_names("materials").items():
        amount = _material_max_amount(item_id, name)
        if amount > 0:
            plan.append(GrantPlanItem(POSSESSION_MATERIAL, item_id, amount))
    return plan


def build_remnant_plan(owned_ids: set[int]) -> list[GrantPlanItem]:
    """Grant 1 of each Important Item with 'Remnant' in the name that the user lacks.

    Substring match handles both 'Remnant: ...' and 'Remnants: ...' which the
    game uses interchangeably for chapter/story-key items.
    """
    plan: list[GrantPlanItem] = []
    for item_id, name in names_service.get_names("important_items").items():
        if item_id in owned_ids:
            continue
        if "remnant" in name.lower():
            plan.append(GrantPlanItem(POSSESSION_IMPORTANT, item_id, 1))
    return plan
