"""Serveo stable domain circuit.

The operator reserves the hostname and binds their own SSH key in the Serveo
account. This circuit only needs those two values; everything else comes from
the blueprint.
"""

from __future__ import annotations

from typing import Any

from ..base import (
    Circuit,
    ConnectionConfigError,
    Question,
    RuntimeContext,
    normalize_label,
    private_key_path,
    ssh_reverse_command,
    validate_label,
)


class ServeoStableCircuit(Circuit):
    id = "serveo_stable"
    title = "Serveo stable domain"
    summary = "Reserved hostname plus your own SSH key."
    legacy_backend = "serveo"

    def questions(self) -> list[Question]:
        return [
            Question(
                key="hostname",
                prompt="Reserved Serveo hostname (label only, no domain)",
                kind="hostname",
                help="The label you reserved in your Serveo account, for example 'my-notion-mcp'.",
            ),
            Question(
                key="ssh_key",
                prompt="Private SSH key bound to that hostname",
                kind="path",
                help="Full path to the private key file. Not the .pub file.",
            ),
        ]

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        hostname = normalize_label(
            merged.get("hostname"), strip_suffix=str(merged.get("public_domain", ""))
        )
        if not hostname:
            raise ConnectionConfigError(
                "Serveo stable mode requires the reserved hostname. Run the connection "
                "profile setup for this circuit."
            )
        merged["hostname"] = validate_label(hostname, what="Serveo hostname")
        merged["ssh_key"] = str(private_key_path(merged.get("ssh_key"), what="Serveo private SSH key"))
        if merged.get("batch_mode"):
            raise ConnectionConfigError(
                "Serveo requires keyboard-interactive auth; 'batch_mode' must stay false."
            )
        return merged

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        from pathlib import Path

        merged = self.validate(settings)
        remote = f"{merged['hostname']}:{int(merged['remote_port'])}:{ctx.local_host}:{int(ctx.local_port)}"
        return ssh_reverse_command(
            key_path=Path(merged["ssh_key"]),
            remote=remote,
            target=str(merged["ssh_host"]).strip(),
            ssh_port=str(merged.get("ssh_port", "")),
            keepalive_interval=int(merged["keepalive_interval"]),
            keepalive_count=int(merged["keepalive_count"]),
            strict_host_key_checking=str(merged["strict_host_key_checking"]),
        )

    def static_url(self, settings: dict[str, Any]) -> str:
        merged = self.merge(settings)
        hostname = normalize_label(merged.get("hostname"), strip_suffix=str(merged.get("public_domain", "")))
        domain = str(merged.get("public_domain", "")).strip(".")
        return f"https://{hostname}.{domain}" if hostname and domain else ""

    def process_match(self, settings: dict[str, Any]) -> str:
        return str(self.merge(settings).get("ssh_host", "serveo.net"))

    def verify(self, settings: dict[str, Any] | None) -> tuple[bool, str]:
        merged = self.validate(settings)
        return True, f"Key and hostname look valid. Public URL will be {self.static_url(merged)}"

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        return [
            f"Hostname: {merged.get('hostname') or '(not set)'}",
            f"SSH key: {merged.get('ssh_key') or '(not set)'}",
            f"Public URL: {self.static_url(merged) or '(not available yet)'}",
        ]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.merge(settings)
        return {
            "tunnel_backend": "serveo",
            "serveo_hostname": str(merged.get("hostname", "")),
            "ssh_key": str(merged.get("ssh_key", "")),
        }
