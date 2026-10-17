"""Summon and roulette rates, edited next to the save file.

Two JSON files live in the folder that holds ``game.db`` (``LUNAR_TEAR_DIR``'s
``server/db`` by default, the save folder on Android):

  * ``gacha_rates.json`` — premium summon rates: ★4/★3 shares, the costume
    share of each rarity, the featured share, the 10-draw guarantee and whether
    multi-step banners boost ★4.
  * ``roulette_rates.json`` — the Global client's Special Gift Roulette: the
    chances of the five gem amounts, which are fixed at 10/30/50/100/3000
    because the client only animates those.

The game server reads both files (``internal/gacha/rates.go`` and
``roulette.go``) again whenever their mtime changes, so saving here takes
effect on the next summon / spin; deleting a file restores the built-in rates.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from web import config

# ``internal/gacha.DefaultRates`` in lunar-tear.
DEFAULT_SUMMON: dict[str, Any] = {
    "fourStarPercent": 5.0,
    "threeStarPercent": 15.0,
    "fourStarCostumeShare": 40.0,
    "threeStarCostumeShare": 100.0 / 3,
    "featuredPercent": 35.0,
    "stepUpBoost": True,
    "multiMinRarity": 3,
}

# ``internal/gacha.DefaultRouletteRates`` in lunar-tear.
ROULETTE_GEMS = (10, 30, 50, 100, 3000)
DEFAULT_ROULETTE: list[float] = [60.0, 25.0, 10.0, 4.7, 0.3]

SUMMON_KEYS = tuple(DEFAULT_SUMMON)


def summon_path() -> Path:
    return config.GAME_DB_PATH.parent / "gacha_rates.json"


def roulette_path() -> Path:
    return config.GAME_DB_PATH.parent / "roulette_rates.json"


def _same(left: float, right: float) -> bool:
    return abs(left - right) < 1e-9


def read_summon() -> dict[str, Any]:
    """The rates in force, plus how they were obtained.

    ``source`` is "file" when the saved file was used, "default" when there is
    no file and "invalid" when the file exists but is unusable (the defaults
    are returned then, as the game server does).
    """
    path = summon_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"rates": dict(DEFAULT_SUMMON), "source": "default", "path": str(path)}
    except (OSError, ValueError):
        return {"rates": dict(DEFAULT_SUMMON), "source": "invalid", "path": str(path)}
    merged = {key: saved.get(key, DEFAULT_SUMMON[key]) for key in SUMMON_KEYS}
    try:
        rates = _clean_summon(merged)
    except ValueError:
        return {"rates": dict(DEFAULT_SUMMON), "source": "invalid", "path": str(path)}
    return {"rates": rates, "source": "file", "path": str(path)}


def read_roulette() -> dict[str, Any]:
    """The roulette chances in force, in the order of ``ROULETTE_GEMS``."""
    path = roulette_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        percents = saved["percents"]
        if not isinstance(percents, list) or len(percents) != len(ROULETTE_GEMS):
            raise ValueError
        chances = [float(p) for p in percents]
        _validate_roulette(chances)
    except FileNotFoundError:
        return {"percents": list(DEFAULT_ROULETTE), "source": "default", "path": str(path)}
    except (OSError, ValueError, KeyError, TypeError):
        return {"percents": list(DEFAULT_ROULETTE), "source": "invalid", "path": str(path)}
    return {"percents": chances, "source": "file", "path": str(path)}


def _clean_summon(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce one payload to the file's schema and validate it."""
    rates = {
        "fourStarPercent": round(float(data["fourStarPercent"]), 4),
        "threeStarPercent": round(float(data["threeStarPercent"]), 4),
        "fourStarCostumeShare": round(float(data["fourStarCostumeShare"]), 4),
        "threeStarCostumeShare": round(float(data["threeStarCostumeShare"]), 4),
        "featuredPercent": round(float(data["featuredPercent"]), 4),
        "stepUpBoost": bool(data.get("stepUpBoost", DEFAULT_SUMMON["stepUpBoost"])),
        "multiMinRarity": int(data["multiMinRarity"]),
    }
    _validate_summon(rates)
    return rates


def _validate_summon(rates: dict[str, Any]) -> None:
    # Mirrors internal/gacha/rates.go's RateConfig.Validate.
    four = rates["fourStarPercent"]
    three = rates["threeStarPercent"]
    if not 0.1 <= four <= 100:
        raise ValueError("★4 rate must be between 0.1% and 100%")
    if three < 0 or four + three > 100:
        raise ValueError("★4 and ★3 together must be at most 100%")
    for key, label in (
        ("fourStarCostumeShare", "4★ costume share"),
        ("threeStarCostumeShare", "3★ costume share"),
        ("featuredPercent", "featured share"),
    ):
        value = rates[key]
        if not 0 <= value <= 100:
            raise ValueError(f"The {label} must be between 0% and 100%")
    if rates["multiMinRarity"] not in (3, 4):
        raise ValueError("The 10-summon guarantee must be ★3 or ★4")


def _validate_roulette(chances: list[float]) -> None:
    # Mirrors internal/gacha/roulette.go's RouletteRates.Validate.
    for chance in chances:
        if not 0 <= chance <= 100:
            raise ValueError("Each roulette chance must be between 0% and 100%")
    total = sum(chances)
    if abs(total - 100) > 0.001:
        raise ValueError(f"The roulette chances must add up to 100% (they add up to {total:.3f}%)")


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".tmp")
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(pending, path)


def save_summon(data: dict[str, Any]) -> dict[str, Any]:
    rates = _clean_summon({**DEFAULT_SUMMON, **data})
    _write_atomic(summon_path(), rates)
    return read_summon()


def save_roulette(data: dict[str, Any]) -> dict[str, Any]:
    percents = data.get("percents")
    if not isinstance(percents, list) or len(percents) != len(ROULETTE_GEMS):
        raise ValueError(f"The roulette needs {len(ROULETTE_GEMS)} chances")
    chances = [round(float(p), 4) for p in percents]
    _validate_roulette(chances)
    _write_atomic(roulette_path(), {"percents": chances})
    return read_roulette()


def reset(kind: str) -> dict[str, Any]:
    """Delete a rates file so the game server falls back to its own values."""
    if kind == "summon":
        summon_path().unlink(missing_ok=True)
        return read_summon()
    if kind == "roulette":
        roulette_path().unlink(missing_ok=True)
        return read_roulette()
    raise ValueError("kind must be 'summon' or 'roulette'")


def state() -> dict[str, Any]:
    """Everything /rates needs to render: both rate sets and their sources."""
    summon = read_summon()
    roulette = read_roulette()
    rates = summon["rates"]
    # The same split rates.go's Tiers() uses, for the preview table.
    fs = rates["fourStarCostumeShare"] / 100
    ts = rates["threeStarCostumeShare"] / 100
    preview = {
        "fourCostume": rates["fourStarPercent"] * fs,
        "fourWeapon": rates["fourStarPercent"] * (1 - fs),
        "threeCostume": rates["threeStarPercent"] * ts,
        "threeWeapon": rates["threeStarPercent"] * (1 - ts),
        "twoWeapon": 100 - rates["fourStarPercent"] - rates["threeStarPercent"],
    }
    return {
        "summon": summon,
        "roulette": dict(roulette, gems=list(ROULETTE_GEMS)),
        "default_summon": DEFAULT_SUMMON,
        "default_roulette": list(DEFAULT_ROULETTE),
        "preview": preview,
        "same_folder": str(config.GAME_DB_PATH.parent),
    }
