"""End-to-end tests for the ide_gateway long-poll bridge (no subprocess
responder). The model in chat holds a long-lived claim_next_request; when the
IDE sends a request, the claim returns it to the model, which completes the
queue entry; the worker then streams the answer back to the IDE.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_bridge.queue import enqueue_request, request_path, _read_json
from plugins.ide_bridge.state import normalize_config as normalize_bridge_config
from plugins.ide_gateway.state import normalize_config as normalize_gateway_config
import plugin_setup


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_worker(runtime: Path, port: int, token: str) -> subprocess.Popen:
    state_path = runtime / "endpoints" / "default.json"
    state_path.write_text(json.dumps({
        "name": "default", "status": "starting", "host": "127.0.0.1", "port": port,
        "base_url": f"http://127.0.0.1:{port}/v1", "model_id": "ide-bridge",
        "token": token, "pid": None, "started_at": "2026-01-01T00:00:00",
        "request_timeout_seconds": 30, "max_request_bytes": 1024 * 1024,
        "max_response_bytes": 1024 * 1024, "max_pending_requests": 8,
        "embeddings_mode": "fallback", "embeddings_dim": 1536,
        "disabled_tools": "", "log_path": str(runtime / "logs" / "default.log"),
    }), encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(PROJECT / "plugins" / "ide_bridge" / "worker.py"), "--state", str(state_path)],
        cwd=str(PROJECT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _wait_healthy(port: int, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


class IdeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.runtime = self.workspace / "temp" / "ide_bridge_runtime"
        for d in ["endpoints", "queues/default", "logs", "pids", "registry"]:
            (self.runtime / d).mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.token = "ideb_bridge-test-token-1234567890"
        self.proc = _start_worker(self.runtime, self.port, self.token)
        if not _wait_healthy(self.port):
            out = self.proc.stdout.read().decode(errors="replace") if self.proc.stdout else ""
            self.fail(f"worker did not become healthy: {out}")
        self.proc.stdout = None  # avoid ResourceWarning on close

    def tearDown(self):
        if getattr(self, "proc", None) is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.tmp.cleanup()

    def _enqueue(self, kind: str = "chat", *, stream: bool = False,
                 path: str = "/v1/chat/completions", prompt: str = "hi",
                 messages=None) -> str:
        return enqueue_request(
            self.runtime, "default",
            {"kind": kind, "path": path, "model": "ide-bridge",
             "messages": messages or [{"role": "user", "content": prompt}],
             "prompt": prompt, "stream": stream},
            timeout_seconds=30, max_pending=8,
        )

    def _wait_status(self, request_id: str, timeout: float = 10) -> dict:
        deadline = time.time() + timeout
        path = request_path(self.runtime, "default", request_id)
        while time.time() < deadline:
            req = _read_json(path, None)
            if req and req.get("status") in ("completed", "failed", "expired"):
                return req
            time.sleep(0.1)
        return _read_json(path, None) or {}

    def _model_claim_and_complete(self, timeout: int = 5) -> dict:
        """Simulate the model holding a long-lived wait_request then completing."""
        from plugins.ide_bridge.queue import claim_next_request, complete_request
        req = claim_next_request(self.runtime, "default", timeout, request_timeout=30)
        if req is None:
            self.fail("model did not receive a request (claim timed out)")
        complete_request(self.runtime, req["request_id"], "model answer via bridge",
                          finish_reason="stop", max_response_bytes=1024 * 1024)
        return req

    # 1. chat non-stream: IDE sends, model claims+completes, IDE gets answer
    def test_chat_non_stream_bridge(self):
        result = {}
        def client():
            payload = json.dumps({"model": "ide-bridge",
                                  "messages": [{"role": "user", "content": "hi"}]}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/v1/chat/completions", data=payload,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result["body"] = json.loads(resp.read().decode("utf-8"))
                    result["status"] = resp.status
            except Exception as exc:
                result["error"] = str(exc)

        t = threading.Thread(target=client)
        t.start()
        # Model bridge: claim and complete (give the client a moment to send)
        self._model_claim_and_complete(timeout=15)
        t.join(timeout=15)
        self.assertFalse(t.is_alive(), f"IDE client did not finish: {result}")
        self.assertEqual(result.get("status"), 200)
        self.assertEqual(result["body"]["choices"][0]["message"]["content"], "model answer via bridge")

    # 2. chat stream: IDE gets SSE with content + [DONE]
    def test_chat_stream_bridge(self):
        result = {}
        def client():
            payload = json.dumps({"model": "ide-bridge", "stream": True,
                                  "messages": [{"role": "user", "content": "hi"}]}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/v1/chat/completions", data=payload,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result["body"] = resp.read().decode("utf-8")
                result["content_type"] = resp.headers.get("Content-Type", "")

        t = threading.Thread(target=client)
        t.start()
        self._model_claim_and_complete(timeout=15)
        t.join(timeout=15)
        self.assertFalse(t.is_alive(), f"IDE client did not finish: {result}")
        self.assertIn("text/event-stream", result.get("content_type", ""))
        self.assertIn("model answer via bridge", result.get("body", ""))
        self.assertTrue(result["body"].rstrip().endswith("data: [DONE]"))


    def test_ide_bridge_models_endpoint_is_self_contained(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/models",
            method="GET",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        ids = [item["id"] for item in body["data"]]
        self.assertIn("ide-bridge", ids)

    # 3. setup defaults produce sandbox mode (default)
    def test_setup_defaults_produce_sandbox_mode(self):
        with mock.patch("builtins.input", return_value=""):
            config = plugin_setup.collect_ide_gateway_config({})
        self.assertTrue(config["autostart"])
        self.assertEqual(config["default_port"], 8787)
        self.assertEqual(config.get("gateway_mode"), "sandbox")
        self.assertNotIn("responder_enabled", config)

    # 3b. bridge_prompt for sandbox mode (default)
    def test_bridge_prompt_sandbox_mode(self):
        from plugins.ide_gateway.plugin import invoke
        ctx = {"workspacePath": str(self.workspace), "effectiveMode": "full_access"}
        cfg = normalize_gateway_config({}, ctx)  # default = sandbox
        result = invoke("ide_gateway_bridge_prompt", {"name": "default", "include_secret": True},
                        {**ctx, "pluginConfig": cfg})
        self.assertTrue(result["ok"])
        self.assertEqual(result["gateway_mode"], "sandbox")
        self.assertIn(".ide_gateway_bootstrap.py install", result["system_prompt"])
        self.assertIn("Do NOT run the command through Local MCP", result["system_prompt"])
        self.assertIn("<INTERNAL_LLM_BASE_URL>", result["system_prompt"])
        self.assertNotIn("IDE api_key: ***", result["system_prompt"])

    def test_bridge_prompt_includes_configured_ide_key_by_default(self):
        from plugins.ide_gateway.plugin import invoke
        ctx = {"workspacePath": str(self.workspace), "effectiveMode": "full_access"}
        cfg = normalize_gateway_config({"default_api_key": "ideg_default-secret-token-1234567890"}, ctx)
        result = invoke("ide_gateway_bridge_prompt", {"name": "default"},
                        {**ctx, "pluginConfig": cfg})
        self.assertIn("ideg_default-secret-token-1234567890", result["system_prompt"])
        redacted = invoke("ide_gateway_bridge_prompt", {"name": "default", "include_secret": False},
                          {**ctx, "pluginConfig": cfg})
        self.assertIn("IDE api_key: ***", redacted["system_prompt"])

    # 4. ide_bridge prompt returns loop instruction with prompt_type routing
    def test_ide_bridge_prompt_returns_loop_instruction(self):
        from plugins.ide_bridge.plugin import invoke
        ctx = {"workspacePath": str(self.workspace), "effectiveMode": "full_access"}
        cfg = normalize_bridge_config({}, ctx)
        result = invoke("ide_bridge_bridge_prompt", {"name": "default", "include_secret": True},
                        {**ctx, "pluginConfig": cfg})
        self.assertTrue(result["ok"])
        self.assertEqual(result["gateway_mode"], "bridge")
        self.assertIn("bridge_step.py", result["system_prompt"])
        self.assertIn("run_program", result["system_prompt"])
        self.assertIn("poll --timeout 3", result["system_prompt"])
        self.assertIn("prompt_type", result["system_prompt"])
        self.assertIn("memory_extraction", result["system_prompt"])
        self.assertIn("complete --request-id", result["system_prompt"])
        self.assertIn("Do not write chat", result["system_prompt"])
        self.assertIn("bridge_script", result)

    def test_ide_bridge_defaults_are_separate_from_gateway(self):
        with mock.patch("builtins.input", return_value=""):
            config = plugin_setup.collect_ide_bridge_config({})
        self.assertEqual(config["default_port"], 8797)
        self.assertEqual(config["gateway_mode"], "bridge")
        self.assertTrue(config["default_api_key"].startswith("ideb_"))

    # 5. classify_prompt: chat vs memory_extraction vs other
    def test_classify_prompt_chat(self):
        from plugins.ide_bridge.bridge_step import classify_prompt
        r = classify_prompt("System: be nice\n\nUser: What is 2+2?", None)
        self.assertEqual(r["prompt_type"], "chat")
        self.assertEqual(r["user_message"], "What is 2+2?")

    def test_classify_prompt_memory_extraction(self):
        from plugins.ide_bridge.bridge_step import classify_prompt
        r = classify_prompt("", [{"role": "user", "content": "SubmitMemoryPlan: sync"}])
        self.assertEqual(r["prompt_type"], "memory_extraction")

    def test_classify_prompt_from_messages(self):
        from plugins.ide_bridge.bridge_step import classify_prompt
        r = classify_prompt("", [{"role": "system", "content": "x"},
                                   {"role": "user", "content": "hello world"}])
        self.assertEqual(r["prompt_type"], "chat")
        self.assertEqual(r["user_message"], "hello world")

    def test_classify_prompt_tail(self):
        from plugins.ide_bridge.bridge_step import classify_prompt
        r = classify_prompt("x" * 5000, None)
        self.assertEqual(len(r["prompt_tail"]), 3000)

    # 5. no api keys leak in endpoint logs
    def test_no_api_keys_leak_in_logs(self):
        def client():
            payload = json.dumps({"model": "ide-bridge",
                                  "messages": [{"role": "user", "content": "x"}]}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/v1/chat/completions", data=payload, method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp.read()
            except Exception:
                pass

        t = threading.Thread(target=client)
        t.start()
        self._model_claim_and_complete(timeout=15)
        t.join(timeout=10)
        log_path = self.runtime / "logs" / "default.log"
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(self.token, text)

    def test_sandbox_tunnel_extracts_hostname_from_public_url(self):
        from plugins.ide_gateway.sandbox_tunnel import _hostname_from_public_url
        self.assertEqual(_hostname_from_public_url("https://tmp-abc.tunnellio.site/v1"), "tmp-abc")

    def test_sandbox_bootstrap_extracts_hostname_from_public_url(self):
        from plugins.ide_gateway.sandbox_bootstrap import _hostname_from_public_url
        self.assertEqual(_hostname_from_public_url("https://my-bridge.tunnellio.site"), "my-bridge")

    def test_sandbox_installer_smoke_without_tunnel(self):
        import socket
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                raw = json.dumps({"data": [{"id": "sandbox-model"}]}).encode(); self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); upstream_port = sock.getsockname()[1]; sock.close()
        server = ThreadingHTTPServer(("127.0.0.1", upstream_port), Upstream); threading.Thread(target=server.serve_forever, daemon=True).start()
        gateway_port = free_port()
        installer = PROJECT / "plugins" / "ide_gateway" / "sandbox_installer.py"
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "USERPROFILE": home}
            proc = subprocess.run([sys.executable, str(installer), "install", "--upstream-base-url", f"http://127.0.0.1:{upstream_port}", "--models", "sandbox-model", "--ide-api-key", "ideg_test_key_123456789", "--hostname", "unit-test", "--port", str(gateway_port), "--skip-tunnel"], capture_output=True, text=True, env=env, timeout=40)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{gateway_port}/health", timeout=5).read())
            self.assertTrue(body["ok"]); self.assertIn("sandbox-model", body["models"])
            stop = subprocess.run([sys.executable, str(installer), "stop"], capture_output=True, text=True, env=env, timeout=20)
            self.assertEqual(stop.returncode, 0, stop.stdout + stop.stderr)
            time.sleep(0.5)
        server.shutdown()

    def test_sandbox_installer_native_bridge_handshake(self):
        import importlib.util
        import socket
        installer_path = PROJECT / "plugins" / "ide_gateway" / "sandbox_installer.py"
        spec = importlib.util.spec_from_file_location("sandbox_installer_test", installer_path)
        module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
        listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1)
        host, control_port = listener.getsockname()
        received = {}
        def control_server():
            conn, _ = listener.accept()
            received.update(module.recv_frame(conn, 5) or {})
            module.send_frame(conn, {"type": "hello", "port": 51000})
            time.sleep(2); conn.close(); listener.close()
        threading.Thread(target=control_server, daemon=True).start()
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"
            threading.Thread(target=module.bridge, args=(host, control_port, "demo-app", 9, "", "", str(ready)), daemon=True).start()
            deadline = time.time() + 5
            while time.time() < deadline and not ready.exists(): time.sleep(0.05)
            self.assertTrue(ready.exists())
            self.assertEqual(received, {"type": "hello", "hostname": "demo-app"})

    def test_sandbox_installer_reuses_unexpired_bridge_profile(self):
        import importlib.util
        installer_path = PROJECT / "plugins" / "ide_gateway" / "sandbox_installer.py"
        spec = importlib.util.spec_from_file_location("sandbox_installer_cache_test", installer_path)
        module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
        future = "2999-01-01T00:00:00+00:00"
        self.assertTrue(module.cached_bridge_valid({
            "hostname": "demo", "public_url": "https://demo.tunnellio.site",
            "bridge_host": "tunnellio.site", "control_port": 7835,
            "domain_expires_at": future,
        }, "demo"))
        self.assertFalse(module.cached_bridge_valid({"hostname": "other"}, "demo"))


if __name__ == "__main__":
    unittest.main()
