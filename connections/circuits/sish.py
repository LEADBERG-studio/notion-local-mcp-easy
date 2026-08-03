"""Self-hosted sish relay circuit.

Everything is the operator's own infrastructure: relay host, SSH port, wildcard
domain, subdomain label and key. This circuit never falls back to a public
relay and never borrows a hostname from any other circuit.
"""

from __future__ import annotations

from pathlib import Path
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


class SishCircuit(Circuit):
    id = "sish"
    title = "Self-hosted sish relay"
    summary = "SSH reverse tunnel into your own relay and wildcard domain."
    legacy_backend = "sish"

    def questions(self) -> list[Question]:
        return [
            Question(key="ssh_host", prompt="sish relay SSH host", kind="text"),
            Question(key="ssh_port", prompt="sish relay SSH port", kind="port"),
            Question(key="wildcard_domain", prompt="Public wildcard base domain", kind="text"),
            Question(key="subdomain", prompt="Reserved subdomain label", kind="hostname"),
            Question(key="ssh_key", prompt="Private SSH key authorized on the relay", kind="path"),
            Question(
                key="ssh_user",
                prompt="SSH user on the relay (empty for none)",
                kind="text",
                required=False,
            ),
        ]

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        host = str(merged.get("ssh_host", "")).strip()
        if not host:
            raise ConnectionConfigError("sish relay SSH host is required.")
        merged["ssh_host"] = host
        port = str(merged.get("ssh_port", "")).strip()
        if not port.isdigit():
            raise ConnectionConfigError("sish relay SSH port must be numeric.")
        merged["ssh_port"] = port
        domain = normalize_label(merged.get("wildcard_domain"))
        if not domain or "." not in domain:
            raise ConnectionConfigError("sish wildcard base domain is required, for example 'tun.example.com'.")
        merged["wildcard_domain"] = domain
        subdomain = normalize_label(merged.get("subdomain"), strip_suffix=domain)
        if not subdomain:
            raise ConnectionConfigError("sish subdomain label is required.")
        merged["subdomain"] = validate_label(subdomain, what="sish subdomain")
        merged["ssh_key"] = str(private_key_path(merged.get("ssh_key"), what="sish private SSH key"))
        return merged

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        merged = self.validate(settings)
        remote = f"{merged['subdomain']}:{int(merged['remote_port'])}:{ctx.local_host}:{int(ctx.local_port)}"
        user = str(merged.get("ssh_user", "")).strip()
        target = f"{user}@{merged['ssh_host']}" if user else str(merged["ssh_host"])
        return ssh_reverse_command(
            key_path=Path(merged["ssh_key"]),
            remote=remote,
            target=target,
            ssh_port=str(merged["ssh_port"]),
            keepalive_interval=int(merged["keepalive_interval"]),
            keepalive_count=int(merged["keepalive_count"]),
            strict_host_key_checking=str(merged["strict_host_key_checking"]),
        )

    def static_url(self, settings: dict[str, Any]) -> str:
        merged = self.merge(settings)
        subdomain = normalize_label(merged.get("subdomain"))
        domain = normalize_label(merged.get("wildcard_domain"))
        return f"https://{subdomain}.{domain}" if subdomain and domain else ""

    def process_match(self, settings: dict[str, Any]) -> str:
        return str(self.merge(settings).get("ssh_host", "")).strip() or "ssh"

    def verify(self, settings: dict[str, Any] | None) -> tuple[bool, str]:
        merged = self.validate(settings)
        return True, f"Relay settings look valid. Public URL will be {self.static_url(merged)}"

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        return [
            f"Relay: {merged.get('ssh_host') or '(not set)'}:{merged.get('ssh_port')}",
            f"Wildcard: {merged.get('wildcard_domain') or '(not set)'}",
            f"Subdomain: {merged.get('subdomain') or '(not set)'}",
            f"SSH key: {merged.get('ssh_key') or '(not set)'}",
            f"Public URL: {self.static_url(merged) or '(not available yet)'}",
        ]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.merge(settings)
        return {
            "tunnel_backend": "sish",
            "tunnel_host": str(merged.get("ssh_host", "")),
            "tunnel_ssh_port": str(merged.get("ssh_port", "")),
            "tunnel_domain": str(merged.get("wildcard_domain", "")),
            "serveo_hostname": str(merged.get("subdomain", "")),
            "ssh_key": str(merged.get("ssh_key", "")),
        }
