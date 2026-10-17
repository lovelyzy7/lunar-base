"""Read the playable-costume catalog and grant costumes via the Go shim.

Catalog source-of-truth: `data/names/playable_costumes.json` produced by
tools/extract_names.py. Pre-curated to the 280 costumes assigned to playable
characters; we further drop R20 (story-starter) costumes here, leaving the
258 R30 + R40 costumes the editor exposes.

Sort order (matches the editor UI):
  4-star (R40)
    1. Recollections of Dusk  -- name starts with "Frozen-Heart"
    2. Dark Memory            -- name starts with "Reborn"
    3. Other R40              -- alphabetical
  3-star (R30) -- alphabetical
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


BACKUP_REASON: Final[str] = "costume-editor"

# Group keys used in the editor template's section dividers.
GROUP_RECOLLECTIONS: Final[str] = "recollections"
GROUP_DARK_MEMORY: Final[str] = "dark_memory"
GROUP_OTHER_R40: Final[str] = "other_r40"
GROUP_R30: Final[str] = "r30"

GROUP_LABELS: Final[dict[str, str]] = {
    GROUP_RECOLLECTIONS: "Recollections of Dusk",
    GROUP_DARK_MEMORY: "Dark Memory",
    GROUP_OTHER_R40: "Other 4-Star",
    GROUP_R30: "3-Star",
}


class CostumeError(Exception):
    """Raised when the costume catalog or shim invocation fails."""


@dataclass(frozen=True)
class CostumeRecord:
    id: int
    name: str
    rarity: int
    character_id: int
    character_name: str
    group_key: str


@dataclass(frozen=True)
class BatchOutcome:
    succeeded: int
    duration_ms: int
    granted_ids: list[int]


_cache: list[CostumeRecord] | None = None


def _classify(name: str, rarity: int) -> str:
    n = name.lower()
    if rarity == 40 and (
        n.startswith("frozen-heart")
        or n.startswith("frozen heart")
        or n.startswith("f-h ")
    ):
        return GROUP_RECOLLECTIONS
    if rarity == 40 and n.startswith("reborn"):
        return GROUP_DARK_MEMORY
    if rarity == 40:
        return GROUP_OTHER_R40
    return GROUP_R30


def _sort_key(rec: CostumeRecord) -> tuple[int, int, str]:
    # 4-star above 3-star -> (rarity_rank, group_rank, name)
    rarity_rank = 0 if rec.rarity == 40 else 1
    group_rank = {
        GROUP_RECOLLECTIONS: 0,
        GROUP_DARK_MEMORY: 1,
        GROUP_OTHER_R40: 2,
        GROUP_R30: 3,
    }[rec.group_key]
    return (rarity_rank, group_rank, rec.name.lower())


def get_catalog() -> list[CostumeRecord]:
    """Return the full sorted catalog (R30 + R40 only). Cached after first read."""
    global _cache
    if _cache is not None:
        return _cache

    path = config.NAMES_DIR / "playable_costumes.json"
    if not path.exists():
        raise CostumeError(
            f"playable_costumes.json not found at {path}. "
            f"Run {config.SETUP_SCRIPT} to extract names."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise CostumeError(f"failed to read {path}: {e}")

    out: list[CostumeRecord] = []
    for r in data.get("records", []):
        rarity = r.get("RarityType")
        if rarity not in (30, 40):
            continue
        out.append(CostumeRecord(
            id=int(r["id"]),
            name=str(r["name"]),
            rarity=int(rarity),
            character_id=int(r.get("CharacterId", 0)),
            character_name=str(r.get("character_name", "")),
            group_key=_classify(str(r["name"]), int(rarity)),
        ))
    out.sort(key=_sort_key)
    _cache = out
    return out


def grouped_catalog(owned_ids: set[int]) -> list[dict]:
    """Return groups in display order, each containing its rows.

    Each row carries an `owned` flag the template uses to grey it out.
    """
    catalog = get_catalog()
    groups: dict[str, list[dict]] = {k: [] for k in GROUP_LABELS}
    for rec in catalog:
        groups[rec.group_key].append({
            "id": rec.id,
            "name": rec.name,
            "rarity": rec.rarity,
            "character_id": rec.character_id,
            "character_name": rec.character_name,
            "owned": rec.id in owned_ids,
        })
    out: list[dict] = []
    for key in (GROUP_RECOLLECTIONS, GROUP_DARK_MEMORY, GROUP_OTHER_R40, GROUP_R30):
        rows = groups[key]
        if not rows:
            continue
        out.append({
            "key": key,
            "label": GROUP_LABELS[key],
            "rows": rows,
            "owned_count": sum(1 for r in rows if r["owned"]),
            "total_count": len(rows),
        })
    return out


def all_catalog_ids() -> set[int]:
    return {rec.id for rec in get_catalog()}


def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise CostumeError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT}, or drop a prebuilt into tools/grant/prebuilt/"
            f"{sys.platform}-{platform.machine().lower()} (see tools/grant/README.md)."
        )


def _ensure_master_data() -> str:
    bin_path = config.find_master_data_bin()
    if bin_path is None:
        raise CostumeError(
            "Master-data binary not found under "
            f"{config.LUNAR_TEAR_DIR / 'server' / 'assets' / 'release'}. "
            "Costume granting needs lunar-tear's encrypted master data."
        )
    return str(bin_path)


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
        raise CostumeError(
            f"grant shim returned non-JSON output (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise CostumeError(msg)


def grant_costumes(user_id: int, costume_ids: list[int]) -> BatchOutcome:
    """Grant the given costume_ids to the user via one shim invocation.

    Already-owned costumes are filtered out client-side here so we don't pay
    the shim's no-op work; lunar-tear's GrantCostume also self-skips, so this
    is only an optimization.
    """
    if user_id <= 0:
        raise CostumeError("user_id must be positive")
    if not costume_ids:
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])

    valid = all_catalog_ids()
    requested = [cid for cid in costume_ids if cid in valid]
    if not requested:
        raise CostumeError("no recognised costume ids in request")

    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "grant_costume_batch",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "costume_ids": requested,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return BatchOutcome(
        succeeded=len(requested),
        duration_ms=duration_ms,
        granted_ids=list(requested),
    )


def grant_all_missing(user_id: int, owned_ids: set[int]) -> BatchOutcome:
    """Grant every catalog costume the user doesn't yet have."""
    missing = [rec.id for rec in get_catalog() if rec.id not in owned_ids]
    if not missing:
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])
    return grant_costumes(user_id, missing)


def update_costume_karma(
    user_id: int,
    costume_karma: dict[int, dict[int, int]],
) -> BatchOutcome:
    """Write per-costume karma OddsNumbers via the shim.

    `costume_karma` maps costume_id -> {slot_number -> odds_number}. The
    shim looks each costume up by id, validates the slot is already
    unlocked, and writes the OddsNumber. Slots not yet unlocked are
    silently skipped (run Upgrade All Costumes first to unlock them for
    every owned SSR).
    """
    if user_id <= 0:
        raise CostumeError("user_id must be positive")
    if not costume_karma:
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])

    payload_costumes = [
        {
            "costume_id": int(cid),
            "karma": {str(slot): int(odds) for slot, odds in slots.items()},
        }
        for cid, slots in costume_karma.items()
        if slots
    ]
    if not payload_costumes:
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "set_costume_karma_batch",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "costume_karma": payload_costumes,
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return BatchOutcome(
        succeeded=len(payload_costumes),
        duration_ms=duration_ms,
        granted_ids=[c["costume_id"] for c in payload_costumes],
    )
