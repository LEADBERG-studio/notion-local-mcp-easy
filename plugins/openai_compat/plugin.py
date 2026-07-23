from __future__ import annotations

from typing import Any

from plugins.ai_shared import (
    health_payload,
    normalize_provider_config,
    post_chat_completion,
    provider_by_name,
    provider_summary,
    resolve_provider_and_model,
    subagent_messages,
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_provider_config(config, plugin_label="OpenAI-compatible")


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)
    return health_payload(context, normalized)


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)

    if tool_name == "openai_compat_list_models":
        provider_name = str(arguments.get("provider", "")).strip() or normalized["default_provider"]
        provider = provider_by_name(normalized, provider_name)
        return {
            "provider": provider_name,
            "models": provider.get("models") or [provider["default_model"]],
            "default_model": provider["default_model"],
            "subagent_model": provider["subagent_model"],
        }

    if tool_name == "openai_compat_describe_provider":
        provider_name = str(arguments.get("provider", "")).strip() or normalized["default_provider"]
        provider = provider_by_name(normalized, provider_name)
        return {
            "provider": provider_summary(provider),
            "defaultProvider": normalized["default_provider"],
            "defaultModel": normalized["default_model"],
            "subagentDefaults": normalized["subagent_defaults"],
        }

    if tool_name == "openai_compat_generate_text":
        prompt = str(arguments.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")
        provider, model = resolve_provider_and_model(
            normalized,
            provider_name=str(arguments.get("provider", "")).strip() or None,
            model_name=str(arguments.get("model", "")).strip() or None,
            for_subagent=False,
        )
        system_prompt = str(arguments.get("system_prompt", "")).strip()
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + [
            {"role": "user", "content": prompt}
        ]
        return post_chat_completion(
            provider,
            messages=messages,
            model=model,
            temperature=float(arguments["temperature"]) if "temperature" in arguments and arguments.get("temperature") != "" else None,
            max_output_tokens=int(arguments["max_output_tokens"]) if "max_output_tokens" in arguments and arguments.get("max_output_tokens") != "" else None,
        )

    if tool_name == "openai_compat_run_subagent":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("openai_compat_run_subagent requires full_access under a trusted profile")
        task = str(arguments.get("task", "")).strip()
        if not task:
            raise ValueError("task is required")
        provider, model = resolve_provider_and_model(
            normalized,
            provider_name=str(arguments.get("provider", "")).strip() or None,
            model_name=str(arguments.get("model", "")).strip() or None,
            for_subagent=True,
        )
        messages = subagent_messages(
            task=task,
            system_prompt=str(arguments.get("system_prompt", "")).strip(),
            context=context,
        )
        defaults = normalized.get("subagent_defaults", {})
        temperature = arguments.get("temperature", defaults.get("temperature"))
        max_output_tokens = arguments.get("max_output_tokens", defaults.get("max_output_tokens"))
        result = post_chat_completion(
            provider,
            messages=messages,
            model=model,
            temperature=float(temperature) if temperature not in {None, ""} else None,
            max_output_tokens=int(max_output_tokens) if max_output_tokens not in {None, ""} else None,
        )
        result["mode"] = "subagent"
        return result

    raise ValueError(f"Unknown OpenAI-compatible tool: {tool_name}")
