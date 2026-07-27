"""Tool bridge: expose the active MCP model's tool catalog as OpenAI tool
definitions, and translate forced tool_choice into directives.

Ported from hyperagent-openai-gateway toolbridge.py. The canonical catalog is a
compact, OpenAI-shaped description of capabilities the MCP model can provide
(shell, write_file, web_search, generate_image, ...). It is the gateway's
source of truth and can be refined against the live MCP tool signatures.
"""
from __future__ import annotations

import json
from typing import Any


def _fn(name: str, description: str, params: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", **params},
        },
    }


_STR = {"type": "string"}

# Canonical catalog of capabilities. Kept compact; parameter schemas are the
# gateway's source of truth and can be refined against the live runtime.
CANONICAL_TOOLS: list[dict] = [
    _fn("shell", "Run a bash command in the agent sandbox (Python/Node/CLI; cwd persists).",
        {"properties": {"command": _STR}, "required": ["command"]}),
    _fn("write_file", "Create or overwrite a file in the sandbox.",
        {"properties": {"path": _STR, "content": _STR}, "required": ["path", "content"]}),
    _fn("read_file", "Read a file from the sandbox.",
        {"properties": {"path": _STR}, "required": ["path"]}),
    _fn("edit_file", "String-replace edit a file.",
        {"properties": {"path": _STR, "old": _STR, "new": _STR}, "required": ["path", "old", "new"]}),
    _fn("list_files", "List or glob files.",
        {"properties": {"pattern": _STR}}),
    _fn("web_search", "Semantic/keyword web search.",
        {"properties": {"query": _STR}, "required": ["query"]}),
    _fn("web_fetch", "Fetch and parse a URL's clean content.",
        {"properties": {"url": _STR}, "required": ["url"]}),
    _fn("image_search", "Search the web for images.",
        {"properties": {"query": _STR}, "required": ["query"]}),
    _fn("generate_image", "Generate or edit an image.",
        {"properties": {"prompt": _STR, "aspect_ratio": _STR}, "required": ["prompt"]}),
    _fn("generate_video", "Generate a short video.",
        {"properties": {"prompt": _STR}, "required": ["prompt"]}),
    _fn("generate_audio", "Text-to-speech (single or multi-speaker).",
        {"properties": {"text": _STR, "voice": _STR}, "required": ["text"]}),
    _fn("transcribe_audio", "Transcribe audio (diarization, timestamps).",
        {"properties": {"file_id": _STR}, "required": ["file_id"]}),
    _fn("create_table", "Create a typed table.",
        {"properties": {"title": _STR, "columns": {"type": "array"}}, "required": ["title"]}),
    _fn("create_document", "Create a persistent document.",
        {"properties": {"title": _STR, "sections": {"type": "array"}}, "required": ["title"]}),
    _fn("publish_webpage", "Publish a self-contained HTML webpage artifact.",
        {"properties": {"title": _STR, "html": _STR}, "required": ["title", "html"]}),
    _fn("publish_slides", "Publish a slide deck artifact.",
        {"properties": {"title": _STR, "html": _STR}, "required": ["title", "html"]}),
    _fn("generate_map", "Generate an interactive map.",
        {"properties": {"title": _STR, "markers": {"type": "array"}}, "required": ["title"]}),
    _fn("directions", "Turn-by-turn directions.",
        {"properties": {"origin": _STR, "destination": _STR}, "required": ["origin", "destination"]}),
    _fn("place_search", "Find places near a location.",
        {"properties": {"query": _STR}, "required": ["query"]}),
    _fn("weather", "Current/forecast weather.",
        {"properties": {"location": _STR}, "required": ["location"]}),
    _fn("search_knowledge", "Search skills and memories.",
        {"properties": {"query": _STR}, "required": ["query"]}),
]

CANONICAL_TOOL_NAMES = {t["function"]["name"] for t in CANONICAL_TOOLS}


def catalog(disabled: set[str] | None = None) -> list[dict]:
    disabled = disabled or set()
    return [t for t in CANONICAL_TOOLS if t["function"]["name"] not in disabled]


def forced_tool_name(tool_choice: Any) -> str | None:
    """Return the canonical tool name a client forced via tool_choice, else None."""
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name")
        if name in CANONICAL_TOOL_NAMES:
            return name
    return None


def primary_param(tool_name: str) -> str:
    """First required parameter of a canonical tool (for arg mapping)."""
    for t in CANONICAL_TOOLS:
        if t["function"]["name"] == tool_name:
            req = t["function"]["parameters"].get("required") or []
            if req:
                return req[0]
            props = list(t["function"]["parameters"].get("properties", {}).keys())
            return props[0] if props else "input"
    return "input"


def build_exec_directive(tool_name: str, arguments: dict) -> str:
    """Mode C auto-exec: instruct the model to run exactly one capability and
    return only its raw result."""
    args = json.dumps(arguments, ensure_ascii=False)
    return (f"[Directive] Use ONLY your '{tool_name}' capability with these arguments: "
            f"{args}. Execute it and reply with only the raw result of that tool — "
            f"no explanation, no extra text.")


def directive_from_tool_choice(tool_choice: Any, tools: list[dict] | None) -> str:
    """Turn a forced tool_choice into a natural-language directive for the model
    (Mode B). Returns '' when nothing is forced."""
    if not tool_choice or tool_choice in ("auto", "none"):
        return ""
    if tool_choice == "required":
        return "\n\n[Directive] You must use at least one available tool to fulfill this request."
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name")
        if name:
            return f"\n\n[Directive] Use the '{name}' capability to fulfill this request."
    return ""


def extract_tool_args(messages: list[dict[str, Any]], tool_name: str) -> dict:
    """Pull arguments for a forced tool from the latest matching tool_call in
    the message history; fall back to the latest user text under the tool's
    primary parameter."""
    for m in reversed(messages):
        for tc in (m.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) or {}
            if fn.get("name") == tool_name and fn.get("arguments"):
                a = fn["arguments"]
                if isinstance(a, str):
                    try:
                        return json.loads(a)
                    except Exception:
                        return {primary_param(tool_name): a}
                if isinstance(a, dict):
                    return a
    return {primary_param(tool_name): _latest_user_text(messages)}


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    from plugins.ide_gateway.openai_translate import latest_user_text
    return latest_user_text(messages)
