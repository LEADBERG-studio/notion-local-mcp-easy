"""Universal backend layer: discover models, call upstream, translate formats.

Supports three backend types:
  - bridge:    queue → model in chat (long-poll via bridge_step.py)
  - sandbox:   direct egress LLM call from a sandbox environment
  - external:  direct call to an OpenAI-compatible provider (Ollama, OpenAI, ...)

The sandbox and external backends share the same call_upstream logic; they differ
only in how the base URL and API key are discovered (env vars vs config).

Anthropic translation is included so models behind an Anthropic egress (Claude
family) can be served through the OpenAI-compatible surface without a separate
adapter.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Tunnellio API — domain provisioning at plugin setup time
# --------------------------------------------------------------------------- #
TUNNELLIO_API_BASE = "https://api.tunnellio.ru"
# Default token for free ephemeral domains (1-day lifetime).
# Users with a paid plan can use their own token for persistent/custom domains.
DEFAULT_TUNNELLIO_TOKEN = "tnl_OK1mxYApPxqTFhRi5K5EDtimMosumaC_"


def _tunnellio_post(path: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    """Call Tunnellio API and return the full parsed response.
    On HTTP error, returns {"ok": False, "error": {...}} with the body."""
    url = TUNNELLIO_API_BASE + "/v1" + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Read the error body so we can handle 409 (conflict) gracefully
        try:
            error_body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            error_body = {}
        return {"ok": False, "error": {
            "code": error_body.get("error", {}).get("code", str(exc.code)),
            "message": error_body.get("error", {}).get("message", str(exc)),
            "details": error_body,
            "http_status": exc.code,
        }}


def _find_existing_key(token: str, fingerprint: str = "") -> str | None:
    """Find an existing key by fingerprint, return key_id or None."""
    resp = _tunnellio_post("/keys/list", token, {})
    if not resp.get("ok"):
        return None
    keys = resp.get("data", {}).get("keys", [])
    for k in keys:
        if k.get("status") == "active" and (
            not fingerprint or k.get("fingerprint") == fingerprint
        ):
            return str(k["id"])
    return None


def provision_sandbox_domain(
    token: str = "",
    hostname: str = "",
    local_port: int = 8787,
) -> dict[str, Any]:
    """Provision a Tunnellio domain for the sandbox.

    If hostname is empty → ephemeral (random, 1-day, auto-deleted on disconnect).
    If hostname is set → persistent (requires paid plan token).

    Returns dict with: key_id, domain_id, public_url, ssh_host, ssh_port,
    ssh_user, remote_hostname, private_key, mode.

    Raises on error.
    """
    token = token or DEFAULT_TUNNELLIO_TOKEN

    # Generate a temporary SSH keypair for this domain
    key_dir = Path(tempfile.mkdtemp(prefix="ide_gateway_tnl_"))
    key_path = key_dir / "tunnel_key"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True, capture_output=True,
    )
    public_key = Path(str(key_path) + ".pub").read_text(encoding="utf-8").strip()
    private_key_content = Path(str(key_path)).read_text(encoding="utf-8").strip()
    private_key = str(key_path)

    # Get fingerprint of the generated key
    fp_result = subprocess.run(
        ["ssh-keygen", "-lf", str(key_path)],
        capture_output=True, text=True,
    )
    fingerprint = ""
    if fp_result.returncode == 0:
        parts = fp_result.stdout.strip().split()
        if len(parts) >= 2:
            fingerprint = parts[1]

    # Try to find an existing key by fingerprint (avoids 409 on duplicate keys)
    key_id = _find_existing_key(token, fingerprint)
    if not key_id:
        # Register a new SSH key
        key_resp = _tunnellio_post("/keys", token, {
            "name": f"ide-gateway-{'ephemeral' if not hostname else hostname}",
            "publicKey": public_key,
            "requestedLifetimeDays": 1 if not hostname else 365,
        })
        if not key_resp.get("ok"):
            # If 409 conflict (key already exists), try to find it by fingerprint
            if key_resp.get("error", {}).get("http_status") == 409:
                key_id = _find_existing_key(token, fingerprint)
                if not key_id:
                    # Try without fingerprint — any active key
                    key_id = _find_existing_key(token)
                    if not key_id:
                        raise RuntimeError(f"Tunnellio key registration failed (409) and no existing key found: {key_resp.get('error', {})}")
            else:
                raise RuntimeError(f"Tunnellio key registration failed: {key_resp.get('error', {})}")
        else:
            key_id = str(key_resp["data"]["key"]["id"])

    # Create domain
    if not hostname:
        # Ephemeral
        resp = _tunnellio_post("/sessions/ephemeral", token, {
            "keyId": key_id,
            "localHost": "127.0.0.1",
            "localPort": local_port,
            "note": "ide-gateway-sandbox",
        })
        if not resp.get("ok"):
            raise RuntimeError(f"Tunnellio ephemeral session failed: {resp.get('error', {})}")
        data = resp["data"]
        session = data.get("session", {})
        domain = data.get("domain", {})
        profile = data.get("connectionProfile", {})
        domain_id = domain.get("id", "")
        public_url = session.get("publicUrl") or domain.get("publicUrl", "")
        mode = "ephemeral"
    else:
        # Persistent
        check = _tunnellio_post("/domains/check", token, {"hostname": hostname})
        if not check.get("ok") or not check.get("data", {}).get("available"):
            raise RuntimeError(f"Hostname '{hostname}' is not available: {check.get('error', check)}")
        resp = _tunnellio_post("/domains", token, {
            "hostname": hostname,
            "keyId": key_id,
            "localPort": local_port,
            "note": "ide-gateway-sandbox",
            "requestedLifetimeDays": 365,
            "authMode": "legacy",
            "stableUrlRequired": True,
            "connectionMode": "direct",
        })
        if not resp.get("ok"):
            raise RuntimeError(f"Tunnellio domain creation failed: {resp.get('error', {})}")
        data = resp["data"]
        domain = data.get("domain", {})
        domain_id = domain.get("id", "")
        # Get connection profile
        cp_resp = _tunnellio_post("/domains/connection-profile", token, {
            "domainId": domain_id,
            "localHost": "127.0.0.1",
            "localPort": local_port,
        })
        if not cp_resp.get("ok"):
            raise RuntimeError(f"Tunnellio connection-profile failed: {cp_resp.get('error', {})}")
        profile = cp_resp["data"]["connectionProfile"]
        public_url = profile.get("publicUrl", domain.get("publicUrl", ""))
        mode = "persistent"

    return {
        "key_id": str(key_id),
        "domain_id": str(domain_id),
        "public_url": public_url,
        "ssh_host": profile.get("sshHost", ""),
        "ssh_port": str(profile.get("sshPort", 22)),
        "ssh_user": profile.get("sshUser", ""),
        "remote_hostname": profile.get("remoteHostname", ""),
        "private_key": private_key,
        "private_key_content": private_key_content,
        "mode": mode,
    }


def check_domain_status(token: str, domain_id: str) -> dict[str, Any] | None:
    """Check if a Tunnellio domain is still active.

    Returns the domain dict if active, None if expired/deleted.
    """
    if not domain_id:
        return None
    try:
        resp = _tunnellio_post("/domains/get", token or DEFAULT_TUNNELLIO_TOKEN, {
            "domainId": domain_id,
        })
        if not resp.get("ok"):
            return None
        domain = resp.get("data", {}).get("domain", {})
        status = str(domain.get("status", "")).lower()
        if status in ("active", "expired", "connecting", "reconnecting"):
            # "expired" status still means the domain record exists; the route
            # may be offline but the domain can be reconnected.
            return domain
        return None
    except Exception:
        return None


def ensure_domain(config: dict[str, Any], local_port: int = 8787) -> dict[str, Any]:
    """Prepare public URL hints for sandbox TCP bridge mode.

    The old implementation reserved a Tunnellio SSH domain and generated SSH
    keys ahead of time. TCP bridge mode is keyless: the sandbox-side
    `sandbox_tunnel.py` asks the bundled Tunnellio client to create or attach
    the bridge at runtime. If a custom hostname is configured we can predict the
    URL; if not, the bridge will generate an ephemeral hostname and write it back
    to the endpoint state after launch.
    """
    hostname = str(
        config.get("tunnellio_custom_hostname")
        or config.get("tunnellio_hostname")
        or ""
    ).strip()
    if not hostname:
        import secrets as _secrets
        hostname = "ide-gateway-" + _secrets.token_hex(4)

    config["tunnellio_hostname"] = hostname
    config["tunnellio_mode"] = "tcp_bridge"
    config["tunnellio_domain_id"] = ""
    config["tunnellio_key_id"] = ""
    config["tunnellio_private_key"] = ""
    config["tunnellio_private_key_content"] = ""
    config["tunnellio_ssh_host"] = ""
    config["tunnellio_ssh_port"] = ""
    config["tunnellio_ssh_user"] = ""
    config["tunnellio_remote_hostname"] = ""

    if hostname:
        public_url = f"https://{hostname}.tunnellio.site"
        config["tunnellio_public_url"] = public_url
        config["upstream_base_url"] = public_url.rstrip("/") + "/v1"
    else:
        config["tunnellio_public_url"] = ""
        config["upstream_base_url"] = ""
    return config


# --------------------------------------------------------------------------- #
# Model discovery
# --------------------------------------------------------------------------- #
def _split_model_names(raw: str) -> list[str]:
    names: list[str] = []
    for item in str(raw or "").replace("\n", ",").replace(";", ",").split(","):
        item = item.strip().strip('"\'')
        if item and item not in names:
            names.append(item)
    return names


def _model_env_candidates(config: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in (
        "IDE_GATEWAY_MODEL", "IDE_GATEWAY_EXTRA_MODELS", "OPENAI_MODEL",
        "OPENAI_MODELS", "MODEL", "MODELS", "DEFAULT_MODEL",
        "AVAILABLE_MODELS", "ACCIO_MODELS", "SANDBOX_MODELS",
        "ANTHROPIC_MODEL", "ANTHROPIC_MODELS",
    ):
        for name in _split_model_names(os.environ.get(key, "")):
            if name not in names:
                names.append(name)
    for name in _split_model_names(str(config.get("upstream_model", ""))):
        if name not in names:
            names.append(name)
    for name in _split_model_names(str(config.get("extra_models", ""))):
        if name not in names:
            names.append(name)
    return names


def _candidate_model_urls(base_url: str) -> list[str]:
    base = base_url.strip().rstrip("/")
    if not base:
        return []
    urls = [base + "/models"]
    if not base.endswith("/v1"):
        urls.append(base + "/v1/models")
    # Some sandbox gateways expose `/api/egress/openai/v1` while the env var
    # points one level higher. Trying both costs little and prevents stale
    # fallback model lists.
    if "/openai" not in base and not base.endswith("/v1"):
        urls.append(base + "/openai/v1/models")
    seen: list[str] = []
    for url in urls:
        if url not in seen:
            seen.append(url)
    return seen


def _append_model(models: list[dict[str, Any]], model_id: str, owner: str) -> None:
    model_id = str(model_id or "").strip()
    if not model_id or model_id in {m["id"] for m in models}:
        return
    models.append({"id": model_id, "object": "model", "created": 0, "owned_by": owner})


def discover_models(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Discover available models from the current sandbox, then fall back safely.

    The important rule: prefer what this sandbox actually exposes. Static names
    are only a last-resort compatibility net, not the advertised truth.
    """
    config = config or {}
    models: list[dict[str, Any]] = []

    # Explicit env/config model names win because some sandboxes intentionally
    # hide `/models` but still provide the active model list via env.
    for name in _model_env_candidates(config):
        _append_model(models, name, "sandbox-env")

    egress_configs = [
        (str(config.get("upstream_base_url", "")).strip(), str(config.get("upstream_api_key", "")).strip(), "config"),
        (os.environ.get("OPENAI_BASE_URL", "").strip(), os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("ACCIO_GATEWAY_TOKEN", "").strip(), "openai-egress"),
        (os.environ.get("ANTHROPIC_BASE_URL", "").strip(), os.environ.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("ACCIO_GATEWAY_TOKEN", "").strip(), "anthropic-egress"),
        (os.environ.get("ACCIO_GATEWAY_BASE_URL", "").strip(), os.environ.get("ACCIO_GATEWAY_TOKEN", "").strip(), "accio-egress"),
    ]

    for base_url, api_key, owner in egress_configs:
        if not base_url:
            continue
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        for url in _candidate_model_urls(base_url):
            try:
                before = len(models)
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8") or "{}")
                raw_models = data.get("data", data.get("models", []))
                if isinstance(raw_models, dict):
                    raw_models = raw_models.values()
                for m in raw_models:
                    if isinstance(m, dict):
                        mid = m.get("id") or m.get("name") or m.get("model")
                        _append_model(models, str(mid or ""), str(m.get("owned_by") or owner))
                    else:
                        _append_model(models, str(m), owner)
                if len(models) > before:
                    break
            except Exception:
                continue

    if not models:
        for name in _DEFAULT_MODELS:
            _append_model(models, name, "fallback")

    # Keep the generic alias available without hiding the real sandbox names.
    _append_model(models, "ide-gateway", "alias")
    return models


_DEFAULT_MODELS = [
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "claude-opus-4-8", "claude-fable-5", "claude-opus-4-7", "claude-sonnet-5",
    "claude-sonnet-4-6", "claude-haiku-4-5",
    "deepseek-v4-pro", "deepseek-v4-flash",
    "qwen3.7-plus", "qwen3.7-max",
    "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
    "grok-4.3", "glm-5.2", "glm-5.1", "hy3", "kimi-k3", "kimi-k2.7-code",
    "minimax-m3", "step-3.7-flash", "mimo-v2.5", "mimo-v2.5-pro",
    "openrouter-fusion",
    # Generic alias
    "ide-gateway",
]


# --------------------------------------------------------------------------- #
# Determine if a model is Anthropic-family (needs translation)
# --------------------------------------------------------------------------- #
_ANTHROPIC_PREFIXES = ("claude", "anthropic")


def is_anthropic_model(model: str) -> bool:
    return any(model.lower().startswith(p) for p in _ANTHROPIC_PREFIXES)


# --------------------------------------------------------------------------- #
# Anthropic translation (OpenAI ⇄ Anthropic Messages API)
# --------------------------------------------------------------------------- #
def openai_to_anthropic(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI chat-completion payload to an Anthropic Messages API payload."""
    messages: list[dict[str, Any]] = []
    system: str | None = None

    for msg in payload.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system = content if isinstance(content, str) else str(content)
            continue

        if isinstance(content, list):
            anthropic_content: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    anthropic_content.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {})
                    url = url.get("url") if isinstance(url, dict) else url
                    if isinstance(url, str) and url.startswith("data:"):
                        media_type = url.split(";")[0].replace("data:", "")
                        data = url.split(",", 1)[1] if "," in url else url
                        anthropic_content.append({
                            "type": "image", "source": {
                                "type": "base64", "media_type": media_type, "data": data}})
            messages.append({"role": role, "content": anthropic_content})
        else:
            messages.append({"role": role, "content": str(content)})

    result: dict[str, Any] = {
        "model": payload.get("model", ""),
        "messages": messages,
        "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 4096,
    }
    if system:
        result["system"] = system
    for key, anth_key in [("temperature", "temperature"), ("top_p", "top_p")]:
        if key in payload:
            result[anth_key] = payload[key]
    if "stop" in payload:
        stop = payload["stop"]
        result["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if payload.get("stream"):
        result["stream"] = True
    return result


def anthropic_to_openai(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert an Anthropic Messages API response to an OpenAI chat-completion."""
    content_parts: list[str] = []
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            content_parts.append(block.get("text", ""))

    stop_reason_map = {
        "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    finish = stop_reason_map.get(payload.get("stop_reason", ""), "stop")

    return {
        "id": payload.get("id", "chatcmpl-anthropic"),
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "\n".join(content_parts)},
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": payload.get("usage", {}).get("input_tokens", 0),
            "completion_tokens": payload.get("usage", {}).get("output_tokens", 0),
            "total_tokens": payload.get("usage", {}).get("input_tokens", 0)
                           + payload.get("usage", {}).get("output_tokens", 0),
        },
    }


# --------------------------------------------------------------------------- #
# Upstream call
# --------------------------------------------------------------------------- #
def call_upstream(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Call the upstream LLM provider and return an OpenAI-shaped result dict.

    config must contain:
      - upstream_base_url: e.g. https://egress.conol.ai/api/egress/openai/v1
                           or https://api.openai.com/v1 or http://127.0.0.1:11434/v1
      - upstream_api_key:  bearer token (may be empty for local unauthenticated)
      - upstream_model:    override model name (optional)

    Returns a dict with keys: content, stream_chunks, tool_calls, finish_reason.
    Raises on error.
    """
    base_url = str(config.get("upstream_base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("upstream_base_url is required for sandbox/external backend")

    api_key = str(config.get("upstream_api_key", "")).strip()
    # Fallback to env vars if config is empty (sandbox auto-discovery)
    if not api_key:
        api_key = (
            os.environ.get("OPENAI_API_KEY", "")
            or os.environ.get("ACCIO_GATEWAY_TOKEN", "")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        ).strip()
    model = str(config.get("upstream_model", "")).strip() or payload.get("model", "ide-gateway")
    stream = bool(payload.get("stream", False))

    anthropic = is_anthropic_model(model) and "anthropic" in base_url.lower()
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}

    if anthropic:
        forward_payload = openai_to_anthropic({**payload, "model": model})
        url = base_url.rstrip("/") + "/v1/messages"
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
    else:
        forward_payload = {**payload, "model": model}
        # Fix max_tokens → max_completion_tokens for newer OpenAI models
        if "max_tokens" in forward_payload:
            forward_payload["max_completion_tokens"] = forward_payload.pop("max_tokens")
        for bad_key in ("top_logprobs", "logprobs", "logit_bias"):
            forward_payload.pop(bad_key, None)
        url = base_url + "/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(config.get("request_timeout_seconds", 300))
    data = json.dumps(forward_payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")

        if stream and "text/event-stream" in ctype:
            chunks = _extract_stream_chunks_from_sse(raw)
            content = "".join(chunks) if chunks else ""
            return {"content": content, "stream_chunks": chunks or None,
                    "tool_calls": None, "finish_reason": "stop"}

        body = json.loads(raw.decode("utf-8") or "{}")
        if anthropic:
            body = anthropic_to_openai(body, model)

        content, tool_calls, finish_reason = _extract_openai_content(body)
        return {"content": content, "stream_chunks": None,
                "tool_calls": tool_calls, "finish_reason": finish_reason}


def call_upstream_stream(payload: dict[str, Any], config: dict[str, Any]):
    """Generator: call upstream with stream=True and yield SSE chunks.

    Yields raw bytes (SSE lines) ready to write to the client connection.
    """
    base_url = str(config.get("upstream_base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("upstream_base_url is required")
    api_key = str(config.get("upstream_api_key", "")).strip()
    if not api_key:
        api_key = (
            os.environ.get("OPENAI_API_KEY", "")
            or os.environ.get("ACCIO_GATEWAY_TOKEN", "")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        ).strip()
    model = str(config.get("upstream_model", "")).strip() or payload.get("model", "ide-gateway")

    anthropic = is_anthropic_model(model) and "anthropic" in base_url.lower()
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}

    if anthropic:
        forward_payload = openai_to_anthropic({**payload, "model": model, "stream": True})
        url = base_url.rstrip("/") + "/v1/messages"
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
    else:
        forward_payload = {**payload, "model": model, "stream": True}
        if "max_tokens" in forward_payload:
            forward_payload["max_completion_tokens"] = forward_payload.pop("max_tokens")
        url = base_url + "/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(config.get("request_timeout_seconds", 300))
    data = json.dumps(forward_payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            yield chunk


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _extract_openai_content(body: dict[str, Any]) -> tuple[str | None, dict | None, str]:
    choices = body.get("choices") or []
    if not choices:
        return None, None, "stop"
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")
    finish = choice.get("finish_reason", "stop")
    if tool_calls:
        return content, {"tool_calls": tool_calls, "finish_reason": finish}, finish
    return content, None, finish


def _extract_stream_chunks_from_sse(raw: bytes) -> list[str]:
    chunks: list[str] = []
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        piece = delta.get("content")
        if piece:
            chunks.append(piece)
    return chunks
