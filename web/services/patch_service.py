"""Background patch jobs for the /patch page.

Every job gets its own sandbox under panel/data/patch/jobs/<id>/:

    input/   uploaded files (kept as evidence)
    work/    scratch space (decoded APK, temporary copies, ...)
    output/  finished artifacts offered as downloads
    job.json status, params, progress, results
    log.txt  combined stdout/stderr of every step

Jobs run in a daemon thread; the page polls JSON status. The bundled
lunar-scripts are invoked as subprocesses with fixed argv lists (never a
shell). Long artifacts stay on disk, so a browser reload or even a panel
restart never loses a finished result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from web import config
from web.services import event_service, settings_service

KINDS = ("apk", "ipa", "masterdata", "listbin")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
_TERMINAL = TERMINAL_STATUSES

_LOCK = threading.RLock()
_LIVE: dict[str, dict[str, Any]] = {}
_CANCEL: dict[str, threading.Event] = {}


class PatchError(RuntimeError):
    pass


class JobCancelled(Exception):
    pass


# --- paths / persistence ----------------------------------------------------

def _jobs_root() -> Path:
    config.PATCH_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return config.PATCH_JOBS_DIR


def job_dir(job_id: str) -> Path:
    if not job_id or any(ch not in "0123456789abcdef-" for ch in job_id.lower()):
        raise PatchError("invalid job id")
    return _jobs_root() / job_id


def _job_file(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def _persist(job: dict[str, Any]) -> None:
    with _LOCK:
        data = {k: v for k, v in job.items() if not k.startswith("_")}
    tmp = _job_file(job["id"]).with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, _job_file(job["id"]))
    except OSError:
        pass


def _load_job(job_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_job_file(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") in ("queued", "running"):
        data.update(
            status="interrupted",
            error="The panel restarted before this job finished.",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
        _persist(data)
    return data


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(job: dict[str, Any], text: str) -> None:
    try:
        with (_job_dir_for(job) / "log.txt").open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n")
    except OSError:
        pass


def _job_dir_for(job: dict[str, Any]) -> Path:
    return job_dir(job["id"])


def _progress(job: dict[str, Any], value: int, step: str = "") -> None:
    job["progress"] = max(0, min(100, int(value)))
    if step:
        job["step"] = step
    _persist(job)


def _check_cancel(job: dict[str, Any]) -> None:
    event = _CANCEL.get(job["id"])
    if event is not None and event.is_set():
        raise JobCancelled()


def _add_output(job: dict[str, Any], path: Path, label: str = "") -> None:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    job["outputs"].append({
        "name": path.name,
        "label": label or path.name,
        "size": size,
        "path": str(path),
    })
    _persist(job)


def _inputs(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("inputs") or [])


def _input_path(job: dict[str, Any], index: int = 0) -> Path:
    entries = _inputs(job)
    if index >= len(entries):
        raise PatchError("No input file uploaded for this job.")
    path = Path(entries[index]["path"])
    if not path.is_file():
        raise PatchError(f"Input file is missing: {path.name}")
    return path


# --- public queries ---------------------------------------------------------

def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        live_ids = set(_LIVE)
    items: dict[str, dict[str, Any]] = {}
    for entry in sorted(_jobs_root().iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        job = _load_job(entry.name)
        if job:
            items[entry.name] = job
    for job_id, job in list(_LIVE.items()):
        items[job_id] = {k: v for k, v in job.items() if not k.startswith("_")}
    return sorted(items.values(), key=lambda j: j.get("created_at", ""), reverse=True)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _LIVE.get(job_id)
        if job:
            return {k: v for k, v in job.items() if not k.startswith("_")}
    return _load_job(job_id)


def job_log(job_id: str, lines: int = 200) -> str:
    path = job_dir(job_id) / "log.txt"
    if not path.is_file():
        return ""
    lines = max(1, min(int(lines), 2000))
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(8192, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
        return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def output_path(job_id: str, name: str) -> tuple[Path, str]:
    job = get_job(job_id)
    if not job:
        raise PatchError("job not found")
    for out in job.get("outputs") or []:
        if out.get("name") == name:
            path = Path(out["path"])
            if path.is_file():
                return path, str(out.get("label") or name)
    raise PatchError("output not found")


# --- job creation -----------------------------------------------------------

def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "upload.bin")
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in base).strip()
    return cleaned[:180] or "upload.bin"


def max_upload_bytes() -> int:
    try:
        mb = int(settings_service.load().get("patch_jobs", {}).get("max_upload_mb", 4096))
    except (TypeError, ValueError):
        mb = 4096
    return max(1, mb) * 1024 * 1024


def create_job(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    if kind not in KINDS:
        raise PatchError(f"unknown patch kind: {kind}")
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()
    directory = job_dir(job_id)
    for sub in ("input", "work", "output"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "kind": kind,
        "title": str(params.get("title") or kind.upper()),
        "status": "queued",
        "created_at": _now(),
        "started_at": "",
        "finished_at": "",
        "progress": 0,
        "step": "queued",
        "params": params,
        "inputs": [],
        "outputs": [],
        "error": "",
        "applied": False,
    }
    with _LOCK:
        _LIVE[job_id] = job
    _persist(job)
    _prune()
    return get_job(job_id) or job


def store_upload(job_id: str, filename: str, fileobj: BinaryIO) -> dict[str, Any]:
    with _LOCK:
        job = _LIVE.get(job_id)
    if not job:
        raise PatchError("job not found")
    limit = max_upload_bytes()
    safe = _safe_filename(filename)
    dest = job_dir(job_id) / "input" / safe
    total = 0
    with dest.open("wb") as fh:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                fh.close()
                dest.unlink(missing_ok=True)
                raise PatchError(f"upload exceeds the {limit // (1024 * 1024)} MB limit")
            fh.write(chunk)
    entry = {"name": safe, "size": total, "path": str(dest)}
    job["inputs"].append(entry)
    _persist(job)
    _log(job, f"[upload] {safe} ({total} bytes)")
    return entry


def start_job(job_id: str) -> dict[str, Any]:
    with _LOCK:
        job = _LIVE.get(job_id)
        if not job:
            job = _load_job(job_id)
        if not job:
            raise PatchError("job not found")
        if job.get("status") not in ("queued",):
            raise PatchError("job already started")
        _LIVE[job_id] = job
        _CANCEL[job_id] = threading.Event()
    thread = threading.Thread(target=_run_job, args=(job_id,), name=f"patch-{job_id}", daemon=True)
    thread.start()
    return get_job(job_id) or job


def cancel_job(job_id: str) -> dict[str, Any]:
    with _LOCK:
        event = _CANCEL.get(job_id)
    if event is None:
        raise PatchError("job is not running")
    event.set()
    proc = None
    with _LOCK:
        job = _LIVE.get(job_id)
        if job:
            proc = job.get("_proc")
    if proc is not None:
        try:
            proc.kill()
        except OSError:
            pass
    return get_job(job_id) or {}


def delete_job(job_id: str) -> None:
    with _LOCK:
        job = _LIVE.get(job_id)
        if job and job.get("status") not in _TERMINAL:
            raise PatchError("cancel the running job before deleting it")
        _LIVE.pop(job_id, None)
        _CANCEL.pop(job_id, None)
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def _prune() -> None:
    try:
        retention = int(settings_service.load().get("patch_jobs", {}).get("retention", 20))
    except (TypeError, ValueError):
        retention = 20
    if retention <= 0:
        return
    finished = []
    for entry in sorted(_jobs_root().iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        try:
            data = json.loads((entry / "job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") in _TERMINAL:
            finished.append(entry)
    for old in finished[retention:]:
        with _LOCK:
            _LIVE.pop(old.name, None)
        shutil.rmtree(old, ignore_errors=True)


# --- sources for the UI -----------------------------------------------------

def masterdata_sources() -> list[dict[str, Any]]:
    release = config.LUNAR_TEAR_DIR / "server" / "assets" / "release"
    if not release.is_dir():
        return []
    items = []
    for path in sorted(release.glob("*.bin.e")):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return items


def listbin_sources() -> list[dict[str, Any]]:
    revisions = config.LUNAR_TEAR_DIR / "server" / "assets" / "revisions"
    if not revisions.is_dir():
        return []
    items = []
    for revision in sorted(revisions.iterdir(), key=lambda p: p.name):
        if not revision.is_dir():
            continue
        for platform in ("android", "ios"):
            path = revision / platform / "list.bin"
            if path.is_file():
                items.append({
                    "name": f"{revision.name}/{platform}/list.bin",
                    "path": str(path),
                    "size": path.stat().st_size,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
    return items


def apply_masterdata(job_id: str) -> dict[str, Any]:
    """Make a finished master-data job's output the active server bin."""
    job = get_job(job_id)
    if not job:
        raise PatchError("job not found")
    if job.get("kind") != "masterdata":
        raise PatchError("only master-data jobs can be applied")
    if job.get("status") != "succeeded":
        raise PatchError("the job has not finished successfully")
    outputs = job.get("outputs") or []
    if not outputs:
        raise PatchError("this job has no output to apply (was it a dry run?)")
    src = Path(outputs[0]["path"])
    result = event_service.activate_bin(str(src), work_dir=str(job_dir(job_id) / "output"))
    with _LOCK:
        live = _LIVE.get(job_id)
    target = live or job
    target["applied"] = True
    _persist(target)
    return result


# --- runners -----------------------------------------------------------------

def _run_job(job_id: str) -> None:
    with _LOCK:
        job = _LIVE.get(job_id)
    if not job:
        return
    job.update(status="running", started_at=_now(), step="starting")
    _persist(job)
    try:
        handler = {
            "apk": _run_apk,
            "ipa": _run_ipa,
            "masterdata": _run_masterdata,
            "listbin": _run_listbin,
        }[job["kind"]]
        handler(job)
        _check_cancel(job)
        job.update(status="succeeded", progress=100, step="done", finished_at=_now())
    except JobCancelled:
        job.update(status="cancelled", step="cancelled", finished_at=_now())
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI + log
        job.update(status="failed", error=str(exc), step="failed", finished_at=_now())
        _log(job, f"[error] {exc}")
    finally:
        with _LOCK:
            proc = job.get("_proc")
            job.pop("_proc", None)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        _persist(job)
        with _LOCK:
            _CANCEL.pop(job_id, None)


def _run_cmd(
    job: dict[str, Any],
    cmd: list[str],
    cwd: Path | None = None,
    progress: int | None = None,
    step: str = "",
) -> None:
    _check_cancel(job)
    if progress is not None:
        _progress(job, progress, step)
    display = " ".join(f'"{part}"' if " " in part else part for part in cmd)
    _log(job, f"$ {display}")
    try:
        proc = subprocess.Popen(
            [str(part) for part in cmd],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise PatchError(f"cannot run {cmd[0]}: {exc}") from exc
    with _LOCK:
        job["_proc"] = proc
    assert proc.stdout is not None
    for line in proc.stdout:
        if _CANCEL.get(job["id"]) and _CANCEL[job["id"]].is_set():
            proc.kill()
            raise JobCancelled()
        _log(job, line.rstrip("\n"))
    rc = proc.wait()
    with _LOCK:
        job["_proc"] = None
    if rc != 0:
        raise PatchError(f"{Path(cmd[0]).name} exited with code {rc}")


def _script(*parts: str) -> Path:
    return config.ROOT.joinpath("scripts", *parts)


def _require_address(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or ":" not in text:
        raise PatchError(f"{label} must be host:port")
    return text


def _run_ipa(job: dict[str, Any]) -> None:
    src = _input_path(job)
    params = job["params"]
    out = job_dir(job["id"]) / "output" / "patched.ipa"
    cmd = [
        sys.executable,
        str(_script("ios", "patch_ipa.py")),
        str(src),
        "--grpc-addr", _require_address(params.get("grpc"), "gRPC address"),
        "--http-addr", _require_address(params.get("cdn"), "HTTP/CDN address"),
        "--output", str(out),
    ]
    auth = str(params.get("auth") or "").strip()
    if auth:
        cmd += ["--auth-host", auth]
    _run_cmd(job, cmd, progress=20, step="patching ipa")
    if not out.is_file():
        raise PatchError("patch_ipa.py produced no output")
    _add_output(job, out, label="patched.ipa (unsigned)")


def _run_masterdata(job: dict[str, Any]) -> None:
    params = job["params"]
    dry = bool(params.get("dry_run"))
    source = str(params.get("source") or "server")
    if source == "upload":
        src = _input_path(job)
    else:
        src = Path(str(params.get("source_path") or "")).resolve()
        allowed = (config.LUNAR_TEAR_DIR / "server" / "assets" / "release").resolve()
        if not src.is_relative_to(allowed):
            raise PatchError("master-data source must live in the server release directory")
        if not src.is_file():
            raise PatchError(f"source bin not found: {src.name}")

    out_dir = job_dir(job["id"]) / "output"
    out = out_dir / (src.name if src.name.endswith(".bin.e") else src.name + ".bin.e")
    cmd = [sys.executable, str(_script("patch_masterdata.py")), "--input", str(src)]
    if dry:
        cmd.append("--dry-run")
    else:
        cmd += ["--output", str(out)]
    _run_cmd(job, cmd, progress=30, step="patching master data")
    if dry:
        _log(job, "[dry-run] no file was written")
        return
    if not out.is_file():
        raise PatchError("patch_masterdata.py produced no output")
    _add_output(job, out, label=out.name)


def _run_listbin(job: dict[str, Any]) -> None:
    params = job["params"]
    dry = bool(params.get("dry_run"))
    source = str(params.get("source") or "server")
    use_all = bool(params.get("all"))

    if source == "upload":
        upload = _input_path(job)
        work = job_dir(job["id"]) / "work"
        work.mkdir(parents=True, exist_ok=True)
        target = work / upload.name
        shutil.copy2(upload, target)
    else:
        target = Path(str(params.get("path") or "")).resolve()
        allowed = (config.LUNAR_TEAR_DIR / "server" / "assets").resolve()
        if not target.is_relative_to(allowed):
            raise PatchError("list.bin target must live under the server assets directory")
        if not target.exists():
            raise PatchError(f"target not found: {target}")

    cmd = [sys.executable, str(_script("assetbundles", "patch_listbin.py")), str(target), "-v"]
    if use_all:
        cmd.append("--all")
    if dry:
        cmd.append("--dry-run")
    progress = 40 if target.is_dir() else 60
    _run_cmd(job, cmd, progress=progress, step="patching list.bin")

    if source == "upload" and not dry and target.is_file():
        out = job_dir(job["id"]) / "output" / target.name
        shutil.copy2(target, out)
        _add_output(job, out, label=target.name)


def _tool_path(tools: dict[str, dict[str, Any]], name: str) -> str:
    entry = tools.get(name) or {}
    if not entry.get("ok"):
        raise PatchError(f"{name} is not available — run panel/patch-deps.sh (or set its path on /settings)")
    return str(entry["path"])


def _ensure_keystore(job: dict[str, Any], tools: dict[str, dict[str, Any]]) -> Path:
    keystore = config.PATCH_KEYSTORE_PATH
    if keystore.is_file():
        return keystore
    keystore.parent.mkdir(parents=True, exist_ok=True)
    keytool = _tool_path(tools, "keytool")
    _run_cmd(job, [
        keytool, "-genkeypair",
        "-keystore", str(keystore),
        "-alias", "androiddebugkey",
        "-storepass", "android",
        "-keypass", "android",
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US",
    ], progress=15, step="creating debug keystore")
    if not keystore.is_file():
        raise PatchError("failed to create the debug keystore")
    return keystore


def _run_apk(job: dict[str, Any]) -> None:
    params = job["params"]
    src = _input_path(job)
    tools = settings_service.detect_tools()
    java = _tool_path(tools, "java")
    zipalign = _tool_path(tools, "zipalign")
    apksigner = _tool_path(tools, "apksigner")
    keystore = _ensure_keystore(job, tools)

    apk_tool = tools.get("apktool") or {}
    if not apk_tool.get("ok"):
        raise PatchError(
            "apktool is not available — run panel/patch-deps.sh (or set its path on /settings)"
        )
    apktool_cmd = (
        [java, "-jar", str(apk_tool["path"])]
        if apk_tool.get("kind") == "jar"
        else [str(apk_tool["path"])]
    )

    work = job_dir(job["id"]) / "work"
    decoded = work / "decoded"
    if decoded.exists():
        shutil.rmtree(decoded, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    suffix = src.suffix.lower()
    if suffix == ".apk":
        _run_cmd(job, apktool_cmd + ["d", "-f", "-o", str(decoded), str(src)],
                 progress=10, step="decoding apk")
    elif suffix == ".zip":
        decoded.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(src) as zf:
                zf.extractall(decoded)
        except zipfile.BadZipFile as exc:
            raise PatchError(f"uploaded zip is not a valid archive: {exc}") from exc
        _progress(job, 30, "unpacked decompiled apk")
    else:
        raise PatchError("APK jobs accept a .apk or a .zip of an apktool-decompiled directory")

    if not (decoded / "apktool.yml").is_file() and not (decoded / "smali").is_dir():
        raise PatchError("decoded directory does not look like an apktool project")

    patch_cmd = [
        sys.executable, str(_script("android", "patch_apk.py")), str(decoded),
        "--grpc-addr", _require_address(params.get("grpc"), "gRPC address"),
        "--http-addr", _require_address(params.get("cdn"), "HTTP/CDN address"),
    ]
    auth = str(params.get("auth") or "").strip()
    if auth:
        patch_cmd += ["--auth-host", auth]
    _run_cmd(job, patch_cmd, progress=45, step="patching apk")

    unsigned = work / "unsigned.apk"
    aligned = work / "aligned.apk"
    signed = job_dir(job["id"]) / "output" / "patched.apk"
    _run_cmd(job, apktool_cmd + ["b", str(decoded), "-o", str(unsigned)],
             progress=65, step="rebuilding apk")
    _run_cmd(job, [zipalign, "-p", "-f", "4", str(unsigned), str(aligned)],
             progress=80, step="aligning apk")
    _run_cmd(job, [
        apksigner, "sign",
        "--ks", str(keystore),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--out", str(signed),
        str(aligned),
    ], progress=90, step="signing apk")
    if not signed.is_file():
        raise PatchError("apksigner produced no output")
    _add_output(job, signed, label="patched.apk (signed)")
