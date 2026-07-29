"""Universal backend layer: discover models, call upstream, translate formats.

Supports three backend types:
  - bridge:    queue → model in chat (long-poll via bridge_step.py)
  - sandbox:   direct egress LLM call from a sandbox environment
  - external:  direct call to an OpenAI-compatible provider (Ollama, OpenAI, ...)

The sandbox and external backends share the same call_upstream logic; they differ
only in how the base URL and API key are discovered (env vars vs config).

Anthropic translation is included so models behind an Anthropic egress (Claude
family) can be served through the OpenAI-compatible surface without a separate
adapter.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


# --------------------------------------------------------------------------- #
# Model discovery
# --------------------------------------------------------------------------- #
def discover_models(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Discover available models from environment variables (sandbox egress) or
    from a static fallback list.

    Sandbox environments typically expose OPENAI_BASE_URL / ANTHROPIC_BASE_URL
    and a model list. We try to read /v1/models from the egress; if that fails,
    we return a sensible default list.
    """
    config = config or {}
    models: list[dict[str, Any]] = []

    # Try OpenAI egress
    openai_base = os.environ.get("OPENAI_BASE_URL", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_base:
        try:
            req = urllib.request.Request(
                openai_base.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {openai_key}"} if openai_key else {},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("data", []):
                    if isinstance(m, dict) and m.get("id"):
                        models.append({"id": m["id"], "object": "model", "created": 0,
                                        "owned_by": m.get("owned_by", "sandbox")})
        except Exception:
            pass

    # If egress didn't return models, use the static fallback (covers known
    # sandbox models + any custom models from config).
    if not models:
        for name in _DEFAULT_MODELS:
            models.append({"id": name, "object": "model", "created": 0, "owned_by": "fallback"})

    # Merge config-provided models
    for name in config.get("extra_models", "").split(","):
        name = name.strip()
        if name and name not in {m["id"] for m in models}:
            models.append({"id": name, "object": "model", "created": 0, "owned_by": "config"})

    return models


_DEFAULT_MODELS = [
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "claude-opus-4-8", "claude-fable-5", "claude-opus-4-7", "claude-sonnet-5",
    "claude-sonnet-4-6", "claude-haiku-4-5",
    "deepseek-v4-pro", "deepseek-v4-flash",
    "qwen3.7-plus", "qwen3.7-max",
    "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
    "grok-4.3", "glm-5.2", "glm-5.1", "hy3", "kimi-k3", "kimi-k2.7-code",
    "minimax-m3", "step-3.7-flash", "mimo-v2.5", "mimo-v2.5-pro",
    "openrouter-fusion",
    # Generic alias
    "ide-gateway",
]


# --------------------------------------------------------------------------- #
# Determine if a model is Anthropic-family (needs translation)
# --------------------------------------------------------------------------- #
_ANTHROPIC_PREFIXES = ("claude", "anthropic")


def is_anthropic_model(model: str) -> bool:
    return any(model.lower().startswith(p) for p in _ANTHROPIC_PREFIXES)


# --------------------------------------------------------------------------- #
# Anthropic translation (OpenAI ⇄ Anthropic Messages API)
# --------------------------------------------------------------------------- #
def openai_to_anthropic(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI chat-completion payload to an Anthropic Messages API payload."""
    messages: list[dict[str, Any]] = []
    system: str | None = None

    for msg in payload.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system = content if isinstance(content, str) else str(content)
            continue

        if isinstance(content, list):
            anthropic_content: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    anthropic_content.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {})
                    url = url.get("url") if isinstance(url, dict) else url
                    if isinstance(url, str) and url.startswith("data:"):
                        media_type = url.split(";")[0].replace("data:", "")
                        data = url.split(",", 1)[1] if "," in url else url
                        anthropic_content.append({
                            "type": "image", "source": {
                                "type": "base64", "media_type": media_type, "data": data}})
            messages.append({"role": role, "content": anthropic_content})
        else:
            messages.append({"role": role, "content": str(content)})

    result: dict[str, Any] = {
        "model": payload.get("model", ""),
        "messages": messages,
        "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 4096,
    }
    if system:
        result["system"] = system
    for key, anth_key in [("temperature", "temperature"), ("top_p", "top_p")]:
        if key in payload:
            result[anth_key] = payload[key]
    if "stop" in payload:
        stop = payload["stop"]
        result["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if payload.get("stream"):
        result["stream"] = True
    return result


def anthropic_to_openai(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert an Anthropic Messages API response to an OpenAI chat-completion."""
    content_parts: list[str] = []
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            content_parts.append(block.get("text", ""))

    stop_reason_map = {
        "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    finish = stop_reason_map.get(payload.get("stop_reason", ""), "stop")

    return {
        "id": payload.get("id", "chatcmpl-anthropic"),
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "\n".join(content_parts)},
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": payload.get("usage", {}).get("input_tokens", 0),
            "completion_tokens": payload.get("usage", {}).get("output_tokens", 0),
            "total_tokens": payload.get("usage", {}).get("input_tokens", 0)
                           + payload.get("usage", {}).get("output_tokens", 0),
        },
    }


# --------------------------------------------------------------------------- #
# Upstream call
# --------------------------------------------------------------------------- #
def call_upstream(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Call the upstream LLM provider and return an OpenAI-shaped result dict.

    config must contain:
      - upstream_base_url: e.g. https://egress.conol.ai/api/egress/openai/v1
                           or https://api.openai.com/v1 or http://127.0.0.1:11434/v1
      - upstream_api_key:  bearer token (may be empty for local unauthenticated)
      - upstream_model:    override model name (optional)

    Returns a dict with keys: content, stream_chunks, tool_calls, finish_reason.
    Raises on error.
    """
    base_url = str(config.get("upstream_base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("upstream_base_url is required for sandbox/external backend")

    api_key = str(config.get("upstream_api_key", "")).strip()
    model = str(config.get("upstream_model", "")).strip() or payload.get("model", "ide-gateway")
    stream = bool(payload.get("stream", False))

    anthropic = is_anthropic_model(model) and "anthropic" in base_url.lower()
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}

    if anthropic:
        forward_payload = openai_to_anthropic({**payload, "model": model})
        url = base_url.rstrip("/") + "/v1/messages"
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
    else:
        forward_payload = {**payload, "model": model}
        # Fix max_tokens → max_completion_tokens for newer OpenAI models
        if "max_tokens" in forward_payload:
            forward_payload["max_completion_tokens"] = forward_payload.pop("max_tokens")
        for bad_key in ("top_logprobs", "logprobs", "logit_bias"):
            forward_payload.pop(bad_key, None)
        url = base_url + "/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(config.get("request_timeout_seconds", 300))
    data = json.dumps(forward_payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")

        if stream and "text/event-stream" in ctype:
            chunks = _extract_stream_chunks_from_sse(raw)
            content = "".join(chunks) if chunks else ""
            return {"content": content, "stream_chunks": chunks or None,
                    "tool_calls": None, "finish_reason": "stop"}

        body = json.loads(raw.decode("utf-8") or "{}")
        if anthropic:
            body = anthropic_to_openai(body, model)

        content, tool_calls, finish_reason = _extract_openai_content(body)
        return {"content": content, "stream_chunks": None,
                "tool_calls": tool_calls, "finish_reason": finish_reason}


def call_upstream_stream(payload: dict[str, Any], config: dict[str, Any]):
    """Generator: call upstream with stream=True and yield SSE chunks.

    Yields raw bytes (SSE lines) ready to write to the client connection.
    """
    base_url = str(config.get("upstream_base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("upstream_base_url is required")
    api_key = str(config.get("upstream_api_key", "")).strip()
    model = str(config.get("upstream_model", "")).strip() or payload.get("model", "ide-gateway")

    anthropic = is_anthropic_model(model) and "anthropic" in base_url.lower()
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}

    if anthropic:
        forward_payload = openai_to_anthropic({**payload, "model": model, "stream": True})
        url = base_url.rstrip("/") + "/v1/messages"
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
    else:
        forward_payload = {**payload, "model": model, "stream": True}
        if "max_tokens" in forward_payload:
            forward_payload["max_completion_tokens"] = forward_payload.pop("max_tokens")
        url = base_url + "/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(config.get("request_timeout_seconds", 300))
    data = json.dumps(forward_payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            yield chunk


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _extract_openai_content(body: dict[str, Any]) -> tuple[str | None, dict | None, str]:
    choices = body.get("choices") or []
    if not choices:
        return None, None, "stop"
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")
    finish = choice.get("finish_reason", "stop")
    if tool_calls:
        return content, {"tool_calls": tool_calls, "finish_reason": finish}, finish
    return content, None, finish


def _extract_stream_chunks_from_sse(raw: bytes) -> list[str]:
    chunks: list[str] = []
    text = raw.decode("utf-8", errors="replace")
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
