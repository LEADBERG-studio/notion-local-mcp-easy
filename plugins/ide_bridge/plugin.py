from __future__ import annotations

from typing import Any

from plugins.ide_bridge.state import (
    normalize_config,
    read_logs,
    rotate_token,
    show_config,
    start_endpoint,
    stop_endpoint,
    endpoint_status,
    bridge_prompt,
)
from plugins.ide_bridge.queue import (
    wait_request,
    send_response,
    fail_request,
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_config(config, context)


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = normalize_config(context.get("pluginConfig") or {}, context)
    return {
        "provider": "ide_bridge",
        "ok": True,
        "defaultHost": config["default_host"],
        "defaultPort": config["default_port"],
        "portRange": config["port_range"],
        "autostart": config["autostart"],
        "endpoints": [
            "/v1/chat/completions (stream + non-stream)",
            "/v1/responses (stream + non-stream, background, cancel, input_items)",
            "/v1/completions (legacy)",
            "/v1/models",
            "/v1/tools",
            "/v1/files (upload, list, get, content, delete)",
            "/v1/images/generations, /v1/images/edits",
            "/v1/audio/speech, /v1/audio/transcriptions, /v1/audio/translations",
            "/v1/embeddings (local fallback)",
            "/v1/moderations (local heuristic)",
        ],
    }


def startup(context: dict[str, Any]) -> dict[str, Any]:
    """Autostart the local queue worker on the preferred port (8797)."""
    config = normalize_config(context.get("pluginConfig") or {}, context)
    result: dict[str, Any] = {"provider": "ide_bridge"}
    if context.get("effectiveMode") != "full_access":
        result["autostart"] = "skipped (full_access required)"
        return result

    # Endpoint autostart
    if config.get("autostart", True):
        try:
            ep = start_endpoint({"name": "default"}, context, config)
            result["endpoint"] = ep.get("status", "unknown")
            result["base_url"] = ep.get("base_url")
            result["model"] = ep.get("model")
        except Exception as exc:
            result["endpoint"] = "failed"
            result["endpoint_error"] = str(exc)
    else:
        result["endpoint"] = "skipped (disabled in config)"

    return result


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = normalize_config(context.get("pluginConfig") or {}, context)

    if tool_name == "ide_bridge_start":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_bridge_start requires full_access under a trusted profile")
        return start_endpoint(arguments, context, config)

    if tool_name == "ide_bridge_stop":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_bridge_stop requires full_access under a trusted profile")
        return stop_endpoint(arguments, context, config)

    if tool_name == "ide_bridge_status":
        return endpoint_status(arguments, context, config)

    if tool_name == "ide_bridge_show_config":
        return show_config(arguments, context, config)

    if tool_name == "ide_bridge_wait_request":
        return wait_request(arguments, context, config)

    if tool_name == "ide_bridge_send_response":
        return send_response(arguments, context, config)

    if tool_name == "ide_bridge_fail_request":
        return fail_request(arguments, context, config)

    if tool_name == "ide_bridge_get_logs":
        return read_logs(arguments, context, config)

    if tool_name == "ide_bridge_rotate_token":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_bridge_rotate_token requires full_access under a trusted profile")
        return rotate_token(arguments, context, config)

    if tool_name == "ide_bridge_bridge_prompt":
        return bridge_prompt(arguments, context, config)

    raise ValueError(f"Unknown ide_bridge tool: {tool_name}")
