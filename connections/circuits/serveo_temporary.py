"""Serveo temporary domain circuit.

Zero configuration. Serveo hands out a random public domain on connect, so the
URL is discovered from the SSH process output and never cached between runs.
"""

from __future__ import annotations

import queue
import re
import subprocess
import time
from typing import Any

from ..base import Circuit, ConnectionConfigError, RuntimeContext, ssh_reverse_command


class ServeoTemporaryCircuit(Circuit):
    id = "serveo_temporary"
    title = "Serveo temporary domain"
    summary = "Random public domain, nothing to configure."
    url_is_dynamic = True
    legacy_backend = "serveo"

    def questions(self):
        return []

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.merge(settings)
        if not str(merged.get("ssh_host", "")).strip():
            raise ConnectionConfigError("Serveo relay host is missing from the profile.")
        try:
            re.compile(str(merged.get("url_pattern", "")))
        except re.error as exc:
            raise ConnectionConfigError(f"Serveo URL pattern is invalid: {exc}") from exc
        return merged

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        merged = self.validate(settings)
        remote = f"{int(merged['remote_port'])}:{ctx.local_host}:{int(ctx.local_port)}"
        return ssh_reverse_command(
            key_path=None,
            remote=remote,
            target=str(merged["ssh_host"]).strip(),
            ssh_port=str(merged.get("ssh_port", "")),
            keepalive_interval=int(merged["keepalive_interval"]),
            keepalive_count=int(merged["keepalive_count"]),
            strict_host_key_checking=str(merged["strict_host_key_checking"]),
        )

    def resolve_url(
        self,
        settings: dict[str, Any],
        ctx: RuntimeContext,
        process: subprocess.Popen | None = None,
        lines: Any = None,
    ) -> str:
        merged = self.validate(settings)
        if lines is None:
            raise ConnectionConfigError("Serveo temporary mode needs the tunnel output stream.")
        pattern = re.compile(str(merged["url_pattern"]))
        deadline = time.time() + float(merged["url_timeout_seconds"])
        while time.time() < deadline:
            if process is not None and process.poll() is not None:
                raise ConnectionConfigError(
                    f"Serveo SSH tunnel exited with code {process.returncode} before publishing a URL."
                )
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            match = pattern.search(line)
            if match:
                return match.group(0)
        raise ConnectionConfigError(
            "Serveo did not publish a temporary URL in time. The relay may be busy or unreachable."
        )

    def process_match(self, settings: dict[str, Any]) -> str:
        return str(self.merge(settings).get("ssh_host", "serveo.net"))

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        merged = self.merge(settings)
        return [f"Relay: {merged['ssh_host']}", "Domain: issued per session (changes on reconnect)"]

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        return {"tunnel_backend": "serveo", "serveo_hostname": "", "ssh_key": ""}
