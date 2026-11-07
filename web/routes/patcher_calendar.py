"""Original calendar pages and routes (ported from Bunker's android_calendar.py).

The calendar replays the live service from global launch, week for week. The
game data is built once per loop with every rotating date moved forward by the
whole weeks between launch and the loop's start; the server's clock then runs
ahead of real time by jumpMillis (plus whole weeks for faster speeds), so the
game moves through the loop while the data stays the same. The server reads
calendar.json next to the save, and swaps in the next loop's game data when a
loop ends.

This is a contained port of Bunker's ``android_calendar.py`` handlers and
helper logic into Lunar Base. Dependencies were adapted as follows:

* Calendar state: ``<lunar-tear>/server/db/calendar.json`` — the same
  ``config.GAME_DB_PATH.parent / "calendar.json"`` formula as the original.
* Next-loop / original-dates copies: ``<lunar-tear>/server/assets/release/``
  (``config.LUNAR_TEAR_DIR / "server" / "assets" / "release"``).
* Schedule extraction (``entries()`` — summons / events / shops / seasons) is
  implemented equivalently on top of ``web.services.masterdata_bin`` (the
  encrypted bin.e the server loads), naming each column through
  ``scripts/schemas.json``; it falls back to the decoded EntityM*Table.json
  dumps and degrades to a page-visible notice instead of failing.
* Names come from ``web/config.py``'s ``NAMES_DIR`` (extracted name files).
* ``reached_chapters()`` reads lunar-tear's save schema
  (``user_quests.quest_state_type = 2`` = cleared) instead of Bunker's
  ``clear_count``.
* ``read_state()`` is side-effect free on GET: the original wrote state on
  read (dataVersion bump / migrations); here only explicit POST handlers
  write calendar.json.

NOT portable in a contained way (Bunker's patcher stack: patch_masterdata,
calendar_history.json, tower_floors.json, the assetbundle texture decoder):
rebuilding the shifted master data (``build_master`` / ``activate_loop`` /
``prepare_next`` / ``build_view``) and banner-art decoding. Those steps are
stubbed out and every affected action degrades to a visible page/redirect
notice (``CalendarBuildUnavailable``) instead of crashing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import NamedTuple
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from web import config
from web.services import masterdata_bin

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))

DAY = 24 * 60 * 60 * 1000
WEEK = 7 * DAY
# Global launch week: Wednesday 28 July 2021, 02:00 UTC, the hour updates went live.
LAUNCH = int(datetime(2021, 7, 28, 2, tzinfo=timezone.utc).timestamp() * 1000)
LOOP_WEEKS = 144
# The first summons opened 20 hours into the launch week (28 July 2021, 22:00
# UTC). The game hangs on a summon page with none open, so a loop opens then
# (server: gametime.FirstSummons).
FIRST_SUMMONS = 20 * 60 * 60 * 1000
# Dates after the service closed (30 April 2024) mean "never ends" or "never starts".
FOREVER = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
EARLIEST = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
NEVER = int(datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
# The game day starts at 00:00 GMT-8 (08:00 UTC).
DAY_START = 8 * 60 * 60 * 1000
# Kept in step with server/internal/masterdata/gacha.go and gacha_special.go.
SHARED_MEDAL = {46, 151} | {401, 501, 535, 541, 542, 543, 549, 550, 555, 556, 582, 583, 585, 510001, 513001, 515001, 536001, 537001, 9000035}
SPECIAL_SUMMONS = {43, 49, 52, 60, 64, 66, 70, 85, 153, 160, 199, 410, 416, 428, 430, 433, 449, 452, 453, 468, 472, 493, 501, 503, 510, 514, 515, 524, 545, 547, 581, 584, 593, 597, 9000007}
PARKED_RERUNS = {159: 3110399000, 414: 1123199000}
BANNERLESS_SKIPPED = {617, 10016, 10107, 10108, 9000040}
SPEEDS = {"1": "Real time", "2": "2×", "3.5": "3.5×", "7": "7×"}

# An event chapter's later runs are copies of its row with these ids, so each
# run opens in its own weeks: chapter + n * COPY_STEP for the nth later run.
COPY_STEP = 10_000_000
# Abyss Towers (EventQuestType 10) reran 2021-2023 before they stayed for good.
TOWER_TYPE = 10
# Home banners whose run is not restored: their summon is not listed then.
UNRESTORED_BANNERS = {274, 365}
# An event's medal exchange and the items it gives.
LINK_SHOP = 3            # EventQuestLink.DestinationDomainType: a shop
CONSUMABLE_ITEM = 6      # PossessionType

# The Schedule page's timeline: from a week back to three months ahead (or
# the loop's end), and what ends within a week. Times are in the dates the
# game shows; the page draws them in the phone's local time.
TIMELINE_KINDS = (("summon", "Summons"), ("event", "Events"), ("shop", "Shops"), ("arena", "Arena"))
TIMELINE_BEFORE, TIMELINE_AFTER, ENDING_SOON = 7 * DAY, 92 * DAY, 7 * DAY

# What a jump skips, by kind, in the order the Jump to page lists them.
MISSED_NAMES = (("event", "event", "events"), ("summon", "summon", "summons"), ("shop", "shop", "shops"),
                ("arena", "Arena season", "Arena seasons"), ("pass", "monthly pass", "monthly passes"))

# The next loop's game data sits beside the master data, where the server
# looks for it (gametime.NextName). Not .bin.e, which the tools would take
# for the master data.
NEXT_NAME = "calendar-next.bin"
# "Show original dates": the game is sent its data with no date shift and its
# clock and times moved back by the loop's offset (server: gametime.ViewOffset,
# service.ViewMasterName). One copy serves every loop.
VIEW_NAME = "calendar-view.bin.e"
VIEW_VERSION_FILE = "calendar-view.version"
# Bumped when the copy's build changes (2 caps start dates at launch and opens
# story chapters on their dates), and when a loop's game data changes (1 opens
# story chapters on their dates).
VIEW_VERSION = "2"
DATA_VERSION = 1

# The release directory the server loads the master data from:
# <lunar-tear>/server/assets/release/ (JP=20240405123139.bin.e, GL=20240404193219.bin.e).
RELEASE_DIR: Path = (config.LUNAR_TEAR_DIR / "server" / "assets" / "release").resolve()

# Summons the game text has no title for: the title is drawn into the banner
# art only. Keyed by banner asset (Bunker archive/schedule.py ART_TITLES).
ART_TITLES = {
    "limited_71": "Elemental Summons", "limited_9001": "Halloween Lucky Summons", "limited_84": "Halloween Bonus Summons",
    "step_up_501001": "Black Friday Multi-Step Summons", "limited_9004": "NieR Day Bonus Summons",
    "limited_9005": "★★★★ Guaranteed Fire-Elemental Holiday Summons (Paid Gems Only)",
    "limited_9006": "★★★★ Guaranteed Light-Elemental Holiday Summons (Paid Gems Only)",
    "limited_9007": "★★★★ Guaranteed Wind-Elemental Holiday Summons (Paid Gems Only)",
    "limited_9008": "★★★★ Guaranteed Water-Elemental Holiday Summons (Paid Gems Only)",
    "limited_9009": "★★★★ Guaranteed Dark-Elemental Holiday Summons (Paid Gems Only)",
    "limited_9010": "The Sun and the Moon Release Countdown Summons Vol. 1",
    "limited_9011": "The Sun and the Moon Release Countdown Summons Vol. 2",
    "limited_9012": "The Sun and the Moon Release Countdown Summons Vol. 3",
    "limited_9013": "The Sun and the Moon Release Countdown Summons Vol. 4",
    "limited_9014": "Subjugation Bonus Summons", "limited_9015": "NieR Day Bonus Summons",
    "step_up_900001": "Subjugation Multi-Step Summons", "limited_9017": "Hunter Summons", "limited_9018": "Subjugation Bonus Summons",
    "limited_9019": "Daily Showcase Summons Vol. 1", "limited_9020": "Daily Showcase Summons Vol. 2",
    "limited_9021": "Daily Showcase Summons Vol. 3", "limited_9022": "Daily Showcase Summons Vol. 4",
    "limited_9023": "Daily Showcase Summons Vol. 5", "step_up_511001": "Anniversary ★★★★ Guaranteed Summons (Paid Gems Only)",
    "limited_9026": "NieR Day Bonus Summons", "limited_9027": "NieR Day Bonus Summons", "limited_9028": "NieR Day Bonus Summons",
    "step_up_514001": "Arena Update Celebration Multi-Step Summons",
    "step_up_522001": "Crossover Quest Support Paid-Only Multi-Step Summons",
    "step_up_525001": "The Sun and the Moon New Chapter Multi-Step Summons",
    "step_up_533001": "Japanese 1.5-Year Anniversary Recollections of Dusk Summons (Paid-Only Multi-Step)",
    "limited_405": "Japanese 1.5-Year Anniversary Abyssal Costume Summons (Paid Gems Only)",
    "step_up_527001": "Crossover Quest Support Paid-Only Multi-Step Summons",
    "step_up_534001": "Monthly Featured Paid-Only Multi-Step Summons",
    "limited_415": "The Sun and the Moon New Chapter ★★★★ Character Guaranteed Summons (Paid Gems Only)",
    "limited_456": "New Year's Celebration Free 10x Summons Daily", "limited_457": "New Year's Celebration Free 10x Summons Daily",
    "step_up_551001": "Monthly Featured Paid-Only Multi-Step Summons", "limited_506": "Healer/Support ★★★★ Guaranteed Summons",
    "common_200001": "Japanese 2.5 Year Anniversary Mama's Dream Summons",
    "limited_554": "Dragon Quest X Crossover Commemorative 10 Free Summons",
    "limited_576": "Japanese 1000-Day Anniversary Unacquired Specific Summons (Paid-only Powerful Skill Character Guaranteed)",
    "limited_586": "Arena MVP Unacquired Specific Summons (Paid-Only Character Guaranteed)",
    "limited_587": "Subjugation MVP Unacquired Specific Summons (Paid-Only Character Guaranteed)",
    "limited_595": "End of the Year New Year Countdown Paid-Only Summons Vol. 1",
    "limited_596": "End of the Year New Year Countdown Paid-Only Summons Vol. 2",
    "limited_589": "New Year's Resurrected Festive Character Paid-Only Guaranteed Summons Vol. 1",
    "limited_590": "New Year's Resurrected Festive Character Paid-Only Guaranteed Summons Vol. 2",
    **{f"limited_{n}": "Story Completion Countdown x10 Free per Day Summons" for n in (600, 601, 602, 603, 604, 605, 609, 610)},
    "limited_123": "Yuletide Wishes Limited-Time Summons", "step_up_503001": "Yuletide Wishes Paid Only: Multi-Step Summons",
    "limited_144": "Japanese 1st Anniversary 218+2 Free Summons",
}


# --- Degradation notices ----------------------------------------------------
# Parts of the original that cannot be equivalent in Lunar Base (the Bunker
# patcher's master-data build and assetbundle decoder) degrade to these
# page-visible hints instead of failing.

class CalendarBuildUnavailable(RuntimeError):
    """Raised where android_calendar.py would rebuild the master-data bin."""


DEPLOY_NOTE = "The game data cannot be rebuilt or deployed from Lunar Base — use the bunker patcher."
BUILD_HINT = ("Game-data rebuild (loop activation / next-loop build / original-dates copy) is not "
              "available in Lunar Base — build and deploy the shifted master data with the bunker patcher.")

_notes: set[str] = set()


def _note(message: str) -> None:
    """Record a degradation notice for the pages to show (deduplicated)."""
    _notes.add(str(message))


def degraded_notes() -> list[str]:
    """Page-visible hints: the static build note plus extraction problems seen."""
    return [BUILD_HINT] + sorted(_notes)


# --- Calendar state ---------------------------------------------------------

def state_path() -> Path:
    # android_calendar: config.GAME_DB_PATH.parent / "calendar.json" —
    # <lunar-tear>/server/db/calendar.json, the file the server reads.
    return config.GAME_DB_PATH.parent / "calendar.json"


def _read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_state():
    """The current calendar state, or None when the calendar is off.

    Side-effect free on read (the original rebuilt the loop's game data and
    wrote state here; Lunar Base only records a visible notice)."""
    state = _read_json(state_path())
    if not (isinstance(state, dict) and state.get("loopStart")):
        return None
    move_next(state)
    if state.get("dataVersion") != DATA_VERSION:
        _note(f"This loop's game data was not built for dataVersion {DATA_VERSION} "
              "(build & deploy it with the bunker patcher).")
    refresh_view(state)
    return state


def write_state(state):
    _write_json(state_path(), state)


def start_of_day(ms):
    return (ms - DAY_START) // DAY * DAY + DAY_START


def auto_weeks(state, real):
    """Whole weeks a faster speed has added since it was chosen, one step per daily reset."""
    twice = round(float(state.get("speed", 1)) * 2)
    since = state.get("speedSince") or real
    days = max(0, (start_of_day(real) - since) // DAY)
    return days * (twice - 2) // 14


def game_now(state, real=None):
    real = now_ms() if real is None else real
    return real + state.get("jumpMillis", 0) + auto_weeks(state, real) * WEEK


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def loop_end(state):
    return state["loopStart"] + LOOP_WEEKS * WEEK


def week_of(state, game):
    return (game - state["loopStart"]) // WEEK + 1


def original(state, game):
    """The same moment in the original service."""
    return game - (state["loopStart"] - LAUNCH)


def label(state):
    return f"Original calendar · loop {state['loop']}"


def open_at_first_summons(state, real=None):
    """Moves a clock before the loop's first summons on in whole days until they
    are open (server: OpenAtFirstSummons). Returns True when it did."""
    behind = state["loopStart"] + FIRST_SUMMONS - game_now(state, real)
    if behind <= 0:
        return False
    state["jumpMillis"] = state.get("jumpMillis", 0) + -(-behind // DAY) * DAY
    return True


def new_state(real):
    start = LAUNCH + (real - LAUNCH) // WEEK * WEEK
    state = {"version": 1, "loop": 1, "loopStart": start, "jumpMillis": 0, "speed": 1, "speedSince": start_of_day(real), "dataVersion": DATA_VERSION}
    open_at_first_summons(state, real)
    return state


def set_speed(state, speed, real=None):
    if speed not in SPEEDS:
        raise ValueError("Unknown speed")
    real = now_ms() if real is None else real
    # Keep the weeks the old speed has already added.
    state["jumpMillis"] = state.get("jumpMillis", 0) + auto_weeks(state, real) * WEEK
    state["speed"] = float(speed) if "." in speed else int(speed)
    state["speedSince"] = start_of_day(real)


def jump(state, target, real=None):
    """Moves the clock forward to target in whole days, so the daily reset stays at the same hour."""
    now = game_now(state, real)
    if target <= now:
        raise ValueError("The calendar only moves forward")
    if target >= loop_end(state):
        raise ValueError("Choose a week in this loop")
    state["jumpMillis"] = state.get("jumpMillis", 0) + -(-(target - now) // DAY) * DAY


# --- Game data (master-data extraction) -------------------------------------
# android_calendar.py read the decoded EntityM*Table.json dumps. The Lunar Base
# equivalent reads the live encrypted bin.e through web.services.masterdata_bin
# (the file the server loads), naming columns via scripts/schemas.json, and
# falls back to the decoded dumps; anything unavailable degrades to a notice.

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _bin_table_name(name: str) -> str:
    """'EventQuestChapter' -> 'm_event_quest_chapter' (the bin table name)."""
    return "m_" + _CAMEL_RE.sub("_", name).lower()


def _cell(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return value


_schemas_cache: dict[str, list[str]] | None = None


def _schema_names() -> dict[str, list[str]]:
    """bin table -> column names by index (scripts/schemas.json)."""
    global _schemas_cache
    if _schemas_cache is not None:
        return _schemas_cache
    out: dict[str, list[str]] = {}
    try:
        data = json.loads((config.ROOT / "scripts" / "schemas.json").read_text(encoding="utf-8"))
        for table, spec in data.items():
            columns = spec.get("columns") if isinstance(spec, dict) else None
            if isinstance(columns, list):
                out[table] = [c[2] for c in sorted(columns, key=lambda c: c[0]) if isinstance(c, list) and len(c) >= 3]
    except (OSError, ValueError) as exc:
        _note(f"scripts/schemas.json unreadable ({exc}); master-data columns will not resolve.")
    _schemas_cache = out
    return out


_bin_cache: tuple[int, dict, bytes] | None = None


def _load_bin_once():
    """(toc, data blob) from ONE decrypt of the live bin (masterdata_bin internals)."""
    global _bin_cache
    path = masterdata_bin.bin_path()
    mtime = path.stat().st_mtime_ns
    if _bin_cache is not None and _bin_cache[0] == mtime:
        return _bin_cache[1], _bin_cache[2]
    toc, blob = masterdata_bin._parse(masterdata_bin._decrypt(path.read_bytes()))
    _bin_cache = (mtime, toc, blob)
    return toc, blob


def _rows_from_bin(bin_table: str) -> list[dict] | None:
    """Rows of one bin table as dicts, or None when it cannot be read."""
    names = _schema_names().get(bin_table)
    if not names:
        return None
    import msgpack
    try:
        toc, blob = _load_bin_once()
        if bin_table not in toc:
            return None
        table, _compressed = masterdata_bin._table_bytes(toc, blob, bin_table)
        raw_rows = msgpack.unpackb(bytes(table), raw=True)
    except Exception:
        # Fall back to the service's per-call reader (re-decrypts per table).
        try:
            raw_rows = masterdata_bin.decode_rows(bin_table)
        except Exception:
            return None
    out = []
    for cells in raw_rows:
        if not isinstance(cells, (list, tuple)):
            continue
        row = {names[i]: _cell(v) for i, v in enumerate(cells) if i < len(names)}
        out.append(row)
    return out


_table_cache: dict[tuple[str, int], list[dict]] = {}


def table(name: str) -> list[dict]:
    """A master-data table as a list of dicts keyed by column name.

    Primary source: the live bin.e through masterdata_bin (equivalent to the
    original's EntityM{name}Table.json dumps); fallback: the decoded dumps."""
    bin_table = _bin_table_name(name)
    try:
        key = (name, masterdata_bin.bin_path().stat().st_mtime_ns)
    except (FileNotFoundError, OSError):
        key = (name, -1)
    if key in _table_cache:
        return _table_cache[key]
    rows = _rows_from_bin(bin_table) if key[1] != -1 else None
    if rows is None:
        try:
            rows = _read_json(config.MASTERDATA_DIR / f"EntityM{name}Table.json", []) or []
        except Exception:
            rows = []
        if not rows:
            _note(f"Schedule table {name} ({bin_table}) unavailable — master-data bin or decoded dump missing.")
    _table_cache[key] = rows
    return rows


def names(kind: str) -> dict[int, str]:
    """id -> display name from an extracted names file (data/names/<kind>.json)."""
    out: dict[int, str] = {}
    for p in (config.NAMES_DIR / f"{kind}.json",
              config.LUNAR_TEAR_DIR / "server" / "assets" / "names" / f"{kind}.json"):
        data = _read_json(p, None)
        if not isinstance(data, dict):
            continue
        for r in data.get("records", []):
            if isinstance(r, dict) and r.get("name_found") and isinstance(r.get("id"), int):
                out[r["id"]] = r.get("name") or ""
        break
    return out


# With the Original calendar story chapters open on their release dates, in
# the first loop only: from the second the player has been through the story.
# Chapters the player has reached (a quest cleared in them) stay open.
def all_chapters():
    return {r["MainQuestChapterId"] for r in table("MainQuestChapter")}


def reached_chapters():
    """Chapters with a quest any player of this save has cleared.

    lunar-tear's save keeps user_quests.quest_state_type (2 = cleared) where
    Bunker's save had clear_count."""
    try:
        db = sqlite3.connect(f"file:{config.GAME_DB_PATH}?mode=ro", uri=True)
        try:
            cleared = {q for (q,) in db.execute("select distinct quest_id from user_quests where quest_state_type = 2")}
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        return set()
    sequence = {r["QuestId"]: r["MainQuestSequenceId"] for r in table("MainQuestSequence")}
    group = {r["MainQuestSequenceId"]: r["MainQuestSequenceGroupId"] for r in table("MainQuestSequenceGroup")}
    chapter = {r["MainQuestSequenceGroupId"]: r["MainQuestChapterId"] for r in table("MainQuestChapter")}
    return {chapter[group[sequence[q]]] for q in cleared if q in sequence and sequence[q] in group and group[sequence[q]] in chapter}


def open_chapters(state):
    return reached_chapters() if state.get("loop", 1) <= 1 else all_chapters()


# --- What is open ------------------------------------------------------------

class Entry(NamedTuple):
    """Dated content: its kind and name, when it is open, the Archive page it
    links to (or "") and its banner art's bundle path (or "")."""
    kind: str
    name: str | None
    start: int
    end: int
    ref: str = ""
    art: str = ""


def summon_art(asset):
    return f"ui/gacha/en/{asset}/mom_banner.assetbundle" if asset else ""


def entries():
    """Dated content the Schedule page lists (Entry)."""
    found = []
    event_names = names("event_quests")
    # The loop's game data already holds every run the history restored, each
    # as its own row with its dates moved into the loop.
    for row in table("EventQuestChapter"):
        # A rerun's copy (chapter + n * COPY_STEP) is the same event in the Archive.
        banner = row.get("BannerAssetId", 0)
        found.append(Entry("event", event_names.get(row["EventQuestChapterId"]), row["StartDatetime"], row["EndDatetime"],
                           f"/schedule/event/{row['EventQuestChapterId'] % COPY_STEP}",
                           f"ui/quest/en/banner/event_banner_{banner:03d}.assetbundle" if banner else ""))
    banner_names = names("gacha_banners")
    # The summons the server offers (server/internal/masterdata/gacha.go):
    # those with a medal, summons sharing a known medal, and the special
    # free, paid-only and guaranteed summons.
    medals = {row["ShopTransitionGachaId"]: row for row in table("GachaMedal")}
    offered = set(medals) | SHARED_MEDAL | SPECIAL_SUMMONS
    parked = set()
    for row in table("MomBanner"):
        gacha = row["DestinationDomainId"]
        if row["DestinationDomainType"] == 1 and gacha in PARKED_RERUNS and gacha in medals and gacha not in parked:
            # Only placeholder rows: the dates come from the medal (see parkedReruns).
            parked.add(gacha)
            convert = medals[gacha]["AutoConvertDatetime"]
            found.append(Entry("summon", banner_names.get(row["MomBannerId"]) or ART_TITLES.get(row["BannerAssetName"]), convert - PARKED_RERUNS[gacha], convert - 2 * DAY,
                               f"/schedule/summon/{gacha}", summon_art(row["BannerAssetName"])))
    for row in table("MomBanner"):
        if row["DestinationDomainType"] == 1 and row["DestinationDomainId"] in offered and row["DestinationDomainId"] not in PARKED_RERUNS:
            # Some titles are only in the banner art (ART_TITLES).
            found.append(Entry("summon", banner_names.get(row["MomBannerId"]) or ART_TITLES.get(row["BannerAssetName"]), row["StartDatetime"], row["EndDatetime"],
                               f"/schedule/summon/{row['DestinationDomainId']}", summon_art(row["BannerAssetName"])))
    # Summons with a medal but no banner row (server: bannerlessSummons).
    with_banner = {row["DestinationDomainId"] for row in table("MomBanner") if row["DestinationDomainType"] == 1}
    opens = shop_opens_by_price()
    item_names = names("consumables")
    for gacha, medal in medals.items():
        start = opens.get(medal["ConsumableItemId"])
        end = medal["AutoConvertDatetime"] - 2 * DAY
        if gacha in with_banner or gacha in BANNERLESS_SKIPPED or start is None or not 0 < start < end:
            continue
        shard = item_names.get(medal["ConsumableItemId"], "")
        label = shard[shard.find("(") + 1:shard.rfind(")")] if "(" in shard else ""
        if label and "summons" not in label.lower():
            label += " Summons"
        found.append(Entry("summon", label or None, start, end, f"/schedule/summon/{gacha}"))
    shop_names = names("shops")
    for row in table("Shop"):
        if row["ShopGroupType"] == 4:
            found.append(Entry("shop", shop_names.get(row["ShopId"]), row["StartDatetime"], row["EndDatetime"]))
    for row in table("PvpSeason"):
        if not row.get("IsInvalid"):
            found.append(Entry("arena", f"Arena Season {row['PvpSeasonId'] % 1000:03d}", row["SeasonStartDatetime"], row["SeasonEndDatetime"]))
    for index, row in enumerate(sorted(table("MissionPass"), key=lambda r: r["StartDatetime"]), 1):
        found.append(Entry("pass", f"Mission pass, month {index}", row["StartDatetime"], row["EndDatetime"]))
    for row in table("BigHuntSchedule"):
        found.append(Entry("hunt", f"Subjugation Battle {row['BigHuntScheduleId']}", row["ChallengeStartDatetime"], row["ChallengeEndDatetime"]))
    for row in table("EnhanceCampaign") + table("QuestCampaign"):
        found.append(Entry("campaign", "", row["StartDatetime"], row["EndDatetime"]))
    for row in table("ConsumableItemTerm"):
        found.append(Entry("expiry", "", row["StartDatetime"], row["EndDatetime"]))
    # Content that never ends after release is part of the game, not the calendar.
    return [e for e in found if e[2] < NEVER and e[3] < NEVER and e[2] < e[3]]


def shop_opens_by_price():
    """When the first shop selling for each item opens (server: shopOpensByPrice)."""
    price = {r["ShopItemId"]: r["PriceId"] for r in table("ShopItem")}
    cell = {r["ShopItemCellId"]: r["ShopItemId"] for r in table("ShopItemCell")}
    groups = {}
    for r in table("ShopItemCellGroup"):
        groups.setdefault(r["ShopItemCellGroupId"], set()).add(price.get(cell.get(r["ShopItemCellId"])))
    out = {}
    for shop in table("Shop"):
        for item in groups.get(shop["ShopItemCellGroupId"], ()):
            if item is not None and (item not in out or shop["StartDatetime"] < out[item]):
                out[item] = shop["StartDatetime"]
    return out


# --- Labels and summaries ---------------------------------------------------
# strftime("%-d") is glibc-only; the panel also runs on Windows, so the day is
# formatted by hand (same "Wed 28 Jul 2021" output as the original).

def _fmt_ms(ms, with_year: bool) -> str:
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    tail = dt.strftime("%b %Y") if with_year else dt.strftime("%b")
    return f"{dt.strftime('%a')} {dt.day} {tail}"


def day_label(ms):
    return _fmt_ms(ms, True)


def short_label(ms):
    return _fmt_ms(ms, False)


def open_at(items, at):
    return [e for e in items if e[2] <= at < e[3]]


OTHER = {"event": "other event", "summon": "other summon", "shop": "other shop"}


def summary(items, at):
    """Open content by kind, soonest to close first; unnamed entries are counted in one line."""
    groups, unnamed = {}, {}
    for kind, name, start, end, *_ in sorted(open_at(items, at), key=lambda e: e[3]):
        if name or kind in ("campaign", "expiry"):
            groups.setdefault(kind, []).append({"name": name, "until": short_label(end - 1)})
        else:
            unnamed[kind] = unnamed.get(kind, 0) + 1
    for kind, count in unnamed.items():
        groups.setdefault(kind, []).append({"name": f"{count} {OTHER[kind]}{'s' if count != 1 else ''}", "until": ""})
    return groups


def timeline(state, items, now):
    """Rows of the summons, events, shops and Arena seasons in the window, and those ending soon."""
    start, end = now - TIMELINE_BEFORE, min(now + TIMELINE_AFTER, loop_end(state))
    shift = state["loopStart"] - LAUNCH if state.get("originalDates") else 0
    labels = dict(TIMELINE_KINDS)

    def item(e):
        # Ends after the loop are the game data's placeholders for content that stays: no end.
        return {"kind": e.kind, "name": e.name or labels[e.kind].removesuffix("s"), "start": e.start - shift,
                "end": e.end - shift if e.end <= loop_end(state) else None, "ref": e.ref,
                "art": "/patcher/calendar/art?path=" + quote(e.art, safe="/") if e.art else ""}
    shown = sorted((e for e in items if e.kind in labels and e.start < end and e.end > start), key=lambda e: (e.start, e.end))
    soon = sorted((e for e in shown if e.start <= now < e.end <= now + ENDING_SOON), key=lambda e: e.end)
    return {"now": now - shift, "from": start - shift, "to": end - shift, "soon": [item(e) for e in soon],
            "rows": [{"kind": kind, "label": label, "items": [item(e) for e in shown if e.kind == kind]} for kind, label in TIMELINE_KINDS]}


def timeline_file():
    return config.DATA_DIR / "timeline.json"


def timeline_hidden():
    """The timeline rows the player hid. Kept here, not in the page: iOS keeps no page storage between sessions."""
    data = _read_json(timeline_file(), {})
    hidden = data.get("hidden", []) if isinstance(data, dict) else []
    return [k for k in hidden if k in dict(TIMELINE_KINDS)] if isinstance(hidden, list) else []


def set_timeline_hidden(kinds):
    _write_json(timeline_file(), {"hidden": [k for k in kinds if k in dict(TIMELINE_KINDS)]})


def updates(state, items, after):
    """Days in the rest of the loop when new events or summons open, named by their first event."""
    days = {}
    for kind, name, start, end, *_ in sorted(items, key=lambda e: (e[2], e[0] != "event")):
        if kind in ("event", "summon") and after < start < loop_end(state):
            day = days.setdefault(start_of_day(start), {"at": start, "names": [], "count": 0})
            day["count"] += 1
            if name:
                day["names"].append(name)
    return [{"at": v["at"], "day": day_label(v["at"]), "week": week_of(state, v["at"]), "name": (v["names"] or ["New summons and quests"])[0],
             "more": v["count"] - 1, "all": " ".join(v["names"])} for k, v in sorted(days.items())]


def missed_text(missed):
    """preview's missed counts as text: "10 events, 1 Arena season"."""
    return ", ".join(f"{missed[kind]} {one if missed[kind] == 1 else many}" for kind, one, many in MISSED_NAMES if missed.get(kind))


def preview(state, items, now, target):
    """What a jump from now to target opens, still pays out, and skips."""
    arrive = now + -(-(target - now) // DAY) * DAY
    paid, missed = [], {}
    for kind, name, start, end, *_ in items:
        if not now < end <= arrive:
            continue
        if start <= now and kind in ("arena", "pass"):
            paid.append(name)
        elif start > now and kind in ("event", "summon", "shop", "arena", "pass"):
            missed[kind] = missed.get(kind, 0) + 1
    return {"arrive": arrive, "day": day_label(arrive), "original": day_label(original(state, arrive)), "week": week_of(state, arrive),
            "days": (arrive - now) // DAY, "open": summary(items, arrive), "paid": paid, "missed": missed,
            "missedText": missed_text(missed)}


def start_preview(real=None):
    real = now_ms() if real is None else real
    state = new_state(real)
    now = game_now(state, real)
    return {"day": day_label(now), "original": day_label(original(state, now)), "ends": day_label(loop_end(state))}


# --- Pages -------------------------------------------------------------------

def redirect(path="/", **kwargs):
    return RedirectResponse(path + ("?" + urlencode(kwargs) if kwargs else ""), status_code=303)


def context(state):
    now = game_now(state)
    items = entries()
    shown = original(state, now) if state.get("originalDates") else now
    return {"state": state, "loop": state["loop"], "week": week_of(state, now), "weeks": LOOP_WEEKS,
            "progress": round(100 * (now - state["loopStart"]) / (LOOP_WEEKS * WEEK), 1),
            "today": day_label(shown), "originalDates": bool(state.get("originalDates")), "original": day_label(original(state, now)), "ends": day_label(loop_end(state)),
            "speed": str(state.get("speed", 1)), "speeds": SPEEDS, "now": now, "timeline": dict(timeline(state, items, now), hidden=timeline_hidden())}


# --- Master-data rebuild (degraded) -----------------------------------------
# android_calendar rebuilt the loop's shifted game data here (build_master:
# restore_history, open_exchanges, shift_table, …) through Bunker's patcher
# stack (patcher.patch_masterdata, tools.dump_masterdata, calendar_history.json,
# tower_floors.json). That stack is not part of Lunar Base; the steps degrade
# to a visible notice while the calendar.json state keeps working (the server
# reads it for jumpMillis / speed).

def build_master(origin, output, offset, view=False, open_chapters=None):
    raise CalendarBuildUnavailable(
        "Rebuilding the master data (build_master) needs the bunker patcher's build stack, "
        "which is not available in Lunar Base.")


def origin_file():
    raise CalendarBuildUnavailable(
        "Choosing original master data (origin.bin.e) needs the bunker patcher (Advanced).")


def next_file() -> Path:
    return RELEASE_DIR / NEXT_NAME


def view_file() -> Path:
    return RELEASE_DIR / VIEW_NAME


def view_version_file() -> Path:
    return RELEASE_DIR / VIEW_VERSION_FILE


def view_version(state):
    """The copy's version: the build's, and first loop or later (story chapters)."""
    return f"{VIEW_VERSION}:{min(state.get('loop', 1), 2)}"


def view_outdated(state):
    version = view_version_file()
    return not view_file().exists() or not version.exists() or version.read_text().strip() != view_version(state)


def build_view(state):
    raise CalendarBuildUnavailable(
        "Building the original-dates copy (build_view) needs the bunker patcher's build stack, "
        "which is not available in Lunar Base.")


def refresh_view(state):
    """Rebuilds the game's copy for "Show original dates" when it is on and the copy is missing or older."""
    if state.get("originalDates") and view_outdated(state):
        try:
            build_view(state)
        except CalendarBuildUnavailable as exc:
            _note(str(exc))


def set_original_dates(state, on):
    """Turns "Show original dates" on or off, building the game's copy when needed."""
    if on and view_outdated(state):
        try:
            build_view(state)
        except CalendarBuildUnavailable as exc:
            _note(str(exc))
    state["originalDates"] = bool(on)


def old_next_file():
    # Bunker kept the pre-1.1.3 next-loop file in the patcher folder.
    return RELEASE_DIR / "calendar-next.bin.e"


def move_next(state):
    """Moves a calendar started before 1.1.3 to the next loop's new place."""
    if not state.get("next") or state["next"] == NEXT_NAME:
        return
    old = old_next_file()
    try:
        if old.exists():
            if next_file().exists():
                old.unlink()
            else:
                old.replace(next_file())
    except OSError as exc:
        _note(f"Could not move the old next-loop file: {exc}")
    state["next"] = NEXT_NAME
    write_state(state)


def activate_loop(state):
    raise CalendarBuildUnavailable(
        "Activating a loop's game data (activate_loop) needs the bunker patcher's build stack, "
        "which is not available in Lunar Base.")


def prepare_next(state):
    raise CalendarBuildUnavailable(
        "Building the next loop's game data (prepare_next) needs the bunker patcher's build stack, "
        "which is not available in Lunar Base.")


def roll_over(state, real=None):
    """Starts the next loop if this one has ended. Returns True when it did."""
    if game_now(state, real) < loop_end(state):
        return False
    while game_now(state, real) >= loop_end(state):
        state["loop"] += 1
        state["loopStart"] += LOOP_WEEKS * WEEK
    open_at_first_summons(state, real)
    try:
        activate_loop(state)
        state.pop("next", None)
        prepare_next(state)
    except CalendarBuildUnavailable as exc:
        _note(str(exc))
    write_state(state)
    return True


def turn_on(real=None):
    """Writes a fresh calendar state; the loop's game-data build degrades to a notice."""
    real = now_ms() if real is None else real
    state = new_state(real)
    try:
        activate_loop(state)
        prepare_next(state)
    except CalendarBuildUnavailable as exc:
        _note(str(exc))
    write_state(state)
    return state


def turn_off():
    state_path().unlink(missing_ok=True)
    for p in (next_file(), view_file(), view_version_file(), old_next_file()):
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            _note(f"Could not remove {p.name}: {exc}")


# --- Handlers ----------------------------------------------------------------
# Form handlers redirect back to the calendar page (message/error banners);
# with the calendar off they land on the panel home, as the original landed on
# its /patcher home. Bunker's "/patcher" page is not part of this port.

def _home(state) -> str:
    return "/patcher/calendar/jump" if state else "/"


@router.post("/patcher/calendar/on")
def calendar_on():
    try:
        turn_on()
        return redirect("/patcher/calendar/jump", message=f"Calendar on. {DEPLOY_NOTE}")
    except Exception as exc:
        return redirect(error=str(exc))


@router.post("/patcher/calendar/off")
def calendar_off():
    turn_off()
    return redirect(message="Calendar off. Choose content and tap Apply.")


@router.post("/patcher/calendar/speed")
def calendar_speed(speed: str = Form(...)):
    state = read_state()
    if not state:
        return redirect(error="The calendar is off")
    try:
        set_speed(state, speed)
        write_state(state)
        return redirect(_home(state), message=f"Speed saved. {DEPLOY_NOTE}")
    except Exception as exc:
        return redirect(_home(state), error=str(exc))


# Banner art for the timeline is decoded from the game files with Bunker's
# assetbundle decoder (archive.media.Textures) — not available here. The
# timeline keeps its links; they answer 404 instead of art.
@router.get("/patcher/calendar/art")
def calendar_art(path: str):
    raise HTTPException(404)


@router.post("/patcher/calendar/timeline")
def calendar_timeline(hidden: str = Form("")):
    set_timeline_hidden([k for k in hidden.split(",") if k])
    return Response(status_code=204)


@router.post("/patcher/calendar/view")
def calendar_view(on: str = Form("")):
    state = read_state()
    if not state:
        return redirect(error="The calendar is off")
    try:
        set_original_dates(state, on == "1")
        write_state(state)
        return redirect(_home(state), message=f"Saved. {DEPLOY_NOTE}")
    except Exception as exc:
        return redirect(_home(state), error=str(exc))


@router.post("/patcher/calendar/jump")
def calendar_jump(target: int = Form(...)):
    state = read_state()
    if not state:
        return redirect(error="The calendar is off")
    try:
        jump(state, target)
        write_state(state)
        return redirect(_home(state), message=f"Moved to week {week_of(state, game_now(state))}. {DEPLOY_NOTE}")
    except Exception as exc:
        return redirect(_home(state), error=str(exc))


@router.get("/patcher/calendar/next")
def calendar_next(request: Request, by: str = "update", message: str | None = None, error: str | None = None):
    state = read_state()
    if not state:
        return redirect(error="The calendar is off")
    now = game_now(state)
    items = entries()
    if by == "week":
        target = now + WEEK
        title, title_key = "Next week", "cal.next_week"
    else:
        upcoming = updates(state, items, now)
        if not upcoming:
            return redirect(_home(state), error="No more updates in this loop")
        target, title = upcoming[0]["at"], "Next update"
        title_key = "cal.next_update"
    if target >= loop_end(state):
        return redirect(_home(state), error="That is past the end of this loop")
    view = preview(state, items, now, target)

    def listed(moments):
        found = [e for e, at in moments if e[0] in ("event", "summon", "shop") and now < at <= view["arrive"]]
        return {"names": [e[1] for e in found if e[1]], "more": sum(1 for e in found if not e[1])}
    opens = listed((e, e[2]) for e in items)
    closes = listed((e, e[3]) for e in items)
    return templates.TemplateResponse(request, "calendar_next.html", {"active": "calendar", "title": title, "title_key": title_key,
        "target": target, "view": view, "opens": opens, "closes": closes,
        "message": message, "error": error, "degraded": degraded_notes()})


@router.get("/patcher/calendar/jump")
def calendar_jump_view(request: Request, at: int = 0, message: str | None = None, error: str | None = None):
    state = read_state()
    if not state:
        return redirect(error="The calendar is off")
    now = game_now(state)
    items = entries()
    upcoming = updates(state, items, now)
    week = week_of(state, now)
    target = at or (upcoming[0]["at"] if upcoming else 0)
    view = preview(state, items, now, target) if now < target < loop_end(state) else None
    return templates.TemplateResponse(request, "calendar_jump.html", {"active": "calendar", "now": now, "week": week,
        "today": day_label(now), "weeks": LOOP_WEEKS, "updates": upcoming, "target": target, "view": view,
        "loopStart": state["loopStart"], "weekMillis": WEEK,
        "message": message, "error": error, "degraded": degraded_notes()})
