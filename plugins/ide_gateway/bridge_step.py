"""IDE Gateway bridge helper for PromptQL / Notion Agent.

The active model in chat cannot call MCP tools directly as tool-calls; it can
only run programs via run_program. This script bridges that gap by working
with the ide_gateway queue files directly (no HTTP, no recursion).

Usage:

  # Phase 1: poll for the next IDE request (blocks up to --timeout seconds)
  python bridge_step.py poll --timeout 30

  # Phase 2a: complete the request with a text answer
  python bridge_step.py complete --request-id req_XXX --content "answer text"

  # Phase 2b: complete with structured payload (images/tool_calls/...)
  python bridge_step.py complete --request-id req_XXX --payload '{"url":"..."}'

  # Phase 2c: fail the request with an error
  python bridge_step.py fail --request-id req_XXX --message "reason"

  # Status check
  python bridge_step.py status

The script reads the endpoint state file to find the runtime root and endpoint
name, then operates on the queue files under
<workspace>/temp/ide_gateway_runtime/queues/<endpoint>/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.ide_gateway.queue import (
    claim_next_request,
    complete_request,
    fail_request_by_id,
    request_counts,
)


def _find_runtime_and_endpoint() -> tuple[Path, str]:
    """Locate the ide_gateway runtime root and default endpoint name by reading
    the endpoint state files."""
    workspace = os.environ.get("MCP_BASE_DIR") or str(Path.home() / "Documents")
    runtime = Path(workspace) / "temp" / "ide_gateway_runtime"
    endpoints_index = runtime / "endpoints.json"
    endpoint = "default"
    if endpoints_index.is_file():
        try:
            data = json.loads(endpoints_index.read_text(encoding="utf-8"))
            names = [k for k, v in data.items() if v]
            if names:
                endpoint = names[0]
        except Exception:
            pass
    # Fallback: scan endpoint state files
    if not runtime.is_dir():
        # Try workspace-relative
        runtime = _REPO_ROOT / "temp" / "ide_gateway_runtime"
    return runtime, endpoint


def _parse_content_arg(args) -> str | None:
    """Read --content from arg, or from stdin if '-'."""
    if args.content is None:
        return None
    if args.content == "-":
        return sys.stdin.read()
    return args.content


def _parse_payload_arg(args) -> dict | None:
    if not args.payload:
        return None
    if args.payload == "-":
        raw = sys.stdin.read()
    else:
        raw = args.payload
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def cmd_poll(args) -> int:
    runtime, endpoint = _find_runtime_and_endpoint()
    timeout = max(1, min(args.timeout, 120))
    req = claim_next_request(runtime, endpoint, timeout, request_timeout=300)
    if req is None:
        print(json.dumps({"ok": False, "status": "timeout",
                          "message": f"No IDE request received in {timeout}s. Run poll again."}))
        return 0
    print(json.dumps({
        "ok": True,
        "status": "claimed",
        "request_id": req["request_id"],
        "kind": req.get("kind", "chat"),
        "path": req.get("path"),
        "model": req.get("model"),
        "stream": req.get("stream", False),
        "messages": req.get("messages"),
        "prompt": req.get("prompt"),
        "instructions": req.get("instructions"),
        "temperature": req.get("temperature"),
        "top_p": req.get("top_p"),
        "max_tokens": req.get("max_tokens"),
        "tools": req.get("tools"),
        "tool_choice": req.get("tool_choice"),
        "input": req.get("input"),
        "prompt_text": req.get("prompt_text"),
        "voice": req.get("voice"),
        "raw_request": req.get("raw_request", {}),
    }, ensure_ascii=False))
    return 0


def cmd_complete(args) -> int:
    runtime, endpoint = _find_runtime_and_endpoint()
    content = _parse_content_arg(args)
    payload = _parse_payload_arg(args)
    result = complete_request(
        runtime, args.request_id, content,
        payload=payload,
        finish_reason=args.finish_reason,
        max_response_bytes=4 * 1024 * 1024,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_fail(args) -> int:
    runtime, endpoint = _find_runtime_and_endpoint()
    result = fail_request_by_id(runtime, args.request_id, args.error_code, args.message)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_status(args) -> int:
    runtime, endpoint = _find_runtime_and_endpoint()
    counts = request_counts(runtime, endpoint)
    print(json.dumps({"ok": True, "endpoint": endpoint, "counts": counts}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="IDE Gateway bridge step helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_poll = sub.add_parser("poll", help="Wait for the next IDE request")
    p_poll.add_argument("--timeout", type=int, default=30, help="Seconds to wait (1-120)")

    p_complete = sub.add_parser("complete", help="Complete a claimed request")
    p_complete.add_argument("--request-id", required=True)
    p_complete.add_argument("--content", default=None,
                            help="Text answer (use - to read from stdin)")
    p_complete.add_argument("--payload", default=None,
                            help="Structured JSON payload (use - for stdin)")
    p_complete.add_argument("--finish-reason", default="stop")

    p_fail = sub.add_parser("fail", help="Fail a claimed request")
    p_fail.add_argument("--request-id", required=True)
    p_fail.add_argument("--message", required=True)
    p_fail.add_argument("--error-code", default="failed")

    p_status = sub.add_parser("status", help="Show queue counters")

    args = parser.parse_args()
    if args.command == "poll":
        return cmd_poll(args)
    if args.command == "complete":
        return cmd_complete(args)
    if args.command == "fail":
        return cmd_fail(args)
    if args.command == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
