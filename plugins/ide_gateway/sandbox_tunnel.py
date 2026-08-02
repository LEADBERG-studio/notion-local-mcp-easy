"""Keyless Tunnellio TCP bridge for sandbox mode.

This helper replaces the old SSH reverse tunnel path. It starts the bundled
Tunnellio client in `bridge` mode, which provisions a keyless TCP bridge,
supervises the connection, and writes runtime status/config snapshots.

Usage:
  python sandbox_tunnel.py --state state.json
  python sandbox_tunnel.py --local-port 8787 --hostname my-sandbox

Environment variables:
  TUNNELLIO_BIN       path to tunnellio executable (optional)
  TUNNELLIO_DOMAIN    hostname override (optional)
  LOCAL_PORT          local port (default: 8787)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STATE_DIR = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp") / "ide_gateway_tunnellio"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _hostname_from_public_url(public_url: str) -> str:
    public_url = str(public_url or "").strip()
    if not public_url:
        return ""
    try:
        from urllib.parse import urlsplit
        host = urlsplit(public_url if "://" in public_url else "https://" + public_url).hostname or ""
    except Exception:
        host = public_url.split("/", 1)[0]
    suffix = ".tunnellio.site"
    if host.endswith(suffix):
        return host[:-len(suffix)]
    return host.split(":", 1)[0]


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip().lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned or f"ide-gateway-{int(time.time())}")[:80]


def _candidate_bins(explicit: str = "") -> list[str]:
    candidates: list[str] = []
    for raw in (explicit, os.environ.get("TUNNELLIO_BIN", "")):
        raw = str(raw or "").strip().strip('"')
        if raw:
            candidates.append(raw)
    candidates.extend([
        str(_REPO_ROOT / "tunnellio.exe"),
        str(_REPO_ROOT / "tunnellio"),
        "tunnellio",
        "tunnellio.exe",
    ])
    return candidates


def _resolve_tunnellio_bin(explicit: str = "") -> str:
    for item in _candidate_bins(explicit):
        path = Path(item).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(item)
        if found:
            return found
    raise RuntimeError(
        "Tunnellio client not found. Set TUNNELLIO_BIN or pass --tunnellio-path. "
        "The client must support the `bridge` command."
    )


def _start_detached(command: list[str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "cwd": str(Path.cwd()),
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _read_status(status_path: Path, runtime_config_path: Path) -> dict[str, Any]:
    status = _load_json(status_path)
    config = _load_json(runtime_config_path)
    public_url = (
        status.get("publicUrl")
        or config.get("runtime", {}).get("publicUrl")
        or config.get("transport", {}).get("publicUrl")
        or config.get("connection", {}).get("connectionProfile", {}).get("publicUrl")
        or config.get("connectionProfile", {}).get("publicUrl")
        or ""
    )
    return {"status": status, "config": config, "public_url": str(public_url or "")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Tunnellio keyless TCP bridge for sandbox")
    parser.add_argument("--state", default="", help="Endpoint state JSON produced by ide_gateway_start")
    parser.add_argument("--local-port", type=int, default=int(os.environ.get("LOCAL_PORT", "8787")))
    parser.add_argument("--hostname", default=os.environ.get("TUNNELLIO_DOMAIN", ""), help="Optional public hostname")
    parser.add_argument("--runtime-name", default="", help="Stable local runtime name")
    parser.add_argument("--tunnellio-path", default="", help="Path to tunnellio executable")
    parser.add_argument("--state-dir", default=os.environ.get("TUNNELLIO_STATE_DIR", ""))
    parser.add_argument("--health-path", default="/health")
    parser.add_argument("--wait-seconds", type=int, default=20)
    args = parser.parse_args()

    state_path = Path(args.state).expanduser() if args.state else None
    state = _load_json(state_path) if state_path else {}

    local_port = int(state.get("port") or args.local_port or 8787)
    hostname_source = "explicit" if args.hostname else ""
    hostname = (args.hostname or state.get("tunnellio_hostname") or "").strip()
    if not hostname and state.get("tunnellio_public_url"):
        hostname = _hostname_from_public_url(str(state.get("tunnellio_public_url", "")))
        hostname_source = "cached_public_url" if hostname else hostname_source
    elif hostname and not hostname_source:
        hostname_source = "state"
    runtime_name = _slug(args.runtime_name or state.get("tunnellio_runtime_name") or hostname or "ide-gateway-sandbox")
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else _DEFAULT_STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)

    status_path = state_dir / f"{runtime_name}.json"
    stop_path = state_dir / f"{runtime_name}.stop"
    runtime_config_path = state_dir / f"{runtime_name}.config.json"
    log_path = state_dir / f"{runtime_name}.log"

    try:
        tunnellio_bin = _resolve_tunnellio_bin(args.tunnellio_path)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    if stop_path.exists():
        try:
            stop_path.unlink()
        except OSError:
            pass

    def build_command(domain: str) -> list[str]:
        cmd = [
            tunnellio_bin,
            "--state-dir", str(state_dir),
            "bridge",
            "--output", "json",
            "--local-host", "127.0.0.1",
            "--local-port", str(local_port),
            "--name", runtime_name,
            "--runtime-name", runtime_name,
            "--run",
            "--watch",
            "--health-path", args.health_path,
            "--health-interval", "10",
            "--health-timeout", "5",
            "--health-failures", "3",
            "--restart-delay", "3",
            "--status-file", str(status_path),
            "--stop-file", str(stop_path),
            "--log-file", str(log_path),
        ]
        if domain:
            cmd.extend(["--domain", domain])
        return cmd

    def launch_and_wait(domain: str) -> tuple[subprocess.Popen, str, dict[str, Any]]:
        proc = _start_detached(build_command(domain), log_path)
        public = f"https://{domain}.tunnellio.site" if domain else ""
        snap: dict[str, Any] = {}
        deadline = time.time() + max(1, args.wait_seconds)
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            data = _read_status(status_path, runtime_config_path)
            if data["public_url"]:
                public = data["public_url"]
                snap = data
                break
            time.sleep(0.5)
        return proc, public, snap

    proc, public_url, snapshot = launch_and_wait(hostname)
    if proc.poll() is not None and hostname and hostname_source == "cached_public_url":
        # A cached 1-day ephemeral hostname may have expired. Fall back to a new
        # ephemeral bridge instead of making the user reconfigure manually.
        hostname = ""
        runtime_name = _slug(args.runtime_name or state.get("tunnellio_runtime_name") or "ide-gateway-sandbox")
        status_path = state_dir / f"{runtime_name}.json"
        stop_path = state_dir / f"{runtime_name}.stop"
        runtime_config_path = state_dir / f"{runtime_name}.config.json"
        log_path = state_dir / f"{runtime_name}.log"
        proc, public_url, snapshot = launch_and_wait(hostname)

    if state_path:
        updated = dict(state)
        if public_url:
            updated["tunnellio_public_url"] = public_url
            updated["tunnellio_hostname"] = _hostname_from_public_url(public_url)
            updated["upstream_base_url"] = public_url.rstrip("/") + "/v1"
        updated["tunnellio_mode"] = "tcp_bridge"
        updated["tunnellio_runtime_name"] = runtime_name
        updated["tunnellio_state_dir"] = str(state_dir)
        updated["tunnellio_status_file"] = str(status_path)
        updated["tunnellio_log_file"] = str(log_path)
        _save_json(state_path, updated)

    if proc.poll() is not None:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        print(json.dumps({
            "ok": False,
            "mode": "tcp_bridge",
            "runtime_name": runtime_name,
            "exit_code": proc.returncode,
            "log_tail": tail,
        }, ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True,
        "mode": "tcp_bridge",
        "public_url": public_url,
        "base_url": public_url.rstrip("/") + "/v1" if public_url else "",
        "runtime_name": runtime_name,
        "pid": proc.pid,
        "status_file": str(status_path),
        "runtime_config_file": str(runtime_config_path),
        "log_file": str(log_path),
        "snapshot": snapshot,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
