"""Read-only access to the shipped connection blueprints.

Blueprints live in ``connections/defaults/<id>.json``. They travel with the
release, are never edited by hand, and are never written back to. A profile is
created by cloning its blueprint the first time an operator configures it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"

# Presentation order used by every menu in the product.
BLUEPRINT_ORDER = (
    "serveo_stable",
    "serveo_temporary",
    "tunnellio_stable",
    "tunnellio_random",
    "tunnellio_bridge",
    "sish",
    "reverse_proxy",
)

_cache: dict[str, dict[str, Any]] = {}


class BlueprintError(RuntimeError):
    """A shipped blueprint is missing or unreadable."""


def blueprint_path(circuit_id: str) -> Path:
    return DEFAULTS_DIR / f"{circuit_id}.json"


def load_blueprint(circuit_id: str) -> dict[str, Any]:
    """Return a private copy of one blueprint."""
    if circuit_id not in _cache:
        path = blueprint_path(circuit_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BlueprintError(f"Blueprint is missing from the release: {path}") from exc
        except json.JSONDecodeError as exc:
            raise BlueprintError(f"Blueprint is not valid JSON: {path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("settings"), dict):
            raise BlueprintError(f"Blueprint must be an object with a 'settings' object: {path}")
        raw.setdefault("id", circuit_id)
        raw.setdefault("blueprintVersion", 1)
        _cache[circuit_id] = raw
    return json.loads(json.dumps(_cache[circuit_id]))


def load_all() -> dict[str, dict[str, Any]]:
    return {circuit_id: load_blueprint(circuit_id) for circuit_id in BLUEPRINT_ORDER}


def available_ids() -> tuple[str, ...]:
    return BLUEPRINT_ORDER
