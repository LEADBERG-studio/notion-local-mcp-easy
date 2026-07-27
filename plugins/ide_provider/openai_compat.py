from __future__ import annotations

import time
from typing import Any


SUPPORTED_ROLES = {"system", "user", "assistant", "tool"}


def chat_completion_response(
    request_id: str,
    model: str,
    content: str,
    finish_reason: str | None = "stop",
) -> dict[str, Any]:
    """Build an OpenAI-compatible chat.completion response payload."""
    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def openai_error(message: str, code: str, type_: str = "ide_provider_error") -> dict[str, Any]:
    """Build an OpenAI-compatible error payload."""
    return {"error": {"message": message, "type": type_, "code": code}}


def models_response(model_id: str = "ide-provider") -> dict[str, Any]:
    """Build an OpenAI-compatible /v1/models list response."""
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "local-mcp-easy",
            }
        ],
    }


def validate_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an OpenAI chat-completions request body.

    Returns a normalized dict. Raises ValueError on invalid input.
    """
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")

    for msg in messages:
        if not isinstance(msg, dict):
            raise ValueError("each message must be an object")
        role = msg.get("role")
        if role not in SUPPORTED_ROLES:
            raise ValueError(f"invalid message role: {role}")
        if "content" not in msg:
            raise ValueError("each message must have a content field")

    return {
        "model": str(payload.get("model", "ide-provider")),
        "messages": messages,
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "max_tokens": payload.get("max_tokens"),
        "stream": bool(payload.get("stream", False)),
    }
