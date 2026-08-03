"""Base contract for independent connection circuits.

Every supported public-endpoint technology (Serveo, Tunnellio, self-hosted
sish, reverse proxy) is implemented as a standalone circuit. A circuit owns:

* its immutable default blueprint (``connections/defaults/<id>.json``)
* its own private settings namespace
* its own validation rules
* its own process command, URL resolution, health path and stop logic

Hard rule: no circuit may read another circuit's settings, and no shared
"tunnel_*" field is interpreted by more than one circuit. A half-configured
Serveo profile can therefore never leak a hostname, key or domain into the
Tunnellio or sish circuits.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")


class ConnectionConfigError(RuntimeError):
    """A circuit profile is missing required data or holds an invalid value."""


class ConnectionVerifyError(RuntimeError):
    """A live verification step for a circuit failed."""


class ConnectionSetupAborted(RuntimeError):
    """Interactive setup lost its input stream.

    Prompt loops must never spin forever when stdin is closed (a service
    start, a CI run, a piped launch). Raising is the only safe answer.
    """


@dataclass
class RuntimeContext:
    """Everything a circuit is allowed to know about the host application."""

    local_port: int = 8765
    local_host: str = "127.0.0.1"
    auth_mode: str = "legacy"
    script_dir: Path = field(default_factory=Path.cwd)
    config_dir: Path = field(default_factory=Path.cwd)
    workspace: Path = field(default_factory=Path.cwd)
    runtime_name: str = "notion-local-mcp"


@dataclass
class Question:
    """One operator prompt owned by a circuit."""

    key: str
    prompt: str
    kind: str = "text"  # text | path | port | hostname | url | secret | bool
    required: bool = True
    help: str = ""


def require_ssh_client() -> None:
    if not shutil.which("ssh"):
        raise ConnectionConfigError(
            "OpenSSH client was not found. Install the Windows optional feature "
            "'OpenSSH Client' or choose a circuit that does not use SSH."
        )


def normalize_label(value: object, *, strip_suffix: str = "") -> str:
    label = str(value or "").strip().lower().strip(".")
    label = label.removeprefix("https://").removeprefix("http://").strip("/")
    if strip_suffix:
        label = label.removesuffix("." + strip_suffix.strip("."))
    return label


def validate_label(value: str, *, what: str) -> str:
    if not HOSTNAME_LABEL_PATTERN.fullmatch(value):
        raise ConnectionConfigError(
            f"{what} must be 3-63 characters: lowercase letters, digits or hyphens."
        )
    return value


def private_key_path(value: object, *, what: str) -> Path:
    raw = str(value or "").strip().strip('"')
    if not raw:
        raise ConnectionConfigError(f"{what} is required.")
    key_path = Path(raw).expanduser()
    if key_path.suffix == ".pub":
        candidate = key_path.with_suffix("")
        if not candidate.is_file():
            raise ConnectionConfigError(
                f"{what} must point to the private key, not the public .pub file: {key_path}"
            )
        key_path = candidate
    if not key_path.is_file():
        raise ConnectionConfigError(f"{what} was not found: {key_path}")
    return key_path.resolve()


def ssh_reverse_command(
    *,
    key_path: Path | None,
    remote: str,
    target: str,
    ssh_port: str = "",
    keepalive_interval: int = 30,
    keepalive_count: int = 3,
    strict_host_key_checking: str = "accept-new",
    no_shell: bool = False,
) -> list[str]:
    """Shared SSH plumbing. Values always come from the calling circuit."""

    require_ssh_client()
    command = ["ssh", "-T"]
    if no_shell:
        command.insert(1, "-N")
    command.extend(
        [
            "-o", f"StrictHostKeyChecking={strict_host_key_checking}",
            "-o", f"ServerAliveInterval={int(keepalive_interval)}",
            "-o", f"ServerAliveCountMax={int(keepalive_count)}",
            "-o", "ExitOnForwardFailure=yes",
        ]
    )
    if key_path is not None:
        command.extend(["-i", str(key_path), "-o", "IdentitiesOnly=yes"])
    if str(ssh_port).strip():
        command.extend(["-p", str(ssh_port).strip()])
    command.extend(["-R", remote, target])
    return command


class Circuit:
    """One self-contained connection technology."""

    id: str = ""
    title: str = ""
    summary: str = ""
    starts_process: bool = True
    url_is_dynamic: bool = False
    legacy_backend: str = "serveo"

    def __init__(self, blueprint: dict[str, Any]) -> None:
        self._blueprint = blueprint

    # ------------------------------------------------------------------ data

    @property
    def blueprint(self) -> dict[str, Any]:
        """Immutable shipped defaults. Callers always get a private copy."""
        return json.loads(json.dumps(self._blueprint))

    @property
    def blueprint_version(self) -> int:
        return int(self._blueprint.get("blueprintVersion", 1) or 1)

    def default_settings(self) -> dict[str, Any]:
        return dict(self.blueprint.get("settings") or {})

    def merge(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        """Blueprint defaults plus known overrides. Unknown keys are dropped.

        Dropping unknown keys is the isolation guarantee: a foreign circuit's
        field can never survive a merge into this circuit's settings.
        """
        merged = self.default_settings()
        for key, value in (settings or {}).items():
            if key in merged:
                merged[key] = value
        return merged

    def questions(self) -> list[Question]:
        """Prompts shown by the profile setup script. Empty means zero input."""
        return []

    def is_configured(self, settings: dict[str, Any] | None) -> bool:
        merged = self.merge(settings)
        for question in self.questions():
            if question.required and not str(merged.get(question.key, "")).strip():
                return False
        return True

    # ------------------------------------------------------------ validation

    def validate(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        """Return normalized settings or raise ``ConnectionConfigError``."""
        return self.merge(settings)

    def verify(self, settings: dict[str, Any] | None) -> tuple[bool, str]:
        """Optional live check performed during profile setup."""
        self.validate(settings)
        return True, "Local checks passed. This circuit has no remote pre-check."

    # --------------------------------------------------------------- runtime

    def build_command(self, settings: dict[str, Any], ctx: RuntimeContext) -> list[str]:
        raise NotImplementedError

    def static_url(self, settings: dict[str, Any]) -> str:
        return ""

    def resolve_url(
        self,
        settings: dict[str, Any],
        ctx: RuntimeContext,
        process: subprocess.Popen | None = None,
        lines: Any = None,
    ) -> str:
        return self.static_url(self.merge(settings))

    def health_path(self, settings: dict[str, Any]) -> str:
        return str(self.merge(settings).get("health_path", "/health")) or "/health"

    def start_attempts(self, settings: dict[str, Any]) -> int:
        return max(1, int(self.merge(settings).get("start_attempts", 4) or 4))

    def process_match(self, settings: dict[str, Any]) -> str:
        return "ssh"

    def stop(self, settings: dict[str, Any], ctx: RuntimeContext) -> None:
        return None

    # ---------------------------------------------------------- presentation

    def summary_lines(self, settings: dict[str, Any] | None) -> list[str]:
        return []

    def legacy_export(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Flat keys mirrored into the legacy config for old consumers."""
        return {}
