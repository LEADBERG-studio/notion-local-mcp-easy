"""Self-contained IDE Gateway installer for model sandboxes.

The installer is embedded into ide_gateway_bridge_prompt, copied into the
model's own sandbox, and executed there. It has no dependency on the local MCP
workspace. It installs a resident OpenAI-compatible proxy and the official
keyless Tunnellio client, then starts both under detached supervisors.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
import signal
import socket
import select
import threading
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

HOME = Path.home() / ".ide_gateway"
CONFIG = HOME / "config.json"
STATE = HOME / "state.json"
LOG_DIR = HOME / "logs"
SERVER_LOG = LOG_DIR / "server.log"
TUNNEL_LOG = LOG_DIR / "tunnel.log"
INSTALLER = HOME / "sandbox_installer.py"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, value: dict[str, Any], *, secret: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    if secret and os.name != "nt":
        path.chmod(0o600)


def split_models(raw: str) -> list[str]:
    result: list[str] = []
    for item in str(raw or "").replace("\n", ",").replace(";", ",").split(","):
        item = item.strip().strip('"\'')
        if item and item not in result:
            result.append(item)
    return result


def normalize_base_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        raise ValueError("internal LLM base URL is required")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("internal LLM base URL must be a full http(s) URL")
    return value


def is_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def stop_pid(pid: Any) -> None:
    try:
        pid = int(pid)
        if pid <= 0:
            return
    except Exception:
        return
    if os.name == "nt":
        # os.kill(pid, 0) is unreliable for detached Windows process groups.
        # taskkill is idempotent enough here and must run even when the probe
        # already reports dead, because inherited log handles can remain open.
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        time.sleep(3.0)
        return
    if not is_alive(pid):
        return
    with contextlib.suppress(Exception):
        os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 8
    while time.time() < deadline and is_alive(pid):
        time.sleep(0.1)
    if is_alive(pid):
        with contextlib.suppress(Exception):
            os.kill(pid, signal.SIGKILL)


def rotate(path: Path, keep: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    oldest = path.with_name(path.name + f".{max(1, keep - 1)}")
    oldest.unlink(missing_ok=True)
    for index in range(max(1, keep - 2), 0, -1):
        source = path.with_name(path.name + f".{index}")
        if source.exists():
            source.replace(path.with_name(path.name + f".{index + 1}"))
    if path.exists():
        try:
            path.replace(path.with_name(path.name + ".1"))
        except PermissionError:
            # A just-terminated Windows child may release its inherited handle
            # slightly later. Do not fail installation because log rotation lagged.
            time.sleep(0.5)
            with contextlib.suppress(PermissionError):
                path.replace(path.with_name(path.name + ".1"))


def detached(command: list[str], log_path: Path) -> subprocess.Popen:
    rotate(log_path)
    handle = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(HOME),
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)
    handle.close()
    return proc


def http_json(url: str, *, method: str = "GET", data: dict | None = None,
              headers: dict[str, str] | None = None, timeout: float = 15) -> tuple[int, dict, dict[str, str]]:
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8") or "{}")
            return resp.status, parsed, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            parsed = {"error": {"message": raw.decode("utf-8", errors="replace")}}
        return exc.code, parsed, dict(exc.headers.items())


def upstream_headers(config: dict[str, Any]) -> dict[str, str]:
    key = str(config.get("upstream_api_key", ""))
    style = str(config.get("upstream_auth_style", "bearer"))
    if not key:
        return {}
    if style == "x-api-key":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def upstream_models(config: dict[str, Any]) -> list[str]:
    configured = split_models(str(config.get("models", "")))
    if configured:
        return configured
    base = str(config["upstream_base_url"]).rstrip("/")
    for suffix in ("/models", "/v1/models"):
        try:
            status, body, _ = http_json(base + suffix, headers=upstream_headers(config), timeout=5)
            if status != 200:
                continue
            items = body.get("data", body.get("models", []))
            values: list[str] = []
            for item in items if isinstance(items, list) else []:
                model = item.get("id") or item.get("name") if isinstance(item, dict) else item
                if model:
                    values.append(str(model))
            if values:
                return values
        except Exception:
            continue
    default_model = str(config.get("default_model", "")).strip()
    return [default_model] if default_model else ["ide-gateway"]


def check_ide_auth(handler: BaseHTTPRequestHandler, config: dict[str, Any]) -> bool:
    expected = str(config.get("ide_api_key", ""))
    if not expected:
        return True
    auth = handler.headers.get("Authorization", "")
    provided = auth[7:].strip() if auth.lower().startswith("bearer ") else handler.headers.get("X-API-Key", "").strip()
    return provided == expected


def choose_model(payload: dict[str, Any], config: dict[str, Any]) -> str:
    requested = str(payload.get("model", "")).strip()
    available = split_models(str(config.get("models", "")))
    default = str(config.get("default_model", "")).strip()
    if requested and requested not in {"ide-gateway", "default"}:
        return requested
    return default or (available[0] if available else requested or "ide-gateway")


def openai_to_anthropic(payload: dict[str, Any], model: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for item in payload.get("messages", []):
        if not isinstance(item, dict): continue
        role = str(item.get("role", "user"))
        content = item.get("content", "")
        if role in {"system", "developer"}:
            system_parts.append(str(content)); continue
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    result: dict[str, Any] = {
        "model": model, "messages": messages,
        "max_tokens": int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 4096),
        "stream": False,
    }
    if system_parts: result["system"] = "\n".join(system_parts)
    for key in ("temperature", "top_p"):
        if key in payload: result[key] = payload[key]
    return result


def anthropic_text(body: dict[str, Any]) -> str:
    return "".join(str(item.get("text", "")) for item in body.get("content", []) if isinstance(item, dict) and item.get("type") == "text")


class Handler(BaseHTTPRequestHandler):
    server_version = "IDEGatewaySandbox/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def config(self) -> dict[str, Any]:
        return self.server.config  # type: ignore[attr-defined]

    def send_json(self, status: int, value: Any) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 16 * 1024 * 1024:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8") or "{}")
        return value if isinstance(value, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True, "mode": "sandbox", "models": upstream_models(self.config)})
            return
        if not check_ide_auth(self, self.config):
            self.send_json(401, {"error": {"message": "Unauthorized", "type": "authentication_error"}})
            return
        if path in {"/v1/models", "/models"}:
            models = upstream_models(self.config)
            self.send_json(200, {"object": "list", "data": [
                {"id": item, "object": "model", "created": 0, "owned_by": "sandbox"}
                for item in models
            ]})
            return
        self.send_json(404, {"error": {"message": "Not found", "type": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if not check_ide_auth(self, self.config):
            self.send_json(401, {"error": {"message": "Unauthorized", "type": "authentication_error"}})
            return
        try:
            payload = self.read_json()
            if path in {"/v1/chat/completions", "/chat/completions"}:
                self.proxy_chat(payload)
                return
            if path in {"/v1/responses", "/responses"}:
                self.proxy_responses(payload)
                return
            self.send_json(404, {"error": {"message": "Not found", "type": "not_found"}})
        except Exception as exc:
            self.send_json(500, {"error": {"message": str(exc), "type": "gateway_error"}})

    def proxy_chat(self, payload: dict[str, Any]) -> None:
        model = choose_model(payload, self.config)
        payload["model"] = model
        stream_requested = bool(payload.get("stream", False))
        base = str(self.config["upstream_base_url"]).rstrip("/")
        style = str(self.config.get("upstream_type", "openai")).lower()
        if style == "anthropic":
            url = base + "/messages"
            forward = openai_to_anthropic(payload, model)
            status, body, _ = http_json(url, method="POST", data=forward,
                                        headers=upstream_headers(self.config),
                                        timeout=float(self.config.get("timeout", 600)))
            if status != 200:
                self.send_json(status, body); return
            text = anthropic_text(body)
            response = {
                "id": body.get("id", "chatcmpl-" + uuid.uuid4().hex),
                "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": body.get("usage", {}).get("input_tokens", 0),
                          "completion_tokens": body.get("usage", {}).get("output_tokens", 0)},
            }
            if stream_requested:
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                chunk = {"id": response["id"], "object": "chat.completion.chunk", "model": model,
                         "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
                done = {"id": response["id"], "object": "chat.completion.chunk", "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(("data: " + json.dumps(done) + "\n\ndata: [DONE]\n\n").encode("utf-8")); return
            self.send_json(200, response); return

        url = base + "/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **upstream_headers(self.config)}
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=float(self.config.get("timeout", 600))) as resp:
                ctype = resp.headers.get("Content-Type", "application/json")
                self.send_response(resp.status); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "close"); self.end_headers()
                while True:
                    chunk = resp.read(65536)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
        except urllib.error.HTTPError as exc:
            raw = exc.read(); self.send_response(exc.code); self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json")); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def proxy_responses(self, payload: dict[str, Any]) -> None:
        base = str(self.config["upstream_base_url"]).rstrip("/")
        payload["model"] = choose_model(payload, self.config)
        # Prefer native Responses API. If unavailable, translate basic text input.
        status, body, _ = http_json(base + "/responses", method="POST", data=payload,
                                    headers=upstream_headers(self.config), timeout=float(self.config.get("timeout", 600)))
        if status != 404:
            self.send_json(status, body)
            return
        raw_input = payload.get("input", "")
        if isinstance(raw_input, list):
            text = "\n".join(str(item.get("content", item)) if isinstance(item, dict) else str(item) for item in raw_input)
        else:
            text = str(raw_input)
        chat = {"model": payload.get("model", "ide-gateway"), "messages": [{"role": "user", "content": text}], "stream": False}
        status, response, _ = http_json(base + "/chat/completions", method="POST", data=chat,
                                        headers=upstream_headers(self.config), timeout=float(self.config.get("timeout", 600)))
        if status != 200:
            self.send_json(status, response)
            return
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content", ""))
        rid = "resp_" + uuid.uuid4().hex
        self.send_json(200, {"id": rid, "object": "response", "status": "completed", "model": chat["model"],
                             "output": [{"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": content}]}]})


def serve(config_path: Path) -> int:
    config = read_json(config_path)
    if not config:
        raise RuntimeError(f"missing config: {config_path}")
    server = ThreadingHTTPServer((str(config.get("host", "127.0.0.1")), int(config.get("port", 8787))), Handler)
    server.daemon_threads = True
    server.config = config  # type: ignore[attr-defined]
    server.serve_forever()
    return 0


def wait_health(port: int, timeout: int = 30) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"local gateway health failed: {last}")


def wait_public_ready(public_url: str, ide_key: str, expected_models: list[str], timeout: int = 120) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(public_url.rstrip("/") + "/health", timeout=5) as resp:
                health = json.loads(resp.read().decode("utf-8"))
            req = urllib.request.Request(
                public_url.rstrip("/") + "/v1/models",
                headers={"Authorization": f"Bearer {ide_key}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                model_body = json.loads(resp.read().decode("utf-8"))
            model_ids = [str(item.get("id")) for item in model_body.get("data", []) if isinstance(item, dict)]
            if health.get("ok") and (not expected_models or any(item in model_ids for item in expected_models)):
                return {"health": health, "models": model_ids}
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    raise RuntimeError(f"public IDE Gateway did not become ready at {public_url}: {last}")


def recv_frame(sock: socket.socket, timeout: float = 0) -> dict[str, Any] | None:
    data = bytearray(); deadline = time.monotonic() + timeout if timeout > 0 else None
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0: return None
            sock.settimeout(remaining)
        chunk = sock.recv(1)
        if not chunk: return None
        if chunk == b"\x00":
            return json.loads(bytes(data).decode("utf-8")) if data else None
        data.extend(chunk)
        if len(data) > 256: raise RuntimeError("bridge frame too large")


def send_frame(sock: socket.socket, message: dict[str, Any]) -> None:
    sock.sendall(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\x00")


def auth_handshake(sock: socket.socket, secret: str) -> None:
    challenge_msg = recv_frame(sock, 10)
    challenge_value = (challenge_msg or {}).get("Challenge")
    if not challenge_value:
        raise RuntimeError("bridge authentication challenge missing")
    challenge = uuid.UUID(str(challenge_value))
    hashed = hashlib.sha256(secret.encode("utf-8")).digest()
    tag = hmac.new(hashed, challenge.bytes, hashlib.sha256).hexdigest()
    send_frame(sock, {"Authenticate": tag})


def copy_both(local: socket.socket, remote: socket.socket) -> None:
    sockets = [local, remote]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 60)
            if not readable: continue
            for source in readable:
                data = source.recv(65536)
                if not data: return
                (remote if source is local else local).sendall(data)
    finally:
        for item in sockets:
            with contextlib.suppress(Exception): item.close()


def handle_connection(conn_id: str, host: str, port: int, local_port: int, secret: str = "", password: str = "") -> None:
    remote = socket.create_connection((host, port), timeout=10)
    if secret:
        auth_handshake(remote, secret)
    frame: dict[str, Any] = {"type": "accept", "connectionId": conn_id, "id": conn_id}
    if password:
        frame["password"] = password
    send_frame(remote, frame)
    print(f"bridge connection {conn_id}: accepted", flush=True)
    local = socket.create_connection(("127.0.0.1", local_port), timeout=10)
    copy_both(local, remote)


def bridge(host: str, port: int, hostname: str, local_port: int, secret: str = "", password: str = "", ready_file: str = "") -> int:
    while True:
        try:
            control = socket.create_connection((host, port), timeout=10)
            if secret:
                auth_handshake(control, secret)
            control.settimeout(None)
            hello_frame: dict[str, Any] = {"type": "hello", "hostname": hostname}
            if password:
                hello_frame["password"] = password
            send_frame(control, hello_frame)
            hello = recv_frame(control, 15)
            if not hello: raise RuntimeError("bridge handshake returned no response")
            if str(hello.get("type", "")).lower() == "error": raise RuntimeError(str(hello))
            print(f"bridge connected: {hello}", flush=True)
            if ready_file:
                write_json(Path(ready_file), {"ok": True, "connected_at": time.time(), "hello": hello, "hostname": hostname})
            while True:
                msg = recv_frame(control, 0)
                if not msg: raise RuntimeError("bridge control connection closed")
                kind = str(msg.get("type", "")).lower()
                print(f"bridge event: {msg}", flush=True)
                if kind == "connection":
                    conn_id = str(msg.get("connectionId") or msg.get("id") or "")
                    threading.Thread(target=handle_connection,args=(conn_id,host,port,local_port,secret,password),daemon=True).start()
                elif kind == "heartbeat": pass
                elif kind == "error": raise RuntimeError(str(msg))
        except KeyboardInterrupt: return 0
        except Exception as exc:
            print(f"bridge reconnect: {exc}", flush=True); time.sleep(5)


def cached_bridge_valid(previous: dict[str, Any], hostname: str) -> bool:
    if str(previous.get("hostname", "")).strip() != str(hostname or "").strip():
        return False
    if not all(str(previous.get(key, "")).strip() for key in ("public_url", "bridge_host", "control_port")):
        return False
    expires = str(previous.get("domain_expires_at", "")).strip()
    if not expires:
        return True
    try:
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expiry.tzinfo is None: expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > datetime.now(timezone.utc)
    except Exception:
        return True


def provision_public_bridge(config: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"localHost": "127.0.0.1", "localPort": int(config["port"])}
    if str(config.get("hostname", "")).strip():
        payload["hostname"] = str(config["hostname"]).strip()
    status_code, body, _ = http_json(
        "https://api.tunnellio.ru/v1/tcp-bridge/launch",
        method="POST", data=payload, timeout=30,
    )
    if status_code not in {200, 201}:
        raise RuntimeError(f"Tunnellio public bridge provisioning failed ({status_code}): {body}")
    data = body.get("data", body) if isinstance(body, dict) else {}
    profile = data.get("connectionProfile", data) if isinstance(data, dict) else {}
    tcp = profile.get("tcpBridge", {}) if isinstance(profile, dict) else {}
    domain = data.get("domain", {}) if isinstance(data, dict) else {}
    hostname = str(tcp.get("hostname") or profile.get("remoteHostname") or domain.get("hostname") or config.get("hostname") or "").strip()
    public_url = str(tcp.get("publicUrl") or profile.get("publicUrl") or domain.get("publicUrl") or "").strip()
    if not hostname or not public_url:
        raise RuntimeError(f"Tunnellio provisioning response missing hostname/publicUrl: {body}")
    config.update({
        "hostname": hostname,
        "public_url": public_url,
        "bridge_host": str(tcp.get("host") or "tunnellio.site"),
        "control_port": int(tcp.get("controlPort") or 7835),
        "bridge_secret": str(tcp.get("token") or ""),
        "bridge_password": str(config.get("bridge_password") or ""),
        "domain_expires_at": str(domain.get("expiresAt") or ""),
    })
    return config


def verify_upstream(config: dict[str, Any]) -> list[str]:
    base = str(config["upstream_base_url"]).rstrip("/")
    models = upstream_models(config)
    if not models:
        raise RuntimeError("no sandbox models discovered; pass --models or --default-model")
    # `/models` may be unavailable, so a configured model list is sufficient.
    return models


def public_status(config: dict[str, Any], state: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    public_url = str(state.get("public_url") or config.get("public_url") or "").rstrip("/")
    result: dict[str, Any] = {
        "ok": False, "public_url": public_url,
        "base_url": public_url + "/v1" if public_url else "",
        "server_alive": is_alive(state.get("server_pid")),
        "tunnel_alive": is_alive(state.get("tunnel_pid")),
        "bridge_connected": (HOME / "bridge-ready.json").is_file(),
        "models": split_models(str(config.get("models", ""))),
    }
    if not public_url:
        result["error"] = "public URL is not provisioned"; return result
    try:
        with urllib.request.urlopen(public_url + "/health", timeout=timeout) as resp:
            result["health"] = json.loads(resp.read().decode("utf-8"))
        req = urllib.request.Request(public_url + "/v1/models", headers={"Authorization": f"Bearer {config.get('ide_api_key', '')}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        result["public_models"] = [str(item.get("id")) for item in body.get("data", []) if isinstance(item, dict)]
        result["ok"] = bool(result.get("health", {}).get("ok"))
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["error"] = "edge route is still propagating" if exc.code == 404 else str(exc)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def install(args: argparse.Namespace) -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(INSTALLER).write_bytes(Path(__file__).read_bytes())
    if os.name != "nt":
        INSTALLER.chmod(0o700)

    previous_config = read_json(CONFIG)
    previous_state = read_json(STATE)
    keep_existing_tunnel = bool(
        is_alive(previous_state.get("tunnel_pid"))
        and str(previous_config.get("hostname", "")) == str(args.hostname or "")
    )
    config = {
        "host": "127.0.0.1",
        "port": args.port,
        "ide_api_key": args.ide_api_key,
        "upstream_base_url": normalize_base_url(args.upstream_base_url),
        "upstream_api_key": args.upstream_api_key,
        "upstream_auth_style": args.upstream_auth_style,
        "upstream_type": args.upstream_type,
        "default_model": args.default_model,
        "models": args.models,
        "timeout": args.timeout,
        "hostname": args.hostname,
        "public_url": f"https://{args.hostname}.tunnellio.site" if args.hostname else "",
    }
    models = verify_upstream(config)
    if not args.skip_tunnel:
        if cached_bridge_valid(previous_config, args.hostname):
            for key in ("public_url", "bridge_host", "control_port", "bridge_secret", "bridge_password", "domain_expires_at", "hostname"):
                if key in previous_config:
                    config[key] = previous_config[key]
        else:
            config = provision_public_bridge(config)
    if not config["default_model"] and models and models[0] != "ide-gateway":
        config["default_model"] = models[0]
    config["models"] = ",".join(models)
    write_json(CONFIG, config, secret=True)

    state = previous_state
    stop_pid(state.get("server_pid"))
    if not keep_existing_tunnel:
        stop_pid(state.get("tunnel_pid"))

    server_proc = detached([sys.executable, str(INSTALLER), "serve", "--config", str(CONFIG)], SERVER_LOG)
    health = wait_health(args.port, timeout=30)

    tunnel_pid = previous_state.get("tunnel_pid") if keep_existing_tunnel else None
    tunnel_status = "reused" if keep_existing_tunnel else "skipped"
    if not args.skip_tunnel and not keep_existing_tunnel:
        ready_file = HOME / "bridge-ready.json"
        ready_file.unlink(missing_ok=True)
        tunnel_command = [sys.executable, str(INSTALLER), "bridge",
                          "--host", str(config.get("bridge_host", args.bridge_host)),
                          "--control-port", str(config.get("control_port", args.control_port)),
                          "--hostname", str(config["hostname"]), "--local-port", str(args.port),
                          "--ready-file", str(ready_file)]
        if str(config.get("bridge_secret", "")):
            tunnel_command.extend(["--secret", str(config["bridge_secret"])])
        if str(config.get("bridge_password", "")):
            tunnel_command.extend(["--password", str(config["bridge_password"])])
        tunnel_proc = detached(tunnel_command, TUNNEL_LOG)
        tunnel_pid = tunnel_proc.pid
        write_json(STATE, {
            "server_pid": server_proc.pid, "tunnel_pid": tunnel_pid,
            "installed_at": time.time(), "status": "starting",
            "public_url": config["public_url"],
            "base_url": config["public_url"].rstrip("/") + "/v1",
            "models": models,
        })
        deadline = time.time() + 30
        while time.time() < deadline and not ready_file.is_file():
            if tunnel_proc.poll() is not None:
                raise RuntimeError(f"TCP bridge exited during startup; see {TUNNEL_LOG}")
            time.sleep(0.25)
        if not ready_file.is_file():
            raise RuntimeError(f"TCP bridge did not become ready; see {TUNNEL_LOG}")
        tunnel_status = "connected"
    if not args.skip_tunnel:
        try:
            public_check = wait_public_ready(config["public_url"], args.ide_api_key, models, timeout=args.public_wait_seconds)
        except Exception as exc:
            # Edge route propagation can take minutes even though the resident
            # server and TCP control bridge are healthy. Keep them alive and
            # expose an exact status command instead of reporting installation failure.
            public_check = {"ok": False, "status": "propagating", "error": str(exc)}
    else:
        public_check = {"ok": True, "health": health, "models": models}

    state = {
        "server_pid": server_proc.pid,
        "tunnel_pid": tunnel_pid,
        "installed_at": time.time(),
        "public_url": config["public_url"],
        "base_url": config["public_url"].rstrip("/") + "/v1" if config["public_url"] else "",
        "models": models,
        "status": "ready" if public_check.get("ok", True) else "propagating",
        "tunnel_reused": keep_existing_tunnel,
    }
    write_json(STATE, state)
    print(json.dumps({
        "ok": True,
        "status": state["status"],
        "server": "running",
        "server_pid": server_proc.pid,
        "tunnel": tunnel_status,
        "tunnel_pid": tunnel_pid,
        "public_url": state["public_url"],
        "base_url": state["base_url"],
        "api_key": args.ide_api_key,
        "models": models,
        "health": health,
        "public_check": public_check,
        "persistence": "detached server + reconnecting pure-Python TCP bridge; rerun install to repair",
    }, ensure_ascii=False))
    return 0


def stop() -> int:
    state = read_json(STATE)
    stop_pid(state.get("tunnel_pid"))
    stop_pid(state.get("server_pid"))
    if os.name == "nt":
        # A dead detached child may still be releasing its inherited log handle.
        time.sleep(2.0)
    state["stopped_at"] = time.time()
    state["server_pid"] = None
    state["tunnel_pid"] = None
    write_json(STATE, state)
    print(json.dumps({"ok": True, "status": "stopped"}))
    return 0


def status() -> int:
    state = read_json(STATE)
    config = read_json(CONFIG)
    result = public_status(config, state)
    result["installed"] = bool(state)
    result["status"] = "ready" if result.get("ok") else str(state.get("status", "unknown"))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if state else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="IDE Gateway sandbox installer")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("install")
    setup.add_argument("--upstream-base-url", required=True)
    setup.add_argument("--upstream-api-key", default="")
    setup.add_argument("--upstream-auth-style", choices=["bearer", "x-api-key"], default="bearer")
    setup.add_argument("--upstream-type", choices=["openai", "anthropic"], default="openai")
    setup.add_argument("--default-model", default="")
    setup.add_argument("--models", default="")
    setup.add_argument("--ide-api-key", required=True)
    setup.add_argument("--hostname", required=True)
    setup.add_argument("--port", type=int, default=8787)
    setup.add_argument("--runtime-name", default="ide-gateway-sandbox")
    setup.add_argument("--timeout", type=int, default=600)
    setup.add_argument("--skip-tunnel", action="store_true")
    setup.add_argument("--bridge-host", default="tunnellio.site")
    setup.add_argument("--control-port", type=int, default=7835)
    setup.add_argument("--public-wait-seconds", type=int, default=30)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--config", default=str(CONFIG))
    bridge_parser = sub.add_parser("bridge")
    bridge_parser.add_argument("--host", default="tunnellio.site")
    bridge_parser.add_argument("--control-port", type=int, default=7835)
    bridge_parser.add_argument("--hostname", required=True)
    bridge_parser.add_argument("--local-port", type=int, default=8787)
    bridge_parser.add_argument("--secret", default="")
    bridge_parser.add_argument("--password", default="")
    bridge_parser.add_argument("--ready-file", default="")
    sub.add_parser("status")
    sub.add_parser("stop")
    args = parser.parse_args()
    if args.command == "install":
        return install(args)
    if args.command == "serve":
        return serve(Path(args.config))
    if args.command == "bridge":
        return bridge(args.host, args.control_port, args.hostname, args.local_port, args.secret, args.password, args.ready_file)
    if args.command == "stop":
        return stop()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
