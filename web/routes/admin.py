"""Admin routes: list event/banner availability straight from the master-data
bin, and repack the bin to match the chosen selection (with a dated backup)."""

from __future__ import annotations

from typing import Any

from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import event_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


@router.get("/admin/events", response_class=HTMLResponse)
def admin_events_view(request: Request, message: str | None = None, error: str | None = None):
    groups: list[dict] = []
    load_error = error
    bin_path = None
    try:
        bin_path = str(event_service.masterdata_bin.bin_path())
        for k in event_service.kinds():
            rows = event_service.list_events(k.key)
            cats: dict[str, list[int]] = {}
            for r in rows:
                if not r.category:
                    continue
                c = cats.setdefault(r.category, [0, 0])
                c[1] += 1
                if r.active:
                    c[0] += 1
            categories = [{"name": n, "active": a, "total": t} for n, (a, t) in sorted(cats.items())]
            groups.append({"key": k.key, "label": k.label, "rows": rows, "categories": categories})
    except (FileNotFoundError, KeyError, OSError, ValueError) as e:
        load_error = str(e)
        groups = []
    return templates.TemplateResponse(
        request,
        "admin_events.html",
        {"active": "admin", "groups": groups, "message": message,
         "error": load_error, "bin_path": bin_path,
         "bin_name": Path(bin_path).name if bin_path else "master-data"},
    )


@router.post("/admin/events/apply")
def admin_events_apply(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """payload: {"selections": {"quest": [ids...], "banner": [ids...]},
    "backup_suffix": "custom-filename-middle" (optional).}"""
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


@router.get("/admin/events/bins")
def admin_events_bins(work_dir: str | None = None) -> JSONResponse:
    """List every *.bin.e under the working directory (default: the server's
    release directory) plus which one is currently active."""
    try:
        data = event_service.list_bins(work_dir)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **data})


@router.get("/admin/events/state")
def admin_events_state(work_dir: str | None = None) -> JSONResponse:
    """Per-row active/on-off state of the current work bin, for Ajax refresh."""
    try:
        data = event_service.event_states(work_dir)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **data})


@router.post("/admin/events/bin/activate")
def admin_events_bin_activate(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """payload: {"path": "/abs/path/to/some.bin.e", "work_dir": "/optional/path"}
    — rename the chosen bin.e to the canonical name (20240404193219.bin.e) in
    its own directory so it becomes the active master-data bin."""
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


@router.post("/admin/events/reorder")
def admin_events_reorder(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """payload: {"kind": "quest"|"banner", "ordered_ids": [id, ...],
    "backup_suffix": "custom-filename-middle" (optional)} — set the
    display order to exactly this sequence (alphabetical, manual, whatever)."""
    kind = str(payload.get("kind", ""))
    ids = payload.get("ordered_ids")
    if kind not in ("quest", "banner") or not isinstance(ids, list):
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
