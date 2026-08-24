"""Lunar Base FastAPI entrypoint.

Run with:
    python -m uvicorn web.app:app --host 127.0.0.1 --port 8888

(or use the run-lunar-base.bat / run-lunar-base.sh helper, which honor the
LUNAR_BASE_HOST and LUNAR_BASE_PORT environment variables)

Access control: a session cookie holds {role, username, user_id}. The auth
gate below redirects anonymous requests to /login, scopes game users to their
own /users/{id} record, and keeps admin-only areas (Users list, Save Data,
Admin) for the admin account.
"""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from web import config, session
from web.routes import admin as admin_routes
from web.routes import auth as auth_routes
from web.routes import backup as backup_routes
from web.routes import costume_editor as costume_editor_routes
from web.routes import item_editor as item_editor_routes
from web.routes import memoir_editor as memoir_editor_routes
from web.routes import mission_editor as mission_editor_routes
from web.routes import quest_editor as quest_editor_routes
from web.routes import upgrade_manager as upgrade_manager_routes
from web.routes import users as users_routes
from web.routes import weapon_editor as weapon_editor_routes

# Requests that never require a session.
_PUBLIC_PREFIXES = ("/login", "/logout", "/static", "/favicon")
# Areas only the admin account may reach.
_ADMIN_ONLY_PREFIXES = ("/admin", "/backups")
# Per-user record path, e.g. /users/5 or /users/5/edit/items.
_USER_PATH = re.compile(r"^/users/(\d+)(?:/|$)")
# Nav entry points -> the per-user suffix, so a game user lands on their own
# editor instead of the (admin-only) picker.
_ENTRY_SUFFIX = {
    "/items": "/edit/items",
    "/costumes": "/edit/costumes",
    "/weapons": "/edit/weapons",
    "/upgrades": "/upgrades",
    "/memoirs": "/memoirs",
    "/missions": "/edit/missions",
    "/quests": "/edit/quests",
}


def gate_decision(path: str, role: str | None, uid: int | None) -> tuple[str, str | None]:
    """Pure access-control decision for one request path.

    Returns one of:
      ("pass", None)            -> allow the request through
      ("redirect", "/login")    -> redirect to the given URL
      ("forbid", None)          -> 403

    Kept side-effect-free so it can be unit tested without HTTP.
    """
    if path.startswith(_PUBLIC_PREFIXES):
        return ("pass", None)
    if not role:
        return ("redirect", "/login")
    if role == "admin":
        return ("pass", None)
    # role == "user": locked to their own record, no admin-only areas.
    if path in ("/users", "/users/"):
        return ("redirect", f"/users/{uid}")
    if path in _ENTRY_SUFFIX:
        return ("redirect", f"/users/{uid}{_ENTRY_SUFFIX[path]}")
    if path.startswith(_ADMIN_ONLY_PREFIXES):
        return ("forbid", None)
    match = _USER_PATH.match(path)
    if match and int(match.group(1)) != uid:
        return ("forbid", None)
    return ("pass", None)


def _forbid(request: Request) -> JSONResponse | HTMLResponse:
    """403 for a game user reaching another record or an admin-only area."""
    if request.method != "GET":
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    return HTMLResponse(
        "<h1>403 Forbidden</h1><p>You can only access your own record. "
        '<a href="/">Return</a> &middot; <a href="/logout">Logout</a></p>',
        status_code=403,
    )


def _static_version() -> str:
    """Cache-busting stamp for static assets. Bumped automatically whenever
    i18n.js / automata.css change, so browsers never serve a stale copy (a
    stale i18n.js without the shared modal would leave every confirm-button
    silent)."""
    stamp = 0
    for name in ("web/static/js/i18n.js", "web/static/css/automata.css"):
        try:
            stamp = max(stamp, int((config.ROOT / name).stat().st_mtime))
        except OSError:
            pass
    return str(stamp)


def create_app() -> FastAPI:
    app = FastAPI(title="Lunar Base", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(
        "/static",
        StaticFiles(directory=str(config.ROOT / "web" / "static")),
        name="static",
    )
    app.include_router(auth_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(users_routes.router)
    app.include_router(item_editor_routes.router)
    app.include_router(costume_editor_routes.router)
    app.include_router(weapon_editor_routes.router)
    app.include_router(upgrade_manager_routes.router)
    app.include_router(memoir_editor_routes.router)
    app.include_router(mission_editor_routes.router)
    app.include_router(quest_editor_routes.router)
    app.include_router(admin_routes.router)

    # Middleware registration order matters: the LAST added is the OUTERMOST.
    # We want, from outside in: SessionMiddleware -> auth_gate -> remember.
    # So a request that the gate forbids never reaches `remember` and cannot
    # poison the remembered-user cookie with an off-limits id.

    # Innermost: remember the last user the operator successfully opened, so the
    # top nav can jump straight to that user's editors. Skips non-2xx responses
    # (e.g. a "user not found" redirect) so only real selections are stored.
    @app.middleware("http")
    async def remember_selected_user(request: Request, call_next):
        response = await call_next(request)
        match = _USER_PATH.match(request.url.path)
        if match and response.status_code < 300:
            response.set_cookie(
                session.COOKIE_NAME,
                match.group(1),
                max_age=session.COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
        return response

    # Middle: access control. Anonymous -> /login; game users are scoped to
    # their own /users/{id} record; admin-only areas stay admin-only.
    # Disabled by default: when --auth / LUNAR_BASE_AUTH is not set the gate is
    # a no-op and every record is fully accessible (original behaviour).
    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        enabled = config.auth_enabled()
        # Exposed to templates so base.html can show the right nav (full nav +
        # no login pill when auth is off).
        request.state.auth_enabled = enabled
        request.state.static_version = _static_version()
        # The user this page is *about*, taken from the URL. base.html prefers
        # this over the global lb_user cookie so each tab stays on its own user
        # (open user 2 in a new tab without hijacking user 5 in another tab).
        m = _USER_PATH.match(request.url.path)
        request.state.current_user_id = int(m.group(1)) if m else None
        if not enabled:
            return await call_next(request)
        action, target = gate_decision(
            request.url.path,
            request.session.get("role"),
            request.session.get("user_id"),
        )
        if action == "redirect":
            return RedirectResponse(url=target, status_code=303)
        if action == "forbid":
            return _forbid(request)
        return await call_next(request)

    # Outermost: populate request.session before the gate reads it.
    app.add_middleware(SessionMiddleware, secret_key=config.get_session_secret(), same_site="lax")

    return app


app = create_app()
