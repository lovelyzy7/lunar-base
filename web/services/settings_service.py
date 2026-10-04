"""Panel settings: data/settings.json + tool detection + wizard config helpers.

Everything the /settings page reads or writes lives here. The file is small and
read by config.load_settings() on every auth/host resolution, so writes are
atomic (tmp + replace) to avoid ever exposing a half-written JSON file.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any

from web import config

_LOCK = threading.RLock()

# Cached tool detection: probing java/apktool spawns subprocesses, and the
# /patch page polls /patch/state every couple of seconds, so keep the result
# for a short while. force=True (the /settings "detect" button) bypasses it.
_TOOLS_CACHE: dict[str, Any] = {"at": 0.0, "key": None, "value": None}
_TOOLS_TTL = 60.0


def invalidate_tools_cache() -> None:
    """Drop the cached tool detection (after installing/removing tools)."""
    with _LOCK:
        _TOOLS_CACHE.update(at=0.0, key=None, value=None)

# Tool names the /patch page needs, in display order.
TOOL_NAMES = ("java", "apktool", "zipalign", "apksigner", "keytool")

_DEFAULT_SETTINGS: dict[str, Any] = {
    "host": "",
    "port": 0,
    "auth": None,
    "patch_tools": {name: "" for name in TOOL_NAMES},
    "patch_defaults": {"grpc": "", "cdn": "", "auth": ""},
    "patch_jobs": {"retention": 20, "max_upload_mb": 4096},
    "server": {
        "control_mode": "",
        "probe_host": "",
        "log_source": "",
        "container_label": "com.lunar.role",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    """Return the merged settings (defaults + data/settings.json)."""
    with _LOCK:
        return _deep_merge(_DEFAULT_SETTINGS, config.load_settings())


def save(values: dict[str, Any]) -> dict:
    """Atomically persist settings (deep-merged onto the current file)."""
    with _LOCK:
        current = config.load_settings()
        merged = _deep_merge(current, values)
        config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".settings-", suffix=".json", dir=str(config.SETTINGS_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp_name, config.SETTINGS_PATH)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return _deep_merge(_DEFAULT_SETTINGS, merged)


# --- validation -------------------------------------------------------------

def validate_host(host: str) -> str:
    host = (host or "").strip()
    if not host:
        return ""
    if len(host) > 253:
        raise ValueError("host is too long")
    allowed = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-_")
    if any(ch not in allowed for ch in host):
        raise ValueError("host may only contain letters, digits, dots, dashes and underscores")
    return host


def validate_port(port: Any) -> int:
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("port must be a number") from None
    if not (1 <= port <= 65535):
        raise ValueError("port must be between 1 and 65535")
    return port


def port_available(host: str, port: int) -> bool:
    """True when a TCP socket can bind host:port right now."""
    bind_host = host or "0.0.0.0"
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


# --- tool detection ---------------------------------------------------------

def _which(name: str) -> str:
    return shutil.which(name) or ""


def _run_capture(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def detect_tools(force: bool = False) -> dict[str, dict[str, Any]]:
    """Resolve every /patch tool. Settings paths win over PATH/bundled copies."""
    saved = load().get("patch_tools", {})
    cache_key = json.dumps(sorted((saved or {}).items())) if isinstance(saved, dict) else str(saved)
    now = time.monotonic()
    if (
        not force
        and _TOOLS_CACHE["value"] is not None
        and _TOOLS_CACHE["key"] == cache_key
        and (now - _TOOLS_CACHE["at"]) < _TOOLS_TTL
    ):
        return _TOOLS_CACHE["value"]

    result: dict[str, dict[str, Any]] = {}

    for name in TOOL_NAMES:
        configured = str(saved.get(name) or "").strip()
        path = configured or _which(name)
        source = "settings" if configured else ("path" if path else "missing")
        entry: dict[str, Any] = {"name": name, "path": path, "source": source, "ok": bool(path)}
        result[name] = entry

    # apktool: default to the jar the setup script downloads.
    apktool = result["apktool"]
    if not apktool["path"]:
        bundled = config.ROOT / "tools" / "apktool" / "apktool.jar"
        if bundled.is_file():
            apktool.update(path=str(bundled), source="bundled", ok=True)
    if apktool["ok"] and str(apktool["path"]).lower().endswith(".jar"):
        apktool["kind"] = "jar"
        java = result["java"]["path"]
        if java:
            rc, out = _run_capture([java, "-jar", str(apktool["path"]), "--version"])
            apktool["version"] = out.splitlines()[0] if out else ""
            apktool["ok"] = rc == 0
        else:
            apktool["ok"] = False
            apktool["version"] = "java missing"
    elif apktool["ok"]:
        apktool["kind"] = "exe"
        rc, out = _run_capture([str(apktool["path"]), "--version"])
        apktool["version"] = out.splitlines()[0] if out else ""
        apktool["ok"] = rc == 0

    if result["java"]["ok"]:
        rc, out = _run_capture([result["java"]["path"], "-version"])
        result["java"]["version"] = out.splitlines()[0] if out else ""

    with _LOCK:
        _TOOLS_CACHE.update(at=time.monotonic(), key=cache_key, value=result)
    return result


# --- patch defaults ---------------------------------------------------------

def server_control_config() -> dict[str, str]:
    """Resolve how the panel reaches/manages the game server.

    Precedence: data/settings.json -> environment -> defaults. Docker mode is
    enabled by the compose file via LUNAR_CONTROL_MODE=docker; local mode keeps
    the original process-based control.
    """
    saved = load().get("server", {})
    if not isinstance(saved, dict):
        saved = {}

    mode = str(saved.get("control_mode") or os.environ.get("LUNAR_CONTROL_MODE") or "local").strip().lower()
    if mode not in ("local", "docker"):
        mode = "local"

    probe = str(saved.get("probe_host") or os.environ.get("LUNAR_SERVER_HOST") or "127.0.0.1").strip()
    if not probe:
        probe = "127.0.0.1"

    log_source = str(saved.get("log_source") or os.environ.get("LUNAR_LOG_SOURCE") or "local").strip().lower()
    if log_source not in ("local", "docker"):
        log_source = "local"

    label = str(saved.get("container_label") or os.environ.get("LUNAR_CONTAINER_LABEL") or "com.lunar.role").strip()
    if not label:
        label = "com.lunar.role"

    return {"mode": mode, "probe_host": probe, "log_source": log_source, "container_label": label}


def wizard_config() -> dict[str, Any]:
    """Read server/.wizard.json (tolerant)."""
    try:
        raw = json.loads(config.WIZARD_CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def patch_defaults() -> dict[str, str]:
    """Default host:port values for the /patch address fields."""
    saved = load().get("patch_defaults", {})
    if all(str(saved.get(k) or "").strip() for k in ("grpc", "cdn")):
        return {
            "grpc": str(saved["grpc"]).strip(),
            "cdn": str(saved["cdn"]).strip(),
            "auth": str(saved.get("auth") or "").strip(),
        }
    wiz = wizard_config()
    ip = str(wiz.get("ip") or "").strip() or (config.detect_lan_ip() or "127.0.0.1")
    grpc = wiz.get("grpc_port") or config.LUNAR_TEAR_DEFAULT_GRPC_PORT
    cdn = wiz.get("cdn_port") or config.LUNAR_TEAR_DEFAULT_CDN_PORT
    auth = wiz.get("auth_port") or config.LUNAR_TEAR_DEFAULT_AUTH_PORT
    return {"grpc": f"{ip}:{grpc}", "cdn": f"{ip}:{cdn}", "auth": f"{ip}:{auth}"}


def lan_ip_choices() -> list[str]:
    """Candidate bind addresses for the host dropdown."""
    choices = ["0.0.0.0", "127.0.0.1"]
    detected = config.detect_lan_ip()
    if detected and detected not in choices:
        choices.append(detected)
    return choices


# --- wizard config (game server) --------------------------------------------

WIZARD_DEVICES = ("phone", "emulator")
WIZARD_DETAILS = ("wifi", "vpn", "manual", "android-studio", "bluestacks", "genymotion")

_WIZARD_SUMMARY = {
    ("phone", "wifi"): "phone (Wi-Fi)",
    ("phone", "vpn"): "phone (VPN)",
    ("phone", "manual"): "phone (manual IP)",
    ("emulator", "android-studio"): "Android Studio (emulator)",
    ("emulator", "bluestacks"): "BlueStacks (emulator)",
    ("emulator", "genymotion"): "Genymotion (emulator)",
}


def validate_wizard_ip(ip: str) -> str:
    ip = (ip or "").strip()
    if not ip:
        raise ValueError("IP address is required")
    try:
        socket.inet_aton(ip)
    except OSError:
        raise ValueError("IP must be a valid IPv4 address") from None
    return ip


def save_wizard_config(values: dict[str, Any]) -> dict[str, Any]:
    """Write server/.wizard.json so `server-start.sh --prefer-saved` works."""
    device = str(values.get("device") or "phone")
    detail = str(values.get("detail") or "wifi")
    if device not in WIZARD_DEVICES:
        raise ValueError("device must be phone or emulator")
    if detail not in WIZARD_DETAILS:
        raise ValueError("unknown connection detail")
    ip = validate_wizard_ip(str(values.get("ip") or ""))
    ports = {
        "grpc": validate_port(values.get("grpc", config.LUNAR_TEAR_DEFAULT_GRPC_PORT)),
        "cdn": validate_port(values.get("cdn", config.LUNAR_TEAR_DEFAULT_CDN_PORT)),
        "auth": validate_port(values.get("auth", config.LUNAR_TEAR_DEFAULT_AUTH_PORT)),
    }
    admin = int(values.get("admin") or 0)
    if admin and not (1 <= admin <= 65535):
        raise ValueError("admin port must be 0 (disabled) or 1..65535")

    current = wizard_config()
    cfg = {
        "ip": ip,
        "device": device,
        "detail": detail,
        "summary": _WIZARD_SUMMARY.get((device, detail), f"{device} ({detail})"),
        "grpc_port": ports["grpc"],
        "cdn_port": ports["cdn"],
        "auth_port": ports["auth"],
    }
    if admin:
        cfg["admin_port"] = admin
    elif current.get("admin_port"):
        cfg["admin_port"] = current["admin_port"]

    config.WIZARD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".wizard-", suffix=".json", dir=str(config.WIZARD_CONFIG_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, config.WIZARD_CONFIG_PATH)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return cfg
