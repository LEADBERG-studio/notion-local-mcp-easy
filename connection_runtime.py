"""Bridge between the launcher and the connection circuits.

Responsibilities are split on purpose:

* :func:`start_flow` is what START.bat uses. Pick a work area and go. If the
  area already has a connection profile, that is the whole interaction. If it
  does not, the operator picks a saved profile or creates one right there.
* :func:`setup_flow` is what SETUP.bat uses: folder, access mode, and which
  saved profile the area should use.
* Profiles themselves are built by ``profiles_setup.py`` (PROFILES.bat).

Every profile list shows the actual settings, not just a name. Choosing between
"Prod MCP" and "Staging MCP" is impossible if you cannot see which domain and
key each one carries.

The launcher keeps working with a flat config dict. That dict is a generated
mirror of the resolved connection, never a source of truth.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from connections import blueprints, setup_flow as flow, store
from connections.base import Circuit, ConnectionConfigError, RuntimeContext
from connections.store import ConnectionStoreError, ResolvedConnection

_ACTIVE: ResolvedConnection | None = None


def active() -> ResolvedConnection:
    if _ACTIVE is None:
        raise ConnectionStoreError(
            "No connection has been resolved yet. The launcher must call "
            "start_flow() or setup_flow() first."
        )
    return _ACTIVE


def active_or_none() -> ResolvedConnection | None:
    return _ACTIVE


def set_active(resolved: ResolvedConnection) -> ResolvedConnection:
    global _ACTIVE
    _ACTIVE = resolved
    return resolved


def reset_active() -> None:
    global _ACTIVE
    _ACTIVE = None


# ------------------------------------------------------------------ helpers


def prompt_existing_folder(label: str, default: Path) -> Path:
    while True:
        raw = flow.default_prompt(f"{label} [{default}]: ").strip().strip('"')
        candidate = Path(raw).expanduser().resolve() if raw else Path(default).resolve()
        if candidate.is_dir():
            return candidate
        print(f"   Folder does not exist: {candidate}")


def print_profile_choices(profiles: list[dict[str, Any]], current: str = "") -> None:
    """One flat numbered list, each entry with its real settings."""
    for index, entry in enumerate(profiles, start=1):
        marker = "  (current)" if entry["id"] == current else ""
        state = "" if store.is_configured(entry["id"]) else "  [INCOMPLETE]"
        print(f" {index}. {entry['name']}{marker}{state}")
        print(f"      protocol: {store.circuit(entry['circuit']).title}")
        for line in store.describe_profile(entry):
            print(f"      {line}")


def create_profile_inline() -> str:
    """Let the operator build a profile without leaving this flow."""
    import profiles_setup

    before = {entry["id"] for entry in store.list_profiles()}
    profiles_setup.new_profile_flow()
    created = [entry for entry in store.list_profiles() if entry["id"] not in before]
    return created[0]["id"] if created else ""


def choose_profile(current: str = "", *, allow_create: bool = True) -> str:
    """Pick a saved profile, or build a new one on the spot."""
    while True:
        profiles = store.list_profiles()
        print("\nConnection profile for this work area:")
        if profiles:
            print_profile_choices(profiles, current)
        else:
            print(" (no profiles saved yet)")
        print("")
        if allow_create:
            print(" n. create a new profile")
        default_index = next(
            (str(i) for i, entry in enumerate(profiles, start=1) if entry["id"] == current), ""
        )
        label = f"Choose a profile [{default_index}]: " if default_index else "Choose a profile: "
        raw = flow.default_prompt(label).strip().lower()
        if not raw and default_index:
            raw = default_index
        if raw == "n" and allow_create:
            created = create_profile_inline()
            if created:
                return created
            continue
        chosen = ""
        if raw.isdigit() and 1 <= int(raw) <= len(profiles):
            chosen = profiles[int(raw) - 1]["id"]
        elif raw:
            entry = store.find_profile(raw)
            chosen = entry["id"] if entry else ""
        if not chosen:
            print("   Enter one of the numbers above" + (", or 'n' to create one." if allow_create else "."))
            continue
        if not store.is_configured(chosen):
            print(
                f"   '{store.get_profile(chosen)['name']}' is incomplete. "
                "Finish it in PROFILES.bat, then come back here."
            )
            continue
        return chosen


def choose_access_mode(current_mode: str) -> str:
    print("\nAccess mode:")
    print(" 1. file_only  - MCP file operations stay inside the selected folder")
    print(" 2. trusted    - adds Python/Git/Node commands with your Windows user rights")
    default = "2" if current_mode == "trusted" else "1"
    while True:
        raw = flow.default_prompt(f"Choose access mode [{default}]: ").strip().lower() or default
        if raw in {"1", "file_only", "file"}:
            return "file_only"
        if raw in {"2", "trusted", "dev"}:
            return "trusted"
        print("   Enter 1 or 2.")


# -------------------------------------------------------------------- flows


def bootstrap(legacy_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load the permanent state, importing an old installation once."""
    current = store.load_current()
    if not current["areas"] and legacy_config:
        current = store.migrate_legacy(legacy_config)
        if current["areas"]:
            print("Imported the previous configuration into the new connection store.")
    return current


def choose_workspace(current: dict[str, Any], default_folder: Path) -> Path:
    """Offer the folders already known, not just the last one used.

    Setup used to prompt for a single path with the active area as the default.
    With several areas configured that hides every other one, so the operator
    had to retype a path they had already registered, and a typo silently
    created a duplicate area.
    """
    areas = list(current.get("areas", {}).values())
    if not areas:
        return prompt_existing_folder("Workspace folder", default_folder)

    print("\nKnown work areas:")
    default_index = 1
    for index, area in enumerate(areas, start=1):
        path = str(area.get("workspace", ""))
        mode = str(area.get("accessMode", "file_only"))
        profile = str(area.get("connectionProfile", "")) or "no profile"
        marker = ""
        if store.normalize_path(path) == store.normalize_path(default_folder):
            default_index = index
            marker = "  (current)"
        print(f" {index}. {path}{marker}")
        print(f"      access: {mode} | profile: {profile}")
    print(" 0. Another folder")

    while True:
        raw = flow.default_prompt(
            f"Choose a work area [{default_index}]: "
        ).strip() or str(default_index)
        if raw == "0":
            return prompt_existing_folder("Workspace folder", default_folder)
        if raw.isdigit() and 1 <= int(raw) <= len(areas):
            chosen = Path(str(areas[int(raw) - 1].get("workspace", "")))
            if chosen.is_dir():
                return chosen.resolve()
            print(f"   Folder is gone: {chosen}")
            return prompt_existing_folder("Workspace folder", default_folder)
        print(f"   Enter a number from 0 to {len(areas)}.")


def setup_flow(script_dir: Path, legacy_config: dict[str, Any] | None = None) -> ResolvedConnection:
    """SETUP.bat: choose folder, access mode and connection profile."""
    current = bootstrap(legacy_config)
    active_area = current["areas"].get(current.get("activeAreaId", "")) or {}
    default_folder = Path(active_area.get("workspace") or script_dir).resolve()

    print("\n=== Work area setup ===")
    workspace = choose_workspace(current, default_folder)
    access_mode = choose_access_mode(str(active_area.get("accessMode", "file_only")))

    existing = current["areas"].get(store.area_id_for(workspace)) or {}
    profile_id = choose_profile(str(existing.get("connectionProfile", "")))

    use_global = flow.ask_yes_no(
        "\nShare one MCP token and OAuth owner code across all work areas?\n"
        "  Yes  - switch channels without re-authorizing your MCP clients\n"
        "  No   - this folder keeps its own credentials\nUse shared credentials?",
        bool(existing.get("useGlobalAuth", False)),
    )

    store.upsert_area(
        current,
        workspace=workspace,
        access_mode=access_mode,
        connection_profile=profile_id,
        use_global_auth=use_global,
    )
    # Remember this as the standing channel, so folders added later at START
    # inherit it instead of asking again.
    if flow.ask_yes_no(
        "\nUse this connection profile for folders added later?", True
    ):
        store.set_default_profile(current, profile_id)
    store.save_current(current)
    resolved = store.resolve(current, script_dir=script_dir)
    store.save_current(current)
    print(f"\nSaved: {store.CURRENT_FILE}")
    return set_active(resolved)


def _area_line(area: dict[str, Any]) -> list[str]:
    entry = store.find_profile(str(area.get("connectionProfile", "")))
    lines = [f"      {area['workspace']}"]
    if entry is None:
        lines.append("      profile: none yet")
    else:
        lines.append(f"      profile: {entry['name']} ({store.circuit(entry['circuit']).title})")
        url = store.circuit(entry["circuit"]).static_url(entry["settings"])
        if url:
            lines.append(f"      url: {url}")
    lines.append(f"      access: {area['accessMode']}")
    return lines


def choose_area(current: dict[str, Any], script_dir: Path) -> dict[str, Any]:
    """START always starts here: which folder do we serve?

    The common setup is one outbound channel registered once in the cloud
    project, with many folders pointed at it. So the folder is the question
    worth asking, and the profile is inherited from the standing default.
    """
    while True:
        areas = list(current["areas"].values())
        active_id = current.get("activeAreaId", "")
        default_profile = store.default_profile_id(current)
        print("\n=== Work area ===")
        for index, area in enumerate(areas, start=1):
            marker = "  (last used)" if area["id"] == active_id else ""
            print(f" {index}. {area['displayName']}{marker}")
            for line in _area_line(area):
                print(line)
        print(" 0. add another folder")
        if default_profile:
            entry = store.get_profile(default_profile)
            print(f"\n    New folders will use: {entry['name']}")
        default_index = next(
            (str(i) for i, area in enumerate(areas, start=1) if area["id"] == active_id), "1"
        )
        raw = flow.default_prompt(f"\nChoose a work area [{default_index}]: ").strip()
        raw = raw or default_index
        if raw == "0":
            added = add_area(current, script_dir)
            if added is not None:
                return added
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(areas):
            return areas[int(raw) - 1]
        print("   Enter one of the numbers above, or 0 to add a folder.")


def _active_area(current: dict[str, Any]) -> dict[str, Any]:
    return current.get("areas", {}).get(str(current.get("activeAreaId", ""))) or {}


def _inherited_access_mode(current: dict[str, Any]) -> str:
    """Default the prompt to whatever the operator last chose."""
    return str(_active_area(current).get("accessMode", "file_only"))


def _inherited_global_auth(current: dict[str, Any]) -> bool:
    """Follow the auth regime already in use.

    Shared credentials are an all-or-nothing decision in practice: the point of
    the switch is to change channels without re-authorizing every client. A new
    folder that opted out on its own would defeat that, and the operator would
    only find out when their client stopped being accepted.
    """
    areas = list(current.get("areas", {}).values())
    if not areas:
        return False
    active = _active_area(current)
    if active:
        return bool(active.get("useGlobalAuth", False))
    return all(bool(area.get("useGlobalAuth", False)) for area in areas)


def _seed_global_auth(current: dict[str, Any]) -> None:
    """Promote the active folder's credentials to the shared slot.

    Turning the switch on with an empty shared slot would mint a fresh token,
    which is the very surprise this is meant to avoid, so the credentials in
    use right now become the shared ones.
    """
    shared = current.get("globalAuth") or {}
    if str(shared.get("token", "")).strip():
        return
    active = _active_area(current)
    auth = active.get("auth") or {}
    if str(auth.get("token", "")).strip():
        current["globalAuth"] = dict(auth)


def add_area(current: dict[str, Any], script_dir: Path) -> dict[str, Any] | None:
    """Add a folder during START and inherit the standing channel."""
    workspace = prompt_existing_folder("Folder to serve", Path(script_dir).resolve())
    known = current["areas"].get(store.area_id_for(workspace))
    if known is not None:
        print(f"   That folder is already saved as '{known['displayName']}'.")
        return known
    inherited = store.default_profile_id(current)
    if inherited:
        entry = store.get_profile(inherited)
        print(f"   Using the standing connection profile: {entry['name']}")
        if not flow.ask_yes_no("   Keep it for this folder?", True):
            inherited = choose_profile()
    else:
        inherited = choose_profile()
    # Ask. Forcing file_only here and telling the operator to run SETUP
    # afterwards looked tidy, but it meant the answer to "do you want full
    # access" was decided for them, and the only way to change it was another
    # trip through SETUP or a hand-edited config.
    access_mode = choose_access_mode(_inherited_access_mode(current))

    # Inherit the auth regime too. A new folder used to always get
    # useGlobalAuth=False, so resolving it minted a brand new Bearer token even
    # for an operator who had deliberately switched to shared credentials.
    use_global = _inherited_global_auth(current)
    if use_global:
        print("   Using the shared MCP token, same as the other folders.")
    elif current.get("areas"):
        # The usual shape of this product is one outbound channel with several
        # folders behind it, and the operator does not expect a new Bearer to
        # appear just because they added a folder. Offer to keep the one they
        # already have; per-folder credentials stay available by answering no.
        use_global = flow.ask_yes_no(
            "   Reuse the MCP token the other folders already use?", True
        )
        if use_global:
            _seed_global_auth(current)

    area = store.upsert_area(
        current,
        workspace=workspace,
        access_mode=access_mode,
        connection_profile=inherited,
        use_global_auth=use_global,
    )
    store.save_current(current)
    print(f"   Added '{area['displayName']}' in {access_mode} mode.")
    return area


def start_flow(script_dir: Path, legacy_config: dict[str, Any] | None = None) -> ResolvedConnection:
    """START.bat: pick the folder, then start.

    The folder is the only question in the normal case. The connection profile
    is inherited from the standing default, and is only asked about when the
    area has none and nothing can be inherited.
    """
    current = bootstrap(legacy_config)
    if not current["areas"]:
        raise ConnectionStoreError(
            "Nothing is configured yet. Run SETUP.bat once to choose a folder, an access "
            "mode and a connection profile."
        )

    area = choose_area(current, script_dir)
    current["activeAreaId"] = area["id"]

    if store.find_profile(str(area.get("connectionProfile", ""))) is None:
        inherited = store.default_profile_id(current)
        if inherited:
            entry = store.get_profile(inherited)
            print(f"\nUsing the standing connection profile: {entry['name']}")
            area["connectionProfile"] = inherited
        else:
            print(f"\nWork area '{area['displayName']}' has no usable connection profile yet.")
            area["connectionProfile"] = choose_profile()
        area["updatedAt"] = store.now_iso()
        store.save_current(current)

    resolved = store.resolve(current, script_dir=script_dir)
    store.save_current(current)
    return set_active(resolved)


# ------------------------------------------------------------- runtime glue


def legacy_config_for(
    resolved: ResolvedConnection,
    *,
    version: str,
    allowed_commands: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mirror = store.legacy_mirror(
        resolved, version=version, allowed_commands=allowed_commands, extra=extra
    )
    store.write_legacy_mirror(mirror)
    return mirror


def starts_tunnel(resolved: ResolvedConnection | None = None) -> bool:
    return (resolved or active()).circuit.starts_process


def tunnel_command(resolved: ResolvedConnection | None = None) -> list[str]:
    resolved = resolved or active()
    return resolved.circuit.build_command(resolved.settings, resolved.context)


def fallback_tunnel_command(resolved: ResolvedConnection | None = None) -> list[str] | None:
    resolved = resolved or active()
    builder = getattr(resolved.circuit, "build_fallback_command", None)
    if builder is None:
        return None
    try:
        return builder(resolved.settings, resolved.context)
    except ConnectionConfigError:
        return None


def tunnel_url(
    process: subprocess.Popen | None = None,
    lines: Any = None,
    resolved: ResolvedConnection | None = None,
) -> str:
    resolved = resolved or active()
    return resolved.circuit.resolve_url(resolved.settings, resolved.context, process, lines)


def static_url(resolved: ResolvedConnection | None = None) -> str:
    resolved = resolved or active()
    return resolved.circuit.static_url(resolved.settings)


def process_match(resolved: ResolvedConnection | None = None) -> str:
    resolved = resolved or active()
    return resolved.circuit.process_match(resolved.settings)


def health_path(resolved: ResolvedConnection | None = None) -> str:
    resolved = resolved or active()
    return resolved.circuit.health_path(resolved.settings)


def start_attempts(resolved: ResolvedConnection | None = None) -> int:
    resolved = resolved or active()
    return resolved.circuit.start_attempts(resolved.settings)


def stop_circuit(resolved: ResolvedConnection | None = None) -> None:
    resolved = resolved or active_or_none()
    if resolved is None:
        return
    try:
        resolved.circuit.stop(resolved.settings, resolved.context)
    except Exception:  # noqa: BLE001 - shutdown must never raise
        pass


def describe(resolved: ResolvedConnection | None = None) -> str:
    resolved = resolved or active()
    return f"{resolved.profile_name} ({resolved.circuit.title})"


__all__ = [
    "Circuit",
    "ConnectionConfigError",
    "ConnectionStoreError",
    "ResolvedConnection",
    "RuntimeContext",
    "active",
    "active_or_none",
    "bootstrap",
    "choose_profile",
    "describe",
    "fallback_tunnel_command",
    "health_path",
    "legacy_config_for",
    "process_match",
    "reset_active",
    "set_active",
    "setup_flow",
    "start_attempts",
    "start_flow",
    "starts_tunnel",
    "static_url",
    "stop_circuit",
    "tunnel_command",
    "tunnel_url",
]
