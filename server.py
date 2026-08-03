from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import datetime as dt
import fnmatch
import functools
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from urllib.parse import unquote, urlsplit

from core import (
    DEFAULT_ALLOWED_COMMANDS,
    DEFAULT_EXCLUDES,
    _consteq,
    normalized_program_name,
    resolve_program,
    safe_path,
    should_skip,
)
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.routes import TOKEN_PATH
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from plugin_runtime import PluginError, PluginManager
from starlette.middleware.gzip import GZipMiddleware

from bulk_tools import (
    ReplaceHit,
    apply_unified_diff,
    first_change_preview,
    render_batch,
    replace_in_text,
    tail_lines,
)
from transport_guard import TransportGuardMiddleware, stats as transport_stats
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse

from auth import (
    ALL_SCOPES,
    AUTH_MODE_DUAL,
    AUTH_MODE_LEGACY,
    AUTH_MODE_OAUTH,
    ConsentHandler,
    LegacyTokenVerifier,
    LocalOAuthProvider,
    OAuthStore,
    SCOPE_COMMANDS_RUN,
    SCOPE_FILES_READ,
    SCOPE_FILES_WRITE,
    SCOPE_GIT,
    build_auth_settings,
    parse_auth_mode,
    protected_resource_document,
    resource_url_for,
)
from auth.oauth import hash_client_secret

TOKEN = os.environ.get("MCP_TOKEN", "").strip()
BASE_DIR = Path(os.environ.get("MCP_BASE_DIR", str(Path.home() / "Documents"))).resolve()
SERVER_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("MCP_PORT", "8765"))
STABLE_HOSTNAME = os.environ.get("MCP_SERVEO_HOSTNAME", "").strip().lower()
SERVEO_SUFFIX = ".serveousercontent.com"
SERVER_NAME = "Notion Local MCP Easy"
SERVER_VERSION = (SERVER_DIR / "VERSION").read_text(encoding="utf-8").strip() if (SERVER_DIR / "VERSION").is_file() else "dev"
try:
    AUTH_MODE = parse_auth_mode(os.environ.get("MCP_AUTH_MODE"))
except ValueError as exc:
    raise RuntimeError(str(exc)) from exc
OAUTH_ENABLED = AUTH_MODE in (AUTH_MODE_OAUTH, AUTH_MODE_DUAL)
OWNER_CODE = os.environ.get("MCP_OAUTH_OWNER_CODE", "").strip()
_default_public_url = (
    f"https://{STABLE_HOSTNAME}{SERVEO_SUFFIX}"
    if STABLE_HOSTNAME
    else f"http://127.0.0.1:{PORT}"
)
PUBLIC_URL = (os.environ.get("MCP_PUBLIC_URL", "").strip() or _default_public_url).rstrip("/")
PUBLIC_HOST = (urlsplit(PUBLIC_URL).hostname or "").lower()
OAUTH_STATE_DIR = Path(
    os.environ.get("MCP_OAUTH_STATE_DIR", "").strip()
    or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LocalMcpEasy"
)
OAUTH_ACCESS_TTL = int(os.environ.get("MCP_OAUTH_ACCESS_TTL", "3600"))
OAUTH_REFRESH_TTL = int(os.environ.get("MCP_OAUTH_REFRESH_TTL", str(30 * 24 * 3600)))
OAUTH_MAX_CLIENTS = int(os.environ.get("MCP_OAUTH_MAX_CLIENTS", "100"))
OAUTH_UNUSED_CLIENT_TTL = int(os.environ.get("MCP_OAUTH_UNUSED_CLIENT_TTL", "3600"))
OAUTH_CONSENT_MAX_ATTEMPTS = int(os.environ.get("MCP_OAUTH_CONSENT_MAX_ATTEMPTS", "5"))
OAUTH_CONSENT_FAILURE_WINDOW = int(
    os.environ.get("MCP_OAUTH_CONSENT_FAILURE_WINDOW_SECONDS", "60")
)
OAUTH_CONSENT_MAX_FAILURES = int(
    os.environ.get("MCP_OAUTH_CONSENT_MAX_FAILURES", "10")
)
OAUTH_OWNER_GRANT_SCOPES = [
    scope
    for scope in os.environ.get("MCP_OAUTH_OWNER_GRANT_SCOPES", "").split()
    if scope in ALL_SCOPES
]


def display_path(path: Path) -> Path | str:
    """Return a BASE_DIR-relative display path robust to Windows 8.3 aliases."""
    path = Path(path)
    try:
        return path.relative_to(BASE_DIR)
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(BASE_DIR.resolve())
    except ValueError:
        return path

ALLOW_COMMANDS = os.environ.get("MCP_ALLOW_COMMANDS", "0").lower() in {"1", "true", "yes"}
ALLOWED_COMMANDS = {
    item.strip().lower()
    for item in os.environ.get(
        "MCP_ALLOWED_COMMANDS", ",".join(sorted(DEFAULT_ALLOWED_COMMANDS))
    ).split(",")
    if item.strip()
}
EXCLUDES = set(DEFAULT_EXCLUDES)
MAX_TEXT_FILE = 5 * 1024 * 1024
MAX_WRITE = 2 * 1024 * 1024
MAX_COPY_MOVE_BYTES = int(
    os.environ.get("MCP_MAX_COPY_MOVE_BYTES", "") or 100 * 1024 * 1024
)
MAX_COMMAND_OUTPUT = 200_000
MAX_RESULTS = 1000
MAX_OUTPUT_CHARS = 10_000
DEFAULT_READ_LINES = 400
CHUNK_CHAR_LIMIT = 9_500

# --- Transport hardening -------------------------------------------------
# The server is almost always reached through a reverse tunnel, and a tunnel
# punishes two things: idle connection churn and large single responses.
#
# Uvicorn's default keep-alive is 5 seconds. A client that reuses its HTTP/1.1
# connection for a burst of small calls can send a request on a socket the
# server is closing at that exact moment; the relay has nothing to forward it
# to and answers 502 Bad Gateway. That is the "many small requests kill the
# transport" symptom. Holding connections open removes the race entirely.
KEEP_ALIVE_SECONDS = max(5, int(os.environ.get("MCP_KEEP_ALIVE_SECONDS", "120")))
# Back-pressure instead of collapse: a tunnel is one TCP path, so accepting an
# unbounded number of concurrent requests only queues them where nobody can see
# it. Refusing excess work is recoverable; a dead transport is not.
LIMIT_CONCURRENCY = max(8, int(os.environ.get("MCP_LIMIT_CONCURRENCY", "64")))
SOCKET_BACKLOG = max(128, int(os.environ.get("MCP_SOCKET_BACKLOG", "512")))
# Headers can get large behind proxies that append forwarding metadata.
H11_MAX_INCOMPLETE_EVENT_SIZE = max(
    16 * 1024, int(os.environ.get("MCP_MAX_HEADER_BYTES", str(64 * 1024)))
)
GRACEFUL_SHUTDOWN_SECONDS = max(1, int(os.environ.get("MCP_GRACEFUL_SHUTDOWN", "5")))
# Compress anything worth compressing. JSON-RPC payloads are text and shrink by
# roughly an order of magnitude, which is the cheapest possible fix for "large
# outputs kill the transport".
GZIP_MIN_SIZE = max(256, int(os.environ.get("MCP_GZIP_MIN_SIZE", "1024")))
TEMP_DIRNAME = "temp"
TEMP_PATH_PREFIX = "@temp/"
TEMP_FILE_TTL_SECONDS = 24 * 60 * 60
ORPHAN_SWEEP_MIN_INTERVAL_SECONDS = 60.0
MAX_COMMAND_TIMEOUT = 300
MAX_COMMAND_JOBS = max(1, int(os.environ.get("MCP_MAX_COMMAND_JOBS", "4")))
JOB_RETENTION_SECONDS = max(60, int(os.environ.get("MCP_COMMAND_JOB_RETENTION_SECONDS", "600")))
MAX_BACKGROUND_COMMAND_OUTPUT = max(
    MAX_COMMAND_OUTPUT,
    int(os.environ.get("MCP_MAX_BACKGROUND_COMMAND_OUTPUT", str(MAX_TEXT_FILE))),
)
BACKGROUND_COMMAND_READ_CHUNK = 64 * 1024
REPO_CONTEXT_FILE = "agent-repo-config.local.json"
REPO_CONTEXT_SCHEMA_VERSION = 3

if not TOKEN:
    raise RuntimeError("MCP_TOKEN is required")
if not BASE_DIR.is_dir():
    raise RuntimeError(f"MCP_BASE_DIR does not exist: {BASE_DIR}")
if OAUTH_ENABLED:
    if not OWNER_CODE:
        raise RuntimeError(
            "MCP_OAUTH_OWNER_CODE is required in oauth/dual mode. "
            "Run OAUTH_SETUP.bat (launcher.py --oauth) to configure it."
        )
    _is_local_issuer = PUBLIC_HOST in {"127.0.0.1", "localhost"}
    if not PUBLIC_URL.startswith("https://") and not _is_local_issuer:
        raise RuntimeError(
            "OAuth requires a stable https public URL (or 127.0.0.1 for local "
            f"testing); got: {PUBLIC_URL}"
        )

oauth_provider: LocalOAuthProvider | None = None
_fastmcp_auth_kwargs = {}
if OAUTH_ENABLED:
    _legacy_verifier = LegacyTokenVerifier(TOKEN) if AUTH_MODE == AUTH_MODE_DUAL else None
    oauth_provider = LocalOAuthProvider(
        store=OAuthStore(OAUTH_STATE_DIR / "oauth_state.json"),
        issuer_url=PUBLIC_URL,
        canonical_resource=resource_url_for(PUBLIC_URL),
        legacy_verifier=_legacy_verifier,
        access_ttl=OAUTH_ACCESS_TTL,
        refresh_ttl=OAUTH_REFRESH_TTL,
        max_clients=OAUTH_MAX_CLIENTS,
        unused_client_ttl=OAUTH_UNUSED_CLIENT_TTL,
        owner_grant_scopes=OAUTH_OWNER_GRANT_SCOPES or None,
    )
    _fastmcp_auth_kwargs = {
        "auth": build_auth_settings(PUBLIC_URL, SERVER_NAME),
        "auth_server_provider": oauth_provider,
    }

mcp = FastMCP(
    SERVER_NAME,
    host="127.0.0.1",
    port=PORT,
    stateless_http=True,
    json_response=True,
    # FastMCP's built-in localhost-only Host allowlist causes HTTP 421 behind
    # Serveo, so it is disabled and replaced by the Host check inside
    # SecurityMiddleware (localhost + *.serveousercontent.com).
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    **_fastmcp_auth_kwargs,
)

PLUGIN_MANAGER = PluginManager(
    server_dir=SERVER_DIR,
    base_dir=BASE_DIR,
    allow_commands=ALLOW_COMMANDS,
    mcp=mcp,
)


@dataclass
class CommandJob:
    job_id: str
    program: str
    args: list[str]
    cwd: str
    timeout: int
    command: str
    stdout_path: Path
    stderr_path: Path
    started_at: float
    process: object | None = None
    status: str = "running"
    cancel_requested: bool = False
    timed_out: bool = False
    truncated: bool = False
    returncode: int | None = None
    finished_at: float | None = None
    task: asyncio.Task | None = None


COMMAND_JOBS: dict[str, CommandJob] = {}


def _is_async_process(proc: object) -> bool:
    return isinstance(proc, asyncio.subprocess.Process)


def _process_returncode(proc: object) -> int | None:
    return getattr(proc, "returncode", None)


def _process_pid(proc: object) -> int:
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("Process pid is unavailable")
    return pid


async def _wait_process(proc: object, timeout: float | None = None) -> int | None:
    if _is_async_process(proc):
        waiter = proc.wait()
        if timeout is None:
            return await waiter
        return await asyncio.wait_for(waiter, timeout=timeout)
    if timeout is None:
        return await asyncio.to_thread(proc.wait)
    return await asyncio.to_thread(proc.wait, timeout=timeout)


def _kill_tree_blocking(proc: object) -> None:
    if _process_returncode(proc) is not None:
        return
    pid = _process_pid(proc)
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


def _close_process_streams(proc: object) -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None)
        if stream is None:
            continue
        with contextlib.suppress(Exception):
            stream.close()

def _clip(text) -> str:
    if text is None:
        return "(no output)"
    text = str(text)
    if text == "":
        return "(empty result)"
    if len(text) > MAX_OUTPUT_CHARS:
        return (
            text[:MAX_OUTPUT_CHARS]
            + f"\n\n... [output truncated: {len(text):,} chars total, showing first "
            f"{MAX_OUTPUT_CHARS:,}. Use offset/limit or a narrower query to see more.]"
        )
    return text


def _require_scope(scope: str | None) -> None:
    if scope is None or AUTH_MODE == AUTH_MODE_LEGACY:
        return
    access = get_access_token()
    if access is None:
        raise PermissionError("Authentication context is missing; the request was not authorized.")
    if scope not in access.scopes:
        raise PermissionError(
            f"Access denied: this operation requires OAuth scope {scope!r}. "
            f"Granted scopes: {', '.join(access.scopes) or '(none)'}."
        )


def tool(scope: str | None = None):
    """Like @tool() but optionally enforces an OAuth scope and clips results."""
    if scope is not None and scope not in ALL_SCOPES:
        raise RuntimeError(f"Tool registered with unknown scope: {scope}")
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            _require_scope(scope)
            return _clip(await fn(*args, **kwargs))
        return mcp.tool()(wrapper)
    return deco


def _path(value: str = ".") -> Path:
    return safe_path(BASE_DIR, value)


def _ensure_writable(path: Path) -> None:
    """Reject writes to git-policy trust anchors."""
    if path.name == REPO_CONTEXT_FILE:
        raise ValueError(
            f"Refusing to modify the repo-context trust anchor '{REPO_CONTEXT_FILE}'. "
            "Use setup_git_context(...) / configure_repo_context(...) instead."
        )
    try:
        parts = Path(display_path(path)).parts
    except ValueError:
        parts = path.parts
    if ".git" in parts:
        raise ValueError("Refusing to modify anything inside a .git directory.")


def _is_binary_bytes(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _temp_dir() -> Path:
    directory = SERVER_DIR / TEMP_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cleanup_temp_files() -> None:
    cutoff = dt.datetime.now().timestamp() - TEMP_FILE_TTL_SECONDS
    directory = _temp_dir()
    for item in directory.glob("*.txt"):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
        except OSError:
            continue


_last_orphan_sweep: float = float("-inf")


def _cleanup_orphan_mcp_tmp() -> None:
    """Sweep stale atomic-write temp files left beside workspace targets."""
    global _last_orphan_sweep
    now_mono = time.monotonic()
    if now_mono - _last_orphan_sweep < ORPHAN_SWEEP_MIN_INTERVAL_SECONDS:
        return
    _last_orphan_sweep = now_mono
    cutoff = dt.datetime.now().timestamp() - TEMP_FILE_TTL_SECONDS
    for root, dirs, files in os.walk(BASE_DIR, topdown=True):
        root_path = Path(root)
        dirs[:] = [
            d for d in dirs
            if not should_skip(root_path / d, False, EXCLUDES)
        ]
        for name in files:
            if not name.endswith(".mcp-tmp"):
                continue
            candidate = root_path / name
            try:
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except OSError:
                continue


def _temp_virtual_path(path: Path) -> str:
    return f"{TEMP_PATH_PREFIX}{path.name}"


def _resolve_temp_path(path: str) -> Path:
    normalized = path.replace("\\", "/")
    if not normalized.startswith(TEMP_PATH_PREFIX):
        raise ValueError("Not a temp path")
    name = normalized[len(TEMP_PATH_PREFIX):].strip()
    if not name or "/" in name or ".." in name:
        raise ValueError("Invalid temp path")
    return _temp_dir() / name


def _resolve_read_file_path(path: str) -> tuple[Path, bool]:
    normalized = path.replace("\\", "/")
    if normalized.startswith(TEMP_PATH_PREFIX):
        return _resolve_temp_path(normalized), True
    return _path(path), False


def _tool_output_path(prefix: str) -> Path:
    _cleanup_temp_files()
    _cleanup_orphan_mcp_tmp()
    safe_prefix = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in prefix
    ).strip("-") or "output"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return _temp_dir() / f"{safe_prefix}-{stamp}.txt"


def _repo_context_path(cwd: Path | None = None, git_args: list[str] | None = None) -> Path:
    scope_dir = BASE_DIR if cwd is None else cwd
    if cwd is not None:
        detected = _detect_git_repo(cwd, git_args)
        if detected["repo_present"]:
            scope_dir = Path(str(detected["top_level"]))
    return safe_path(scope_dir, REPO_CONTEXT_FILE)


def _normalize_repo_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    trimmed = raw.rstrip("/")
    if trimmed.lower().endswith(".git"):
        trimmed = trimmed[:-4]

    if "://" in trimmed:
        parsed = urlsplit(trimmed)
        host = (parsed.hostname or parsed.netloc).lower()
        repo_path = parsed.path.strip("/")
        if repo_path.lower().endswith(".git"):
            repo_path = repo_path[:-4]
        return f"{host}/{repo_path}".lower()

    ssh_match = re.fullmatch(r"(?:[^@]+@)?([^:]+):(.+)", trimmed)
    if ssh_match:
        host = ssh_match.group(1).lower()
        repo_path = ssh_match.group(2).strip("/")
        if repo_path.lower().endswith(".git"):
            repo_path = repo_path[:-4]
        return f"{host}/{repo_path}".lower()

    return trimmed.lower()


def _parse_fork_status(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"fork", "true", "yes"}:
        return True
    if normalized in {"not_fork", "false", "no", "standalone"}:
        return False
    raise ValueError("fork_status must be 'fork' or 'not_fork'")


def _coerce_repo_context(raw: dict[str, object], config_path: Path) -> dict[str, object]:
    status = raw.get("status")
    if status is None:
        status = "configured" if raw.get("repository_url") else "disabled"
    if not isinstance(status, str) or status not in {"configured", "disabled"}:
        raise ValueError("status must be 'configured' or 'disabled'")

    git_enabled = raw.get("git_enabled")
    if not isinstance(git_enabled, bool):
        git_enabled = status == "configured"

    repository_url = raw.get("repository_url", "")
    if repository_url is None:
        repository_url = ""
    if not isinstance(repository_url, str):
        raise ValueError("repository_url must be a string")
    repository_url = repository_url.strip()
    normalized_repository_url = _normalize_repo_url(repository_url) if repository_url else ""

    is_fork = raw.get("is_fork")
    if is_fork is not None and not isinstance(is_fork, bool):
        raise ValueError("is_fork must be true, false, or null")

    upstream_url = raw.get("upstream_url", "")
    if upstream_url is None:
        upstream_url = ""
    if not isinstance(upstream_url, str):
        raise ValueError("upstream_url must be a string")
    upstream_url = upstream_url.strip()
    normalized_upstream_url = _normalize_repo_url(upstream_url) if upstream_url else ""

    default_branch = raw.get("default_branch", "")
    if default_branch is None:
        default_branch = ""
    if not isinstance(default_branch, str):
        raise ValueError("default_branch must be a string")
    default_branch = default_branch.strip()

    branch_mode = raw.get("branch_mode", "default_branch")
    if not isinstance(branch_mode, str) or branch_mode not in {"default_branch", "specified_branch"}:
        raise ValueError("branch_mode must be 'default_branch' or 'specified_branch'")

    commit_branch = raw.get("commit_branch", "")
    if commit_branch is None:
        commit_branch = ""
    if not isinstance(commit_branch, str):
        raise ValueError("commit_branch must be a string")
    commit_branch = commit_branch.strip()

    disabled_reason = raw.get("disabled_reason", "")
    if disabled_reason is None:
        disabled_reason = ""
    if not isinstance(disabled_reason, str):
        raise ValueError("disabled_reason must be a string")

    last_detected_origin = raw.get("last_detected_origin", "")
    if last_detected_origin is None:
        last_detected_origin = ""
    if not isinstance(last_detected_origin, str):
        raise ValueError("last_detected_origin must be a string")

    last_detected_branch = raw.get("last_detected_branch", "")
    if last_detected_branch is None:
        last_detected_branch = ""
    if not isinstance(last_detected_branch, str):
        raise ValueError("last_detected_branch must be a string")

    configured_at = raw.get("configured_at", "")
    if configured_at is None:
        configured_at = ""
    if not isinstance(configured_at, str):
        raise ValueError("configured_at must be a string")

    last_checked_at = raw.get("last_checked_at", "")
    if last_checked_at is None:
        last_checked_at = ""
    if not isinstance(last_checked_at, str):
        raise ValueError("last_checked_at must be a string")

    if status == "configured":
        if not repository_url:
            raise ValueError("repository_url is required when git is configured")
        if not isinstance(is_fork, bool):
            raise ValueError("is_fork must be true or false when git is configured")
        if not git_enabled:
            raise ValueError("git_enabled cannot be false when status is configured")
    elif git_enabled:
        raise ValueError("git_enabled cannot be true when status is disabled")

    return {
        "schema_version": REPO_CONTEXT_SCHEMA_VERSION,
        "status": status,
        "git_enabled": git_enabled,
        "repository_url": repository_url,
        "normalized_repository_url": normalized_repository_url,
        "is_fork": is_fork,
        "upstream_url": upstream_url,
        "normalized_upstream_url": normalized_upstream_url,
        "default_branch": default_branch,
        "branch_mode": branch_mode,
        "commit_branch": commit_branch,
        "disabled_reason": disabled_reason,
        "last_detected_origin": last_detected_origin,
        "last_detected_branch": last_detected_branch,
        "configured_at": configured_at,
        "last_checked_at": last_checked_at,
        "config_path": config_path,
    }


def _load_repo_context(cwd: Path | None = None, git_args: list[str] | None = None) -> dict[str, object] | None:
    config_path = _repo_context_path(cwd, git_args)
    if not config_path.is_file():
        return None

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid repo context file: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("repo context must be a JSON object")
    return _coerce_repo_context(raw, config_path)


def _save_repo_context(
    *,
    status: str,
    repository_url: str = "",
    is_fork: bool | None = None,
    upstream_url: str = "",
    default_branch: str = "",
    branch_mode: str = "default_branch",
    commit_branch: str = "",
    git_enabled: bool | None = None,
    disabled_reason: str = "",
    last_detected_origin: str = "",
    last_detected_branch: str = "",
    cwd: Path | None = None,
    git_args: list[str] | None = None,
) -> Path:
    if status not in {"configured", "disabled"}:
        raise ValueError("status must be 'configured' or 'disabled'")
    if branch_mode not in {"default_branch", "specified_branch"}:
        raise ValueError("branch_mode must be 'default_branch' or 'specified_branch'")

    existing: dict[str, object] | None
    try:
        existing = _load_repo_context(cwd, git_args)
    except ValueError:
        existing = None

    if git_enabled is None:
        git_enabled = status == "configured"

    repository_url = repository_url.strip()
    upstream_url = upstream_url.strip()
    default_branch = default_branch.strip()
    commit_branch = commit_branch.strip()
    disabled_reason = disabled_reason.strip()
    normalized_repository_url = _normalize_repo_url(repository_url) if repository_url else ""
    normalized_upstream_url = _normalize_repo_url(upstream_url) if upstream_url else ""
    now = dt.datetime.now().isoformat(timespec="seconds")

    if status == "configured":
        if not repository_url:
            raise ValueError("repository_url is required when configuring git")
        if not isinstance(is_fork, bool):
            raise ValueError("is_fork must be true or false when configuring git")
        if branch_mode == "default_branch" and not default_branch:
            raise ValueError("default_branch is required when branch_mode='default_branch'")
        if branch_mode == "specified_branch" and not commit_branch:
            raise ValueError("commit_branch is required when branch_mode='specified_branch'")
    elif git_enabled:
        raise ValueError("git_enabled cannot be true when status is disabled")

    payload = {
        "schema_version": REPO_CONTEXT_SCHEMA_VERSION,
        "status": status,
        "git_enabled": git_enabled,
        "repository_url": repository_url,
        "normalized_repository_url": normalized_repository_url,
        "is_fork": is_fork,
        "upstream_url": upstream_url,
        "normalized_upstream_url": normalized_upstream_url,
        "default_branch": default_branch,
        "branch_mode": branch_mode,
        "commit_branch": commit_branch,
        "disabled_reason": disabled_reason,
        "last_detected_origin": last_detected_origin.strip(),
        "last_detected_branch": last_detected_branch.strip(),
        "configured_at": existing.get("configured_at", now) if existing else now,
        "last_checked_at": now,
    }

    config_path = _repo_context_path(cwd, git_args)
    _atomic_write_text(config_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return config_path


def _target_commit_branch(config: dict[str, object]) -> str:
    if str(config.get("branch_mode", "default_branch")) == "specified_branch":
        return str(config.get("commit_branch", "")).strip()
    return str(config.get("default_branch", "")).strip()


def _git_executable() -> str | None:
    return shutil.which("git")


def _require_git_executable() -> str:
    executable = _git_executable()
    if not executable:
        raise ValueError("Git is not installed or not on PATH.")
    return executable


_UNSAFE_GIT_GLOBAL_OPTIONS = {
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
}


def _split_git_global_args(git_args: list[str]) -> tuple[list[str], str]:
    options_with_value = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
    prefix: list[str] = []
    skip_next = False
    subcommand = ""

    for arg in git_args:
        if skip_next:
            prefix.append(arg)
            skip_next = False
            continue
        if arg in options_with_value:
            prefix.append(arg)
            skip_next = True
            continue
        if any(arg.startswith(f"{option}=") for option in options_with_value if option.startswith("--")):
            prefix.append(arg)
            continue
        if arg.startswith("-") and not subcommand:
            prefix.append(arg)
            continue
        if not subcommand:
            subcommand = arg.lower()
            break

    return prefix, subcommand


def _sanitized_env() -> dict[str, str]:
    """Return os.environ minus server secrets for child processes."""
    secret_keys = {
        "MCP_TOKEN",
        "MCP_OAUTH_OWNER_CODE",
        "MCP_OAUTH_OWNER_GRANT_SCOPES",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key not in secret_keys and not key.startswith("MCP_OAUTH_")
    }


def _run_git_query(
    cwd: Path,
    *args: str,
    timeout: int = 5,
    git_prefix: list[str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    executable = _git_executable()
    if not executable:
        return None
    return subprocess.run(
        [executable, *(git_prefix or []), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=_sanitized_env(),
    )


def _run_git_checked(cwd: Path, *args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    executable = _require_git_executable()
    result = subprocess.run(
        [executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=_sanitized_env(),
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "unknown error").strip()
        raise ValueError(f"git {' '.join(args)} failed: {details}")
    return result


def _detect_git_repo(cwd: Path, git_args: list[str] | None = None) -> dict[str, object]:
    executable = _git_executable()
    result: dict[str, object] = {
        "git_installed": bool(executable),
        "repo_present": False,
        "top_level": "",
        "branch": "",
        "origin_url": "",
        "normalized_origin_url": "",
        "upstream_url": "",
        "normalized_upstream_url": "",
        "remotes": {},
    }
    if not executable:
        return result

    git_prefix, _ = _split_git_global_args(list(git_args or []))
    top_level = _run_git_query(cwd, "rev-parse", "--show-toplevel", git_prefix=git_prefix)
    if top_level is None or top_level.returncode != 0:
        return result

    root = top_level.stdout.strip()
    branch = _run_git_query(cwd, "branch", "--show-current", git_prefix=git_prefix)
    remotes_query = _run_git_query(cwd, "remote", git_prefix=git_prefix)
    remotes: dict[str, str] = {}
    if remotes_query is not None and remotes_query.returncode == 0:
        for remote_name in [line.strip() for line in remotes_query.stdout.splitlines() if line.strip()]:
            url_query = _run_git_query(cwd, "config", "--get", f"remote.{remote_name}.url", git_prefix=git_prefix)
            if url_query is not None and url_query.returncode == 0:
                remotes[remote_name] = url_query.stdout.strip()

    origin_url = remotes.get("origin", "")
    upstream_url = remotes.get("upstream", "")
    branch_name = branch.stdout.strip() if branch is not None and branch.returncode == 0 else ""

    result.update(
        {
            "repo_present": True,
            "top_level": root,
            "branch": branch_name,
            "origin_url": origin_url,
            "normalized_origin_url": _normalize_repo_url(origin_url) if origin_url else "",
            "upstream_url": upstream_url,
            "normalized_upstream_url": _normalize_repo_url(upstream_url) if upstream_url else "",
            "remotes": remotes,
        }
    )
    return result


def _inspect_git_repository_text(cwd: Path) -> str:
    detected = _detect_git_repo(cwd)
    lines = [f"workspace path: {cwd}"]
    lines.append(f"git installed: {'yes' if detected['git_installed'] else 'no'}")
    if not detected["git_installed"]:
        return "\n".join(lines)
    lines.append(f"repository present: {'yes' if detected['repo_present'] else 'no'}")
    if not detected["repo_present"]:
        return "\n".join(lines)

    lines.append(f"git root: {detected['top_level']}")
    lines.append(f"git branch: {detected['branch'] or '(detached or unknown)'}")
    remotes = detected["remotes"]
    if not remotes:
        lines.append("git remotes: (none)")
    else:
        lines.append("git remotes:")
        for name in sorted(remotes):
            lines.append(f"- {name}: {remotes[name]}")
    return "\n".join(lines)


def _repo_context_state(cwd: Path, git_args: list[str] | None = None) -> tuple[str, dict[str, object] | None, dict[str, object], list[str]]:
    lines: list[str] = []
    config: dict[str, object] | None

    try:
        config = _load_repo_context(cwd, git_args)
    except ValueError as exc:
        config = None
        lines.append(f"repo context status: invalid ({exc})")
        detected = _detect_git_repo(cwd, git_args)
        lines.append("git policy: blocked")
        lines.append("next step: recreate the local repo context with setup_git_context(...) or configure_repo_context(...)")
        return "invalid_context", config, detected, lines

    if config is None:
        lines.append(f"repo context status: missing ({REPO_CONTEXT_FILE})")
    else:
        lines.append(f"repo context status: {config['status']}")
        lines.append(f"git enabled: {'yes' if config['git_enabled'] else 'no'}")
        if config["repository_url"]:
            lines.append(f"repo url: {config['repository_url']}")
        if config["is_fork"] is not None:
            lines.append(f"repo fork: {'yes' if config['is_fork'] else 'no'}")
        if config["upstream_url"]:
            lines.append(f"repo upstream: {config['upstream_url']}")
        if config["default_branch"]:
            lines.append(f"repo default branch: {config['default_branch']}")
        branch_mode = str(config.get("branch_mode", "default_branch"))
        if branch_mode == "specified_branch":
            lines.append(f"commit branch policy: explicit branch ({config['commit_branch'] or 'unset'})")
        else:
            lines.append(f"commit branch policy: default branch ({config['default_branch'] or 'unset'})")
        if config["disabled_reason"]:
            lines.append(f"disabled reason: {config['disabled_reason']}")

    detected = _detect_git_repo(cwd, git_args)
    if not detected["git_installed"]:
        lines.append("git detected: not installed")
        lines.append("git policy: blocked")
        lines.append("next step: install Git or keep trusted developer mode turned off for git work")
        return "git_unavailable", config, detected, lines

    if not detected["repo_present"]:
        lines.append("git detected: no repository in current path")
        if config is None:
            lines.append("git policy: blocked")
            lines.append("next step: ask the user to choose one of: init_new_repo, attach_to_remote, or disable_git")
            lines.append("branch policy choice: the user must also choose whether commits go to the default branch or to a specific branch name")
            return "setup_required_no_repo", config, detected, lines
        if config["status"] == "disabled":
            lines.append("git policy: disabled by user for this workspace")
            lines.append("next step: re-enable with setup_git_context(mode='init_new_repo' or mode='attach_to_remote') if needed")
            return "disabled", config, detected, lines
        lines.append("git policy: blocked")
        lines.append("next step: restore the repository in this folder or run setup_git_context(mode='attach_to_remote', ...) to initialize it here")
        return "repo_missing", config, detected, lines

    lines.append(f"git root: {detected['top_level']}")
    lines.append(f"git branch: {detected['branch'] or '(detached or unknown)'}")
    lines.append(f"git origin: {detected['origin_url'] or '(missing)'}")
    lines.append(f"git upstream: {detected['upstream_url'] or '(missing)'}")

    if config is None:
        lines.append("git policy: blocked")
        lines.append("next step: ask the user to choose one of: bind_existing_repo, attach_to_remote, or disable_git")
        lines.append("branch policy choice: the user must also choose whether commits go to the default branch or to a specific branch name")
        return "setup_required_existing_repo", config, detected, lines

    if config["status"] == "disabled":
        lines.append("git policy: disabled by user for this workspace")
        lines.append("next step: re-enable with setup_git_context(mode='bind_existing_repo', ...) or mode='attach_to_remote' if the target repo changed")
        return "disabled", config, detected, lines

    target_branch = _target_commit_branch(config)
    if not target_branch:
        lines.append("branch policy check: target branch is not configured")
        lines.append("git policy: blocked")
        lines.append("next step: rerun setup_git_context(...) and choose default_branch or commit_branch explicitly")
        return "branch_policy_missing", config, detected, lines

    if not detected["origin_url"]:
        lines.append("repo context check: origin missing")
        lines.append("git policy: blocked")
        lines.append("next step: run setup_git_context(mode='bind_existing_repo', repository_url='...', fork_status='fork|not_fork', branch_mode='default_branch|specified_branch', commit_branch='...') to set origin")
        return "repo_present_no_origin", config, detected, lines

    if detected["normalized_origin_url"] != config["normalized_repository_url"]:
        lines.append("repo context check: mismatch")
        lines.append("git policy: blocked")
        lines.append("next step: run setup_git_context(..., force_origin_update=true) or disable_git for this workspace")
        return "repo_present_bound_mismatch", config, detected, lines

    lines.append(f"commit target branch: {target_branch}")
    lines.append("repo context check: ok")
    lines.append("git policy: allowed")
    return "repo_present_bound_ok", config, detected, lines


def _repo_context_summary(cwd: Path, git_args: list[str] | None = None) -> str:
    _, _, _, lines = _repo_context_state(cwd, git_args)
    return "\n".join(lines)


def _build_repo_context_desired(
    *,
    status: str,
    repository_url: str,
    is_fork: bool | None,
    upstream_url: str,
    default_branch: str,
    branch_mode: str,
    commit_branch: str,
    git_enabled: bool,
    disabled_reason: str,
) -> dict[str, object]:
    repository_url = repository_url.strip()
    upstream_url = upstream_url.strip()
    default_branch = default_branch.strip()
    commit_branch = commit_branch.strip()
    disabled_reason = disabled_reason.strip()
    return {
        "status": status,
        "git_enabled": git_enabled,
        "repository_url": repository_url,
        "normalized_repository_url": _normalize_repo_url(repository_url) if repository_url else "",
        "is_fork": is_fork,
        "upstream_url": upstream_url,
        "normalized_upstream_url": _normalize_repo_url(upstream_url) if upstream_url else "",
        "default_branch": default_branch,
        "branch_mode": branch_mode,
        "commit_branch": commit_branch,
        "disabled_reason": disabled_reason,
    }


def _format_policy_value(value: object) -> str:
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _repo_context_change_descriptions(
    existing: dict[str, object], desired: dict[str, object]
) -> list[str]:
    comparisons = [
        ("status", "status"),
        ("git_enabled", "git enabled"),
        ("normalized_repository_url", "repository URL"),
        ("is_fork", "fork setting"),
        ("normalized_upstream_url", "upstream URL"),
        ("default_branch", "default branch"),
        ("branch_mode", "branch mode"),
        ("commit_branch", "commit branch"),
        ("disabled_reason", "disabled reason"),
    ]
    changes: list[str] = []
    for key, label in comparisons:
        if existing.get(key) != desired.get(key):
            changes.append(
                f"{label}: {_format_policy_value(existing.get(key))} -> {_format_policy_value(desired.get(key))}"
            )
    return changes


def _require_repo_context_confirmation(
    existing: dict[str, object] | None,
    desired: dict[str, object],
    confirm_reconfigure: bool,
) -> None:
    if existing is None:
        return
    changes = _repo_context_change_descriptions(existing, desired)
    if changes and not confirm_reconfigure:
        raise ValueError(
            "Repo context already exists for this repository. Any change requires explicit confirmation. "
            "Rerun with confirm_reconfigure=true. Proposed changes:\n- "
            + "\n- ".join(changes)
        )


def _require_explicit_defaults(defaults_used: list[str], confirm_defaults: bool) -> None:
    if defaults_used and not confirm_defaults:
        raise ValueError(
            "Some settings are not explicitly set. Pass explicit values or rerun with "
            "confirm_defaults=true to accept these defaults: "
            + ", ".join(defaults_used)
        )


def _repo_state_label(state: str) -> str:
    return {
        "repo_present_bound_ok": "ok",
        "setup_required_no_repo": "no git repo",
        "setup_required_existing_repo": "missing context",
        "repo_present_bound_mismatch": "origin mismatch",
        "repo_present_no_origin": "origin missing",
        "branch_policy_missing": "branch policy missing",
        "disabled": "disabled",
        "repo_missing": "repo missing",
        "git_unavailable": "git unavailable",
        "invalid_context": "invalid context",
    }.get(state, state.replace("_", " "))


def _discover_workspace_git_roots(base_dir: Path, limit: int = 25) -> list[Path]:
    roots: list[Path] = []
    for current, dirs, files in os.walk(base_dir):
        current_path = Path(current)
        has_git = ".git" in dirs or ".git" in files
        dirs[:] = sorted(
            d
            for d in dirs
            if d != ".git" and d not in EXCLUDES and not d.startswith(".")
        )
        if has_git:
            safe_path(base_dir, current_path)
            roots.append(current_path.resolve())
            if len(roots) >= limit:
                break
    seen: set[Path] = set()
    ordered: list[Path] = []
    for repo_root in sorted(roots, key=lambda p: (len(p.relative_to(base_dir).parts), str(p).lower())):
        if repo_root not in seen:
            seen.add(repo_root)
            ordered.append(repo_root)
    return ordered


def _format_workspace_repo_line(repo_root: Path) -> str:
    state, config, detected, _ = _repo_context_state(repo_root)
    rel = "." if repo_root == BASE_DIR else str(display_path(repo_root))
    current_branch = str(detected.get("branch", "")).strip() or "(detached or unknown)"
    target_branch = _target_commit_branch(config) if config else ""
    parts = [_repo_state_label(state), f"current {current_branch}"]
    if target_branch:
        parts.append(f"target {target_branch}")
    return f"{rel} - " + " | ".join(parts)


def _workspace_repo_overview(base_dir: Path = BASE_DIR, max_nested: int = 10) -> str:
    roots = _discover_workspace_git_roots(base_dir)
    if not roots:
        return "git repos in workspace: none"

    lines = [f"git repos in workspace: {len(roots)}"]
    if base_dir in roots:
        lines.append(f"root repo: {_format_workspace_repo_line(base_dir)}")
    else:
        lines.append("root repo: none")

    nested = [repo_root for repo_root in roots if repo_root != base_dir]
    lines.append(f"nested repos: {len(nested)}")
    for repo_root in nested[:max_nested]:
        lines.append(f"- {_format_workspace_repo_line(repo_root)}")
    if len(nested) > max_nested:
        lines.append(f"... and {len(nested) - max_nested} more nested repos")
    lines.append("Use repo_context_status(cwd='...') for full details on a specific repo.")
    return "\n".join(lines)


def _ensure_remote_url(cwd: Path, remote_name: str, url: str, force_update: bool, confirm_reconfigure: bool = False) -> str:
    current_query = _run_git_query(cwd, "config", "--get", f"remote.{remote_name}.url")
    current_url = current_query.stdout.strip() if current_query is not None and current_query.returncode == 0 else ""
    current_normalized = _normalize_repo_url(current_url) if current_url else ""
    target_normalized = _normalize_repo_url(url)

    if not current_url:
        _run_git_checked(cwd, "remote", "add", remote_name, url)
        return f"added remote {remote_name}"
    if current_normalized == target_normalized:
        return f"kept remote {remote_name}"
    if not force_update:
        raise ValueError(
            f"remote.{remote_name}.url already points to {current_url}. "
            f"Use force_origin_update=true to change it to {url}."
        )
    if not confirm_reconfigure:
        raise ValueError(
            f"remote.{remote_name}.url already points to {current_url}. "
            "Changing an existing git remote requires explicit confirmation. "
            "Rerun with confirm_reconfigure=true as well."
        )
    _run_git_checked(cwd, "remote", "set-url", remote_name, url)
    return f"updated remote {remote_name}"


def _setup_git_context_sync(
    cwd: Path,
    *,
    mode: str,
    repository_url: str = "",
    fork_status: str = "",
    upstream_url: str = "",
    default_branch: str = "",
    branch_mode: str = "default_branch",
    commit_branch: str = "",
    disable_reason: str = "",
    force_origin_update: bool = False,
    set_upstream_remote: bool = False,
    confirm_defaults: bool = False,
    confirm_reconfigure: bool = False,
) -> str:
    mode = mode.strip()
    branch_mode = branch_mode.strip() or "default_branch"
    if mode not in {"bind_existing_repo", "attach_to_remote", "init_new_repo", "disable_git"}:
        raise ValueError("mode must be one of: bind_existing_repo, attach_to_remote, init_new_repo, disable_git")
    if branch_mode not in {"default_branch", "specified_branch"}:
        raise ValueError("branch_mode must be 'default_branch' or 'specified_branch'")

    try:
        existing = _load_repo_context(cwd)
    except ValueError:
        existing = None
    detected_before = _detect_git_repo(cwd)
    if not detected_before["git_installed"]:
        raise ValueError("Git is not installed or not on PATH.")

    if mode == "disable_git":
        repository_url = (
            str(existing["repository_url"]) if existing and existing["repository_url"] else str(detected_before["origin_url"])
        ).strip()
        is_fork = existing["is_fork"] if existing else None
        upstream_url = str(existing["upstream_url"]) if existing else ""
        default_branch = str(existing["default_branch"]) if existing else ""
        branch_mode = str(existing["branch_mode"]) if existing and existing.get("branch_mode") else "default_branch"
        commit_branch = str(existing["commit_branch"]) if existing else ""
        defaults_used: list[str] = []
        if not disable_reason.strip():
            disable_reason = "user choice"
            defaults_used.append("disable_reason='user choice'")
        _require_explicit_defaults(defaults_used, confirm_defaults)
        desired = _build_repo_context_desired(
            status="disabled",
            repository_url=repository_url,
            is_fork=is_fork if isinstance(is_fork, bool) else None,
            upstream_url=upstream_url,
            default_branch=default_branch,
            branch_mode=branch_mode,
            commit_branch=commit_branch,
            git_enabled=False,
            disabled_reason=disable_reason,
        )
        _require_repo_context_confirmation(existing, desired, confirm_reconfigure)
        config_path = _save_repo_context(
            cwd=cwd,
            status="disabled",
            repository_url=repository_url,
            is_fork=is_fork if isinstance(is_fork, bool) else None,
            upstream_url=upstream_url,
            default_branch=default_branch,
            branch_mode=branch_mode,
            commit_branch=commit_branch,
            git_enabled=False,
            disabled_reason=disable_reason,
            last_detected_origin=str(detected_before["origin_url"]),
            last_detected_branch=str(detected_before["branch"]),
        )
        summary = _repo_context_summary(cwd)
        return f"Saved disabled git policy to {display_path(config_path)}\n\n{summary}"

    if mode == "bind_existing_repo" and not detected_before["repo_present"]:
        raise ValueError(
            "No git repository exists here yet. Ask the user whether to init_new_repo, attach_to_remote, or disable_git."
        )

    repository_url = repository_url.strip()
    if not repository_url:
        raise ValueError("repository_url is required for this setup mode")
    if not fork_status.strip():
        raise ValueError("fork_status must be explicitly set to 'fork' or 'not_fork'")
    is_fork = _parse_fork_status(fork_status)
    upstream_url = upstream_url.strip()
    default_branch = default_branch.strip()
    commit_branch = commit_branch.strip()
    defaults_used: list[str] = []

    if branch_mode == "specified_branch" and not commit_branch:
        raise ValueError("commit_branch is required when branch_mode='specified_branch'")

    if branch_mode == "default_branch" and not default_branch:
        if mode == "init_new_repo" or (mode == "attach_to_remote" and not detected_before["repo_present"]):
            default_branch = "main"
            defaults_used.append("default_branch='main'")
        elif existing and existing.get("default_branch"):
            default_branch = str(existing["default_branch"]).strip()
        elif detected_before["branch"]:
            default_branch = str(detected_before["branch"]).strip()
            defaults_used.append(f"default_branch='{default_branch}'")
        else:
            raise ValueError(
                "default_branch is not set. Pass it explicitly, or rerun with confirm_defaults=true "
                "only when a safe default is available."
            )
    _require_explicit_defaults(defaults_used, confirm_defaults)

    desired = _build_repo_context_desired(
        status="configured",
        repository_url=repository_url,
        is_fork=is_fork,
        upstream_url=upstream_url,
        default_branch=default_branch,
        branch_mode=branch_mode,
        commit_branch=commit_branch,
        git_enabled=True,
        disabled_reason="",
    )
    _require_repo_context_confirmation(existing, desired, confirm_reconfigure)

    actions: list[str] = []
    work_root = cwd

    if mode == "init_new_repo":
        if detected_before["repo_present"]:
            raise ValueError("A git repository already exists here. Use bind_existing_repo or attach_to_remote instead.")
        _run_git_checked(cwd, "init")
        actions.append("initialized git repository")
        work_root = cwd
        if default_branch:
            _run_git_checked(cwd, "branch", "-M", default_branch)
            actions.append(f"set default branch to {default_branch}")
    elif mode == "attach_to_remote":
        if not detected_before["repo_present"]:
            _run_git_checked(cwd, "init")
            actions.append("initialized git repository")
            if default_branch:
                _run_git_checked(cwd, "branch", "-M", default_branch)
                actions.append(f"set default branch to {default_branch}")
            work_root = cwd
        else:
            work_root = Path(str(detected_before["top_level"]))
    elif mode == "bind_existing_repo":
        work_root = Path(str(detected_before["top_level"]))

    actions.append(_ensure_remote_url(work_root, "origin", repository_url, force_origin_update, confirm_reconfigure))
    if upstream_url and set_upstream_remote:
        actions.append(_ensure_remote_url(work_root, "upstream", upstream_url, True, confirm_reconfigure))

    detected_after = _detect_git_repo(work_root)
    final_branch = str(detected_after["branch"] or default_branch).strip()
    stored_default_branch = default_branch or final_branch
    if branch_mode == "default_branch" and not stored_default_branch:
        raise ValueError("default_branch is required when branch_mode='default_branch'")

    config_path = _save_repo_context(
        cwd=work_root,
        status="configured",
        repository_url=repository_url,
        is_fork=is_fork,
        upstream_url=upstream_url,
        default_branch=stored_default_branch,
        branch_mode=branch_mode,
        commit_branch=commit_branch,
        git_enabled=True,
        disabled_reason="",
        last_detected_origin=str(detected_after["origin_url"]),
        last_detected_branch=str(final_branch),
    )
    summary = _repo_context_summary(work_root)
    return (
        f"Saved repo context to {display_path(config_path)}\n"
        f"mode: {mode}\n"
        f"branch policy: {branch_mode}\n"
        f"actions: {', '.join(actions)}\n\n"
        f"{summary}"
    )


def _split_git_command(git_args: list[str]) -> tuple[list[str], str, list[str]]:
    options_with_value = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
    prefix: list[str] = []
    index = 0
    while index < len(git_args):
        arg = git_args[index]
        if arg in options_with_value:
            prefix.append(arg)
            if index + 1 < len(git_args):
                prefix.append(git_args[index + 1])
            index += 2
            continue
        if any(arg.startswith(f"{option}=") for option in options_with_value if option.startswith("--")):
            prefix.append(arg)
            index += 1
            continue
        if arg.startswith("-"):
            prefix.append(arg)
            index += 1
            continue
        return prefix, arg.lower(), git_args[index + 1:]
    return prefix, "", []


def _reject_unsafe_git_global_options(prefix: list[str]) -> None:
    for arg in prefix:
        base = arg.split("=", 1)[0]
        if base in _UNSAFE_GIT_GLOBAL_OPTIONS:
            raise ValueError(
                "Git is blocked because the global option "
                f"'{base}' can run arbitrary programs or retarget git outside the validated workspace."
            )


def _command_positionals(args: list[str], options_with_value: set[str] | None = None) -> list[str]:
    options_with_value = options_with_value or set()
    positionals: list[str] = []
    skip_next = False
    passthrough = False
    for arg in args:
        if passthrough:
            positionals.append(arg)
            continue
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            passthrough = True
            continue
        if arg in options_with_value:
            skip_next = True
            continue
        if any(arg.startswith(f"{option}=") for option in options_with_value if option.startswith("--")):
            continue
        if arg.startswith("-"):
            continue
        positionals.append(arg)
    return positionals


def _allowed_remote_urls(config: dict[str, object]) -> set[str]:
    urls = {
        str(config.get("normalized_repository_url", "")).strip(),
        str(config.get("normalized_upstream_url", "")).strip(),
    }
    return {url for url in urls if url}


def _normalize_remote_candidate(remote_ref: str, detected: dict[str, object]) -> str:
    remotes = detected.get("remotes", {})
    if isinstance(remotes, dict) and remote_ref in remotes:
        return _normalize_repo_url(str(remotes[remote_ref]))
    if "://" in remote_ref or re.fullmatch(r"(?:[^@]+@)?[^:]+:.+", remote_ref):
        return _normalize_repo_url(remote_ref)
    return ""


def _ensure_remote_reference_allowed(
    remote_ref: str,
    config: dict[str, object],
    detected: dict[str, object],
    *,
    context: str,
) -> None:
    if not remote_ref:
        return
    normalized = _normalize_remote_candidate(remote_ref, detected)
    if not normalized:
        raise ValueError(
            f"Git is blocked because {context} must use a configured remote, but got {remote_ref}."
        )
    if normalized not in _allowed_remote_urls(config):
        raise ValueError(
            f"Git is blocked because {context} points to a remote outside the approved repo context: {remote_ref}."
        )


def _require_current_branch_matches(current_branch: str, target_branch: str) -> None:
    if not current_branch:
        raise ValueError(
            f"Git is blocked because changes for this workspace must happen on {target_branch}, "
            "but the repository is currently detached or the branch is unknown."
        )
    if current_branch != target_branch:
        raise ValueError(
            f"Git is blocked because this workspace is configured to work on {target_branch}, "
            f"but the current branch is {current_branch}. Switch branches first or update the repo context."
        )


def _is_git_config_read_only(args: list[str]) -> bool:
    mutating_flags = {"--add", "--replace-all", "--unset", "--unset-all", "--remove-section", "--rename-section", "-e", "--edit"}
    if any(flag in args for flag in mutating_flags):
        return False
    positionals = _command_positionals(args, {"-f", "--file", "--type", "--default", "--blob", "--fixed-value", "--url"})
    return len(positionals) <= 1


def _is_git_remote_read_only(args: list[str]) -> bool:
    if not args:
        return True
    if args[0] in {"-v", "--verbose"}:
        return True
    return args[0] in {"show", "get-url"}


def _remote_read_only_target(args: list[str]) -> str:
    if not args or args[0] in {"-v", "--verbose"}:
        return ""
    if args[0] in {"show", "get-url"}:
        positionals = _command_positionals(args[1:])
        return positionals[0] if positionals else ""
    return ""


def _is_git_branch_read_only(args: list[str]) -> bool:
    if not args:
        return True
    mutating_flags = {"-d", "-D", "-m", "-M", "-c", "-C", "--move", "--copy", "--delete", "--set-upstream-to", "--unset-upstream", "--edit-description"}
    if any(flag in args for flag in mutating_flags):
        return False
    positionals = _command_positionals(args, {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--format", "--sort", "--column"})
    return len(positionals) == 0


def _is_git_tag_read_only(args: list[str]) -> bool:
    if not args:
        return True
    if any(flag in args for flag in {"-d", "--delete", "-f", "--force", "-a", "-s", "-u", "-m", "-F", "--cleanup", "--trailer"}):
        return False
    positionals = _command_positionals(args, {"-m", "-F", "-u", "--cleanup", "--trailer"})
    return len(positionals) == 0 or any(flag in args for flag in {"-l", "--list"})


def _checkout_target_branch(args: list[str]) -> str:
    scan = args[: args.index("--")] if "--" in args else args
    index = 0
    while index < len(scan):
        arg = scan[index]
        if arg in {"-b", "-B", "--orphan"}:
            return scan[index + 1] if index + 1 < len(scan) else ""
        if arg.startswith("-"):
            index += 1
            continue
        return arg
    return ""


def _switch_target_branch(args: list[str]) -> str:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-c", "-C", "--orphan"}:
            return args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith("-"):
            index += 1
            continue
        return arg
    return ""


def _branch_target(args: list[str]) -> str:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-d", "-D", "-m", "-M", "-c", "-C", "--move", "--copy", "--delete", "--set-upstream-to"}:
            return args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith("--set-upstream-to="):
            return arg.split("=", 1)[1]
        if arg.startswith("-"):
            index += 1
            continue
        return arg
    return ""


def _blocked_push_mode(args: list[str], refspecs: list[str]) -> str:
    if any(arg in {"-f", "--force", "--force-with-lease"} or arg.startswith("--force-with-lease=") for arg in args):
        return "force push is not allowed"
    for refspec in refspecs:
        if refspec.startswith("+"):
            return "forced refspec updates are not allowed"
        if refspec.startswith(":"):
            return "delete refspecs are not allowed"
    return ""


def _transport_refspecs(args: list[str]) -> list[str]:
    positionals = _command_positionals(args, {"--depth", "--deepen", "--shallow-since", "--shallow-exclude", "--refmap", "--filter", "-o", "--server-option", "--upload-pack", "--recurse-submodules", "--jobs", "-j"})
    return positionals[1:] if len(positionals) > 1 else []


def _ensure_transport_refspec_allowed(refspec: str, current_branch: str, target_branch: str, *, context: str) -> None:
    if refspec.startswith("+"):
        raise ValueError(f"Git is blocked because {context} uses a forced-update refspec.")
    if refspec.startswith(":"):
        raise ValueError(f"Git is blocked because {context} uses a delete refspec.")
    if ":" not in refspec:
        return
    ref_target = _refspec_target_branch(refspec, current_branch)
    if ref_target != target_branch:
        raise ValueError(
            f"Git is blocked because {context} may only update {target_branch}, but the refspec targets {ref_target}."
        )


def _push_remote_and_refspecs(args: list[str]) -> tuple[str, list[str]]:
    positionals = _command_positionals(args, {"-u", "--set-upstream", "--repo", "--receive-pack", "--exec", "-o", "--push-option"})
    if not positionals:
        return "", []
    return positionals[0], positionals[1:]


def _pull_remote_and_branch(args: list[str]) -> tuple[str, str]:
    positionals = _command_positionals(args, {"--rebase-merges", "--strategy", "--strategy-option"})
    remote = positionals[0] if positionals else ""
    branch = positionals[1] if len(positionals) > 1 else ""
    return remote, branch


def _fetch_remote(args: list[str]) -> str:
    if "--all" in args:
        return "__ALL__"
    positionals = _command_positionals(args, {"--depth", "--deepen", "--shallow-since", "--shallow-exclude", "--refmap", "--filter", "-o", "--server-option", "--upload-pack"})
    return positionals[0] if positionals else ""


def _refspec_target_branch(refspec: str, current_branch: str) -> str:
    target = refspec
    if ":" in refspec:
        target = refspec.split(":", 1)[1]
    target = target.lstrip("+")
    if target in {"", "HEAD"}:
        return current_branch
    if target.startswith("refs/heads/"):
        return target[len("refs/heads/") :]
    return target


def _ensure_git_context_for_command(cwd: Path, git_args: list[str] | None = None) -> None:
    git_args = list(git_args or [])
    git_prefix, _prefix_subcommand = _split_git_global_args(git_args)
    _reject_unsafe_git_global_options(git_prefix)
    state, config, detected, lines = _repo_context_state(cwd, git_args)
    if state != "repo_present_bound_ok":
        raise ValueError("Git is blocked for this workspace.\n\n" + "\n".join(lines))
    if config is None:
        raise ValueError("Git is blocked because repo context data is unavailable.")

    _, subcommand, tail = _split_git_command(git_args)
    if not subcommand:
        raise ValueError("Git is blocked because the command could not be classified safely.")

    target_branch = _target_commit_branch(config)
    if not target_branch:
        raise ValueError(
            "Git is blocked because commit branch policy is not fully configured. "
            "Run setup_git_context(...) and choose default_branch or commit_branch explicitly."
        )
    current_branch = str(detected.get("branch", "")).strip()

    simple_read_only = {
        "status",
        "log",
        "show",
        "diff",
        "rev-parse",
        "describe",
        "ls-files",
        "ls-tree",
        "cat-file",
        "blame",
        "grep",
        "symbolic-ref",
    }
    if subcommand in simple_read_only:
        return

    if subcommand == "config":
        if _is_git_config_read_only(tail):
            return
        raise ValueError(
            "Git is blocked because mutating git config is not allowed through ordinary git commands. "
            "Use setup_git_context(...) or configure_repo_context(...) only with explicit user confirmation."
        )

    if subcommand == "remote":
        if _is_git_remote_read_only(tail):
            remote_target = _remote_read_only_target(tail)
            _ensure_remote_reference_allowed(remote_target, config, detected, context="this remote lookup")
            return
        raise ValueError(
            "Git is blocked because remote changes are not allowed through ordinary git commands. "
            "Use setup_git_context(...) with explicit confirmation instead."
        )

    if subcommand == "branch":
        if _is_git_branch_read_only(tail):
            return
        branch_target = _branch_target(tail)
        if branch_target and branch_target != target_branch:
            raise ValueError(
                f"Git is blocked because branch operations for this workspace must stay on {target_branch}, "
                f"but the command targets {branch_target}."
            )
        _require_current_branch_matches(current_branch, target_branch)
        return

    if subcommand == "checkout":
        branch_target = _checkout_target_branch(tail)
        if branch_target:
            if branch_target != target_branch:
                raise ValueError(
                    f"Git is blocked because checkout for this workspace must stay on {target_branch}, "
                    f"but the command targets {branch_target}."
                )
            return
        _require_current_branch_matches(current_branch, target_branch)
        return

    if subcommand == "switch":
        branch_target = _switch_target_branch(tail)
        if branch_target:
            if branch_target != target_branch:
                raise ValueError(
                    f"Git is blocked because switch for this workspace must stay on {target_branch}, "
                    f"but the command targets {branch_target}."
                )
            return
        _require_current_branch_matches(current_branch, target_branch)
        return

    if subcommand == "fetch":
        remote_target = _fetch_remote(tail)
        if remote_target == "__ALL__":
            for remote_name in sorted(detected.get("remotes", {})):
                _ensure_remote_reference_allowed(remote_name, config, detected, context="git fetch --all")
            return
        _ensure_remote_reference_allowed(remote_target, config, detected, context="git fetch")
        for refspec in _transport_refspecs(tail):
            _ensure_transport_refspec_allowed(refspec, current_branch, target_branch, context="git fetch")
        return

    if subcommand == "pull":
        _require_current_branch_matches(current_branch, target_branch)
        remote_target, branch_target = _pull_remote_and_branch(tail)
        _ensure_remote_reference_allowed(remote_target, config, detected, context="git pull")
        if branch_target and ":" in branch_target:
            _ensure_transport_refspec_allowed(branch_target, current_branch, target_branch, context="git pull")
        elif branch_target and branch_target != target_branch:
            raise ValueError(
                f"Git is blocked because pull for this workspace must stay on {target_branch}, "
                f"but the command targets {branch_target}."
            )
        return

    if subcommand == "push":
        _require_current_branch_matches(current_branch, target_branch)
        remote_target, refspecs = _push_remote_and_refspecs(tail)
        blocked = _blocked_push_mode(tail, refspecs)
        if blocked:
            raise ValueError(f"Git is blocked because {blocked}.")
        _ensure_remote_reference_allowed(remote_target, config, detected, context="git push")
        for refspec in refspecs:
            ref_target = _refspec_target_branch(refspec, current_branch)
            if ref_target != target_branch:
                raise ValueError(
                    f"Git is blocked because push for this workspace must stay on {target_branch}, "
                    f"but the command targets {ref_target}."
                )
        return

    if subcommand == "tag":
        if _is_git_tag_read_only(tail):
            return
        _require_current_branch_matches(current_branch, target_branch)
        return

    branch_bound_commands = {
        "add",
        "rm",
        "mv",
        "restore",
        "reset",
        "clean",
        "stash",
        "commit",
        "merge",
        "rebase",
        "cherry-pick",
        "revert",
        "am",
    }
    if subcommand in branch_bound_commands:
        _require_current_branch_matches(current_branch, target_branch)
        return

    raise ValueError(
        f"Git is blocked because the command '{subcommand}' is not yet explicitly classified by the repo policy."
    )


def _read_text_with_replace(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _slice_chunk(
    text: str,
    offset: int = 0,
    limit: int = 0,
    char_limit: int = CHUNK_CHAR_LIMIT,
    char_offset: int = 0,
) -> dict[str, object]:
    lines = text.splitlines(keepends=True)
    start = max(0, offset)
    start_char_offset = max(0, char_offset if start < len(lines) else 0)
    line_limit = max(1, min(limit or DEFAULT_READ_LINES, 2000))
    total = len(lines)

    if start >= total:
        return {
            "start": start,
            "end": start,
            "next_offset": start,
            "next_char_offset": 0,
            "total": total,
            "body": "(end of content)",
            "reason": None,
            "is_complete": True,
            "char_limit": char_limit,
            "line_fragment": False,
        }

    selected: list[str] = []
    selected_chars = 0
    stop_reason: str | None = None
    next_offset = start
    next_char_offset = start_char_offset
    line_fragment = False
    consumed_lines = 0

    for index in range(start, total):
        line = lines[index]
        current_char_offset = next_char_offset if index == start else 0
        remaining_line = line[current_char_offset:]

        if consumed_lines >= line_limit:
            stop_reason = "line limit"
            next_offset = index
            next_char_offset = 0
            break

        if selected_chars + len(remaining_line) > char_limit:
            stop_reason = "character limit"
            available = char_limit - selected_chars
            if available > 0:
                selected.append(remaining_line[:available])
                selected_chars += available
                next_offset = index
                next_char_offset = current_char_offset + available
                line_fragment = next_char_offset < len(line)
            else:
                next_offset = index
                next_char_offset = current_char_offset
            break

        selected.append(remaining_line)
        selected_chars += len(remaining_line)
        consumed_lines += 1
        next_offset = index + 1
        next_char_offset = 0
    else:
        next_offset = total
        next_char_offset = 0

    is_complete = next_offset >= total and next_char_offset == 0
    body = "".join(selected) or "(empty result)"

    return {
        "start": start,
        "end": start + consumed_lines,
        "next_offset": next_offset,
        "next_char_offset": next_char_offset,
        "total": total,
        "body": body,
        "reason": stop_reason,
        "is_complete": is_complete,
        "char_limit": char_limit,
        "line_fragment": line_fragment,
    }


def _render_chunk_text(chunk: dict[str, object], source_label: str) -> tuple[str, bool]:
    start = int(chunk["start"])
    end = int(chunk["end"])
    next_offset = int(chunk["next_offset"])
    next_char_offset = int(chunk["next_char_offset"])
    total = int(chunk["total"])
    body = str(chunk["body"])
    reason = chunk["reason"]
    is_complete = bool(chunk["is_complete"])

    char_suffix = f" | next char offset {next_char_offset}" if next_char_offset else ""
    header = f"[lines {start}–{end} of {total} | next offset {next_offset}{char_suffix}]"
    if next_offset < total or next_char_offset:
        continue_args = f"path={source_label!r}, offset={next_offset}"
        if next_char_offset:
            continue_args += f", char_offset={next_char_offset}"
        footer = (
            f"\n\n... [more content hidden. Stopped by {reason or 'character limit'}. "
            f"Call read_file({continue_args}) to continue.]"
        )
    else:
        footer = ""
    return header + "\n" + body + footer, is_complete


def _format_chunk_text(
    text: str,
    source_label: str,
    offset: int = 0,
    limit: int = 0,
    char_offset: int = 0,
) -> tuple[str, bool]:
    working_char_limit = CHUNK_CHAR_LIMIT

    while True:
        chunk = _slice_chunk(
            text,
            offset=offset,
            limit=limit,
            char_limit=working_char_limit,
            char_offset=char_offset,
        )
        rendered, is_complete = _render_chunk_text(chunk, source_label)
        overflow = len(rendered) - MAX_OUTPUT_CHARS
        if overflow <= 0:
            return rendered, is_complete
        reduced = max(1, working_char_limit - overflow)
        if reduced >= working_char_limit:
            return rendered, is_complete
        working_char_limit = reduced


def _save_long_output(prefix: str, text: str) -> str:
    output_path = _tool_output_path(prefix)
    _atomic_write_text(output_path, text)
    virtual_path = _temp_virtual_path(output_path)
    preview, _ = _format_chunk_text(text, virtual_path)
    return f"Full output saved to {virtual_path}\n\n{preview}"


def _direct_or_saved_output(prefix: str, text: str) -> str:
    if text is None:
        text = "(no output)"
    text = str(text)
    if text == "":
        text = "(empty result)"
    if len(text) > CHUNK_CHAR_LIMIT:
        return _save_long_output(prefix, text)
    return text


def _text_file(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    size = path.stat().st_size
    if size > MAX_TEXT_FILE:
        raise ValueError(f"File is too large ({size:,} bytes; limit {MAX_TEXT_FILE:,})")


def _atomic_write_text(path: Path, content: str) -> None:
    """Write via unique temp file + replace: crash-safe and safe for parallel calls."""
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".mcp-tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


async def _kill_tree(proc: object) -> None:
    if _process_returncode(proc) is not None:
        return
    if _is_async_process(proc):
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/T",
                "/F",
                "/PID",
                str(_process_pid(proc)),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            proc.kill()
    else:
        await asyncio.to_thread(_kill_tree_blocking, proc)
    with contextlib.suppress(TimeoutError, subprocess.TimeoutExpired):
        await _wait_process(proc, timeout=5)


async def _capture_process(
    proc: asyncio.subprocess.Process, timeout: int
) -> tuple[bytes, bytes, bool, bool]:
    """Capture bounded output. Returns stdout, stderr, timed_out, truncated."""
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    total = 0
    limit_reached = asyncio.Event()

    async def consume(stream: asyncio.StreamReader, target: bytearray) -> None:
        nonlocal total
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return
            remaining = MAX_COMMAND_OUTPUT - total
            if remaining <= 0:
                limit_reached.set()
                continue
            accepted = chunk[:remaining]
            target.extend(accepted)
            total += len(accepted)
            if len(accepted) < len(chunk):
                limit_reached.set()

    async def finish() -> None:
        assert proc.stdout is not None and proc.stderr is not None
        await asyncio.gather(
            consume(proc.stdout, stdout_buffer),
            consume(proc.stderr, stderr_buffer),
            proc.wait(),
        )

    run_task = asyncio.create_task(finish())
    limit_task = asyncio.create_task(limit_reached.wait())
    done, _ = await asyncio.wait(
        {run_task, limit_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
    )
    timed_out = not done
    truncated = limit_task in done and limit_reached.is_set()
    if timed_out or truncated:
        await _kill_tree(proc)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(run_task, timeout=5)
    limit_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await limit_task
    return bytes(stdout_buffer), bytes(stderr_buffer), timed_out, truncated


@tool(scope=SCOPE_FILES_READ)
async def transport_health() -> str:
    """Report transport limits and traffic counters for this server.

    Use this when calls fail intermittently: it distinguishes a genuine tool
    error from the transport shedding load. `rejectedOverload` above zero means
    requests were refused on purpose; a high `replayedRetries` means the client
    is resending requests that already arrived.
    """
    payload = {
        "server": {
            "version": SERVER_VERSION,
            "port": PORT,
            "authMode": AUTH_MODE,
            "workspace": str(BASE_DIR),
            "trustedCommands": ALLOW_COMMANDS,
        },
        "transport": {
            "keepAliveSeconds": KEEP_ALIVE_SECONDS,
            "limitConcurrency": LIMIT_CONCURRENCY,
            "socketBacklog": SOCKET_BACKLOG,
            "gzipMinSize": GZIP_MIN_SIZE,
            "gracefulShutdownSeconds": GRACEFUL_SHUTDOWN_SECONDS,
        },
        "outputLimits": {
            "toolOutputChars": MAX_OUTPUT_CHARS,
            "chunkChars": CHUNK_CHAR_LIMIT,
            "commandOutputChars": MAX_COMMAND_OUTPUT,
            "maxResults": MAX_RESULTS,
        },
        "guard": transport_stats(),
        "commandJobs": {
            "maxConcurrent": MAX_COMMAND_JOBS,
            "tracked": len(COMMAND_JOBS),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool(scope=SCOPE_FILES_READ)
async def workspace_info() -> str:
    """Show the allowed workspace, active mode, and git repo-context status."""
    commands = ", ".join(sorted(ALLOWED_COMMANDS)) if ALLOW_COMMANDS else "disabled"
    mode = "trusted developer mode" if ALLOW_COMMANDS else "file-only mode"
    repo_overview = await asyncio.to_thread(_workspace_repo_overview, BASE_DIR)
    profile = PLUGIN_MANAGER.active_profile
    plugin_lines = [
        f"active profile id: {profile.get('profileId', '(none)')}",
        f"active path slot: {profile.get('pathSlot', 0)}",
        f"active access mode: {profile.get('accessMode', 'file_only')}",
        f"active environment mode: {profile.get('environmentMode', 'DEFAULT')}",
        f"profile storage: {PLUGIN_MANAGER.profile_context.get('profileStoragePath') or '(legacy synthetic mode)'}",
        f"plugins discovered: {len(PLUGIN_MANAGER.manifests)}",
        f"plugins loaded: {sum(1 for state in PLUGIN_MANAGER.states.values() if state.get('status') == 'loaded')}",
    ]
    return (
        f"workspace: {BASE_DIR}\nmode: {mode}\ncommands: {commands}\n"
        f"max text file: {MAX_TEXT_FILE:,} bytes\n"
        f"repo context file: {REPO_CONTEXT_FILE}\n"
        + "\n".join(plugin_lines)
        + "\n"
        + repo_overview
    )


@tool(scope=SCOPE_FILES_READ)
async def list_plugins() -> str:
    """List discoverable plugins and their current attachment/effective state."""
    return await asyncio.to_thread(PLUGIN_MANAGER.list_plugins_text)


@tool(scope=SCOPE_FILES_READ)
async def plugin_status() -> str:
    """Show active profile, plugin scope, effective mode, and loader diagnostics."""
    return await asyncio.to_thread(PLUGIN_MANAGER.diagnostics_text)


@tool(scope=SCOPE_FILES_WRITE)
async def attach_plugin(
    plugin_id: str,
    scope: str = "current",
    requested_mode: str = "read_only",
    config_json: str = "{}",
) -> str:
    """Attach a discoverable plugin in current or global scope. Restart MCP after attaching to rebuild the tool registry."""
    try:
        config = json.loads(config_json or "{}")
    except json.JSONDecodeError as exc:
        raise PluginError(f"config_json is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise PluginError("config_json must decode to a JSON object")
    return await asyncio.to_thread(
        PLUGIN_MANAGER.attach_plugin,
        plugin_id,
        scope,
        requested_mode,
        config,
    )


@tool(scope=SCOPE_FILES_WRITE)
async def detach_plugin(plugin_id: str, scope: str = "current") -> str:
    """Detach a plugin from current or global scope. Restart MCP after detaching to rebuild the tool registry."""
    return await asyncio.to_thread(PLUGIN_MANAGER.detach_plugin, plugin_id, scope)


@tool(scope=SCOPE_GIT)
async def repo_context_status(cwd: str = ".") -> str:
    """Show the current repo-context configuration, git detection, and next setup step."""
    workdir = _path(cwd)
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return await asyncio.to_thread(_repo_context_summary, workdir)


@tool(scope=SCOPE_GIT)
async def inspect_git_repository(cwd: str = ".") -> str:
    """Inspect the git repository in this workspace without running any mutating git command."""
    workdir = _path(cwd)
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return await asyncio.to_thread(_inspect_git_repository_text, workdir)


@tool(scope=SCOPE_GIT)
async def configure_repo_context(
    repository_url: str,
    is_fork: bool,
    upstream_url: str = "",
    default_branch: str = "",
    branch_mode: str = "default_branch",
    commit_branch: str = "",
    cwd: str = ".",
    confirm_defaults: bool = False,
    confirm_reconfigure: bool = False,
) -> str:
    """Low-level manual override for the local repo-context file. Prefer setup_git_context() for normal use."""
    workdir = _path(cwd)
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    try:
        existing = await asyncio.to_thread(_load_repo_context, workdir)
    except ValueError:
        existing = None
    detected = await asyncio.to_thread(_detect_git_repo, workdir)
    branch_mode = branch_mode.strip() or "default_branch"
    default_branch = default_branch.strip()
    defaults_used: list[str] = []
    if branch_mode == "specified_branch" and not commit_branch.strip():
        raise ValueError("commit_branch is required when branch_mode='specified_branch'")
    if branch_mode == "default_branch" and not default_branch:
        inferred_default_branch = str(detected["branch"] or "").strip()
        if not inferred_default_branch:
            raise ValueError("default_branch must be explicitly set when it cannot be inferred safely")
        default_branch = inferred_default_branch
        defaults_used.append(f"default_branch='{default_branch}'")
    _require_explicit_defaults(defaults_used, confirm_defaults)
    desired = _build_repo_context_desired(
        status="configured",
        repository_url=repository_url,
        is_fork=is_fork,
        upstream_url=upstream_url,
        default_branch=default_branch,
        branch_mode=branch_mode,
        commit_branch=commit_branch,
        git_enabled=True,
        disabled_reason="",
    )
    _require_repo_context_confirmation(existing, desired, confirm_reconfigure)
    config_path = await asyncio.to_thread(
        _save_repo_context,
        cwd=workdir,
        status="configured",
        repository_url=repository_url,
        is_fork=is_fork,
        upstream_url=upstream_url,
        default_branch=default_branch,
        branch_mode=branch_mode,
        commit_branch=commit_branch,
        git_enabled=True,
        disabled_reason="",
        last_detected_origin=str(detected["origin_url"]),
        last_detected_branch=str(detected["branch"] or default_branch),
    )
    summary = await asyncio.to_thread(_repo_context_summary, workdir)
    return f"Saved repo context to {display_path(config_path)}\n\n{summary}"


@tool(scope=SCOPE_GIT)
async def setup_git_context(
    mode: str,
    repository_url: str = "",
    fork_status: str = "",
    upstream_url: str = "",
    cwd: str = ".",
    default_branch: str = "",
    branch_mode: str = "default_branch",
    commit_branch: str = "",
    disable_reason: str = "",
    force_origin_update: bool = False,
    set_upstream_remote: bool = False,
    confirm_defaults: bool = False,
    confirm_reconfigure: bool = False,
) -> str:
    """Safely initialize, bind, rebind, or disable git for this workspace before ordinary git commands are allowed."""
    workdir = _path(cwd)
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return await asyncio.to_thread(
        _setup_git_context_sync,
        workdir,
        mode=mode,
        repository_url=repository_url,
        fork_status=fork_status,
        upstream_url=upstream_url,
        default_branch=default_branch,
        branch_mode=branch_mode,
        commit_branch=commit_branch,
        disable_reason=disable_reason,
        force_origin_update=force_origin_update,
        set_upstream_remote=set_upstream_remote,
        confirm_defaults=confirm_defaults,
        confirm_reconfigure=confirm_reconfigure,
    )


@tool(scope=SCOPE_FILES_READ)
async def list_dir(

    path: str = ".",
    recursive: bool = False,
    include_hidden: bool = False,
    max_results: int = 300,
) -> str:
    """List files inside the workspace. Large dependency/cache folders are skipped."""
    root = _path(path)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    limit = max(1, min(max_results, MAX_RESULTS))
    rows: list[str] = []

    if not recursive:
        for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if should_skip(item, include_hidden, EXCLUDES):
                continue
            kind = "DIR" if item.is_dir() else f"{item.stat().st_size:,} B"
            rows.append(f"{kind:>12}  {item.name}")
            if len(rows) >= limit:
                break
    else:
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            dirs[:] = sorted(
                d
                for d in dirs
                if not should_skip(current_path / d, include_hidden, EXCLUDES)
            )
            for name in sorted(files):
                item = current_path / name
                if should_skip(item, include_hidden, EXCLUDES):
                    continue
                rows.append(str(item.relative_to(root)))
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break

    if not rows:
        return "Directory is empty."
    suffix = f"\n... limited to {limit} results" if len(rows) >= limit else ""
    return _direct_or_saved_output("list-dir", "\n".join(rows) + suffix)


@tool(scope=SCOPE_FILES_READ)
async def file_info(path: str) -> str:
    """Show file or directory metadata."""
    item = _path(path)
    _ensure_writable(item)
    if not item.exists():
        return f"Not found: {path}"
    stat = item.stat()
    kind = "directory" if item.is_dir() else "file"
    modified = dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    return (
        f"path: {display_path(item)}\ntype: {kind}\n"
        f"size: {stat.st_size:,}\nmodified: {modified}"
    )


@tool(scope=SCOPE_FILES_READ)
async def read_file(path: str, offset: int = 0, limit: int = 0, char_offset: int = 0) -> str:
    """Read a text file in chunks with a character budget that takes priority over line count."""
    item, is_temp_file = _resolve_read_file_path(path)
    if not item.exists():
        raise ValueError(f"File not found: {item}")
    if not item.is_file():
        raise ValueError(f"Not a file: {item}")

    def _read() -> str:
        _text_file(item)
        with item.open("rb") as handle:
            if _is_binary_bytes(handle.read(8192)):
                label = path if is_temp_file else str(display_path(item))
                return f"(binary file, not shown as text): {label} — {item.stat().st_size:,} bytes"
        text_content = _read_text_with_replace(item)
        rendered, is_complete = _format_chunk_text(
            text_content,
            path,
            offset=offset,
            limit=limit,
            char_offset=char_offset,
        )
        if is_temp_file and is_complete:
            with contextlib.suppress(OSError):
                item.unlink()
        return rendered

    return await asyncio.to_thread(_read)



@tool(scope=SCOPE_FILES_WRITE)
async def write_file(path: str, content: str, overwrite: bool = True) -> str:
    """Write a UTF-8 text file inside the workspace."""
    encoded_size = len(content.encode("utf-8"))
    if encoded_size > MAX_WRITE:
        raise ValueError(f"Content exceeds {MAX_WRITE:,} bytes")
    item = _path(path)
    _ensure_writable(item)
    if item.exists() and not overwrite:
        raise ValueError(f"File already exists: {path}")

    def _write() -> None:
        item.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(item, content)

    await asyncio.to_thread(_write)
    return f"Wrote {len(content):,} characters to {display_path(item)}"


@tool(scope=SCOPE_FILES_WRITE)
async def append_file(path: str, content: str) -> str:
    """Append UTF-8 text while keeping the resulting file under the size limit."""
    encoded_size = len(content.encode("utf-8"))
    if encoded_size > MAX_WRITE:
        raise ValueError(f"Content exceeds {MAX_WRITE:,} bytes")
    item = _path(path)
    _ensure_writable(item)
    current_size = item.stat().st_size if item.exists() else 0
    if current_size + encoded_size > MAX_TEXT_FILE:
        raise ValueError(f"Resulting file would exceed {MAX_TEXT_FILE:,} bytes")

    def _append() -> None:
        item.parent.mkdir(parents=True, exist_ok=True)
        with item.open("a", encoding="utf-8", newline="") as handle:
            handle.write(content)

    await asyncio.to_thread(_append)
    return f"Appended {len(content):,} characters to {display_path(item)}"


@tool(scope=SCOPE_FILES_READ)
async def read_many_files(paths: str, per_file_chars: int = 4000) -> str:
    """Read several files in one call. Give a comma or newline separated list.

    Prefer this over repeated read_file calls: every request through a tunnel is
    another chance to hit a relay hiccup, and a burst of small calls is the
    traffic shape that breaks transports. The files share one character budget,
    so the answer stays predictable no matter how many were asked for.
    """
    wanted = [item.strip() for item in re.split(r"[,\n]", paths) if item.strip()]
    if not wanted:
        raise ValueError("paths is required: pass a comma or newline separated list")
    if len(wanted) > 40:
        raise ValueError(f"Too many files at once ({len(wanted)}); ask for 40 or fewer")

    def _read_all() -> str:
        entries: list[tuple[str, str]] = []
        for raw in wanted:
            try:
                item = _path(raw)
            except Exception as exc:  # noqa: BLE001 - reported per file, never fatal
                entries.append((raw, f"(error: {exc})"))
                continue
            if not item.exists():
                entries.append((raw, "(file not found)"))
                continue
            if not item.is_file():
                entries.append((raw, "(not a file)"))
                continue
            try:
                data = item.read_bytes()
            except OSError as exc:
                entries.append((raw, f"(unreadable: {exc})"))
                continue
            if _is_binary_bytes(data[:8192]):
                entries.append((raw, f"(binary file, {len(data):,} bytes)"))
                continue
            entries.append((str(display_path(item)), data.decode("utf-8", errors="replace")))
        return render_batch(
            entries,
            per_file_chars=max(200, min(int(per_file_chars), CHUNK_CHAR_LIMIT)),
            total_chars=MAX_OUTPUT_CHARS,
        )

    return await asyncio.to_thread(_read_all)


@tool(scope=SCOPE_FILES_READ)
async def tail_file(path: str, limit: int = 100) -> str:
    """Show the last lines of a text file. Made for logs.

    Reading a whole log to see the newest error wastes the budget and the tokens;
    the interesting part of a log is almost always at the end.
    """
    item = _path(path)
    if not item.is_file():
        raise ValueError(f"File not found: {item}")

    def _tail() -> str:
        _text_file(item)
        data = item.read_bytes()
        if _is_binary_bytes(data[:8192]):
            raise ValueError(f"Refusing to tail a binary file: {display_path(item)}")
        lines, total = tail_lines(data.decode("utf-8", errors="replace"), int(limit))
        head = f"{display_path(item)}: last {len(lines)} of {total:,} lines"
        return head + "\n" + "\n".join(lines)

    return await asyncio.to_thread(_tail)


@tool(scope=SCOPE_FILES_WRITE)
async def apply_patch(path: str, diff: str) -> str:
    """Apply a unified diff to one file.

    Cheaper and safer than rewriting a file to change a few lines: the diff
    carries only the change, and the context lines prove the file still looks the
    way the caller thinks it does. Either every hunk applies or none do, so the
    file never ends up in a state nobody described.

    Line numbers are treated as hints and the context is searched for nearby, so
    a slightly stale diff still applies.
    """
    item = _path(path)
    _ensure_writable(item)
    if not item.is_file():
        raise ValueError(f"File not found: {item}")

    def _patch() -> str:
        _text_file(item)
        data = item.read_bytes()
        if _is_binary_bytes(data):
            raise ValueError(f"Refusing to patch binary file: {display_path(item)}")
        original = data.decode("utf-8", errors="replace")
        updated, hunks = apply_unified_diff(original, diff)
        if updated == original:
            return f"No change: the diff is already applied to {display_path(item)}"
        if len(updated.encode("utf-8")) > MAX_WRITE:
            raise ValueError(f"Result exceeds {MAX_WRITE:,} bytes")
        item.write_text(updated, encoding="utf-8", newline="")
        preview = first_change_preview(original, updated)
        suffix = f"\nfirst change: {preview}" if preview else ""
        return f"Applied {hunks} hunk(s) to {display_path(item)}{suffix}"

    return await asyncio.to_thread(_patch)


@tool(scope=SCOPE_FILES_WRITE)
async def search_and_replace(
    search: str,
    replace: str,
    file_glob: str = "*",
    path: str = ".",
    regex: bool = False,
    ignore_case: bool = False,
    apply: bool = False,
    max_files: int = 200,
) -> str:
    """Replace text across many files. Previews by default.

    ``apply`` is false to begin with on purpose: a project-wide replacement is
    easy to get wrong and hard to undo, so the first answer shows what would
    change, with a sample line per file. Run it again with apply=true once the
    preview looks right.
    """
    root = _path(path)
    if not root.exists():
        raise ValueError(f"Path not found: {root}")

    def _run() -> str:
        candidates = (
            [root]
            if root.is_file()
            else [item for item in sorted(root.rglob(file_glob)) if item.is_file()]
        )
        hits: list[ReplaceHit] = []
        scanned = 0
        for item in candidates:
            if len(hits) >= int(max_files):
                break
            if any(part in EXCLUDES for part in item.relative_to(BASE_DIR).parts[:-1]):
                continue
            try:
                data = item.read_bytes()
            except OSError:
                continue
            if _is_binary_bytes(data[:8192]) or len(data) > MAX_TEXT_FILE:
                continue
            scanned += 1
            original = data.decode("utf-8", errors="replace")
            updated, count = replace_in_text(
                original, search, replace, regex=regex, ignore_case=ignore_case
            )
            if not count:
                continue
            hit = ReplaceHit(
                path=str(display_path(item)),
                count=count,
                preview=first_change_preview(original, updated),
            )
            hits.append(hit)
            if apply:
                _ensure_writable(item)
                item.write_text(updated, encoding="utf-8", newline="")
        if not hits:
            return f"No matches for {search!r} in {scanned:,} scanned file(s)."
        total = sum(hit.count for hit in hits)
        verb = "Replaced" if apply else "Would replace"
        lines = [f"{verb} {total:,} occurrence(s) in {len(hits)} file(s):"]
        for hit in hits:
            lines.append(f"  {hit.path}  ({hit.count})")
            if hit.preview:
                lines.append(f"      {hit.preview}")
        if not apply:
            lines.append("")
            lines.append("Nothing was written. Re-run with apply=true to commit this.")
        return "\n".join(lines)

    return await asyncio.to_thread(_run)


@tool(scope=SCOPE_FILES_WRITE)
async def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Replace exact text in a UTF-8 file. Read the file first."""
    item = _path(path)
    _ensure_writable(item)
    _text_file(item)

    def _edit() -> int:
        data = item.read_bytes()
        if _is_binary_bytes(data):
            raise ValueError(f"Refusing to edit binary file: {display_path(item)}")
        text_content = data.decode("utf-8", errors="replace")
        found = text_content.count(old_string)
        if found == 0:
            raise ValueError("old_string was not found")
        if not replace_all and found > 1:
            raise ValueError(
                f"old_string occurs {found} times; use a larger match or replace_all"
            )
        count = found if replace_all else 1
        updated = (
            text_content.replace(old_string, new_string)
            if replace_all
            else text_content.replace(old_string, new_string, 1)
        )
        if len(updated.encode("utf-8")) > MAX_TEXT_FILE:
            raise ValueError("Updated file would exceed the size limit")
        _atomic_write_text(item, updated)
        return count

    count = await asyncio.to_thread(_edit)
    return f"Replaced {count} occurrence(s) in {display_path(item)}"


@tool(scope=SCOPE_FILES_WRITE)
async def create_dir(path: str) -> str:
    """Create a directory and missing parents. Existing directories are accepted."""
    item = _path(path)
    _ensure_writable(item)
    await asyncio.to_thread(item.mkdir, parents=True, exist_ok=True)
    return f"Directory ready: {display_path(item)}"


@tool(scope=SCOPE_FILES_WRITE)
async def delete_file(path: str) -> str:
    """Delete one file or one empty directory. Recursive deletion is unavailable."""
    item = _path(path)
    _ensure_writable(item)
    if not item.exists():
        return f"Not found: {path}"
    if item.is_dir():
        await asyncio.to_thread(item.rmdir)
    else:
        await asyncio.to_thread(item.unlink)
    return f"Deleted: {display_path(item)}"


@tool(scope=SCOPE_FILES_WRITE)
async def copy_file(src: str, dst: str, overwrite: bool = False) -> str:
    """Copy one file inside the workspace."""
    source, target = _path(src), _path(dst)
    _ensure_writable(target)
    if not source.is_file():
        raise ValueError(f"Source is not a file: {src}")
    source_size = source.stat().st_size
    if source_size > MAX_COPY_MOVE_BYTES:
        raise ValueError(f"Source size {source_size:,} bytes exceeds copy/move limit {MAX_COPY_MOVE_BYTES:,}")
    if target.exists() and not overwrite:
        raise ValueError(f"Destination exists: {dst}")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, source, target)
    return f"Copied {display_path(source)} -> {display_path(target)}"


@tool(scope=SCOPE_FILES_WRITE)
async def move_file(src: str, dst: str, overwrite: bool = False) -> str:
    """Move or rename one file inside the workspace."""
    source, target = _path(src), _path(dst)
    _ensure_writable(source)
    _ensure_writable(target)
    if not source.is_file():
        raise ValueError(f"Source is not a file: {src}")
    source_size = source.stat().st_size
    if source_size > MAX_COPY_MOVE_BYTES:
        raise ValueError(f"Source size {source_size:,} bytes exceeds copy/move limit {MAX_COPY_MOVE_BYTES:,}")
    if target.exists() and not overwrite:
        raise ValueError(f"Destination exists: {dst}")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(source), str(target))
    return f"Moved {display_path(source)} -> {display_path(target)}"


@tool(scope=SCOPE_FILES_READ)
async def glob_files(pattern: str, path: str = ".", max_results: int = 300) -> str:
    """Find workspace files using a glob such as **/*.py."""
    root = _path(path)
    limit = max(1, min(max_results, MAX_RESULTS))
    rows: list[str] = []
    for item in root.glob(pattern):
        if not item.is_file() or any(part in EXCLUDES for part in item.parts):
            continue
        safe_path(BASE_DIR, item)
        rows.append(str(item.relative_to(root)))
        if len(rows) >= limit:
            break
    return "\n".join(sorted(rows)) if rows else "No files matched."


@tool(scope=SCOPE_FILES_READ)
async def grep_files(
    pattern: str,
    path: str = ".",
    file_glob: str = "*",
    regex: bool = False,
    max_results: int = 100,
) -> str:
    """Search text files with bounded output. Regex mode is disabled for safety."""
    if regex:
        raise ValueError("Regex mode is disabled to prevent pathological expressions")
    root = _path(path)
    limit = max(1, min(max_results, 500))

    def _grep() -> str:
        rows: list[str] = []
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            dirs[:] = [d for d in dirs if d not in EXCLUDES and not d.startswith(".")]
            for name in files:
                if not fnmatch.fnmatch(name, file_glob):
                    continue
                item = current_path / name
                try:
                    if item.stat().st_size > MAX_TEXT_FILE:
                        continue
                    for number, line in enumerate(
                        item.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                    ):
                        if pattern.lower() in line.lower():
                            rows.append(
                                f"{item.relative_to(root)}:{number}: {line.rstrip()}"
                            )
                            if len(rows) >= limit:
                                rows.append(f"... limited to {limit} results")
                                return _direct_or_saved_output("grep-files", "\n".join(rows))
                except (OSError, UnicodeError):
                    continue
        result = "\n".join(rows) if rows else "No matches found."
        return _direct_or_saved_output("grep-files", result)

    return await asyncio.to_thread(_grep)


async def _capture_process_to_files(
    proc: asyncio.subprocess.Process,
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> tuple[bool, bool]:
    total = 0
    limit_reached = asyncio.Event()

    async def consume(stream: asyncio.StreamReader, target_path: Path) -> None:
        nonlocal total
        with target_path.open("wb") as handle:
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    handle.flush()
                    os.fsync(handle.fileno())
                    return
                remaining = MAX_COMMAND_OUTPUT - total
                if remaining <= 0:
                    limit_reached.set()
                    continue
                accepted = chunk[:remaining]
                handle.write(accepted)
                total += len(accepted)
                if len(accepted) < len(chunk):
                    limit_reached.set()

    async def finish() -> None:
        assert proc.stdout is not None and proc.stderr is not None
        await asyncio.gather(
            consume(proc.stdout, stdout_path),
            consume(proc.stderr, stderr_path),
            proc.wait(),
        )

    run_task = asyncio.create_task(finish())
    limit_task = asyncio.create_task(limit_reached.wait())
    done, _ = await asyncio.wait(
        {run_task, limit_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
    )

    timed_out = False
    truncated = False
    if run_task in done:
        await run_task
        truncated = limit_task in done and limit_reached.is_set()
    elif limit_task in done:
        truncated = True
        await _kill_tree(proc)
        await run_task
    else:
        timed_out = True
        await _kill_tree(proc)
        await run_task

    limit_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await limit_task
    return timed_out, truncated


def _format_command_result(
    returncode: int | None,
    stdout_text: str,
    stderr_text: str,
    *,
    timed_out: bool = False,
    truncated: bool = False,
    truncated_limit: int = MAX_COMMAND_OUTPUT,
) -> str:
    stdout_text = stdout_text if stdout_text != "" else "(empty result)"
    stderr_text = stderr_text if stderr_text != "" else "(empty result)"
    prefix_parts: list[str] = []
    if timed_out:
        prefix_parts.append("Timed out before the command completed (process tree stopped).")
    if truncated:
        prefix_parts.append(
            f"Output truncated after reaching the safe combined limit of {truncated_limit:,} bytes."
        )
    prefix = "\n".join(prefix_parts)
    if prefix:
        prefix += "\n"
    return (
        prefix
        + f"exit code: {returncode}\n"
        + f"--- stdout ---\n{stdout_text}\n"
        + f"--- stderr ---\n{stderr_text}"
    )


async def _prepare_command(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 60,
) -> tuple[str, Path, list[str], int]:
    if not ALLOW_COMMANDS:
        raise ValueError(
            "Command execution is disabled. Re-run SETUP.bat to enable trusted developer mode."
        )
    args_list = list(args or [])
    executable = resolve_program(BASE_DIR, program, ALLOWED_COMMANDS)
    workdir = _path(cwd)
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    if normalized_program_name(program) == "git":
        await asyncio.to_thread(_ensure_git_context_for_command, workdir, args_list)
    seconds = max(1, min(timeout, MAX_COMMAND_TIMEOUT))
    return executable, workdir, args_list, seconds


def _command_summary(program: str, args: list[str]) -> str:
    pieces = [program, *args]
    return " ".join(piece if " " not in piece else repr(piece) for piece in pieces)


async def _read_command_output(path: Path) -> str:
    if not path.exists():
        return ""
    return await asyncio.to_thread(_read_text_with_replace, path)


def _delete_job_artifacts(job: CommandJob) -> None:
    for artifact in (job.stdout_path, job.stderr_path):
        with contextlib.suppress(OSError):
            artifact.unlink()


def _job_elapsed_seconds(job: CommandJob) -> float:
    end = job.finished_at if job.finished_at is not None else time.time()
    return max(0.0, end - job.started_at)


def _prune_command_jobs() -> None:
    now = time.time()
    expired: list[str] = []
    for job_id, job in COMMAND_JOBS.items():
        if job.status == "running":
            continue
        if job.finished_at is None:
            continue
        if now - job.finished_at >= JOB_RETENTION_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        job = COMMAND_JOBS.pop(job_id, None)
        if job is not None:
            _delete_job_artifacts(job)


def _count_running_command_jobs() -> int:
    return sum(1 for job in COMMAND_JOBS.values() if job.status == "running")


def _capture_process_to_job_files_blocking(
    proc: subprocess.Popen[bytes],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> tuple[bool, bool]:
    total = 0
    truncated = False
    total_lock = threading.Lock()

    def consume(stream, target_path: Path) -> None:
        nonlocal total, truncated
        with target_path.open("wb", buffering=1024 * 1024) as handle:
            while True:
                chunk = stream.read(BACKGROUND_COMMAND_READ_CHUNK)
                if not chunk:
                    return
                with total_lock:
                    remaining = MAX_BACKGROUND_COMMAND_OUTPUT - total
                    if remaining <= 0:
                        truncated = True
                        continue
                    accepted = chunk[:remaining]
                    if accepted:
                        handle.write(accepted)
                        total += len(accepted)
                    if len(accepted) < len(chunk):
                        truncated = True

    assert proc.stdout is not None and proc.stderr is not None
    threads = [
        threading.Thread(target=consume, args=(proc.stdout, stdout_path), daemon=True),
        threading.Thread(target=consume, args=(proc.stderr, stderr_path), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree_blocking(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    finally:
        for thread in threads:
            thread.join(timeout=5)
        _close_process_streams(proc)
    return timed_out, truncated


async def _capture_process_to_job_files(
    proc: subprocess.Popen[bytes],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> tuple[bool, bool]:
    return await asyncio.to_thread(
        _capture_process_to_job_files_blocking,
        proc,
        stdout_path,
        stderr_path,
        timeout,
    )


async def _run_command_job(job_id: str) -> None:
    job = COMMAND_JOBS[job_id]
    proc = job.process
    if proc is None:
        job.status = "failed"
        job.finished_at = time.time()
        return
    try:
        timed_out, truncated = await _capture_process_to_job_files(
            proc,
            job.stdout_path,
            job.stderr_path,
            job.timeout,
        )
        job.timed_out = timed_out
        job.truncated = truncated
        job.returncode = proc.returncode
        if job.cancel_requested:
            job.status = "cancelled"
        elif timed_out:
            job.status = "timed_out"
        elif proc.returncode == 0:
            job.status = "completed"
        else:
            job.status = "failed"
    except Exception as exc:
        with contextlib.suppress(OSError):
            with job.stderr_path.open("ab") as handle:
                handle.write(f"\n[internal job runner error] {exc}\n".encode("utf-8", "replace"))
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                await _kill_tree(proc)
        job.returncode = proc.returncode
        job.status = "failed"
    finally:
        job.finished_at = time.time()
        job.process = None
        job.task = None


def _job_status_summary(job: CommandJob) -> str:
    lines = [
        f"job_id: {job.job_id}",
        f"status: {job.status}",
        f"command: {job.command}",
        f"cwd: {job.cwd}",
        f"elapsed: {_job_elapsed_seconds(job):.1f}s",
        f"timeout: {job.timeout}s",
    ]
    if job.returncode is not None:
        lines.append(f"exit code: {job.returncode}")
    if job.truncated:
        lines.append(
            f"captured output truncated after {MAX_BACKGROUND_COMMAND_OUTPUT:,} bytes"
        )
    return "\n".join(lines)


async def _job_result(job: CommandJob) -> str:
    stdout_text = await _read_command_output(job.stdout_path)
    stderr_text = await _read_command_output(job.stderr_path)
    if len(stdout_text) + len(stderr_text) > MAX_OUTPUT_CHARS:
        lines = [
            _job_status_summary(job),
            f"stdout: {_temp_virtual_path(job.stdout_path)}",
            f"stderr: {_temp_virtual_path(job.stderr_path)}",
        ]
        return "\n".join(lines)
    formatted = _format_command_result(
        job.returncode,
        stdout_text,
        stderr_text,
        timed_out=job.timed_out,
        truncated=job.truncated,
        truncated_limit=MAX_BACKGROUND_COMMAND_OUTPUT,
    )
    if job.status != "completed":
        return f"{_job_status_summary(job)}\n\n{formatted}"
    return formatted


def _background_only_reason(program: str, args: list[str] | None) -> str | None:
    name = normalized_program_name(program)
    lowered = [str(item).strip().lower() for item in (args or [])]
    if name in {"python", "py"} and "-m" in lowered:
        idx = lowered.index("-m")
        module_name = lowered[idx + 1] if idx + 1 < len(lowered) else ""
        if module_name in {"unittest", "pytest"}:
            return f"{program} -m {module_name} usually outlives one Streamable HTTP request"
    if name == "pytest":
        return "pytest runs are safer as background command jobs"
    if name == "git" and lowered and lowered[0] in {"push", "pull", "fetch", "clone", "merge", "rebase"}:
        return f"git {lowered[0]} is safer as a background command job"
    if name in {"npm", "npx"} and lowered and lowered[0] in {"install", "ci", "test", "run", "build"}:
        return f"{name} {lowered[0]} is safer as a background command job"
    return None


@tool(scope=SCOPE_COMMANDS_RUN)
async def start_command(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 60,
) -> str:
    """Trusted developer mode: start an allow-listed program in the background and return a job id immediately."""
    _prune_command_jobs()
    if _count_running_command_jobs() >= MAX_COMMAND_JOBS:
        raise ValueError(
            f"Too many running command jobs. Wait for one to finish or cancel it first (limit {MAX_COMMAND_JOBS})."
        )

    executable, workdir, args_list, seconds = await _prepare_command(program, args, cwd, timeout)
    flags = 0x00000200 if os.name == "nt" else 0
    job_id = uuid.uuid4().hex[:12]
    stdout_capture = _tool_output_path(f"command-job-{job_id}-stdout")
    stderr_capture = _tool_output_path(f"command-job-{job_id}-stderr")
    proc = subprocess.Popen(
        [executable, *args_list],
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        env=_sanitized_env(),
    )
    job = CommandJob(
        job_id=job_id,
        program=program,
        args=args_list,
        cwd=str(display_path(workdir)) if workdir != BASE_DIR else ".",
        timeout=seconds,
        command=_command_summary(program, args_list),
        stdout_path=stdout_capture,
        stderr_path=stderr_capture,
        started_at=time.time(),
        process=proc,
    )
    COMMAND_JOBS[job_id] = job
    job.task = asyncio.create_task(_run_command_job(job_id))
    return (
        f"Started background command job {job_id}.\n"
        f"command: {job.command}\n"
        f"cwd: {job.cwd}\n"
        f"timeout: {seconds}s\n"
        f'Poll get_command_status(job_id="{job_id}") for progress and the result.'
    )


@tool(scope=SCOPE_COMMANDS_RUN)
async def get_command_status(job_id: str) -> str:
    """Get the current status or final result of a background command job."""
    _prune_command_jobs()
    job = COMMAND_JOBS.get(job_id)
    if job is None:
        raise ValueError(
            f"Unknown command job: {job_id}. Use list_commands() to see tracked jobs."
        )
    if job.status == "running":
        return _job_status_summary(job)
    return await _job_result(job)


@tool(scope=SCOPE_COMMANDS_RUN)
async def cancel_command(job_id: str) -> str:
    """Cancel a running background command job."""
    _prune_command_jobs()
    job = COMMAND_JOBS.get(job_id)
    if job is None:
        raise ValueError(
            f"Unknown command job: {job_id}. Use list_commands() to see tracked jobs."
        )
    if job.status != "running" or job.process is None:
        return f"Command job {job_id} is already {job.status}."
    job.cancel_requested = True
    await _kill_tree(job.process)
    if job.task is not None:
        await job.task
    return _job_status_summary(job)


@tool(scope=SCOPE_COMMANDS_RUN)
async def list_commands() -> str:
    """List tracked background command jobs."""
    _prune_command_jobs()
    if not COMMAND_JOBS:
        return "No tracked command jobs."
    rows = []
    for job in sorted(
        COMMAND_JOBS.values(), key=lambda item: (item.status != "running", -item.started_at)
    ):
        rows.append(
            f"{job.job_id} | {job.status} | {_job_elapsed_seconds(job):.1f}s | {job.command}"
        )
    return "\n".join(rows)


@tool(scope=SCOPE_COMMANDS_RUN)
async def run_command(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 60,
) -> str:
    """Trusted developer mode: run an allow-listed program without a shell.

    Short output is returned directly. Long output is saved to a file and returned
    through the same chunked reading model as read_file().
    """
    executable, workdir, args_list, seconds = await _prepare_command(program, args, cwd, timeout)
    reason = _background_only_reason(program, args_list)
    if reason:
        raise ValueError(
            "This command is likely to outlive a single Streamable HTTP request. "
            f"Use start_command(...) and poll get_command_status(...). Reason: {reason}."
        )
    flags = 0x00000200 if os.name == "nt" else 0

    stdout_capture = _tool_output_path("run-command-stdout")
    stderr_capture = _tool_output_path("run-command-stderr")

    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            *args_list,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=flags,
            env=_sanitized_env(),
        )
        timed_out, truncated = await _capture_process_to_files(
            proc,
            stdout_capture,
            stderr_capture,
            seconds,
        )

        stdout_text = await _read_command_output(stdout_capture)
        stderr_text = await _read_command_output(stderr_capture)
        result = _format_command_result(
            proc.returncode,
            stdout_text,
            stderr_text,
            timed_out=timed_out,
            truncated=truncated,
            truncated_limit=MAX_COMMAND_OUTPUT,
        )
        return _direct_or_saved_output("run-command", result)
    finally:
        with contextlib.suppress(OSError):
            stdout_capture.unlink()
        with contextlib.suppress(OSError):
            stderr_capture.unlink()


def _extract_token(request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def _host_allowed(host_header: str) -> bool:
    host = host_header.split(":", 1)[0].strip().lower()
    if host in {"127.0.0.1", "localhost"}:
        return True
    if PUBLIC_HOST and host == PUBLIC_HOST:
        return True
    if STABLE_HOSTNAME:
        return host == f"{STABLE_HOSTNAME}{SERVEO_SUFFIX}"
    return host.endswith(SERVEO_SUFFIX)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Legacy-mode gate: host allowlist + static master token on every route."""

    async def dispatch(self, request, call_next):
        if not _host_allowed(request.headers.get("host", "")):
            return JSONResponse({"error": "forbidden host"}, status_code=403)
        incoming = _extract_token(request)
        if not incoming or not _consteq(incoming, TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.url.path == "/health":
            return JSONResponse({"status": "ok"})
        return await call_next(request)


class HostCheckMiddleware(BaseHTTPMiddleware):
    """OAuth-mode gate: host allowlist only; auth is enforced per route."""

    async def dispatch(self, request, call_next):
        if not _host_allowed(request.headers.get("host", "")):
            return JSONResponse({"error": "forbidden host"}, status_code=403)
        return await call_next(request)


class XApiKeyCompatMiddleware:
    """Dual mode: let legacy clients send the master token via X-API-Key."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = list(scope.get("headers", []))
            has_auth = any(name == b"authorization" for name, _ in headers)
            api_key = next((value for name, value in headers if name == b"x-api-key"), b"")
            if not has_auth and api_key:
                headers.append((b"authorization", b"Bearer " + api_key))
                scope = dict(scope)
                scope["headers"] = headers
        await self.app(scope, receive, send)


_AUTHORIZE_HINT_STYLE = """
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         max-width: 34rem; margin: 8vh auto; padding: 0 1.25rem; line-height: 1.5; }
  h1 { font-size: 1.25rem; }
  .card { border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
          padding: 1.25rem 1.5rem; }
  ol { padding-left: 1.2rem; }
  li { margin: .35rem 0; }
  code { background: rgba(128,128,128,.15); padding: .1rem .3rem; border-radius: 4px; }
  .muted { opacity: .65; font-size: .85rem; }
"""


def _authorize_hint_html() -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finish connecting &mdash; {SERVER_NAME}</title>
<style>{_AUTHORIZE_HINT_STYLE}</style></head>
<body><div class="card">
<h1>Almost there &mdash; finish the connection</h1>
<p>The OAuth request reached this server without the required query parameters.</p>
<ol>
  <li>Return to the MCP client and start the connection again.</li>
  <li>If you are using Serveo or another public tunnel, open the URL once in a browser and let any interstitial finish loading.</li>
  <li>Then retry the MCP connection flow.</li>
</ol>
<p class="muted">A normal OAuth request is not interrupted. This page only appears when the incoming authorize URL is incomplete.</p>
</div></body></html>"""


class AuthorizeHintMiddleware(BaseHTTPMiddleware):
    _REQUIRED_PARAMS = ("client_id", "response_type", "code_challenge")

    async def dispatch(self, request, call_next):
        if request.method == "GET" and request.url.path == "/authorize":
            if any(not request.query_params.get(name) for name in self._REQUIRED_PARAMS):
                response = HTMLResponse(_authorize_hint_html(), status_code=400)
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                return response
        return await call_next(request)


def _presented_client_secret(request, form, client_id: str, auth_method: str) -> str | None:
    if auth_method == "client_secret_basic":
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return None
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        if ":" not in decoded:
            return None
        basic_client_id, secret = decoded.split(":", 1)
        if unquote(basic_client_id) != client_id:
            return None
        return unquote(secret)
    if auth_method == "client_secret_post":
        raw = form.get("client_secret")
        return raw if isinstance(raw, str) else None
    return None


class ClientSecretAuthMiddleware(BaseHTTPMiddleware):
    """Enforce confidential-client secret authentication on POST /token."""

    async def dispatch(self, request, call_next):
        if request.method != "POST" or request.url.path != TOKEN_PATH:
            return await call_next(request)
        assert oauth_provider is not None
        try:
            await request.body()
            form = await request.form()
        except Exception:
            return await call_next(request)
        client_id = form.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            return await call_next(request)
        stored = oauth_provider.store.clients.get(client_id)
        secret_hash = stored.get("client_secret") if isinstance(stored, dict) else None
        if not secret_hash:
            return await call_next(request)
        auth_method = str(stored.get("token_endpoint_auth_method") or "")
        presented = _presented_client_secret(request, form, client_id, auth_method)
        if not presented or not _consteq(hash_client_secret(presented), secret_hash):
            return JSONResponse(
                {
                    "error": "invalid_client",
                    "error_description": "client authentication failed",
                },
                status_code=401,
            )
        return await call_next(request)


if OAUTH_ENABLED:
    assert oauth_provider is not None
    _consent_handler = ConsentHandler(
        provider=oauth_provider,
        owner_code=OWNER_CODE,
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        max_attempts_per_txn=OAUTH_CONSENT_MAX_ATTEMPTS,
        failure_window_seconds=OAUTH_CONSENT_FAILURE_WINDOW,
        max_failures_per_window=OAUTH_CONSENT_MAX_FAILURES,
    )

    @mcp.custom_route("/consent", methods=["GET", "POST"])
    async def consent_route(request):
        return await _consent_handler.handle(request)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request):
        incoming = _extract_token(request)
        if not incoming or not _consteq(incoming, TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET", "OPTIONS"])
    async def protected_resource_alias(request):
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "mcp-protocol-version",
        }
        if request.method == "OPTIONS":
            return JSONResponse(None, status_code=204, headers=headers)
        return JSONResponse(protected_resource_document(PUBLIC_URL), headers=headers)


def _configure_logging() -> None:
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)


if __name__ == "__main__":
    import uvicorn

    _cleanup_temp_files()
    app = mcp.streamable_http_app()
    # Order matters: middleware added last runs first. The guard must see the
    # request before anything else so a duplicate never reaches the tool layer,
    # and gzip must wrap the guard so replayed bodies are compressed too.
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_SIZE)
    app.add_middleware(TransportGuardMiddleware)
    if AUTH_MODE == AUTH_MODE_LEGACY:
        app.add_middleware(SecurityMiddleware)
    else:
        if AUTH_MODE == AUTH_MODE_DUAL:
            app.add_middleware(XApiKeyCompatMiddleware)
        app.add_middleware(AuthorizeHintMiddleware)
        app.add_middleware(ClientSecretAuthMiddleware)
        app.add_middleware(HostCheckMiddleware)
    print(f"{SERVER_NAME} {SERVER_VERSION}: http://127.0.0.1:{PORT}/mcp")
    print(f"Workspace: {BASE_DIR}")
    print(f"Commands: {'trusted developer mode' if ALLOW_COMMANDS else 'file-only mode'}")
    print(f"Auth mode: {AUTH_MODE}")
    if OAUTH_ENABLED:
        print(f"OAuth issuer: {PUBLIC_URL}")
        print(f"OAuth resource: {resource_url_for(PUBLIC_URL)}")
        print(
            "OAuth discovery: "
            f"{PUBLIC_URL}/.well-known/oauth-authorization-server | "
            f"{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp"
        )
    elif PUBLIC_URL:
        print(f"Public URL: {PUBLIC_URL}")
    _configure_logging()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="info",
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        limit_concurrency=LIMIT_CONCURRENCY,
        backlog=SOCKET_BACKLOG,
        h11_max_incomplete_event_size=H11_MAX_INCOMPLETE_EVENT_SIZE,
    )
