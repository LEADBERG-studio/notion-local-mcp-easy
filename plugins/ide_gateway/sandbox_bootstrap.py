"""One-shot sandbox bootstrap for IDE Gateway.

Starts the sandbox OpenAI-compatible server and the keyless Tunnellio TCP bridge
from a single command. The point is to make the model do exactly one
`run_program` call after the user says "подними мост".
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = Path(__file__).resolve().parent


def _load_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _save_json(path: str | Path, data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _detect_sandbox_egress() -> dict[str, str]:
    base_url = _first_env(
        "OPENAI_BASE_URL",
        "ACCIO_GATEWAY_BASE_URL",
        "SANDBOX_OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
    )
    api_key = _first_env(
        "OPENAI_API_KEY",
        "ACCIO_GATEWAY_TOKEN",
        "SANDBOX_OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    )
    model = _first_env("IDE_GATEWAY_MODEL", "OPENAI_MODEL", "MODEL", "DEFAULT_MODEL", "ANTHROPIC_MODEL")
    extra_models = _first_env("IDE_GATEWAY_EXTRA_MODELS", "OPENAI_MODELS", "MODELS", "AVAILABLE_MODELS", "ACCIO_MODELS", "SANDBOX_MODELS", "ANTHROPIC_MODELS")
    return {
        "upstream_base_url": base_url,
        "upstream_api_key": api_key,
        "upstream_model": model,
        "extra_models": extra_models,
    }


def _merge_state(state_path: str, updates: dict[str, Any]) -> dict[str, Any]:
    state = _load_json(state_path)
    changed = False
    for key, value in updates.items():
        if value and not state.get(key):
            state[key] = value
            changed = True
    if changed and state_path:
        _save_json(state_path, state)
    return state


def _wait_health(port: int, timeout: int = 20) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + max(1, timeout)
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200:
                    return True, text
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    return False, last


def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(args, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description="IDE Gateway one-shot sandbox bootstrap")
    parser.add_argument("--state", default="")
    parser.add_argument("--port", type=int, default=int(os.environ.get("IDE_GATEWAY_PORT", "8787")))
    parser.add_argument("--hostname", default=os.environ.get("TUNNELLIO_DOMAIN", ""))
    parser.add_argument("--tunnellio-path", default="")
    parser.add_argument("--wait-seconds", type=int, default=25)
    args = parser.parse_args()

    detected = _detect_sandbox_egress()
    state = _merge_state(args.state, detected) if args.state else detected
    port = int(state.get("port") or args.port or 8787)

    server_cmd = [sys.executable, str(_PLUGIN_DIR / "sandbox_server.py")]
    if args.state:
        server_cmd += ["--state", args.state]
    else:
        server_cmd += ["--port", str(port)]

    srv_rc, srv_out, srv_err = _run(server_cmd, timeout=10)
    healthy, health_detail = _wait_health(port, args.wait_seconds)
    if srv_rc != 0 or not healthy:
        print(json.dumps({
            "ok": False,
            "stage": "server",
            "return_code": srv_rc,
            "stdout": srv_out[-4000:],
            "stderr": srv_err[-4000:],
            "health": health_detail,
            "detected_egress": {k: ("***" if "key" in k else v) for k, v in detected.items()},
        }, ensure_ascii=False))
        return 1

    tunnel_cmd = [sys.executable, str(_PLUGIN_DIR / "sandbox_tunnel.py")]
    if args.state:
        tunnel_cmd += ["--state", args.state]
    else:
        tunnel_cmd += ["--local-port", str(port)]
    if args.hostname:
        tunnel_cmd += ["--hostname", args.hostname]
    if args.tunnellio_path:
        tunnel_cmd += ["--tunnellio-path", args.tunnellio_path]

    tnl_rc, tnl_out, tnl_err = _run(tunnel_cmd, timeout=max(30, args.wait_seconds + 10))
    try:
        tunnel = json.loads(tnl_out.strip().splitlines()[-1]) if tnl_out.strip() else {}
    except Exception:
        tunnel = {"raw_stdout": tnl_out[-4000:]}

    ok = tnl_rc == 0 and bool(tunnel.get("ok"))
    public_url = tunnel.get("public_url", "")
    if args.state and public_url:
        updated = _load_json(args.state)
        updated["tunnellio_public_url"] = public_url
        updated["tunnellio_hostname"] = _hostname_from_public_url(public_url)
        updated["upstream_base_url"] = public_url.rstrip("/") + "/v1"
        _save_json(args.state, updated)

    print(json.dumps({
        "ok": ok,
        "mode": "sandbox_tcp_bridge",
        "server": {"healthy": healthy, "health": health_detail},
        "tunnel": tunnel,
        "base_url": (public_url.rstrip("/") + "/v1") if public_url else tunnel.get("base_url", ""),
        "detected_egress": {k: ("***" if "key" in k else v) for k, v in detected.items()},
        "stderr": tnl_err[-4000:],
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
