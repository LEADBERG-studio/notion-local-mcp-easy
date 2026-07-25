"""Shared auth primitives for Local MCP Easy.

This module defines the authorization modes and the OAuth scope model used by
the MCP tools. It has no dependencies on the HTTP layer so it can be imported
from the server, the launcher and the tests alike.
"""
from __future__ import annotations

from urllib.parse import urlsplit

AUTH_MODE_LEGACY = "legacy"
AUTH_MODE_OAUTH = "oauth"
AUTH_MODE_DUAL = "dual"
AUTH_MODES = (AUTH_MODE_LEGACY, AUTH_MODE_OAUTH, AUTH_MODE_DUAL)

SCOPE_FILES_READ = "mcp:files:read"
SCOPE_FILES_WRITE = "mcp:files:write"
SCOPE_COMMANDS_RUN = "mcp:commands:run"
SCOPE_GIT = "mcp:git"

ALL_SCOPES = (
    SCOPE_FILES_READ,
    SCOPE_FILES_WRITE,
    SCOPE_COMMANDS_RUN,
    SCOPE_GIT,
)

SCOPE_DESCRIPTIONS = {
    SCOPE_FILES_READ: "Read files and directories inside the workspace",
    SCOPE_FILES_WRITE: "Create, edit, move and delete files inside the workspace",
    SCOPE_COMMANDS_RUN: (
        "Run allow-listed developer commands. In trusted developer mode these "
        "programs (Python, Git, Node, ...) run with the operating-system user's "
        "rights and can reach files and the network OUTSIDE the workspace — treat "
        "this as near-full system access, not a workspace-scoped permission."
    ),
    SCOPE_GIT: "Inspect Git repositories and manage repo context files",
}

DEFAULT_SCOPES = (SCOPE_FILES_READ, SCOPE_FILES_WRITE)
LEGACY_CLIENT_ID = "legacy-master-token"


def parse_auth_mode(raw: str | None) -> str:
    value = (raw or AUTH_MODE_LEGACY).strip().lower()
    if value not in AUTH_MODES:
        raise ValueError(
            f"Invalid MCP_AUTH_MODE: {raw!r}. Expected one of: {', '.join(AUTH_MODES)}"
        )
    return value


def normalize_resource(url: str) -> str:
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        return f"{scheme}://{(parts.netloc or '').lower()}{parts.path.rstrip('/')}"
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return f"{scheme}://{host}{path}"


def resources_match(candidate: str | None, canonical: str) -> bool:
    if not candidate:
        return False
    return normalize_resource(candidate) == normalize_resource(canonical)
