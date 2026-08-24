"""Backup and restore operations for game.db.

- create_backup() uses sqlite3.Connection.backup() so it is safe to run while
  lunar-tear has the database open.
- restore_backup() refuses if lunar-tear appears to be running (port probe),
  takes a safety pre-restore backup, then copies the chosen file over game.db.
- list_backups() / prune_to_last_n() manage the rolling pool under data/backups/.
"""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from web import config


class RestoreBlocked(Exception):
    """Raised when a restore is refused for safety reasons."""


VALID_REASONS = (
    "manual", "auto", "item-editor", "costume-editor", "weapon-editor",
    "upgrade-manager", "memoir-editor", "mission-editor", "quest-editor", "pre-restore",
)

# Display labels used by templates. Filename forms stay kebab-case for safety.
REASON_LABELS: dict[str, str] = {
    "manual": "Manual",
    "auto": "Auto",
    "item-editor": "Item Editor",
    "costume-editor": "Costume Editor",
    "weapon-editor": "Weapon Editor",
    "upgrade-manager": "Upgrade Manager",
    "memoir-editor": "Memoir Editor",
    "mission-editor": "Mission Editor",
    "quest-editor": "Quest Editor",
    "pre-restore": "Pre-Restore",
}


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


@dataclass(frozen=True)
class BackupInfo:
    filename: str
    path: Path
    created_at: datetime
    size_bytes: int
    reason: str

    @property
    def size_human(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @property
    def reason_display(self) -> str:
        return reason_label(self.reason)


# Where backups are stored. The /backups page can override the default
# (data/backups) with a custom directory, persisted server-side in
# data/backup_dir.txt so every backup (manual and pre-mutation) honors it.
_BACKUP_DIR_OVERRIDE = config.DATA_DIR / "backup_dir.txt"


def get_backup_dir() -> Path:
    """The configured backup directory (custom override or the default), created
    on demand. The override is persisted server-side in data/backup_dir.txt."""
    try:
        text = _BACKUP_DIR_OVERRIDE.read_text(encoding="utf-8").strip()
        if text:
            d = Path(text).expanduser().resolve()
            d.mkdir(parents=True, exist_ok=True)
            return d
    except OSError:
        pass
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return config.BACKUP_DIR


def set_backup_dir(path: str) -> Path:
    """Persist a custom backup directory (created automatically if missing)."""
    d = Path(path).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir():
        raise ValueError(f"not a directory: {d}")
    _BACKUP_DIR_OVERRIDE.write_text(str(d), encoding="utf-8")
    return d


def reset_backup_dir() -> Path:
    """Clear the override so backups go to the default data/backups again."""
    try:
        _BACKUP_DIR_OVERRIDE.unlink()
    except FileNotFoundError:
        pass
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return config.BACKUP_DIR


def ensure_dirs() -> None:
    get_backup_dir().mkdir(parents=True, exist_ok=True)


def create_backup(reason: str = "manual", backup_dir: str | None = None) -> BackupInfo:
    """Take a live-safe snapshot of game.db, stored in the configured backup
    directory (or `backup_dir` when given). Prunes to BACKUP_RETENTION
    afterward."""
    if reason not in VALID_REASONS:
        raise ValueError(f"reason must be one of {VALID_REASONS}, got {reason!r}")
    if not config.GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Game database not found: {config.GAME_DB_PATH}")

    dest_dir = get_backup_dir()
    if backup_dir:
        dest_dir = set_backup_dir(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"backup_{timestamp}_{reason}.db"
    dest = dest_dir / filename

    src = sqlite3.connect(str(config.GAME_DB_PATH))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    prune_to_last_n(config.BACKUP_RETENTION)
    return _info_from_path(dest)


def list_backups() -> list[BackupInfo]:
    """Return all backups (from the configured directory), newest first."""
    dest_dir = get_backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        dest_dir.glob("backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [_info_from_path(f) for f in files]


def prune_to_last_n(n: int) -> int:
    """Delete the oldest backups beyond the n most recent. Returns count deleted."""
    backups = list_backups()
    excess = backups[n:]
    for b in excess:
        try:
            b.path.unlink()
        except OSError:
            pass
    return len(excess)


def detect_lunar_tear_running() -> str | None:
    """Return a human-readable reason string if lunar-tear is detected as running.

    Probes the gRPC port (8003 by default, or whatever is in .wizard.json). If
    something is listening, lunar-tear is almost certainly up.
    """
    grpc_port = config.LUNAR_TEAR_DEFAULT_GRPC_PORT
    if config.WIZARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(config.WIZARD_CONFIG_PATH.read_text(encoding="utf-8"))
            grpc_port = int(cfg.get("grpc_port", grpc_port))
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        if sock.connect_ex(("127.0.0.1", grpc_port)) == 0:
            return f"lunar-tear gRPC server is listening on port {grpc_port}. Stop it before restoring."
    finally:
        sock.close()
    return None


def restore_backup(filename: str) -> BackupInfo:
    """Overwrite game.db with the named backup file.

    Refuses if lunar-tear is running. Takes a safety pre-restore backup first.
    Returns the BackupInfo of the restored source.
    """
    dest_dir = get_backup_dir()
    backup_path = (dest_dir / filename).resolve()
    if backup_path.parent != dest_dir.resolve():
        raise FileNotFoundError(f"Backup not found: {filename}")
    if not backup_path.exists() or not backup_path.is_file():
        raise FileNotFoundError(f"Backup not found: {filename}")

    blocker = detect_lunar_tear_running()
    if blocker:
        raise RestoreBlocked(blocker)

    if config.GAME_DB_PATH.exists():
        create_backup(reason="pre-restore")

    # Remove stale WAL/SHM sidecars so SQLite reopens cleanly.
    for sidecar_name in (config.GAME_DB_PATH.name + "-wal", config.GAME_DB_PATH.name + "-shm"):
        sidecar = config.GAME_DB_PATH.parent / sidecar_name
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    config.GAME_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(backup_path, config.GAME_DB_PATH)
    return _info_from_path(backup_path)


def _info_from_path(p: Path) -> BackupInfo:
    stat = p.stat()
    stem = p.stem  # backup_YYYY-MM-DDTHH-MM-SS_reason
    parts = stem.split("_", 2)
    ts_str = parts[1] if len(parts) > 1 else ""
    reason = parts[2] if len(parts) > 2 else "manual"
    try:
        created = datetime.strptime(ts_str, "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        created = datetime.fromtimestamp(stat.st_mtime)
    return BackupInfo(
        filename=p.name,
        path=p,
        created_at=created,
        size_bytes=stat.st_size,
        reason=reason,
    )
