import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_gateway.queue import enqueue_request, request_path, _read_json
import plugin_setup
from plugins.ide_gateway.state import normalize_config


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- #
# Mock OpenAI-compatible upstream server
# --------------------------------------------------------------------------- #
class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        stream = bool(payload.get("stream", False))
        path = self.path

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # Emit a couple of chat.completion.chunk deltas then [DONE]
            for piece in ["Hello ", "from ", "upstream"]:
                chunk = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                         "created": int(time.time()), "model": payload.get("model", "mock"),
                         "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            done_chunk = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                          "created": int(time.time()), "model": payload.get("model", "mock"),
                          "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(done_chunk)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return

        # Non-stream chat
        if path.endswith("/chat/completions"):
            resp = {"id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()),
                    "model": payload.get("model", "mock"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "non-stream upstream answer"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        # Non-stream responses
        if path.endswith("/responses"):
            resp = {"id": "resp_mock", "object": "response", "created_at": int(time.time()),
                    "model": payload.get("model", "mock"), "status": "completed",
                    "output": [{"type": "message", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": "responses upstream answer"}]}],
                    "output_text": "responses upstream answer"}
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


class _UpstreamServer:
    def __init__(self):
        self.port = free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _UpstreamHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"


# --------------------------------------------------------------------------- #
# Helper: write a responder state file pointing at the mock upstream
# --------------------------------------------------------------------------- #
def _write_responder_state(runtime_root: Path, endpoint: str, upstream_base_url: str,
                           upstream_model: str = "mock-model") -> Path:
    resp_dir = runtime_root / "responder"
    resp_dir.mkdir(parents=True, exist_ok=True)
    (runtime_root / "logs").mkdir(parents=True, exist_ok=True)
    (runtime_root / "queues" / endpoint).mkdir(parents=True, exist_ok=True)
    state_path = resp_dir / f"{endpoint}.json"
    state = {
        "name": endpoint,
        "status": "starting",
        "pid": None,
        "started_at": "",
        "last_request_id": "",
        "last_error": "",
        "upstream_type": "openai_compatible",
        "upstream_model": upstream_model,
        "upstream_base_url": upstream_base_url,
        "responder_request_timeout_seconds": 30,
        "responder_poll_interval_seconds": 0.25,
        "responder_upstream_api_key": "",
        "max_response_bytes": 4 * 1024 * 1024,
        "responder_log_path": str(runtime_root / "logs" / f"{endpoint}.responder.log"),
        "log_path": str(runtime_root / "logs" / f"{endpoint}.log"),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def _start_responder_proc(state_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(PROJECT / "plugins" / "ide_gateway" / "responder.py"), "--state", str(state_path)],
        cwd=str(PROJECT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class IdeGatewayResponderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # runtime_root(context) = workspace/temp/ide_gateway_runtime; mimic that
        # by making the test workspace the tmp dir root and the runtime its
        # temp/ide_gateway_runtime subdir.
        self.workspace = Path(self.tmp.name)
        self.runtime = self.workspace / "temp" / "ide_gateway_runtime"
        for name in ["endpoints", "queues", "logs", "pids", "registry", "responder"]:
            (self.runtime / name).mkdir(parents=True, exist_ok=True)
        (self.runtime / "queues" / "default").mkdir(parents=True, exist_ok=True)
        self.upstream = _UpstreamServer().start()
        self.state_path = _write_responder_state(self.runtime, "default", self.upstream.base_url)
        self.proc = _start_responder_proc(self.state_path)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state.get("status") == "running":
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            self.fail("responder did not become running")

    def tearDown(self):
        if getattr(self, "proc", None) is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        try:
                            self.proc.wait(timeout=2)
                        except Exception:
                            pass
            except Exception:
                pass
        if getattr(self, "upstream", None) is not None:
            try:
                self.upstream.stop()
            except Exception:
                pass
        self.tmp.cleanup()

    def _enqueue(self, kind: str, *, stream: bool = False, path: str = "/v1/chat/completions",
                 messages=None, prompt: str = "") -> str:
        return enqueue_request(
            self.runtime, "default",
            {
                "kind": kind, "path": path,
                "model": "ide-gateway",
                "messages": messages or [{"role": "user", "content": prompt or "hi"}],
                "prompt": prompt or "hi",
                "stream": stream,
            },
            timeout_seconds=30, max_pending=8,
        )

    def _wait_status(self, request_id: str, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        path = request_path(self.runtime, "default", request_id)
        while time.time() < deadline:
            req = _read_json(path, None)
            if req and req.get("status") in ("completed", "failed", "expired"):
                return req
            time.sleep(0.1)
        return _read_json(path, None) or {}


    def test_responder_start_fails_fast_without_upstream(self):
        from plugins.ide_gateway.state import start_responder
        context = {"workspacePath": str(self.workspace), "effectiveMode": "full_access"}
        config = normalize_config({
            "default_api_key": "ideg_" + "a" * 24,
            "responder_enabled": True,
            "responder_autostart": True,
            "responder_upstream_type": "openai_compatible",
            "responder_upstream_base_url": "",
            "responder_upstream_model": "mock-model",
        }, context)
        result = start_responder({}, context, config)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "not_configured")
        self.assertIn("responder_upstream_base_url", result["message"])

    # 1. responder start creates running state
    def test_responder_start_creates_running_state(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "running")
        self.assertIsNotNone(state.get("pid"))
        self.assertGreater(state["pid"], 0)
        self.assertEqual(state["upstream_type"], "openai_compatible")
        self.assertEqual(state["upstream_model"], "mock-model")

    # 2. second start is idempotent
    def test_second_start_is_idempotent(self):
        from plugins.ide_gateway.state import start_responder, _load_responder_state
        context = {"workspacePath": str(self.workspace), "effectiveMode": "full_access"}
        config = normalize_config({}, context)
        config["responder_upstream_base_url"] = self.upstream.base_url
        config["responder_upstream_model"] = "mock-model"
        result = start_responder({"name": "default"}, context, config)
        self.assertTrue(result["ok"])
        self.assertTrue(result["already_running"])
        self.assertEqual(result["status"], "running")
        state = _load_responder_state(context, "default")
        self.assertEqual(state["status"], "running")
        # cleanup the duplicate handle returned; original proc still alive
        _kill = result.get("pid")
        if _kill and _kill != self.proc.pid:
            import subprocess as _sp
            _sp.run(["taskkill", "/T", "/F", "/PID", str(_kill)], check=False,
                     stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

    # 3. responder stop works
    def test_responder_stop_works(self):
        from plugins.ide_gateway.state import stop_responder
        context = {"workspacePath": str(self.workspace), "effectiveMode": "full_access"}
        result = stop_responder({"name": "default"}, context, {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "stopped")
        # The responder subprocess should be dead now; reap it so tearDown is clean.
        try:
            if self.proc.poll() is None:
                self.proc.wait(timeout=3)
        except Exception:
            pass

    # 4. responder claims queued /v1/responses request
    def test_responder_claims_responses_request(self):
        rid = self._enqueue("responses", path="/v1/responses", prompt="responses req")
        # Give the responder a moment to claim
        path = request_path(self.runtime, "default", rid)
        deadline = time.time() + 5
        while time.time() < deadline:
            req = _read_json(path, None)
            if req and req.get("status") in ("claimed", "completed", "failed"):
                self.assertIn(req["status"], ("claimed", "completed", "failed"))
                return
            time.sleep(0.1)
        self.fail("responder did not claim the responses request")

    # 5. responder completes /v1/responses non-stream
    def test_responder_completes_responses_non_stream(self):
        rid = self._enqueue("responses", stream=False, path="/v1/responses", prompt="hello")
        req = self._wait_status(rid)
        self.assertEqual(req.get("status"), "completed")
        content = (req.get("response") or {}).get("content") or ""
        self.assertIn("upstream answer", content)

    # 6. responder completes /v1/responses stream with full lifecycle
    def test_responder_completes_responses_stream(self):
        rid = self._enqueue("responses", stream=True, path="/v1/responses", prompt="stream me")
        req = self._wait_status(rid)
        self.assertEqual(req.get("status"), "completed")
        response = req.get("response") or {}
        chunks = response.get("stream_chunks")
        self.assertIsNotNone(chunks, "stream_chunks must be set for streamed replies")
        joined = "".join(chunks)
        self.assertIn("upstream", joined)

    # 7. responder completes /v1/chat/completions non-stream
    def test_responder_completes_chat_non_stream(self):
        rid = self._enqueue("chat", stream=False, path="/v1/chat/completions", prompt="hi")
        req = self._wait_status(rid)
        self.assertEqual(req.get("status"), "completed")
        content = (req.get("response") or {}).get("content") or ""
        self.assertIn("upstream answer", content)

    # 8. responder completes /v1/chat/completions stream with [DONE]-style chunks
    def test_responder_completes_chat_stream(self):
        rid = self._enqueue("chat", stream=True, path="/v1/chat/completions", prompt="stream")
        req = self._wait_status(rid)
        self.assertEqual(req.get("status"), "completed")
        chunks = (req.get("response") or {}).get("stream_chunks")
        self.assertIsNotNone(chunks)
        self.assertTrue(any("upstream" in c for c in chunks))

    # 9. upstream failure marks request failed with visible error
    def test_upstream_failure_marks_request_failed(self):
        # Stop the mock upstream so calls fail
        self.upstream.stop()
        rid = self._enqueue("chat", stream=False, path="/v1/chat/completions", prompt="hi")
        req = self._wait_status(rid, timeout=15)
        self.assertEqual(req.get("status"), "failed")
        err = req.get("error") or {}
        self.assertIn("message", err)
        self.assertEqual(err.get("code"), "responder_error")

    # 10. no API keys leak in status/logs
    def test_no_api_keys_leak_in_logs(self):
        # Enqueue and complete a request, then inspect logs.
        rid = self._enqueue("chat", stream=False, prompt="x")
        self._wait_status(rid)
        log_path = self.runtime / "logs" / "default.responder.log"
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("Bearer ", text)
            self.assertNotIn("ideg_", text)

    # 11. setup can enable responder autostart
    def test_setup_can_enable_responder_autostart(self):
        existing = {
            "responder_upstream_base_url": self.upstream.base_url,
            "responder_upstream_model": "mock-model",
        }
        answers = iter([
            "custom",  # setup preset
            "yes",     # endpoint autostart
            "8787",    # preferred port
            "ide-gateway",
            "yes",     # enable autonomous responder
            "yes",     # autostart responder
            "openai_compatible",
            self.upstream.base_url,
            "",        # upstream API key
            "mock-model",
            "8787",    # port range start
            "8899",    # port range end
            "300",     # gateway timeout
            "300",     # responder timeout
            "fallback",
            "",        # disabled tools
        ])
        original_input = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input
        def fake_input(prompt=""):
            try:
                return next(answers)
            except StopIteration:
                return ""
        if isinstance(__builtins__, dict):
            __builtins__["input"] = fake_input
        else:
            __builtins__.input = fake_input
        try:
            config = plugin_setup.collect_ide_gateway_config(existing)
        finally:
            if isinstance(__builtins__, dict):
                __builtins__["input"] = original_input
            else:
                __builtins__.input = original_input

        self.assertTrue(config["responder_enabled"])
        self.assertTrue(config["responder_autostart"])
        self.assertEqual(config["responder_upstream_type"], "openai_compatible")
        self.assertEqual(config["responder_upstream_base_url"], self.upstream.base_url)
        self.assertEqual(config["responder_upstream_model"], "mock-model")


    def test_plugin_local_config_validates_responder_settings(self):
        config = normalize_config({
            "responder_enabled": "yes",
            "responder_autostart": "true",
            "responder_upstream_type": "manual",
            "responder_request_timeout_seconds": 50,
            "responder_poll_interval_seconds": "0.5",
            "responder_max_concurrent_requests": 2,
        }, {"workspacePath": str(self.runtime)})
        self.assertTrue(config["responder_enabled"])
        self.assertTrue(config["responder_autostart"])
        self.assertEqual(config["responder_upstream_type"], "manual")
        self.assertEqual(config["responder_request_timeout_seconds"], 50)
        self.assertAlmostEqual(config["responder_poll_interval_seconds"], 0.5)
        self.assertEqual(config["responder_max_concurrent_requests"], 2)


if __name__ == "__main__":
    unittest.main()
