"""Sandbox-mode standalone server: exposes the full OpenAI-compatible surface
and serves requests directly through the sandbox LLM egress (no queue, no
bridge_step.py, no MCP tool-call blocking).

This server runs as a resident background process inside the sandbox
(launched via run_program / start_command). It reads egress credentials from
environment variables (OPENAI_BASE_URL, OPENAI_API_KEY, ANTHROPIC_BASE_URL)
and proxies IDE requests directly to the LLM.

Architecture:
  IDE → http://localhost:8787/v1 → sandbox_server → egress LLM → IDE

No queue, no poll-loop, no MCP dependency. Pure HTTP proxy with OpenAI +
Anthropic translation and the full /v1/* endpoint surface.

Usage:
  python sandbox_server.py [--port 8787]
  (or set IDE_GATEWAY_PORT env var)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

# Make the repo root importable so we can use backend.py and openai_* modules
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from plugins.ide_gateway.backend import (
    discover_models,
    call_upstream,
    call_upstream_stream,
    is_anthropic_model,
)
from plugins.ide_gateway.openai_translate import (
    build_chat_completion,
    build_completion_response,
    build_response_object,
    flatten_messages,
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
    images_response,
    transcription_response,
    speech_url_response,
    first_url,
)
from plugins.ide_gateway.openai_toolbridge import catalog, directive_from_tool_choice


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _config() -> dict[str, Any]:
    return {
        "upstream_base_url": os.environ.get("OPENAI_BASE_URL", ""),
        "upstream_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "upstream_model": os.environ.get("IDE_GATEWAY_MODEL", ""),
        "request_timeout_seconds": int(os.environ.get("IDE_GATEWAY_TIMEOUT", "300")),
        "extra_models": os.environ.get("IDE_GATEWAY_EXTRA_MODELS", ""),
    }


STATE: dict[str, Any] = {}
STATE_PATH: str = ""


def _load_state(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _config() -> dict[str, Any]:
    # Start from env vars (sandbox auto-discovery)
    cfg = {
        "upstream_base_url": os.environ.get("OPENAI_BASE_URL", ""),
        "upstream_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "upstream_model": os.environ.get("IDE_GATEWAY_MODEL", ""),
        "request_timeout_seconds": int(os.environ.get("IDE_GATEWAY_TIMEOUT", "300")),
        "extra_models": os.environ.get("IDE_GATEWAY_EXTRA_MODELS", ""),
    }
    # Override with state file values if present (from start_endpoint)
    if STATE:
        if STATE.get("upstream_base_url"):
            cfg["upstream_base_url"] = STATE["upstream_base_url"]
        if STATE.get("upstream_api_key"):
            cfg["upstream_api_key"] = STATE["upstream_api_key"]
        if STATE.get("upstream_model"):
            cfg["upstream_model"] = STATE["upstream_model"]
        if STATE.get("request_timeout_seconds"):
            cfg["request_timeout_seconds"] = STATE["request_timeout_seconds"]
        if STATE.get("extra_models"):
            cfg["extra_models"] = STATE["extra_models"]
    return cfg


def _openai_error(message: str, code: str, status: int = 400) -> dict[str, Any]:
    return {"error": {"message": message, "type": "ide_gateway_error", "code": code}, "_status": status}


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _begin_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _write_sse(self, data: str) -> None:
        self.wfile.write(data.encode("utf-8"))
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        if length > 4 * 1024 * 1024:
            raise ValueError("payload too large")
        return self.rfile.read(length) if length else b""

    # --- GET routes ---
    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(200, {"status": "alive", "mode": "sandbox",
                                 "models": len(discover_models(_config()))})
            return
        if path in ("/v1/models", "/models"):
            models = discover_models(_config())
            self._send_json(200, {"object": "list", "data": models})
            return
        if path in ("/v1/tools", "/tools"):
            self._send_json(200, {"object": "list", "data": catalog()})
            return
        self._send_json(404, _openai_error("Not found", "not_found", 404))

    # --- POST routes ---
    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path in ("/v1/chat/completions", "/chat/completions"):
                self._handle_chat()
                return
            if path in ("/v1/completions", "/completions"):
                self._handle_completions()
                return
            if path in ("/v1/images/generations", "/images/generations"):
                self._handle_images()
                return
            if path in ("/v1/audio/speech", "/audio/speech"):
                self._handle_audio_speech()
                return
            if path in ("/v1/embeddings", "/embeddings"):
                self._handle_embeddings()
                return
            if path in ("/v1/moderations", "/moderations"):
                self._handle_moderations()
                return
            self._send_json(404, _openai_error("Not found", "not_found", 404))
        except Exception as exc:
            self._send_json(500, _openai_error(str(exc), "internal_error", 500))

    # --- handlers ---
    def _handle_chat(self):
        body = self._read_body()
        if not body:
            self._send_json(400, _openai_error("Missing body", "bad_request", 400))
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, _openai_error(f"Invalid JSON: {exc}", "bad_request", 400))
            return

        model = payload.get("model", "ide-gateway")
        stream = bool(payload.get("stream", False))
        cfg = _config()

        # If upstream_model not set, use the model from the request
        if not cfg["upstream_model"]:
            cfg["upstream_model"] = model

        # Apply tool_choice directive
        prompt = flatten_messages(payload.get("messages", []))
        prompt += directive_from_tool_choice(payload.get("tool_choice"), payload.get("tools"))

        if stream:
            self._begin_sse()
            cid = "chatcmpl-" + uuid.uuid4().hex[:24]
            # Role delta
            self._write_sse(f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})}\n\n')
            try:
                for chunk in call_upstream_stream(payload, cfg):
                    self.wfile.write(chunk)
                    try:
                        self.wfile.flush()
                    except Exception:
                        pass
            except Exception as exc:
                self._write_sse(f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"content": f"Error: {exc}"}, "finish_reason": None}]})}\n\n')
            self._write_sse(f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n')
            self._write_sse("data: [DONE]\n\n")
            return

        result = call_upstream(payload, cfg)
        content = result.get("content") or ""
        finish = result.get("finish_reason", "stop")
        self._send_json(200, build_chat_completion(model, content, finish=finish))

    def _handle_completions(self):
        body = self._read_body()
        payload = json.loads(body.decode("utf-8") or "{}")
        prompt = payload.get("prompt", "")
        if isinstance(prompt, list):
            prompt = "\n".join(str(p) for p in prompt)
        model = payload.get("model", "ide-gateway")
        cfg = _config()
        if not cfg["upstream_model"]:
            cfg["upstream_model"] = model
        chat_payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                        "stream": False, "temperature": payload.get("temperature"),
                        "max_tokens": payload.get("max_tokens")}
        result = call_upstream(chat_payload, cfg)
        self._send_json(200, build_completion_response(model, result.get("content") or ""))

    def _handle_images(self):
        body = self._read_body()
        payload = json.loads(body.decode("utf-8") or "{}")
        prompt = payload.get("prompt", "")
        n = int(payload.get("n", 1) or 1)
        cfg = _config()
        if not cfg["upstream_model"]:
            cfg["upstream_model"] = payload.get("model", "ide-gateway")
        directive = f"[Directive] Generate an image: {prompt}. {PUBLISH_HINT}"
        chat_payload = {"model": cfg["upstream_model"], "messages": [{"role": "user", "content": directive}]}
        result = call_upstream(chat_payload, cfg)
        content = result.get("content") or ""
        urls = extract_urls(content)
        if urls:
            self._send_json(200, images_response(urls, revised_prompt=prompt, n=n))
            return
        self._send_json(502, _openai_error("No fetchable image URL", "no_artifact", 502))

    def _handle_audio_speech(self):
        body = self._read_body()
        payload = json.loads(body.decode("utf-8") or "{}")
        text = payload.get("input", "")
        voice = payload.get("voice", "")
        cfg = _config()
        if not cfg["upstream_model"]:
            cfg["upstream_model"] = payload.get("model", "ide-gateway")
        voice_str = f" using voice '{voice}'" if voice else ""
        directive = f"[Directive] Generate audio (text-to-speech){voice_str}: {text}. {PUBLISH_HINT}"
        chat_payload = {"model": cfg["upstream_model"], "messages": [{"role": "user", "content": directive}]}
        result = call_upstream(chat_payload, cfg)
        content = result.get("content") or ""
        url = first_url(content)
        if not url:
            self._send_json(502, _openai_error("No fetchable audio URL", "no_artifact", 502))
            return
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                audio = r.read()
            fmt = (payload.get("response_format") or "mp3").lower()
            ctype = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus",
                     "aac": "audio/aac", "flac": "audio/flac", "pcm": "audio/L16"}.get(fmt, "application/octet-stream")
            self._send_bytes(200, ctype, audio)
        except Exception:
            self._send_json(200, speech_url_response(url, cfg["upstream_model"]))

    def _handle_embeddings(self):
        body = self._read_body()
        payload = json.loads(body.decode("utf-8") or "{}")
        inputs = normalize_inputs(payload.get("input", ""))
        self._send_json(200, embeddings_response(payload.get("model", "ide-gateway"), inputs, 1536))

    def _handle_moderations(self):
        body = self._read_body()
        payload = json.loads(body.decode("utf-8") or "{}")
        inputs = normalize_inputs(payload.get("input", ""))
        self._send_json(200, moderations_response(payload.get("model", ""), inputs))


def main() -> None:
    global STATE, STATE_PATH
    parser = argparse.ArgumentParser(description="IDE Gateway sandbox server")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--state", default=None, help="Path to endpoint state JSON")
    args = parser.parse_args()

    # Load state file if provided (launched by start_endpoint)
    if args.state:
        STATE_PATH = args.state
        try:
            STATE = _load_state(STATE_PATH)
        except Exception as e:
            print(f"Warning: could not load state file: {e}")

    port = args.port or (STATE.get("port") if STATE else None) or int(os.environ.get("IDE_GATEWAY_PORT", "8787"))
    bind_host = "0.0.0.0"
    if STATE and STATE.get("host"):
        bind_host = STATE["host"]
    server = ThreadingHTTPServer((bind_host, port), Handler)
    server.daemon_threads = True

    cfg = _config()
    print(f"IDE Gateway sandbox server on :{port}")
    print(f"  upstream: {cfg['upstream_base_url'] or '(not set — will use env vars)'}")
    print(f"  models: {len(discover_models(cfg))}")
    print(f"  mode: {STATE.get('gateway_mode', 'sandbox') if STATE else 'standalone'}")
    print(f"  endpoints: /v1/chat/completions, /v1/models, /v1/tools, "
          f"/v1/completions, /v1/images/generations, /v1/audio/speech, "
          f"/v1/embeddings, /v1/moderations, /health")

    # Detached daemon: survive parent process kill (platform 5-min timeout).
    # On Linux: fork + setsid. On Windows: DETACHED_PROCESS.
    if os.name == "posix":
        try:
            pid = os.fork()
            if pid > 0:
                # Parent: print and exit immediately
                print(f"Sandbox server started as daemon (PID {pid}).", file=sys.stderr)
                return
            # Child: become session leader, detach from parent
            os.setsid()
        except (AttributeError, OSError):
            pass  # fork not available — run inline

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
