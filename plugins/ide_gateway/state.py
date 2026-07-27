"""IDE Gateway endpoint lifecycle: start/stop/status/show_config/rotate/logs.

Ported from plugins/ide_provider/state.py, adapted for the full OpenAI surface.
Each endpoint is a worker subprocess reading its state file; the worker exposes
the full /v1/* surface and bridges requests to the active MCP model via the
file queue.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from plugins.ide_gateway.queue import clean_queue, request_counts
from plugins.ide_gateway.security import generate_token, redact_line


DEFAULT_CONFIG: dict[str, Any] = {
    "default_host": "127.0.0.1",
    "port_range": [8787, 8899],
    "default_port": 8787,
    "autostart": True,
    "default_model_id": "ide-gateway",
    "max_request_bytes": 4 * 1024 * 1024,
    "max_response_bytes": 4 * 1024 * 1024,
    "request_timeout_seconds": 300,
    "wait_timeout_seconds": 3600,
    "max_pending_requests": 8,
    "log_retention_lines": 1000,
    "default_api_key": "",
    "embeddings_mode": "fallback",
    "embeddings_dim": 1536,
    "disabled_tools": "",
}

_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9._/-]{1,80}$")
_API_KEY_RE = re.compile(r"^ideg_[A-Za-z0-9_-]{16,}$")


def runtime_root(context: dict[str, Any]) -> Path:
    workspace = Path(str(context.get("workspacePath", ""))).resolve()
    return workspace / "temp" / "ide_gateway_runtime"


def safe_runtime_path(context: dict[str, Any], *parts: str) -> Path:
    root = runtime_root(context).resolve()
    path = root.joinpath(*parts).resolve()
    if root != path and root not in path.parents:
        raise ValueError("runtime path escapes ide_gateway runtime root")
    return path


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _endpoint_state_path(context: dict[str, Any], name: str) -> Path:
    return safe_runtime_path(context, "endpoints", f"{name}.json")


def _endpoint_index_path(context: dict[str, Any]) -> Path:
    return safe_runtime_path(context, "endpoints.json")


def normalize_name(name: Any) -> str:
    name = str(name or "default").strip().lower()
    name = re.sub(r"[^a-z0-9_-]", "", name)
    if not name:
        name = "default"
    return name[:64]


def is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) != 0


def find_free_port(host: str, low: int, high: int) -> int:
    for port in range(low, high + 1):
        if is_port_free(host, port):
            return port
    raise ValueError("no free port in range")


def normalize_config(config: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(config, dict):
        config = {}

    normalized: dict[str, Any] = dict(DEFAULT_CONFIG)
    for key in DEFAULT_CONFIG:
        if key in config:
            normalized[key] = config[key]

    host = str(normalized.get("default_host", "")).strip().lower()
    if host == "localhost":
        host = "127.0.0.1"
    if host not in _ALLOWED_HOSTS:
        raise ValueError(f"default_host must be one of {_ALLOWED_HOSTS}")
    normalized["default_host"] = "127.0.0.1"

    port_range = normalized.get("port_range")
    if not isinstance(port_range, (list, tuple)) or len(port_range) != 2:
        raise ValueError("port_range must be a list of two integers")
    try:
        low = int(port_range[0])
        high = int(port_range[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("port_range values must be integers") from exc
    if low < 1024 or high > 65535 or low > high:
        raise ValueError("port_range must be within 1024..65535 and low <= high")
    normalized["port_range"] = [low, high]

    model_id = str(normalized.get("default_model_id", "")).strip()
    if not _MODEL_ID_RE.match(model_id):
        raise ValueError("default_model_id must match ^[a-zA-Z0-9._/-]{1,80}$")
    normalized["default_model_id"] = model_id

    default_api_key = str(normalized.get("default_api_key", "") or "").strip()
    if default_api_key and not _API_KEY_RE.match(default_api_key):
        raise ValueError("default_api_key must be a generated ideg_ token")
    normalized["default_api_key"] = default_api_key

    for key in ("max_request_bytes", "max_response_bytes"):
        try:
            value = int(normalized[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a positive integer") from exc
        if value <= 0:
            raise ValueError(f"{key} must be positive")
        normalized[key] = value

    request_timeout = int(normalized["request_timeout_seconds"])
    if request_timeout < 5 or request_timeout > 1800:
        raise ValueError("request_timeout_seconds must be between 5 and 1800")
    normalized["request_timeout_seconds"] = request_timeout

    wait_timeout = int(normalized["wait_timeout_seconds"])
    if wait_timeout < 1 or wait_timeout > 86400:
        raise ValueError("wait_timeout_seconds must be between 1 and 86400")
    normalized["wait_timeout_seconds"] = wait_timeout

    max_pending = int(normalized["max_pending_requests"])
    if max_pending < 1:
        raise ValueError("max_pending_requests must be at least 1")
    normalized["max_pending_requests"] = max_pending

    log_retention = int(normalized["log_retention_lines"])
    if log_retention < 0:
        raise ValueError("log_retention_lines must be non-negative")
    normalized["log_retention_lines"] = log_retention

    emb_mode = str(normalized.get("embeddings_mode", "fallback")).strip().lower()
    if emb_mode not in {"fallback", "off"}:
        emb_mode = "fallback"
    normalized["embeddings_mode"] = emb_mode

    try:
        emb_dim = int(normalized.get("embeddings_dim", 1536))
    except (TypeError, ValueError):
        emb_dim = 1536
    if emb_dim <= 0:
        emb_dim = 1536
    normalized["embeddings_dim"] = emb_dim

    normalized["disabled_tools"] = str(normalized.get("disabled_tools", "") or "")

    default_port = int(normalized.get("default_port", 8787) or 8787)
    if default_port < 1024 or default_port > 65535:
        default_port = 8787
    normalized["default_port"] = default_port

    autostart = normalized.get("autostart", True)
    if not isinstance(autostart, bool):
        autostart = str(autostart).strip().lower() in {"1", "true", "yes", "on"}
    normalized["autostart"] = autostart

    return normalized


def _is_process_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


_ACTIVE_PROCS: dict[int, subprocess.Popen[bytes]] = {}


def _reap_proc(pid: int, timeout: float = 5.0) -> None:
    proc = _ACTIVE_PROCS.pop(pid, None)
    if proc is None:
        return
    if proc.poll() is None:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    _reap_proc(pid)


def _save_endpoint_state(context: dict[str, Any], state: dict[str, Any]) -> None:
    name = state["name"]
    path = _endpoint_state_path(context, name)
    _atomic_write_json(path, state)
    index_path = _endpoint_index_path(context)
    index = _read_json(index_path, {}) or {}
    index[name] = True
    _atomic_write_json(index_path, index)


def _load_endpoint_state(context: dict[str, Any], name: str) -> dict[str, Any] | None:
    path = _endpoint_state_path(context, name)
    return _read_json(path, None)


def start_endpoint(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if context.get("effectiveMode") != "full_access":
        raise ValueError("ide_gateway_start requires full_access under a trusted profile")

    name = normalize_name(arguments.get("name"))
    host = str(arguments.get("host") or config["default_host"]).strip().lower()
    if host == "localhost":
        host = "127.0.0.1"
    if host not in _ALLOWED_HOSTS:
        raise ValueError(f"host must be one of {_ALLOWED_HOSTS}")

    model_id = str(arguments.get("model_id") or config["default_model_id"]).strip()
    if not _MODEL_ID_RE.match(model_id):
        raise ValueError("model_id is invalid")

    request_timeout = int(arguments.get("request_timeout_seconds") or config["request_timeout_seconds"])
    if request_timeout < 5 or request_timeout > 1800:
        raise ValueError("request_timeout_seconds must be between 5 and 1800")

    # Idempotent same-endpoint check MUST happen before port-free check.
    existing = _load_endpoint_state(context, name)
    if existing and existing.get("status") == "running" and _is_process_alive(existing.get("pid")):
        return {
            "ok": True, "name": name, "status": "already_running",
            "base_url": existing["base_url"], "api_key": existing["token"],
            "model": existing["model_id"],
            "endpoints": _endpoint_summary(),
            "opencode": {"provider_type": "openai-compatible", "baseURL": existing["base_url"],
                         "apiKey": existing["token"], "model": existing["model_id"]},
            "message": f"Endpoint {name} is already running.",
        }

    if existing and existing.get("status") == "running" and not _is_process_alive(existing.get("pid")):
        clean_queue(runtime_root(context), name)

    port_arg = arguments.get("port")
    if port_arg is not None and str(port_arg).strip() != "":
        port = int(port_arg)
    else:
        # Sticky preferred port: default_port (8787). If taken by a live
        # endpoint of ours, treat as already_running; if taken by something
        # else, fall back to a free port in the range.
        port = int(config.get("default_port", 8787) or 8787)
    if port <= 0:
        low, high = config["port_range"]
        port = find_free_port(host, low, high)
    else:
        if port < 1024 or port > 65535:
            raise ValueError("port must be within 1024..65535, or 0/empty for auto-pick")
        if not is_port_free(host, port):
            # If the port is held by our own live worker, treat as already running.
            existing_same_port = _load_endpoint_state(context, name)
            if (existing_same_port and existing_same_port.get("status") == "running"
                    and existing_same_port.get("port") == port
                    and _is_process_alive(existing_same_port.get("pid"))):
                return {
                    "ok": True, "name": name, "status": "already_running",
                    "base_url": existing_same_port["base_url"], "api_key": existing_same_port["token"],
                    "model": existing_same_port["model_id"],
                    "endpoints": _endpoint_summary(),
                    "opencode": {"provider_type": "openai-compatible", "baseURL": existing_same_port["base_url"],
                                 "apiKey": existing_same_port["token"], "model": existing_same_port["model_id"]},
                    "message": f"Endpoint {name} is already running on the preferred port {port}.",
                }
            low, high = config["port_range"]
            port = find_free_port(host, low, high)

    token = str(config.get("default_api_key") or "").strip() or generate_token()
    now = datetime.datetime.now().isoformat()
    root = runtime_root(context)
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("endpoints", "queues", "logs", "pids", "registry"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    base_url = f"http://{host}:{port}/v1"
    log_path = root / "logs" / f"{name}.log"
    state: dict[str, Any] = {
        "name": name, "status": "starting",
        "host": host, "port": port, "base_url": base_url,
        "model_id": model_id, "token": token, "pid": None,
        "started_at": now,
        "request_timeout_seconds": request_timeout,
        "max_request_bytes": config["max_request_bytes"],
        "max_response_bytes": config["max_response_bytes"],
        "max_pending_requests": config["max_pending_requests"],
        "embeddings_mode": config["embeddings_mode"],
        "embeddings_dim": config["embeddings_dim"],
        "disabled_tools": config["disabled_tools"],
        "log_path": str(log_path),
        "requests_total": 0, "responses_total": 0, "errors_total": 0, "last_error": "",
    }
    _save_endpoint_state(context, state)

    worker_path = Path(__file__).resolve().parent / "worker.py"
    state_path = _endpoint_state_path(context, name)
    workspace = Path(str(context.get("workspacePath", ""))).resolve()
    with open(log_path, "ab") as log_f:
        proc = subprocess.Popen(
            [sys.executable, str(worker_path), "--state", str(state_path)],
            cwd=str(workspace), stdout=log_f, stderr=subprocess.STDOUT,
            close_fds=(os.name != "nt"),
        )
    _ACTIVE_PROCS[proc.pid] = proc

    pid = proc.pid
    state["pid"] = pid
    _save_endpoint_state(context, state)

    health_url = f"http://{host}:{port}/health"
    ready = False
    deadline = time.time() + 20
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    if data.get("ok"):
                        ready = True
                        break
        except Exception as exc:
            last_err = str(exc)
        time.sleep(0.25)

    if not ready:
        _kill_pid(pid)
        state["status"] = "failed"
        _save_endpoint_state(context, state)
        return {"ok": False, "status": "failed", "message": f"worker did not become healthy: {last_err}"}

    state["status"] = "running"
    _save_endpoint_state(context, state)

    return {
        "ok": True, "name": name, "status": "running",
        "base_url": base_url, "api_key": token, "model": model_id,
        "endpoints": _endpoint_summary(),
        "opencode": {"provider_type": "openai-compatible", "baseURL": base_url,
                     "apiKey": token, "model": model_id},
        "message": (
            "IDE Gateway is running with the full OpenAI-compatible surface. Add it "
            "as an OpenAI-compatible provider in your IDE. Keep this chat active and "
            "call ide_gateway_wait_request to serve requests; reply via "
            "ide_gateway_send_response."
        ),
    }


def _endpoint_summary() -> list[str]:
    return [
        "/v1/chat/completions (stream + non-stream)",
        "/v1/responses (stream + non-stream, background, cancel, input_items)",
        "/v1/completions (legacy)",
        "/v1/models",
        "/v1/tools",
        "/v1/files (upload, list, get, content, delete)",
        "/v1/images/generations, /v1/images/edits",
        "/v1/audio/speech, /v1/audio/transcriptions, /v1/audio/translations",
        "/v1/embeddings (local fallback)",
        "/v1/moderations (local heuristic)",
    ]


def stop_endpoint(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if context.get("effectiveMode") != "full_access":
        raise ValueError("ide_gateway_stop requires full_access under a trusted profile")

    name = normalize_name(arguments.get("name"))
    state = _load_endpoint_state(context, name)
    if not state or state.get("status") != "running":
        return {"ok": False, "status": "not_running", "message": f"Endpoint {name} is not running"}

    pid = state.get("pid")
    if pid and _is_process_alive(pid):
        _kill_pid(pid)

    state["status"] = "stopped"
    _save_endpoint_state(context, state)
    clean_queue(runtime_root(context), name)
    return {"ok": True, "name": name, "status": "stopped", "message": f"Endpoint {name} stopped"}


def endpoint_status(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    name = arguments.get("name")
    runtime = runtime_root(context)
    index_path = runtime / "endpoints.json"
    index = _read_json(index_path, {}) or {}
    names = [normalize_name(name)] if name else list(index.keys())

    endpoints: list[dict[str, Any]] = []
    for n in names:
        state = _load_endpoint_state(context, n)
        if not state:
            continue
        if state.get("status") == "running" and not _is_process_alive(state.get("pid")):
            state["status"] = "stopped"
            _save_endpoint_state(context, state)
        counts = request_counts(runtime, n)
        endpoints.append({
            "name": n, "status": state.get("status"),
            "host": state.get("host"), "port": state.get("port"),
            "model_id": state.get("model_id"), "base_url": state.get("base_url"),
            "pending": counts.get("pending", 0), "claimed": counts.get("claimed", 0),
            "completed": counts.get("completed", 0), "failed": counts.get("failed", 0),
            "expired": counts.get("expired", 0),
        })
    return {"ok": True, "endpoints": endpoints}


def show_config(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    name = normalize_name(arguments.get("name"))
    include_secret = bool(arguments.get("include_secret", False))
    state = _load_endpoint_state(context, name)
    if not state or state.get("status") != "running":
        return {"ok": False, "status": "not_running", "message": f"Endpoint {name} is not running"}

    base_url = state["base_url"]
    model_id = state["model_id"]
    token = state["token"] if include_secret else "***"
    return {
        "ok": True, "name": name, "base_url": base_url,
        "api_key": token, "model": model_id,
        "endpoints": _endpoint_summary(),
        "opencode": {"provider_type": "openai-compatible", "baseURL": base_url,
                     "apiKey": token, "model": model_id},
        "message": "Use these settings in your IDE as an OpenAI-compatible provider.",
    }


def rotate_token(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if context.get("effectiveMode") != "full_access":
        raise ValueError("ide_gateway_rotate_token requires full_access under a trusted profile")

    name = normalize_name(arguments.get("name"))
    state = _load_endpoint_state(context, name)
    if not state or state.get("status") != "running":
        return {"ok": False, "status": "not_running", "message": f"Endpoint {name} is not running"}

    new_token = generate_token()
    state["token"] = new_token
    _save_endpoint_state(context, state)
    return {"ok": True, "name": name, "token": new_token}


def read_logs(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    name = normalize_name(arguments.get("name"))
    limit = int(arguments.get("limit") or 100)
    log_path = safe_runtime_path(context, "logs", f"{name}.log")
    if not log_path.is_file():
        return {"ok": True, "name": name, "limit": limit, "lines": []}

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"ok": True, "name": name, "limit": limit, "lines": []}

    lines = text.splitlines()
    redacted = [redact_line(line) for line in lines[-limit:]]
    return {"ok": True, "name": name, "limit": limit, "lines": redacted}
