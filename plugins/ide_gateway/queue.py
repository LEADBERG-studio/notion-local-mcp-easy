"""File-based request queue bridging IDE HTTP requests to the active MCP model.

Ported and extended from plugins/ide_provider/queue.py. The queue stores every
OpenAI-shaped request type (chat completions, responses, images, audio, files,
embeddings, moderations, tools) as a JSON file under
<runtime>/queues/<endpoint>/<request_id>.json. The active MCP model claims a
pending request via ide_gateway_wait_request and completes it via
ide_gateway_send_response / ide_gateway_fail_request.

Responses may be:
  - plain text content (chat/responses/transcription) -> {response: {content, finish_reason}}
  - structured payload (images/embeddings/moderations) -> {response: {payload: <obj>}}
  - streamed content (chat stream) -> handled by the worker via poll-diff; the
    model may set response.stream_chunks to a list of strings for token-like
    emulation, otherwise the whole content is flushed in one delta.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"


class FileLock:
    """Windows-compatible exclusive lock using O_CREAT|O_EXCL."""

    def __init__(self, path: Path, timeout: float = 10.0, stale_timeout: float = 30.0):
        self.path = Path(path)
        self.timeout = timeout
        self.stale_timeout = stale_timeout
        self.fd: int | None = None

    def __enter__(self) -> "FileLock":
        deadline = time.time() + self.timeout
        while True:
            self._maybe_clear_stale()
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("utf-8"))
                return self
            except FileExistsError:
                if time.time() > deadline:
                    raise TimeoutError(f"lock timeout: {self.path}") from None
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def _maybe_clear_stale(self) -> None:
        try:
            if self.path.exists():
                mtime = self.path.stat().st_mtime
                if time.time() - mtime > self.stale_timeout:
                    with contextlib.suppress(OSError):
                        self.path.unlink()
        except OSError:
            pass


def _new_request_id() -> str:
    return "req_" + secrets.token_urlsafe(12)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    last_error: OSError | None = None
    for _ in range(80):
        try:
            os.replace(str(tmp), str(path))
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    with contextlib.suppress(OSError):
        tmp.unlink()
    if last_error is not None:
        raise last_error
    raise OSError(f"failed to replace {path}")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def queue_dir(runtime_root: Path, endpoint: str) -> Path:
    return Path(runtime_root) / "queues" / endpoint


def request_path(runtime_root: Path, endpoint: str, request_id: str) -> Path:
    return queue_dir(runtime_root, endpoint) / f"{request_id}.json"


def _find_request_path(runtime_root: Path, request_id: str) -> Path | None:
    queues = Path(runtime_root) / "queues"
    if not queues.is_dir():
        return None
    for endpoint_dir in queues.iterdir():
        if endpoint_dir.is_dir():
            candidate = endpoint_dir / f"{request_id}.json"
            if candidate.is_file():
                return candidate
    return None


def enqueue_request(
    runtime_root: Path,
    endpoint: str,
    request_data: dict[str, Any],
    *,
    timeout_seconds: int,
    max_pending: int,
) -> str:
    """Create a new pending request file and return its id."""
    qdir = queue_dir(runtime_root, endpoint)
    qdir.mkdir(parents=True, exist_ok=True)

    pending = 0
    for file in qdir.glob("*.json"):
        try:
            data = _read_json(file)
            if not data:
                continue
            if data.get("status") == STATUS_PENDING:
                if data.get("expires_at") and data["expires_at"] < time.time():
                    data["status"] = STATUS_EXPIRED
                    _atomic_write_json(file, data)
                else:
                    pending += 1
        except Exception:
            continue

    if pending >= max_pending:
        raise ValueError("queue full")

    request_id = _new_request_id()
    now = time.time()
    request: dict[str, Any] = {
        "request_id": request_id,
        "endpoint": endpoint,
        "status": STATUS_PENDING,
        "created_at": datetime.datetime.now().isoformat(),
        "claimed_at": None,
        "completed_at": None,
        "expires_at": now + timeout_seconds,
        # Common fields
        "kind": request_data.get("kind", "chat"),
        "method": request_data.get("method", "POST"),
        "path": request_data.get("path", "/v1/chat/completions"),
        "model": request_data.get("model"),
        "messages": request_data.get("messages"),
        "prompt": request_data.get("prompt"),
        "instructions": request_data.get("instructions"),
        "temperature": request_data.get("temperature"),
        "top_p": request_data.get("top_p"),
        "max_tokens": request_data.get("max_tokens"),
        "stream": request_data.get("stream", False),
        "stream_options": request_data.get("stream_options"),
        "tools": request_data.get("tools"),
        "tool_choice": request_data.get("tool_choice"),
        "previous_response_id": request_data.get("previous_response_id"),
        "background": request_data.get("background", False),
        # Media
        "prompt_text": request_data.get("prompt_text"),
        "voice": request_data.get("voice"),
        "response_format": request_data.get("response_format"),
        "n": request_data.get("n"),
        # Embeddings/moderations raw input
        "input": request_data.get("input"),
        # Files
        "file_name": request_data.get("file_name"),
        "file_purpose": request_data.get("file_purpose"),
        "file_id": request_data.get("file_id"),
        # Raw body preserved for the model
        "raw_request": request_data.get("raw_request", {}),
        "response": None,
        "error": None,
    }
    _atomic_write_json(request_path(runtime_root, endpoint, request_id), request)
    return request_id


def claim_next_request(
    runtime_root: Path,
    endpoint: str,
    wait_timeout: int,
    *,
    request_timeout: int = 300,
) -> dict[str, Any] | None:
    """Wait up to wait_timeout seconds for a pending request and atomically claim it."""
    qdir = queue_dir(runtime_root, endpoint)
    qdir.mkdir(parents=True, exist_ok=True)

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        files = sorted(qdir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for file in files:
            try:
                req = _read_json(file)
                if not req:
                    continue
                if req.get("status") == STATUS_PENDING:
                    if req.get("expires_at") and req["expires_at"] < time.time():
                        req["status"] = STATUS_EXPIRED
                        _atomic_write_json(file, req)
                        continue
                    lock_path = file.with_suffix(".lock")
                    try:
                        with FileLock(lock_path, timeout=1.0):
                            req = _read_json(file)
                            if not req or req.get("status") != STATUS_PENDING:
                                continue
                            if req.get("expires_at") and req["expires_at"] < time.time():
                                req["status"] = STATUS_EXPIRED
                                _atomic_write_json(file, req)
                                continue
                            req["status"] = STATUS_CLAIMED
                            req["claimed_at"] = datetime.datetime.now().isoformat()
                            req["expires_at"] = time.time() + request_timeout
                            _atomic_write_json(file, req)
                            return req
                    except TimeoutError:
                        continue
                elif req.get("status") == STATUS_CLAIMED:
                    if req.get("expires_at") and req["expires_at"] < time.time():
                        req["status"] = STATUS_EXPIRED
                        _atomic_write_json(file, req)
                        continue
            except Exception:
                continue
        time.sleep(0.25)
    return None


def complete_request(
    runtime_root: Path,
    request_id: str,
    content: str | None,
    *,
    payload: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    stream_chunks: list[str] | None = None,
    max_response_bytes: int,
) -> dict[str, Any]:
    """Mark a claimed request as completed.

    content: plain text answer (chat/responses/transcription).
    payload: structured OpenAI-shaped answer (images/embeddings/moderations).
    stream_chunks: optional token-like chunks for streamed replies; if absent,
        the worker flushes `content` as a single delta.
    """
    if content is not None and len(content.encode("utf-8")) > max_response_bytes:
        return {"ok": False, "status": "too_large", "message": "Response exceeds max_response_bytes"}

    path = _find_request_path(runtime_root, request_id)
    if not path:
        return {"ok": False, "status": "not_found", "message": f"Request {request_id} not found"}

    req = _read_json(path)
    if not req:
        return {"ok": False, "status": "not_found", "message": f"Request {request_id} not found"}
    if req.get("status") != STATUS_CLAIMED:
        return {"ok": False, "status": "not_claimable", "current": req.get("status"),
                "message": f"Request is {req.get('status')}, not claimed"}

    response: dict[str, Any] = {"finish_reason": finish_reason or "stop"}
    if content is not None:
        response["content"] = content
    if payload is not None:
        response["payload"] = payload
    if stream_chunks is not None:
        response["stream_chunks"] = stream_chunks

    req["status"] = STATUS_COMPLETED
    req["completed_at"] = datetime.datetime.now().isoformat()
    req["response"] = response
    _atomic_write_json(path, req)
    return {"ok": True, "request_id": request_id, "status": STATUS_COMPLETED}


def fail_request_by_id(
    runtime_root: Path,
    request_id: str,
    error_code: str | None,
    message: str,
) -> dict[str, Any]:
    path = _find_request_path(runtime_root, request_id)
    if not path:
        return {"ok": False, "status": "not_found", "message": f"Request {request_id} not found"}

    req = _read_json(path)
    if not req:
        return {"ok": False, "status": "not_found", "message": f"Request {request_id} not found"}
    if req.get("status") not in (STATUS_PENDING, STATUS_CLAIMED):
        return {"ok": False, "status": "not_claimable", "current": req.get("status"),
                "message": f"Request is {req.get('status')}, cannot be failed"}

    req["status"] = STATUS_FAILED
    req["completed_at"] = datetime.datetime.now().isoformat()
    req["error"] = {"code": error_code or "failed", "message": message}
    _atomic_write_json(path, req)
    return {"ok": True, "request_id": request_id, "status": STATUS_FAILED}


def wait_for_completion(
    runtime_root: Path,
    endpoint: str,
    request_id: str,
    timeout: int,
) -> dict[str, Any]:
    """Poll a request file until it is completed, failed, or timeout elapses."""
    path = request_path(runtime_root, endpoint, request_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = _read_json(path)
        if not req:
            return {"status": "timeout"}
        status = req.get("status")
        if status == STATUS_COMPLETED:
            return {"status": STATUS_COMPLETED, "request": req}
        if status == STATUS_FAILED:
            return {"status": STATUS_FAILED, "request": req}
        if status == STATUS_EXPIRED:
            return {"status": "timeout"}
        time.sleep(0.25)

    req = _read_json(path)
    if req and req.get("status") in (STATUS_PENDING, STATUS_CLAIMED):
        req["status"] = STATUS_EXPIRED
        _atomic_write_json(path, req)
    return {"status": "timeout"}


def request_counts(runtime_root: Path, endpoint: str) -> dict[str, int]:
    qdir = queue_dir(runtime_root, endpoint)
    counts = {STATUS_PENDING: 0, STATUS_CLAIMED: 0, STATUS_COMPLETED: 0,
              STATUS_FAILED: 0, STATUS_EXPIRED: 0}
    if not qdir.is_dir():
        return counts
    for file in qdir.glob("*.json"):
        try:
            req = _read_json(file)
            if req and req.get("status") in counts:
                counts[req["status"]] += 1
        except Exception:
            continue
    return counts


def clean_queue(runtime_root: Path, endpoint: str) -> None:
    qdir = queue_dir(runtime_root, endpoint)
    if qdir.is_dir():
        for item in qdir.iterdir():
            try:
                item.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Response / files registries (in-memory equivalent of hyperagent state.py)   #
# The worker persists responses and files under <runtime>/registry/ so the   #
# Responses API (previous_response_id, get, cancel, input_items) and the    #
# Files API (list, get, content, delete) survive across worker calls.       #
# --------------------------------------------------------------------------- #
def registry_root(runtime_root: Path) -> Path:
    p = Path(runtime_root) / "registry"
    p.mkdir(parents=True, exist_ok=True)
    (p / "responses").mkdir(parents=True, exist_ok=True)
    (p / "files").mkdir(parents=True, exist_ok=True)
    return p


def put_response(runtime_root: Path, rid: str, model: str, status: str,
                 meta: dict[str, Any] | None = None) -> None:
    p = registry_root(runtime_root) / "responses" / f"{rid}.json"
    data = {"id": rid, "model": model, "status": status, "meta": meta or {}}
    _atomic_write_json(p, data)


def get_response(runtime_root: Path, rid: str) -> dict[str, Any] | None:
    p = registry_root(runtime_root) / "responses" / f"{rid}.json"
    return _read_json(p, None)


def put_file(runtime_root: Path, fid: str, filename: str, nbytes: int, purpose: str,
             created: int = 0, content: bytes | None = None) -> None:
    reg = registry_root(runtime_root)
    meta_path = reg / "files" / f"{fid}.json"
    data_path = reg / "files" / f"{fid}.bin"
    _atomic_write_json(meta_path, {"id": fid, "filename": filename, "bytes": nbytes,
                                   "purpose": purpose, "created": created or int(time.time())})
    if content is not None:
        data_path.write_bytes(content)


def get_file(runtime_root: Path, fid: str) -> dict[str, Any] | None:
    p = registry_root(runtime_root) / "files" / f"{fid}.json"
    return _read_json(p, None)


def get_file_content(runtime_root: Path, fid: str) -> bytes | None:
    p = registry_root(runtime_root) / "files" / f"{fid}.bin"
    if p.is_file():
        return p.read_bytes()
    return None


def list_files(runtime_root: Path) -> list[dict[str, Any]]:
    d = registry_root(runtime_root) / "files"
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for meta in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        rec = _read_json(meta, None)
        if rec:
            out.append(rec)
    return out


def delete_file(runtime_root: Path, fid: str) -> bool:
    reg = registry_root(runtime_root) / "files"
    meta = reg / f"{fid}.json"
    data = reg / f"{fid}.bin"
    ok = False
    if meta.is_file():
        meta.unlink()
        ok = True
    if data.is_file():
        data.unlink()
        ok = True
    return ok


# --------------------------------------------------------------------------- #
# Tool entrypoints (called from plugin.invoke via state.runtime_root)         #
# --------------------------------------------------------------------------- #
def wait_request(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    from plugins.ide_gateway.state import normalize_name, runtime_root

    endpoint = normalize_name(arguments.get("name"))
    wait_timeout = int(arguments.get("timeout_seconds") or config["wait_timeout_seconds"])
    max_wait = int(config["wait_timeout_seconds"])
    if wait_timeout > max_wait:
        wait_timeout = max_wait
    if wait_timeout < 1:
        wait_timeout = 1

    root = runtime_root(context)
    request_timeout = int(config["request_timeout_seconds"])
    req = claim_next_request(root, endpoint, wait_timeout, request_timeout=request_timeout)
    if req is None:
        return {"ok": False, "status": "timeout", "message": "No IDE request received before timeout."}

    return {
        "ok": True,
        "endpoint": endpoint,
        "request_id": req["request_id"],
        "kind": req.get("kind", "chat"),
        "path": req.get("path"),
        "model": req.get("model"),
        "messages": req.get("messages"),
        "prompt": req.get("prompt"),
        "instructions": req.get("instructions"),
        "temperature": req.get("temperature"),
        "top_p": req.get("top_p"),
        "max_tokens": req.get("max_tokens"),
        "stream": req.get("stream", False),
        "stream_options": req.get("stream_options"),
        "tools": req.get("tools"),
        "tool_choice": req.get("tool_choice"),
        "previous_response_id": req.get("previous_response_id"),
        "background": req.get("background", False),
        "prompt_text": req.get("prompt_text"),
        "voice": req.get("voice"),
        "response_format": req.get("response_format"),
        "n": req.get("n"),
        "input": req.get("input"),
        "file_name": req.get("file_name"),
        "file_purpose": req.get("file_purpose"),
        "file_id": req.get("file_id"),
        "raw_request": req.get("raw_request", {}),
        "instructions_hint": (
            "Handle this IDE request using MCP tools if needed, then call "
            "ide_gateway_send_response (with content for text answers, or payload "
            "for images/embeddings/moderations) or ide_gateway_fail_request."
        ),
    }


def send_response(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    from plugins.ide_gateway.state import runtime_root

    request_id = str(arguments["request_id"])
    content = arguments.get("content")
    payload = arguments.get("payload")
    finish_reason = arguments.get("finish_reason") or None
    stream_chunks = arguments.get("stream_chunks")

    if content is not None and not isinstance(content, str):
        content = str(content)
    if payload is not None and not isinstance(payload, dict):
        payload = None
    if stream_chunks is not None and not isinstance(stream_chunks, list):
        stream_chunks = None

    return complete_request(
        runtime_root(context),
        request_id,
        content,
        payload=payload,
        finish_reason=finish_reason,
        stream_chunks=stream_chunks,
        max_response_bytes=int(config["max_response_bytes"]),
    )


def fail_request(arguments: dict[str, Any], context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    from plugins.ide_gateway.state import runtime_root

    request_id = str(arguments["request_id"])
    message = str(arguments["message"])
    error_code = arguments.get("error_code") or None
    return fail_request_by_id(runtime_root(context), request_id, error_code, message)
