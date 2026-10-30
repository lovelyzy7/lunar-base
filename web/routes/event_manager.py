"""活动管理 / Event Manager — merged event-related panels.

One page for every dated-content family that previously lived on the Admin →
Events panel (and its duplicates): 活动 (event quests), 商店 (shops), 卡池
(summon banners) and 任务 (mission terms). Sections share one APPLY bar and the
same JSON API shape the old /admin/events endpoints used:

  GET  /events                 the page (four small sections)
  GET  /events/state           per-section active/window state (Ajax refresh)
  POST /events/apply           rebuild the bin for the selected ids per section
  POST /events/reorder         persist a display order (sections with sort cols)
  GET  /events/bins            every manageable *.bin.e + the active one
  POST /events/bin/activate    rename a chosen bin.e to the canonical name

The writable range is documented on the page: 40 catalogued event systems
(zh-l10n/work/diag-events/EVENT-CATALOG.md) funnel into these four sections
through the master-data tables whose Start/EndDatetime columns the bin rewriter
understands (web/services/event_service.py::_KINDS).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import event_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))

# Page section order + labels (活动/商店/卡池/任务). Labels are plain Chinese
# here because this page is the 汉化 panel surface; the i18n layer may still
# override them via em.section.* keys.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("quest", "活动", "em.section_events"),
    ("shop", "商店", "em.section_shops"),
    ("banner", "卡池", "em.section_banners"),
    ("mission", "任务", "em.section_missions"),
)

# Systems with no Start/EndDatetime window the rewriter can edit (control points
# live in other tables, see EVENT-CATALOG.md) are documented here so the page
# states its real writable range instead of over-promising.
UNWRITABLE_NOTE = (
    "月票/MissionPass、竞技场/PvP、大狩猎/BigHunt 等系统的档期在各自专表 "
    "(m_mission_pass / m_pvp_season / m_big_hunt_schedule …)，不在本页四分区的写入面内。"
)


def _groups() -> tuple[list[dict], str | None]:
    groups: list[dict] = []
    error = None
    wanted = {key: (label, i18n) for key, label, i18n in SECTIONS}
    for k in event_service.kinds():
        if k.key not in wanted:
            continue
        label, i18n = wanted[k.key]
        try:
            rows = event_service.list_events(k.key)
        except (FileNotFoundError, KeyError, OSError, ValueError) as e:  # keep other sections alive
            error = str(e)
            rows = []
        cats: dict[str, list[int]] = {}
        for r in rows:
            if not r.category:
                continue
            c = cats.setdefault(r.category, [0, 0])
            c[1] += 1
            if r.active:
                c[0] += 1
        categories = [{"name": n, "active": a, "total": t} for n, (a, t) in sorted(cats.items())]
        groups.append({
            "key": k.key, "label": label, "i18n": i18n,
            "rows": rows, "categories": categories,
            "writable": bool(event_service._SORT_COLS.get(k.key)),  # reorder only where a sort col exists
        })
    order = {key: i for i, (key, _, _) in enumerate(SECTIONS)}
    groups.sort(key=lambda g: order.get(g["key"], 99))
    return groups, error


@router.get("/events", response_class=HTMLResponse)
def event_manager_view(request: Request, message: str | None = None, error: str | None = None):
    groups, load_error = _groups()
    bin_path = None
    try:
        bin_path = str(event_service.masterdata_bin.bin_path())
    except (FileNotFoundError, OSError, ValueError) as e:
        load_error = load_error or str(e)
    return templates.TemplateResponse(
        request,
        "event_manager.html",
        {"active": "events", "groups": groups, "message": message,
         "error": load_error or error, "bin_path": bin_path,
         "bin_name": Path(bin_path).name if bin_path else "master-data",
         "unwritable_note": UNWRITABLE_NOTE,
         "server_platform": sys.platform},
    )


@router.get("/events/state")
def event_manager_state(work_dir: str | None = None) -> JSONResponse:
    try:
        data = event_service.event_states(work_dir)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **data})


@router.post("/events/apply")
def event_manager_apply(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """payload: {"selections": {"quest": [ids...], "shop": [...], "banner": [...],
    "mission": [...]}, "backup_suffix": "...", "work_dir": "..."}"""
    selections = payload.get("selections")
    if not isinstance(selections, dict) or not selections:
        return JSONResponse({"ok": False, "error": "selections object is required"}, status_code=400)
    parsed: dict[str, list[int]] = {}
    try:
        for kind, ids in selections.items():
            parsed[str(kind)] = [int(i) for i in ids]
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "selection ids must be integers"}, status_code=400)
    backup_suffix = payload.get("backup_suffix")
    if backup_suffix is not None and not isinstance(backup_suffix, str):
        return JSONResponse({"ok": False, "error": "backup_suffix must be a string"}, status_code=400)
    work_dir = payload.get("work_dir")
    if work_dir is not None and not isinstance(work_dir, str):
        return JSONResponse({"ok": False, "error": "work_dir must be a string"}, status_code=400)
    try:
        result = event_service.apply(parsed, backup_suffix=backup_suffix,
                                     work_dir=(work_dir.strip() if work_dir and work_dir.strip() else None))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except (FileNotFoundError, KeyError, OSError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **result})


@router.post("/events/reorder")
def event_manager_reorder(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """payload: {"kind": "quest"|"shop"|"banner", "ordered_ids": [...],
    "backup_suffix": "...", "work_dir": "..."} — mission terms have no sort
    column and are rejected by event_service.reorder."""
    kind = str(payload.get("kind", ""))
    ids = payload.get("ordered_ids")
    if not kind or not isinstance(ids, list):
        return JSONResponse({"ok": False, "error": "kind and ordered_ids[] are required"}, status_code=400)
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "ordered_ids must be integers"}, status_code=400)
    backup_suffix = payload.get("backup_suffix")
    if backup_suffix is not None and not isinstance(backup_suffix, str):
        return JSONResponse({"ok": False, "error": "backup_suffix must be a string"}, status_code=400)
    work_dir = payload.get("work_dir")
    if work_dir is not None and not isinstance(work_dir, str):
        return JSONResponse({"ok": False, "error": "work_dir must be a string"}, status_code=400)
    try:
        result = event_service.reorder(kind, ids, backup_suffix=backup_suffix,
                                       work_dir=(work_dir.strip() if work_dir and work_dir.strip() else None))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except (FileNotFoundError, KeyError, OSError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **result})


@router.get("/events/bins")
def event_manager_bins(work_dir: str | None = None) -> JSONResponse:
    try:
        data = event_service.list_bins(work_dir)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **data})


@router.post("/events/bin/activate")
def event_manager_bin_activate(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    path = payload.get("path")
    work_dir = payload.get("work_dir")
    if not isinstance(path, str) or not path.strip():
        return JSONResponse({"ok": False, "error": "path is required"}, status_code=400)
    if work_dir is not None and not isinstance(work_dir, str):
        return JSONResponse({"ok": False, "error": "work_dir must be a string"}, status_code=400)
    try:
        result = event_service.activate_bin(
            path.strip(),
            (work_dir.strip() if work_dir and work_dir.strip() else None))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except (FileNotFoundError, KeyError, OSError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **result})
