from __future__ import annotations

from typing import Any

from plugins.ide_gateway.state import (
    normalize_config,
    read_logs,
    rotate_token,
    show_config,
    start_endpoint,
    stop_endpoint,
    endpoint_status,
    start_responder,
    stop_responder,
    responder_status,
    read_responder_logs,
)
from plugins.ide_gateway.queue import (
    wait_request,
    send_response,
    fail_request,
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_config(config, context)


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = normalize_config(context.get("pluginConfig") or {}, context)
    return {
        "provider": "ide_gateway",
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
    """Called by PluginManager after tools are registered, if the plugin is
    attached in full_access. Autostarts the default endpoint on the preferred
    port (8787) and the autonomous responder, unless disabled in config."""
    config = normalize_config(context.get("pluginConfig") or {}, context)
    result: dict[str, Any] = {"provider": "ide_gateway"}
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

    # Responder autostart (only meaningful if the endpoint is up)
    if config.get("responder_enabled", True) and config.get("responder_autostart", True):
        try:
            rsp = start_responder({"name": "default"}, context, config)
            result["responder"] = rsp.get("status", "unknown")
            result["upstream_type"] = rsp.get("upstream_type")
        except Exception as exc:
            result["responder"] = "failed"
            result["responder_error"] = str(exc)
    else:
        result["responder"] = "skipped (disabled in config)"

    return result


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = normalize_config(context.get("pluginConfig") or {}, context)

    if tool_name == "ide_gateway_start":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_gateway_start requires full_access under a trusted profile")
        return start_endpoint(arguments, context, config)

    if tool_name == "ide_gateway_stop":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_gateway_stop requires full_access under a trusted profile")
        return stop_endpoint(arguments, context, config)

    if tool_name == "ide_gateway_status":
        return endpoint_status(arguments, context, config)

    if tool_name == "ide_gateway_show_config":
        return show_config(arguments, context, config)

    if tool_name == "ide_gateway_wait_request":
        return wait_request(arguments, context, config)

    if tool_name == "ide_gateway_send_response":
        return send_response(arguments, context, config)

    if tool_name == "ide_gateway_fail_request":
        return fail_request(arguments, context, config)

    if tool_name == "ide_gateway_get_logs":
        return read_logs(arguments, context, config)

    if tool_name == "ide_gateway_rotate_token":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_gateway_rotate_token requires full_access under a trusted profile")
        return rotate_token(arguments, context, config)

    if tool_name == "ide_gateway_responder_start":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_gateway_responder_start requires full_access under a trusted profile")
        return start_responder(arguments, context, config)

    if tool_name == "ide_gateway_responder_stop":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_gateway_responder_stop requires full_access under a trusted profile")
        return stop_responder(arguments, context, config)

    if tool_name == "ide_gateway_responder_status":
        return responder_status(arguments, context, config)

    if tool_name == "ide_gateway_responder_logs":
        return read_responder_logs(arguments, context, config)

    raise ValueError(f"Unknown ide_gateway tool: {tool_name}")
