from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from profiles import (
    attach_plugin_record,
    compute_environment_mode,
    detach_plugin_record,
    load_profiles,
    normalize_profile,
    resolve_profile_storage_path,
    save_profiles,
)

PLUGIN_DIRNAME = "plugins"
ALLOWED_PLUGIN_SCOPES = {"current", "global"}
ALLOWED_PLUGIN_REQUESTED_MODES = {"read_only", "full_access"}
ALLOWED_TOOL_MODES = {"read_only", "full_access"}



class PluginError(ValueError):
    pass


def discover_plugin_manifests(plugins_root: Path) -> dict[str, dict[str, Any]]:
    manifests: dict[str, dict[str, Any]] = {}
    if not plugins_root.is_dir():
        return manifests
    for item in sorted(plugins_root.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir():
            continue
        manifest_path = item / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            manifests[item.name] = {
                "id": item.name,
                "display_name": item.name,
                "status": "invalid_manifest",
                "manifestPath": manifest_path,
                "error": f"invalid manifest: {exc}",
            }
            continue
        try:
            manifest = validate_plugin_manifest(raw, manifest_path)
        except Exception as exc:
            fallback_id = str(raw.get("id", item.name)).strip() if isinstance(raw, dict) else item.name
            fallback_name = (
                str(raw.get("display_name", fallback_id or item.name)).strip()
                if isinstance(raw, dict)
                else item.name
            )
            resolved_key = fallback_id or item.name
            manifests[resolved_key] = {
                "id": resolved_key,
                "display_name": fallback_name or resolved_key,
                "status": "invalid_manifest",
                "manifestPath": manifest_path,
                "error": f"invalid manifest: {exc}",
            }
            continue
        manifests[manifest["id"]] = manifest
    return manifests


def validate_plugin_manifest(raw: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PluginError("plugin manifest must be a JSON object")
    plugin_id = str(raw.get("id", "")).strip()
    if not plugin_id:
        raise PluginError("plugin manifest field 'id' is required")
    display_name = str(raw.get("display_name", plugin_id)).strip() or plugin_id
    version = str(raw.get("version", "0.0.0")).strip() or "0.0.0"
    entrypoint = str(raw.get("entrypoint", "")).strip()
    if not entrypoint:
        raise PluginError("plugin manifest field 'entrypoint' is required")
    supported_modes = raw.get("supported_modes")
    if not isinstance(supported_modes, list) or not supported_modes:
        raise PluginError("plugin manifest field 'supported_modes' must be a non-empty list")
    for mode in supported_modes:
        if mode not in ALLOWED_TOOL_MODES:
            raise PluginError("supported_modes must contain only 'read_only' or 'full_access'")
    install_scope_support = str(raw.get("install_scope_support", "both")).strip() or "both"
    if install_scope_support not in {"current", "global", "both"}:
        raise PluginError("install_scope_support must be 'current', 'global', or 'both'")
    tools = raw.get("tools")
    if not isinstance(tools, list):
        raise PluginError("plugin manifest field 'tools' must be a list")
    normalized_tools = []
    for tool in tools:
        normalized_tools.append(validate_tool_descriptor(tool, plugin_id))
    entrypoint_path = manifest_path.parent / entrypoint
    return {
        "id": plugin_id,
        "display_name": display_name,
        "version": version,
        "entrypoint": entrypoint,
        "entrypointPath": entrypoint_path,
        "supported_modes": supported_modes,
        "capabilities": list(raw.get("capabilities") or []),
        "config_schema_version": int(raw.get("config_schema_version", 1) or 1),
        "required_env": list(raw.get("required_env") or []),
        "dependencies": list(raw.get("dependencies") or []),
        "required": bool(raw.get("required", False)),
        "install_scope_support": install_scope_support,
        "tools": normalized_tools,
        "manifestPath": manifest_path,
    }


def validate_tool_descriptor(raw: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PluginError("tool descriptor must be an object")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise PluginError("tool descriptor field 'name' is required")
    if not name.startswith(f"{plugin_id}_"):
        raise PluginError(f"tool '{name}' must use namespace root '{plugin_id}_'")
    mode_required = str(raw.get("mode_required", "read_only")).strip() or "read_only"
    if mode_required not in ALLOWED_TOOL_MODES:
        raise PluginError("mode_required must be 'read_only' or 'full_access'")
    input_schema = raw.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(input_schema, dict):
        raise PluginError("input_schema must be an object")
    output_schema = raw.get("output_schema") or {"type": "object"}
    if not isinstance(output_schema, dict):
        raise PluginError("output_schema must be an object")
    return {
        "name": name,
        "title": str(raw.get("title", name)).strip() or name,
        "description": str(raw.get("description", "")).strip() or name,
        "capability": str(raw.get("capability", "plugin.misc")).strip() or "plugin.misc",
        "mode_required": mode_required,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "handler_ref": str(raw.get("handler_ref", name)).strip() or name,
        "safety_tags": list(raw.get("safety_tags") or []),
        "diagnostic_visibility": str(raw.get("diagnostic_visibility", "full")).strip() or "full",
    }


def load_plugin_module(manifest: dict[str, Any]) -> ModuleType:
    entrypoint_path = Path(manifest["entrypointPath"])
    if not entrypoint_path.is_file():
        raise PluginError(f"plugin entrypoint not found: {entrypoint_path}")
    module_name = f"notion_local_mcp_plugin_{manifest['id']}"
    spec = importlib.util.spec_from_file_location(module_name, entrypoint_path)
    if spec is None or spec.loader is None:
        raise PluginError(f"cannot load plugin entrypoint: {entrypoint_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_plugin_method(module: ModuleType, method_name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(module, method_name, None)
    if method is None or not callable(method):
        raise PluginError(f"plugin entrypoint must define callable '{method_name}'")
    return method(*args, **kwargs)


def build_legacy_profile_context(base_dir: Path, allow_commands: bool) -> dict[str, Any]:
    profile = {
        "profileId": "legacy-synthetic",
        "pathSlot": 0,
        "workspacePath": str(base_dir.resolve()),
        "accessMode": "trusted" if allow_commands else "file_only",
        "environmentMode": "DEFAULT",
        "displayName": base_dir.name or str(base_dir.resolve()),
        "createdAt": "",
        "updatedAt": "",
        "metadata": {
            "createdFrom": "legacy-runtime",
            "lastSelectedAt": "",
            "lastKnownGood": True,
            "notes": "synthetic runtime profile; no profile storage configured",
        },
        "plugins": {},
    }
    return {
        "profileStoragePath": None,
        "storage": None,
        "activeProfile": profile,
        "activeProfileId": profile["profileId"],
        "globalPlugins": {},
        "profileMode": "legacy",
    }


def load_profile_context_from_env(base_dir: Path, allow_commands: bool) -> dict[str, Any]:
    storage_env = os.environ.get("MCP_PROFILE_STORAGE", "").strip()
    profile_id = os.environ.get("MCP_PROFILE_ID", "").strip()
    if not storage_env or not profile_id:
        return build_legacy_profile_context(base_dir, allow_commands)
    storage_path = resolve_profile_storage_path(storage_env)
    storage = load_profiles(storage_path)
    profiles = storage.get("profiles") if isinstance(storage.get("profiles"), dict) else {}
    profile = profiles.get(profile_id)
    if not isinstance(profile, dict):
        return build_legacy_profile_context(base_dir, allow_commands)
    normalized = normalize_profile(
        profile,
        fallback_access_mode="trusted" if allow_commands else "file_only",
    )
    normalized["environmentMode"] = compute_environment_mode(normalized)
    return {
        "profileStoragePath": storage_path,
        "storage": storage,
        "activeProfile": normalized,
        "activeProfileId": normalized["profileId"],
        "globalPlugins": storage.get("globalPlugins") if isinstance(storage.get("globalPlugins"), dict) else {},
        "profileMode": "stored",
    }


def compute_effective_mode(
    *,
    server_allow_commands: bool,
    profile_access_mode: str,
    requested_mode: str,
    supported_modes: list[str],
) -> str | None:
    max_mode = "full_access" if server_allow_commands and profile_access_mode == "trusted" else "read_only"
    if requested_mode not in ALLOWED_PLUGIN_REQUESTED_MODES:
        requested_mode = "read_only"
    if max_mode == "read_only":
        desired = "read_only"
    else:
        desired = requested_mode
    if desired == "full_access" and "full_access" not in supported_modes:
        return None
    if desired == "read_only" and "read_only" not in supported_modes:
        return None
    return desired


def _attachment_state(global_record: dict[str, Any] | None, current_record: dict[str, Any] | None) -> str:
    if global_record and current_record:
        return "both"
    if current_record:
        return "current"
    if global_record:
        return "global"
    return "none"


def _effective_plugin_config(global_record: dict[str, Any] | None, current_record: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    if current_record and global_record:
        merged = dict(global_record.get("config") or {})
        merged.update(current_record.get("config") or {})
        return merged, "merged"
    if current_record:
        return dict(current_record.get("config") or {}), "current"
    if global_record:
        return dict(global_record.get("config") or {}), "global"
    return {}, "none"


def _current_profile_plugin_record(profile_context: dict[str, Any], plugin_id: str) -> dict[str, Any] | None:
    active_profile = profile_context.get("activeProfile") or {}
    plugins = active_profile.get("plugins") if isinstance(active_profile.get("plugins"), dict) else {}
    record = plugins.get(plugin_id)
    return record if isinstance(record, dict) else None


def _global_plugin_record(profile_context: dict[str, Any], plugin_id: str) -> dict[str, Any] | None:
    global_plugins = profile_context.get("globalPlugins") if isinstance(profile_context.get("globalPlugins"), dict) else {}
    record = global_plugins.get(plugin_id)
    return record if isinstance(record, dict) else None


def _safe_profile_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "-" for ch in value.strip()).strip(".-")
    if cleaned:
        return cleaned
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _workspace_digest(value: str) -> str:
    normalized = os.path.normcase(value)
    return hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:12]


def _local_config_candidate_paths(manifest: dict[str, Any], profile_context: dict[str, Any], scope: str) -> list[Path]:
    plugin_dir = Path(manifest["manifestPath"]).parent
    active_profile = profile_context.get("activeProfile") or {}
    profile_id = str(active_profile.get("profileId", "") or "").strip()
    workspace_path = str(active_profile.get("workspacePath", "") or "").strip()
    if scope == "global":
        return [plugin_dir / "plugin.local.global.json"]
    paths: list[Path] = []
    if profile_id:
        paths.append(plugin_dir / f"plugin.local.current.{_safe_profile_component(profile_id)}.json")
    if workspace_path:
        paths.append(plugin_dir / f"plugin.local.current.{_workspace_digest(workspace_path)}.json")
    paths.append(plugin_dir / "plugin.local.current.json")
    return paths


def _local_config_matches_active_profile(payload: dict[str, Any], profile_context: dict[str, Any]) -> bool:
    active_profile = profile_context.get("activeProfile") or {}
    payload_profile_id = str(payload.get("profileId", "") or "").strip()
    active_profile_id = str(active_profile.get("profileId", "") or "").strip()
    if payload_profile_id and active_profile_id and payload_profile_id != active_profile_id:
        return False
    payload_workspace = str(payload.get("workspacePath", "") or "").strip()
    active_workspace = str(active_profile.get("workspacePath", "") or "").strip()
    if payload_workspace and active_workspace and os.path.normcase(payload_workspace) != os.path.normcase(active_workspace):
        return False
    if not payload_profile_id and not payload_workspace:
        return False
    return True


def _read_local_plugin_record(path: Path, *, plugin_id: str, expected_scope: str, profile_context: dict[str, Any]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("pluginId", "") or "").strip() != plugin_id:
        return None
    if str(raw.get("scope", "") or "").strip() != expected_scope:
        return None
    if expected_scope == "current" and not _local_config_matches_active_profile(raw, profile_context):
        return None
    requested_mode = str(raw.get("requestedMode", "read_only") or "read_only")
    if requested_mode not in ALLOWED_PLUGIN_REQUESTED_MODES:
        requested_mode = "read_only"
    config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
    return {
        "scope": expected_scope,
        "requestedMode": requested_mode,
        "config": config,
        "attachedAt": str(raw.get("updatedAt", "") or ""),
        "source": f"plugin_local_{expected_scope}",
        "configPath": str(path),
    }


def _first_local_plugin_record(manifest: dict[str, Any], profile_context: dict[str, Any], scope: str) -> dict[str, Any] | None:
    for path in _local_config_candidate_paths(manifest, profile_context, scope):
        record = _read_local_plugin_record(
            path,
            plugin_id=str(manifest["id"]),
            expected_scope=scope,
            profile_context=profile_context,
        )
        if record is not None:
            return record
    return None


def _plugin_record_source(record: dict[str, Any] | None, fallback: str) -> str:
    if isinstance(record, dict):
        return str(record.get("source", fallback) or fallback)
    return fallback


def _effective_plugin_config_source(config_source: str, global_record: dict[str, Any] | None, current_record: dict[str, Any] | None) -> str:
    global_source = _plugin_record_source(global_record, "global")
    current_source = _plugin_record_source(current_record, "current")
    if config_source == "merged":
        if global_source == "global" and current_source == "current":
            return "merged"
        return f"{global_source}+{current_source}"
    if config_source == "global":
        return global_source
    if config_source == "current":
        return current_source
    return config_source


def build_plugin_states(
    manifests: dict[str, dict[str, Any]],
    profile_context: dict[str, Any],
    *,
    server_allow_commands: bool,
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    profile_access_mode = str((profile_context.get("activeProfile") or {}).get("accessMode", "file_only"))
    for plugin_id, manifest in manifests.items():
        if manifest.get("status") == "invalid_manifest":
            states[plugin_id] = {
                "pluginId": plugin_id,
                "displayName": manifest.get("display_name", plugin_id),
                "status": "failed",
                "attachScope": "none",
                "requestedMode": "read_only",
                "effectiveMode": None,
                "configSource": "none",
                "error": manifest.get("error", "invalid manifest"),
                "manifestPath": str(manifest.get("manifestPath", "")),
                "entrypointPath": "",
            }
            continue
        stored_global_record = _global_plugin_record(profile_context, plugin_id)
        stored_current_record = _current_profile_plugin_record(profile_context, plugin_id)
        local_global_record = _first_local_plugin_record(manifest, profile_context, "global")
        local_current_record = _first_local_plugin_record(manifest, profile_context, "current")
        global_record = local_global_record or stored_global_record
        current_record = local_current_record or stored_current_record
        attach_scope = _attachment_state(global_record, current_record)
        effective_config, config_source = _effective_plugin_config(global_record, current_record)
        config_source = _effective_plugin_config_source(config_source, global_record, current_record)
        requested_mode = "read_only"
        if current_record and isinstance(current_record.get("requestedMode"), str):
            requested_mode = str(current_record["requestedMode"])
        elif global_record and isinstance(global_record.get("requestedMode"), str):
            requested_mode = str(global_record["requestedMode"])
        effective_mode = compute_effective_mode(
            server_allow_commands=server_allow_commands,
            profile_access_mode=profile_access_mode,
            requested_mode=requested_mode,
            supported_modes=list(manifest.get("supported_modes") or []),
        )
        state = {
            "pluginId": plugin_id,
            "displayName": manifest["display_name"],
            "status": "not_attached" if attach_scope == "none" else "discoverable",
            "attachScope": attach_scope,
            "requestedMode": requested_mode,
            "effectiveMode": effective_mode,
            "configSource": config_source,
            "config": effective_config,
            "manifestPath": str(manifest["manifestPath"]),
            "entrypointPath": str(manifest["entrypointPath"]),
            "error": "",
            "localConfigPaths": {
                "global": [str(path) for path in _local_config_candidate_paths(manifest, profile_context, "global")],
                "current": [str(path) for path in _local_config_candidate_paths(manifest, profile_context, "current")],
            },
        }
        states[plugin_id] = state
    return states


def register_plugin_tools(
    *,
    mcp: Any,
    manifests: dict[str, dict[str, Any]],
    states: dict[str, dict[str, Any]],
    profile_context: dict[str, Any],
    server_allow_commands: bool,
) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for plugin_id, manifest in manifests.items():
        state = states.get(plugin_id)
        if state is None:
            continue
        if state["attachScope"] == "none":
            continue
        if state["effectiveMode"] is None:
            state["status"] = "disabled"
            state["error"] = "requested mode is not supported under the current safety mode"
            continue
        try:
            module = load_plugin_module(manifest)
            validate_result = _call_plugin_method(
                module,
                "validate_config",
                dict(state.get("config") or {}),
                build_plugin_runtime_context(profile_context, state),
            )
            if isinstance(validate_result, dict):
                state["config"] = validate_result
            health = _call_plugin_method(
                module,
                "healthcheck",
                build_plugin_runtime_context(profile_context, state),
            )
            state["health"] = health
            loaded[plugin_id] = {"module": module, "manifest": manifest, "state": state}
            state["status"] = "loaded"
            for descriptor in manifest["tools"]:
                if descriptor["mode_required"] == "full_access" and state["effectiveMode"] != "full_access":
                    continue
                handler = build_proxy_handler(
                    plugin_id=plugin_id,
                    descriptor=descriptor,
                    module=module,
                    state=state,
                    profile_context=profile_context,
                )
                mcp.tool(
                    name=descriptor["name"],
                    title=descriptor["title"],
                    description=descriptor["description"],
                )(handler)
        except Exception as exc:  # pragma: no cover - safety path covered indirectly
            state["status"] = "failed"
            state["error"] = str(exc)
            if manifest.get("required"):
                raise
    return loaded


def build_plugin_runtime_context(profile_context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    active_profile = profile_context.get("activeProfile") or {}
    return {
        "profileId": active_profile.get("profileId"),
        "pathSlot": active_profile.get("pathSlot"),
        "workspacePath": active_profile.get("workspacePath"),
        "accessMode": active_profile.get("accessMode"),
        "environmentMode": active_profile.get("environmentMode"),
        "attachScope": state.get("attachScope"),
        "requestedMode": state.get("requestedMode"),
        "effectiveMode": state.get("effectiveMode"),
        "configSource": state.get("configSource"),
        "pluginConfig": dict(state.get("config") or {}),
    }


def _annotation_from_schema(schema: dict[str, Any]) -> Any:
    json_type = schema.get("type")
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    return str


def _default_value_for_schema(schema: dict[str, Any]) -> Any:
    json_type = schema.get("type")
    if json_type == "integer":
        return 0
    if json_type == "number":
        return 0.0
    if json_type == "boolean":
        return False
    return ""


def build_signature_from_schema(input_schema: dict[str, Any]) -> inspect.Signature:
    properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
    required = set(input_schema.get("required") or [])
    parameters = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            schema = {"type": "string"}
        default = inspect._empty if name in required else _default_value_for_schema(schema)
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=_annotation_from_schema(schema),
            )
        )
    return inspect.Signature(parameters=parameters)


def build_proxy_handler(
    *,
    plugin_id: str,
    descriptor: dict[str, Any],
    module: ModuleType,
    state: dict[str, Any],
    profile_context: dict[str, Any],
) -> Callable[..., Any]:
    async def handler(**kwargs: Any) -> Any:
        runtime_context = build_plugin_runtime_context(profile_context, state)
        if descriptor["mode_required"] == "full_access" and state.get("effectiveMode") != "full_access":
            raise PluginError(
                f"Plugin '{plugin_id}' is attached, but tool '{descriptor['name']}' requires full_access under a trusted profile"
            )
        result = _call_plugin_method(
            module,
            "invoke",
            descriptor["name"],
            kwargs,
            runtime_context,
        )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False, indent=2)
        return str(result)

    handler.__name__ = descriptor["name"].replace("-", "_")
    handler.__doc__ = descriptor["description"]
    handler.__signature__ = build_signature_from_schema(descriptor["input_schema"])
    return handler


class PluginManager:
    def __init__(self, *, server_dir: Path, base_dir: Path, allow_commands: bool, mcp: Any):
        self.server_dir = server_dir
        self.base_dir = base_dir
        self.allow_commands = allow_commands
        self.mcp = mcp
        self.plugins_root = self.server_dir / PLUGIN_DIRNAME
        self.profile_context = load_profile_context_from_env(base_dir, allow_commands)
        self.manifests = discover_plugin_manifests(self.plugins_root)
        self.states = build_plugin_states(
            self.manifests,
            self.profile_context,
            server_allow_commands=allow_commands,
        )
        if any(state.get("attachScope") in {"current", "both"} for state in self.states.values()):
            active_profile = self.profile_context.get("activeProfile")
            if isinstance(active_profile, dict):
                active_profile["environmentMode"] = "CUSTOM"
        self.loaded = register_plugin_tools(
            mcp=mcp,
            manifests=self.manifests,
            states=self.states,
            profile_context=self.profile_context,
            server_allow_commands=allow_commands,
        )

    @property
    def active_profile(self) -> dict[str, Any]:
        return self.profile_context.get("activeProfile") or {}

    def diagnostics_text(self) -> str:
        last_startup_error = next(
            (
                str(state.get("error", "")).strip()
                for state in self.states.values()
                if str(state.get("error", "")).strip()
            ),
            "",
        )
        lines = [
            f"active profile id: {self.active_profile.get('profileId', '(none)')}",
            f"active path slot: {self.active_profile.get('pathSlot', 0)}",
            f"active workspace path: {self.active_profile.get('workspacePath', self.base_dir)}",
            f"active access mode: {self.active_profile.get('accessMode', 'file_only')}",
            f"active environment mode: {self.active_profile.get('environmentMode', 'DEFAULT')}",
            f"profile storage: {self.profile_context.get('profileStoragePath') or '(legacy synthetic mode)'}",
            f"last startup error: {last_startup_error or '(none)'}",
        ]
        for plugin_id in sorted(self.states):
            state = self.states[plugin_id]
            lines.extend(
                [
                    f"[{plugin_id}] status: {state.get('status', 'unknown')}",
                    f"[{plugin_id}] scope: {state.get('attachScope', 'none')}",
                    f"[{plugin_id}] requested mode: {state.get('requestedMode', 'read_only')}",
                    f"[{plugin_id}] effective mode: {state.get('effectiveMode') or '(none)'}",
                    f"[{plugin_id}] config source: {state.get('configSource', 'none')}",
                    f"[{plugin_id}] local config candidates: {json.dumps(state.get('localConfigPaths', {}), ensure_ascii=False, sort_keys=True)}",
                    f"[{plugin_id}] manifest path: {state.get('manifestPath', '')}",
                    f"[{plugin_id}] entrypoint path: {state.get('entrypointPath', '')}",
                    f"[{plugin_id}] health: {json.dumps(state.get('health', {}), ensure_ascii=False, sort_keys=True)}",
                ]
            )
            if state.get("error"):
                lines.append(f"[{plugin_id}] error: {state['error']}")
        return "\n".join(lines)

    def list_plugins_text(self) -> str:
        if not self.manifests:
            return f"No plugins discovered under {self.plugins_root}"
        lines = [f"plugins root: {self.plugins_root}"]
        for plugin_id in sorted(self.manifests):
            manifest = self.manifests[plugin_id]
            state = self.states.get(plugin_id, {})
            lines.append(
                f"- {plugin_id}: {manifest.get('display_name', plugin_id)} | status={state.get('status', 'discoverable')} | scope={state.get('attachScope', 'none')} | effective={state.get('effectiveMode') or '(none)'}"
            )
        return "\n".join(lines)

    def _require_storage(self) -> tuple[Path, dict[str, Any], str]:
        storage_path = self.profile_context.get("profileStoragePath")
        profile_id = str(self.profile_context.get("activeProfileId", "") or "")
        if storage_path is None:
            raise PluginError(
                "Plugin attach/detach requires stored workflow profiles. Start the server through the launcher after migration."
            )
        storage = load_profiles(Path(storage_path))
        return Path(storage_path), storage, profile_id

    def _refresh_profile_context(self, storage_path: Path) -> None:
        storage = load_profiles(storage_path)
        profile_id = str(storage.get("activeProfileId", "") or self.profile_context.get("activeProfileId", "") or "")
        profiles = storage.get("profiles") if isinstance(storage.get("profiles"), dict) else {}
        profile = profiles.get(profile_id)
        if isinstance(profile, dict):
            normalized = normalize_profile(
                profile,
                fallback_access_mode=("trusted" if self.allow_commands else "file_only"),
            )
            normalized["environmentMode"] = compute_environment_mode(normalized)
            self.profile_context["activeProfile"] = normalized
            self.profile_context["activeProfileId"] = normalized["profileId"]
        self.profile_context["storage"] = storage
        self.profile_context["globalPlugins"] = storage.get("globalPlugins") if isinstance(storage.get("globalPlugins"), dict) else {}

    def attach_plugin(self, plugin_id: str, scope: str, requested_mode: str, config: dict[str, Any]) -> str:
        if scope not in ALLOWED_PLUGIN_SCOPES:
            raise PluginError("scope must be 'current' or 'global'")
        if requested_mode not in ALLOWED_PLUGIN_REQUESTED_MODES:
            raise PluginError("requested_mode must be 'read_only' or 'full_access'")
        manifest = self.manifests.get(plugin_id)
        if manifest is None:
            raise PluginError(f"Unknown plugin: {plugin_id}")
        if manifest.get("status") == "invalid_manifest":
            raise PluginError(
                f"Plugin '{plugin_id}' has an invalid manifest and cannot be attached: {manifest.get('error', 'invalid manifest')}"
            )
        if manifest.get("install_scope_support") == "current" and scope != "current":
            raise PluginError(f"Plugin '{plugin_id}' supports current scope only")
        if manifest.get("install_scope_support") == "global" and scope != "global":
            raise PluginError(f"Plugin '{plugin_id}' supports global scope only")
        module = load_plugin_module(manifest)
        runtime_context = {
            **build_plugin_runtime_context(self.profile_context, {
                "attachScope": scope,
                "requestedMode": requested_mode,
                "effectiveMode": requested_mode,
                "configSource": scope,
                "config": config,
            }),
            "attachIntent": scope,
        }
        normalized = _call_plugin_method(module, "validate_config", config, runtime_context)
        if normalized is None:
            normalized = config
        storage_path, storage, profile_id = self._require_storage()
        updated = attach_plugin_record(
            storage,
            profile_id=profile_id,
            plugin_id=plugin_id,
            scope=scope,
            requested_mode=requested_mode,
            config=normalized if isinstance(normalized, dict) else config,
        )
        save_profiles(updated, storage_path)
        self._refresh_profile_context(storage_path)
        return (
            f"Attached plugin '{plugin_id}' in {scope} scope.\n"
            f"Requested mode: {requested_mode}.\n"
            f"Saved to: {storage_path}.\n"
            f"Restart MCP to rebuild the plugin registry for the active profile."
        )

    def detach_plugin(self, plugin_id: str, scope: str) -> str:
        if scope not in ALLOWED_PLUGIN_SCOPES:
            raise PluginError("scope must be 'current' or 'global'")
        storage_path, storage, profile_id = self._require_storage()
        updated = detach_plugin_record(
            storage,
            profile_id=profile_id,
            plugin_id=plugin_id,
            scope=scope,
        )
        save_profiles(updated, storage_path)
        self._refresh_profile_context(storage_path)
        return (
            f"Detached plugin '{plugin_id}' from {scope} scope.\n"
            f"Saved to: {storage_path}.\n"
            f"Restart MCP to rebuild the plugin registry for the active profile."
        )
