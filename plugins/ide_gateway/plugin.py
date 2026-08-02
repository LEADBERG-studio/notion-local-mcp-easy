from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from plugins.ide_gateway.state import (
    normalize_config,
    read_logs,
    rotate_token,
    show_config,
    start_endpoint,
    stop_endpoint,
    endpoint_status,
    bridge_prompt,
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
    attached in full_access. Autostarts the endpoint on the preferred
    port (8787). For sandbox mode, also checks/re-provisions the Tunnellio
    domain before starting."""
    config = normalize_config(context.get("pluginConfig") or {}, context)
    result: dict[str, Any] = {"provider": "ide_gateway"}
    if context.get("effectiveMode") != "full_access":
        result["autostart"] = "skipped (full_access required)"
        return result

    # Sandbox mode: choose the public hostname before the model enters the sandbox.
    # If an earlier endpoint state has one, reuse it so the local IDE/proxy sees
    # a stable URL across restarts. If the 1-day domain expired physically, the
    # sandbox TCP bridge recreates the same hostname during bootstrap.
    if config.get("gateway_mode") == "sandbox":
        try:
            from plugins.ide_gateway.backend import ensure_domain
            local_port = int(config.get("default_port", 8787))
            if not config.get("tunnellio_hostname"):
                workspace = Path(str(context.get("workspacePath", ""))).resolve()
                previous_state = workspace / "temp" / "ide_gateway_runtime" / "endpoints" / "default.json"
                if previous_state.is_file():
                    try:
                        prev = json.loads(previous_state.read_text(encoding="utf-8-sig"))
                        if prev.get("tunnellio_hostname"):
                            config["tunnellio_hostname"] = str(prev["tunnellio_hostname"]).strip()
                        elif prev.get("tunnellio_public_url"):
                            from urllib.parse import urlsplit
                            host = urlsplit(str(prev["tunnellio_public_url"])).hostname or ""
                            suffix = ".tunnellio.site"
                            config["tunnellio_hostname"] = host[:-len(suffix)] if host.endswith(suffix) else host
                    except Exception:
                        pass
            config = ensure_domain(config, local_port=local_port)
            result["tunnel_url"] = config.get("tunnellio_public_url", "")
            result["tunnel_hostname"] = config.get("tunnellio_hostname", "")
            result["tunnel_status"] = "hostname_reserved" if config.get("tunnellio_hostname") else "failed"
        except Exception as exc:
            result["tunnel_status"] = "failed"
            result["tunnel_error"] = str(exc)

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

    if tool_name == "ide_gateway_get_logs":
        return read_logs(arguments, context, config)

    if tool_name == "ide_gateway_rotate_token":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("ide_gateway_rotate_token requires full_access under a trusted profile")
        return rotate_token(arguments, context, config)

    if tool_name == "ide_gateway_bridge_prompt":
        return bridge_prompt(arguments, context, config)

    raise ValueError(f"Unknown ide_gateway tool: {tool_name}")
