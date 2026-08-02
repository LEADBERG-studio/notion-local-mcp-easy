"""OpenAI-compatible request schemas, validated without pydantic.

The worker is a stdlib HTTP server (no FastAPI/pydantic available in the worker
process), so we do permissive validation by hand. Extra fields are accepted and
preserved in raw_request so any OpenAI client keeps working.
"""
from __future__ import annotations

from typing import Any

SUPPORTED_ROLES = {"system", "developer", "user", "assistant", "tool"}


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _content_to_text(content: Any) -> str:
    """Flatten a message content (string or content-parts array) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not _is_list(content):
        return str(content)
    parts: list[str] = []
    for p in content:
        if not _is_dict(p):
            parts.append(str(p))
            continue
        t = p.get("type")
        if t in (None, "text", "input_text", "output_text"):
            parts.append(str(p.get("text", "") or ""))
        elif t in ("image_url", "input_image"):
            url = p.get("image_url", {})
            url = url.get("url") if _is_dict(url) else (p.get("image_url") or p.get("url"))
            parts.append(f"[image: {url}]")
        elif t == "input_audio":
            parts.append("[audio attachment]")
        elif t in ("file", "input_file"):
            f = p.get("file", {}) or {}
            parts.append(f"[file: {f.get('filename') or f.get('file_id') or 'attached'}]")
        else:
            parts.append(str(p.get("text", "") or ""))
    return "\n".join(x for x in parts if x)


def validate_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an OpenAI chat-completions request body. Returns normalized dict."""
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")

    messages = payload.get("messages")
    if not _is_list(messages) or not messages:
        raise ValueError("messages must be a non-empty list")

    norm_messages: list[dict[str, Any]] = []
    for msg in messages:
        if not _is_dict(msg):
            raise ValueError("each message must be an object")
        role = msg.get("role")
        if role not in SUPPORTED_ROLES:
            raise ValueError(f"invalid message role: {role}")
        if "content" not in msg and role != "assistant":
            raise ValueError("each non-assistant message must have a content field")
        norm_messages.append(msg)

    return {
        "model": _as_str(payload.get("model", "ide-bridge")),
        "messages": norm_messages,
        "stream": _as_bool(payload.get("stream", False)),
        "stream_options": payload.get("stream_options") if _is_dict(payload.get("stream_options")) else None,
        "tools": payload.get("tools") if _is_list(payload.get("tools")) else None,
        "tool_choice": payload.get("tool_choice"),
        "functions": payload.get("functions") if _is_list(payload.get("functions")) else None,
        "function_call": payload.get("function_call"),
        "response_format": payload.get("response_format") if _is_dict(payload.get("response_format")) else None,
        "max_tokens": payload.get("max_tokens"),
        "max_completion_tokens": payload.get("max_completion_tokens"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "n": payload.get("n", 1),
        "stop": payload.get("stop"),
        "modalities": payload.get("modalities"),
        "audio": payload.get("audio") if _is_dict(payload.get("audio")) else None,
        "web_search_options": payload.get("web_search_options") if _is_dict(payload.get("web_search_options")) else None,
        "metadata": payload.get("metadata") if _is_dict(payload.get("metadata")) else None,
        "user": _as_str(payload.get("user", "")),
        "conversation_id": _as_str(payload.get("conversation_id", "")),
        "raw_request": payload,
    }


def validate_completion_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")
    prompt = payload.get("prompt", "")
    if isinstance(prompt, list):
        prompt = "\n".join(str(p) for p in prompt)
    return {
        "model": _as_str(payload.get("model", "ide-bridge")),
        "prompt": prompt,
        "stream": _as_bool(payload.get("stream", False)),
        "max_tokens": payload.get("max_tokens"),
        "raw_request": payload,
    }


def validate_responses_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")
    inp = payload.get("input")
    if isinstance(inp, str):
        prompt = inp
    elif _is_list(inp):
        prompt = ""
    else:
        prompt = ""
    instructions = _as_str(payload.get("instructions", ""), "")
    return {
        "model": _as_str(payload.get("model", "ide-bridge")),
        "input": inp,
        "instructions": instructions,
        "prompt": prompt,
        "stream": _as_bool(payload.get("stream", False)),
        "background": _as_bool(payload.get("background", False)),
        "previous_response_id": _as_str(payload.get("previous_response_id", ""), ""),
        "tools": payload.get("tools") if _is_list(payload.get("tools")) else None,
        "tool_choice": payload.get("tool_choice"),
        "metadata": payload.get("metadata") if _is_dict(payload.get("metadata")) else None,
        "raw_request": payload,
    }


def validate_embedding_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")
    inp = payload.get("input")
    if inp is None:
        raise ValueError("input is required")
    return {
        "model": _as_str(payload.get("model", "ide-bridge")),
        "input": inp,
        "raw_request": payload,
    }


def validate_moderation_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")
    inp = payload.get("input")
    if inp is None:
        raise ValueError("input is required")
    return {
        "model": _as_str(payload.get("model", ""), ""),
        "input": inp,
        "raw_request": payload,
    }


def validate_images_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")
    prompt = _as_str(payload.get("prompt", ""), "")
    return {
        "prompt": prompt,
        "model": _as_str(payload.get("model", ""), ""),
        "n": int(payload.get("n", 1) or 1),
        "size": _as_str(payload.get("size", ""), ""),
        "response_format": _as_str(payload.get("response_format", "url"), "url"),
        "raw_request": payload,
    }


def validate_speech_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not _is_dict(payload):
        raise ValueError("request body must be a JSON object")
    return {
        "model": _as_str(payload.get("model", ""), ""),
        "input": _as_str(payload.get("input", ""), ""),
        "voice": _as_str(payload.get("voice", ""), ""),
        "response_format": _as_str(payload.get("response_format", "mp3"), "mp3"),
        "raw_request": payload,
    }


def validate_list_models_request(query: dict[str, Any]) -> dict[str, Any]:
    return {"raw_request": query or {}}
