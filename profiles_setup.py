"""Connection profile setup for Notion Local MCP Easy.

A connection profile is a **named instance** of a circuit, not the circuit
itself. Two folders can use the same protocol with different domains, keys or
relays, so each combination is saved under its own name and picked from one
flat numbered list.

This is the only script that writes connection profiles. It is deliberately
separate from SETUP.bat and START.bat:

* START picks a work area and starts. If the area already has a profile, that
  is one keypress.
* SETUP picks folder, access mode and which saved profile the area uses.
* PROFILES (this script) is where profiles are built, edited and named.

Profiles survive product upgrades. They only change when this script runs.

Usage:
    python profiles_setup.py               interactive menu
    python profiles_setup.py --list        print profiles with their settings
    python profiles_setup.py --new <circuit_id>
    python profiles_setup.py --edit <profile_id>
    python profiles_setup.py --verify <profile_id>
    python profiles_setup.py --rename <profile_id>
    python profiles_setup.py --duplicate <profile_id>
    python profiles_setup.py --delete <profile_id>
    python profiles_setup.py --reset <profile_id>
    python profiles_setup.py --blueprint <circuit_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from connections import blueprints, setup_flow, store  # noqa: E402
from connections.base import ConnectionConfigError, ConnectionSetupAborted  # noqa: E402

VERSION_FILE = SCRIPT_DIR / "VERSION"


def product_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def status_label(entry: dict) -> str:
    if not store.is_configured(entry["id"]):
        return "INCOMPLETE"
    return "ready + verified" if entry.get("verifiedAt") else "ready"


# ------------------------------------------------------------------ printing


def print_profiles() -> list[dict]:
    """The numbered list. Always shows settings, never just a name."""
    profiles = store.list_profiles()
    print(f"\n=== Connection profiles (Notion Local MCP Easy {product_version()}) ===\n")
    if not profiles:
        print(" No profiles yet. Create one with 'n'.\n")
        return profiles
    for index, entry in enumerate(profiles, start=1):
        print(f" {index}. {entry['name']}")
        print(f"      protocol: {store.circuit(entry['circuit']).title}   status: {status_label(entry)}")
        for line in store.describe_profile(entry):
            print(f"      {line}")
        print("")
    return profiles


def print_circuits() -> list[str]:
    ids = list(blueprints.available_ids())
    print("\nAvailable protocols:\n")
    for index, circuit_id in enumerate(ids, start=1):
        active = store.circuit(circuit_id)
        existing = len(store.profiles_for_circuit(circuit_id))
        suffix = f"   ({existing} profile(s) already)" if existing else ""
        print(f" {index}. {active.title}{suffix}")
        print(f"      {active.blueprint.get('summary', active.summary)}")
        required = [q.prompt for q in active.questions() if q.required]
        print(f"      asks: {', '.join(required) if required else 'nothing'}")
        print("")
    return ids


def pick(items: list, label: str) -> int | None:
    if not items:
        return None
    raw = setup_flow.default_prompt(f"{label} [1-{len(items)}, Enter to cancel]: ").strip()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        return int(raw) - 1
    print("   Enter one of the numbers above.")
    return None


# ------------------------------------------------------------------- actions


def ask_name(default: str, *, taken_hint: str = "") -> str:
    if taken_hint:
        print(f"   {taken_hint}")
    raw = setup_flow.default_prompt(f"Profile name [{default}]: ").strip()
    return raw or default


def create(circuit_id: str) -> int:
    active = store.circuit(circuit_id)
    settings = setup_flow.configure_circuit(active, active.default_settings())
    print("\nChecking the profile...")
    ok, message = setup_flow.verify_circuit(active, settings)
    print(f"   {message}")
    if not ok:
        print("\nThe profile was NOT saved. Fix the values and try again.")
        return 1
    print("")
    name = ask_name(
        store.suggest_profile_name(circuit_id, settings),
        taken_hint="Give it a name you will recognise in SETUP, for example 'Prod MCP'.",
    )
    entry = store.create_profile(circuit_id, name, settings, verified=True)
    print(f"\nSaved profile '{entry['name']}' in {store.PROFILES_FILE}")
    for line in store.describe_profile(entry):
        print(f"   {line}")
    print("\nAssign it to a work area with SETUP.bat when you want to switch over.")
    return 0


def edit(profile_id: str) -> int:
    entry = store.get_profile(profile_id)
    if entry is None:
        print(f"Unknown profile '{profile_id}'.")
        return 1
    active = store.circuit(entry["circuit"])
    print(f"\nCurrent settings of '{entry['name']}':")
    for line in store.describe_profile(entry):
        print(f"   {line}")
    settings = setup_flow.configure_circuit(active, entry["settings"])
    print("\nChecking the profile...")
    ok, message = setup_flow.verify_circuit(active, settings)
    print(f"   {message}")
    if not ok:
        print("\nNothing was changed. Fix the values and try again.")
        return 1
    updated = store.update_profile(entry["id"], settings=settings, verified=True)
    print(f"\nUpdated '{updated['name']}'.")
    for line in store.describe_profile(updated):
        print(f"   {line}")
    return 0


def verify(profile_id: str) -> int:
    entry = store.get_profile(profile_id)
    if entry is None:
        print(f"Unknown profile '{profile_id}'.")
        return 1
    active = store.circuit(entry["circuit"])
    ok, message = setup_flow.verify_circuit(active, entry["settings"])
    print(f"\n{entry['name']}: {message}")
    if ok:
        store.update_profile(entry["id"], verified=True)
    return 0 if ok else 1


def rename(profile_id: str) -> int:
    entry = store.get_profile(profile_id)
    if entry is None:
        print(f"Unknown profile '{profile_id}'.")
        return 1
    updated = store.update_profile(entry["id"], name=ask_name(entry["name"]))
    print(f"Renamed to '{updated['name']}'.")
    return 0


def duplicate(profile_id: str) -> int:
    """Copy a profile, then change what differs.

    This is the fast path for 'same protocol, different domain or key'.
    """
    entry = store.get_profile(profile_id)
    if entry is None:
        print(f"Unknown profile '{profile_id}'.")
        return 1
    active = store.circuit(entry["circuit"])
    print(f"\nCopying '{entry['name']}'. Change what differs, keep the rest.")
    settings = setup_flow.configure_circuit(active, entry["settings"])
    ok, message = setup_flow.verify_circuit(active, settings)
    print(f"   {message}")
    if not ok:
        print("\nThe copy was NOT saved.")
        return 1
    name = ask_name(store.suggest_profile_name(entry["circuit"], settings))
    created = store.create_profile(entry["circuit"], name, settings, verified=True)
    print(f"\nCreated '{created['name']}'.")
    return 0


def delete(profile_id: str) -> int:
    entry = store.get_profile(profile_id)
    if entry is None:
        print(f"Unknown profile '{profile_id}'.")
        return 1
    users = [
        area["displayName"]
        for area in store.load_current()["areas"].values()
        if str(area.get("connectionProfile", "")) in {entry["id"], entry["circuit"]}
    ]
    if users:
        print(f"\nUsed by work area(s): {', '.join(users)}.")
        print("They will ask for a new profile at the next start.")
    if not setup_flow.ask_yes_no(f"Delete profile '{entry['name']}'?", False):
        print("Nothing changed.")
        return 0
    store.delete_profile(entry["id"])
    print(f"Deleted '{entry['name']}'.")
    return 0


def reset(profile_id: str) -> int:
    entry = store.get_profile(profile_id)
    label = entry["name"] if entry else profile_id
    if not setup_flow.ask_yes_no(f"Reset '{label}' back to the shipped blueprint?", False):
        print("Nothing changed.")
        return 0
    store.reset_profile(profile_id)
    print(f"'{label}' is back to its shipped defaults.")
    return 0


def show_blueprint(circuit_id: str) -> int:
    print(json.dumps(blueprints.load_blueprint(circuit_id), ensure_ascii=False, indent=2))
    print("\nThis is the read-only shipped example. Editing it by hand is not supported.")
    return 0


# ---------------------------------------------------------------------- menu


def new_profile_flow() -> int:
    ids = print_circuits()
    index = pick(ids, "Choose a protocol")
    return 0 if index is None else create(ids[index])


ACTIONS = {
    "e": ("edit", edit),
    "v": ("verify", verify),
    "r": ("rename", rename),
    "d": ("duplicate", duplicate),
    "x": ("delete", delete),
    "z": ("reset to blueprint", reset),
}


def menu() -> int:
    while True:
        profiles = print_profiles()
        print("Actions:")
        print("  n      create a new profile")
        if profiles:
            print("  e<n>   edit profile n")
            print("  v<n>   verify profile n")
            print("  r<n>   rename profile n")
            print("  d<n>   duplicate profile n (same protocol, different domain or key)")
            print("  x<n>   delete profile n")
            print("  z<n>   reset profile n to its blueprint")
        print("  b      show a protocol blueprint")
        print("  q      quit")
        raw = setup_flow.default_prompt("\nChoice: ").strip().lower()
        if raw in {"q", "quit", "exit", ""}:
            return 0
        try:
            if raw == "n":
                new_profile_flow()
            elif raw == "b":
                ids = print_circuits()
                index = pick(ids, "Show blueprint for")
                if index is not None:
                    show_blueprint(ids[index])
            elif raw[0] in ACTIONS and raw[1:].isdigit():
                number = int(raw[1:])
                if not 1 <= number <= len(profiles):
                    print("\nNo profile with that number.\n")
                    continue
                ACTIONS[raw[0]][1](profiles[number - 1]["id"])
            else:
                print("\nUnknown choice.\n")
        except ConnectionConfigError as exc:
            print(f"\nERROR: {exc}")
        except KeyboardInterrupt:
            print("\nCancelled.")
        print("")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and manage connection profiles.")
    parser.add_argument("--list", action="store_true", help="print profiles with their settings")
    parser.add_argument("--new", metavar="CIRCUIT", default="")
    parser.add_argument("--edit", metavar="PROFILE", default="")
    parser.add_argument("--verify", metavar="PROFILE", default="")
    parser.add_argument("--rename", metavar="PROFILE", default="")
    parser.add_argument("--duplicate", metavar="PROFILE", default="")
    parser.add_argument("--delete", metavar="PROFILE", default="")
    parser.add_argument("--reset", metavar="PROFILE", default="")
    parser.add_argument("--blueprint", metavar="CIRCUIT", default="")
    args = parser.parse_args(argv)

    try:
        if args.list:
            print_profiles()
            return 0
        for flag, handler in (
            (args.new, create),
            (args.edit, edit),
            (args.verify, verify),
            (args.rename, rename),
            (args.duplicate, duplicate),
            (args.delete, delete),
            (args.reset, reset),
            (args.blueprint, show_blueprint),
        ):
            if flag:
                return handler(flag)
        return menu()
    except ConnectionSetupAborted as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except ConnectionConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
