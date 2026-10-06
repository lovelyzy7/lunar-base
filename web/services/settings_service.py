"""Panel settings: data/settings.json + wizard config helpers.

Everything the /settings page reads or writes lives here. The file is small and
read by config.load_settings() on every auth/host resolution, so writes are
atomic (tmp + replace) to avoid ever exposing a half-written JSON file.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from typing import Any

from web import config

_LOCK = threading.RLock()

_DEFAULT_SETTINGS: dict[str, Any] = {
    "host": "",
    "port": 0,
    "auth": None,
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


# --- server control / wizard -------------------------------------------------

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
