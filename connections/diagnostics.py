"""See and clean up leftover tunnel processes.

Every tunnel circuit spawns a child process: ``ssh.exe`` for the SSH circuits,
``tunnellio.exe`` for the managed ones. When a launcher window is killed rather
than stopped, or a relay drops mid-restart, those children can outlive their
parent. Until now there was no way to see them: an operator had to read raw
task lists and guess which process belonged to which tunnel.

This module answers two questions:

1. Which tunnel processes are running right now, and what is each one serving?
2. Which of them are orphans, meaning no live launcher runtime claims them?

The live tunnel recorded in ``runtime.json`` is never reported as an orphan and
is never terminated by the cleanup path.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TUNNEL_IMAGES = ("ssh.exe", "tunnellio.exe")


@dataclass
class TunnelProcess:
    pid: int
    image: str
    command_line: str = ""
    started_at: str = ""
    is_live: bool = False
    claimed_by: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_orphan(self) -> bool:
        return not self.is_live

    @property
    def remote_forward(self) -> str:
        """The ``-R`` argument, which says what the tunnel publishes."""
        parts = self.command_line.split()
        for index, part in enumerate(parts):
            if part == "-R" and index + 1 < len(parts):
                return parts[index + 1]
            if part.startswith("-R") and len(part) > 2:
                return part[2:]
        return ""

    @property
    def local_port(self) -> str:
        forward = self.remote_forward
        return forward.rsplit(":", 1)[-1] if ":" in forward else ""

    def describe(self) -> str:
        state = "LIVE" if self.is_live else "orphan"
        bits = [f"[{state}] pid {self.pid} {self.image}"]
        if self.remote_forward:
            bits.append(f"forwards {self.remote_forward}")
        if self.started_at:
            bits.append(f"since {self.started_at}")
        if self.claimed_by:
            bits.append(f"claimed by {self.claimed_by}")
        return "  ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "image": self.image,
            "commandLine": self.command_line,
            "startedAt": self.started_at,
            "isLive": self.is_live,
            "isOrphan": self.is_orphan,
            "claimedBy": self.claimed_by,
            "remoteForward": self.remote_forward,
            "notes": list(self.notes),
        }


def _powershell_process_list() -> list[dict[str, Any]]:
    """Read pid, start time and full command line for the tunnel images."""
    if os.name != "nt":
        return _posix_process_list()
    query = " or ".join(f"Name='{image}'" for image in TUNNEL_IMAGES)
    script = (
        f"Get-CimInstance Win32_Process -Filter \"{query}\" | "
        "Select-Object ProcessId,Name,CreationDate,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [item for item in data if isinstance(item, dict)]


def _posix_process_list() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,lstart=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    entries: list[dict[str, Any]] = []
    wanted = {image.removesuffix(".exe") for image in TUNNEL_IMAGES}
    for line in (result.stdout or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        command = parts[1]
        if not any(name in command for name in wanted):
            continue
        entries.append({"ProcessId": int(parts[0]), "Name": "ssh", "CommandLine": command})
    return entries


def _format_started(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == "None":
        return ""
    # PowerShell may serialise CreationDate as /Date(1234567890)/.
    if text.startswith("/Date(") and text.endswith(")/"):
        digits = text[6:-2].split("+")[0].split("-")[0]
        if digits.lstrip("-").isdigit():
            from datetime import datetime

            return datetime.fromtimestamp(int(digits) / 1000).isoformat(timespec="seconds")
    return text


def load_runtime(runtime_file: Path) -> dict[str, Any]:
    try:
        data = json.loads(runtime_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def scan(runtime_file: Path, *, workspace: str = "") -> list[TunnelProcess]:
    """List tunnel processes and mark the one the launcher currently owns."""
    runtime = load_runtime(runtime_file)
    live_pid = int(runtime.get("tunnel_pid", 0) or 0)
    live_label = str(runtime.get("url") or runtime.get("tunnellio_runtime_name") or "")

    processes: list[TunnelProcess] = []
    for item in _powershell_process_list():
        try:
            pid = int(item.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        process = TunnelProcess(
            pid=pid,
            image=str(item.get("Name") or ""),
            command_line=" ".join(str(item.get("CommandLine") or "").split()),
            started_at=_format_started(item.get("CreationDate")),
        )
        if pid == live_pid:
            process.is_live = True
            process.claimed_by = live_label or "the running launcher"
        elif workspace and workspace.lower() in process.command_line.lower():
            process.notes.append("command line mentions this workspace")
        processes.append(process)
    processes.sort(key=lambda item: (not item.is_live, item.pid))
    return processes


def orphans(processes: list[TunnelProcess]) -> list[TunnelProcess]:
    return [process for process in processes if process.is_orphan]


def terminate(pid: int, *, force: bool = False) -> tuple[bool, str]:
    """Stop one process by pid."""
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid)]
        if force:
            command.append("/F")
    else:
        command = ["kill", "-9" if force else "-15", str(pid)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, f"stopped pid {pid}"
    return False, (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def report(processes: list[TunnelProcess]) -> str:
    if not processes:
        return "No tunnel processes are running."
    lines = [f"Tunnel processes found: {len(processes)}"]
    for process in processes:
        lines.append("  " + process.describe())
        for note in process.notes:
            lines.append(f"      note: {note}")
    stray = orphans(processes)
    if stray:
        lines.append("")
        lines.append(
            f"{len(stray)} orphaned process(es): no running launcher claims them. "
            "They may still hold a relay port and block a reconnect."
        )
    return "\n".join(lines)


__all__ = [
    "TUNNEL_IMAGES",
    "TunnelProcess",
    "load_runtime",
    "orphans",
    "report",
    "scan",
    "terminate",
]
