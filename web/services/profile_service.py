"""Edit the account-level fields shown on the Profile page.

Display name, message, level, exp and paid/free gems live in lunar-tear's
`user_profile` / `user_status` / `user_gem` tables. This service writes them
through the Go shim (`set_user_info`) so the change goes through lunar-tear's own
`UpdateUser` transaction — one automatic backup first, exactly like every other
mutation in this app.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from web import config
from web.services import backup_service

BACKUP_REASON = "profile-editor"

# The game truncates overly long strings itself; we reject them up-front so the
# operator sees a clear message instead of a silent cut.
MAX_NAME_LEN = 32
MAX_MESSAGE_LEN = 128


class ProfileError(Exception):
    """Raised when validation fails or the shim invocation errors out."""


@dataclass(frozen=True)
class InfoOutcome:
    applied: int
    duration_ms: int


def _ensure_shim_available() -> None:
    if not config.GRANT_EXE_PATH.exists():
        raise ProfileError(
            f"{config.GRANT_EXE_PATH.name} not found at {config.GRANT_EXE_PATH}. "
            f"Run {config.SETUP_SCRIPT} to build it (Go must be on PATH)."
        )


def _invoke_shim(payload: dict) -> dict:
    proc = subprocess.run(
        [str(config.GRANT_EXE_PATH)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    try:
        result = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        raise ProfileError(
            f"grant shim returned non-JSON output (exit={proc.returncode}): {stdout!r} {stderr!r}"
        )
    if proc.returncode != 0 or not result.get("ok"):
        raise ProfileError(result.get("error") or stderr or f"shim exited {proc.returncode}")
    return result


def exp_curve() -> list[int]:
    """Cumulative EXP thresholds by level — numerical parameter map id 1, the
    same curve lunar-tear's LevelAndCap uses. Index = level. Empty on failure."""
    path = config.MASTERDATA_DIR / "EntityMNumericalParameterMapTable.json"
    curve: list[int] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            recs = data if isinstance(data, list) else data.get("records", [])
            rows = [r for r in recs if r.get("NumericalParameterMapId") == 1]
            if rows:
                size = max(int(r["ParameterKey"]) for r in rows) + 1
                curve = [0] * size
                for r in rows:
                    curve[int(r["ParameterKey"])] = int(r["ParameterValue"])
        except (OSError, ValueError, KeyError):
            curve = []
    return curve


def _derive_level_exp(level: int | None, exp: int | None) -> tuple[int | None, int | None]:
    """Keep level and exp consistent with the game's curve (LevelAndCap):

    * EXP is authoritative — when exp is given (even together with a level),
      the level is derived from it (and exp is capped to the curve's max);
    * when ONLY a level is given, exp is set to that level's threshold (the
      minimum exp required to BE that level).
    Falls back to the raw values when the curve is unavailable.
    """
    curve = exp_curve()
    if not curve:
        return level, exp
    if exp is not None:
        exp = min(int(exp), curve[-1])
        lvl = 1
        for i in range(1, len(curve)):
            if exp >= curve[i]:
                lvl = i
            else:
                break
        return lvl, exp
    if level is not None:
        lvl = max(1, min(int(level), len(curve) - 1))
        return lvl, curve[lvl]
    return level, exp


def set_user_info(
    user_id: int,
    *,
    name: str | None = None,
    message: str | None = None,
    level: int | None = None,
    exp: int | None = None,
    paid_gem: int | None = None,
    free_gem: int | None = None,
) -> InfoOutcome:
    """Update only the provided fields (None = leave unchanged). Level and exp
    are kept consistent: they are never stored as a mismatched pair."""
    if user_id <= 0:
        raise ProfileError("user_id must be positive")

    payload: dict = {
        "action": "set_user_info",
        "db_path": str(config.GAME_DB_PATH),
        "user_id": user_id,
    }
    count = 0
    if name is not None:
        if len(name) > MAX_NAME_LEN:
            raise ProfileError(f"name must be at most {MAX_NAME_LEN} characters")
        payload["name"] = name
        count += 1
    if message is not None:
        if len(message) > MAX_MESSAGE_LEN:
            raise ProfileError(f"message must be at most {MAX_MESSAGE_LEN} characters")
        payload["message"] = message
        count += 1
    # 等级与经验挂钩（经验为准；只改等级则补该级门槛经验）
    level, exp = _derive_level_exp(level, exp)
    for key, value in (("level", level), ("exp", exp), ("paid_gem", paid_gem), ("free_gem", free_gem)):
        if value is None:
            continue
        if value < 0:
            raise ProfileError(f"{key} must not be negative")
        payload[key] = int(value)
        count += 1
    if count == 0:
        raise ProfileError("nothing to update")

    _ensure_shim_available()
    backup_service.create_backup(reason=BACKUP_REASON)
    started = time.monotonic()
    result = _invoke_shim(payload)
    duration_ms = int((time.monotonic() - started) * 1000)
    return InfoOutcome(applied=int(result.get("applied", 0)), duration_ms=duration_ms)
