"""Mission ("quest") catalog + per-user mission editing.

Read side: loads the decoded mission master data shipped inside lunar-tear
(assets/masterdata/*.json + assets/names/missions.json) and joins it with the
player's user_missions rows so the editor can list every mission, by category,
with its status and progress.

Write side: edits go through the Go shim (`set_missions`), which applies them
inside one lunar-tear `UpdateUser` transaction — the same store path the game
server uses — after one auto-backup. Writing SQL to user_missions directly (as
an earlier version did) can persist rows the server never writes (e.g. status 0
= "Unknown") and races a running server's WAL, which the client can read back
as a corrupted save. Status 0 is therefore persisted as a row *deletion*: no
row is the canonical "not started" state.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from web import config
from web.services import backup_service


# --- mission status (lunar-tear model.MissionProgressStatusType) ------------
STATUS_NOT_STARTED = 0
STATUS_IN_PROGRESS = 1
STATUS_CLEAR = 2
STATUS_REWARD_RECEIVED = 9

STATUS_LABELS: dict[int, str] = {
    STATUS_NOT_STARTED: "Not started",
    STATUS_IN_PROGRESS: "In progress",
    STATUS_CLEAR: "Clear (claimable)",
    STATUS_REWARD_RECEIVED: "Reward received",
}

# Statuses the editor lets you set a mission to.
SETTABLE_STATUSES: tuple[int, ...] = (
    STATUS_NOT_STARTED,
    STATUS_IN_PROGRESS,
    STATUS_CLEAR,
    STATUS_REWARD_RECEIVED,
)

# --- MissionCategoryType (m_mission_group) ----------------------------------
CATEGORY_LABELS: dict[int, str] = {
    1: "Daily",
    2: "Normal",
    3: "Event",
    4: "Period",
    5: "Costume",
    6: "Story",
    7: "Challenge",
    9: "Login (Beginner)",
    10: "Login (Weekly)",
}

EVENT_CATEGORY_TYPE = 3

# --- MissionClearConditionType -> short human label (best-effort) -----------
CONDITION_LABELS: dict[int, str] = {
    1: "Clear quests",
    5: "Enhance weapons",
    6: "Enhance weapon skills",
    7: "Evolve weapons",
    8: "Ascend weapons",
    9: "Enhance characters",
    10: "Enhance character skills",
    11: "Ascend characters",
    12: "Enhance companions",
    13: "Enhance memoirs",
    15: "Play Arena",
    16: "Win Arena",
    17: "Win Arena in a row",
    18: "Chapter summons",
    21: "Shop purchases",
    22: "Reach player level",
    23: "Log in",
    25: "Complete missions",
    26: "Clear Exploration",
    27: "Set favorite character",
    28: "Reach total force",
    31: "Tap Mama",
    32: "Walk in The Cage",
    35: "Defeat bosses",
    39: "Encyclopedia entries",
    40: "Reach character level",
    43: "Reach weapon level",
    49: "Acquire costumes",
    51: "Subjugation battles",
    53: "Subjugation score rank",
    54: "Unlock Mythic Slab panels",
    60: "Collect Hidden Stories",
    65: "Spend stamina on quests",
    67: "Log in (total days)",
    70: "Refine weapons",
    71: "Exalt characters",
}


def condition_label(condition_type: int) -> str:
    return CONDITION_LABELS.get(condition_type, f"Condition {condition_type}")


def category_label(category_type: int) -> str:
    return CATEGORY_LABELS.get(category_type, f"Category {category_type}")


# --- catalog ----------------------------------------------------------------
@dataclass(frozen=True)
class MissionDef:
    mission_id: int
    name: str
    group_id: int
    category: int
    condition_type: int
    clear_value: int
    term_id: int
    reward_id: int


@dataclass
class MissionCatalog:
    by_id: dict[int, MissionDef]
    # term_id -> (start_ms, end_ms)
    terms: dict[int, tuple[int, int]]

    def is_active(self, m: MissionDef, now_ms: int) -> bool:
        window = self.terms.get(m.term_id)
        if window is None:  # fail-open if the term row is missing
            return True
        start, end = window
        return start <= now_ms <= end


_catalog_cache: MissionCatalog | None = None


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_masterdata_dir() -> Path:
    """Pick the directory that actually holds the decoded mission tables.

    lunar-base keeps its own copy under data/masterdata/ (populated by the
    setup's master-data dump); a lunar-tear checkout has them under
    server/assets/masterdata/. Prefer whichever exists.
    """
    for d in (config.MASTERDATA_DIR, config.LUNAR_TEAR_MASTERDATA_DIR):
        if (d / "EntityMMissionTable.json").exists():
            return d
    return config.MASTERDATA_DIR


def _resolve_names_path() -> Path:
    for p in (config.NAMES_DIR / "missions.json", config.MISSION_NAMES_PATH):
        if p.exists():
            return p
    return config.NAMES_DIR / "missions.json"


def load_catalog(
    masterdata_dir: Path | None = None,
    names_path: Path | None = None,
    *,
    use_cache: bool = True,
) -> MissionCatalog:
    """Load and index the mission master data. Cached after first load."""
    global _catalog_cache
    if use_cache and _catalog_cache is not None:
        return _catalog_cache

    masterdata_dir = masterdata_dir or _resolve_masterdata_dir()
    names_path = names_path or _resolve_names_path()

    required = (
        masterdata_dir / "EntityMMissionTable.json",
        masterdata_dir / "EntityMMissionGroupTable.json",
        masterdata_dir / "EntityMMissionTermTable.json",
    )
    missing = [p for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Mission master data not found (e.g. {missing[0]}). Populate lunar-base/data/masterdata/ "
            f"(or lunar-tear/server/assets/masterdata/) with the decoded EntityMMission*.json tables."
        )

    missions = _read_json(required[0])
    groups = _read_json(required[1])
    terms = _read_json(required[2])

    cat_by_group = {g["MissionGroupId"]: g["MissionCategoryType"] for g in groups}
    term_map = {t["MissionTermId"]: (t["StartDatetime"], t["EndDatetime"]) for t in terms}

    name_by_id: dict[int, str] = {}
    if names_path.exists():
        try:
            records = _read_json(names_path).get("records", [])  # type: ignore[union-attr]
            for r in records:
                mid, nm = r.get("id"), r.get("name")
                if isinstance(mid, int) and isinstance(nm, str):
                    name_by_id[mid] = nm
        except (json.JSONDecodeError, OSError, AttributeError):
            name_by_id = {}

    by_id: dict[int, MissionDef] = {}
    for m in missions:
        mid = m["MissionId"]
        group_id = m["MissionGroupId"]
        by_id[mid] = MissionDef(
            mission_id=mid,
            name=name_by_id.get(mid, f"Mission {mid}"),
            group_id=group_id,
            category=cat_by_group.get(group_id, 0),
            condition_type=m["MissionClearConditionType"],
            clear_value=m["ClearConditionValue"],
            term_id=m["MissionTermId"],
            reward_id=m["MissionRewardId"],
        )

    catalog = MissionCatalog(by_id=by_id, terms=term_map)
    if use_cache:
        _catalog_cache = catalog
    return catalog


# --- per-user view ----------------------------------------------------------
@dataclass(frozen=True)
class MissionRow:
    mission_id: int
    name: str
    condition_type: int
    condition_label: str
    clear_value: int
    status: int
    status_label: str
    progress: int
    active: bool
    has_row: bool


@dataclass
class CategoryGroup:
    category: int
    key: str
    label: str
    rows: list[MissionRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def cleared(self) -> int:
        return sum(1 for r in self.rows if r.status >= STATUS_CLEAR)

    @property
    def active_count(self) -> int:
        return sum(1 for r in self.rows if r.active)


@contextmanager
def _readonly_conn() -> Iterator[sqlite3.Connection]:
    if not config.GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Game database not found: {config.GAME_DB_PATH}")
    uri = f"{config.GAME_DB_PATH.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _read_user_rows(conn: sqlite3.Connection, user_id: int) -> dict[int, tuple[int, int]]:
    """mission_id -> (status, progress) for one user."""
    out: dict[int, tuple[int, int]] = {}
    for row in conn.execute(
        "SELECT mission_id, mission_progress_status_type, progress_value "
        "FROM user_missions WHERE user_id = ?",
        (user_id,),
    ):
        out[row["mission_id"]] = (row["mission_progress_status_type"], row["progress_value"])
    return out


def user_exists(user_id: int) -> bool:
    with _readonly_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None


def get_user_missions(
    user_id: int,
    *,
    active_only: bool = False,
    now_ms: int | None = None,
) -> list[CategoryGroup]:
    """Return missions grouped by category for one user.

    active_only=True drops missions whose term window does not currently
    contain now_ms (useful to hide thousands of finished-event missions).
    """
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    catalog = load_catalog()

    with _readonly_conn() as conn:
        owned = _read_user_rows(conn, user_id)

    groups: dict[int, CategoryGroup] = {}
    for mid, m in catalog.by_id.items():
        active = catalog.is_active(m, now_ms)
        if active_only and not active:
            continue
        status, progress = owned.get(mid, (STATUS_NOT_STARTED, 0))
        has_row = mid in owned
        grp = groups.get(m.category)
        if grp is None:
            grp = CategoryGroup(
                category=m.category,
                key=f"cat{m.category}",
                label=category_label(m.category),
            )
            groups[m.category] = grp
        grp.rows.append(
            MissionRow(
                mission_id=mid,
                name=m.name,
                condition_type=m.condition_type,
                condition_label=condition_label(m.condition_type),
                clear_value=m.clear_value,
                status=status,
                status_label=STATUS_LABELS.get(status, str(status)),
                progress=progress,
                active=active,
                has_row=has_row,
            )
        )

    # Completed first, then active, then by id.
    for grp in groups.values():
        grp.rows.sort(key=lambda r: (r.status < STATUS_CLEAR, not r.active, r.mission_id))

    # Category display order: known categories first (by the label map order),
    # then any unknown category ids.
    order = list(CATEGORY_LABELS.keys())
    return sorted(
        groups.values(),
        key=lambda g: (order.index(g.category) if g.category in order else len(order) + g.category),
    )


# --- writes -----------------------------------------------------------------
BACKUP_REASON = "mission-editor"


@dataclass(frozen=True)
class WriteOutcome:
    applied: int
    duration_ms: int
    rows: list[dict]  # [{mission_id, status, progress}]


class MissionError(Exception):
    """Raised when the shim is missing, refuses, or fails a mission write."""


def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise MissionError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT} to build it (Go must be on PATH)."
        )


def _invoke_shim(payload: dict, *, timeout: int = 120) -> dict:
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
        raise MissionError(
            f"shim returned non-JSON (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        raise MissionError(result.get("error") or stderr or f"shim exited {proc.returncode}")
    return result


def _write_missions(user_id: int, items: list[tuple[int, int, int]]) -> WriteOutcome:
    """Apply (mission_id, status, progress) changes in one shim transaction."""
    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    rows = [
        {"mission_id": mid, "status": status, "progress": progress}
        for (mid, status, progress) in items
    ]
    result = _invoke_shim({
        "action": "set_missions",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
        "missions": rows,
    })
    return WriteOutcome(
        applied=int(result.get("applied", len(rows))),
        duration_ms=int((time.monotonic() - started) * 1000),
        rows=rows,
    )


def _validate_status(status: int) -> None:
    if status not in SETTABLE_STATUSES:
        raise ValueError(f"invalid status {status}; expected one of {SETTABLE_STATUSES}")


def set_mission(user_id: int, mission_id: int, status: int, progress: int) -> WriteOutcome:
    """Set one mission's status and progress value."""
    _validate_status(status)
    catalog = load_catalog()
    if mission_id not in catalog.by_id:
        raise ValueError(f"unknown mission id {mission_id}")
    progress = max(0, int(progress))
    return _write_missions(user_id, [(mission_id, status, progress)])


def _bulk_complete(user_id: int, defs: list[MissionDef], status: int) -> WriteOutcome:
    _validate_status(status)
    # Completing fills progress to the clear target; resetting (status below
    # Clear, e.g. Not started) zeroes it.
    items = [(m.mission_id, status, m.clear_value if status >= STATUS_CLEAR else 0) for m in defs]
    return _write_missions(user_id, items)


def complete_category(
    user_id: int, category: int, status: int, *, active_only: bool = True, now_ms: int | None = None
) -> WriteOutcome:
    """Mark every mission in a category complete (progress = clear value)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    catalog = load_catalog()
    defs = [
        m for m in catalog.by_id.values()
        if m.category == category and (not active_only or catalog.is_active(m, now_ms))
    ]
    return _bulk_complete(user_id, defs, status)


def complete_all(
    user_id: int, status: int, *, include_events: bool = False, active_only: bool = True,
    now_ms: int | None = None,
) -> WriteOutcome:
    """Mark all (active, non-event by default) missions complete."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    catalog = load_catalog()
    defs = []
    for m in catalog.by_id.values():
        if active_only and not catalog.is_active(m, now_ms):
            continue
        if not include_events and m.category == EVENT_CATEGORY_TYPE:
            continue
        defs.append(m)
    return _bulk_complete(user_id, defs, status)
