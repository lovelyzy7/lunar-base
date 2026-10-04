"""Read the playable-weapon catalog and grant weapons via the Go shim.

The catalog is built at startup by walking `EntityMWeaponEvolutionGroupTable`
and joining against `data/names/weapons.json` (produced by extract_names.py).
Every weapon belongs to exactly one evolution chain; we pick a single display
weapon per chain and skip R20 chains entirely (those are story-starter
weapons the player gets through normal play).

Sections (rendered in this order; alphabetical within each):
  - Recollections of Dusk: 12 chains, size-2, R40 base in 510011-550031.
    Display id = base_id + 1, the R50 non-refinable step-2 form.
  - Dark Memory: 21 chains, size-11. Display id = chain[-1], the R50 final
    "IV" form listed in the game-systems spec.
  - Subjugation: 11 chains, size-2. The 6 "Blackhorn X of Dust" weapons
    (R40 bases 400001-400051 every 10) plus the 5 named late-game Blackhorn
    weapons (Atrocity 500011, Grudge 340401, Hate 500051, Loathing 500031,
    Mourning 500041). Spite (500021) is intentionally excluded. Display id
    = base_id + 1, the R50 refinable step-2 form (these are obtain-once
    late-game weapons, so we grant the final form directly).
  - Other 4-Star: 347 chains, size-2, R40 base (excluding RoD and
    Subjugation). Display id = base_id (R40 step 1) so the player evolves
    to R50 themselves with materials.
  - 3-Star: 128 chains, size-2, R30 base (excluding the R30 base of Dark
    Memory chains, which are size-11). Display id = base_id (R30 step 1).

Total catalog: 519 weapons. R20 (55 chains) excluded.

Story unlocks: GrantWeapon auto-unlocks acquisition-type stories only. Per
in-game logic, stories 2-4 unlock at max-level / evolution / max-after-evo
milestones. For Dark Memory we grant the final R50 form, which has been
fully evolved (4th, 7th, 10th evolutions all happened conceptually), so
stories 2-4 are passed as ExtraStoryUnlocks to the shim. RoD/Subjugation
also grant the evolved R50, but per game convention their stories 2-4 are
deferred to the upgrade stage where we will track max-level explicitly.

The 999-row inventory cap is hard-enforced server-side: every grant call is
pre-flighted and the whole batch is refused if it would push the user over.
GrantWeapon does NOT self-skip already-owned weapons (each call inserts a
fresh UUID), so we filter requested ids against user_weapons here.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable

from web import config
from web.services import backup_service


BACKUP_REASON: Final[str] = "weapon-editor"

WEAPON_INVENTORY_CAP: Final[int] = 999

GROUP_RECOLLECTIONS: Final[str] = "recollections"
GROUP_DARK_MEMORY: Final[str] = "dark_memory"
GROUP_SUBJUGATION: Final[str] = "subjugation"
GROUP_OTHER_R40: Final[str] = "other_r40"
GROUP_R30: Final[str] = "r30"

GROUP_LABELS: Final[dict[str, str]] = {
    GROUP_RECOLLECTIONS: "Recollections of Dusk",
    GROUP_DARK_MEMORY: "Dark Memory",
    GROUP_SUBJUGATION: "Subjugation",
    GROUP_OTHER_R40: "Other 4-Star",
    GROUP_R30: "3-Star",
}

# The 21 R50-final Dark Memory weapon ids from the game-systems spec.
_DARK_MEMORY_FINAL_IDS: Final[frozenset[int]] = frozenset({
    410031, 410071, 410111, 410151,
    420031, 420071, 420111, 420151,
    430031, 430071, 430111, 430151,
    440031, 440071, 440111, 440151, 440191,
    450031, 450071, 450111, 450151,
})

# Subjugation R40-base ids. The first 6 are the original "Blackhorn X of Dust"
# series (text-bundle drops the "Blackhorn" prefix; we add it back below). The
# remaining 5 are the late-game named Blackhorn weapons (Atrocity, Grudge,
# Hate, Loathing, Mourning) — these already carry the "Blackhorn" prefix in
# the bundle. Spite (500021/22) is intentionally excluded per user spec.
_SUBJUGATION_R40_BASES: Final[frozenset[int]] = frozenset({
    # "Blackhorn X of Dust"
    400001, 400011, 400021, 400031, 400041, 400051,
    # Named Blackhorn weapons
    340401,  # Blackhorn Grudge
    500011,  # Blackhorn Atrocity
    500031,  # Blackhorn Loathing
    500041,  # Blackhorn Mourning
    500051,  # Blackhorn Hate
})

# RoD R40-base ids span 510011-550031 (12 chains).
_ROD_R40_MIN: Final[int] = 510011
_ROD_R40_MAX: Final[int] = 550031

# Story indices to unlock explicitly when granting a Dark Memory R50. Story 1
# auto-unlocks via the acquisition-type path inside GrantWeapon; 2-4 are tied
# to evolution milestones (after the 4th, 7th, and 10th evolutions). Since
# we grant the final form directly, those evolutions effectively happened.
_DARK_MEMORY_EXTRA_STORIES: Final[tuple[int, ...]] = (2, 3, 4)


class WeaponError(Exception):
    """Raised when the weapon catalog or shim invocation fails."""


@dataclass(frozen=True)
class WeaponRecord:
    id: int            # the display id (what we actually grant)
    name: str
    rarity: int        # rarity of the display id, not the base
    group_key: str
    chain_size: int    # for debugging / introspection
    extra_story_unlocks: tuple[int, ...] = ()  # passed to the shim per-weapon


@dataclass(frozen=True)
class BatchOutcome:
    succeeded: int
    duration_ms: int
    granted_ids: list[int]


_cache: list[WeaponRecord] | None = None


def _classify_chain(
    chain: list[dict],
    base_rec: dict | None,
) -> tuple[str, int] | None:
    """Return (group_key, display_id) for a chain, or None if it should be skipped.

    `chain` is sorted ascending by EvolutionOrder. `base_rec` is the names-file
    record for chain[0]['WeaponId'] (may be None if missing).
    """
    if not chain or base_rec is None:
        return None

    base_id = chain[0]["WeaponId"]
    final_id = chain[-1]["WeaponId"]
    base_rarity = base_rec.get("RarityType")
    size = len(chain)

    # Dark Memory: 11-step chain ending in the documented R50 ids.
    if size == 11 and final_id in _DARK_MEMORY_FINAL_IDS:
        return (GROUP_DARK_MEMORY, final_id)

    # Recollections of Dusk: size-2, R40 base in the documented range.
    if size == 2 and base_rarity == 40 and _ROD_R40_MIN <= base_id <= _ROD_R40_MAX:
        # Display the R50 step-2 form (base+1).
        return (GROUP_RECOLLECTIONS, base_id + 1)

    # Subjugation (Blackhorn): size-2, R40 base in the documented set.
    # Display the R50 refinable step-2 form (base+1).
    if size == 2 and base_rarity == 40 and base_id in _SUBJUGATION_R40_BASES:
        return (GROUP_SUBJUGATION, base_id + 1)

    # Other 4-Star: size-2, R40 base. Grant the R40 base so the player evolves.
    if size == 2 and base_rarity == 40:
        return (GROUP_OTHER_R40, base_id)

    # 3-Star: size-2, R30 base. Grant the R30 base.
    if size == 2 and base_rarity == 30:
        return (GROUP_R30, base_id)

    # R20 chains and anything unexpected: skip.
    return None


_GROUP_ORDER: Final[tuple[str, ...]] = (
    GROUP_RECOLLECTIONS,
    GROUP_DARK_MEMORY,
    GROUP_SUBJUGATION,
    GROUP_OTHER_R40,
    GROUP_R30,
)


def _sort_key(rec: WeaponRecord) -> tuple[int, str]:
    return (_GROUP_ORDER.index(rec.group_key), rec.name.lower())


def get_catalog() -> list[WeaponRecord]:
    """Return the full sorted catalog (519 weapons). Cached after first read."""
    global _cache
    if _cache is not None:
        return _cache

    names_path = config.NAMES_DIR / "weapons.json"
    evo_path = config.MASTERDATA_DIR / "EntityMWeaponEvolutionGroupTable.json"
    if not names_path.exists():
        raise WeaponError(
            f"weapons.json not found at {names_path}. Run {config.SETUP_SCRIPT} to extract names."
        )
    if not evo_path.exists():
        raise WeaponError(
            f"EntityMWeaponEvolutionGroupTable.json not found at {evo_path}. "
            f"Run {config.SETUP_SCRIPT} to dump master data."
        )

    try:
        names_data = json.loads(names_path.read_text(encoding="utf-8"))
        evo_data = json.loads(evo_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise WeaponError(f"failed to read weapon catalog source: {e}")

    by_id: dict[int, dict] = {int(r["id"]): r for r in names_data.get("records", [])}

    # Group evolution rows by WeaponEvolutionGroupId, sorted by EvolutionOrder.
    chains: dict[int, list[dict]] = {}
    for row in evo_data:
        gid = int(row["WeaponEvolutionGroupId"])
        chains.setdefault(gid, []).append(row)
    for gid, chain in chains.items():
        chain.sort(key=lambda x: int(x["EvolutionOrder"]))

    out: list[WeaponRecord] = []
    for gid, chain in chains.items():
        base_id = int(chain[0]["WeaponId"])
        base_rec = by_id.get(base_id)
        classified = _classify_chain(chain, base_rec)
        if classified is None:
            continue
        group_key, display_id = classified
        display_rec = by_id.get(display_id)
        if not display_rec or not display_rec.get("name_found"):
            # Refuse to ship synthetic-label rows. If this fires, the spec
            # changed and the catalog needs a closer look.
            continue
        extras: tuple[int, ...] = ()
        if group_key == GROUP_DARK_MEMORY:
            extras = _DARK_MEMORY_EXTRA_STORIES
        display_name = str(display_rec["name"])
        # The English text bundle resolves Subjugation names as "X of Dust"
        # without the "Blackhorn" title prefix the in-game UI shows. Add it
        # so the editor label matches the player's mental model.
        if group_key == GROUP_SUBJUGATION and not display_name.lower().startswith("blackhorn"):
            display_name = f"Blackhorn {display_name}"
        out.append(WeaponRecord(
            id=display_id,
            name=display_name,
            rarity=int(display_rec.get("RarityType", 0)),
            group_key=group_key,
            chain_size=len(chain),
            extra_story_unlocks=extras,
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
            "owned": rec.id in owned_ids,
        })
    out: list[dict] = []
    for key in _GROUP_ORDER:
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
        raise WeaponError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT} to build it (Go must be on PATH)."
        )


def _ensure_master_data() -> str:
    bin_path = config.find_master_data_bin()
    if bin_path is None:
        raise WeaponError(
            "Master-data binary not found under "
            f"{config.LUNAR_TEAR_DIR / 'server' / 'assets' / 'release'}. "
            "Weapon granting needs lunar-tear's encrypted master data."
        )
    return str(bin_path)


# Forced-play contents-story cutscenes keyed by the weapon that owns them.
# EntityMContentsStoryTable's Dark Memory entries are the client's
# "first acquisition" cutscenes: each owned DM weapon queues one, the client
# replays it on map entry / launch until it is registered as played, and a
# mass grant therefore soft-locks progression. We mark them played in the
# same transaction as the grant.
_dm_cutscene_by_weapon: dict[int, int] | None = None


def _contents_story_path() -> Path | None:
    for d in (config.MASTERDATA_DIR, config.LUNAR_TEAR_MASTERDATA_DIR):
        p = d / "EntityMContentsStoryTable.json"
        if p.exists():
            return p
    return None


def _dark_memory_cutscene_ids(weapon_ids: Iterable[int]) -> list[int]:
    """ContentsStoryId of the forced-play cutscene for each given weapon.

    Returns [] when the contents-story table is unavailable or the weapons
    have no cutscene, so granting never fails just because the optional
    mapping is missing.
    """
    global _dm_cutscene_by_weapon
    if _dm_cutscene_by_weapon is None:
        _dm_cutscene_by_weapon = {}
        path = _contents_story_path()
        if path is not None:
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rows = []
            for rec in rows:
                # Dark Memory entries: forced-play, unlocked by weapon
                # ownership. Anything else is left to the player.
                if not rec.get("IsForcedPlay"):
                    continue
                if int(rec.get("ContentsStoryUnlockConditionType", 0)) != 1:
                    continue
                weapon_id = rec.get("ConditionValue")
                story_id = rec.get("ContentsStoryId")
                if isinstance(weapon_id, int) and isinstance(story_id, int):
                    _dm_cutscene_by_weapon[weapon_id] = story_id
    return sorted({_dm_cutscene_by_weapon[wid] for wid in weapon_ids if wid in _dm_cutscene_by_weapon})


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
        raise WeaponError(
            f"grant shim returned non-JSON output (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise WeaponError(msg)


def _check_inventory_capacity(current: int, to_grant: int) -> None:
    """Raise WeaponError if granting would push the user over the 999 cap."""
    projected = current + to_grant
    if projected > WEAPON_INVENTORY_CAP:
        raise WeaponError(
            f"You have {current} / {WEAPON_INVENTORY_CAP} weapons. "
            f"Granting {to_grant} would push you to {projected}. "
            f"Remove {projected - WEAPON_INVENTORY_CAP} weapons in-game before retrying."
        )


def grant_weapons(
    user_id: int,
    weapon_ids: list[int],
    owned_ids: set[int],
    inventory_count: int,
) -> BatchOutcome:
    """Grant the given weapon_ids to the user via one shim invocation.

    Already-owned weapons are filtered out client-side here because GrantWeapon
    does NOT self-skip — each call adds a fresh UUID. We also pre-flight the
    999-row inventory cap and refuse the entire batch if it would overflow.

    Each granted weapon may carry extra_story_unlocks (e.g. Dark Memory R50,
    where stories 2-4 unlock at evolution milestones we skipped by granting
    the final form directly). The shim runs the GrantWeaponStoryUnlock calls
    inside the same UpdateUser transaction.

    Dark Memory weapons also queue a forced-play acquisition cutscene; the
    shim marks those cutscenes played in the same transaction so a mass grant
    can never leave the map-progression soft-lock behind.
    """
    if user_id <= 0:
        raise WeaponError("user_id must be positive")
    if not weapon_ids:
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])

    by_id = {rec.id: rec for rec in get_catalog()}
    requested = [wid for wid in weapon_ids if wid in by_id and wid not in owned_ids]
    if not requested:
        # Either nothing matched the catalog or every requested weapon is owned.
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])

    _check_inventory_capacity(inventory_count, len(requested))

    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    _invoke_shim({
        "action": "grant_weapon_batch",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "weapons": [
            {
                "weapon_id": wid,
                "extra_story_unlocks": list(by_id[wid].extra_story_unlocks),
            }
            for wid in requested
        ],
        # Mark the granted Dark Memory cutscenes as played (self-skips any
        # that are already registered).
        "contents_story_ids": _dark_memory_cutscene_ids(requested),
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return BatchOutcome(
        succeeded=len(requested),
        duration_ms=duration_ms,
        granted_ids=list(requested),
    )


def grant_all_missing(
    user_id: int,
    owned_ids: set[int],
    inventory_count: int,
) -> BatchOutcome:
    """Grant every catalog weapon the user doesn't yet own."""
    missing = [rec.id for rec in get_catalog() if rec.id not in owned_ids]
    if not missing:
        return BatchOutcome(succeeded=0, duration_ms=0, granted_ids=[])
    return grant_weapons(user_id, missing, owned_ids, inventory_count)
