"""Shared plumbing for circuits that drive the Tunnellio CLI.

This module carries no configuration of its own. Every value is passed in by
the calling circuit, so the random-domain and TCP-bridge circuits stay fully
independent from each other.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..base import ConnectionConfigError


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:48] or "notion-local-mcp"


def resolve_client(explicit_path: str, script_dir: Path) -> Path:
    raw = str(explicit_path or "").strip()
    candidate = Path(raw).expanduser() if raw else (script_dir / "tunnellio.exe")
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise ConnectionConfigError(
            f"Tunnellio client was not found: {candidate}. Restore tunnellio.exe next to "
            "launcher.py or point the profile at another path."
        )
    return candidate


def resolve_state_dir(explicit_path: str, config_dir: Path, circuit_id: str) -> Path:
    raw = str(explicit_path or "").strip()
    candidate = (
        Path(raw).expanduser() if raw else (config_dir / "tunnellio-state" / circuit_id)
    ).resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def verify_api_token(base_url: str, token: str, *, timeout: int = 15) -> tuple[bool, str]:
    """Confirm the API token against the Tunnellio server.

    ``POST /v1/meta`` is the cheapest authenticated endpoint. A ``plan_required``
    answer still proves the credential is real and accepted, so only genuine
    authentication failures are rejected.
    """

    token = str(token or "").strip()
    if not token:
        return False, "API token is empty."
    endpoint = str(base_url or "").strip().rstrip("/") + "/v1/meta"
    request = urllib.request.Request(
        endpoint,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if 200 <= response.status < 300:
                return True, "Token accepted by the Tunnellio server."
            return False, f"Tunnellio server answered HTTP {response.status}."
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # noqa: BLE001 - diagnostics only
            body = ""
        lowered = body.lower()
        if exc.code == 401 or "invalid_token" in lowered or "unauthorized" in lowered:
            return False, "Tunnellio rejected this token (unauthorized)."
        if exc.code == 403 and "plan" in lowered:
            return True, "Token is valid. The account plan limits this endpoint, which is expected."
        if exc.code == 403:
            return False, f"Tunnellio refused the token: {body or 'forbidden'}"
        return False, f"Tunnellio server answered HTTP {exc.code}: {body or exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"Could not reach the Tunnellio server: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return False, f"Could not reach the Tunnellio server: {exc}"


def read_snapshot(status_path: Path, config_path: Path) -> dict[str, Any]:
    """Merge the runtime status file and the runtime config snapshot."""
    merged: dict[str, Any] = {}
    for path in (status_path, config_path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def public_url_from_snapshot(snapshot: dict[str, Any]) -> str:
    if not isinstance(snapshot, dict):
        return ""
    transport = snapshot.get("transport")
    if isinstance(transport, dict):
        candidate = str(transport.get("publicUrl", "")).strip()
        if candidate:
            return candidate
    connection = snapshot.get("connection")
    if isinstance(connection, dict):
        profile = connection.get("connectionProfile")
        if isinstance(profile, dict):
            candidate = str(profile.get("publicUrl", "")).strip()
            if candidate:
                return candidate
        candidate = str(connection.get("publicUrl", "")).strip()
        if candidate:
            return candidate
    # Shapes written by the supervised runtime and by plan output.
    for key in ("runtime", "connectionProfile"):
        block = snapshot.get(key)
        if isinstance(block, dict):
            candidate = str(block.get("publicUrl", "")).strip()
            if candidate:
                return candidate
    for key in ("publicUrl", "public_url", "url"):
        candidate = str(snapshot.get(key, "")).strip()
        if candidate.startswith("http"):
            return candidate
    return ""


def wait_for_public_url(
    status_path: Path,
    config_path: Path,
    *,
    timeout_seconds: float,
    process: Any = None,
) -> str:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return ""
        url = public_url_from_snapshot(read_snapshot(status_path, config_path))
        if url:
            return url
        time.sleep(0.5)
    return public_url_from_snapshot(read_snapshot(status_path, config_path))


def log_tail(path: Path, limit: int = 3000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""
