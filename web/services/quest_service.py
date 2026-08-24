"""Quest catalog (from data/names) + faithful quest-clearing via the Go shim.

Display tree: built from `data/names/main_quests.json` and `event_quests.json`,
which already carry the full chapter -> difficulty -> quest hierarchy with names
(matching the in-game structure, incl. Easy/Normal/Hard tiers).

Cleared status: read directly from game.db's `user_quests` table
(quest_state_type == 2) -- fast, no catalog load on page view.

Clearing: the `clear_quests` shim action replays lunar-tear's real finish flow
(HandleQuestFinish / HandleEventQuestFinish) inside one UpdateUser transaction --
granting first-clear + mission + drop rewards, marking cleared, advancing the
story pointer, and recording side-story scenarios. Already-cleared quests are
skipped server-side so drops are never re-rolled.
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

BACKUP_REASON: Final[str] = "quest-editor"


class QuestError(Exception):
    """Raised when the quest catalog or shim invocation fails."""


@dataclass(frozen=True)
class WriteOutcome:
    applied: int
    duration_ms: int
    applied_ids: tuple[int, ...] = ()


# (section_key, names file) in display order.
_SECTIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("main", "Main Story", "main_quests.json"),
    ("event", "Events", "event_quests.json"),
)

_tree_cache: dict[str, list[dict]] | None = None


def _load_records(filename: str) -> list[dict]:
    path = config.NAMES_DIR / filename
    if not path.exists():
        raise QuestError(
            f"{filename} not found at {path}. Run {config.SETUP_SCRIPT} to extract names."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise QuestError(f"failed to read {path}: {e}")
    if isinstance(data, dict):
        return data.get("records", [])
    return data if isinstance(data, list) else []


def _chapter_from_record(rec: dict, section: str) -> dict | None:
    """Turn one names-file chapter record into a display chapter, or None if it
    has no playable quests."""
    tiers: list[dict] = []
    for diff in rec.get("difficulties", []):
        quests = [
            {
                "quest_id": int(q["quest_id"]),
                "name": str(q.get("quest_name") or f"Quest {q['quest_id']}"),
            }
            for q in diff.get("quests", [])
            if q.get("quest_id")
        ]
        if quests:
            tiers.append({"label": str(diff.get("difficulty_label") or ""), "quests": quests})
    if not tiers:
        return None

    if section == "main":
        chapter_id = int(rec.get("MainQuestChapterId", rec.get("id", 0)))
        subtitle = str(rec.get("season_title") or "")
        sort = (
            int(rec.get("MainQuestSeasonId", 0)),
            int(rec.get("route_sort_order", 0)),
            int(rec.get("chapter_sort_order", 0)),
        )
    else:
        chapter_id = int(rec.get("EventQuestChapterId", rec.get("id", 0)))
        subtitle = ""
        sort = (int(rec.get("DisplaySortOrder", 0)), 0, int(rec.get("id", 0)))

    return {
        "chapter_id": chapter_id,
        "name": str(rec.get("name") or f"Chapter {chapter_id}"),
        "subtitle": subtitle,
        "event_type": int(rec.get("EventQuestType", 0)) if section == "event" else 0,
        "tiers": tiers,
        "_sort": sort,
    }


def _build_tree() -> dict[str, list[dict]]:
    global _tree_cache
    if _tree_cache is not None:
        return _tree_cache
    tree: dict[str, list[dict]] = {}
    for key, _label, filename in _SECTIONS:
        chapters: list[dict] = []
        for rec in _load_records(filename):
            ch = _chapter_from_record(rec, key)
            if ch is not None:
                chapters.append(ch)
        chapters.sort(key=lambda c: c["_sort"])
        for c in chapters:
            c.pop("_sort", None)
        tree[key] = chapters
    _tree_cache = tree
    return tree


def cleared_quest_ids(user_id: int) -> set[int]:
    """quest_ids the user has cleared (user_quests.quest_state_type == 2)."""
    if not config.GAME_DB_PATH.exists():
        raise QuestError(f"Game database not found: {config.GAME_DB_PATH}")
    uri = f"{config.GAME_DB_PATH.as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise QuestError(f"failed to open game.db: {e}")
    try:
        cur = conn.execute(
            "SELECT quest_id FROM user_quests WHERE user_id=? AND quest_state_type=2",
            (user_id,),
        )
        return {int(row[0]) for row in cur.fetchall()}
    except sqlite3.Error as e:
        raise QuestError(f"failed to read user_quests: {e}")
    finally:
        conn.close()


# Event sub-tab labels by EventQuestType. The descriptive ones (Record,
# Variation, Abyss Tower, Chambers of Dusk, Fate Board) match the in-game
# category names; the generic-named types (3/6/7/8/9, whose chapters are just
# "Event Quest Chapter NNNNN") get a reasonable label -- rename freely.
_EVENT_TYPE_LABELS: Final[dict[int, str]] = {
    1: "Record",
    2: "Variation",
    3: "Limited Story",
    4: "Daily",
    5: "Guerrilla",
    6: "Special",
    7: "Challenge",
    8: "Story Events",
    9: "Bonus",
    10: "Abyss Tower",
    11: "Chambers of Dusk",
    12: "Fate Board",
}


def _event_type_label(type_id: int, names: list[str]) -> str:
    """Tab label for an event type. Known types use the curated name; unknown
    types fall back to the common 'Prefix:' shared by their chapter names, then
    to a generic label."""
    if type_id in _EVENT_TYPE_LABELS:
        return _EVENT_TYPE_LABELS[type_id]
    import collections

    prefixes = [n.split(":")[0].strip() for n in names if ":" in n]
    if prefixes:
        common, count = collections.Counter(prefixes).most_common(1)[0]
        if count >= max(2, len(names) // 3) and not any(c.isdigit() for c in common):
            return common
    return f"Event Type {type_id}"


def _merge_chapter(ch: dict, cleared: set[int]) -> dict:
    """Attach per-quest cleared flags + chapter cleared/total counts."""
    ch_total = 0
    ch_cleared = 0
    out_tiers: list[dict] = []
    for tier in ch["tiers"]:
        rows = []
        for q in tier["quests"]:
            is_cleared = q["quest_id"] in cleared
            ch_total += 1
            if is_cleared:
                ch_cleared += 1
            rows.append({"quest_id": q["quest_id"], "name": q["name"], "cleared": is_cleared})
        out_tiers.append({"label": tier["label"], "quests": rows})
    return {
        "chapter_id": ch["chapter_id"],
        "name": ch["name"],
        "subtitle": ch["subtitle"],
        "cleared_count": ch_cleared,
        "total_count": ch_total,
        "tiers": out_tiers,
    }


def grouped_quests(user_id: int) -> dict:
    """Tab-ready data for the template:

        {
          "main_chapters": [chapter, ...],
          "event_groups": [{key, label, cleared, total, chapters:[chapter, ...]}, ...],
          "cleared_count": int, "total_count": int,
        }

    where each chapter = {chapter_id, name, subtitle, cleared_count,
    total_count, tiers:[{label, quests:[{quest_id, name, cleared}]}]}.
    """
    tree = _build_tree()
    cleared = cleared_quest_ids(user_id)
    grand_cleared = 0
    grand_total = 0

    main_chapters: list[dict] = []
    for ch in tree.get("main", []):
        m = _merge_chapter(ch, cleared)
        main_chapters.append(m)
        grand_cleared += m["cleared_count"]
        grand_total += m["total_count"]

    # Group event chapters by EventQuestType (preserving each type's order).
    by_type: dict[int, list[dict]] = {}
    names_by_type: dict[int, list[str]] = {}
    for ch in tree.get("event", []):
        t = int(ch.get("event_type", 0))
        by_type.setdefault(t, []).append(ch)
        names_by_type.setdefault(t, []).append(ch["name"])

    event_groups: list[dict] = []
    for t in sorted(by_type):
        chapters: list[dict] = []
        g_cleared = 0
        g_total = 0
        for ch in by_type[t]:
            m = _merge_chapter(ch, cleared)
            chapters.append(m)
            g_cleared += m["cleared_count"]
            g_total += m["total_count"]
        grand_cleared += g_cleared
        grand_total += g_total
        event_groups.append({
            "key": f"evt{t}",
            "label": _event_type_label(t, names_by_type[t]),
            "cleared": g_cleared,
            "total": g_total,
            "chapters": chapters,
        })

    return {
        "main_chapters": main_chapters,
        "event_groups": event_groups,
        "cleared_count": grand_cleared,
        "total_count": grand_total,
    }


def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise QuestError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT} to build it (Go must be on PATH)."
        )


def _ensure_master_data() -> str:
    bin_path = config.find_master_data_bin()
    if bin_path is None:
        raise QuestError(
            "Master-data binary not found under "
            f"{config.LUNAR_TEAR_DIR / 'server' / 'assets' / 'release'}. "
            "Clearing quests needs lunar-tear's encrypted master data."
        )
    return str(bin_path)


def _invoke_shim(payload: dict) -> dict:
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
        raise QuestError(
            f"grant shim returned non-JSON output (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        msg = result.get("error") or stderr or f"shim exited {proc.returncode}"
        raise QuestError(msg)
    return result


def revert_quests(user_id: int, quest_ids: list[int]) -> WriteOutcome:
    """Reopen the given cleared quest_ids via the Go shim (one backup + one
    transaction): QuestStateType -> Active, clear counters/timestamps reset,
    quest-mission rows reset. Not-cleared quests are skipped server-side."""
    if user_id <= 0:
        raise QuestError("user_id must be positive")
    if not quest_ids:
        return WriteOutcome(applied=0, duration_ms=0)

    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "revert_quests",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "quest_ids": [int(q) for q in quest_ids],
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return WriteOutcome(
        applied=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        applied_ids=tuple(int(i) for i in result.get("quest_ids", [])),
    )


def clear_quests(user_id: int, quest_ids: list[int]) -> WriteOutcome:
    """Faithfully clear the given quest_ids via the Go shim (one backup + one
    transaction). Already-cleared quests are skipped server-side."""
    if user_id <= 0:
        raise QuestError("user_id must be positive")
    if not quest_ids:
        return WriteOutcome(applied=0, duration_ms=0)

    _ensure_shim_available()
    bin_path = _ensure_master_data()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim({
        "action": "clear_quests",
        "db_path": str(config.GAME_DB_PATH),
        "master_data_path": bin_path,
        "user_id": user_id,
        "quest_ids": [int(q) for q in quest_ids],
    })
    duration_ms = int((time.monotonic() - started) * 1000)
    return WriteOutcome(
        applied=int(result.get("applied", 0)),
        duration_ms=duration_ms,
        applied_ids=tuple(int(i) for i in result.get("quest_ids", [])),
    )
