"""Small backend shim for IDE Bridge.

IDE Bridge is queue/poll only, but the OpenAI-compatible worker still exposes
/v1/models. Keep discovery lightweight and local so the plugin is fully
self-contained and does not depend on ide_gateway internals.
"""
from __future__ import annotations

import os
from typing import Any


def _split_model_names(raw: str) -> list[str]:
    names: list[str] = []
    for item in str(raw or "").replace("\n", ",").replace(";", ",").split(","):
        item = item.strip().strip('"\'')
        if item and item not in names:
            names.append(item)
    return names


def _append_model(models: list[dict[str, Any]], model_id: str, owner: str) -> None:
    model_id = str(model_id or "").strip()
    if not model_id or model_id in {m["id"] for m in models}:
        return
    models.append({"id": model_id, "object": "model", "created": 0, "owned_by": owner})


def discover_models(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or {}
    models: list[dict[str, Any]] = []
    for name in _split_model_names(str(config.get("upstream_model", ""))):
        _append_model(models, name, "config")
    for name in _split_model_names(str(config.get("extra_models", ""))):
        _append_model(models, name, "config")
    for key in ("IDE_BRIDGE_MODEL", "IDE_BRIDGE_EXTRA_MODELS", "MODEL", "MODELS", "AVAILABLE_MODELS"):
        for name in _split_model_names(os.environ.get(key, "")):
            _append_model(models, name, "env")
    _append_model(models, "ide-bridge", "alias")
    return models
