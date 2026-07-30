"""Tunnellio tunnel for sandbox — creates a persistent domain with a
hostname reserved by the worker.

The worker (on Windows) reserves the hostname and computes the public_url.
The sandbox reads the hostname, generates an SSH key, creates the domain
with that hostname, registers the key, and starts the SSH reverse tunnel.

Usage:
  python sandbox_tunnel.py --state <state.json>
  python sandbox_tunnel.py --local-port 8787 --hostname my-sandbox

The state.json contains tunnellio_hostname (reserved by the worker).
If --hostname is passed, it overrides the state value.

Environment variables:
  TUNNELLIO_TOKEN  — Tunnellio API token (default: built-in free token)
  TUNNELLIO_DOMAIN — persistent hostname (overrides state)
  LOCAL_PORT       — local port (default: 8787)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

API_BASE = "https://api.tunnellio.ru"
DEFAULT_TUNNELLIO_TOKEN = "tnl_OK1mxYApPxqTFhRi5K5EDtimMosumaC_"


def _api_post(path: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    url = API_BASE + "/v1" + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            error_body = {}
        return {"ok": False, "error": {
            "code": str(exc.code),
            "message": str(exc),
            "details": error_body,
        }}


def _find_existing_key(token: str, fingerprint: str = "") -> str | None:
    resp = _api_post("/keys/list", token, {})
    if not resp.get("ok"):
        return None
    for k in resp.get("data", {}).get("keys", []):
        if k.get("status") == "active" and (not fingerprint or k.get("fingerprint") == fingerprint):
            return str(k["id"])
    return None


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Tunnellio sandbox tunnel")
    parser.add_argument("--state", default="", help="Path to endpoint state JSON (reads hostname)")
    parser.add_argument("--local-port", type=int, default=int(os.environ.get("LOCAL_PORT", "8787")))
    parser.add_argument("--tunnellio-token", default=os.environ.get("TUNNELLIO_TOKEN", ""))
    parser.add_argument("--hostname", default=os.environ.get("TUNNELLIO_DOMAIN", ""),
                        help="Override hostname (otherwise read from state)")
    parser.add_argument("--ssh-key", default="", help="Reuse existing key file")
    args = parser.parse_args()

    token = args.tunnellio_token or DEFAULT_TUNNELLIO_TOKEN
    hostname = args.hostname.strip()
    local_port = args.local_port

    # Read hostname from state file if not passed explicitly
    if not hostname and args.state:
        try:
            with open(args.state, "r", encoding="utf-8-sig") as f:
                state = json.load(f)
            hostname = str(state.get("tunnellio_hostname", "")).strip()
            if not local_port and state.get("port"):
                local_port = int(state["port"])
        except Exception as exc:
            print(f"Warning: could not read state file: {exc}", file=sys.stderr)

    if not hostname:
        # No hostname — use ephemeral (random, different each time)
        print("No hostname configured — using ephemeral mode.", file=sys.stderr)
    else:
        print(f"Using hostname: {hostname}", file=sys.stderr)

    # --- Step 1: Generate SSH keypair (in the sandbox!) ---
    key_dir = Path(tempfile.mkdtemp(prefix="tunnel_"))
    key_path = Path(args.ssh_key) if args.ssh_key else key_dir / "key"

    if not key_path.is_file():
        print(f"Generating SSH keypair at {key_path}...", file=sys.stderr)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
            check=True, capture_output=True,
        )
        key_path.chmod(0o600)

    public_key = Path(str(key_path) + ".pub").read_text(encoding="utf-8").strip()

    # Get fingerprint
    fp_result = subprocess.run(
        ["ssh-keygen", "-lf", str(key_path)],
        capture_output=True, text=True,
    )
    fingerprint = ""
    if fp_result.returncode == 0:
        parts = fp_result.stdout.strip().split()
        if len(parts) >= 2:
            fingerprint = parts[1]

    # --- Step 2: Find or register key ---
    key_id = _find_existing_key(token, fingerprint)
    if not key_id:
        print("Registering SSH key with Tunnellio...", file=sys.stderr)
        resp = _api_post("/keys", token, {
            "name": f"ide-gateway-{hostname or 'ephemeral'}",
            "publicKey": public_key,
            "requestedLifetimeDays": 365 if hostname else 1,
        })
        if not resp.get("ok"):
            if resp.get("error", {}).get("code") == "409":
                key_id = _find_existing_key(token)
                if not key_id:
                    print(json.dumps({"ok": False, "error": "Key 409 and no existing key"}))
                    return 1
            else:
                print(json.dumps({"ok": False, "error": f"Key failed: {resp.get('error', {})}"}))
                return 1
        else:
            key_id = str(resp["data"]["key"]["id"])
    print(f"Key ID: {key_id}", file=sys.stderr)

    key_id_val = int(key_id) if str(key_id).isdigit() else key_id

    # --- Step 3: Create persistent domain (with hostname from worker) ---
    if hostname:
        # Check if domain already exists
        check = _api_post("/domains/check", token, {"hostname": hostname})
        available = check.get("ok") and check.get("data", {}).get("available", False)

        if available:
            # Create persistent domain with this hostname
            print(f"Creating persistent domain '{hostname}'...", file=sys.stderr)
            resp = _api_post("/domains", token, {
                "hostname": hostname,
                "keyId": key_id_val,
                "localPort": local_port,
                "note": "ide-gateway-sandbox",
                "requestedLifetimeDays": 365,
                "authMode": "legacy",
                "stableUrlRequired": True,
                "connectionMode": "direct",
            })
            if not resp.get("ok"):
                print(json.dumps({"ok": False, "error": f"Domain creation failed: {resp.get('error', {})}"}))
                return 1
            domain_id = resp["data"]["domain"]["id"]
        else:
            # Domain already exists — find it
            print(f"Domain '{hostname}' already exists, finding it...", file=sys.stderr)
            domains_resp = _api_post("/domains/list", token, {})
            domain_id = None
            for d in domains_resp.get("data", {}).get("domains", []):
                if d.get("hostname") == hostname:
                    domain_id = d.get("id")
                    break
            if not domain_id:
                print(json.dumps({"ok": False, "error": f"Domain '{hostname}' not found"}))
                return 1

        # Get connection profile
        resp = _api_post("/domains/connection-profile", token, {
            "domainId": domain_id,
            "localHost": "127.0.0.1",
            "localPort": local_port,
        })
    else:
        # Ephemeral
        print("Creating ephemeral session...", file=sys.stderr)
        resp = _api_post("/sessions/ephemeral", token, {
            "keyId": key_id_val,
            "localHost": "127.0.0.1",
            "localPort": local_port,
            "note": "ide-gateway-sandbox",
        })

    if not resp.get("ok"):
        print(json.dumps({"ok": False, "error": f"Session/domain failed: {resp.get('error', {})}"}))
        return 1

    data = resp["data"]
    profile = data.get("connectionProfile", {})
    session = data.get("session", {})
    domain = data.get("domain", {})

    public_url = session.get("publicUrl") or domain.get("publicUrl") or profile.get("publicUrl", "")
    ssh_host = profile.get("sshHost", "tunnellio.site")
    ssh_port = str(profile.get("sshPort", 2222))
    ssh_user = profile.get("sshUser", "tunnel")
    remote_hostname = profile.get("remoteHostname", "")

    if not public_url or not remote_hostname:
        print(json.dumps({"ok": False, "error": "Missing public_url or remote_hostname", "data": data}))
        return 1

    # --- Step 4: Start SSH reverse tunnel ---
    ssh_cmd = [
        "ssh",
        "-i", str(key_path),
        "-p", ssh_port,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-R", f"{remote_hostname}:80:127.0.0.1:{local_port}",
        f"{ssh_user}@{ssh_host}",
    ]

    print(json.dumps({
        "ok": True,
        "public_url": public_url,
        "ssh_host": ssh_host,
        "ssh_port": ssh_port,
        "remote_hostname": remote_hostname,
        "key_path": str(key_path),
        "mode": "persistent" if hostname else "ephemeral",
    }))

    print(f"\nSSH tunnel: {' '.join(ssh_cmd)}", file=sys.stderr)
    print(f"Public URL: {public_url}", file=sys.stderr)
    print("Tunnel running. Press Ctrl+C to stop.", file=sys.stderr)

    try:
        proc = subprocess.Popen(ssh_cmd, stdout=sys.stderr, stderr=sys.stderr)
        proc.wait()
        return proc.returncode
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
