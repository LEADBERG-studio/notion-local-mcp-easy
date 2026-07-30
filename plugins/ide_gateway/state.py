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
    # Gateway mode: "sandbox" (default, resident egress) | "bridge" (queue + model)
    # | "external" (direct OpenAI-compatible provider)
    "gateway_mode": "sandbox",
    # Sandbox/external backend settings (used when gateway_mode != "bridge")
    "upstream_base_url": "",
    "upstream_api_key": "",
    "upstream_model": "",
    "sandbox_script": "",  # path to sandbox_server.py (auto-detected if empty)
    "extra_models": "",
    # Tunnellio sandbox tunnel config (provisioned at setup time)
    "tunnellio_token": "",
    "tunnellio_domain_id": "",
    "tunnellio_key_id": "",
    "tunnellio_public_url": "",
    "tunnellio_ssh_host": "",
    "tunnellio_ssh_port": "",
    "tunnellio_ssh_user": "",
    "tunnellio_remote_hostname": "",
    "tunnellio_private_key": "",
    "tunnellio_private_key_content": "",
    "tunnellio_mode": "",
    "tunnellio_hostname": "",
    "tunnellio_custom_hostname": "",
}

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}
_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9._/-]{1,80}$")
_API_KEY_RE = re.compile(r"^ideg_[A-Za-z0-9_-]{16,}$")


def _is_allowed_host(host: str) -> bool:
    """Check if the host is allowed for binding. Allows loopback, 0.0.0.0,
    and LAN IP addresses."""
    if host in _ALLOWED_HOSTS:
        return True
    # Allow IP addresses (IPv4)
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


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
    if not _is_allowed_host(host):
        raise ValueError(f"default_host must be 127.0.0.1, localhost, 0.0.0.0, or a LAN IP address")
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

    # Gateway mode
    gw_mode = str(normalized.get("gateway_mode", "sandbox")).strip().lower()
    if gw_mode not in {"bridge", "sandbox", "external"}:
        gw_mode = "sandbox"
    normalized["gateway_mode"] = gw_mode

    normalized["upstream_base_url"] = str(normalized.get("upstream_base_url", "") or "").strip()
    normalized["upstream_api_key"] = str(normalized.get("upstream_api_key", "") or "").strip()
    normalized["upstream_model"] = str(normalized.get("upstream_model", "") or "").strip()
    normalized["sandbox_script"] = str(normalized.get("sandbox_script", "") or "").strip()
    normalized["extra_models"] = str(normalized.get("extra_models", "") or "")

    # Tunnellio config fields (pass-through, validated at provision time)
    for k in ("tunnellio_token", "tunnellio_domain_id", "tunnellio_key_id",
              "tunnellio_public_url", "tunnellio_ssh_host", "tunnellio_ssh_port",
              "tunnellio_ssh_user",               "tunnellio_remote_hostname", "tunnellio_private_key",
              "tunnellio_private_key_content",
              "tunnellio_mode", "tunnellio_hostname", "tunnellio_custom_hostname"):
        normalized[k] = str(normalized.get(k, "") or "").strip()

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
    if not _is_allowed_host(host):
        raise ValueError(f"host must be 127.0.0.1, localhost, 0.0.0.0, or a LAN IP address")

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

    token = str(config.get("default_api_key") or "").strip()
    if not token:
        # Try reading from global config.json (survives version upgrades)
        try:
            global_cfg_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NotionMcpEasy" / "config.json"
            if global_cfg_path.is_file():
                import json as _json
                global_cfg = _json.loads(global_cfg_path.read_text(encoding="utf-8"))
                token = str(global_cfg.get("ide_gateway_api_key", "")).strip()
        except Exception:
            pass
    if not token:
        token = generate_token()
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
        "gateway_mode": config.get("gateway_mode", "bridge"),
        "upstream_base_url": config.get("upstream_base_url", ""),
        "upstream_api_key": config.get("upstream_api_key", ""),
        "upstream_model": config.get("upstream_model", ""),
        "extra_models": config.get("extra_models", ""),
        # Tunnellio sandbox tunnel config (provisioned at setup time)
        "tunnellio_token": config.get("tunnellio_token", ""),
        "tunnellio_domain_id": config.get("tunnellio_domain_id", ""),
        "tunnellio_key_id": config.get("tunnellio_key_id", ""),
        "tunnellio_public_url": config.get("tunnellio_public_url", ""),
        "tunnellio_hostname": config.get("tunnellio_hostname", ""),
        "tunnellio_ssh_host": config.get("tunnellio_ssh_host", ""),
        "tunnellio_ssh_port": config.get("tunnellio_ssh_port", ""),
        "tunnellio_ssh_user": config.get("tunnellio_ssh_user", ""),
        "tunnellio_remote_hostname": config.get("tunnellio_remote_hostname", ""),
        "tunnellio_private_key": config.get("tunnellio_private_key", ""),
        "tunnellio_private_key_content": config.get("tunnellio_private_key_content", ""),
        "tunnellio_mode": config.get("tunnellio_mode", ""),
        "log_path": str(log_path),
        "requests_total": 0, "responses_total": 0, "errors_total": 0, "last_error": "",
    }
    _save_endpoint_state(context, state)

    # Choose the server script based on gateway_mode.
    # bridge → worker.py (queue-based, model serves via poll loop)
    # sandbox → worker.py too, but the model launches sandbox_server.py
    #   separately in the sandbox via run_program (where env vars live).
    # external → sandbox_server.py on this machine (direct upstream, no queue)
    gw_mode = config.get("gateway_mode", "bridge")
    if gw_mode == "external":
        server_script = Path(__file__).resolve().parent / "sandbox_server.py"
    else:
        server_script = Path(__file__).resolve().parent / "worker.py"
    state_path = _endpoint_state_path(context, name)
    workspace = Path(str(context.get("workspacePath", ""))).resolve()
    with open(log_path, "ab") as log_f:
        proc = subprocess.Popen(
            [sys.executable, str(server_script), "--state", str(state_path)],
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


def bridge_prompt(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Return a system-prompt snippet for the configured gateway mode."""
    name = normalize_name(arguments.get("name"))
    state = _load_endpoint_state(context, name)
    base_url = state["base_url"] if state else "http://127.0.0.1:8787/v1"
    model = state["model_id"] if state else config.get("default_model_id", "ide-gateway")
    token = state["token"] if state and arguments.get("include_secret") else "***"
    workspace = str(context.get("workspacePath", ""))
    bridge_script = str(Path(__file__).resolve().parent / "bridge_step.py")
    sandbox_script = str(Path(__file__).resolve().parent / "sandbox_server.py")
    gw_mode = config.get("gateway_mode", "bridge")

    if gw_mode == "sandbox":
        prompt = _sandbox_prompt(base_url, model, token, sandbox_script, workspace, state)
        msg = "Скажите модели: «подними мост». Она запустит sandbox_server.py как фоновый процесс, и IDE будет работать напрямую с LLM."
    elif gw_mode == "external":
        upstream = config.get("upstream_base_url", "")
        prompt = _external_prompt(base_url, model, token, upstream)
        msg = "External mode: шлюз уже работает напрямую с upstream. Модели ничего делать не нужно."
    else:
        prompt = _bridge_prompt_text(base_url, model, token, workspace, bridge_script)
        msg = "Bridge mode: скажите модели «подними мост» — она запустит poll-цикл через bridge_step.py."

    return {
        "ok": True,
        "name": name,
        "gateway_mode": gw_mode,
        "base_url": base_url,
        "api_key": token,
        "model": model,
        "bridge_script": bridge_script if gw_mode == "bridge" else None,
        "sandbox_script": sandbox_script if gw_mode == "sandbox" else None,
        "system_prompt": prompt,
        "message": msg,
    }


def _bridge_prompt_text(base_url: str, model: str, token: str,
                         workspace: str, bridge_script: str) -> str:
    return (
        "# IDE Gateway bridge mode (run_program loop)\n"
        "You are connected to an IDE through the ide_gateway plugin. "
        "Keep the bridge alive so IDE requests are served automatically.\n\n"
        "## How it works\n"
        f"The IDE sends OpenAI-compatible requests to {base_url}. The gateway "
        "worker queues them. You serve them by running bridge_step.py via "
        "run_program, which works with the queue files directly (no HTTP "
        "recursion, no MCP tool-call timeout issues).\n\n"
        "## Bridge script\n"
        f"Script: `{bridge_script}`\n"
        f"Workspace: `{workspace}`\n\n"
        "## Loop (repeat forever, do not write chat summaries between iterations)\n"
        "1. **Poll** for the next IDE request (3s timeout, avoids 502 proxy):\n"
        "   run_program: python " + repr(bridge_script) + " poll --timeout 3\n"
        "   - If output says \"status\": \"timeout\", immediately poll again.\n"
        "   - If output says \"status\": \"claimed\", read prompt_type, "
        "user_message, request_id from the JSON output.\n\n"
        "2. If prompt_type == \"memory_extraction\":\n"
        "   - run_program: python " + repr(bridge_script) + " complete "
        "--request-id <id> --content \"SubmitMemoryPlan: noop, items=[].\"\n"
        "   - Go back to step 1.\n\n"
        "3. If prompt_type == \"chat\":\n"
        "   - Read user_message — this is the real question from the IDE.\n"
        "   - Answer it using your MCP tools (read_file, run_command, "
        "web_search, etc.) as needed.\n"
        "   - run_program: python " + repr(bridge_script) + " complete "
        "--request-id <id> --content \"<your answer>\"\n"
        "   - For multi-line content, use --content - and pipe via stdin.\n"
        "   - Go back to step 1.\n\n"
        "4. If prompt_type == \"other\":\n"
        "   - run_program: python " + repr(bridge_script) + " complete "
        "--request-id <id> --content \"\"\n"
        "   - Go back to step 1.\n\n"
        "5. If you cannot answer, **fail** the request:\n"
        "   run_program: python " + repr(bridge_script) + " fail "
        "--request-id <id> --message \"reason\"\n"
        "   - Go back to step 1.\n\n"
        "Do not write chat messages between iterations. "
        "Do not wrap this in a Python script with a loop. "
        "Each run_program is a separate call.\n\n"
        "## Connection (for reference)\n"
        f"- base_url: {base_url}\n"
        f"- api_key: {token}\n"
        f"- model: {model}\n\n"
        "## Check status\n"
        "   run_program: python " + repr(bridge_script) + " status\n"
        "Shows queue counters (pending/claimed/completed/failed/expired).\n\n"
        "## First-time setup\n"
        "Make sure ide_gateway_status shows the endpoint as running. "
        "If not, call ide_gateway_start first."
    )


def _sandbox_prompt(base_url: str, model: str, token: str,
                     sandbox_script: str, workspace: str,
                     state: dict[str, Any] | None = None) -> str:
    tunnel_script = str(Path(__file__).resolve().parent / "sandbox_tunnel.py")
    # Compute the endpoint state file path (where worker stores its config)
    state_path = ""
    public_url = ""
    if state:
        name = state.get("name", "default")
        # The state file is at <runtime>/endpoints/<name>.json
        rt = runtime_root({"workspacePath": workspace})
        state_path = str(rt / "endpoints" / f"{name}.json")
        public_url = state.get("tunnellio_public_url", "")

    prompt = (
        "# Подними мост (sandbox mode)\n"
        "Нужно запустить 2 процесса:\n"
        "1. sandbox_server.py — LLM-шлюз\n"
        "2. sandbox_tunnel.py — туннель к Tunnellio\n\n"
        "## Шаг 1: запусти sandbox-сервер\n"
        "   run_program: python " + repr(sandbox_script) + " --port 8787\n"
        "Сервер поднимется и будет обслуживать запросы через LLM egress.\n\n"
        "## Шаг 2: запусти туннель\n"
        "Воркер уже зарезервировал имя домена. Туннель читает hostname из state,\n"
        "генерирует SSH-ключ в sandbox, создаёт домен с этим hostname и поднимает туннель.\n"
    )
    if state_path:
        prompt += (
            "   run_program: python " + repr(tunnel_script) + " --state " + repr(state_path) + "\n"
        )
    else:
        prompt += (
            "   run_program: python " + repr(tunnel_script) + " --local-port 8787\n"
        )
    prompt += (
        "В выводе будет публичный URL.\n\n"
        "## Проверка\n"
        "   run_program: curl -s http://127.0.0.1:8787/health\n"
        "Должно быть: {\"status\": \"alive\", \"mode\": \"sandbox\"}\n\n"
        "## Подключение IDE\n"
    )
    if public_url:
        prompt += f"   base_url: {public_url.rstrip('/')}/v1\n"
    else:
        prompt += f"   base_url: {base_url}\n"
    prompt += (
        f"   api_key: {token}\n"
        f"   model: {model} (или любой из /v1/models)\n\n"
        "Модель больше ничего не делает — туннель и сервер работают в фоне."
    )
    return prompt


def _external_prompt(base_url: str, model: str, token: str,
                      upstream: str) -> str:
    return (
        "# Мост уже работает\n"
        "Шлюз настроен на прямой вызов upstream-провайдера.\n"
        "Тебе ничего делать не нужно — IDE-запросы обслуживаются автоматически.\n\n"
        "## Подключение IDE\n"
        f"   base_url: {base_url}\n"
        f"   api_key: {token}\n"
        f"   model: {model}\n"
        f"   upstream: {upstream}\n\n"
        "## Проверка\n"
        "   run_program: curl -s http://127.0.0.1:8787/health"
    )
