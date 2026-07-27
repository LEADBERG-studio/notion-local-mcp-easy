from __future__ import annotations

import hmac
import re
import secrets


def generate_token() -> str:
    """Generate a local IDE provider API token."""
    return "idep_" + secrets.token_urlsafe(32)


_TOKEN_RE = re.compile(r"idep_[A-Za-z0-9_-]+")

_SECRET_RE = re.compile(
    r"(?i)\b(authorization|x-api-key|token|secret|password|api_key)\b['\"]?\s*[:=]\s*\S.*"
)


def redact_line(line: str) -> str:
    """Redact tokens and secret-bearing fields from a single log line."""
    line = _TOKEN_RE.sub("***", line)
    line = _SECRET_RE.sub(lambda m: f"{m.group(1)}: ***", line)
    return line


def check_auth(headers: dict[str, str], token: str) -> bool:
    """Validate Authorization or X-API-Key header against the endpoint token."""
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    else:
        provided = auth.strip()
    if provided and hmac.compare_digest(provided, token):
        return True
    api_key = headers.get("X-API-Key", "")
    if api_key and hmac.compare_digest(api_key.strip(), token):
        return True
    return False
