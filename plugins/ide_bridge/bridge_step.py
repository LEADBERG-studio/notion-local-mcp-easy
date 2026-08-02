"""IDE Bridge bridge helper for PromptQL / Notion Agent.

The active model in chat cannot call MCP tools directly as tool-calls; it can
only run programs via run_program. This script bridges that gap by working
with the ide_bridge queue files directly (no HTTP, no recursion).

Usage:

  # Step 1: poll for the next IDE request (blocks up to --timeout seconds)
  python bridge_step.py poll --timeout 3

  # Step 2a: complete the request with a text answer
  python bridge_step.py complete --request-id req_XXX --content "answer text"

  # Step 2b: complete with structured payload (images/tool_calls/...)
  python bridge_step.py complete --request-id req_XXX --payload '{"url":"..."}'

  # Step 2c: fail the request with an error
  python bridge_step.py fail --request-id req_XXX --message "reason"

  # Status check
  python bridge_step.py status

The script reads the endpoint state file to find the runtime root and endpoint
name, then operates on the queue files under
<workspace>/temp/ide_bridge_runtime/queues/<endpoint>/.

poll output includes classified fields to help the model decide what to do:
  - prompt_type: "chat" (user question) | "memory_extraction" (PromptQL sync) | "other"
  - user_message: the extracted last user message (without preamble/system text)
  - prompt_tail: last 3000 chars of the full prompt for context
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.ide_bridge.queue import (
    claim_next_request,
    complete_request,
    fail_request_by_id,
    request_counts,
)


def _find_runtime_and_endpoint() -> tuple[Path, str]:
    """Locate the ide_bridge runtime root and default endpoint name by reading
    the endpoint state files."""
    workspace = os.environ.get("MCP_BASE_DIR") or str(Path.home() / "Documents")
    runtime = Path(workspace) / "temp" / "ide_bridge_runtime"
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
        runtime = _REPO_ROOT / "temp" / "ide_bridge_runtime"
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


# --------------------------------------------------------------------------- #
# Prompt classification
# --------------------------------------------------------------------------- #
_USER_MESSAGE_RE = re.compile(
    r"(?:\[user\]|User:|\"role\":\s*\"user\")\s*[:\s]*\s*(.+?)(?=\n\s*(?:\[(?:user|assistant|system|tool)\]|User:|Assistant:|System:|Tool:|\"role\":)|$)",
    re.IGNORECASE | re.DOTALL,
)


def classify_prompt(prompt: str, messages: list | None) -> dict:
    """Extract the last real user message from a flattened prompt and classify
    the request type.

    Returns {prompt_type, user_message, prompt_tail}.

    prompt_type:
      - "memory_extraction": the IDE/PromptQL is syncing memory (SubmitMemoryPlan,
        SubmitContextQuery, etc.) — answer with a noop.
      - "chat": a real user question from the IDE.
      - "other": system/developer preamble without a clear user message.
    """
    full_prompt = prompt or ""
    # Try to extract the last user message from messages first (more reliable).
    user_message = ""
    if messages:
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        str(p.get("text", "")) for p in content
                        if isinstance(p, dict) and p.get("type") in (None, "text", "input_text", "output_text")
                    )
                if content:
                    user_message = str(content).strip()
                    break

    # Fallback: extract from flattened prompt using regex.
    if not user_message and full_prompt:
        matches = _USER_MESSAGE_RE.findall(full_prompt)
        if matches:
            user_message = matches[-1].strip()

    # Classify
    low = (user_message or full_prompt).lower()
    if any(kw in low for kw in ("submitmemoryplan", "submitcontextquery",
                                 "memory_extraction", "memoryextraction",
                                 "submitmemory", "memoryplan")):
        prompt_type = "memory_extraction"
    elif user_message:
        prompt_type = "chat"
    else:
        prompt_type = "other"

    return {
        "prompt_type": prompt_type,
        "user_message": user_message,
        "prompt_tail": full_prompt[-3000:] if full_prompt else "",
    }


def cmd_poll(args) -> int:
    runtime, endpoint = _find_runtime_and_endpoint()
    # Default timeout is 3s to avoid 502 proxy timeout on Notion Agent's
    # run_program layer. The model calls poll repeatedly in short iterations.
    timeout = max(1, min(args.timeout, 120))
    req = claim_next_request(runtime, endpoint, timeout, request_timeout=300)
    if req is None:
        print(json.dumps({"ok": False, "status": "timeout",
                          "message": f"No IDE request received in {timeout}s. Run poll again."}))
        return 0

    prompt = req.get("prompt") or ""
    messages = req.get("messages")
    classified = classify_prompt(prompt, messages)

    # Full prompt without truncation (model requested no clipping).
    output = {
        "ok": True,
        "status": "claimed",
        "request_id": req["request_id"],
        "kind": req.get("kind", "chat"),
        "path": req.get("path"),
        "model": req.get("model"),
        "stream": req.get("stream", False),
        "messages": messages,
        "prompt": prompt,
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
        # Classification fields (model uses these to decide what to do).
        "prompt_type": classified["prompt_type"],
        "user_message": classified["user_message"],
        "prompt_tail": classified["prompt_tail"],
    }
    print(json.dumps(output, ensure_ascii=False))
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
    parser = argparse.ArgumentParser(description="IDE Bridge bridge step helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_poll = sub.add_parser("poll", help="Wait for the next IDE request")
    # Default 3s: avoids 502 proxy timeout on Notion Agent's run_program layer.
    p_poll.add_argument("--timeout", type=int, default=3, help="Seconds to wait (1-120, default 3)")

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
