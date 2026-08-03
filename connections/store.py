"""Single source of truth for connection state.

Two files replace the old pile of configs:

``current-connection.json``
    One permanent record of every known work area and which connection profile
    each one uses, plus the currently active area. This is what START reads.

``connection-profiles.v2.json``
    The configured circuits. Each entry is a clone of its shipped blueprint
    plus the operator's answers. Product upgrades never touch this file; only
    the connection profile setup script writes to it.

The legacy ``config.json`` is still produced, but purely as a generated mirror
for older consumers. Nothing reads authority back out of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import blueprints
from .base import Circuit, ConnectionConfigError, RuntimeContext
from .circuits import CIRCUIT_CLASSES

APP_NAME = "NotionMcpEasy"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
CURRENT_FILE = CONFIG_DIR / "current-connection.json"
PROFILES_FILE = CONFIG_DIR / "connection-profiles.v2.json"
LEGACY_CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_PROFILES_FILE = CONFIG_DIR / "connection-profiles.json"

CURRENT_SCHEMA_VERSION = 1
PROFILES_SCHEMA_VERSION = 2

ACCESS_MODES = ("file_only", "trusted")
AUTH_MODES = ("legacy", "oauth", "dual")


class ConnectionStoreError(RuntimeError):
    """The persistent connection state is unusable."""


# --------------------------------------------------------------------- utils


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_path(value: str | Path) -> Path:
    return Path(str(value)).expanduser().resolve()


def area_id_for(workspace: str | Path) -> str:
    digest = hashlib.sha1(
        os.path.normcase(str(normalize_path(workspace))).encode("utf-8", errors="replace")
    ).hexdigest()
    return f"area-{digest[:12]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    """Atomic write with exactly one rolling backup, not an endless pile."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def new_token() -> str:
    return "bridge-secret-token-" + secrets.token_urlsafe(18)


def new_owner_code() -> str:
    return secrets.token_urlsafe(9)


# ------------------------------------------------------------------ circuits


def circuit(circuit_id: str) -> Circuit:
    circuit_id = str(circuit_id or "").strip()
    if circuit_id not in CIRCUIT_CLASSES:
        raise ConnectionConfigError(
            f"Unknown connection circuit '{circuit_id}'. Known circuits: "
            + ", ".join(blueprints.available_ids())
        )
    return CIRCUIT_CLASSES[circuit_id](blueprints.load_blueprint(circuit_id))


def all_circuits() -> list[Circuit]:
    return [circuit(circuit_id) for circuit_id in blueprints.available_ids()]


# ------------------------------------------------------------------ profiles


def default_profiles() -> dict[str, Any]:
    return {"schemaVersion": PROFILES_SCHEMA_VERSION, "profiles": {}}


def load_profiles(path: Path | None = None) -> dict[str, Any]:
    raw = _read_json(path or PROFILES_FILE)
    storage = default_profiles()
    profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    for circuit_id, entry in profiles.items():
        if circuit_id not in CIRCUIT_CLASSES or not isinstance(entry, dict):
            continue
        settings = entry.get("settings") if isinstance(entry.get("settings"), dict) else {}
        storage["profiles"][circuit_id] = {
            "id": circuit_id,
            "settings": circuit(circuit_id).merge(settings),
            "blueprintVersion": int(entry.get("blueprintVersion", 1) or 1),
            "configuredAt": str(entry.get("configuredAt", "")),
            "updatedAt": str(entry.get("updatedAt", "")),
            "verifiedAt": str(entry.get("verifiedAt", "")),
            "notes": str(entry.get("notes", "")),
        }
    return storage


def save_profiles(storage: dict[str, Any], path: Path | None = None) -> Path:
    payload = {
        "schemaVersion": PROFILES_SCHEMA_VERSION,
        "profiles": storage.get("profiles") or {},
    }
    return _write_json(path or PROFILES_FILE, payload)


def get_profile(circuit_id: str, path: Path | None = None) -> dict[str, Any] | None:
    return load_profiles(path).get("profiles", {}).get(circuit_id)


def profile_settings(circuit_id: str, path: Path | None = None) -> dict[str, Any]:
    """Configured settings, or a pristine blueprint clone when never set up."""
    entry = get_profile(circuit_id, path)
    active = circuit(circuit_id)
    return active.merge((entry or {}).get("settings"))


def save_profile(
    circuit_id: str,
    settings: dict[str, Any],
    *,
    verified: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    active = circuit(circuit_id)
    storage = load_profiles(path)
    previous = storage["profiles"].get(circuit_id) or {}
    entry = {
        "id": circuit_id,
        "settings": active.merge(settings),
        "blueprintVersion": active.blueprint_version,
        "configuredAt": str(previous.get("configuredAt") or now_iso()),
        "updatedAt": now_iso(),
        "verifiedAt": now_iso() if verified else str(previous.get("verifiedAt", "")),
        "notes": str(previous.get("notes", "")),
    }
    storage["profiles"][circuit_id] = entry
    save_profiles(storage, path)
    return entry


def reset_profile(circuit_id: str, path: Path | None = None) -> dict[str, Any]:
    """Drop operator answers and return to the untouched shipped blueprint."""
    storage = load_profiles(path)
    storage["profiles"].pop(circuit_id, None)
    save_profiles(storage, path)
    return circuit(circuit_id).default_settings()


def is_configured(circuit_id: str, path: Path | None = None) -> bool:
    entry = get_profile(circuit_id, path)
    if entry is None:
        active = circuit(circuit_id)
        return not any(question.required for question in active.questions())
    return circuit(circuit_id).is_configured(entry.get("settings"))


def configured_ids(path: Path | None = None) -> list[str]:
    return [cid for cid in blueprints.available_ids() if is_configured(cid, path)]


# --------------------------------------------------------------------- areas


def default_current() -> dict[str, Any]:
    return {
        "schemaVersion": CURRENT_SCHEMA_VERSION,
        "activeAreaId": "",
        "areas": {},
        "globalAuth": {"mode": "legacy", "token": "", "oauthOwnerCode": ""},
    }


def _normalize_auth(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("mode", "legacy")).strip().lower()
    return {
        "mode": mode if mode in AUTH_MODES else "legacy",
        "token": str(raw.get("token", "")),
        "oauthOwnerCode": str(raw.get("oauthOwnerCode", "")),
    }


def _normalize_area(raw: dict[str, Any]) -> dict[str, Any]:
    workspace = str(raw.get("workspace", "")).strip()
    access_mode = str(raw.get("accessMode", "file_only")).strip().lower()
    profile = str(raw.get("connectionProfile", "")).strip()
    return {
        "id": str(raw.get("id") or (area_id_for(workspace) if workspace else "")),
        "workspace": workspace,
        "displayName": str(raw.get("displayName") or (Path(workspace).name if workspace else "")),
        "accessMode": access_mode if access_mode in ACCESS_MODES else "file_only",
        "connectionProfile": profile if profile in CIRCUIT_CLASSES else "",
        "port": int(raw.get("port", 8765) or 8765),
        "useGlobalAuth": bool(raw.get("useGlobalAuth", False)),
        "auth": _normalize_auth(raw.get("auth")),
        "plugins": raw.get("plugins") if isinstance(raw.get("plugins"), dict) else {},
        "createdAt": str(raw.get("createdAt") or now_iso()),
        "updatedAt": str(raw.get("updatedAt") or now_iso()),
    }


def load_current(path: Path | None = None) -> dict[str, Any]:
    raw = _read_json(path or CURRENT_FILE)
    current = default_current()
    if not raw:
        return current
    current["activeAreaId"] = str(raw.get("activeAreaId", ""))
    current["globalAuth"] = _normalize_auth(raw.get("globalAuth"))
    areas = raw.get("areas") if isinstance(raw.get("areas"), dict) else {}
    for area_id, entry in areas.items():
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_area({**entry, "id": entry.get("id") or area_id})
        if normalized["workspace"]:
            current["areas"][normalized["id"]] = normalized
    if current["activeAreaId"] not in current["areas"]:
        current["activeAreaId"] = next(iter(current["areas"]), "")
    return current


def save_current(current: dict[str, Any], path: Path | None = None) -> Path:
    payload = {
        "schemaVersion": CURRENT_SCHEMA_VERSION,
        "activeAreaId": str(current.get("activeAreaId", "")),
        "areas": current.get("areas") or {},
        "globalAuth": _normalize_auth(current.get("globalAuth")),
    }
    return _write_json(path or CURRENT_FILE, payload)


def upsert_area(
    current: dict[str, Any],
    *,
    workspace: str | Path,
    access_mode: str | None = None,
    connection_profile: str | None = None,
    port: int | None = None,
    use_global_auth: bool | None = None,
    make_active: bool = True,
) -> dict[str, Any]:
    workspace_path = normalize_path(workspace)
    area_id = area_id_for(workspace_path)
    area = current["areas"].get(area_id) or _normalize_area(
        {"id": area_id, "workspace": str(workspace_path)}
    )
    area["workspace"] = str(workspace_path)
    area["displayName"] = workspace_path.name or str(workspace_path)
    if access_mode is not None:
        area["accessMode"] = access_mode if access_mode in ACCESS_MODES else "file_only"
    if connection_profile is not None:
        area["connectionProfile"] = connection_profile
    if port is not None:
        area["port"] = int(port)
    if use_global_auth is not None:
        area["useGlobalAuth"] = bool(use_global_auth)
    area["updatedAt"] = now_iso()
    current["areas"][area_id] = area
    if make_active:
        current["activeAreaId"] = area_id
    return area


def effective_auth(current: dict[str, Any], area: dict[str, Any]) -> dict[str, Any]:
    """Per-area credentials by default, shared credentials when the flag is on.

    The global switch exists so an operator can swap connection channels without
    re-authorizing every MCP client.
    """
    if area.get("useGlobalAuth"):
        auth = _normalize_auth(current.get("globalAuth"))
        if not auth["token"]:
            auth["token"] = new_token()
        if auth["mode"] in {"oauth", "dual"} and not auth["oauthOwnerCode"]:
            auth["oauthOwnerCode"] = new_owner_code()
        current["globalAuth"] = auth
        return auth
    auth = _normalize_auth(area.get("auth"))
    if not auth["token"]:
        auth["token"] = new_token()
    if auth["mode"] in {"oauth", "dual"} and not auth["oauthOwnerCode"]:
        auth["oauthOwnerCode"] = new_owner_code()
    area["auth"] = auth
    return auth


# ------------------------------------------------------------------ resolved


@dataclass
class ResolvedConnection:
    area: dict[str, Any]
    circuit: Circuit
    settings: dict[str, Any]
    auth: dict[str, Any]
    context: RuntimeContext

    @property
    def public_url(self) -> str:
        return self.circuit.static_url(self.settings)


def runtime_name_for(workspace: str | Path, script_dir: Path) -> str:
    workspace_path = normalize_path(workspace)
    identity = os.path.normcase(str(workspace_path)) + "|" + os.path.normcase(str(script_dir))
    digest = hashlib.sha1(identity.encode("utf-8", errors="replace")).hexdigest()[:8]
    stem = workspace_path.name or "workspace"
    return f"{stem}-mcp-{digest}"


def resolve(
    current: dict[str, Any],
    *,
    script_dir: Path,
    config_dir: Path | None = None,
    area_id: str = "",
    profiles_path: Path | None = None,
) -> ResolvedConnection:
    area_id = area_id or str(current.get("activeAreaId", ""))
    area = current.get("areas", {}).get(area_id)
    if not area:
        raise ConnectionStoreError(
            "No work area is selected yet. Run SETUP.bat to choose a folder, an access "
            "mode and a connection profile."
        )
    circuit_id = str(area.get("connectionProfile", "")).strip()
    if not circuit_id:
        raise ConnectionStoreError(
            f"Work area '{area.get('displayName')}' has no connection profile assigned. "
            "Run SETUP.bat and pick one."
        )
    if not is_configured(circuit_id, profiles_path):
        raise ConnectionStoreError(
            f"Connection profile '{circuit_id}' is not configured yet. "
            "Run PROFILES.bat to set it up, then select it in SETUP.bat."
        )
    active = circuit(circuit_id)
    settings = active.validate(profile_settings(circuit_id, profiles_path))
    auth = effective_auth(current, area)
    workspace = normalize_path(area["workspace"])
    context = RuntimeContext(
        local_port=int(area.get("port", 8765) or 8765),
        auth_mode=auth["mode"],
        script_dir=script_dir,
        config_dir=config_dir or CONFIG_DIR,
        workspace=workspace,
        runtime_name=runtime_name_for(workspace, script_dir),
    )
    return ResolvedConnection(
        area=area, circuit=active, settings=settings, auth=auth, context=context
    )


# ------------------------------------------------------------- legacy mirror


LEGACY_CIRCUIT_FIELDS = (
    "serveo_hostname",
    "ssh_key",
    "public_url",
    "tunnel_host",
    "tunnel_ssh_port",
    "tunnel_domain",
    "tunnellio_token",
    "tunnellio_domain",
    "tunnellio_base_url",
    "tunnellio_connection_mode",
)


def legacy_mirror(
    resolved: ResolvedConnection,
    *,
    version: str,
    allowed_commands: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the flat legacy config for older consumers.

    Only the active circuit contributes fields. Every other circuit's legacy
    key is written empty, so a stale value can never be picked up by mistake.
    """
    area = resolved.area
    mirror: dict[str, Any] = {
        "version": version,
        "workspace": str(normalize_path(area["workspace"])),
        "port": int(area.get("port", 8765) or 8765),
        "allow_commands": area.get("accessMode") == "trusted",
        "token": resolved.auth["token"],
        "auth_mode": resolved.auth["mode"],
        "oauth_owner_code": resolved.auth["oauthOwnerCode"],
        "connection_profile": resolved.circuit.id,
        "tunnel_mode_preference": resolved.circuit.id,
        "tunnel_backend": resolved.circuit.legacy_backend,
    }
    for key in LEGACY_CIRCUIT_FIELDS:
        mirror[key] = ""
    mirror.update(resolved.circuit.legacy_export(resolved.settings))
    if allowed_commands and mirror["allow_commands"]:
        mirror["allowed_commands"] = sorted(allowed_commands)
    if extra:
        mirror.update(extra)
    return mirror


def write_legacy_mirror(mirror: dict[str, Any], path: Path | None = None) -> Path:
    return _write_json(path or LEGACY_CONFIG_FILE, mirror)


# ----------------------------------------------------------------- migration


LEGACY_MODE_MAP = {
    "tunnellio": "tunnellio_stable",
    "serveo_temporary": "serveo_temporary",
    "serveo_stable": "serveo_stable",
    "reverse_proxy": "reverse_proxy",
    "sish": "sish",
}


def circuit_from_legacy(legacy: dict[str, Any]) -> str:
    """Map an old flat config onto a circuit id without guessing.

    Only explicit evidence counts. The presence of tunnellio.exe on disk, or a
    leftover hostname from another mode, is never treated as intent.
    """
    preference = str(legacy.get("tunnel_mode_preference", "")).strip().lower()
    if preference in CIRCUIT_CLASSES:
        return preference
    if preference in LEGACY_MODE_MAP:
        mapped = LEGACY_MODE_MAP[preference]
        if mapped == "tunnellio_stable" and not str(legacy.get("tunnellio_domain", "")).strip():
            return "tunnellio_random" if str(legacy.get("tunnellio_token", "")).strip() else ""
        return mapped
    backend = str(legacy.get("tunnel_backend", "")).strip().lower()
    if backend == "custom_proxy":
        return "reverse_proxy"
    if backend == "sish":
        return "sish"
    if backend == "tunnellio":
        if str(legacy.get("tunnellio_domain", "")).strip():
            return "tunnellio_stable"
        if str(legacy.get("tunnellio_token", "")).strip():
            return "tunnellio_random"
        return ""
    if backend == "serveo":
        return "serveo_stable" if str(legacy.get("serveo_hostname", "")).strip() else "serveo_temporary"
    return ""


def settings_from_legacy(circuit_id: str, legacy: dict[str, Any]) -> dict[str, Any]:
    """Translate old flat fields into one circuit's private namespace."""
    if circuit_id == "serveo_stable":
        return {"hostname": legacy.get("serveo_hostname", ""), "ssh_key": legacy.get("ssh_key", "")}
    if circuit_id == "serveo_temporary":
        return {}
    if circuit_id == "tunnellio_stable":
        return {
            "domain": legacy.get("tunnellio_domain", ""),
            "ssh_key": legacy.get("ssh_key", "") or legacy.get("tunnellio_key", ""),
            "ssh_host": legacy.get("tunnel_host", "") or "tunnellio.site",
            "ssh_port": str(legacy.get("tunnel_ssh_port", "") or "2222"),
        }
    if circuit_id == "tunnellio_random":
        return {
            "api_token": legacy.get("tunnellio_token", ""),
            "base_url": legacy.get("tunnellio_base_url", "") or "https://api.tunnellio.ru",
        }
    if circuit_id == "sish":
        return {
            "ssh_host": legacy.get("tunnel_host", ""),
            "ssh_port": str(legacy.get("tunnel_ssh_port", "") or "2222"),
            "wildcard_domain": legacy.get("tunnel_domain", ""),
            "subdomain": legacy.get("serveo_hostname", ""),
            "ssh_key": legacy.get("ssh_key", ""),
        }
    if circuit_id == "reverse_proxy":
        return {"public_url": legacy.get("public_url", "")}
    return {}


def migrate_legacy(
    legacy: dict[str, Any],
    *,
    current_path: Path | None = None,
    profiles_path: Path | None = None,
) -> dict[str, Any]:
    """One-time import of an old installation. Never rotates secrets."""
    current = load_current(current_path)
    if current["areas"] or not legacy:
        return current
    workspace = str(legacy.get("workspace", "")).strip()
    if not workspace:
        return current
    circuit_id = circuit_from_legacy(legacy)
    if circuit_id:
        storage = load_profiles(profiles_path)
        if circuit_id not in storage["profiles"]:
            active = circuit(circuit_id)
            merged = active.merge(settings_from_legacy(circuit_id, legacy))
            storage["profiles"][circuit_id] = {
                "id": circuit_id,
                "settings": merged,
                "blueprintVersion": active.blueprint_version,
                "configuredAt": now_iso(),
                "updatedAt": now_iso(),
                "verifiedAt": "",
                "notes": "imported from the pre-2.4.0 flat config",
            }
            save_profiles(storage, profiles_path)
    area = upsert_area(
        current,
        workspace=workspace,
        access_mode="trusted" if legacy.get("allow_commands") else "file_only",
        connection_profile=circuit_id or None,
        port=int(legacy.get("port", 8765) or 8765),
        use_global_auth=False,
    )
    area["auth"] = _normalize_auth(
        {
            "mode": legacy.get("auth_mode", "legacy"),
            "token": legacy.get("token", ""),
            "oauthOwnerCode": legacy.get("oauth_owner_code", ""),
        }
    )
    save_current(current, current_path)
    return current
