"""In-panel setup actions so the /settings page fully owns initialization.

Every action from the setup scripts is exposed as a background job under
data/setup/jobs/<id>/ (job.json + log.txt) that the page polls:

    venv        create panel/.venv
    deps        pip install -r panel/web/requirements.txt
    masterdata  dump the encrypted bin into panel/data/masterdata
    names       extract display names into panel/data/names
    shim        copy + go build the grant shim
    patchdeps   install/refresh the /patch tools

Status detection is automatic: finished (or skipped, when its inputs are
missing) actions report done=True and the UI disables their button.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from web import config
from web.services import settings_service

ACTIONS = ("venv", "deps", "masterdata", "names", "shim", "patchdeps")
_TERMINAL = ("succeeded", "failed", "cancelled")

_LOCK = threading.RLock()
_LIVE: dict[str, dict[str, Any]] = {}
_CANCEL: dict[str, threading.Event] = {}

_REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "jinja2",
    "bcrypt",
    "itsdangerous",
    "lz4.block",
    "Crypto",
    "msgpack",
    "google.protobuf",
)

# Fixed input the server always loads (see cmd/lunar-tear/main.go).
_CANONICAL_BIN = "20240404193219.bin.e"


class SetupError(RuntimeError):
    pass


def _jobs_root() -> Path:
    root = config.DATA_DIR / "setup" / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_dir(job_id: str) -> Path:
    if not job_id or any(ch not in "0123456789abcdef-" for ch in job_id.lower()):
        raise SetupError("invalid job id")
    return _jobs_root() / job_id


def _venv_python() -> Path:
    if os.name == "nt":
        return config.ROOT / ".venv" / "Scripts" / "python.exe"
    return config.ROOT / ".venv" / "bin" / "python"


def _python() -> str:
    venv = _venv_python()
    return str(venv if venv.is_file() else sys.executable)


def _server_dir() -> Path:
    return config.LUNAR_TEAR_DIR / "server"


def _revisions_dir() -> Path:
    return _server_dir() / "assets" / "revisions"


def _missing_modules() -> list[str]:
    missing = []
    for name in _REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def _shim_outdated() -> bool:
    exe = config.GRANT_EXE_PATH
    if not exe.is_file():
        return False
    sources = config.ROOT / "tools" / "grant" / "src"
    newest = max((p.stat().st_mtime for p in sources.glob("*.go")), default=0.0)
    try:
        return newest > exe.stat().st_mtime
    except OSError:
        return False


def status() -> dict[str, dict[str, Any]]:
    """Per-action completion state used to enable/disable the buttons."""
    st: dict[str, dict[str, Any]] = {}

    venv = _venv_python()
    st["venv"] = {"done": venv.is_file(), "detail": str(venv) if venv.is_file() else "not created"}

    missing = _missing_modules()
    st["deps"] = {
        "done": not missing,
        "detail": "all installed" if not missing else "missing: " + ", ".join(missing),
    }

    masterdata = len(list(config.MASTERDATA_DIR.glob("*.json")))
    st["masterdata"] = {"done": masterdata > 0, "detail": f"{masterdata} tables"}

    names = len(list(config.NAMES_DIR.glob("*.json")))
    revisions = _revisions_dir()
    if names:
        st["names"] = {"done": True, "detail": f"{names} files"}
    elif not revisions.is_dir():
        st["names"] = {
            "done": False,
            "skipped": True,
            "reason": "server/assets/revisions not found",
            "detail": "skipped: revisions tree missing",
        }
    else:
        st["names"] = {"done": False, "detail": "not extracted"}

    outdated = _shim_outdated()
    shim = config.GRANT_EXE_PATH
    st["shim"] = {
        "done": shim.is_file() and not outdated,
        "outdated": outdated,
        "detail": "outdated (sources changed)" if outdated else (shim.name if shim.is_file() else "not built"),
    }

    tools = settings_service.detect_tools()
    ok = sum(1 for tool in tools.values() if tool.get("ok"))
    st["patchdeps"] = {"done": ok == len(tools), "detail": f"{ok}/{len(tools)} tools"}

    return st


# --- job plumbing -----------------------------------------------------------

def _persist(job: dict[str, Any]) -> None:
    with _LOCK:
        data = {k: v for k, v in job.items() if not k.startswith("_")}
    path = _job_dir(job["id"]) / "job.json"
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _load_job(job_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads((_job_dir(job_id) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") in ("queued", "running"):
        # The panel restarted while the job was running; nothing is executing
        # it anymore, so mark it interrupted instead of showing RUNNING forever.
        data.update(status="failed", error="The panel restarted before this step finished.",
                    step="interrupted", finished_at=_now())
        _persist(data)
    return data


def list_jobs() -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for entry in sorted(_jobs_root().iterdir(), reverse=True):
        if entry.is_dir():
            job = _load_job(entry.name)
            if job:
                items[entry.name] = job
    with _LOCK:
        for job_id, job in _LIVE.items():
            items[job_id] = {k: v for k, v in job.items() if not k.startswith("_")}
    return sorted(items.values(), key=lambda j: j.get("created_at", ""), reverse=True)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _LIVE.get(job_id)
        if job:
            return {k: v for k, v in job.items() if not k.startswith("_")}
    return _load_job(job_id)


def job_log(job_id: str, lines: int = 200) -> str:
    path = _job_dir(job_id) / "log.txt"
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


def _log(job: dict[str, Any], text: str) -> None:
    try:
        with (_job_dir(job["id"]) / "log.txt").open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n")
    except OSError:
        pass


def _run_cmd(job: dict[str, Any], cmd: list[str], cwd: Path | None = None) -> None:
    event = _CANCEL.get(job["id"])
    if event is not None and event.is_set():
        raise SetupError("cancelled")
    _log(job, "$ " + " ".join(f'"{p}"' if " " in str(p) else str(p) for p in cmd))
    try:
        proc = subprocess.Popen(
            [str(p) for p in cmd],
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
        raise SetupError(f"cannot run {cmd[0]}: {exc}") from exc
    with _LOCK:
        job["_proc"] = proc
    assert proc.stdout is not None
    for line in proc.stdout:
        if event is not None and event.is_set():
            proc.kill()
            raise SetupError("cancelled")
        _log(job, line.rstrip("\n"))
    rc = proc.wait()
    with _LOCK:
        job["_proc"] = None
    if rc != 0:
        raise SetupError(f"{Path(cmd[0]).name} exited with code {rc}")


def _build_shim(job: dict[str, Any]) -> None:
    if shutil.which("go") is None:
        raise SetupError("Go is not on PATH (1.25+ required)")
    server = _server_dir()
    if not (server / "go.mod").is_file():
        raise SetupError(f"game server not found at {server}")
    sources = config.ROOT / "tools" / "grant" / "src"
    if not (sources / "main.go").is_file():
        raise SetupError("panel/tools/grant/src/main.go missing")
    dest = server / "cmd" / "lunar-base-grant"
    dest.mkdir(parents=True, exist_ok=True)
    _log(job, f"[shim] copying {sources}/*.go -> {dest}")
    for src in sorted(sources.glob("*.go")):
        shutil.copy2(src, dest / src.name)
    _run_cmd(job, ["go", "build", "-o", str(config.GRANT_EXE_PATH), "./cmd/lunar-base-grant/"], cwd=server)


def _run_action(job: dict[str, Any]) -> None:
    action = job["action"]
    if action == "venv":
        _run_cmd(job, [sys.executable, "-m", "venv", str(_venv_python().parent.parent)])
    elif action == "deps":
        _run_cmd(job, [_python(), "-m", "pip", "install", "-r", str(config.ROOT / "web" / "requirements.txt")])
    elif action == "masterdata":
        script = config.ROOT / "scripts" / "dump_masterdata.py"
        binary = _server_dir() / "assets" / "release" / _CANONICAL_BIN
        if not binary.is_file():
            raise SetupError(f"master data binary not found: {binary}")
        _run_cmd(job, [_python(), "-X", "utf8", str(script),
                       "--input", str(binary), "--output", str(config.MASTERDATA_DIR)])
    elif action == "names":
        if not _revisions_dir().is_dir():
            raise SetupError("server/assets/revisions not found")
        _run_cmd(job, [_python(), str(config.ROOT / "tools" / "extract_names.py"),
                       "--revisions-dir", str(_revisions_dir())])
    elif action == "shim":
        _build_shim(job)
    elif action == "patchdeps":
        setup = config.ROOT / ("setup.bat" if os.name == "nt" else "setup.sh")
        if not setup.is_file():
            raise SetupError(f"{setup.name} not found")
        _run_cmd(job, [str(setup), "patch-deps"] if os.name == "nt" else ["bash", str(setup), "patch-deps"])
    else:
        raise SetupError(f"unknown action {action!r}")


def _run_job(job_id: str) -> None:
    with _LOCK:
        job = _LIVE.get(job_id)
    if not job:
        return
    job.update(status="running", started_at=_now(), progress=5, step="running")
    _persist(job)
    try:
        _run_action(job)
        job.update(status="succeeded", progress=100, step="done", finished_at=_now())
    except SetupError as exc:
        message = str(exc)
        if message == "cancelled":
            job.update(status="cancelled", step="cancelled", finished_at=_now())
        else:
            job.update(status="failed", error=message, step="failed", finished_at=_now())
            _log(job, f"[error] {message}")
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI
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
        # Tool status may have changed (pip/apktool/JDK install): drop the cache
        # so the next /settings poll reports the fresh result immediately.
        settings_service.invalidate_tools_cache()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _prune(keep: int = 20) -> None:
    """Keep the newest `keep` jobs; older finished ones are removed."""
    finished: list[Path] = []
    for entry in sorted(_jobs_root().iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        data = _load_job(entry.name)
        if data and data.get("status") in _TERMINAL:
            finished.append(entry)
    for old in finished[keep:]:
        with _LOCK:
            _LIVE.pop(old.name, None)
        shutil.rmtree(old, ignore_errors=True)


def run_action(action: str) -> dict[str, Any]:
    action = str(action or "")
    if action not in ACTIONS:
        raise SetupError(f"unknown action: {action}")
    state = status().get(action) or {}
    if state.get("done"):
        raise SetupError("already done")
    if state.get("skipped"):
        raise SetupError("skipped: " + str(state.get("reason") or "inputs missing"))
    with _LOCK:
        for job in _LIVE.values():
            if job["action"] == action and job["status"] in ("queued", "running"):
                raise SetupError("already running")

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()
    directory = _job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "action": action,
        "status": "queued",
        "created_at": _now(),
        "started_at": "",
        "finished_at": "",
        "progress": 0,
        "step": "queued",
        "error": "",
    }
    with _LOCK:
        _LIVE[job_id] = job
        _CANCEL[job_id] = threading.Event()
    _persist(job)
    _prune()
    threading.Thread(target=_run_job, args=(job_id,), name=f"setup-{job_id}", daemon=True).start()
    return _LIVE[job_id].copy()


def cancel_job(job_id: str) -> dict[str, Any]:
    with _LOCK:
        event = _CANCEL.get(job_id)
    if event is None:
        raise SetupError("job is not running")
    event.set()
    return get_job(job_id) or {}
