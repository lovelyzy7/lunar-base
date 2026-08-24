"""Routes for the backup/restore stage."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import backup_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


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
        },
    )


@router.post("/backups/create")
def create_backup_action(backup_dir: str = Form("")):
    try:
        info = backup_service.create_backup(
            reason="manual", backup_dir=(backup_dir.strip() or None))
    except FileNotFoundError as e:
        return _redirect("/backups", error=str(e))
    except ValueError as e:
        return _redirect("/backups", error=str(e))
    except Exception as e:
        return _redirect("/backups", error=f"Backup failed: {e}")
    return _redirect("/backups", message=f"Created {info.filename} ({info.size_human}).")


@router.post("/backups/set_dir")
def set_backup_dir_action(backup_dir: str = Form(...)):
    if not backup_dir.strip():
        return _redirect("/backups", error="Backup directory is required.")
    try:
        d = backup_service.set_backup_dir(backup_dir.strip())
    except Exception as e:
        return _redirect("/backups", error=f"Failed to set backup directory: {e}")
    return _redirect("/backups", message=f"Backup directory set: {d}")


@router.post("/backups/restore")
def restore_backup_action(filename: str = Form(...), confirm: str = Form(...)):
    if confirm.strip() != "RESTORE":
        return _redirect("/backups", error="Confirmation phrase did not match. Type RESTORE in uppercase to confirm.")
    try:
        info = backup_service.restore_backup(filename)
    except backup_service.RestoreBlocked as e:
        return _redirect("/backups", error=str(e))
    except FileNotFoundError as e:
        return _redirect("/backups", error=str(e))
    except Exception as e:
        return _redirect("/backups", error=f"Restore failed: {e}")
    return _redirect(
        "/backups",
        message=f"Restored from {info.filename}. A pre-restore safety backup was taken first.",
    )
