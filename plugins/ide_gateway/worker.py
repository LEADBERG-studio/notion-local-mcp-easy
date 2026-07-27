"""IDE Gateway worker: a stdlib HTTP server exposing the full OpenAI-compatible
surface and bridging requests to the active MCP model through the file queue.

Ported from hyperagent-openai-gateway app.py + ide_provider worker.py. The
translation layer (flatten_messages, build_chat_completion, tool bridge, media
URL extraction, local fallbacks) is reused verbatim; only the upstream is
replaced — instead of polling Hyperagent MCP, we enqueue an OpenAI-shaped
request and wait for the active MCP model to answer via the queue.
"""
from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.ide_gateway.openai_schemas import (
    validate_chat_request,
    validate_completion_request,
    validate_responses_request,
    validate_embedding_request,
    validate_moderation_request,
    validate_images_request,
    validate_speech_request,
    validate_list_models_request,
)
from plugins.ide_gateway.openai_translate import (
    build_chat_completion,
    build_completion_response,
    build_response_object,
    flatten_messages,
    latest_user_text,
    model_object,
)
from plugins.ide_gateway.openai_fallbacks import (
    embeddings_response,
    normalize_inputs,
    moderations_response,
)
from plugins.ide_gateway.openai_media import (
    PUBLISH_HINT,
    extract_urls,
    extract_artifacts,
    first_url,
    images_response,
    transcription_response,
    speech_url_response,
)
from plugins.ide_gateway.openai_toolbridge import (
    catalog,
    build_exec_directive,
    directive_from_tool_choice,
    extract_tool_args,
    forced_tool_name,
    primary_param,
)
from plugins.ide_gateway.openai_streaming import (
    render_chat_stream_events,
    render_responses_stream_events,
)
from plugins.ide_gateway.queue import (
    enqueue_request,
    request_counts,
    request_path,
    wait_for_completion,
    get_response as registry_get_response,
    put_response as registry_put_response,
    put_file,
    get_file,
    get_file_content,
    list_files,
    delete_file,
)
from plugins.ide_gateway.security import check_auth, redact_line

import argparse
import contextlib
import json
import os
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


class _NoReuseHTTPServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR permits duplicate binds of an active port; for an
    # ide_gateway endpoint a duplicate bind must fail loudly.
    allow_reuse_address = False
    daemon_threads = True


STATE: dict[str, Any] = {}
STATE_PATH: str = ""


def load_state(path: str) -> dict[str, Any]:
    # utf-8-sig tolerates an accidental BOM (e.g. state written by an editor).
    with open(path, "r", encoding="utf-8-sig") as f:
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


def _runtime_root() -> Path:
    return Path(STATE_PATH).parent.parent


def _openai_error(message: str, code: str, type_: str = "ide_gateway_error",
                  status: int = 400) -> dict[str, Any]:
    return {"error": {"message": message, "type": type_, "code": code}, "_status": status}


def _json_response(status: int, body: dict[str, Any]) -> tuple[int, str, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return status, "application/json; charset=utf-8", data


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

    def _send_bytes(self, status: int, content_type: str, data: bytes,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _write_sse(self, line: bytes) -> None:
        self.wfile.write(line)
        with contextlib.suppress(Exception):
            self.wfile.flush()

    def _write_sse_event(self, event: dict[str, Any] | str) -> None:
        if isinstance(event, str):
            self._write_sse(f"data: {event}\n\n".encode("utf-8"))
        else:
            self._write_sse(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))

    def _write_sse_comment(self, comment: str = "keepalive") -> None:
        self._write_sse(f": {comment}\n\n".encode("utf-8"))

    def _begin_sse(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

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
        runtime = _runtime_root()
        counts = request_counts(runtime, STATE.get("name", "default"))
        return {
            "ok": True,
            "name": STATE.get("name", "default"),
            "status": STATE.get("status", "running"),
            "model_attached": counts.get("claimed", 0) > 0,
            "pending_requests": counts.get("pending", 0),
        }

    # --------------------------- routing ---------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(200, self._health())
            return
        if path in ("/v1/models", "/models"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            self._send_json(200, self._models_response())
            return
        if path in ("/v1/tools", "/tools"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            disabled = {t.strip() for t in str(STATE.get("disabled_tools", "")).split(",") if t.strip()}
            self._send_json(200, {"object": "list", "data": catalog(disabled=disabled)})
            return
        if path in ("/v1/files", "/files"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            self._send_json(200, {"object": "list", "data": [self._file_object(f) for f in list_files(_runtime_root())]})
            return
        # /v1/files/{id}/content must be checked before /v1/files/{id}
        if (path.startswith("/v1/files/") or path.startswith("/files/")) and path.endswith("/content"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            fid = path.rstrip("/").rsplit("/", 2)[-2]
            if not fid:
                self._send_json(404, _openai_error("Not found", "not_found", status=404))
                return
            content = get_file_content(_runtime_root(), fid)
            if content is None:
                self._send_json(404, _openai_error(f"Content for file '{fid}' is not retained.", "not_found", status=404))
                return
            self._send_bytes(200, "application/octet-stream", content)
            return
        if path.startswith("/v1/files/") or path.startswith("/files/"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            fid = path.rsplit("/", 1)[-1]
            if not fid:
                self._send_json(404, _openai_error("Not found", "not_found", status=404))
                return
            rec = get_file(_runtime_root(), fid)
            if not rec:
                self._send_json(404, _openai_error(f"File '{fid}' not found.", "not_found", status=404))
                return
            self._send_json(200, self._file_object(rec))
            return
        if path.startswith("/v1/responses/") or path.startswith("/responses/"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            self._handle_response_get(path)
            return
        self._send_json(404, _openai_error("Not found", "not_found", status=404))

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path.startswith("/v1/files/") or path.startswith("/files/"):
            if not self._authorized():
                self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
                return
            fid = path.rsplit("/", 1)[-1]
            ok = delete_file(_runtime_root(), fid)
            if not ok:
                self._send_json(404, _openai_error(f"File '{fid}' not found.", "not_found", status=404))
                return
            self._send_json(200, {"id": fid, "object": "file", "deleted": True})
            return
        self._send_json(404, _openai_error("Not found", "not_found", status=404))

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path in ("/v1/chat/completions", "/chat/completions"):
                self._handle_chat_completions()
                return
            if path in ("/v1/completions", "/completions"):
                self._handle_completions()
                return
            if path in ("/v1/responses", "/responses"):
                self._handle_responses()
                return
            if path in ("/v1/images/generations", "/images/generations"):
                self._handle_images_generations()
                return
            if path in ("/v1/images/edits", "/images/edits"):
                self._handle_images_edits()
                return
            if path in ("/v1/audio/speech", "/audio/speech"):
                self._handle_audio_speech()
                return
            if path in ("/v1/audio/transcriptions", "/audio/transcriptions"):
                self._handle_audio_transcription(translate=False)
                return
            if path in ("/v1/audio/translations", "/audio/translations"):
                self._handle_audio_transcription(translate=True)
                return
            if path in ("/v1/embeddings", "/embeddings"):
                self._handle_embeddings()
                return
            if path in ("/v1/moderations", "/moderations"):
                self._handle_moderations()
                return
            if path in ("/v1/files", "/files"):
                self._handle_file_upload()
                return
            self._send_json(404, _openai_error("Not found", "not_found", status=404))
        except Exception as exc:  # noqa: BLE001
            try:
                self.log_message("POST %s error: %s", path, redact_line(str(exc)))
            except Exception:
                pass
            try:
                self._send_json(500, _openai_error("Internal error", "internal_error", status=500))
            except Exception:
                pass

    # --------------------------- handlers --------------------------------- #
    def _models_response(self) -> dict[str, Any]:
        model_id = STATE.get("model_id", "ide-gateway")
        return {"object": "list", "data": [model_object(model_id, name=model_id, description="Active MCP model")]}

    def _file_object(self, rec: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": rec.get("id", ""), "object": "file",
            "bytes": rec.get("bytes", 0), "created_at": rec.get("created", 0),
            "filename": rec.get("filename", ""), "purpose": rec.get("purpose", "assistants"),
        }

    def _enqueue_and_wait(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Enqueue a model-served request and wait for the model's reply."""
        runtime = _runtime_root()
        endpoint = STATE.get("name", "default")
        request_timeout = int(STATE.get("request_timeout_seconds", 300))
        max_pending = int(STATE.get("max_pending_requests", 8))
        try:
            request_id = enqueue_request(
                runtime, endpoint, request_data,
                timeout_seconds=request_timeout, max_pending=max_pending,
            )
        except ValueError as exc:
            if "queue full" in str(exc).lower():
                return _openai_error("Queue full", "queue_full", status=409)
            return _openai_error(str(exc), "internal_error", status=500)
        result = wait_for_completion(runtime, endpoint, request_id, request_timeout)
        if result["status"] == "completed":
            return {"status": "completed", "request": result["request"]}
        if result["status"] == "failed":
            req = result.get("request") or {}
            err = req.get("error") or {}
            return _openai_error(
                err.get("message", "Request failed"),
                err.get("code", "request_failed"),
                status=500,
            )
        return _openai_error("Timed out waiting for MCP model", "model_timeout", status=504)

    def _handle_chat_completions(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing or oversized body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, _openai_error(f"Invalid JSON: {exc}", "bad_request", status=400))
            return
        try:
            validated = validate_chat_request(payload)
        except ValueError as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return

        model_id = STATE.get("model_id", "ide-gateway")
        forced = forced_tool_name(validated["tool_choice"])
        # Mode C: client forced a canonical tool with tool_choice.
        if forced:
            args = extract_tool_args(validated["messages"], forced)
            if STATE.get("exec_mode", "roundtrip") == "auto":
                # Ask the model to run the tool and return its raw result.
                request_data = {
                    "kind": "chat", "path": "/v1/chat/completions",
                    "model": validated["model"], "messages": validated["messages"],
                    "stream": validated["stream"], "raw_request": payload,
                    "prompt": build_exec_directive(forced, args),
                }
                if validated["stream"]:
                    self._stream_chat_via_queue(request_data, model_id, validated)
                    return
                result = self._enqueue_and_wait(request_data)
                if "error" in result:
                    self._send_json(result["_status"], result)
                    return
                content = str((result["request"].get("response") or {}).get("content") or "")
                self._send_json(200, build_chat_completion(model_id, content))
                return
            # roundtrip mode: return a tool_call completion without running it.
            self._send_json(200, self._tool_call_completion(model_id, forced, args))
            return

        # Normal: flatten messages + apply directive from tool_choice (Mode B).
        prompt = flatten_messages(validated["messages"])
        prompt += directive_from_tool_choice(validated["tool_choice"], validated["tools"])

        request_data = {
            "kind": "chat", "path": "/v1/chat/completions",
            "model": validated["model"], "messages": validated["messages"],
            "temperature": validated.get("temperature"),
            "top_p": validated.get("top_p"),
            "max_tokens": validated.get("max_tokens"),
            "stream": validated["stream"],
            "stream_options": validated.get("stream_options"),
            "tools": validated.get("tools"),
            "tool_choice": validated.get("tool_choice"),
            "raw_request": payload,
            "prompt": prompt,
        }
        if validated["stream"]:
            self._stream_chat_via_queue(request_data, model_id, validated)
            return

        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            self._send_json(result["_status"], result)
            return
        content = str((result["request"].get("response") or {}).get("content") or "")
        finish = (result["request"].get("response") or {}).get("finish_reason") or "stop"
        self._send_json(200, build_chat_completion(model_id, content, finish=finish))

    def _stream_chat_via_queue(self, request_data: dict[str, Any], model_id: str,
                               validated: dict[str, Any]) -> None:
        runtime = _runtime_root()
        endpoint = STATE.get("name", "default")
        request_timeout = int(STATE.get("request_timeout_seconds", 300))
        max_pending = int(STATE.get("max_pending_requests", 8))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]

        try:
            request_id = enqueue_request(
                runtime, endpoint, request_data,
                timeout_seconds=request_timeout, max_pending=max_pending,
            )
        except ValueError as exc:
            self._send_json(409 if "queue full" in str(exc).lower() else 500,
                            _openai_error(str(exc), "queue_full" if "queue full" in str(exc).lower() else "internal_error",
                                          status=409 if "queue full" in str(exc).lower() else 500))
            return

        opts = validated.get("stream_options") or {}
        include_usage = bool(opts.get("include_usage"))

        self._begin_sse(200)
        self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                               "created": int(time.time()), "model": model_id,
                               "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        self._write_sse_comment("queued")

        deadline = time.time() + request_timeout
        next_keepalive = time.time() + 5.0
        while time.time() < deadline:
            try:
                req = json.loads(request_path(runtime, endpoint, request_id).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                req = None
            if req and req.get("status") == "completed":
                events = render_chat_stream_events(
                    cid=cid, model=model_id, req=req, include_usage=include_usage,
                )
                # Drop the leading role delta (already emitted), then explicitly terminate stream.
                stream_events = events[1:-1] if events and events[-1] == "data: [DONE]\n\n" else events[1:]
                for ev in stream_events:
                    self._write_sse(ev.encode("utf-8"))
                self._write_sse("data: [DONE]\n\n".encode("utf-8"))
                return
            if req and req.get("status") == "failed":
                err = req.get("error") or {}
                msg = str(err.get("message", "Request failed"))
                self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                                       "created": int(time.time()), "model": model_id,
                                       "choices": [{"index": 0, "delta": {"content": msg}, "finish_reason": None}]})
                self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                                       "created": int(time.time()), "model": model_id,
                                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                self._write_sse("data: [DONE]\n\n".encode("utf-8"))
                return
            if req and req.get("status") == "expired":
                self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                                       "created": int(time.time()), "model": model_id,
                                       "choices": [{"index": 0, "delta": {"content": "Timed out waiting for MCP model"}, "finish_reason": None}]})
                self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                                       "created": int(time.time()), "model": model_id,
                                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                self._write_sse("data: [DONE]\n\n".encode("utf-8"))
                return
            now = time.time()
            if now >= next_keepalive:
                self._write_sse_comment("waiting")
                next_keepalive = now + 5.0
            time.sleep(0.25)

        self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                               "created": int(time.time()), "model": model_id,
                               "choices": [{"index": 0, "delta": {"content": "Timed out waiting for MCP model"}, "finish_reason": None}]})
        self._write_sse_event({"id": cid, "object": "chat.completion.chunk",
                               "created": int(time.time()), "model": model_id,
                               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self._write_sse("data: [DONE]\n\n".encode("utf-8"))

    def _tool_call_completion(self, model: str, tool_name: str, arguments: dict) -> dict[str, Any]:
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "system_fingerprint": "ide-gateway-toolrunner",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(arguments)}}]},
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _handle_completions(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_completion_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return
        request_data = {
            "kind": "completion", "path": "/v1/completions",
            "model": validated["model"], "prompt": validated["prompt"],
            "stream": validated["stream"], "raw_request": payload,
        }
        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            self._send_json(result["_status"], result)
            return
        text = str((result["request"].get("response") or {}).get("content") or "")
        self._send_json(200, build_completion_response(validated["model"], text))

    def _handle_responses(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_responses_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return

        # Build a self-contained prompt.
        if isinstance(validated["input"], str):
            prompt = validated["input"]
        elif isinstance(validated["input"], list):
            prompt = flatten_messages(validated["input"])
        else:
            prompt = ""
        if validated["instructions"]:
            prompt = f"{validated['instructions']}\n\n{prompt}"
        prompt += directive_from_tool_choice(validated["tool_choice"], validated["tools"])

        # Stateful chain: reconstruct prior context from previous_response_id.
        if validated["previous_response_id"]:
            rec = registry_get_response(_runtime_root(), validated["previous_response_id"])
            # Prior transcript would normally come from the model; we only have
            # the registry metadata, so we pass the id as context hint.
            if rec:
                prompt = f"[Prior response {validated['previous_response_id']} (model: {rec.get('model', '')})]\n\nNew request:\n{prompt}"

        rid = "resp_" + uuid.uuid4().hex[:24]
        model_id = STATE.get("model_id", "ide-gateway")
        registry_put_response(_runtime_root(), rid, validated["model"], "in_progress",
                              {"metadata": validated.get("metadata")})

        request_data = {
            "kind": "responses", "path": "/v1/responses",
            "model": validated["model"], "prompt": prompt,
            "instructions": validated["instructions"],
            "stream": validated["stream"],
            "background": validated["background"],
            "previous_response_id": validated["previous_response_id"],
            "tools": validated.get("tools"),
            "tool_choice": validated.get("tool_choice"),
            "raw_request": payload,
        }

        if validated["stream"]:
            self._stream_responses_via_queue(request_data, rid, model_id)
            return

        if validated["background"]:
            # Enqueue without waiting; return in_progress immediately.
            runtime = _runtime_root()
            endpoint = STATE.get("name", "default")
            request_timeout = int(STATE.get("request_timeout_seconds", 300))
            max_pending = int(STATE.get("max_pending_requests", 8))
            try:
                enqueue_request(runtime, endpoint, request_data,
                                timeout_seconds=request_timeout, max_pending=max_pending)
            except ValueError as exc:
                self._send_json(409 if "queue full" in str(exc).lower() else 500,
                                _openai_error(str(exc), "queue_full" if "queue full" in str(exc).lower() else "internal_error",
                                              status=409 if "queue full" in str(exc).lower() else 500))
                return
            self._send_json(200, build_response_object(rid, model_id, "in_progress", ""))
            return

        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            registry_put_response(_runtime_root(), rid, validated["model"], "failed", {})
            self._send_json(result["_status"], result)
            return
        content = str((result["request"].get("response") or {}).get("content") or "")
        registry_put_response(_runtime_root(), rid, validated["model"], "completed", {})
        self._send_json(200, build_response_object(rid, model_id, "completed", content))

    def _stream_responses_via_queue(self, request_data: dict[str, Any], rid: str,
                                    model_id: str) -> None:
        runtime = _runtime_root()
        endpoint = STATE.get("name", "default")
        request_timeout = int(STATE.get("request_timeout_seconds", 300))
        max_pending = int(STATE.get("max_pending_requests", 8))
        try:
            request_id = enqueue_request(
                runtime, endpoint, request_data,
                timeout_seconds=request_timeout, max_pending=max_pending,
            )
        except ValueError as exc:
            self._send_json(409 if "queue full" in str(exc).lower() else 500,
                            _openai_error(str(exc), "queue_full" if "queue full" in str(exc).lower() else "internal_error",
                                          status=409 if "queue full" in str(exc).lower() else 500))
            return

        self._begin_sse(200)
        self._write_sse_event({"type": "response.created", "response": {
            "id": rid, "object": "response", "model": model_id,
            "created_at": int(time.time()), "status": "in_progress"}})
        self._write_sse_comment("queued")

        deadline = time.time() + request_timeout
        next_keepalive = time.time() + 5.0
        while time.time() < deadline:
            try:
                req = json.loads(request_path(runtime, endpoint, request_id).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                req = None
            if req and req.get("status") == "completed":
                events = render_responses_stream_events(rid=rid, model=model_id, req=req)
                # Drop the leading response.created (already emitted), then explicitly terminate stream.
                stream_events = events[1:-1] if events and events[-1] == "data: [DONE]\n\n" else events[1:]
                for ev in stream_events:
                    self._write_sse(ev.encode("utf-8"))
                registry_put_response(_runtime_root(), rid, request_data["model"], "completed", {})
                self._write_sse("data: [DONE]\n\n".encode("utf-8"))
                return
            if req and req.get("status") in ("failed", "expired"):
                msg = "Timed out waiting for MCP model" if req.get("status") == "expired" else \
                      str((req.get("error") or {}).get("message", "Request failed"))
                self._write_sse_event({"type": "response.output_text.delta", "response_id": rid,
                                        "output_index": 0, "content_index": 0, "sequence_number": 1, "delta": msg})
                self._write_sse_event({"type": "response.completed", "response": {
                    "id": rid, "object": "response", "model": model_id,
                    "created_at": int(time.time()), "status": "completed",
                    "output_text": msg, "output": [{
                        "type": "message", "id": "msg_" + uuid.uuid4().hex[:20],
                        "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": msg, "annotations": []}]}]}})
                self._write_sse("data: [DONE]\n\n".encode("utf-8"))
                return
            now = time.time()
            if now >= next_keepalive:
                self._write_sse_comment("waiting")
                next_keepalive = now + 5.0
            time.sleep(0.25)

        self._write_sse_event({"type": "response.completed", "response": {
            "id": rid, "object": "response", "model": model_id,
            "created_at": int(time.time()), "status": "completed", "output_text": "Timed out"}})
        self._write_sse("data: [DONE]\n\n".encode("utf-8"))

    def _handle_response_get(self, path: str) -> None:
        # /v1/responses/{rid} [, /cancel, /input_items]
        parts = path.rstrip("/").split("/")
        rid = parts[-2] if parts[-1] in ("cancel", "input_items") else parts[-1]
        rec = registry_get_response(_runtime_root(), rid)
        if not rec:
            self._send_json(404, _openai_error(f"Response '{rid}' not found.", "not_found", status=404))
            return
        if parts[-1] == "cancel":
            registry_put_response(_runtime_root(), rid, rec.get("model", ""), "cancelled", rec.get("meta"))
            self._send_json(200, build_response_object(rid, rec.get("model", ""), "cancelled", ""))
            return
        if parts[-1] == "input_items":
            self._send_json(200, {"object": "list", "data": [], "has_more": False})
            return
        status = rec.get("status", "completed")
        self._send_json(200, build_response_object(rid, rec.get("model", ""), status, ""))

    def _handle_images_generations(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_images_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return
        prompt = f"[Directive] Generate an image: {validated['prompt']}. {PUBLISH_HINT}"
        request_data = {
            "kind": "images", "path": "/v1/images/generations",
            "model": validated["model"] or STATE.get("model_id", "ide-gateway"),
            "prompt_text": prompt, "n": validated["n"],
            "raw_request": payload,
        }
        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            self._send_json(result["_status"], result)
            return
        content = str((result["request"].get("response") or {}).get("content") or "")
        # Model may return either a URL in content or a structured payload.
        payload_resp = (result["request"].get("response") or {}).get("payload")
        if isinstance(payload_resp, dict) and "data" in payload_resp:
            self._send_json(200, payload_resp)
            return
        urls = extract_urls(content)
        if urls:
            self._send_json(200, images_response(urls, revised_prompt=validated["prompt"], n=validated["n"]))
            return
        arts = extract_artifacts(content)
        hint = (f" (agent returned artifact {arts[0]}, not externally fetchable; the "
                "publish-public directive should yield a URL)" if arts else "")
        self._send_json(502, _openai_error(f"Upstream returned no fetchable image URL{hint}.",
                                            "no_artifact", status=502))

    def _handle_images_edits(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        ctype = self.headers.get("Content-Type", "")
        prompt = ""
        model = ""
        if ctype.startswith("multipart/"):
            body = self._read_limited_body(int(STATE.get("max_request_bytes", 4 * 1024 * 1024)))
            prompt, model, _ = _parse_multipart_simple(body, ctype)
        else:
            max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
            try:
                body = self._read_limited_body(max_request_bytes)
            except ValueError:
                self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
                return
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    prompt = str(payload.get("prompt", ""))
                    model = str(payload.get("model", ""))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        directive = f"[Directive] Edit/generate an image: {prompt}. {PUBLISH_HINT}"
        request_data = {
            "kind": "images", "path": "/v1/images/edits",
            "model": model or STATE.get("model_id", "ide-gateway"),
            "prompt_text": directive, "n": 1, "raw_request": {"prompt": prompt},
        }
        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            self._send_json(result["_status"], result)
            return
        content = str((result["request"].get("response") or {}).get("content") or "")
        urls = extract_urls(content)
        if urls:
            self._send_json(200, images_response(urls, revised_prompt=prompt, n=1))
            return
        self._send_json(502, _openai_error("Upstream returned no fetchable image URL.",
                                           "no_artifact", status=502))

    def _handle_audio_speech(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_speech_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return
        voice = f" using voice '{validated['voice']}'" if validated["voice"] else ""
        directive = (f"[Directive] Generate audio (text-to-speech){voice}: "
                     f"{validated['input']}. {PUBLISH_HINT}")
        request_data = {
            "kind": "audio", "path": "/v1/audio/speech",
            "model": validated["model"] or STATE.get("model_id", "ide-gateway"),
            "prompt_text": directive, "voice": validated["voice"],
            "response_format": validated["response_format"],
            "raw_request": payload,
        }
        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            self._send_json(result["_status"], result)
            return
        content = str((result["request"].get("response") or {}).get("content") or "")
        url = first_url(content)
        if not url:
            arts = extract_artifacts(content)
            hint = (f" (agent returned artifact {arts[0]}, not externally fetchable)" if arts else "")
            self._send_json(502, _openai_error(f"Upstream returned no fetchable audio URL{hint}.",
                                                "no_artifact", status=502))
            return
        # Try to fetch the audio bytes; fall back to URL-in-JSON.
        try:
            import urllib.request as _urllib
            with _urllib.urlopen(url, timeout=30) as r:
                audio_bytes = r.read()
            fmt = (validated["response_format"] or "mp3").lower()
            ctype = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus",
                     "aac": "audio/aac", "flac": "audio/flac", "pcm": "audio/L16"}.get(fmt, "application/octet-stream")
            self._send_bytes(200, ctype, audio_bytes)
        except Exception:
            self._send_json(200, speech_url_response(url, validated["model"] or STATE.get("model_id", "ide-gateway")))

    def _handle_audio_transcription(self, translate: bool) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/"):
            self._send_json(400, _openai_error("multipart/form-data required", "bad_request", status=400))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        body = self._read_limited_body(max_request_bytes)
        fields = _parse_multipart_simple(body, ctype, capture_file=True)
        audio_bytes = fields.get("file_data") or b""
        filename = fields.get("file") or "audio"
        verb = "Transcribe and translate to English" if translate else "Transcribe"
        # Store the attachment so the model can reference it; we pass bytes inline
        # via the queue request (the model gets raw_request).
        fid = "file_" + uuid.uuid4().hex[:12]
        put_file(_runtime_root(), fid, filename, len(audio_bytes), "assistants",
                 created=int(time.time()), content=audio_bytes)
        directive = f"[Directive] {verb} the attached audio (file_id={fid}, filename={filename}). Reply with only the resulting text."
        request_data = {
            "kind": "audio", "path": "/v1/audio/transcriptions" if not translate else "/v1/audio/translations",
            "model": STATE.get("model_id", "ide-gateway"),
            "prompt_text": directive, "file_id": fid, "file_name": filename,
            "raw_request": {"filename": filename, "file_id": fid},
        }
        result = self._enqueue_and_wait(request_data)
        if "error" in result:
            self._send_json(result["_status"], result)
            return
        content = str((result["request"].get("response") or {}).get("content") or "")
        self._send_json(200, transcription_response(content))

    def _handle_embeddings(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        mode = str(STATE.get("embeddings_mode", "fallback")).lower()
        if mode == "off":
            self._send_json(501, _openai_error("Embeddings disabled (embeddings_mode=off).",
                                                "not_implemented", status=501))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_embedding_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return
        # Embeddings are served by the local fallback (no model round-trip needed).
        inputs = normalize_inputs(validated["input"])
        dim = int(STATE.get("embeddings_dim", 1536))
        self._send_json(200, embeddings_response(validated["model"], inputs, dim))

    def _handle_moderations(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        try:
            body = self._read_limited_body(max_request_bytes)
        except ValueError:
            self._send_json(413, _openai_error("Payload too large", "payload_too_large", status=413))
            return
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", status=400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_moderation_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, _openai_error(str(exc), "bad_request", status=400))
            return
        inputs = normalize_inputs(validated["input"])
        self._send_json(200, moderations_response(validated["model"], inputs))

    def _handle_file_upload(self) -> None:
        if not self._authorized():
            self._send_json(401, _openai_error("Unauthorized", "unauthorized", status=401))
            return
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/"):
            self._send_json(400, _openai_error("multipart/form-data required", "bad_request", status=400))
            return
        max_request_bytes = int(STATE.get("max_request_bytes", 4 * 1024 * 1024))
        body = self._read_limited_body(max_request_bytes)
        fields = _parse_multipart_simple(body, ctype, capture_file=True)
        data = fields.get("file_data") or b""
        filename = fields.get("file") or "upload.bin"
        purpose = fields.get("purpose") or "assistants"
        fid = "file_" + uuid.uuid4().hex[:12]
        put_file(_runtime_root(), fid, filename, len(data), purpose,
                 created=int(time.time()), content=data)
        rec = get_file(_runtime_root(), fid) or {"id": fid, "filename": filename, "bytes": len(data), "purpose": purpose, "created": int(time.time())}
        self._send_json(200, self._file_object(rec))


# --------------------------- multipart parsing ----------------------------- #
def _parse_multipart_simple(body: bytes, content_type: str,
                            capture_file: bool = False) -> dict[str, Any]:
    """Minimal multipart/form-data parser for `file`, `prompt`, `model`, `purpose`.

    Returns field name -> value (str), plus `file_data` (bytes) when capture_file.
    """
    out: dict[str, Any] = {}
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part.split("=", 1)[1].strip('"')
            break
    if not boundary:
        return out
    delim = ("--" + boundary).encode()
    segments = body.split(delim)
    for seg in segments:
        if not seg or seg in (b"--\r\n", b"--\r\n--", b"\r\n--\r\n", b"--"):
            continue
        if seg.startswith(b"--"):
            continue
        # strip leading CRLF
        if seg.startswith(b"\r\n"):
            seg = seg[2:]
        # find header/body separator
        sep = seg.find(b"\r\n\r\n")
        if sep < 0:
            continue
        header_blob = seg[:sep].decode("utf-8", errors="replace")
        value = seg[sep + 4:]
        # strip trailing CRLF
        if value.endswith(b"\r\n"):
            value = value[:-2]
        name = None
        filename = None
        for line in header_blob.split("\r\n"):
            line = line.lower()
            if "content-disposition" in line and "name=" in line:
                idx = line.find("name=")
                name = line[idx + 5:].split(";", 1)[0].strip().strip('"')
                if "filename=" in line:
                    fidx = line.find("filename=")
                    filename = line[fidx + 9:].split(";", 1)[0].strip().strip('"')
        if name is None:
            continue
        if filename is not None:
            out["file"] = filename
            if capture_file:
                out["file_data"] = value
        else:
            try:
                out[name] = value.decode("utf-8")
            except UnicodeDecodeError:
                out[name] = value.decode("latin-1", errors="replace")
    return out


# ------------------------------- main -------------------------------------- #
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


if __name__ == "__main__":
    main()
