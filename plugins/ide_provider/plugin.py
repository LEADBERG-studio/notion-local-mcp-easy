from __future__ import annotations

from typing import Any

from plugins.ide_provider.state import (
    normalize_config,
    read_logs,
    rotate_token,
    show_config,
    start_endpoint,
    stop_endpoint,
    endpoint_status,
)
from plugins.ide_provider.queue import (
    wait_request,
    send_response,
    fail_request,
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_config(config, context)


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = normalize_config(context.get("pluginConfig") or {}, context)
    return {
        "provider": "ide_provider",
        "ok": True,
        "defaultHost": config["default_host"],
        "portRange": config["port_range"],
    }


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = normalize_config(context.get("pluginConfig") or {}, context)

    if tool_name == "ide_provider_start":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_provider_start requires full_access under a trusted profile")
        return start_endpoint(arguments, context, config)

    if tool_name == "ide_provider_stop":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_provider_stop requires full_access under a trusted profile")
        return stop_endpoint(arguments, context, config)

    if tool_name == "ide_provider_status":
        return endpoint_status(arguments, context, config)

    if tool_name == "ide_provider_show_config":
        return show_config(arguments, context, config)

    if tool_name == "ide_provider_wait_request":
        return wait_request(arguments, context, config)

    if tool_name == "ide_provider_send_response":
        return send_response(arguments, context, config)

    if tool_name == "ide_provider_fail_request":
        return fail_request(arguments, context, config)

    if tool_name == "ide_provider_get_logs":
        return read_logs(arguments, context, config)

    if tool_name == "ide_provider_rotate_token":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_provider_rotate_token requires full_access under a trusted profile")
        return rotate_token(arguments, context, config)

    raise ValueError(f"Unknown ide_provider tool: {tool_name}")
