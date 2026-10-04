"""Game-server control for the /settings page.

Two modes (resolved by settings_service.server_control_config):

  local   start/stop/restart the game server on this machine via the wizard
          scripts, kill by tracked PID/process group and by port owners.
  docker  control the compose-managed containers through a restricted
          docker-socket-proxy (never the raw Docker socket). Actions apply to
          every container carrying the configured label, and logs come from the
          server container's Docker log stream.

Probing/logging use the configurable probe host so a panel container can see
the server service on the compose network instead of its own loopback.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

from web import config
from web.services import settings_service


class ServerControlError(RuntimeError):
    pass


def _cfg() -> dict[str, str]:
    return settings_service.server_control_config()


def _wizard() -> dict[str, Any]:
    try:
        raw = json.loads(config.WIZARD_CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def ports() -> dict[str, int]:
    cfg = _wizard()
    return {
        "grpc": int(cfg.get("grpc_port") or config.LUNAR_TEAR_DEFAULT_GRPC_PORT),
        "cdn": int(cfg.get("cdn_port") or config.LUNAR_TEAR_DEFAULT_CDN_PORT),
        "auth": int(cfg.get("auth_port") or config.LUNAR_TEAR_DEFAULT_AUTH_PORT),
    }


def _port_open(port: int) -> bool:
    """Probe the configured server host (loopback or a compose service name)."""
    host = _cfg()["probe_host"]
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


# --- Docker API (through docker-socket-proxy) -------------------------------

def _docker_address() -> tuple[str, int]:
    raw = os.environ.get("DOCKER_HOST", "").strip()
    for prefix in ("tcp://", "http://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    raw = raw.rstrip("/")
    if not raw:
        raise ServerControlError("DOCKER_HOST is not set (expected tcp://dockerproxy:2375)")
    host, _, port = raw.partition(":")
    try:
        return host, int(port or 2375)
    except ValueError:
        raise ServerControlError(f"invalid DOCKER_HOST: {raw}") from None


def _docker_request(method: str, path: str, timeout: float = 20.0) -> tuple[int, bytes]:
    host, port = _docker_address()
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    except OSError as exc:
        raise ServerControlError(f"cannot reach the Docker proxy: {exc}") from exc
    finally:
        try:
            conn.close()  # type: ignore[possibly-undefined]
        except (NameError, OSError):
            pass
    if status == 403:
        raise ServerControlError("dockerproxy denied the request (check its allowlist)")
    return status, body


def _docker_get(path: str) -> Any:
    status, body = _docker_request("GET", path)
    if status >= 400:
        raise ServerControlError(f"Docker API GET {path} -> HTTP {status}")
    try:
        return json.loads(body or b"null")
    except ValueError:
        return None


def _docker_post(path: str) -> int:
    status, _ = _docker_request("POST", path)
    if status not in (200, 201, 204, 304):
        raise ServerControlError(f"Docker API POST {path} -> HTTP {status}")
    return status


def _docker_targets() -> list[dict[str, Any]]:
    label = _cfg()["container_label"]
    filters = urllib.parse.quote(json.dumps({"label": [label]}))
    rows = _docker_get(f"/containers/json?all=1&filters={filters}") or []
    targets = []
    for row in rows:
        labels = row.get("Labels") or {}
        targets.append({
            "id": row.get("Id"),
            "name": (row.get("Names") or [""])[0].lstrip("/"),
            "role": str(labels.get(label) or ""),
            "running": row.get("State") == "running",
        })
    return targets


# Compose startup order: assets first (cdn), then auth, then the game server.
_ROLE_ORDER = {"cdn": 0, "auth": 1, "server": 2}


def _docker_sorted(targets: list[dict[str, Any]], reverse: bool = False) -> list[dict[str, Any]]:
    return sorted(targets, key=lambda t: _ROLE_ORDER.get(str(t.get("role") or ""), 1), reverse=reverse)


def _docker_action(action: str) -> dict[str, Any]:
    targets = _docker_targets()
    if not targets:
        raise ServerControlError(f"no containers labelled '{_cfg()['container_label']}' were found")

    acted: list[dict[str, Any]] = []
    if action == "restart":
        # Restart all three services: game server first (reverse order), then
        # cdn/auth before the server comes back (startup dependencies).
        # Re-fetch between phases so the start phase sees the new states.
        for phase in ("stop", "start"):
            current = targets if phase == "stop" else _docker_targets()
            for target in _docker_sorted(current, reverse=(phase == "stop")):
                if phase == "start" and target["running"]:
                    continue
                http = _docker_post(f"/containers/{target['id']}/{phase}?t=10")
                acted.append({"role": target["role"], "name": target["name"], "action": phase, "http": http})
        return {"action": action, "containers": acted}

    for target in _docker_sorted(targets, reverse=(action == "stop")):
        if action == "start" and target["running"]:
            continue
        path = f"/containers/{target['id']}/{action}"
        if action == "stop":
            path += "?t=10"
        acted.append({"role": target["role"], "name": target["name"], "http": _docker_post(path)})
    return {"action": action, "containers": acted}


def _demux_docker_logs(data: bytes) -> bytes:
    """Strip Docker's 8-byte multiplexing headers (stdout/stderr streams)."""
    if len(data) < 8:
        return data
    out = bytearray()
    index = 0
    while index < len(data):
        if index + 8 > len(data):
            return data
        stream = data[index]
        size = int.from_bytes(data[index + 4:index + 8], "big")
        if stream not in (0, 1, 2) or index + 8 + size > len(data):
            return data
        out += data[index + 8:index + 8 + size]
        index += 8 + size
    return bytes(out)


def _docker_server_logs(lines: int) -> str:
    targets = _docker_targets()
    server = next((t for t in targets if t["role"] == "server"), None)
    if server is None and targets:
        server = targets[0]
    if server is None:
        return ""
    status, body = _docker_request(
        "GET", f"/containers/{server['id']}/logs?stdout=1&stderr=1&tail={lines}"
    )
    if status >= 400:
        raise ServerControlError(f"Docker API logs -> HTTP {status}")
    return _demux_docker_logs(body).decode("utf-8", errors="replace")


# --- local process helpers --------------------------------------------------

def _read_pid() -> int | None:
    try:
        text = config.SERVER_PID_PATH.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _write_pid(pid: int | None) -> None:
    try:
        if pid is None:
            config.SERVER_PID_PATH.unlink(missing_ok=True)
        else:
            config.SERVER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
            config.SERVER_PID_PATH.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_our_process(pid: int) -> bool:
    """Best-effort check that a tracked PID still belongs to the server tree."""
    if os.name == "nt" or not _pid_alive(pid):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return False
    return any(token in cmdline for token in ("wizard", "server-start", "cmd/dev", "bin/dev"))


def _kill_pid(pid: int, force: bool = False) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.killpg(os.getpgid(pid), sig)
        return True
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            return True
        except OSError:
            return False


def _pids_on_port(port: int) -> list[int]:
    """Best-effort PIDs listening on a TCP port (POSIX fuser/lsof, Windows netstat)."""
    pids: set[int] = set()
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
                    try:
                        pids.add(int(parts[4]))
                    except ValueError:
                        pass
        except (OSError, subprocess.SubprocessError):
            pass
        return sorted(pids)

    for cmd in (["fuser", "-n", "tcp", str(port)], ["lsof", "-t", "-iTCP:" + str(port), "-sTCP:LISTEN"]):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            blob = (proc.stdout or "") + " " + (proc.stderr or "")
            for token in blob.replace(":", " ").split():
                if token.isdigit():
                    pids.add(int(token))
            if pids:
                break
        except (OSError, subprocess.SubprocessError):
            continue
    return sorted(pids)


# --- public API --------------------------------------------------------------

def status() -> dict[str, Any]:
    cfg = _cfg()
    p = ports()
    listening = {name: _port_open(port) for name, port in p.items()}
    running = any(listening.values())
    docker_running: bool | None = None
    if cfg["mode"] == "docker":
        try:
            targets = _docker_targets()
            docker_running = any(t["running"] for t in targets)
            running = running or docker_running
        except ServerControlError:
            docker_running = None
    pid = _read_pid()
    if cfg["mode"] == "local" and pid is not None and not _pid_alive(pid):
        _write_pid(None)
        pid = None
    wizard = _wizard()
    return {
        "running": running,
        "listening": listening,
        "ports": p,
        "pid": pid if cfg["mode"] == "local" else None,
        "mode": cfg["mode"],
        "probe_host": cfg["probe_host"],
        "docker_running": docker_running,
        "wizard_configured": bool(wizard.get("ip")),
        "wizard": wizard,
    }


def log_tail(lines: int = 200) -> str:
    lines = max(1, min(int(lines), 2000))
    cfg = _cfg()
    if cfg["log_source"] == "docker":
        try:
            return _docker_server_logs(lines)
        except ServerControlError as exc:
            return f"[docker logs unavailable] {exc}"

    path = config.SERVER_LOG_PATH
    if not path.is_file():
        return ""
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
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""


def start() -> dict[str, Any]:
    """Start the server locally or, in docker mode, start all labelled containers."""
    if _cfg()["mode"] == "docker":
        return _docker_action("start")

    if not config.WIZARD_CONFIG_PATH.is_file() or not _wizard().get("ip"):
        raise ServerControlError(
            "No saved wizard config. Run ./server-start.sh once (or save the game-server "
            "settings on this page) so the server can start without prompts."
        )
    current = status()
    if current["running"]:
        raise ServerControlError(
            f"The server already looks running (gRPC port {current['ports']['grpc']} / "
            f"CDN port {current['ports']['cdn']})."
        )

    config.SERVER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = open(config.SERVER_LOG_PATH, "ab")  # noqa: SIM115 - handed to the child
    header = f"\n===== server start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode()
    log.write(header)
    log.flush()

    try:
        if os.name == "nt":
            cmd = ["cmd", "/c", str(config.ROOT / "server-start.bat"), "--prefer-saved"]
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0
            )
            proc = subprocess.Popen(
                cmd, cwd=str(config.ROOT), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, creationflags=creationflags,
            )
        else:
            cmd = [str(config.ROOT / "server-start.sh"), "--prefer-saved"]
            proc = subprocess.Popen(
                cmd, cwd=str(config.ROOT), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
    finally:
        log.close()
    _write_pid(proc.pid)
    return {"pid": proc.pid}


def stop() -> dict[str, Any]:
    """Stop the server locally or, in docker mode, stop all labelled containers."""
    if _cfg()["mode"] == "docker":
        return _docker_action("stop")

    killed: list[int] = []
    tracked = _read_pid()
    if tracked and _is_our_process(tracked):
        if _kill_pid(tracked):
            killed.append(tracked)
        time.sleep(0.6)
        if _pid_alive(tracked):
            _kill_pid(tracked, force=True)

    for port in ports().values():
        for pid in _pids_on_port(port):
            if pid in killed or pid == os.getpid():
                continue
            if _kill_pid(pid):
                killed.append(pid)
    time.sleep(0.5)
    for port in ports().values():
        for pid in _pids_on_port(port):
            if pid not in killed and pid != os.getpid():
                _kill_pid(pid, force=True)
                killed.append(pid)

    _write_pid(None)
    remaining = {name: _port_open(port) for name, port in ports().items()}
    return {
        "killed": killed,
        "still_listening": {k: v for k, v in remaining.items() if v},
        "stopped": not any(remaining.values()),
    }


def restart() -> dict[str, Any]:
    if _cfg()["mode"] == "docker":
        return _docker_action("restart")
    stopped = stop()
    started = start()
    return {"stopped": stopped, "started": started}
