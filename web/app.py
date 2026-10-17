"""Lunar Base FastAPI entrypoint.

Run with:
    python -m uvicorn web.app:app --host 127.0.0.1 --port 8888

(or use the panel-start.sh / panel-start.bat helper at the repo root, which
auto-initializes on first run; a standalone base uses start.sh / start.bat)

This build has no login: the panel is a single-user tool, so every page is open
to whoever runs it. The session cookie, the auth gate, the login page and the
admin account are gone.
"""

from __future__ import annotations

import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from web import config, session
from web.routes import admin as admin_routes
from web.routes import backup as backup_routes
from web.routes import costume_editor as costume_editor_routes
from web.routes import item_editor as item_editor_routes
from web.routes import memoir_editor as memoir_editor_routes
from web.routes import profile as profile_routes
from web.routes import mission_editor as mission_editor_routes
from web.routes import quest_editor as quest_editor_routes
from web.routes import rates as rates_routes
from web.routes import settings as settings_routes
from web.routes import upgrade_manager as upgrade_manager_routes
from web.routes import users as users_routes
from web.routes import weapon_editor as weapon_editor_routes

# Per-user record path, e.g. /users/5 or /users/5/edit/items.
_USER_PATH = re.compile(r"^/users/(\d+)(?:/|$)")


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

    # Root favicon: browsers and old shortcuts ask for /favicon.ico directly
    # even when <link rel="icon"> tags exist.
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico():
        return FileResponse(
            config.ROOT / "web" / "static" / "img" / "favicon.ico",
            media_type="image/x-icon",
        )
    app.include_router(backup_routes.router)
    app.include_router(users_routes.router)
    app.include_router(item_editor_routes.router)
    app.include_router(costume_editor_routes.router)
    app.include_router(weapon_editor_routes.router)
    app.include_router(upgrade_manager_routes.router)
    app.include_router(memoir_editor_routes.router)
    app.include_router(profile_routes.router)
    app.include_router(mission_editor_routes.router)
    app.include_router(quest_editor_routes.router)
    app.include_router(rates_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(settings_routes.router)

    @app.middleware("http")
    async def remember_region(request, call_next):
        """Tell the pages which release this build is, for the EN/JP badge.

        The Android WebView also sets this cookie, but the desktop panel and a
        cookie-cleared WebView then still know it.
        """
        response = await call_next(request)
        if "lunar_region" not in request.cookies:
            response.set_cookie("lunar_region", config.BUILD_REGION, path="/", samesite="strict")
        return response

    # Innermost: remember the last user the operator successfully opened, so the
    # top nav can jump straight to that user's editors. Skips non-2xx responses
    # (e.g. a "user not found" redirect) so only real selections are stored.
    @app.middleware("http")
    async def remember_selected_user(request: Request, call_next):
        response = await call_next(request)
        match = _USER_PATH.match(request.url.path)
        if match and response.status_code < 300:
            # A route that removed the user (profile delete) flags the request so
            # the remembered-user cookie is dropped instead of pointing at a
            # record that no longer exists.
            if getattr(request.state, "forget_selected_user", False):
                response.delete_cookie(session.COOKIE_NAME)
            else:
                response.set_cookie(
                    session.COOKIE_NAME,
                    match.group(1),
                    max_age=session.COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="lax",
                )
        return response

    # Every page is served as the only user of this device: no roles, no login.
    # Templates still read these, so they are filled in on every request.
    @app.middleware("http")
    async def page_state(request: Request, call_next):
        request.state.auth_enabled = False
        # The on-device build sets this so the nav can show the pages only it has.
        request.state.pod_extras = os.environ.get("LUNAR_BASE_EXTRAS") == "1"
        request.state.static_version = _static_version()
        request.state.role = "admin"
        match = _USER_PATH.match(request.url.path)
        request.state.current_user_id = int(match.group(1)) if match else None
        return await call_next(request)

    return app


app = create_app()
