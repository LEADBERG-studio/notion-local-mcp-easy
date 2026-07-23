from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "NotionMcpEasy"
PROFILE_SCHEMA_VERSION = 1
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
PROFILE_STORAGE_FILE = CONFIG_DIR / "workflow-profiles.json"


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def normalize_workspace_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def derive_display_name(workspace_path: str | Path) -> str:
    path = Path(workspace_path)
    name = path.name.strip()
    return name or str(path)


def access_mode_from_allow_commands(allow_commands: bool) -> str:
    return "trusted" if allow_commands else "file_only"


def allow_commands_from_access_mode(access_mode: str) -> bool:
    return access_mode == "trusted"


def compute_environment_mode(profile: dict[str, Any]) -> str:
    plugins = profile.get("plugins") or {}
    if isinstance(plugins, dict) and plugins:
        return "CUSTOM"
    return "DEFAULT"


def resolve_profile_storage_path(explicit_path: str | Path | None = None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    return PROFILE_STORAGE_FILE


def default_storage() -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "activeProfileId": "",
        "profiles": {},
        "globalPlugins": {},
    }


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def _stable_profile_id(workspace_path: Path) -> str:
    normalized = os.path.normcase(str(workspace_path))
    digest = hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()
    return f"workspace-{digest[:12]}"


def _copy_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(profile))


def build_profile(
    *,
    profile_id: str,
    path_slot: int,
    workspace_path: str | Path,
    access_mode: str,
    created_from: str,
    display_name: str | None = None,
    plugins: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    path = normalize_workspace_path(workspace_path)
    created = created_at or now_iso()
    updated = updated_at or created
    profile = {
        "profileId": profile_id,
        "pathSlot": int(path_slot),
        "workspacePath": str(path),
        "accessMode": access_mode,
        "environmentMode": "DEFAULT",
        "displayName": display_name or derive_display_name(path),
        "createdAt": created,
        "updatedAt": updated,
        "metadata": {
            "createdFrom": created_from,
            "lastSelectedAt": "",
            "lastKnownGood": True,
            "notes": "",
        },
        "plugins": plugins or {},
    }
    if metadata:
        profile["metadata"].update(metadata)
    profile["environmentMode"] = compute_environment_mode(profile)
    return profile


def normalize_profile(profile: dict[str, Any], *, fallback_access_mode: str) -> dict[str, Any]:
    path = normalize_workspace_path(str(profile.get("workspacePath", "")))
    profile_id = str(profile.get("profileId", "")).strip() or _stable_profile_id(path)
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    plugins = profile.get("plugins") if isinstance(profile.get("plugins"), dict) else {}
    created_at = str(profile.get("createdAt", "")).strip() or now_iso()
    updated_at = str(profile.get("updatedAt", "")).strip() or created_at
    normalized = build_profile(
        profile_id=profile_id,
        path_slot=int(profile.get("pathSlot", 0) or 0),
        workspace_path=path,
        access_mode=str(profile.get("accessMode", fallback_access_mode) or fallback_access_mode),
        created_from=str(metadata.get("createdFrom") or "repair"),
        display_name=str(profile.get("displayName", "")).strip() or None,
        plugins=plugins,
        metadata=metadata,
        created_at=created_at,
        updated_at=updated_at,
    )
    normalized["metadata"]["lastSelectedAt"] = str(metadata.get("lastSelectedAt", ""))
    normalized["metadata"]["lastKnownGood"] = bool(metadata.get("lastKnownGood", True))
    normalized["metadata"]["notes"] = str(metadata.get("notes", ""))
    return normalized


def load_profiles(path: str | Path | None = None) -> dict[str, Any]:
    storage_path = resolve_profile_storage_path(path)
    if not storage_path.is_file():
        return default_storage()
    try:
        raw = json.loads(storage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid profile storage: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("profile storage must be a JSON object")
    storage = default_storage()
    storage["schemaVersion"] = int(raw.get("schemaVersion", PROFILE_SCHEMA_VERSION) or PROFILE_SCHEMA_VERSION)
    storage["activeProfileId"] = str(raw.get("activeProfileId", "") or "")
    storage["globalPlugins"] = raw.get("globalPlugins") if isinstance(raw.get("globalPlugins"), dict) else {}
    raw_profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    fallback_access_mode = "file_only"
    profiles: dict[str, Any] = {}
    for key, profile in raw_profiles.items():
        if not isinstance(profile, dict):
            continue
        normalized = normalize_profile(profile, fallback_access_mode=fallback_access_mode)
        profiles[str(key)] = normalized
    storage["profiles"] = profiles
    return storage


def save_profiles(storage: dict[str, Any], path: str | Path | None = None) -> Path:
    storage_path = resolve_profile_storage_path(path)
    payload = {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "activeProfileId": str(storage.get("activeProfileId", "") or ""),
        "profiles": storage.get("profiles") or {},
        "globalPlugins": storage.get("globalPlugins") or {},
    }
    _atomic_write_text(storage_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return storage_path


def _normalized_slot_paths(paths: dict[int, str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for slot, raw in sorted(paths.items()):
        try:
            result[int(slot)] = str(normalize_workspace_path(raw))
        except OSError:
            continue
    return result


def _find_profile_by_path(profiles: dict[str, Any], workspace_path: str) -> str | None:
    target = os.path.normcase(workspace_path)
    for profile_id, profile in profiles.items():
        if os.path.normcase(str(profile.get("workspacePath", ""))) == target:
            return profile_id
    return None


def _find_profile_by_slot(profiles: dict[str, Any], slot: int) -> str | None:
    for profile_id, profile in profiles.items():
        if int(profile.get("pathSlot", 0) or 0) == int(slot):
            return profile_id
    return None


def sync_profiles_with_slots(
    storage: dict[str, Any],
    paths: dict[int, str],
    *,
    legacy_allow_commands: bool,
    active_workspace: str | Path | None = None,
    created_from: str = "sync",
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    updated = default_storage()
    updated["schemaVersion"] = PROFILE_SCHEMA_VERSION
    updated["globalPlugins"] = storage.get("globalPlugins") if isinstance(storage.get("globalPlugins"), dict) else {}
    existing_profiles = storage.get("profiles") if isinstance(storage.get("profiles"), dict) else {}
    normalized_profiles: dict[str, Any] = {}
    fallback_access_mode = access_mode_from_allow_commands(legacy_allow_commands)
    for profile_id, profile in existing_profiles.items():
        if not isinstance(profile, dict):
            continue
        try:
            normalized_profiles[str(profile_id)] = normalize_profile(profile, fallback_access_mode=fallback_access_mode)
        except OSError:
            continue

    slot_paths = _normalized_slot_paths(paths)
    changed = False
    next_profiles: dict[str, Any] = {}

    for slot, workspace_path in slot_paths.items():
        matched_id = None
        slot_match = _find_profile_by_slot(normalized_profiles, slot)
        if slot_match is not None:
            profile = normalized_profiles[slot_match]
            if os.path.normcase(str(profile.get("workspacePath", ""))) == os.path.normcase(workspace_path):
                matched_id = slot_match
        if matched_id is None:
            matched_id = _find_profile_by_path(normalized_profiles, workspace_path)

        if matched_id is None:
            profile_id = _stable_profile_id(Path(workspace_path))
            profile = build_profile(
                profile_id=profile_id,
                path_slot=slot,
                workspace_path=workspace_path,
                access_mode=fallback_access_mode,
                created_from=created_from,
            )
            changed = True
        else:
            profile = _copy_profile(normalized_profiles[matched_id])
            if int(profile.get("pathSlot", 0) or 0) != slot:
                profile["pathSlot"] = slot
                changed = True
            if str(profile.get("workspacePath", "")) != workspace_path:
                profile["workspacePath"] = workspace_path
                changed = True
            if not str(profile.get("accessMode", "")).strip():
                profile["accessMode"] = fallback_access_mode
                changed = True
            profile["environmentMode"] = compute_environment_mode(profile)
        profile["displayName"] = str(profile.get("displayName", "")).strip() or derive_display_name(workspace_path)
        profile["updatedAt"] = str(profile.get("updatedAt", "")).strip() or now_iso()
        next_profiles[str(profile["profileId"])] = profile

    updated["profiles"] = next_profiles

    active_profile_id = str(storage.get("activeProfileId", "") or "")
    active_profile: dict[str, Any] | None = None
    if active_workspace is not None:
        try:
            active_workspace_path = str(normalize_workspace_path(active_workspace))
        except OSError:
            active_workspace_path = ""
        if active_workspace_path:
            matched_id = _find_profile_by_path(next_profiles, active_workspace_path)
            if matched_id:
                active_profile_id = matched_id
    if active_profile_id and active_profile_id in next_profiles:
        active_profile = next_profiles[active_profile_id]
    elif next_profiles:
        first_id = next(iter(sorted(next_profiles, key=lambda pid: int(next_profiles[pid].get("pathSlot", 0) or 0))))
        active_profile_id = first_id
        active_profile = next_profiles[first_id]
        changed = True
    else:
        active_profile_id = ""
        active_profile = None
    updated["activeProfileId"] = active_profile_id
    if active_profile is not None:
        last_selected = str(active_profile.get("metadata", {}).get("lastSelectedAt", ""))
        if not last_selected:
            active_profile.setdefault("metadata", {})["lastSelectedAt"] = now_iso()
            changed = True
    if storage.get("schemaVersion") != PROFILE_SCHEMA_VERSION:
        changed = True
    if storage.get("activeProfileId") != active_profile_id:
        changed = True
    return updated, active_profile, changed


def mark_active_profile(storage: dict[str, Any], profile_id: str) -> dict[str, Any]:
    profiles = storage.get("profiles") if isinstance(storage.get("profiles"), dict) else {}
    if profile_id not in profiles:
        raise KeyError(f"Unknown profile: {profile_id}")
    storage = json.loads(json.dumps(storage))
    storage["activeProfileId"] = profile_id
    profile = storage["profiles"][profile_id]
    profile.setdefault("metadata", {})["lastSelectedAt"] = now_iso()
    profile["updatedAt"] = now_iso()
    profile["environmentMode"] = compute_environment_mode(profile)
    return storage


def apply_profile_to_legacy_config(config: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    updated = dict(config)
    updated["workspace"] = str(profile["workspacePath"])
    updated["allow_commands"] = allow_commands_from_access_mode(str(profile.get("accessMode", "file_only")))
    return updated


def attach_plugin_record(
    storage: dict[str, Any],
    *,
    profile_id: str | None,
    plugin_id: str,
    scope: str,
    requested_mode: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    updated = json.loads(json.dumps(storage))
    record = {
        "scope": scope,
        "requestedMode": requested_mode,
        "config": config,
        "attachedAt": now_iso(),
    }
    if scope == "global":
        updated.setdefault("globalPlugins", {})[plugin_id] = record
        return updated
    if scope != "current":
        raise ValueError("scope must be 'current' or 'global'")
    if not profile_id:
        raise ValueError("active profile is required for current-scope attachment")
    profiles = updated.setdefault("profiles", {})
    if profile_id not in profiles:
        raise KeyError(f"Unknown profile: {profile_id}")
    profile = profiles[profile_id]
    profile.setdefault("plugins", {})[plugin_id] = record
    profile["environmentMode"] = compute_environment_mode(profile)
    profile["updatedAt"] = now_iso()
    return updated


def detach_plugin_record(
    storage: dict[str, Any], *, profile_id: str | None, plugin_id: str, scope: str
) -> dict[str, Any]:
    updated = json.loads(json.dumps(storage))
    if scope == "global":
        updated.setdefault("globalPlugins", {}).pop(plugin_id, None)
        return updated
    if scope != "current":
        raise ValueError("scope must be 'current' or 'global'")
    if not profile_id:
        raise ValueError("active profile is required for current-scope detach")
    profiles = updated.setdefault("profiles", {})
    if profile_id not in profiles:
        raise KeyError(f"Unknown profile: {profile_id}")
    profile = profiles[profile_id]
    if isinstance(profile.get("plugins"), dict):
        profile["plugins"].pop(plugin_id, None)
    profile["environmentMode"] = compute_environment_mode(profile)
    profile["updatedAt"] = now_iso()
    return updated
