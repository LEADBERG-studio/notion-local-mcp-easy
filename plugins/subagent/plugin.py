"""Remote model subagent.

Lets the model in this chat talk to another model through the tunnel, for prompt
testing, tool testing, or delegating work.

Two rules shape the whole plugin:

1. **Nothing about the connection is ever returned.** Endpoint, key and model id
   live in the local plugin config and never appear in a tool result, an error
   message, or a log line. The calling model gets answers, not credentials. Even
   the model name is optional to expose, because a name is a fingerprint.
2. **Every answer is bounded.** A remote model can produce an unbounded reply,
   and an unbounded reply is both a transport risk and a token bill. Replies are
   capped, sessions keep a rolling window, and the caller is told when something
   was trimmed.

Sessions exist so prompt iteration is cheap: start once, send several turns, and
the history travels on the remote side of the conversation instead of being
resent by the caller every time.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# Bounded by default. These are transport and cost guards, not opinions about
# what the remote model should say.
DEFAULT_REPLY_CHARS = 8_000
MAX_REPLY_CHARS = 40_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
DEFAULT_HISTORY_TURNS = 12
MAX_SESSIONS = 8
SESSION_TTL_SECONDS = 60 * 60

# Session state is process-local on purpose: it holds prompt text, so it must not
# survive a restart or land on disk.
_SESSIONS: dict[str, dict[str, Any]] = {}


# ----------------------------------------------------------------- config


def _target(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve the single configured remote target."""
    base_url = str(config.get("base_url", "")).strip().rstrip("/")
    model = str(config.get("model", "")).strip()
    if not base_url or not model:
        raise ValueError(
            "Subagent plugin is not configured yet. Run plugins\\subagent\\SETUP.bat "
            "and provide base_url, model and a key."
        )
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("Subagent base_url must be a full http(s) URL.")
    api_key = str(config.get("api_key", "")).strip()
    api_key_env = str(config.get("api_key_env", "")).strip()
    if api_key_env and not api_key:
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                "The subagent key is missing. Its environment variable is not set on "
                "this machine."
            )
    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "expose_model": bool(config.get("expose_model", False)),
        "system_prompt": str(config.get("system_prompt", "")).strip(),
        "temperature": config.get("temperature"),
        "max_output_tokens": config.get("max_output_tokens"),
        "reply_char_limit": _bounded(
            config.get("reply_char_limit"), DEFAULT_REPLY_CHARS, MAX_REPLY_CHARS
        ),
        "timeout_seconds": _bounded(config.get("timeout_seconds"), DEFAULT_TIMEOUT, MAX_TIMEOUT),
        "history_turns": _bounded(config.get("history_turns"), DEFAULT_HISTORY_TURNS, 100),
    }


def _bounded(value: Any, default: int, ceiling: int) -> int:
    try:
        resolved = int(str(value).strip()) if str(value or "").strip() else default
    except (TypeError, ValueError):
        resolved = default
    return max(1, min(resolved, ceiling))


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    # Validate without raising when unconfigured: the plugin should load and say
    # so through its own tools rather than break server startup.
    if not str(config.get("base_url", "")).strip():
        return config
    _target(config)
    return config


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    try:
        target = _target(config)
    except ValueError as exc:
        return {"provider": "subagent", "configured": False, "detail": str(exc)}
    # Deliberately no endpoint, no key, no model unless explicitly allowed.
    return {
        "provider": "subagent",
        "configured": True,
        "keyPresent": bool(target["api_key"]),
        "model": target["model"] if target["expose_model"] else "<hidden>",
        "replyCharLimit": target["reply_char_limit"],
        "activeSessions": len(_live_sessions()),
    }


# ---------------------------------------------------------------- sessions


def _live_sessions() -> dict[str, dict[str, Any]]:
    now = time.time()
    for key, session in list(_SESSIONS.items()):
        if now - session["updated"] > SESSION_TTL_SECONDS:
            _SESSIONS.pop(key, None)
    return _SESSIONS


def _new_session(target: dict[str, Any], system_prompt: str) -> dict[str, Any]:
    sessions = _live_sessions()
    if len(sessions) >= MAX_SESSIONS:
        oldest = min(sessions, key=lambda key: sessions[key]["updated"])
        sessions.pop(oldest, None)
    session_id = "sub-" + uuid.uuid4().hex[:8]
    session = {
        "id": session_id,
        "system": system_prompt or target["system_prompt"],
        "turns": [],
        "created": time.time(),
        "updated": time.time(),
    }
    sessions[session_id] = session
    return session


def _session(session_id: str) -> dict[str, Any]:
    session = _live_sessions().get(str(session_id).strip())
    if session is None:
        raise ValueError(
            f"Unknown or expired subagent session: {session_id}. Start a new one with "
            "subagent_start."
        )
    return session


# ------------------------------------------------------------------- call


def _redact(text: str, target: dict[str, Any]) -> str:
    """Last line of defence: strip anything identifying from remote text."""
    cleaned = str(text or "")
    for secret in (target.get("api_key"), target.get("base_url")):
        if secret and str(secret) in cleaned:
            cleaned = cleaned.replace(str(secret), "<redacted>")
    if not target.get("expose_model") and target.get("model"):
        cleaned = cleaned.replace(str(target["model"]), "<model>")
    return cleaned


def _ask_remote(target: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": target["model"], "messages": messages}
    if target.get("temperature") is not None:
        try:
            payload["temperature"] = float(target["temperature"])
        except (TypeError, ValueError):
            pass
    if target.get("max_output_tokens"):
        try:
            payload["max_tokens"] = int(target["max_output_tokens"])
        except (TypeError, ValueError):
            pass
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if target["api_key"]:
        headers["Authorization"] = f"Bearer {target['api_key']}"
    request = urllib.request.Request(
        target["base_url"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=target["timeout_seconds"]) as response:
            raw = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        # Never echo the URL back: the error text would leak the endpoint.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # noqa: BLE001 - diagnostics only
            detail = ""
        if exc.code in {401, 403}:
            raise ValueError(
                "The remote model rejected the configured credentials. "
                "Re-run the subagent plugin setup."
            ) from exc
        raise ValueError(
            f"The remote model answered HTTP {exc.code}. {_redact(detail, target)}".strip()
        ) from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach the remote model: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ValueError(
            f"The remote model did not answer within {target['timeout_seconds']}s."
        ) from exc

    message = ((raw.get("choices") or [{}])[0].get("message") or {})
    content = message.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    content = _redact(content, target)
    limit = target["reply_char_limit"]
    result: dict[str, Any] = {"reply": content[:limit]}
    if len(content) > limit:
        result["truncated"] = True
        result["replyChars"] = len(content)
        result["hint"] = (
            "The reply was longer than the configured limit. Ask for a shorter answer "
            "or raise reply_char_limit in the plugin config."
        )
    usage = raw.get("usage")
    if isinstance(usage, dict) and usage:
        # Usage is the one remote detail worth surfacing: it is what costs money.
        result["usage"] = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if usage.get(key) is not None
        }
    if target["expose_model"]:
        result["model"] = target["model"]
    return result


def _build_messages(session: dict[str, Any] | None, system: str, prompt: str, turns: int) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if session is not None:
        for turn in session["turns"][-turns * 2 :]:
            messages.append(turn)
    messages.append({"role": "user", "content": prompt})
    return messages


# ------------------------------------------------------------------ tools


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}

    if tool_name == "subagent_status":
        status = healthcheck(context)
        status["sessions"] = [
            {
                "id": session["id"],
                "turns": len(session["turns"]) // 2,
                "ageSeconds": int(time.time() - session["created"]),
            }
            for session in _live_sessions().values()
        ]
        return status

    target = _target(config)

    if tool_name == "subagent_ask":
        prompt = str(arguments.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")
        system = str(arguments.get("system_prompt", "")).strip() or target["system_prompt"]
        return _ask_remote(target, _build_messages(None, system, prompt, 0))

    if tool_name == "subagent_start":
        system = str(arguments.get("system_prompt", "")).strip()
        session = _new_session(target, system)
        return {
            "session": session["id"],
            "note": "Send turns with subagent_say and close it with subagent_end.",
        }

    if tool_name == "subagent_say":
        session = _session(str(arguments.get("session", "")))
        prompt = str(arguments.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")
        messages = _build_messages(session, session["system"], prompt, target["history_turns"])
        result = _ask_remote(target, messages)
        session["turns"].append({"role": "user", "content": prompt})
        session["turns"].append({"role": "assistant", "content": result["reply"]})
        # Keep the rolling window bounded so a long session cannot grow without end.
        session["turns"] = session["turns"][-target["history_turns"] * 2 :]
        session["updated"] = time.time()
        result["session"] = session["id"]
        result["turns"] = len(session["turns"]) // 2
        return result

    if tool_name == "subagent_end":
        session_id = str(arguments.get("session", "")).strip()
        removed = _live_sessions().pop(session_id, None) is not None
        return {"session": session_id, "closed": removed}

    raise ValueError(f"Unknown subagent tool: {tool_name}")
