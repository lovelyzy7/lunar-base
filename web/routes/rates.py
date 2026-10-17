"""/rates page: the summon and roulette chances, saved next to the save file.

The game server polls both files, so saving applies to the next summon or spin
without restarting anything. Deleting a file (Reset) restores lunar-tear's own
rates.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import rates_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


@router.get("/rates", response_class=HTMLResponse)
def rates_view(request: Request):
    return templates.TemplateResponse(
        request, "rates.html", {"active": "rates", "state": rates_service.state()}
    )


@router.get("/rates/state")
def rates_state() -> JSONResponse:
    return JSONResponse({"ok": True, **rates_service.state()})


@router.post("/rates/save")
def rates_save(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Save one card's rates; the payload names the card ("summon"/"roulette")."""
    kind = str(payload.get("kind", ""))
    try:
        if kind == "summon":
            rates_service.save_summon(payload.get("rates") or {})
        elif kind == "roulette":
            rates_service.save_roulette(payload)
        else:
            return JSONResponse({"ok": False, "error": "kind must be 'summon' or 'roulette'"}, status_code=400)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    return JSONResponse({"ok": True, **rates_service.state()})


@router.post("/rates/reset")
def rates_reset(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        rates_service.reset(str(payload.get("kind", "")))
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    return JSONResponse({"ok": True, **rates_service.state()})
