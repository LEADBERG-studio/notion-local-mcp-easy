"""Tunnellio stable domain circuit.

The operator reserves the domain in the Tunnellio cabinet and binds their own
SSH key to it. Two transports can carry that reservation:

``ssh`` (default)
    A plain SSH reverse forward straight to the Tunnellio edge, which speaks
    the sish protocol. No API call, no client binary, no API token. This is the
    most robust path because it depends on nothing but OpenSSH.

``cli``
    The managed ``tunnellio.exe connect`` path, which adds supervision, health
    checks and a runtime snapshot. Enabled from Tunnellio client 0.6.0 onward:
    before that, ``connect`` always issued ``POST /v1/meta`` first and free
    accounts answered ``403 plan_required``, so the whole launch aborted. The
    0.6.0 client treats that endpoint as advisory and never asks for an API
    token in this mode.

Either way this circuit needs exactly two answers from the operator: the
reserved domain and the private key bound to it.
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
from . import _tunnellio_client as client

SUPPORTED_TRANSPORTS = {"ssh", "cli"}

#: The CLI transport is only safe from this client version onward.
MIN_CLI_CLIENT_VERSION = (0, 6, 0)


class TunnellioStableCircuit(Circuit):
    id = "tunnellio_stable"
    title = "Tunnellio stable domain"
    summary = "Reserved Tunnellio domain plus your own SSH key. No API token."
    legacy_backend = "tunnellio"

    def questions(self) -> list[Question]:
        return [
            Question(
                key="domain",
                prompt="Reserved Tunnellio domain (label only, no suffix)",
                kind="hostname",
                help="The label reserved in the Tunnellio cabinet, for example 'my-mcp'.",
            ),
            Question(
                key="ssh_key",
                prompt="Private SSH key bound to that domain",
                kind="path",
                help="Full path to the private key file. Not the .pub file.",
            ),
        ]

    # ------------------------------------------------------------ validation

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        transport = str(merged.get("transport", "ssh")).strip().lower() or "ssh"
        if transport not in SUPPORTED_TRANSPORTS:
            raise ConnectionConfigError(
                f"Tunnellio stable transport '{transport}' is not supported. "
                "Use 'ssh' for a direct reverse forward or 'cli' for the managed client."
            )
        merged["transport"] = transport
        domain = normalize_label(merged.get("domain"), strip_suffix=str(merged.get("public_domain", "")))
        if not domain:
            raise ConnectionConfigError(
                "Tunnellio stable mode requires the reserved domain label. Run the "
                "connection profile setup for this circuit."
            )
        merged["domain"] = validate_label(domain, what="Tunnellio domain")
        # The key is what authorises the reservation, so it is required for both
        # transports even though the CLI never sends it to the API.
        merged["ssh_key"] = str(
            private_key_path(merged.get("ssh_key"), what="Tunnellio private SSH key")
        )
        return merged

    # --------------------------------------------------------------- runtime

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

    def _build_ssh_command(self, merged: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        remote = f"{merged['domain']}:{int(merged['remote_port'])}:{ctx.local_host}:{int(ctx.local_port)}"
        target = f"{str(merged['ssh_user']).strip()}@{str(merged['ssh_host']).strip()}"
        return ssh_reverse_command(
            key_path=Path(merged["ssh_key"]),
            remote=remote,
            target=target,
            ssh_port=str(merged.get("ssh_port", "")),
            keepalive_interval=int(merged["keepalive_interval"]),
            keepalive_count=int(merged["keepalive_count"]),
            strict_host_key_checking=str(merged["strict_host_key_checking"]),
            no_shell=True,
        )

    def _build_cli_command(self, merged: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        binary = client.resolve_client(str(merged.get("client_path", "")), ctx.script_dir)
        state_dir, name, status_path, _config_path, stop_path, log_path = self._paths(merged, ctx)
        stop_path.unlink(missing_ok=True)
        # No --token here on purpose: a reserved domain served over SSH does not
        # touch the Integration API, and the 0.6.0 client no longer demands one.
        return [
            str(binary),
            "--state-dir", str(state_dir),
            "connect",
            "--output", "json",
            "--transport", "ssh",
            "--domain", f"existing:{merged['domain']}",
            "--local-host", ctx.local_host,
            "--local-port", str(int(ctx.local_port)),
            "--name", name,
            "--runtime-name", name,
            "--run",
            "--watch" if bool(merged.get("watch", True)) else "--no-watch",
            "--health-path", str(merged.get("health_path", "/health")),
            "--status-file", str(status_path),
            "--stop-file", str(stop_path),
            "--log-file", str(log_path),
        ]

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        merged = self.validate(settings)
        if merged["transport"] == "cli":
            return self._build_cli_command(merged, ctx)
        return self._build_ssh_command(merged, ctx)

    def build_fallback_command(
        self, settings: dict[str, Any], ctx: RuntimeContext
    ) -> list[str] | None:
        """If the managed client cannot start, fall back to direct SSH.

        The reservation is the same either way, so a missing or broken binary
        should not cost the operator their tunnel.
        """
        merged = self.validate(settings)
        if merged["transport"] != "cli" or not bool(merged.get("cli_fallback_to_ssh", True)):
            return None
        return self._build_ssh_command(merged, ctx)

    def static_url(self, settings: dict[str, Any]) -> str:
        merged = self.merge(settings)
        domain = normalize_label(merged.get("domain"), strip_suffix=str(merged.get("public_domain", "")))
        suffix = str(merged.get("public_domain", "")).strip(".")
        return f"https://{domain}.{suffix}" if domain and suffix else ""

    def process_match(self, settings: dict[str, Any]) -> str:
        merged = self.merge(settings)
        return "tunnellio.exe" if str(merged.get("transport", "ssh")) == "cli" else "ssh"

    def stop(self, settings: dict[str, Any], ctx: RuntimeContext) -> None:
        merged = self.merge(settings)
        if str(merged.get("transport", "ssh")) != "cli":
            return
        try:
            _dir, _name, _status, _config, stop_path, _log = self._paths(merged, ctx)
            stop_path.write_text("stop\n", encoding="utf-8")
        except (ConnectionConfigError, OSError):
            return

    # ---------------------------------------------------------- presentation

    def verify(self, settings: dict[str, Any] | None) -> tuple[bool, str]:
        merged = self.validate(settings)
        if merged["transport"] == "cli":
            try:
                client.resolve_client(str(merged.get("client_path", "")), Path.cwd())
            except ConnectionConfigError:
                # Not fatal: the circuit falls back to direct SSH at start time.
                return True, (
                    "Domain and key look valid. The Tunnellio client was not found, "
                    f"so this profile will use direct SSH. URL: {self.static_url(merged)}"
                )
        return True, f"Domain and key look valid. Public URL will be {self.static_url(merged)}"

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        return [
            f"Domain: {merged.get('domain') or '(not set)'}",
            f"SSH key: {merged.get('ssh_key') or '(not set)'}",
            f"Transport: {merged.get('transport', 'ssh')}",
            f"Public URL: {self.static_url(merged) or '(not available yet)'}",
        ]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.merge(settings)
        return {
            "tunnel_backend": "tunnellio",
            "tunnellio_domain": str(merged.get("domain", "")),
            "ssh_key": str(merged.get("ssh_key", "")),
            "tunnel_host": str(merged.get("ssh_host", "")),
            "tunnel_ssh_port": str(merged.get("ssh_port", "")),
            "tunnel_domain": str(merged.get("public_domain", "")),
        }
