"""Translate between OpenAI wire shapes and the single-prompt queue model.

Ported from the standalone hyperagent-openai-gateway translate.py. The Notion
MCP "active model" has no upstream thread model; the gateway flattens an OpenAI
message list into one self-contained prompt, the MCP model answers with plain
text, and we render an OpenAI chat.completion object back to the IDE.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

ROLE_LABEL = {
    "system": "System",
    "developer": "Developer",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool result",
}


def _content_to_text(content: Any) -> str:
    """Flatten a message content (string or content-parts array) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for p in content:
        if not isinstance(p, dict):
            parts.append(str(p))
            continue
        t = p.get("type")
        if t in (None, "text", "input_text", "output_text"):
            parts.append(str(p.get("text", "") or ""))
        elif t in ("image_url", "input_image"):
            url = p.get("image_url", {})
            url = url.get("url") if isinstance(url, dict) else (p.get("image_url") or p.get("url"))
            parts.append(f"[image: {url}]")
        elif t == "input_audio":
            parts.append("[audio attachment]")
        elif t in ("file", "input_file"):
            f = p.get("file", {}) or {}
            parts.append(f"[file: {f.get('filename') or f.get('file_id') or 'attached'}]")
        else:
            parts.append(str(p.get("text", "") or ""))
    return "\n".join(x for x in parts if x)


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Build a single self-contained prompt from an OpenAI message list.

    Used for stateless requests (one queue turn per call). System and developer
    messages become a preamble; the transcript preserves turn order.
    """
    preamble: list[str] = []
    transcript: list[str] = []
    for m in messages:
        text = _content_to_text(m.get("content"))
        role = m.get("role", "user")
        if role in ("system", "developer"):
            if text:
                preamble.append(text)
            continue
        if role == "tool":
            transcript.append(f"Tool result ({m.get('tool_call_id', '') or ''}): {text}")
            continue
        if role == "assistant" and m.get("tool_calls"):
            calls = ", ".join(
                f"{(c.get('function') or {}).get('name')}({(c.get('function') or {}).get('arguments')})"
                for c in m["tool_calls"]
                if isinstance(c, dict)
            )
            transcript.append(f"Assistant (tool calls): {calls}")
            if text:
                transcript.append(f"Assistant: {text}")
            continue
        transcript.append(f"{ROLE_LABEL.get(role, str(role).title())}: {text}")

    out: list[str] = []
    if preamble:
        out.append("\n".join(preamble))
    if len(transcript) == 1 and not preamble:
        return transcript[0].split(": ", 1)[-1]
    if transcript:
        out.append("\n".join(transcript))
    return "\n\n".join(out).strip()


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _content_to_text(m.get("content"))
    return flatten_messages(messages)


def _finish_reason(has_tool_calls: bool) -> str:
    return "tool_calls" if has_tool_calls else "stop"


def build_chat_completion(model: str, content: str, *,
                          finish: str | None = None,
                          tool_calls: list[dict[str, Any]] | None = None,
                          created: int | None = None) -> dict[str, Any]:
    """Render an OpenAI chat.completion object from a finished turn."""
    message: dict[str, Any] = {"role": "assistant", "content": content or None, "refusal": None}
    has_tc = bool(tool_calls)
    if has_tc:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "system_fingerprint": "ide-bridge",
        "choices": [{
            "index": 0,
            "message": message,
            "logprobs": None,
            "finish_reason": finish or _finish_reason(has_tc),
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def build_completion_response(model: str, text: str, created: int | None = None) -> dict[str, Any]:
    return {
        "id": "cmpl-" + uuid.uuid4().hex[:24],
        "object": "text_completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "text": text,
            "finish_reason": "stop",
            "logprobs": None,
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def build_response_object(rid: str, model: str, status: str, text: str) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if status == "completed":
        output = [{
            "type": "message",
            "id": "msg_" + uuid.uuid4().hex[:20],
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }]
    return {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "output_text": text if status == "completed" else None,
        "usage": None,
        "metadata": {},
    }


def model_object(model_id: str, *, name: str = "", description: str = "") -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "local-mcp-easy",
        "metadata": {"name": name or model_id, "description": description},
    }
