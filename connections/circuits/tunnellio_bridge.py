"""Tunnellio keyless TCP bridge circuit.

The native bridge protocol needs no SSH key, no key registration and no cloud
token for a random domain. Leaving both the domain and the token empty is a
valid, fully working profile: the server issues an ephemeral domain on connect.

A reserved domain can be pinned instead, and an API token can be supplied when
the account plan requires one for reserved domains.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..base import (
    Circuit,
    ConnectionConfigError,
    Question,
    RuntimeContext,
    normalize_label,
    validate_label,
)
from . import _tunnellio_client as client


class TunnellioBridgeCircuit(Circuit):
    id = "tunnellio_bridge"
    title = "Tunnellio direct TCP bridge"
    summary = "Keyless native bridge. Random domain out of the box, reserved domain optional."
    url_is_dynamic = True
    legacy_backend = "tunnellio"

    def questions(self) -> list[Question]:
        return [
            Question(
                key="domain",
                prompt="Reserved Tunnellio domain (leave empty for a random one)",
                kind="hostname",
                required=False,
                help="Optional. Empty means the server issues an ephemeral domain.",
            ),
            Question(
                key="api_token",
                prompt="Tunnellio API token (only if your plan needs one)",
                kind="secret",
                required=False,
                help="Optional. Validated against the server when provided.",
            ),
        ]

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        domain = normalize_label(merged.get("domain"), strip_suffix=str(merged.get("public_domain", "")))
        if domain:
            validate_label(domain, what="Tunnellio bridge domain")
        merged["domain"] = domain
        if not str(merged.get("base_url", "")).strip().startswith("http"):
            raise ConnectionConfigError("Tunnellio base URL must be a full http(s) origin.")
        return merged

    def verify(self, settings: dict[str, Any] | None) -> tuple[bool, str]:
        merged = self.validate(settings)
        token = str(merged.get("api_token", "")).strip()
        if not token:
            return True, "Keyless bridge profile. A random domain will be issued at start time."
        return client.verify_api_token(str(merged["base_url"]), token)

    def _runtime_name(self, merged: dict[str, Any], ctx: RuntimeContext) -> str:
        explicit = str(merged.get("runtime_name", "")).strip()
        return client.slugify(explicit or merged.get("domain") or ctx.runtime_name)

    def _paths(self, merged: dict[str, Any], ctx: RuntimeContext):
        state_dir = client.resolve_state_dir(str(merged.get("state_dir", "")), ctx.config_dir, self.id)
        name = self._runtime_name(merged, ctx)
        return (
            state_dir,
            name,
            state_dir / f"{name}.json",
            state_dir / f"{name}.config.json",
            state_dir / f"{name}.stop",
            state_dir / f"{name}.log",
        )

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        merged = self.validate(settings)
        binary = client.resolve_client(str(merged.get("client_path", "")), ctx.script_dir)
        state_dir, name, status_path, _config_path, stop_path, log_path = self._paths(merged, ctx)
        stop_path.unlink(missing_ok=True)
        command = [str(binary)]
        token = str(merged.get("api_token", "")).strip()
        if token:
            command.extend(["--token", token, "--base-url", str(merged["base_url"]).rstrip("/")])
        command.extend(
            [
                "--state-dir", str(state_dir),
                "bridge",
                "--output", "json",
                "--local-host", ctx.local_host,
                "--local-port", str(int(ctx.local_port)),
                "--name", name,
                "--runtime-name", name,
                "--run",
                "--watch" if bool(merged.get("watch", True)) else "--no-watch",
                "--health-path", str(merged.get("health_path", "/health")),
                "--health-interval", str(int(merged.get("health_interval", 10))),
                "--health-timeout", str(int(merged.get("health_timeout", 5))),
                "--health-failures", str(int(merged.get("health_failures", 3))),
                "--restart-delay", str(int(merged.get("restart_delay", 3))),
                "--status-file", str(status_path),
                "--stop-file", str(stop_path),
                "--log-file", str(log_path),
            ]
        )
        if merged["domain"]:
            command.extend(["--domain", merged["domain"]])
        return command

    def build_fallback_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str] | None:
        """Retry command used when a cached ephemeral domain has expired."""
        merged = self.validate(settings)
        if not merged["domain"] or not bool(merged.get("expired_domain_fallback", True)):
            return None
        retry = dict(merged)
        retry["domain"] = ""
        return self.build_command(retry, ctx)

    def resolve_url(
        self,
        settings: dict[str, Any],
        ctx: RuntimeContext,
        process: subprocess.Popen | None = None,
        lines: Any = None,
    ) -> str:
        merged = self.validate(settings)
        _dir, _name, status_path, config_path, _stop, log_path = self._paths(merged, ctx)
        url = client.wait_for_public_url(
            status_path,
            config_path,
            timeout_seconds=float(merged.get("url_timeout_seconds", 40) or 40),
            process=process,
        )
        if url:
            return url
        if merged["domain"]:
            suffix = str(merged.get("public_domain", "")).strip(".")
            if suffix:
                return f"https://{merged['domain']}.{suffix}"
        tail = client.log_tail(log_path)
        raise ConnectionConfigError(
            "The Tunnellio bridge did not publish a public URL in time."
            + (f" Client log tail:\n{tail}" if tail else "")
        )

    def static_url(self, settings: dict[str, Any]) -> str:
        merged = self.merge(settings)
        domain = normalize_label(merged.get("domain"), strip_suffix=str(merged.get("public_domain", "")))
        suffix = str(merged.get("public_domain", "")).strip(".")
        return f"https://{domain}.{suffix}" if domain and suffix else ""

    def process_match(self, settings: dict[str, Any]) -> str:
        return "tunnellio.exe"

    def stop(self, settings: dict[str, Any], ctx: RuntimeContext) -> None:
        merged = self.merge(settings)
        try:
            _dir, _name, _status, _config, stop_path, _log = self._paths(merged, ctx)
            stop_path.write_text("stop\n", encoding="utf-8")
        except (ConnectionConfigError, OSError):
            return

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        token = str(merged.get("api_token", ""))
        masked = f"{token[:4]}...{token[-4:]}" if len(token) > 10 else ("set" if token else "not used")
        return [
            f"Domain: {merged.get('domain') or 'random (server-issued)'}",
            f"API token: {masked}",
            "Transport: keyless native TCP bridge",
        ]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.merge(settings)
        return {
            "tunnel_backend": "tunnellio",
            "tunnellio_domain": str(merged.get("domain", "")),
            "tunnellio_token": str(merged.get("api_token", "")),
            "tunnellio_base_url": str(merged.get("base_url", "")),
            "tunnellio_connection_mode": "tcp_bridge",
        }
