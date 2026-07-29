"""Tunnellio tunnel helper for sandbox environments.

This script runs inside the Notion Agent sandbox (Linux) and establishes a
reverse SSH tunnel to Tunnellio, exposing sandbox_server.py (port 8787) on a
public HTTPS URL.

It uses the Tunnellio Integration API (https://api.tunnellio.ru/v1) to:
  1. Register an SSH public key (if not already registered)
  2. Create a persistent or ephemeral domain
  3. Get a ConnectionProfile (SSH command + public URL)
  4. Launch the SSH reverse tunnel

Usage:
  python sandbox_tunnel.py --tunnellio-token <token> --local-port 8787
  python sandbox_tunnel.py --tunnellio-token <token> --local-port 8787 --hostname my-sandbox
  python sandbox_tunnel.py --tunnellio-token <token> --local-port 8787 --ephemeral

Environment variables (alternative to flags):
  TUNNELLIO_TOKEN    — Tunnellio API token
  TUNNELLIO_BASE_URL — API base (default: https://api.tunnellio.ru)
  TUNNELLIO_DOMAIN   — persistent hostname (e.g. my-sandbox)
  TUNNELLIO_KEY      — path to SSH private key (auto-generated if not set)
  LOCAL_PORT         — local port to expose (default: 8787)

Output (JSON on stdout):
  {"ok": true, "public_url": "https://my-sandbox.tunnellio.site", "ssh_command": "..."}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = os.environ.get("TUNNELLIO_BASE_URL", "https://api.tunnellio.ru").rstrip("/")


def _api_post(path: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    """Call the Tunnellio API and return the parsed response."""
    url = API_BASE + "/v1" + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _generate_ssh_keypair(key_path: Path) -> tuple[str, str]:
    """Generate an SSH keypair. Returns (private_key_path, public_key_string)."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True, capture_output=True,
    )
    pub_path = Path(str(key_path) + ".pub")
    public_key = pub_path.read_text(encoding="utf-8").strip()
    return str(key_path), public_key


def _register_key(token: str, public_key: str, name: str = "sandbox") -> str:
    """Register an SSH public key. Returns keyId."""
    resp = _api_post("/keys", token, {
        "name": name,
        "publicKey": public_key,
        "requestedLifetimeDays": 365,
    })
    if not resp.get("ok"):
        raise RuntimeError(f"Failed to register key: {resp.get('error', {})}")
    return resp["data"]["id"]


def _list_keys(token: str) -> list[dict[str, Any]]:
    resp = _api_post("/keys/list", token, {})
    if not resp.get("ok"):
        return []
    return resp.get("data", {}).get("keys", [])


def _find_or_register_key(token: str, private_key_path: Path) -> tuple[str, str]:
    """Find an existing key by fingerprint or register a new one."""
    # Generate keypair if it doesn't exist
    if not private_key_path.is_file():
        _generate_ssh_keypair(private_key_path)
    public_key = Path(str(private_key_path) + ".pub").read_text(encoding="utf-8").strip()

    # Try to find existing key by public key content
    # Get fingerprint
    fp_result = subprocess.run(
        ["ssh-keygen", "-lf", str(private_key_path)],
        capture_output=True, text=True,
    )
    fingerprint = ""
    if fp_result.returncode == 0:
        # Format: "256 SHA256:xxx  key comment (ED25519)"
        parts = fp_result.stdout.strip().split()
        if len(parts) >= 2:
            fingerprint = parts[1]

    # List keys and find by fingerprint
    keys = _list_keys(token)
    for k in keys:
        if k.get("fingerprint") == fingerprint:
            return k["id"], str(private_key_path)

    # Register new key
    key_id = _register_key(token, public_key, name="ide-gateway-sandbox")
    return key_id, str(private_key_path)


def _create_ephemeral_session(token: str, key_id: str, local_port: int) -> dict[str, Any]:
    resp = _api_post("/sessions/ephemeral", token, {
        "keyId": key_id,
        "localHost": "127.0.0.1",
        "localPort": local_port,
        "note": "ide-gateway-sandbox",
    })
    if not resp.get("ok"):
        raise RuntimeError(f"Failed to create ephemeral session: {resp.get('error', {})}")
    return resp["data"]


def _create_persistent_domain(token: str, key_id: str, hostname: str, local_port: int) -> dict[str, Any]:
    # Check availability
    check = _api_post("/domains/check", token, {"hostname": hostname})
    if not check.get("ok") or not check.get("data", {}).get("available"):
        raise RuntimeError(f"Hostname '{hostname}' is not available")

    resp = _api_post("/domains", token, {
        "hostname": hostname,
        "keyId": key_id,
        "localPort": local_port,
        "note": "ide-gateway-sandbox",
        "requestedLifetimeDays": 365,
        "authMode": "legacy",
        "stableUrlRequired": True,
        "connectionMode": "direct",
    })
    if not resp.get("ok"):
        raise RuntimeError(f"Failed to create domain: {resp.get('error', {})}")
    return resp["data"]


def _get_connection_profile(token: str, domain_id: str, local_port: int) -> dict[str, Any]:
    resp = _api_post("/domains/connection-profile", token, {
        "domainId": domain_id,
        "localHost": "127.0.0.1",
        "localPort": local_port,
    })
    if not resp.get("ok"):
        raise RuntimeError(f"Failed to get connection profile: {resp.get('error', {})}")
    return resp["data"]["connectionProfile"]


def _build_ssh_command(profile: dict[str, Any], private_key: str) -> list[str]:
    """Build an SSH command from a ConnectionProfile."""
    ssh_host = profile.get("sshHost", "")
    ssh_port = str(profile.get("sshPort", 22))
    ssh_user = profile.get("sshUser", "")
    remote_hostname = profile.get("remoteHostname", "")
    local_port = str(profile.get("localPort", 8787))

    return [
        "ssh",
        "-i", private_key,
        "-p", ssh_port,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-R", f"{remote_hostname}:80:127.0.0.1:{local_port}",
        f"{ssh_user}@{ssh_host}",
    ]


def main() -> int:
    import argparse
    global API_BASE
    parser = argparse.ArgumentParser(description="Tunnellio sandbox tunnel")
    parser.add_argument("--tunnellio-token", default=os.environ.get("TUNNELLIO_TOKEN", ""))
    parser.add_argument("--tunnellio-base-url", default=os.environ.get("TUNNELLIO_BASE_URL", API_BASE))
    parser.add_argument("--local-port", type=int, default=int(os.environ.get("LOCAL_PORT", "8787")))
    parser.add_argument("--hostname", default=os.environ.get("TUNNELLIO_DOMAIN", ""))
    parser.add_argument("--ephemeral", action="store_true", default=not bool(os.environ.get("TUNNELLIO_DOMAIN", "")))
    parser.add_argument("--ssh-key", default=os.environ.get("TUNNELLIO_KEY", ""))
    parser.add_argument("--no-connect", action="store_true", help="Only get the URL, don't start SSH")
    args = parser.parse_args()

    API_BASE = args.tunnellio_base_url.rstrip("/")

    token = args.tunnellio_token
    if not token:
        print(json.dumps({"ok": False, "error": "TUNNELLIO_TOKEN is required"}))
        return 1

    # Determine SSH key path
    key_path = Path(args.ssh_key or os.path.expanduser("~/.ssh/ide_gateway_tunnellio"))

    # Find or register key
    key_id, private_key = _find_or_register_key(token, key_path)

    # Create domain or ephemeral session
    if args.ephemeral or not args.hostname:
        session_data = _create_ephemeral_session(token, key_id, args.local_port)
        session = session_data.get("session", {})
        domain = session_data.get("domain", {})
        profile = session_data.get("connectionProfile", {})
        public_url = session.get("publicUrl") or domain.get("publicUrl", "")
    else:
        domain_data = _create_persistent_domain(token, key_id, args.hostname, args.local_port)
        domain = domain_data.get("domain", {})
        domain_id = domain.get("id", "")
        profile = _get_connection_profile(token, domain_id, args.local_port)
        public_url = profile.get("publicUrl", "")

    if not profile:
        print(json.dumps({"ok": False, "error": "No connection profile in response"}))
        return 1

    if not public_url:
        public_url = profile.get("publicUrl", "")

    ssh_cmd = _build_ssh_command(profile, private_key)

    result = {
        "ok": True,
        "public_url": public_url,
        "ssh_command": " ".join(ssh_cmd),
        "private_key": private_key,
        "mode": "ephemeral" if (args.ephemeral or not args.hostname) else "persistent",
    }

    if args.no_connect:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # Start SSH tunnel
    print(json.dumps(result, ensure_ascii=False))
    print(f"\nStarting SSH tunnel: {' '.join(ssh_cmd)}", file=sys.stderr)

    try:
        proc = subprocess.Popen(ssh_cmd, stdout=sys.stdout, stderr=sys.stderr)
        proc.wait()
        return proc.returncode
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
