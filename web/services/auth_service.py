"""Authentication backed by lunar-tear's auth.db (read-only) + a local admin.

Two kinds of login:

  - **Game user** — verified against ``auth.db.auth_users`` (bcrypt). The auth
    account links to a game record via ``game.db users.facebook_id ==
    auth_users.id`` (set by lunar-tear's register-account). A game user may
    only ever touch their own record.

  - **Admin** — credentials stored in ``data/admin.json`` (bcrypt), never in
    auth.db, so auth.db stays read-only. Admin can reach every record.

Both databases are opened in SQLite read-only URI mode; this module never
writes to either game.db or auth.db.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import bcrypt

from web import config


@dataclass(frozen=True)
class GameLogin:
    auth_id: int
    username: str
    user_id: int  # game.db users.user_id bound to this account


def _check_bcrypt(password: str, hashed: bytes | str) -> bool:
    if isinstance(hashed, str):
        hashed = hashed.encode("utf-8")
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed)
    except (ValueError, TypeError):
        return False


# --- Game users (auth.db) --------------------------------------------------


def _auth_row(username: str) -> tuple[int, bytes] | None:
    if not config.AUTH_DB_PATH.exists():
        raise FileNotFoundError(f"Auth database not found: {config.AUTH_DB_PATH}")
    uri = f"{config.AUTH_DB_PATH.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT id, password FROM auth_users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return int(row[0]), row[1]


def _game_user_id_for_auth_id(auth_id: int) -> int | None:
    """Resolve the game record bound to an auth account via facebook_id."""
    if not config.GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Game database not found: {config.GAME_DB_PATH}")
    uri = f"{config.GAME_DB_PATH.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT user_id FROM users WHERE facebook_id = ?", (auth_id,)
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row is not None else None


def verify_game_user(username: str, password: str) -> GameLogin | None:
    """Verify a game login. Returns None on bad credentials.

    Raises ValueError if the credentials are valid but no game record is bound
    to the account (so the caller can show a clear, distinct message).
    """
    row = _auth_row(username)
    if row is None:
        return None
    auth_id, hashed = row
    if not _check_bcrypt(password, hashed):
        return None
    user_id = _game_user_id_for_auth_id(auth_id)
    if user_id is None:
        raise ValueError("This account has no game character linked yet.")
    return GameLogin(auth_id=auth_id, username=username, user_id=user_id)


# --- Admin (data/admin.json) ----------------------------------------------


def admin_configured() -> bool:
    return config.ADMIN_CONFIG_PATH.exists()


def admin_status() -> dict:
    """Public status for the /settings admin section."""
    admin = _load_admin() or {}
    return {
        "configured": admin_configured(),
        "username": str(admin.get("username") or ""),
    }


def set_admin_credentials(username: str, password: str) -> str:
    """Create or reset the local admin account (used by the /settings page).

    Writes the same data/admin.json format as tools/set_admin_password.py, so
    both paths stay interchangeable. auth.db is still never written.
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("Admin username cannot be empty.")
    if len(username) > 64:
        raise ValueError("Admin username is too long (max 64 characters).")
    if not password:
        raise ValueError("Password cannot be empty.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ADMIN_CONFIG_PATH.write_text(
        json.dumps({"username": username, "password_hash": password_hash}, indent=2) + "\n",
        encoding="utf-8",
    )
    return username


def _load_admin() -> dict | None:
    try:
        return json.loads(config.ADMIN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verify_admin(username: str, password: str) -> str | None:
    """Return the admin username on success, else None."""
    admin = _load_admin()
    if not admin:
        return None
    stored_user = admin.get("username")
    stored_hash = admin.get("password_hash")
    if not stored_user or not stored_hash:
        return None
    if username != stored_user:
        return None
    if not _check_bcrypt(password, stored_hash):
        return None
    return stored_user
