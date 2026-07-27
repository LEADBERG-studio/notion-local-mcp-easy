import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_gateway.queue import claim_next_request, complete_request


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class IdeGatewayWorkerStreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ["endpoints", "queues", "logs", "pids", "registry"]:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.token = "ideg_test-token-1234567890"
        self.state_path = self.root / "endpoints" / "default.json"
        self.log_path = self.root / "logs" / "default.log"
        self.state_path.write_text(json.dumps({
            "name": "default",
            "status": "starting",
            "host": "127.0.0.1",
            "port": self.port,
            "base_url": f"http://127.0.0.1:{self.port}/v1",
            "model_id": "ide-gateway",
            "token": self.token,
            "pid": None,
            "request_timeout_seconds": 10,
            "max_request_bytes": 1024 * 1024,
            "max_response_bytes": 1024 * 1024,
            "max_pending_requests": 8,
            "embeddings_mode": "fallback",
            "embeddings_dim": 1536,
            "disabled_tools": "",
            "log_path": str(self.log_path),
        }), encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(PROJECT / "plugins" / "ide_gateway" / "worker.py"), "--state", str(self.state_path)],
            cwd=str(PROJECT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=0.5) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                time.sleep(0.1)
        self.fail("worker did not become healthy")

    def tearDown(self):
        if getattr(self, "proc", None) is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.tmp.cleanup()

    def test_chat_stream_completion_ends_with_done(self):
        result = {}

        def client():
            payload = json.dumps({
                "model": "ide-gateway",
                "stream": True,
                "messages": [{"role": "user", "content": "hello"}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/v1/chat/completions",
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result["status"] = resp.status
                result["content_type"] = resp.headers.get("Content-Type", "")
                result["body"] = resp.read().decode("utf-8")

        thread = threading.Thread(target=client)
        thread.start()

        req = claim_next_request(self.root, "default", 5, request_timeout=10)
        self.assertIsNotNone(req)
        complete = complete_request(
            self.root,
            req["request_id"],
            "visible gateway response",
            finish_reason="stop",
            max_response_bytes=1024 * 1024,
        )
        self.assertTrue(complete["ok"], complete)

        thread.join(timeout=15)
        self.assertFalse(thread.is_alive(), "client did not finish")
        self.assertEqual(result.get("status"), 200)
        self.assertIn("text/event-stream", result.get("content_type", ""))
        body = result.get("body", "")
        self.assertIn("visible gateway response", body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"), body)


if __name__ == "__main__":
    unittest.main()
