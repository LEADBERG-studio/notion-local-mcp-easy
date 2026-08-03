"""Tunnellio random (server-issued) domain circuit.

The operator supplies only an API token. The setup script validates that token
against the Tunnellio server and refuses to save the profile until the server
confirms it. At runtime the managed client asks the server for a domain and the
final URL is read back from the runtime snapshot.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..base import Circuit, ConnectionConfigError, Question, RuntimeContext
from . import _tunnellio_client as client


class TunnellioRandomCircuit(Circuit):
    id = "tunnellio_random"
    title = "Tunnellio random domain"
    summary = "Managed runtime, server-issued ephemeral domain. API token only."
    url_is_dynamic = True
    legacy_backend = "tunnellio"

    def questions(self) -> list[Question]:
        return [
            Question(
                key="api_token",
                prompt="Tunnellio API token",
                kind="secret",
                help="Created in the Tunnellio cabinet. Checked against the server before saving.",
            )
        ]

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        if not str(merged.get("api_token", "")).strip():
            raise ConnectionConfigError(
                "Tunnellio random mode requires an API token. Run the connection "
                "profile setup for this circuit."
            )
        if not str(merged.get("base_url", "")).strip().startswith("http"):
            raise ConnectionConfigError("Tunnellio base URL must be a full http(s) origin.")
        return merged

    def verify(self, settings: dict[str, Any] | None) -> tuple[bool, str]:
        merged = self.validate(settings)
        return client.verify_api_token(
            str(merged["base_url"]),
            str(merged["api_token"]),
            timeout=int(merged.get("verify_timeout_seconds", 15) or 15),
        )

    def _runtime_name(self, merged: dict[str, Any], ctx: RuntimeContext) -> str:
        return client.slugify(str(merged.get("runtime_name", "")).strip() or ctx.runtime_name)

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
        command = [
            str(binary),
            "--token", str(merged["api_token"]),
            "--base-url", str(merged["base_url"]).rstrip("/"),
            "--state-dir", str(state_dir),
            "connect",
            "--output", "json",
            "--local-host", ctx.local_host,
            "--local-port", str(int(ctx.local_port)),
            "--run",
            "--watch" if bool(merged.get("watch")) else "--no-watch",
            "--name", name,
            "--runtime-name", name,
            "--requested-auth-mode", str(ctx.auth_mode or "legacy"),
            "--connection-mode", str(merged["connection_mode"]),
            "--oauth-client-policy", str(merged["oauth_client_policy"]),
            "--session-strategy", str(merged["session_strategy"]),
            "--health-path", str(merged.get("health_path", "/health")),
            "--status-file", str(status_path),
            "--stop-file", str(stop_path),
            "--log-file", str(log_path),
        ]
        command.append("--use-discovery" if bool(merged.get("use_discovery")) else "--no-use-discovery")
        command.append("--enable-pkce" if bool(merged.get("enable_pkce")) else "--no-enable-pkce")
        return command

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
        tail = client.log_tail(log_path)
        raise ConnectionConfigError(
            "Tunnellio did not publish a random domain in time."
            + (f" Client log tail:\n{tail}" if tail else "")
        )

    def process_match(self, settings: dict[str, Any]) -> str:
        return "tunnellio.exe"

    def stop(self, settings: dict[str, Any], ctx: RuntimeContext) -> None:
        merged = self.merge(settings)
        try:
            _dir, _name, _status, _config, stop_path, _log = self._paths(merged, ctx)
        except ConnectionConfigError:
            return
        try:
            stop_path.write_text("stop\n", encoding="utf-8")
        except OSError:
            pass

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        token = str(merged.get("api_token", ""))
        masked = f"{token[:4]}...{token[-4:]}" if len(token) > 10 else ("set" if token else "(not set)")
        return [
            f"API token: {masked}",
            f"Server: {merged.get('base_url')}",
            "Domain: issued by the server per session",
        ]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.merge(settings)
        return {
            "tunnel_backend": "tunnellio",
            "tunnellio_token": str(merged.get("api_token", "")),
            "tunnellio_base_url": str(merged.get("base_url", "")),
            "tunnellio_domain": "",
            "tunnellio_connection_mode": str(merged.get("connection_mode", "")),
        }
