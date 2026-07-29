"""Tunnellio tunnel helper for sandbox environments.

Reads domain configuration from a JSON state file (created at plugin setup
time by provision_sandbox_domain). The sandbox just needs to launch the SSH
reverse tunnel — the domain, key, and public URL are already provisioned.

Usage:
  python sandbox_tunnel.py --state <state.json> --ssh-key <key_path>
  python sandbox_tunnel.py --state <state.json>   (reads ssh_key from state)

State JSON fields (from plugin setup):
  tunnellio_ssh_host, tunnellio_ssh_port, tunnellio_ssh_user,
  tunnellio_remote_hostname, tunnellio_private_key, tunnellio_public_url,
  port (local sandbox_server port)

Output (JSON on stdout):
  {"ok": true, "public_url": "https://xxx.tunnellio.site"}
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
    import tempfile
    import stat
    parser = argparse.ArgumentParser(description="Tunnellio sandbox tunnel (reads config from state)")
    parser.add_argument("--state", required=True, help="Path to endpoint state JSON")
    parser.add_argument("--ssh-key", default="", help="Override path to SSH private key")
    parser.add_argument("--local-port", type=int, default=None, help="Override local port")
    args = parser.parse_args()

    # Load state file (created at plugin setup by provision_sandbox_domain)
    with open(args.state, "r", encoding="utf-8-sig") as f:
        state = json.load(f)

    ssh_host = state.get("tunnellio_ssh_host", "")
    ssh_port = str(state.get("tunnellio_ssh_port", "22"))
    ssh_user = state.get("tunnellio_ssh_user", "")
    remote_hostname = state.get("tunnellio_remote_hostname", "")
    public_url = state.get("tunnellio_public_url", "")
    local_port = str(args.local_port or state.get("port", 8787))

    # Get private key: either from --ssh-key (file path) or from
    # tunnellio_private_key_content (inline key content written to a temp file).
    private_key_content = state.get("tunnellio_private_key_content", "")
    if args.ssh_key:
        private_key = args.ssh_key
    elif private_key_content:
        # Write the key content to a temp file (sandbox can't read the
        # original Windows file path).
        key_file = Path(tempfile.mktemp(prefix="tunnel_key_", suffix=".pem"))
        key_file.write_text(private_key_content + "\n", encoding="utf-8")
        # SSH requires 0600 permissions on private key files
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        private_key = str(key_file)
    else:
        # Fall back to the file path (if the tunnel runs on the same machine)
        private_key = state.get("tunnellio_private_key", "")

    if not ssh_host or not private_key or not remote_hostname:
        print(json.dumps({"ok": False, "error": "State file missing tunnel config",
                          "needed": ["tunnellio_ssh_host", "tunnellio_private_key_content",
                                     "tunnellio_remote_hostname"]}))
        return 1

    if not public_url:
        print(json.dumps({"ok": False, "error": "No public_url in state"}))
        return 1

    ssh_cmd = [
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

    print(json.dumps({"ok": True, "public_url": public_url, "ssh_command": " ".join(ssh_cmd)}))
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
