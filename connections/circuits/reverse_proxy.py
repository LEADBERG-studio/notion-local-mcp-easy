"""Custom public URL / reverse proxy circuit.

No tunnel process at all. The MCP server binds locally and the operator's own
proxy publishes it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..base import Circuit, ConnectionConfigError, Question, RuntimeContext


def validate_public_origin(value: object) -> str:
    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        raise ConnectionConfigError("Reverse proxy mode requires a public base URL.")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectionConfigError("Use a full http:// or https:// public URL.")
    if not parsed.netloc:
        raise ConnectionConfigError("The public URL must include a hostname.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConnectionConfigError("Use only the base origin, without path, query or fragment.")
    return f"{parsed.scheme}://{parsed.netloc}"


class ReverseProxyCircuit(Circuit):
    id = "reverse_proxy"
    title = "Custom public URL / reverse proxy"
    summary = "No built-in tunnel. Your proxy fronts the local MCP port."
    starts_process = False
    legacy_backend = "custom_proxy"

    def questions(self) -> list[Question]:
        return [
            Question(
                key="public_url",
                prompt="Public base URL (for example https://mcp.example.com)",
                kind="url",
                help="Origin only. Your proxy must forward it to the local MCP port.",
            )
        ]

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        merged["public_url"] = validate_public_origin(merged.get("public_url"))
        return merged

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        raise ConnectionConfigError("Reverse proxy mode does not start a tunnel process.")

    def static_url(self, settings: dict[str, Any]) -> str:
        merged = self.merge(settings)
        return str(merged.get("public_url", "")).strip().rstrip("/")

    def process_match(self, settings: dict[str, Any]) -> str:
        return ""

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        return [f"Public URL: {merged.get('public_url') or '(not set)'}", "Tunnel process: none"]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.merge(settings)
        return {"tunnel_backend": "custom_proxy", "public_url": str(merged.get("public_url", ""))}
