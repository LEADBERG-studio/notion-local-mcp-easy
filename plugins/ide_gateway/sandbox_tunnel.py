"""Tunnellio tunnel for sandbox — self-contained.

Generates SSH keypair IN THE SANDBOX, registers with Tunnellio API,
creates session, and starts SSH reverse tunnel. No Windows file access,
no cross-platform permission issues.

Usage:
  python sandbox_tunnel.py --local-port 8787
  python sandbox_tunnel.py --local-port 8787 --hostname my-sandbox
  python sandbox_tunnel.py --local-port 8787 --tunnellio-token tnl_xxx

Environment variables:
  TUNNELLIO_TOKEN  — Tunnellio API token (default: built-in free token)
  TUNNELLIO_DOMAIN — persistent hostname (empty = ephemeral)
  LOCAL_PORT       — local port (default: 8787)

Output (JSON on stdout first):
  {"ok": true, "public_url": "https://xxx.tunnellio.site"}
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
    parser = argparse.ArgumentParser(description="Tunnellio sandbox tunnel (self-contained)")
    parser.add_argument("--local-port", type=int, default=int(os.environ.get("LOCAL_PORT", "8787")))
    parser.add_argument("--tunnellio-token", default=os.environ.get("TUNNELLIO_TOKEN", ""))
    parser.add_argument("--hostname", default=os.environ.get("TUNNELLIO_DOMAIN", ""))
    parser.add_argument("--ssh-key", default="", help="Reuse existing key file")
    args = parser.parse_args()

    token = args.tunnellio_token or DEFAULT_TUNNELLIO_TOKEN
    hostname = args.hostname.strip()
    local_port = args.local_port

    # --- Step 1: Generate or reuse SSH keypair (in the sandbox!) ---
    key_dir = Path(tempfile.mkdtemp(prefix="tunnel_"))
    key_path = Path(args.ssh_key) if args.ssh_key else key_dir / "key"

    if not key_path.is_file():
        print(f"Generating SSH keypair at {key_path}...", file=sys.stderr)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
            check=True, capture_output=True,
        )
        # Set correct permissions (0600) — SSH refuses "too open" keys
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

    # --- Step 2: Find or register key with Tunnellio ---
    key_id = _find_existing_key(token, fingerprint)
    if not key_id:
        print("Registering SSH key with Tunnellio...", file=sys.stderr)
        resp = _api_post("/keys", token, {
            "name": f"ide-gateway-{'ephemeral' if not hostname else hostname}",
            "publicKey": public_key,
            "requestedLifetimeDays": 1 if not hostname else 365,
        })
        if not resp.get("ok"):
            # Maybe 409 — try finding any active key
            if resp.get("error", {}).get("code") == "409":
                key_id = _find_existing_key(token)
                if not key_id:
                    print(json.dumps({"ok": False, "error": "Key registration failed (409) and no existing key found"}))
                    return 1
            else:
                print(json.dumps({"ok": False, "error": f"Key registration failed: {resp.get('error', {})}"}))
                return 1
        else:
            key_id = str(resp["data"]["key"]["id"])

    print(f"Key ID: {key_id}", file=sys.stderr)

    # --- Step 3: Create ephemeral session or persistent domain ---
    if not hostname:
        print("Creating ephemeral session...", file=sys.stderr)
        resp = _api_post("/sessions/ephemeral", token, {
            "keyId": int(key_id) if str(key_id).isdigit() else key_id,
            "localHost": "127.0.0.1",
            "localPort": local_port,
            "note": "ide-gateway-sandbox",
        })
    else:
        print(f"Creating persistent domain '{hostname}'...", file=sys.stderr)
        # Check availability
        check = _api_post("/domains/check", token, {"hostname": hostname})
        if check.get("ok") and not check.get("data", {}).get("available"):
            # Domain already exists — try to get its connection profile
            print(f"Domain '{hostname}' already exists, getting profile...", file=sys.stderr)
            resp = _api_post("/domains/connection-profile", token, {
                "domainId": 0,  # Will need domain ID — try listing
                "localHost": "127.0.0.1",
                "localPort": local_port,
            })
        else:
            resp = _api_post("/domains", token, {
                "hostname": hostname,
                "keyId": int(key_id) if str(key_id).isdigit() else key_id,
                "localPort": local_port,
                "note": "ide-gateway-sandbox",
                "requestedLifetimeDays": 365,
                "authMode": "legacy",
                "stableUrlRequired": True,
                "connectionMode": "direct",
            })
            if resp.get("ok"):
                domain_id = resp["data"]["domain"]["id"]
                # Get connection profile
                resp = _api_post("/domains/connection-profile", token, {
                    "domainId": domain_id,
                    "localHost": "127.0.0.1",
                    "localPort": local_port,
                })

    if not resp.get("ok"):
        print(json.dumps({"ok": False, "error": f"Session/domain creation failed: {resp.get('error', {})}"}))
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
        print(json.dumps({"ok": False, "error": "Missing public_url or remote_hostname",
                          "data": data}))
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

    # Print JSON result for the model to read
    print(json.dumps({
        "ok": True,
        "public_url": public_url,
        "ssh_host": ssh_host,
        "ssh_port": ssh_port,
        "remote_hostname": remote_hostname,
        "key_path": str(key_path),
        "mode": "ephemeral" if not hostname else "persistent",
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
