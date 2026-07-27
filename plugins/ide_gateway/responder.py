"""IDE Gateway autonomous responder daemon.

A subprocess that polls the ide_gateway request queue, claims pending requests,
forwards them to a configured OpenAI-compatible upstream (or a manual bridge),
and completes them through the queue so the worker can stream the answer back
to the IDE.

Lifecycle:
  1. claim_next_request(runtime, endpoint, poll_interval)
  2. route by request.kind (/v1/responses | /v1/chat/completions | /v1/completions)
  3. call upstream via urllib (non-stream or stream)
  4. complete_request(content=..., stream_chunks=[...])  OR  fail_request_by_id

For streaming requests, the responder stores `stream_chunks` on the completed
request so the worker can emit token-like deltas preserving the SSE lifecycle
that was already validated in 1.8.1 (response.created -> ... -> [DONE]).
"""
from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.ide_gateway.queue import (
    claim_next_request,
    complete_request,
    fail_request_by_id,
    request_counts,
)
from plugins.ide_gateway.security import redact_line

import argparse
import contextlib
import json
import os
import threading
import time
import traceback
import urllib.request
from typing import Any

STATE: dict[str, Any] = {}
STATE_PATH: str = ""


def _log(message: str) -> None:
    log_path = STATE.get("responder_log_path") or STATE.get("log_path")
    if not log_path and STATE_PATH:
        log_path = str(Path(STATE_PATH).parent.parent / "logs" / f"{STATE.get('name', 'default')}.responder.log")
    if not log_path:
        return
    try:
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"{datetime_now()} {redact_line(message)}\n")
    except Exception:
        pass


def datetime_now() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def _runtime_root() -> Path:
    return Path(STATE_PATH).parent.parent


def _save_state(path: str, state: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_state(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Upstream call
# --------------------------------------------------------------------------- #
def _cfg(config: dict[str, Any], *names: str, default: Any = "") -> Any:
    """Read a config value trying both responder_-prefixed (plugin config)
    and bare (responder state file) naming."""
    for n in names:
        if n in config:
            return config[n]
    return default


def _upstream_headers(config: dict[str, Any]) -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    key = str(_cfg(config, "responder_upstream_api_key", "upstream_api_key")).strip()
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _upstream_url(config: dict[str, Any], kind: str, stream: bool) -> str:
    base = str(_cfg(config, "responder_upstream_base_url", "upstream_base_url")).strip().rstrip("/")
    if not base:
        raise ValueError("responder_upstream_base_url is required for non-manual mode")
    if kind in ("responses", "responses_stream"):
        path = "/responses"
    else:
        path = "/chat/completions"
    return base + path


def _build_upstream_payload(req: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    kind = req.get("kind", "chat")
    upstream_model = str(_cfg(config, "responder_upstream_model", "upstream_model")).strip()
    stream = bool(req.get("stream", False))

    if kind in ("responses", "responses_stream"):
        # Forward as a Responses-style request when upstream supports it; many
        # OpenAI-compatible upstreams only speak chat/completions, so we fall
        # back to chat shape and let the worker translate the content back.
        return {
            "model": upstream_model or req.get("model", "ide-gateway"),
            "input": req.get("prompt") or _messages_to_text(req.get("messages") or []),
            "stream": stream,
        }

    # chat / completion
    messages = req.get("messages")
    if not messages:
        messages = [{"role": "user", "content": req.get("prompt") or ""}]
    payload = {
        "model": upstream_model or req.get("model", "ide-gateway"),
        "messages": messages,
        "stream": stream,
    }
    if req.get("temperature") is not None:
        payload["temperature"] = req["temperature"]
    if req.get("top_p") is not None:
        payload["top_p"] = req["top_p"]
    if req.get("max_tokens") is not None:
        payload["max_tokens"] = req["max_tokens"]
    if req.get("tools"):
        payload["tools"] = req["tools"]
    if req.get("tool_choice"):
        payload["tool_choice"] = req["tool_choice"]
    return payload


def _messages_to_text(messages: list) -> str:
    out = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        out.append(f"{role}: {content}")
    return "\n".join(out)


def _extract_content(upstream_payload: dict[str, Any]) -> tuple[str | None, list[str] | None, dict[str, Any] | None]:
    """Extract (content, stream_chunks, tool_calls) from an upstream chat response."""
    # Responses API shape: output[].content[].text  / output_text
    if "output_text" in upstream_payload:
        text = str(upstream_payload.get("output_text") or "")
        return text, None, None
    output = upstream_payload.get("output")
    if isinstance(output, list) and output:
        # Concatenate text blocks from message items.
        texts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                        texts.append(str(block.get("text", "") or ""))
        if texts:
            return "\n".join(texts), None, None

    # Chat completions shape: choices[].message.content
    choices = upstream_payload.get("choices") or []
    if not choices:
        return None, None, None
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")
    finish = choice.get("finish_reason") or "stop"
    if tool_calls:
        return content, None, {"tool_calls": tool_calls, "finish_reason": finish}
    return content, None, None


def _extract_stream_chunks_from_sse(raw_body: bytes) -> list[str]:
    """Parse upstream SSE chat.completion.chunk stream and collect delta content."""
    chunks: list[str] = []
    text = raw_body.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        piece = delta.get("content")
        if piece:
            chunks.append(piece)
    return chunks


def call_upstream(req: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Forward a queued request to the upstream. Returns a result dict with
    content/stream_chunks/tool_calls or raises on error."""
    upstream_type = str(_cfg(config, "responder_upstream_type", "upstream_type", default="openai_compatible"))
    if upstream_type == "manual":
        raise ValueError("responder is in manual mode; upstream calls are disabled")

    kind = req.get("kind", "chat")
    stream = bool(req.get("stream", False))
    url = _upstream_url(config, kind, stream)
    payload = _build_upstream_payload(req, config)
    timeout = int(_cfg(config, "responder_request_timeout_seconds", "request_timeout_seconds", default=300))
    headers = _upstream_headers(config)

    _log(f"upstream request started: {url} stream={stream}")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(request, timeout=timeout) as resp:
        status = resp.status
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        _log(f"upstream status {status} ctype={ctype}")

        if stream and "text/event-stream" in ctype:
            chunks = _extract_stream_chunks_from_sse(raw)
            content = "".join(chunks) if chunks else ""
            return {"content": content, "stream_chunks": chunks or None, "tool_calls": None, "finish_reason": "stop"}

        body = json.loads(raw.decode("utf-8") or "{}")
        content, stream_chunks, tool_meta = _extract_content(body)
        if tool_meta:
            return {"content": content, "stream_chunks": None, "tool_calls": tool_meta,
                    "finish_reason": tool_meta.get("finish_reason", "tool_calls")}
        # Determine finish_reason: chat shape has choices[].finish_reason;
        # responses shape has status=completed.
        finish_reason = "stop"
        if body.get("choices"):
            try:
                finish_reason = (body["choices"][0] or {}).get("finish_reason", "stop")
            except Exception:
                pass
        elif body.get("status") == "completed":
            finish_reason = "stop"
        return {"content": content, "stream_chunks": None, "tool_calls": None,
                "finish_reason": finish_reason}


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def _handle_request(req: dict[str, Any], config: dict[str, Any]) -> None:
    request_id = req["request_id"]
    endpoint = req.get("endpoint", STATE.get("name", "default"))
    kind = req.get("kind", "chat")
    stream = bool(req.get("stream", False))
    route = req.get("path", "/v1/chat/completions")
    _log(f"claimed {request_id} route {route} kind={kind} stream={stream}")

    # Track last_request_id in the responder state file (best-effort).
    try:
        STATE["last_request_id"] = request_id
        _save_state(STATE_PATH, STATE)
    except Exception:
        pass

    max_bytes = int(_cfg(config, "max_response_bytes", default=4 * 1024 * 1024))
    try:
        result = call_upstream(req, config)
    except Exception as exc:
        _log(f"failed {request_id} error {exc}")
        fail_request_by_id(_runtime_root(), request_id, "responder_error", str(exc))
        return

    content = result.get("content")
    stream_chunks = result.get("stream_chunks")
    tool_meta = result.get("tool_calls")
    finish_reason = result.get("finish_reason", "stop")

    if tool_meta:
        # Transparent pass-through of tool calls: complete with a structured
        # payload so the worker can serialize tool_calls in the OpenAI shape.
        complete_request(
            _runtime_root(), request_id, content,
            payload={"tool_calls": tool_meta.get("tool_calls"), "finish_reason": tool_meta.get("finish_reason", "tool_calls")},
            finish_reason=tool_meta.get("finish_reason", "tool_calls"),
            max_response_bytes=max_bytes,
        )
        _log(f"completed {request_id} (tool_calls)")
        return

    complete_request(
        _runtime_root(), request_id, content,
        stream_chunks=stream_chunks,
        finish_reason=finish_reason,
        max_response_bytes=max_bytes,
    )
    _log(f"completed {request_id} (content len={len(content or '')})")


def serve_loop(stop_event: threading.Event, config: dict[str, Any]) -> None:
    endpoint = STATE.get("name", "default")
    runtime = _runtime_root()
    poll_interval = float(_cfg(config, "responder_poll_interval_seconds", "poll_interval_seconds", default=0.25))
    claim_timeout = 1  # short claim window so stop is responsive

    _log(f"responder loop started endpoint={endpoint} upstream={_cfg(config, 'responder_upstream_type', 'upstream_type')}")
    while not stop_event.is_set():
        try:
            req = claim_next_request(runtime, endpoint, claim_timeout,
                                     request_timeout=int(_cfg(config, "responder_request_timeout_seconds", "request_timeout_seconds", default=300)))
        except Exception as exc:
            _log(f"claim error: {exc}")
            time.sleep(poll_interval)
            continue
        if req is None:
            continue
        try:
            _handle_request(req, config)
        except Exception as exc:
            _log(f"handle error for {req.get('request_id')}: {exc}\n{traceback.format_exc()}")
            with contextlib.suppress(Exception):
                fail_request_by_id(runtime, req.get("request_id"), "responder_error", str(exc))
    _log("responder loop stopped")


# --------------------------------------------------------------------------- #
# Process entry
# --------------------------------------------------------------------------- #
def _install_excepthook(log_path: str | None) -> None:
    def _hook(args: Any) -> None:
        try:
            buf = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            redacted = redact_line(buf)
            path = log_path
            if not path and STATE_PATH:
                path = str(Path(STATE_PATH).parent.parent / "logs" / f"{STATE.get('name', 'default')}.responder.log")
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
    STATE = _load_state(STATE_PATH)
    STATE["status"] = "running"
    STATE["pid"] = os.getpid()
    STATE["started_at"] = datetime_now()
    STATE["last_request_id"] = ""
    STATE["last_error"] = ""
    _save_state(STATE_PATH, STATE)

    log_path = STATE.get("responder_log_path") or STATE.get("log_path")
    _install_excepthook(log_path)

    stop_event = threading.Event()
    try:
        serve_loop(stop_event, STATE)
    except KeyboardInterrupt:
        pass
    finally:
        STATE["status"] = "stopped"
        with contextlib.suppress(Exception):
            _save_state(STATE_PATH, STATE)


if __name__ == "__main__":
    main()
