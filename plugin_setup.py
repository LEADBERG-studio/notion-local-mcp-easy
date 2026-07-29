from __future__ import annotations

import argparse
import hashlib
import secrets
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

APP_NAME = "NotionMcpEasy"
ALLOWED_SCOPES = {"current", "global"}
ALLOWED_MODES = {"read_only", "full_access"}

def now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")

def default_profile_storage_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME / "workflow-profiles.json"

def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)

def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    return cleaned or hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:12]

def load_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.is_file():
        raise SystemExit(f"plugin.json not found in {plugin_dir}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not str(raw.get("id", "")).strip():
        raise SystemExit("plugin.json must contain an object with field 'id'")
    return raw

def load_profile_storage(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit("Workflow profiles not initialized yet. Start MCP through START.bat first.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"Invalid profile storage: {path}")
    return raw

def active_profile(storage: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(storage.get("activeProfileId", "") or "")
    profiles = storage.get("profiles") if isinstance(storage.get("profiles"), dict) else {}
    profile = profiles.get(profile_id)
    if not isinstance(profile, dict):
        raise SystemExit("No active profile found. Start MCP and select a workspace first.")
    return profile

def local_config_path(plugin_dir: Path, scope: str, profile_id: str | None = None) -> Path:
    if scope == "global":
        return plugin_dir / "plugin.local.global.json"
    if scope != "current":
        raise ValueError("scope must be current or global")
    if not profile_id:
        raise ValueError("profile_id is required for current scope")
    return plugin_dir / f"plugin.local.current.{safe_component(profile_id)}.json"

def existing_config(plugin_dir: Path, scope: str, profile_id: str | None) -> dict[str, Any]:
    raw = load_json(local_config_path(plugin_dir, scope, profile_id), {})
    return raw if isinstance(raw, dict) else {}

def prompt_choice(label: str, choices: list[str], default: str) -> str:
    prompt = "/".join(f"[{item}]" if item == default else item for item in choices)
    while True:
        value = input(f"{label} ({prompt}): ").strip().lower()
        if not value:
            return default
        matches = [item for item in choices if item.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        print("Choose one of: " + ", ".join(choices))

def prompt_text(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print("Value is required.")

def prompt_bool(label: str, default: bool = True) -> bool:
    return prompt_choice(label, ["yes", "no"], "yes" if default else "no") == "yes"

def generate_ide_gateway_api_key() -> str:
    return "ideg_" + secrets.token_urlsafe(32)


def collect_sqlite_config(existing: dict[str, Any]) -> dict[str, Any]:
    print("SQLite setup: add workspace-relative database file connections.")
    connections = []
    old = existing.get("connections") if isinstance(existing.get("connections"), list) else []
    while True:
        prev = old[len(connections)] if len(connections) < len(old) and isinstance(old[len(connections)], dict) else {}
        name = prompt_text("Connection alias", str(prev.get("name", "")) or ("main" if not connections else ""), required=not connections)
        if not name:
            break
        path = prompt_text("SQLite file path relative to workspace", str(prev.get("path", "")), required=True)
        connections.append({"name": name, "path": path})
        if not prompt_bool("Add another SQLite connection?", False):
            break
    return {"connections": connections}

def collect_postgres_config(existing: dict[str, Any]) -> dict[str, Any]:
    print("PostgreSQL setup: use password_env, never store passwords in plugin config.")
    connections = []
    old = existing.get("connections") if isinstance(existing.get("connections"), list) else []
    while True:
        prev = old[len(connections)] if len(connections) < len(old) and isinstance(old[len(connections)], dict) else {}
        name = prompt_text("Connection alias", str(prev.get("name", "")) or ("main" if not connections else ""), required=not connections)
        if not name:
            break
        record = {"name": name, "database": prompt_text("Database name", str(prev.get("database", "")), required=True)}
        for key, label in [("host", "Host"), ("port", "Port"), ("user", "User"), ("password_env", "Password env var"), ("sslmode", "SSL mode")]:
            value = prompt_text(label, str(prev.get(key, "")))
            if value:
                record[key] = value
        connections.append(record)
        if not prompt_bool("Add another PostgreSQL connection?", False):
            break
    return {"connections": connections}

def collect_openai_compat_config(existing: dict[str, Any]) -> dict[str, Any]:
    print("OpenAI-compatible setup: API keys are referenced by environment variable name.")
    providers = []
    old = existing.get("providers") if isinstance(existing.get("providers"), list) else []
    while True:
        prev = old[len(providers)] if len(providers) < len(old) and isinstance(old[len(providers)], dict) else {}
        name = prompt_text("Provider alias", str(prev.get("name", "")) or ("main" if not providers else ""), required=not providers)
        if not name:
            break
        models_default = ",".join(str(item) for item in prev.get("models", []) if str(item).strip()) if isinstance(prev.get("models"), list) else ""
        models = [item.strip() for item in prompt_text("Models comma-separated", models_default).split(",") if item.strip()]
        default_model = prompt_text("Default model", str(prev.get("default_model", "")) or (models[0] if models else ""), required=True)
        if default_model not in models:
            models.insert(0, default_model)
        providers.append({
            "name": name,
            "base_url": prompt_text("Base URL before /chat/completions", str(prev.get("base_url", "")), required=True).rstrip("/"),
            "api_key_env": prompt_text("API key env var", str(prev.get("api_key_env", "")), required=True),
            "models": models,
            "default_model": default_model,
            "subagent_model": prompt_text("Subagent model", str(prev.get("subagent_model", "")) or default_model, required=True),
        })
        if not prompt_bool("Add another provider?", False):
            break
    return {
        "providers": providers,
        "default_provider": prompt_text("Default provider alias", str(existing.get("default_provider", "")) or providers[0]["name"], required=True),
        "default_model": prompt_text("Default model override", str(existing.get("default_model", "")) or providers[0]["default_model"], required=True),
        "subagent_defaults": existing.get("subagent_defaults") if isinstance(existing.get("subagent_defaults"), dict) else {"temperature": 0.2, "max_output_tokens": 800},
    }

def collect_ide_gateway_config(existing: dict[str, Any]) -> dict[str, Any]:
    print("IDE Gateway setup: OpenAI-compatible bridge from IDE to the active MCP model.")
    config = dict(existing)
    api_key = str(config.get("default_api_key", "")).strip()
    if not api_key:
        api_key = generate_ide_gateway_api_key()
        config["default_api_key"] = api_key
        print("Generated local IDE Gateway API key and saved it to the plugin-local config.")
    else:
        print("Keeping existing local IDE Gateway API key from the plugin-local config.")

    autostart = prompt_bool("Enable endpoint autostart (preferred port 8787)?", True)
    config["autostart"] = autostart
    if autostart:
        port = prompt_text("Preferred port (default 8787)", str(config.get("default_port", 8787)))
        try:
            config["default_port"] = int(port)
        except ValueError:
            config["default_port"] = 8787

    # Gateway mode: sandbox (default, resident egress) | bridge | external
    gw_mode = prompt_choice(
        "Gateway mode",
        ["sandbox", "bridge", "external"],
        str(config.get("gateway_mode", "sandbox")) or "sandbox")
    config["gateway_mode"] = gw_mode

    if gw_mode == "sandbox":
        print("Sandbox mode (по умолчанию): LLM-шлюз в sandbox + Tunnellio туннель.")
        print("При подключении плагина создаётся домен Tunnellio.")
        use_own_token = prompt_bool("Использовать свой Tunnellio API token (платный тариф)?", False)
        if use_own_token:
            tnl_token = prompt_text("Tunnellio API token", "")
            domain_type = prompt_choice("Тип домена", ["ephemeral", "custom"], "ephemeral")
            hostname = ""
            if domain_type == "custom":
                hostname = prompt_text("Имя постоянного домена (e.g. my-sandbox)", "")
                config["tunnellio_custom_hostname"] = hostname
        else:
            tnl_token = ""  # будет использовать зашитый дефолтный
            hostname = ""  # ephemeral
            print("Будет создан временный домен (жизнь 1 сутки, бесплатный).")
        # Provision domain via API
        try:
            from plugins.ide_gateway.backend import provision_sandbox_domain
            print("Создаю домен через Tunnellio API...")
            domain = provision_sandbox_domain(
                token=tnl_token, hostname=hostname, local_port=config.get("default_port", 8787))
            config["tunnellio_token"] = tnl_token
            config["tunnellio_domain_id"] = domain["domain_id"]
            config["tunnellio_key_id"] = domain["key_id"]
            config["tunnellio_public_url"] = domain["public_url"]
            config["tunnellio_ssh_host"] = domain["ssh_host"]
            config["tunnellio_ssh_port"] = domain["ssh_port"]
            config["tunnellio_ssh_user"] = domain["ssh_user"]
            config["tunnellio_remote_hostname"] = domain["remote_hostname"]
            config["tunnellio_private_key"] = domain["private_key"]
            config["tunnellio_mode"] = domain["mode"]
            config["tunnellio_hostname"] = hostname
            print(f"Домен создан: {domain['public_url']}")
            print(f"Режим: {domain['mode']}")
            config["upstream_base_url"] = domain["public_url"].rstrip("/") + "/v1"
            config["upstream_api_key"] = ""
            config["upstream_model"] = ""
        except Exception as exc:
            print(f"Ошибка создания домена: {exc}")
            print("Продолжаю без туннеля — модель создаст его вручную.")
            config["upstream_base_url"] = ""
            config["upstream_api_key"] = ""
            config["upstream_model"] = ""
    elif gw_mode == "external":
        print("External mode: the gateway worker calls an OpenAI-compatible")
        print("upstream provider directly (Ollama, OpenAI, etc.).")
        base = prompt_text("Upstream base URL (e.g. http://127.0.0.1:11434/v1)",
                           str(config.get("upstream_base_url", "")))
        config["upstream_base_url"] = base.rstrip("/")
        key = prompt_text("Upstream API key (empty for local unauthenticated)",
                          str(config.get("upstream_api_key", "")))
        config["upstream_api_key"] = key
        umodel = prompt_text("Upstream model id",
                             str(config.get("upstream_model", "")))
        config["upstream_model"] = umodel
    else:
        print("Bridge mode: the model runs bridge_step.py poll loop via run_program.")
        config["upstream_base_url"] = ""
        config["upstream_api_key"] = ""
        config["upstream_model"] = ""

    setup_mode = prompt_choice("Endpoint defaults", ["default", "custom"], "default")
    if setup_mode == "default":
        return config

    model = prompt_text("Default model id", str(config.get("default_model_id", "ide-gateway")) or "ide-gateway")
    if model:
        config["default_model_id"] = model
    first = prompt_text("Port range start", str((config.get("port_range") or [8787, 8899])[0]))
    last = prompt_text("Port range end", str((config.get("port_range") or [8787, 8899])[1]))
    if first and last:
        config["port_range"] = [int(first), int(last)]
    timeout = prompt_text("Request timeout seconds", str(config.get("request_timeout_seconds", 300)))
    if timeout:
        config["request_timeout_seconds"] = int(timeout)
    embeddings = prompt_choice("Embeddings mode", ["fallback", "off"], "fallback")
    config["embeddings_mode"] = embeddings
    disabled = prompt_text("Disabled tools (comma-separated, empty for none)", str(config.get("disabled_tools", "")))
    config["disabled_tools"] = disabled
    return config

def collect_config(plugin_id: str, existing: dict[str, Any]) -> dict[str, Any]:
    base = existing.get("config") if isinstance(existing.get("config"), dict) else {}
    if plugin_id == "sqlite":
        return collect_sqlite_config(base)
    if plugin_id == "postgres":
        return collect_postgres_config(base)
    if plugin_id == "openai_compat":
        return collect_openai_compat_config(base)
    if plugin_id == "ide_gateway":
        return collect_ide_gateway_config(base)
    raw = prompt_text("Config JSON", json.dumps(base, ensure_ascii=False))
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise SystemExit("Config JSON must be an object")
    return parsed

def load_plugin_entrypoint(plugin_dir: Path, manifest: dict[str, Any]):
    entrypoint = str(manifest.get("entrypoint", "")).strip()
    if not entrypoint:
        return None
    path = plugin_dir / entrypoint
    if not path.is_file():
        return None
    root = plugin_dir.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(f"plugin_setup_validate_{manifest['id']}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def validate_config(plugin_dir: Path, manifest: dict[str, Any], config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    module = load_plugin_entrypoint(plugin_dir, manifest)
    method = getattr(module, "validate_config", None) if module is not None else None
    if callable(method):
        result = method(config, context)
        if isinstance(result, dict):
            return result
    return config

def write_local_plugin_config(plugin_dir: Path, scope: str, requested_mode: str, config: dict[str, Any], profile_storage_path: Path | None = None) -> Path:
    plugin_dir = plugin_dir.resolve()
    manifest = load_manifest(plugin_dir)
    plugin_id = str(manifest["id"])
    if scope not in ALLOWED_SCOPES:
        raise SystemExit("scope must be current or global")
    if requested_mode not in ALLOWED_MODES:
        raise SystemExit("requested mode must be read_only or full_access")
    supported = set(manifest.get("supported_modes") or [])
    if requested_mode not in supported:
        raise SystemExit(f"Plugin {plugin_id!r} does not support mode {requested_mode!r}")
    profile_storage_path = profile_storage_path or default_profile_storage_path()
    profile = {}
    profile_id = ""
    if scope == "current":
        profile = active_profile(load_profile_storage(profile_storage_path))
        profile_id = str(profile.get("profileId", "") or "")
    context = {
        "profileId": profile.get("profileId"),
        "pathSlot": profile.get("pathSlot"),
        "workspacePath": profile.get("workspacePath"),
        "accessMode": profile.get("accessMode"),
        "environmentMode": "CUSTOM" if scope == "current" else profile.get("environmentMode"),
        "attachScope": scope,
        "requestedMode": requested_mode,
        "effectiveMode": requested_mode,
        "configSource": f"plugin_local_{scope}",
        "pluginConfig": config,
    }
    normalized = validate_config(plugin_dir, manifest, config, context)
    payload = {
        "schemaVersion": 1,
        "pluginId": plugin_id,
        "scope": scope,
        "requestedMode": requested_mode,
        "config": normalized,
        "updatedAt": now_iso(),
    }
    if scope == "current":
        payload["profileId"] = profile_id
        payload["workspacePath"] = str(profile.get("workspacePath", ""))
        payload["pathSlot"] = profile.get("pathSlot", 0)
    path = local_config_path(plugin_dir, scope, profile_id or None)
    atomic_write_json(path, payload)
    return path

def remove_local_plugin_config(plugin_dir: Path, scope: str, profile_storage_path: Path | None = None) -> Path:
    plugin_dir = plugin_dir.resolve()
    load_manifest(plugin_dir)
    profile_id = ""
    if scope == "current":
        profile_id = str(active_profile(load_profile_storage(profile_storage_path or default_profile_storage_path())).get("profileId", "") or "")
    path = local_config_path(plugin_dir, scope, profile_id or None)
    path.unlink(missing_ok=True)
    return path

def print_status(plugin_dir: Path, profile_storage_path: Path | None = None) -> None:
    plugin_dir = plugin_dir.resolve()
    manifest = load_manifest(plugin_dir)
    print(f"Plugin: {manifest['id']} ({manifest.get('display_name', manifest['id'])})")
    print(f"Plugin dir: {plugin_dir}")
    candidates = [plugin_dir / "plugin.local.global.json"]
    try:
        profile = active_profile(load_profile_storage(profile_storage_path or default_profile_storage_path()))
        candidates.append(local_config_path(plugin_dir, "current", str(profile.get("profileId", "") or "")))
        print(f"Active profile: {profile.get('profileId')} | {profile.get('workspacePath')}")
    except SystemExit as exc:
        print(str(exc))
    found = False
    for path in candidates:
        if path.is_file():
            found = True
            payload = load_json(path, {})
            print(f"- {path.name}: scope={payload.get('scope')} requestedMode={payload.get('requestedMode')} updatedAt={payload.get('updatedAt')}")
    if not found:
        print("No local plugin config files found.")

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plugin-local setup helper")
    parser.add_argument("--plugin-dir", default=".")
    parser.add_argument("--action", choices=["setup", "enable", "disable", "status"], default="setup")
    parser.add_argument("--scope", choices=sorted(ALLOWED_SCOPES))
    parser.add_argument("--requested-mode", choices=sorted(ALLOWED_MODES))
    parser.add_argument("--config-json")
    parser.add_argument("--profile-storage")
    return parser.parse_args(argv)

def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    plugin_dir = Path(args.plugin_dir).resolve()
    manifest = load_manifest(plugin_dir)
    plugin_id = str(manifest["id"])
    profile_storage = Path(args.profile_storage).expanduser().resolve() if args.profile_storage else None
    if args.action == "status":
        print_status(plugin_dir, profile_storage)
        return 0
    if args.action == "disable":
        scope = args.scope or prompt_choice("Disable scope", ["current", "global"], "current")
        path = remove_local_plugin_config(plugin_dir, scope, profile_storage)
        print(f"Disabled plugin '{plugin_id}' for {scope} scope.")
        print(f"Removed local config if present: {path}")
        print("Restart MCP to rebuild the plugin registry.")
        return 0
    default_mode = "full_access" if "full_access" in set(manifest.get("supported_modes") or []) else "read_only"
    if args.action == "enable":
        scope = args.scope or "current"
        requested_mode = args.requested_mode or default_mode
    else:
        scope = args.scope or prompt_choice("Install scope", ["current", "global"], "current")
        requested_mode = args.requested_mode or prompt_choice("Requested mode", ["read_only", "full_access"], default_mode)

    if args.config_json is not None:
        config = json.loads(args.config_json or "{}")
        if not isinstance(config, dict):
            raise SystemExit("--config-json must decode to an object")
    else:
        profile_id = ""
        if scope == "current":
            profile_id = str(active_profile(load_profile_storage(profile_storage or default_profile_storage_path())).get("profileId", "") or "")
        existing = existing_config(plugin_dir, scope, profile_id or None)
        if args.action == "enable":
            config = collect_config(plugin_id, existing)
        else:
            config = collect_config(plugin_id, existing)
    path = write_local_plugin_config(plugin_dir, scope, requested_mode, config, profile_storage)
    print(f"Enabled plugin '{plugin_id}' for {scope} scope.")
    print(f"Requested mode: {requested_mode}")
    print(f"Local config: {path}")
    print("Restart MCP to rebuild the plugin registry.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
