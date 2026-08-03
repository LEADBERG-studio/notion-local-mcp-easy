"""Transport-level protection for the MCP HTTP endpoint.

The server is reached through a reverse tunnel, which is a single TCP path with
a proxy at each end. Three failure modes come from that shape, and none of them
are bugs in tool code:

1. **Retry storms.** A tunnel hiccup makes the client resend a request it already
   delivered. Without deduplication the server runs the same work twice, and the
   second run competes with the first for the same connection. Every JSON-RPC
   request carries an id, so an identical in-flight request can be *joined*
   rather than re-executed, and a just-completed one can be replayed from cache.
2. **Bursts of identical reads.** Agents commonly re-read the same file or list
   the same directory several times while reasoning. Those are pure reads and a
   short TTL cache answers them without touching the disk or the event loop.
3. **Overload.** Accepting unbounded concurrency on one TCP path just queues work
   somewhere invisible until something times out. Admission control with a
   ``Retry-After`` answer is recoverable; a dead transport is not.

Everything here is keyed on the raw request body, so it is transport-level and
knows nothing about individual tools. Cached entries live in memory only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(minimum, value)


# How long a completed response stays replayable for an identical request.
DEDUP_TTL_SECONDS = _int_env("MCP_DEDUP_TTL_SECONDS", 20, 0)
# How long identical read-only calls are answered from cache.
READ_CACHE_TTL_SECONDS = _int_env("MCP_READ_CACHE_TTL_SECONDS", 5, 0)
# Bounded so a long session cannot grow memory without end.
CACHE_MAX_ENTRIES = _int_env("MCP_CACHE_MAX_ENTRIES", 256, 8)
# Responses larger than this are not cached: holding megabytes to save a repeat
# is the wrong trade.
CACHE_MAX_BODY_BYTES = _int_env("MCP_CACHE_MAX_BODY", 512 * 1024, 4096)
# Concurrent in-flight requests. Beyond this, callers are asked to retry.
MAX_INFLIGHT = _int_env("MCP_MAX_INFLIGHT", 24, 1)
# How long a queued request waits for a slot before being told to retry.
ADMISSION_WAIT_SECONDS = _int_env("MCP_ADMISSION_WAIT_SECONDS", 25, 1)
RETRY_AFTER_SECONDS = _int_env("MCP_RETRY_AFTER_SECONDS", 2, 1)

# Tool calls that only observe state. Repeating one within the cache window
# cannot change an answer, so it is safe to serve from memory. Anything not
# listed here is treated as a mutation and never cached.
CACHEABLE_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob_files",
        "grep_files",
        "file_info",
        "workspace_info",
        "list_plugins",
        "plugin_status",
        "list_commands",
        "repo_context_status",
        "inspect_git_repository",
        "ide_gateway_show_config",
    }
)

# JSON-RPC methods that are pure protocol chatter. These repeat constantly and
# never change between calls within a session.
CACHEABLE_METHODS = frozenset({"tools/list", "resources/list", "prompts/list", "initialize"})


@dataclass
class _Entry:
    created: float
    ttl: float
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    event: asyncio.Event | None = None
    hits: int = 0

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created) > self.ttl

    @property
    def ready(self) -> bool:
        return self.event is None or self.event.is_set()


@dataclass
class GuardStats:
    served: int = 0
    joined: int = 0
    replayed: int = 0
    cached: int = 0
    rejected: int = 0
    peak_inflight: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "served": self.served,
            "joinedDuplicates": self.joined,
            "replayedRetries": self.replayed,
            "cacheHits": self.cached,
            "rejectedOverload": self.rejected,
            "peakInflight": self.peak_inflight,
            "limits": {
                "maxInflight": MAX_INFLIGHT,
                "dedupTtlSeconds": DEDUP_TTL_SECONDS,
                "readCacheTtlSeconds": READ_CACHE_TTL_SECONDS,
            },
        }


STATS = GuardStats()


def describe_request(body: bytes) -> tuple[str, str, float]:
    """Return (rpc id, tool or method name, cache ttl) for a request body.

    A ttl of 0 means "never cache", which is the default for anything that is
    not a recognised read.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return "", "", 0.0
    if isinstance(payload, list):
        # Batched calls are not cached: one mutation in the batch poisons it.
        return "", "batch", 0.0
    if not isinstance(payload, dict):
        return "", "", 0.0
    rpc_id = str(payload.get("id", ""))
    method = str(payload.get("method", ""))
    if method in CACHEABLE_METHODS:
        return rpc_id, method, float(READ_CACHE_TTL_SECONDS)
    if method == "tools/call":
        params = payload.get("params")
        name = str((params or {}).get("name", "")) if isinstance(params, dict) else ""
        ttl = float(READ_CACHE_TTL_SECONDS) if name in CACHEABLE_TOOLS else 0.0
        return rpc_id, name or method, ttl
    return rpc_id, method, 0.0


class TransportGuardMiddleware(BaseHTTPMiddleware):
    """Deduplicate, cache and admission-control MCP POST traffic."""

    def __init__(self, app: Any, *, path: str = "/mcp") -> None:
        super().__init__(app)
        self._path = path
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(MAX_INFLIGHT)
        self._inflight = 0

    # ------------------------------------------------------------- internals

    def _prune(self) -> None:
        if len(self._entries) <= CACHE_MAX_ENTRIES:
            stale = [key for key, entry in self._entries.items() if entry.ready and entry.expired]
        else:
            stale = sorted(
                (key for key, entry in self._entries.items() if entry.ready),
                key=lambda key: self._entries[key].created,
            )[: max(1, len(self._entries) - CACHE_MAX_ENTRIES)]
        for key in stale:
            self._entries.pop(key, None)

    @staticmethod
    def _key(request: Any, body: bytes, rpc_id: str) -> str:
        """Cache key.

        The credential is part of the key. Without it, a cached success from an
        authorised caller could be replayed to an unauthorised one, turning a
        performance cache into an authentication bypass. The credential is
        hashed, never stored. Caught by the OAuth suite when this was missing.
        """
        session = request.headers.get("mcp-session-id", "")
        credential = "|".join(
            request.headers.get(name, "")
            for name in ("authorization", "x-api-key", "x-mcp-token")
        )
        identity = hashlib.sha256(credential.encode("utf-8", errors="replace")).hexdigest()[:16]
        digest = hashlib.sha256(body).hexdigest()
        return f"{session}|{identity}|{rpc_id}|{digest}"

    @staticmethod
    def _replay(entry: _Entry, *, reason: str) -> Response:
        headers = dict(entry.headers)
        headers.pop("content-length", None)
        headers.pop("content-encoding", None)
        headers["x-mcp-cache"] = reason
        return Response(content=entry.body, status_code=entry.status, headers=headers)

    def _overloaded(self) -> JSONResponse:
        STATS.rejected += 1
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32000,
                    "message": (
                        "The server is busy and refused this request instead of "
                        "queueing it invisibly. Retry shortly."
                    ),
                    "data": {"retryAfterSeconds": RETRY_AFTER_SECONDS},
                },
                "id": None,
            },
            status_code=503,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS), "x-mcp-cache": "rejected"},
        )

    # ---------------------------------------------------------------- public

    async def dispatch(self, request: Any, call_next: Any) -> Response:
        if request.method != "POST" or not request.url.path.rstrip("/").endswith(
            self._path.rstrip("/")
        ):
            return await call_next(request)

        body = await request.body()
        if not body:
            return await call_next(request)

        rpc_id, label, ttl = describe_request(body)
        dedup_ttl = float(DEDUP_TTL_SECONDS)
        cache_ttl = max(ttl, dedup_ttl if rpc_id else 0.0)
        if cache_ttl <= 0:
            return await self._guarded_call(request, call_next)

        key = self._key(request, body, rpc_id)

        async with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is not None and not entry.expired:
                if entry.ready:
                    entry.hits += 1
                    reason = "hit" if ttl > 0 else "replay"
                    if ttl > 0:
                        STATS.cached += 1
                    else:
                        STATS.replayed += 1
                    return self._replay(entry, reason=reason)
                # An identical request is still running. Wait for its answer
                # instead of starting the same work a second time.
                waiter = entry
            else:
                waiter = None
                entry = _Entry(created=time.monotonic(), ttl=cache_ttl, event=asyncio.Event())
                self._entries[key] = entry

        if waiter is not None:
            assert waiter.event is not None
            try:
                await asyncio.wait_for(waiter.event.wait(), timeout=ADMISSION_WAIT_SECONDS)
            except asyncio.TimeoutError:
                return self._overloaded()
            STATS.joined += 1
            return self._replay(waiter, reason="joined")

        try:
            response = await self._guarded_call(request, call_next)
            payload = await _read_response_body(response)
            cacheable = (
                200 <= response.status_code < 300
                and len(payload) <= CACHE_MAX_BODY_BYTES
            )
            entry.status = response.status_code
            entry.headers = {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in {"content-length", "content-encoding"}
            }
            entry.body = payload
            if not cacheable:
                # Drop it outright. Leaving a zero-ttl entry behind is a trap:
                # a same-instant retry can still land on it before the clock
                # moves, and would then be replayed.
                entry.ttl = 0.0
                async with self._lock:
                    self._entries.pop(key, None)
            return Response(
                content=payload,
                status_code=response.status_code,
                headers=entry.headers,
            )
        except Exception:
            async with self._lock:
                self._entries.pop(key, None)
            raise
        finally:
            if entry.event is not None:
                entry.event.set()

    async def _guarded_call(self, request: Any, call_next: Any) -> Response:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=ADMISSION_WAIT_SECONDS)
        except asyncio.TimeoutError:
            return self._overloaded()
        self._inflight += 1
        STATS.peak_inflight = max(STATS.peak_inflight, self._inflight)
        try:
            response = await call_next(request)
            STATS.served += 1
            return response
        finally:
            self._inflight -= 1
            self._semaphore.release()


async def _read_response_body(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        return b""
    async for chunk in iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    return b"".join(chunks)


def stats() -> dict[str, Any]:
    return STATS.snapshot()


__all__ = [
    "ADMISSION_WAIT_SECONDS",
    "CACHEABLE_METHODS",
    "CACHEABLE_TOOLS",
    "CACHE_MAX_BODY_BYTES",
    "CACHE_MAX_ENTRIES",
    "DEDUP_TTL_SECONDS",
    "MAX_INFLIGHT",
    "READ_CACHE_TTL_SECONDS",
    "RETRY_AFTER_SECONDS",
    "TransportGuardMiddleware",
    "describe_request",
    "stats",
]
