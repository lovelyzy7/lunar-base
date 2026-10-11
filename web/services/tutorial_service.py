"""Tutorial prep: mark feature tutorials as completed so a tutorial that
freezes the client is never played, while the features it introduces still get
unlocked. Quests, story progress and rewards are not touched.

Writes go through the Go shim (`prep_tutorials` / `clear_tutorials`) inside
lunar-tear's own UpdateUser transaction; a backup is taken first, exactly like
the other editors.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from typing import Final

from web import config
from web.services import backup_service

BACKUP_REASON: Final[str] = "tutorial-prep"

# (tutorial_type, completion phase, label). Phases are the values a normally
# played save reports. `core` unlocks the early-game features (menu, deck,
# enhance, companion, parts, explore, hard modes, events, dailies); `combat`
# covers the battle tutorials. Everything else is left untouched until it has
# been verified against the client.
PRESETS: Final[dict[str, tuple[tuple[int, int, str], ...]]] = {
    "core": (
        (2, 20, "Main menu (Mama)"),
        (3, 30, "Menu second step"),
        (7, 10, "Enhance"),
        (8, 40, "Companion"),
        (9, 10, "Parts / debris"),
        (10, 20, "Explore"),
        (12, 10, "Main quest: Hard"),
        (13, 10, "Main quest: Very Hard"),
        (14, 10, "Event quests"),
        (19, 10, "Daily quests"),
    ),
    "combat": (
        (4, 10, "Battle: weapon skill"),
        (5, 10, "Battle: costume skill"),
        (6, 10, "Black bird"),
    ),
    "mama": (
        (25, 50, "Mama's Room"),
        (26, 99999, "Mama's Room (forced scene gate)"),
        (24, 99999, "Flying Mama"),
        (30, 99999, "Fruit in Mama's Room"),
    ),
    "cage": (
        (202, 99999, "Portal Cage: chapter"),
        (27, 99999, "Portal Cage: access icon"),
        (28, 99999, "Portal Cage: daily challenge"),
        (29, 99999, "Portal Cage: daily summons"),
        (31, 99999, "Portal Cage 31"),
        (32, 99999, "Portal Cage 32"),
        (33, 99999, "Portal Cage 33"),
        (34, 99999, "Portal Cage 34"),
        (35, 99999, "Portal Cage 35"),
        (36, 99999, "Portal Cage 36"),
        (37, 99999, "Portal Cage 37"),
        (38, 99999, "Portal Cage 38"),
        (39, 99999, "Portal Cage 39"),
        (1901, 99999, "Portal Cage scene 2230"),
        (1902, 99999, "Portal Cage scene 2231"),
        (1906, 99999, "Portal Cage scene 2232 (consumes an item)"),
        (1907, 99999, "Portal Cage scene 2232"),
    ),
}

PRESET_LABELS: Final[dict[str, str]] = {
    "core": "Core features",
    "combat": "Battle / scene tutorials",
    "mama": "Mama's Room / portal cage",
    "cage": "Portal Cage / black bird",
}

# choiceId -> companionId (mirrors lunar-tear's tutorialCompanionChoices).
COMPANION_CHOICES: Final[dict[int, int]] = {1: 2, 2: 1, 3: 7, 4: 10}
DEFAULT_COMPANION_CHOICE: Final[int] = 2

# type -> (phase, label, preset key), built from PRESETS.
PHASES: Final[dict[int, tuple[int, str, str]]] = {
    t: (phase, label, key)
    for key, rows in PRESETS.items()
    for (t, phase, label) in rows
}


class TutorialError(Exception):
    """Raised when the tutorial state cannot be read or written."""


@dataclass(frozen=True)
class PrepOutcome:
    written: tuple[int, ...]
    skipped: tuple[int, ...]
    deck_created: bool
    companion_id: int
    duration_ms: int


def _state_path() -> object:
    return config.GAME_DB_PATH


def read_state(user_id: int) -> dict[int, tuple[int, int]]:
    """user_tutorials rows for the user: {type: (progress_phase, choice_id)}."""
    if user_id <= 0:
        raise TutorialError("user_id must be positive")
    path = _state_path()
    if not path.exists():
        raise TutorialError(f"Game database not found: {path}")
    uri = f"{path.as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise TutorialError(f"failed to open game.db: {e}")
    try:
        cur = conn.execute(
            "SELECT tutorial_type, progress_phase, choice_id "
            "FROM user_tutorials WHERE user_id=?",
            (user_id,),
        )
        return {int(t): (int(p), int(c)) for t, p, c in cur.fetchall()}
    except sqlite3.Error as e:
        raise TutorialError(f"failed to read user_tutorials: {e}")
    finally:
        conn.close()


def preset_groups(user_id: int) -> list[dict]:
    """Preset rows enriched with the user's current state (for the template)."""
    state = read_state(user_id)
    groups: list[dict] = []
    for key, rows in PRESETS.items():
        items = []
        for t, phase, label in rows:
            current = state.get(t, (0, 0))[0]
            items.append({
                "type": t,
                "phase": phase,
                "label": label,
                "current": current,
                "done": current >= phase,
            })
        groups.append({"key": key, "label": PRESET_LABELS[key], "rows": items})
    return groups


def preview(user_id: int, types: list[int]) -> list[dict]:
    """What prep would change, without writing anything."""
    state = read_state(user_id)
    out = []
    for t in types:
        if t not in PHASES:
            raise TutorialError(f"unknown tutorial type {t}")
        phase, label, _key = PHASES[t]
        current = state.get(t, (0, 0))[0]
        out.append({
            "type": t,
            "label": label,
            "current": current,
            "target": phase,
            "action": "write" if current < phase else "skip",
        })
    return out


def _clean_types(types: list[int]) -> list[int]:
    cleaned: list[int] = []
    for t in types:
        t = int(t)
        if t not in PHASES:
            raise TutorialError(f"unknown tutorial type {t}")
        if t not in cleaned:
            cleaned.append(t)
    if not cleaned:
        raise TutorialError("no tutorial types selected")
    return cleaned


def _ensure_shim() -> None:
    if not config.GRANT_EXE_PATH.is_file():
        raise TutorialError(
            f"grant shim not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT} to build it."
        )


def _ensure_master_data() -> str:
    bin_path = config.find_master_data_bin()
    if bin_path is None:
        raise TutorialError(
            "Master-data binary not found under "
            f"{config.LUNAR_TEAR_DIR / 'server' / 'assets' / 'release'}. "
            "Granting the companion tutorial reward needs lunar-tear's "
            "encrypted master data."
        )
    return str(bin_path)


def _invoke(payload: dict) -> dict:
    proc = subprocess.run(
        [str(config.GRANT_EXE_PATH)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=300,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    try:
        result = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        raise TutorialError(
            f"grant shim returned non-JSON output (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise TutorialError(msg)
    return result


def prep(
    user_id: int,
    types: list[int],
    companion_choice: int = DEFAULT_COMPANION_CHOICE,
    create_deck: bool = True,
    phase_overrides: dict[int, int] | None = None,
) -> PrepOutcome:
    """Mark the given tutorials completed (+ deck + companion reward).

    ``phase_overrides`` lets a caller write a non-preset phase for a type
    (single-type experiments); the type itself must still be a known preset.
    """
    if user_id <= 0:
        raise TutorialError("user_id must be positive")
    selected = _clean_types(types)
    if companion_choice not in COMPANION_CHOICES:
        raise TutorialError(f"invalid companion choice {companion_choice}")
    overrides: dict[int, int] = {}
    for t, phase in (phase_overrides or {}).items():
        t, phase = int(t), int(phase)
        if t not in PHASES:
            raise TutorialError(f"unknown tutorial type {t}")
        if phase <= 0:
            raise TutorialError(f"invalid phase {phase} for tutorial {t}")
        overrides[t] = phase

    def phase_of(t: int) -> int:
        return overrides.get(t, PHASES[t][0])

    grant_companion = 8 in selected
    _ensure_shim()
    master_data = _ensure_master_data() if grant_companion else ""

    payload = {
        "action": "prep_tutorials",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "tutorials": [
            {
                "type": t,
                "phase": phase_of(t),
                "choice_id": companion_choice if t == 8 else 0,
            }
            for t in selected
        ],
        "create_deck": bool(create_deck),
        "grant_companion": grant_companion,
    }
    if master_data:
        payload["master_data_path"] = master_data

    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke(payload)
    duration_ms = int((time.monotonic() - started) * 1000)
    return PrepOutcome(
        written=tuple(int(t) for t in result.get("tutorial_types", [])),
        skipped=tuple(int(t) for t in result.get("tutorial_skipped", [])),
        deck_created=bool(result.get("deck_created", False)),
        companion_id=int(result.get("companion_id", 0)),
        duration_ms=duration_ms,
    )


def clear(user_id: int, types: list[int]) -> tuple[int, ...]:
    """Delete the given tutorial rows (revert a prep)."""
    if user_id <= 0:
        raise TutorialError("user_id must be positive")
    selected = _clean_types(types)
    _ensure_shim()
    backup_service.create_backup(reason=BACKUP_REASON)
    result = _invoke({
        "action": "clear_tutorials",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "tutorial_types": selected,
    })
    return tuple(int(t) for t in result.get("tutorial_types", []))
