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
import re
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
PROFILES_SCHEMA_VERSION = 3

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


def slugify_profile_name(name: str) -> str:
    """Stable id for a saved profile, derived from its human name."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return cleaned[:48] or "profile"


def default_profiles() -> dict[str, Any]:
    return {"schemaVersion": PROFILES_SCHEMA_VERSION, "profiles": {}}


def _normalize_profile(profile_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    """One saved profile instance.

    A profile is a named instance of a circuit, not the circuit itself. Two
    folders can use the same protocol with different domains or keys, so each
    combination is saved under its own name.
    """
    circuit_id = str(entry.get("circuit") or entry.get("id") or profile_id).strip()
    if circuit_id not in CIRCUIT_CLASSES:
        return None
    settings = entry.get("settings") if isinstance(entry.get("settings"), dict) else {}
    active = circuit(circuit_id)
    name = str(entry.get("name") or "").strip() or active.title
    return {
        "id": str(profile_id),
        "name": name,
        "circuit": circuit_id,
        "settings": active.merge(settings),
        "blueprintVersion": int(entry.get("blueprintVersion", 1) or 1),
        "createdAt": str(entry.get("createdAt") or entry.get("configuredAt") or ""),
        "updatedAt": str(entry.get("updatedAt", "")),
        "verifiedAt": str(entry.get("verifiedAt", "")),
        "notes": str(entry.get("notes", "")),
    }


def load_profiles(path: Path | None = None) -> dict[str, Any]:
    raw = _read_json(path or PROFILES_FILE)
    storage = default_profiles()
    entries = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    version = int(raw.get("schemaVersion", PROFILES_SCHEMA_VERSION) or PROFILES_SCHEMA_VERSION)
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        if version < 3 and "circuit" not in entry:
            # v2 stored exactly one profile per circuit, keyed by circuit id.
            # Keep that key as the instance id so existing areas still resolve.
            entry = {**entry, "circuit": str(key)}
        normalized = _normalize_profile(str(key), entry)
        if normalized is not None:
            storage["profiles"][normalized["id"]] = normalized
    return storage


def save_profiles(storage: dict[str, Any], path: Path | None = None) -> Path:
    payload = {
        "schemaVersion": PROFILES_SCHEMA_VERSION,
        "profiles": storage.get("profiles") or {},
    }
    return _write_json(path or PROFILES_FILE, payload)


def list_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    """All saved profiles, ordered so the console can number them 1..N."""
    profiles = list(load_profiles(path)["profiles"].values())
    order = {cid: index for index, cid in enumerate(blueprints.available_ids())}
    profiles.sort(key=lambda item: (order.get(item["circuit"], 99), item["name"].lower()))
    return profiles


def get_profile(profile_id: str, path: Path | None = None) -> dict[str, Any] | None:
    return load_profiles(path)["profiles"].get(str(profile_id))


def find_profile(reference: str, path: Path | None = None) -> dict[str, Any] | None:
    """Resolve a profile by id, by name, or by circuit id for old areas."""
    reference = str(reference or "").strip()
    if not reference:
        return None
    profiles = load_profiles(path)["profiles"]
    if reference in profiles:
        return profiles[reference]
    lowered = reference.lower()
    for entry in profiles.values():
        if entry["name"].lower() == lowered:
            return entry
    # Pre-2.4.1 areas stored a bare circuit id. Only accept it when there is
    # exactly one profile for that circuit, so the choice is never a guess.
    matches = [entry for entry in profiles.values() if entry["circuit"] == reference]
    return matches[0] if len(matches) == 1 else None


def profiles_for_circuit(circuit_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    return [entry for entry in list_profiles(path) if entry["circuit"] == circuit_id]


def profile_settings(profile_id: str, path: Path | None = None) -> dict[str, Any]:
    """Settings of a saved profile, or a pristine blueprint clone."""
    entry = get_profile(profile_id, path)
    if entry is not None:
        return dict(entry["settings"])
    if str(profile_id) in CIRCUIT_CLASSES:
        return circuit(str(profile_id)).default_settings()
    raise ConnectionConfigError(f"Unknown connection profile '{profile_id}'.")


def unique_profile_id(name: str, path: Path | None = None) -> str:
    base = slugify_profile_name(name)
    taken = set(load_profiles(path)["profiles"])
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    return f"{base}-{secrets.token_hex(3)}"


def suggest_profile_name(circuit_id: str, settings: dict[str, Any] | None = None) -> str:
    """A readable default name built from what makes this profile distinct."""
    active = circuit(circuit_id)
    merged = active.merge(settings)
    for key in ("domain", "hostname", "subdomain", "public_url"):
        value = str(merged.get(key, "")).strip()
        if value:
            label = value.replace("https://", "").replace("http://", "").strip("/")
            return f"{active.title} - {label}"
    return active.title


def create_profile(
    circuit_id: str,
    name: str,
    settings: dict[str, Any],
    *,
    verified: bool = False,
    profile_id: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    active = circuit(circuit_id)
    storage = load_profiles(path)
    resolved_id = str(profile_id).strip() or unique_profile_id(name, path)
    entry = {
        "id": resolved_id,
        "name": str(name).strip() or active.title,
        "circuit": circuit_id,
        "settings": active.merge(settings),
        "blueprintVersion": active.blueprint_version,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "verifiedAt": now_iso() if verified else "",
        "notes": "",
    }
    storage["profiles"][resolved_id] = entry
    save_profiles(storage, path)
    return entry


def update_profile(
    profile_id: str,
    *,
    settings: dict[str, Any] | None = None,
    name: str | None = None,
    verified: bool | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    storage = load_profiles(path)
    entry = storage["profiles"].get(str(profile_id))
    if entry is None:
        raise ConnectionConfigError(f"Unknown connection profile '{profile_id}'.")
    active = circuit(entry["circuit"])
    if settings is not None:
        entry["settings"] = active.merge(settings)
    if name is not None and str(name).strip():
        entry["name"] = str(name).strip()
    if verified is not None:
        entry["verifiedAt"] = now_iso() if verified else ""
    entry["blueprintVersion"] = active.blueprint_version
    entry["updatedAt"] = now_iso()
    storage["profiles"][entry["id"]] = entry
    save_profiles(storage, path)
    return entry


def delete_profile(profile_id: str, path: Path | None = None) -> bool:
    storage = load_profiles(path)
    removed = storage["profiles"].pop(str(profile_id), None) is not None
    if removed:
        save_profiles(storage, path)
    return removed


def reset_profile(profile_id: str, path: Path | None = None) -> dict[str, Any]:
    """Drop operator answers and return to the untouched shipped blueprint."""
    entry = get_profile(profile_id, path)
    if entry is None:
        if str(profile_id) in CIRCUIT_CLASSES:
            return circuit(str(profile_id)).default_settings()
        raise ConnectionConfigError(f"Unknown connection profile '{profile_id}'.")
    defaults = circuit(entry["circuit"]).default_settings()
    update_profile(entry["id"], settings=defaults, verified=False, path=path)
    return defaults


def is_configured(profile_id: str, path: Path | None = None) -> bool:
    entry = get_profile(profile_id, path)
    if entry is None:
        if str(profile_id) not in CIRCUIT_CLASSES:
            return False
        active = circuit(str(profile_id))
        return not any(question.required for question in active.questions())
    return circuit(entry["circuit"]).is_configured(entry["settings"])


def configured_ids(path: Path | None = None) -> list[str]:
    return [entry["id"] for entry in list_profiles(path) if is_configured(entry["id"], path)]


def describe_profile(entry: dict[str, Any]) -> list[str]:
    """Human-readable settings of a saved profile.

    Setup shows this next to the name: an operator cannot decide whether to
    change a profile without seeing what is actually in it.
    """
    return circuit(entry["circuit"]).summary_lines(entry["settings"])


# --------------------------------------------------------------------- areas


def default_current() -> dict[str, Any]:
    return {
        "schemaVersion": CURRENT_SCHEMA_VERSION,
        "activeAreaId": "",
        "areas": {},
        "globalAuth": {"mode": "legacy", "token": "", "oauthOwnerCode": ""},
        # The standing outbound channel. The common setup is one configured
        # protocol registered in the cloud project, with many folders pointed
        # at it, so a new folder should inherit this instead of asking.
        "defaultProfile": "",
    }


def default_profile_id(current: dict[str, Any], profiles_path: Path | None = None) -> str:
    """The profile a new work area should inherit.

    Explicit default first, then the profile most areas already use, then the
    only saved profile. Returns an empty string when there is nothing to
    inherit, in which case the caller must ask.
    """
    explicit = find_profile(str(current.get("defaultProfile", "")), profiles_path)
    if explicit is not None and is_configured(explicit["id"], profiles_path):
        return explicit["id"]
    counts: dict[str, int] = {}
    for area in current.get("areas", {}).values():
        entry = find_profile(str(area.get("connectionProfile", "")), profiles_path)
        if entry is not None and is_configured(entry["id"], profiles_path):
            counts[entry["id"]] = counts.get(entry["id"], 0) + 1
    if counts:
        return max(sorted(counts), key=lambda key: counts[key])
    usable = [entry["id"] for entry in list_profiles(profiles_path) if is_configured(entry["id"], profiles_path)]
    return usable[0] if len(usable) == 1 else ""


def set_default_profile(current: dict[str, Any], profile_id: str) -> None:
    current["defaultProfile"] = str(profile_id or "")


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
        # A profile reference: the id of a saved profile instance. Old areas
        # may still hold a bare circuit id, which find_profile() resolves.
        "connectionProfile": profile,
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
    current["defaultProfile"] = str(raw.get("defaultProfile", ""))
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
        "defaultProfile": str(current.get("defaultProfile", "")),
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
    profile: dict[str, Any] | None = None

    @property
    def profile_name(self) -> str:
        return str((self.profile or {}).get("name") or self.circuit.title)

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
    reference = str(area.get("connectionProfile", "")).strip()
    if not reference:
        raise ConnectionStoreError(
            f"Work area '{area.get('displayName')}' has no connection profile assigned. "
            "Run SETUP.bat and pick one."
        )
    entry = find_profile(reference, profiles_path)
    if entry is None:
        raise ConnectionStoreError(
            f"Connection profile '{reference}' no longer exists. "
            "Run SETUP.bat to pick another one, or PROFILES.bat to create it."
        )
    if not is_configured(entry["id"], profiles_path):
        raise ConnectionStoreError(
            f"Connection profile '{entry['name']}' is not configured yet. "
            "Run PROFILES.bat to finish it, then select it in SETUP.bat."
        )
    active = circuit(entry["circuit"])
    settings = active.validate(entry["settings"])
    profile = entry
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
        area=area, circuit=active, settings=settings, auth=auth, context=context, profile=profile
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
        "connection_profile_id": str((resolved.profile or {}).get("id", "")),
        "connection_profile_name": resolved.profile_name,
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
    profile_reference = ""
    if circuit_id:
        merged = circuit(circuit_id).merge(settings_from_legacy(circuit_id, legacy))
        existing = profiles_for_circuit(circuit_id, profiles_path)
        if existing:
            profile_reference = existing[0]["id"]
        else:
            entry = create_profile(
                circuit_id,
                suggest_profile_name(circuit_id, merged),
                merged,
                profile_id=circuit_id,
                path=profiles_path,
            )
            profile_reference = entry["id"]
    area = upsert_area(
        current,
        workspace=workspace,
        access_mode="trusted" if legacy.get("allow_commands") else "file_only",
        connection_profile=profile_reference or None,
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
