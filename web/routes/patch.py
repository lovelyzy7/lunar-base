"""/patch page: run the bundled lunar-scripts patch tools locally.

Four job kinds — APK (full apktool + zipalign + apksigner pipeline), IPA,
master-data bin, and list.bin — each running as a background job with a
persistent sandbox, live log/progress polling and downloadable outputs.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import patch_service, settings_service
router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _page_data() -> dict[str, Any]:
    return {
        "active": "patch",
        "tools": settings_service.detect_tools(),
        "patch_settings": settings_service.load(),
        "defaults": settings_service.patch_defaults(),
        "masterdata_sources": patch_service.masterdata_sources(),
        "listbin_sources": patch_service.listbin_sources(),
        "jobs": patch_service.list_jobs(),
        "max_upload_mb": patch_service.max_upload_bytes() // (1024 * 1024),
    }


@router.get("/patch", response_class=HTMLResponse)
def patch_view(request: Request):
    return templates.TemplateResponse(request, "patch.html", _page_data())


@router.get("/patch/state")
def patch_state() -> JSONResponse:
    """Everything the page polls: jobs, tool status, selectable sources."""
    return JSONResponse({"ok": True, **_page_data()})


@router.post("/patch/jobs")
async def patch_create(
    kind: str = Form(...),
    params: str = Form("{}"),
    files: list[UploadFile] = File(default=[]),
) -> JSONResponse:
    try:
        parsed = json.loads(params or "{}")
    except json.JSONDecodeError:
        return _err("params must be a JSON object")
    if not isinstance(parsed, dict):
        return _err("params must be a JSON object")
    if kind not in patch_service.KINDS:
        return _err(f"unknown patch kind: {kind}")

    needs_file = kind in ("apk", "ipa") or str(parsed.get("source") or "server") == "upload"
    if needs_file and not files:
        return _err("an input file is required for this job")
    if kind == "masterdata" and str(parsed.get("source") or "server") == "server" and not parsed.get("source_path"):
        return _err("choose a master-data bin (or switch the source to upload)")
    if kind == "listbin" and str(parsed.get("source") or "server") == "server" and not parsed.get("path"):
        return _err("choose a list.bin target (or switch the source to upload)")

    try:
        job = patch_service.create_job(kind, parsed)
        for upload in files:
            if upload.filename:
                patch_service.store_upload(job["id"], upload.filename, upload.file)
        job = patch_service.start_job(job["id"])
    except patch_service.PatchError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"cannot store the upload: {exc}", 500)
    return JSONResponse({"ok": True, "job": job})


@router.post("/patch/detect-tools")
def patch_detect_tools() -> JSONResponse:
    return JSONResponse({"ok": True, "tools": settings_service.detect_tools(force=True)})


@router.post("/patch/settings")
def patch_save_settings(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Persist the /patch-related settings (tools, defaults, job storage)."""
    tools = payload.get("patch_tools")
    defaults = payload.get("patch_defaults")
    jobs = payload.get("patch_jobs")
    if tools is not None and not isinstance(tools, dict):
        return _err("patch_tools must be an object")
    if defaults is not None and not isinstance(defaults, dict):
        return _err("patch_defaults must be an object")
    if jobs is not None and not isinstance(jobs, dict):
        return _err("patch_jobs must be an object")
    try:
        values: dict[str, Any] = {}
        if isinstance(tools, dict):
            values["patch_tools"] = {
                name: str(tools.get(name) or "").strip() for name in settings_service.TOOL_NAMES
            }
        if isinstance(defaults, dict):
            values["patch_defaults"] = {
                key: str(defaults.get(key) or "").strip() for key in ("grpc", "cdn", "auth")
            }
        if isinstance(jobs, dict):
            retention = int(jobs.get("retention", 20))
            if not (0 <= retention <= 1000):
                return _err("retention must be between 0 and 1000")
            max_mb = int(jobs.get("max_upload_mb", 4096))
            if not (1 <= max_mb <= 65536):
                return _err("max_upload_mb must be between 1 and 65536")
            values["patch_jobs"] = {"retention": retention, "max_upload_mb": max_mb}
        if not values:
            return _err("nothing to save")
        saved = settings_service.save(values)
    except (TypeError, ValueError):
        return _err("invalid value")
    return JSONResponse({"ok": True, "settings": saved, "tools": settings_service.detect_tools()})


@router.get("/patch/jobs/{job_id}")
def patch_job(job_id: str, tail: int = 120) -> JSONResponse:
    job = patch_service.get_job(job_id)
    if not job:
        return _err("job not found", 404)
    return JSONResponse({"ok": True, "job": job, "log": patch_service.job_log(job_id, tail)})


@router.post("/patch/jobs/{job_id}/cancel")
def patch_cancel(job_id: str) -> JSONResponse:
    try:
        patch_service.cancel_job(job_id)
    except patch_service.PatchError as exc:
        return _err(str(exc))
    return JSONResponse({"ok": True, "job": patch_service.get_job(job_id)})


@router.post("/patch/jobs/{job_id}/delete")
def patch_delete(job_id: str) -> JSONResponse:
    try:
        patch_service.delete_job(job_id)
    except patch_service.PatchError as exc:
        return _err(str(exc))
    return JSONResponse({"ok": True})


@router.post("/patch/jobs/clear-finished")
def patch_clear_finished() -> JSONResponse:
    removed = 0
    for job in patch_service.list_jobs():
        if job.get("status") in patch_service.TERMINAL_STATUSES:
            try:
                patch_service.delete_job(job["id"])
                removed += 1
            except patch_service.PatchError:
                pass
    return JSONResponse({"ok": True, "removed": removed})


@router.get("/patch/jobs/{job_id}/download/{name}", response_model=None)
def patch_download(job_id: str, name: str):
    try:
        path, label = patch_service.output_path(job_id, name)
    except patch_service.PatchError as exc:
        return _err(str(exc), 404)
    return FileResponse(path, filename=label, media_type="application/octet-stream")


@router.post("/patch/jobs/{job_id}/apply")
def patch_apply(job_id: str) -> JSONResponse:
    """Make a finished master-data job the active bin the server loads."""
    try:
        result = patch_service.apply_masterdata(job_id)
    except patch_service.PatchError as exc:
        return _err(str(exc))
    except (ValueError, OSError) as exc:
        return _err(str(exc), 500)
    return JSONResponse({"ok": True, **result})


@router.post("/patch/sources")
def patch_sources(_payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    """Refresh the selectable source lists (called after bin activation etc.)."""
    return JSONResponse({
        "ok": True,
        "masterdata_sources": patch_service.masterdata_sources(),
        "listbin_sources": patch_service.listbin_sources(),
    })
