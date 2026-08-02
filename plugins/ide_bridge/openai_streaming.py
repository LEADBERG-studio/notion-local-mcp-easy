"""Emulated OpenAI SSE streaming via queue poll-diff.

The active MCP model does not push partial tokens. It answers a queued request
either with a single `content` string (flushed as one delta) or with an optional
`stream_chunks` list (flushed chunk-by-chunk). These helpers render standard
OpenAI chat.completion.chunk / response.* SSE events as the worker polls the
request file for completion.

Ported from hyperagent-openai-gateway streaming.py, adapted to the queue model.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable


def _chunk(cid: str, model: str, created: int, delta: dict,
           finish: str | None = None) -> str:
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(obj)}\n\n"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _comment(comment: str = "keepalive") -> str:
    return f": {comment}\n\n"


def render_chat_stream_events(
    *,
    cid: str,
    model: str,
    req: dict[str, Any],
    include_usage: bool = False,
) -> list[str]:
    """Render the full set of SSE strings for a completed chat-completions
    streaming request. The worker calls this once the request is completed and
    flushes the events in order."""
    created = int(time.time())
    events: list[str] = []

    # role delta first (OpenAI convention)
    events.append(_chunk(cid, model, created, {"role": "assistant", "content": ""}))

    response = req.get("response") or {}
    content = str(response.get("content") or "")
    chunks = response.get("stream_chunks")
    finish = response.get("finish_reason") or "stop"

    if isinstance(chunks, list) and chunks:
        for piece in chunks:
            if not isinstance(piece, str):
                piece = str(piece)
            if piece:
                events.append(_chunk(cid, model, created, {"content": piece}))
    elif content:
        events.append(_chunk(cid, model, created, {"content": content}))

    events.append(_chunk(cid, model, created, {}, finish=finish))

    if include_usage:
        obj = {
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": model, "choices": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        events.append(f"data: {json.dumps(obj)}\n\n")

    events.append("data: [DONE]\n\n")
    return events


def render_responses_stream_events(
    *,
    rid: str,
    model: str,
    req: dict[str, Any],
) -> list[str]:
    """Render OpenAI Responses API streaming events for a completed request.

    Strict IDE clients need the full output item/content part lifecycle, not
    only response.output_text.delta + response.completed.
    """
    created = int(time.time())
    base = {"id": rid, "object": "response", "model": model, "created_at": created}
    events: list[str] = []
    seq = 0

    def env(status: str, output_text: str | None = None,
            output: list | None = None) -> dict[str, Any]:
        r = dict(base, status=status)
        if output is not None:
            r["output"] = output
        if output_text is not None:
            r["output_text"] = output_text
        return r

    response = req.get("response") or {}
    content = str(response.get("content") or "")
    chunks = response.get("stream_chunks")
    finish_reason = str(response.get("finish_reason") or "stop")
    msg_id = "msg_" + uuid.uuid4().hex[:20]

    message_item = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "status": "in_progress",
        "content": [],
    }
    text_part = {"type": "output_text", "text": "", "annotations": []}

    events.append(_sse("response.created", {
        "type": "response.created",
        "response": env("in_progress"),
    }))
    events.append(_sse("response.in_progress", {
        "type": "response.in_progress",
        "response": env("in_progress"),
    }))
    events.append(_sse("response.output_item.added", {
        "type": "response.output_item.added",
        "response_id": rid,
        "output_index": 0,
        "item": message_item,
    }))
    events.append(_sse("response.content_part.added", {
        "type": "response.content_part.added",
        "response_id": rid,
        "item_id": msg_id,
        "output_index": 0,
        "content_index": 0,
        "part": text_part,
    }))

    def emit_delta(piece: str) -> None:
        nonlocal seq
        if not piece:
            return
        seq += 1
        events.append(_sse("response.output_text.delta", {
            "type": "response.output_text.delta",
            "response_id": rid,
            "item_id": msg_id,
            "output_index": 0,
            "content_index": 0,
            "sequence_number": seq,
            "delta": piece,
        }))

    if isinstance(chunks, list) and chunks:
        for piece in chunks:
            emit_delta(piece if isinstance(piece, str) else str(piece))
    else:
        emit_delta(content)

    done_part = {"type": "output_text", "text": content, "annotations": []}
    completed_item = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [done_part],
    }

    events.append(_sse("response.output_text.done", {
        "type": "response.output_text.done",
        "response_id": rid,
        "item_id": msg_id,
        "output_index": 0,
        "content_index": 0,
        "text": content,
    }))
    events.append(_sse("response.content_part.done", {
        "type": "response.content_part.done",
        "response_id": rid,
        "item_id": msg_id,
        "output_index": 0,
        "content_index": 0,
        "part": done_part,
    }))
    events.append(_sse("response.output_item.done", {
        "type": "response.output_item.done",
        "response_id": rid,
        "output_index": 0,
        "item": completed_item,
    }))

    completed = env("completed", output_text=content, output=[completed_item])
    completed["finish_reason"] = finish_reason
    events.append(_sse("response.completed", {
        "type": "response.completed",
        "response": completed,
    }))
    events.append("data: [DONE]\n\n")
    return events
