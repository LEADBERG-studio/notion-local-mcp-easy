"""Connection profile setup for Notion Local MCP Easy.

This is the only script that writes connection profiles. It is deliberately
separate from SETUP.bat and START.bat:

* START picks a work area and nothing else.
* SETUP picks folder, access mode and which configured profile an area uses.
* PROFILES (this script) is where a profile is actually built or repaired.

Profiles survive product upgrades. They only change when this script runs.

Usage:
    python profiles_setup.py            interactive menu
    python profiles_setup.py --list     print status and exit
    python profiles_setup.py --configure <circuit_id>
    python profiles_setup.py --verify <circuit_id>
    python profiles_setup.py --reset <circuit_id>
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


def status_label(circuit_id: str) -> str:
    entry = store.get_profile(circuit_id)
    if entry is None:
        active = store.circuit(circuit_id)
        if not any(question.required for question in active.questions()):
            return "ready (no input needed)"
        return "not configured"
    if not store.is_configured(circuit_id):
        return "incomplete"
    return "configured" + (" + verified" if entry.get("verifiedAt") else "")


def print_table() -> list[str]:
    ids = list(blueprints.available_ids())
    print(f"\n=== Connection profiles (Notion Local MCP Easy {product_version()}) ===\n")
    for index, circuit_id in enumerate(ids, start=1):
        active = store.circuit(circuit_id)
        print(f" {index}. {active.title}")
        print(f"      id: {circuit_id}   status: {status_label(circuit_id)}")
        setup_flow.print_summary(active, store.profile_settings(circuit_id))
        print("")
    return ids


def configure(circuit_id: str) -> int:
    active = store.circuit(circuit_id)
    settings = setup_flow.configure_circuit(active, store.profile_settings(circuit_id))
    print("\nChecking the profile...")
    ok, message = setup_flow.verify_circuit(active, settings)
    print(f"   {message}")
    if not ok:
        print("\nThe profile was NOT saved. Fix the values and run this step again.")
        return 1
    store.save_profile(circuit_id, settings, verified=True)
    print(f"\nSaved: {store.PROFILES_FILE}")
    setup_flow.print_summary(active, settings)
    print("\nAssign it to a work area with SETUP.bat when you want to switch over.")
    return 0


def verify(circuit_id: str) -> int:
    active = store.circuit(circuit_id)
    settings = store.profile_settings(circuit_id)
    ok, message = setup_flow.verify_circuit(active, settings)
    print(f"\n{active.title}: {message}")
    if ok and store.get_profile(circuit_id) is not None:
        store.save_profile(circuit_id, settings, verified=True)
    return 0 if ok else 1


def reset(circuit_id: str) -> int:
    active = store.circuit(circuit_id)
    if not setup_flow.ask_yes_no(
        f"Reset '{active.title}' back to the shipped blueprint and drop your answers?", False
    ):
        print("Nothing changed.")
        return 0
    store.reset_profile(circuit_id)
    print(f"'{active.title}' is back to its shipped defaults.")
    return 0


def show_blueprint(circuit_id: str) -> int:
    print(json.dumps(blueprints.load_blueprint(circuit_id), ensure_ascii=False, indent=2))
    print("\nThis is the read-only shipped example. Editing it by hand is not supported.")
    return 0


def menu() -> int:
    while True:
        ids = print_table()
        print("Actions:")
        print("  <n>    configure profile n")
        print("  v<n>   verify profile n")
        print("  b<n>   show the read-only blueprint for profile n")
        print("  r<n>   reset profile n to the blueprint")
        print("  q      quit")
        raw = setup_flow.default_prompt("\nChoice: ").strip().lower()
        if raw in {"q", "quit", "exit", ""}:
            return 0
        prefix = raw[0]
        number = raw[1:] if prefix in {"v", "b", "r"} else raw
        if not number.isdigit() or not 1 <= int(number) <= len(ids):
            print("\nUnknown choice.\n")
            continue
        circuit_id = ids[int(number) - 1]
        try:
            if prefix == "v":
                verify(circuit_id)
            elif prefix == "b":
                show_blueprint(circuit_id)
            elif prefix == "r":
                reset(circuit_id)
            else:
                configure(circuit_id)
        except ConnectionConfigError as exc:
            print(f"\nERROR: {exc}")
        except KeyboardInterrupt:
            print("\nCancelled.")
        print("")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure connection profiles.")
    parser.add_argument("--list", action="store_true", help="print profile status and exit")
    parser.add_argument("--configure", metavar="CIRCUIT", default="")
    parser.add_argument("--verify", metavar="CIRCUIT", default="")
    parser.add_argument("--reset", metavar="CIRCUIT", default="")
    parser.add_argument("--blueprint", metavar="CIRCUIT", default="")
    args = parser.parse_args(argv)

    try:
        if args.list:
            print_table()
            return 0
        if args.configure:
            return configure(args.configure)
        if args.verify:
            return verify(args.verify)
        if args.reset:
            return reset(args.reset)
        if args.blueprint:
            return show_blueprint(args.blueprint)
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
