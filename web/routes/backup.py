"""Routes for the backup/restore stage."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import backup_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _json_or_redirect(as_json: bool, target: str, *, message: str | None = None, error: str | None = None):
    """Ajax callers get JSON; plain form posts keep the redirect behaviour."""
    if as_json:
        status = 400 if error else 200
        return JSONResponse({"ok": error is None, "error": error, "message": message}, status_code=status)
    return _redirect(target, message=message, error=error)


def _redirect(target: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    qs = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{target}{qs}", status_code=303)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active": "home",
            "message": message,
            "error": error,
            "game_db_exists": config.GAME_DB_PATH.exists(),
            "game_db_path": config.GAME_DB_PATH,
            "lunar_tear_running": backup_service.detect_lunar_tear_running(),
        },
    )


@router.get("/backups", response_class=HTMLResponse)
def list_backups(request: Request, message: str | None = None, error: str | None = None):
    try:
        backup_dir = str(backup_service.get_backup_dir())
    except OSError as e:
        return _redirect("/", error=str(e))
    return templates.TemplateResponse(
        request,
        "backup.html",
        {
            "active": "backups",
            "message": message,
            "error": error,
            "backups": backup_service.list_backups(),
            "retention": config.BACKUP_RETENTION,
            "game_db_exists": config.GAME_DB_PATH.exists(),
            "game_db_path": config.GAME_DB_PATH,
            "lunar_tear_running": backup_service.detect_lunar_tear_running(),
            "backup_dir": backup_dir,
            "default_backup_dir": str(config.BACKUP_DIR),
            "state_json": _state(),
        },
    )


def _state() -> dict:
    """Snapshot of everything the Save Data page shows, for Ajax refresh."""
    try:
        backup_dir = str(backup_service.get_backup_dir())
    except OSError:
        backup_dir = str(config.BACKUP_DIR)
    return {
        "backup_dir": backup_dir,
        "default_backup_dir": str(config.BACKUP_DIR),
        "retention": config.BACKUP_RETENTION,
        "game_db_exists": config.GAME_DB_PATH.exists(),
        "game_db_path": str(config.GAME_DB_PATH),
        "lunar_tear_running": backup_service.detect_lunar_tear_running(),
        "backups": [
            {
                "filename": b.filename,
                "created_at": b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": b.reason,
                "reason_display": b.reason_display,
                "size_human": b.size_human,
            }
            for b in backup_service.list_backups()
        ],
    }


@router.get("/backups/state")
def backups_state() -> JSONResponse:
    """Current archives + settings, used for in-place Ajax refresh."""
    try:
        return JSONResponse({"ok": True, **_state()})
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def _post_body(request: Request) -> tuple[bool, dict]:
    """(is_json, body). JSON bodies come from Ajax; form bodies keep working."""
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("application/json"):
        try:
            data = await request.json()
        except Exception:
            data = {}
        return True, (data if isinstance(data, dict) else {})
    try:
        form = await request.form()
    except Exception:
        form = {}
    return False, dict(form)


@router.post("/backups/create")
async def create_backup_action(request: Request):
    """Create a backup. JSON body -> JSON reply (Ajax); form -> redirect."""
    as_json, body = await _post_body(request)
    backup_dir = str(body.get("backup_dir") or "")
    try:
        info = backup_service.create_backup(
            reason="manual", backup_dir=(backup_dir.strip() or None))
    except FileNotFoundError as e:
        return _json_or_redirect(as_json, "/backups", error=str(e))
    except ValueError as e:
        return _json_or_redirect(as_json, "/backups", error=str(e))
    except Exception as e:
        return _json_or_redirect(as_json, "/backups", error=f"Backup failed: {e}")
    if as_json:
        return JSONResponse({"ok": True, "filename": info.filename, "size": info.size_human, **_state()})
    return _redirect("/backups", message=f"Created {info.filename} ({info.size_human}).")


@router.post("/backups/set_dir")
async def set_backup_dir_action(request: Request):
    as_json, body = await _post_body(request)
    backup_dir = str(body.get("backup_dir") or "")
    if not backup_dir.strip():
        return _json_or_redirect(as_json, "/backups", error="Backup directory is required.")
    try:
        d = backup_service.set_backup_dir(backup_dir.strip())
    except Exception as e:
        return _json_or_redirect(as_json, "/backups", error=f"Failed to set backup directory: {e}")
    if as_json:
        return JSONResponse({"ok": True, "backup_dir": str(d), **_state()})
    return _redirect("/backups", message=f"Backup directory set: {d}")


@router.post("/backups/restore")
async def restore_backup_action(request: Request):
    as_json, body = await _post_body(request)
    filename = str(body.get("filename") or "")
    confirm = str(body.get("confirm") or "")
    if confirm.strip() != "RESTORE":
        return _json_or_redirect(as_json, "/backups",
                                 error="Confirmation phrase did not match. Type RESTORE in uppercase to confirm.")
    try:
        info = backup_service.restore_backup(filename)
    except backup_service.RestoreBlocked as e:
        return _json_or_redirect(as_json, "/backups", error=str(e))
    except FileNotFoundError as e:
        return _json_or_redirect(as_json, "/backups", error=str(e))
    except Exception as e:
        return _json_or_redirect(as_json, "/backups", error=f"Restore failed: {e}")
    message = f"Restored from {info.filename}. A pre-restore safety backup was taken first."
    if as_json:
        return JSONResponse({"ok": True, "message": message, **_state()})
    return _redirect("/backups", message=message)
