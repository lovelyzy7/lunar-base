"""/settings page: what this build can actually change.

There is no login, no host/port to configure and no server to start from here:
the panel runs inside the game's app, on loopback, and the launcher owns the
server. So the page shows read-only status and the display/language choices,
which live in the browser (localStorage) and apply instantly.

`GET /settings/state` returns the same status as JSON for the page's refresh.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _status() -> dict[str, Any]:
    """Read-only facts about this installation."""
    game_db = config.GAME_DB_PATH
    master = config.find_master_data_bin() if hasattr(config, "find_master_data_bin") else None
    try:
        region = config.SERVER_REGION if hasattr(config, "SERVER_REGION") else ""
    except Exception:
        region = ""
    return {
        "game_db": game_db.is_file(),
        "game_db_path": str(game_db),
        "master_data": bool(master and master.is_file()),
        "master_data_path": str(master) if master else "",
        "revisions": bool(getattr(config, "REVISIONS_DIR", None) and config.REVISIONS_DIR.is_dir()),
        "region": region,
        "retention": getattr(config, "BACKUP_RETENTION", 0),
        "backup_dir": str(getattr(config, "BACKUP_DIR", "")),
        "data_dir": str(getattr(config, "DATA_DIR", "")),
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"active": "settings", "status": _status()})


@router.get("/settings/state")
def settings_state() -> JSONResponse:
    return JSONResponse({"ok": True, "status": _status()})
