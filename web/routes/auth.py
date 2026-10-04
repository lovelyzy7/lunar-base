"""Login / logout for Lunar Base.

Admin credentials (data/admin.json) are tried first, then game accounts
(auth.db). On success the user's role + bound game record are stored in the
signed session cookie; the auth gate in web.app enforces access from there.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import auth_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = None):
    # Login control off, or already logged in? Send them on. /login is only
    # reachable while the auth gate is enabled.
    if not config.auth_enabled() or request.session.get("role"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "admin_configured": auth_service.admin_configured()},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    username = username.strip()

    # The form is only served while login control is on; reject stale posts.
    if not config.auth_enabled():
        return RedirectResponse(url="/", status_code=303)

    # 1) Admin.
    admin_name = auth_service.verify_admin(username, password)
    if admin_name:
        request.session.clear()
        request.session.update({"role": "admin", "username": admin_name})
        return RedirectResponse(url="/users", status_code=303)

    # 2) Game account.
    try:
        login = auth_service.verify_game_user(username, password)
    except ValueError as e:
        return _login_error(request, str(e))
    except FileNotFoundError:
        return _login_error(request, "Auth database is unavailable.")

    if login is not None:
        request.session.clear()
        request.session.update(
            {"role": "user", "username": login.username, "user_id": login.user_id}
        )
        return RedirectResponse(url=f"/users/{login.user_id}", status_code=303)

    return _login_error(request, "Invalid username or password.")


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _login_error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": message, "admin_configured": auth_service.admin_configured()},
        status_code=401,
    )
