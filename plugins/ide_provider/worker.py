from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.ide_provider.openai_compat import (
    chat_completion_response,
    models_response,
    openai_error,
    validate_chat_request,
)
from plugins.ide_provider.queue import (
    enqueue_request,
    request_counts,
    wait_for_completion,
)
from plugins.ide_provider.security import check_auth, redact_line

import argparse
import contextlib
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


class _NoReuseHTTPServer(ThreadingHTTPServer):
    # ThreadingHTTPServer (via HTTPServer/TCPServer) sets allow_reuse_address = 1.
    # On Windows, SO_REUSEADDR permits multiple sockets to bind the SAME active
    # port (unlike POSIX TIME_WAIT reuse). For an ide_provider endpoint a
    # duplicate bind of an in-use port must fail loudly so a second worker
    # exits instead of silently stealing traffic.
    allow_reuse_address = False
    daemon_threads = True


STATE: dict[str, Any] = {}
STATE_PATH: str = ""


def load_state(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def current_token() -> str:
    try:
        state = load_state(STATE_PATH)
        return str(state.get("token", ""))
    except Exception:
        return str(STATE.get("token", ""))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        try:
            line = format % args
            redacted = redact_line(line)
            log_path = STATE.get("log_path")
            if not log_path and STATE_PATH:
                log_path = str(Path(STATE_PATH).parent.parent / "logs" / f"{STATE.get('name', 'default')}.log")
            if log_path:
                with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(f"{redacted}\n")
        except Exception:
            pass

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        token = current_token()
        auth = self.headers.get("Authorization", "")
        api_key = self.headers.get("X-API-Key", "")
        return check_auth({"Authorization": auth, "X-API-Key": api_key}, token)

    def _read_limited_body(self, max_bytes: int) -> bytes:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return b""
        try:
            length = int(length_header)
        except ValueError:
            return b""
        if length > max_bytes:
            raise ValueError("payload too large")
        return self.rfile.read(length)

    def _health(self) -> dict[str, Any]:
        runtime = Path(STATE_PATH).parent.parent
        counts = request_counts(runtime, STATE.get("name", "default"))
        return {
            "ok": True,
            "name": STATE.get("name", "default"),
            "status": STATE.get("status", "running"),
            "model_attached": counts.get("claimed", 0) > 0,
            "pending_requests": counts.get("pending", 0),
        }

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(200, self._health())
            return
        if path == "/v1/models":
            if not self._authorized():
                self._send_json(401, openai_error("Unauthorized", "unauthorized"))
                return
            self._send_json(200, models_response(STATE.get("model_id", "ide-provider")))
            return
        self._send_json(404, openai_error("Not found", "not_found"))

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/v1/chat/completions":
            self._handle_chat_completions()
            return
        self._send_json(404, openai_error("Not found", "not_found"))

    def _handle_chat_completions(self) -> None:
        try:
            self._do_chat_completions()
        except Exception as exc:  # noqa: BLE001
            try:
                self.log_message("chat completions error: %s", redact_line(str(exc)))
            except Exception:
                pass
            try:
                self._send_json(500, openai_error("Internal error", "internal_error"))
            except Exception:
                pass

    def _do_chat_completions(self) -> None:
        if not self._authorized():
            self._send_json(401, openai_error("Unauthorized", "unauthorized"))
            return

        max_request_bytes = int(STATE.get("max_request_bytes", 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, openai_error("Payload too large", "payload_too_large"))
            return

        if not body:
            self._send_json(400, openai_error("Missing or oversized body", "bad_request"))
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, openai_error(f"Invalid JSON: {exc}", "bad_request"))
            return

        try:
            validated = validate_chat_request(payload)
        except ValueError as exc:
            self._send_json(400, openai_error(str(exc), "bad_request"))
            return

        if validated["stream"]:
            self._send_json(
                400,
                openai_error(
                    "Streaming is not supported by ide_provider MVP.",
                    "streaming_not_supported",
                ),
            )
            return

        runtime = Path(STATE_PATH).parent.parent
        endpoint = STATE.get("name", "default")
        request_timeout = int(STATE.get("request_timeout_seconds", 300))
        max_pending = int(STATE.get("max_pending_requests", 8))

        try:
            request_id = enqueue_request(
                runtime,
                endpoint,
                {
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "model": validated["model"],
                    "messages": validated["messages"],
                    "temperature": validated.get("temperature"),
                    "top_p": validated.get("top_p"),
                    "max_tokens": validated.get("max_tokens"),
                    "stream": validated["stream"],
                    "raw_request": payload,
                },
                timeout_seconds=request_timeout,
                max_pending=max_pending,
            )
        except ValueError as exc:
            if "queue full" in str(exc).lower():
                self._send_json(409, openai_error("Queue full", "queue_full"))
                return
            self._send_json(500, openai_error(str(exc), "internal_error"))
            return

        result = wait_for_completion(runtime, endpoint, request_id, request_timeout)
        if result["status"] == "completed":
            req = result["request"]
            content = req["response"]["content"]
            finish = req["response"].get("finish_reason", "stop")
            self._send_json(
                200,
                chat_completion_response(
                    request_id,
                    STATE.get("model_id", "ide-provider"),
                    content,
                    finish,
                ),
            )
            return
        if result["status"] == "failed":
            req = result["request"]
            error = req.get("error") or {}
            self._send_json(
                500,
                openai_error(
                    error.get("message", "Request failed"),
                    error.get("code", "request_failed"),
                ),
            )
            return

        self._send_json(504, openai_error("Timed out waiting for MCP model", "model_timeout"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    args = parser.parse_args()

    global STATE, STATE_PATH
    STATE_PATH = args.state
    STATE = load_state(STATE_PATH)
    STATE["status"] = "running"
    save_state(STATE_PATH, STATE)

    host = STATE.get("host", "127.0.0.1")
    if host == "localhost":
        host = "127.0.0.1"
    if host not in {"127.0.0.1"}:
        STATE["status"] = "failed"
        STATE["last_error"] = f"refusing to bind non-loopback host: {host!r}"
        with contextlib.suppress(Exception):
            save_state(STATE_PATH, STATE)
        raise SystemExit(STATE["last_error"])

    port = int(STATE.get("port", 0))
    if not port:
        STATE["status"] = "failed"
        STATE["last_error"] = "port is required"
        with contextlib.suppress(Exception):
            save_state(STATE_PATH, STATE)
        raise SystemExit(STATE["last_error"])

    _install_excepthook(STATE.get("log_path"))
    server = None
    try:
        server = _NoReuseHTTPServer((host, port), Handler)
    except OSError as exc:
        STATE["status"] = "failed"
        STATE["last_error"] = f"bind failed: {exc}"
        with contextlib.suppress(Exception):
            save_state(STATE_PATH, STATE)
        raise SystemExit(STATE["last_error"]) from exc
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE["status"] = "stopped"
        with contextlib.suppress(Exception):
            save_state(STATE_PATH, STATE)


def _install_excepthook(log_path: str | None) -> None:
    def _hook(args: Any) -> None:
        try:
            buf = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            redacted = redact_line(buf)
            path = log_path
            if not path and STATE_PATH:
                path = str(Path(STATE_PATH).parent.parent / "logs" / f"{STATE.get('name', 'default')}.log")
            if path:
                with open(path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(redacted + "\n")
        except Exception:
            pass

    threading.excepthook = _hook  # type: ignore[assignment]


if __name__ == "__main__":
    main()
