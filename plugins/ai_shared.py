from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


def normalize_provider_config(config: dict[str, Any], *, plugin_label: str) -> dict[str, Any]:
    providers_raw = config.get("providers") if isinstance(config.get("providers"), list) else []
    providers: list[dict[str, Any]] = []
    seen = set()
    for item in providers_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        base_url = str(item.get("base_url", "")).strip().rstrip("/")
        api_key_env = str(item.get("api_key_env", "")).strip()
        models = [str(model).strip() for model in item.get("models", []) if str(model).strip()]
        default_model = str(item.get("default_model", "")).strip() or (models[0] if models else "")
        subagent_model = str(item.get("subagent_model", "")).strip() or default_model
        if not name or not base_url or not api_key_env or not default_model:
            raise ValueError(
                f"{plugin_label} plugin requires providers with name, base_url, api_key_env, and default_model"
            )
        if name in seen:
            raise ValueError(f"Duplicate {plugin_label} provider alias: {name}")
        seen.add(name)
        providers.append(
            {
                "name": name,
                "base_url": base_url,
                "api_key_env": api_key_env,
                "models": models,
                "default_model": default_model,
                "subagent_model": subagent_model,
            }
        )
    if not providers:
        raise ValueError(f"{plugin_label} plugin requires config.providers with at least one provider")
    default_provider = str(config.get("default_provider", "")).strip() or providers[0]["name"]
    provider_names = {provider["name"] for provider in providers}
    if default_provider not in provider_names:
        raise ValueError(f"Unknown default provider: {default_provider}")
    default_model = str(config.get("default_model", "")).strip()
    if not default_model:
        default_model = provider_by_name({"providers": providers}, default_provider)["default_model"]
    subagent_defaults = config.get("subagent_defaults") if isinstance(config.get("subagent_defaults"), dict) else {}
    return {
        "providers": providers,
        "default_provider": default_provider,
        "default_model": default_model,
        "subagent_defaults": {
            "temperature": subagent_defaults.get("temperature", 0.2),
            "max_output_tokens": subagent_defaults.get("max_output_tokens", 800),
        },
    }


def provider_by_name(config: dict[str, Any], name: str) -> dict[str, Any]:
    for provider in config.get("providers", []):
        if provider.get("name") == name:
            return provider
    raise ValueError(f"Unknown provider alias: {name}")


def provider_summary(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": provider["name"],
        "base_url": provider["base_url"],
        "api_key_env": provider["api_key_env"],
        "apiKeyPresent": bool(os.environ.get(provider["api_key_env"], "")),
        "models": list(provider.get("models") or []),
        "default_model": provider["default_model"],
        "subagent_model": provider["subagent_model"],
    }


def health_payload(context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    default_provider = str(config.get("default_provider", "")).strip()
    return {
        "workspace": str(context.get("workspacePath", "")),
        "defaultProvider": default_provider,
        "defaultModel": str(config.get("default_model", "")),
        "providers": [provider_summary(provider) for provider in config.get("providers", [])],
    }


def resolve_provider_and_model(
    config: dict[str, Any],
    *,
    provider_name: str | None,
    model_name: str | None,
    for_subagent: bool,
) -> tuple[dict[str, Any], str]:
    selected_provider = provider_by_name(
        config,
        provider_name or str(config.get("default_provider", "")).strip(),
    )
    if model_name:
        return selected_provider, model_name
    if for_subagent:
        return selected_provider, str(selected_provider.get("subagent_model") or config.get("default_model", ""))
    return selected_provider, str(config.get("default_model", "") or selected_provider.get("default_model", ""))


def _bearer_token(provider: dict[str, Any]) -> str:
    env_name = provider["api_key_env"]
    token = os.environ.get(env_name, "")
    if not token:
        raise ValueError(f"Environment variable not set: {env_name}")
    return token


def post_chat_completion(
    provider: dict[str, Any],
    *,
    messages: list[dict[str, str]],
    model: str,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_output_tokens is not None:
        payload["max_tokens"] = int(max_output_tokens)
    request = urllib.request.Request(
        provider["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_bearer_token(provider)}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = json.loads(response.read().decode("utf-8"))
    choice = ((raw.get("choices") or [{}])[0].get("message") or {})
    content = choice.get("content", "")
    return {
        "provider": provider["name"],
        "model": model,
        "output": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        "usage": raw.get("usage") or {},
    }


def subagent_messages(*, task: str, system_prompt: str, context: dict[str, Any]) -> list[dict[str, str]]:
    enforced_system = (
        "You are a delegated subagent running inside notion-local-mcp-easy. "
        f"Active workspace: {context.get('workspacePath', '')}. "
        f"Access mode: {context.get('accessMode', 'file_only')}. "
        f"Effective mode: {context.get('effectiveMode', 'read_only')}. "
        "Do not assume permissions beyond the provided profile and effective mode. "
        "Return only the requested task result."
    )
    system_text = enforced_system if not system_prompt else enforced_system + "\n\n" + system_prompt
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": task},
    ]
