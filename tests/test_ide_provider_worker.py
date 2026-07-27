import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_provider.queue import claim_next_request, complete_request
from plugins.ide_provider.state import (
    _load_endpoint_state,
    endpoint_status,
    normalize_config,
    rotate_token,
    show_config,
    start_endpoint,
    stop_endpoint,
)


class IdeProviderWorkerTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self._tempdir.name)
        (self.runtime_root / "endpoints").mkdir(parents=True)
        (self.runtime_root / "queues").mkdir(parents=True)
        (self.runtime_root / "logs").mkdir(parents=True)
        self.process = None

    def tearDown(self):
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
            except Exception:
                pass
        self._tempdir.cleanup()

    def _free_port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _request(self, url, token=None, data=None, method="GET"):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        return req

    def _start_worker(self, port, token, state):
        state_path = self.runtime_root / "endpoints" / "ep1.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(PROJECT / "plugins" / "ide_provider" / "worker.py"), "--state", str(state_path)],
            cwd=str(PROJECT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health_url = f"http://127.0.0.1:{port}/health"
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self._request(health_url), timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                if self.process.poll() is not None:
                    self.fail(f"worker exited with code {self.process.returncode}")
                time.sleep(0.1)
        else:
            self.fail("worker did not become healthy")

    def _make_state(self, port, token, request_timeout_seconds=300):
        return {
            "name": "ep1",
            "host": "127.0.0.1",
            "port": port,
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model_id": "test-model",
            "token": token,
            "status": "running",
            "request_timeout_seconds": request_timeout_seconds,
            "max_request_bytes": 1024 * 1024,
            "max_response_bytes": 1024 * 1024,
            "max_pending_requests": 8,
            "log_path": str(self.runtime_root / "logs" / "ep1.log"),
            "requests_total": 0,
            "responses_total": 0,
            "errors_total": 0,
            "last_error": "",
        }

    def test_health_works_unauthenticated(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)
        req = self._request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as response:
            self.assertEqual(response.status, 200)
            body = json.loads(response.read())
            self.assertTrue(body["ok"])
            self.assertEqual(body["name"], "ep1")
            self.assertEqual(body["status"], "running")
            self.assertIn("model_attached", body)
            self.assertIn("pending_requests", body)

    def test_models_requires_token(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self._request(f"http://127.0.0.1:{port}/v1/models"), timeout=2)
        self.assertEqual(caught.exception.code, 401)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_models_returns_model_with_token(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)
        req = self._request(f"http://127.0.0.1:{port}/v1/models", token=token)
        with urllib.request.urlopen(req, timeout=2) as response:
            self.assertEqual(response.status, 200)
            body = json.loads(response.read())
            self.assertEqual(body["data"][0]["id"], "test-model")

    def test_chat_completions_no_waiting_model_times_out(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token, request_timeout_seconds=2)
        self._start_worker(port, token, state)
        payload = json.dumps({
            "model": "ide-provider",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        req = self._request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            token=token,
            data=payload,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 504)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "model_timeout")

    def test_chat_completions_full_flow_with_fake_responder(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token, request_timeout_seconds=10)
        self._start_worker(port, token, state)

        responder_result = {}

        def responder():
            req = claim_next_request(self.runtime_root, "ep1", wait_timeout=10, request_timeout=10)
            responder_result["req"] = req
            if req:
                complete_request(
                    self.runtime_root,
                    req["request_id"],
                    "Hello back",
                    "stop",
                    max_response_bytes=1024,
                )

        thread = threading.Thread(target=responder)
        thread.start()

        payload = json.dumps({
            "model": "ide-provider",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        req = self._request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            token=token,
            data=payload,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                self.assertEqual(response.status, 200)
                body = json.loads(response.read())
                self.assertEqual(body["choices"][0]["message"]["content"], "Hello back")
                self.assertEqual(body["choices"][0]["finish_reason"], "stop")
                self.assertTrue(body["id"].startswith("chatcmpl-"))
        finally:
            thread.join(timeout=5)

        self.assertIsNotNone(responder_result["req"])

    def test_streaming_rejected(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)
        payload = json.dumps({
            "model": "ide-provider",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }).encode("utf-8")
        req = self._request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            token=token,
            data=payload,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "streaming_not_supported")

    def test_oversized_body_rejected(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        state["max_request_bytes"] = 128
        self._start_worker(port, token, state)
        big_content = "x" * 200
        payload = json.dumps({
            "model": "ide-provider",
            "messages": [{"role": "user", "content": big_content}],
        }).encode("utf-8")
        req = self._request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            token=token,
            data=payload,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 413)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "payload_too_large")

    def test_invalid_json_rejected(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)
        payload = b"not json{broken"
        req = self._request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            token=token,
            data=payload,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_bad_token_rejected(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)
        payload = json.dumps({
            "model": "ide-provider",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        req = self._request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            token="wrong-token",
            data=payload,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 401)

    def test_worker_bind_failure_marks_failed(self):
        port = self._free_port()
        token = "test-token"
        state = self._make_state(port, token)
        self._start_worker(port, token, state)

        state2 = self._make_state(port, token)
        state2["name"] = "ep2"
        state2["log_path"] = str(self.runtime_root / "logs" / "ep2.log")
        state2_path = self.runtime_root / "endpoints" / "ep2.json"
        state2_path.write_text(json.dumps(state2), encoding="utf-8")

        process2 = subprocess.Popen(
            [sys.executable, str(PROJECT / "plugins" / "ide_provider" / "worker.py"), "--state", str(state2_path)],
            cwd=str(PROJECT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                if process2.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(process2.poll(), "second worker should exit due to bind failure")
            failed_state = json.loads(state2_path.read_text(encoding="utf-8"))
            self.assertEqual(failed_state["status"], "failed")
        finally:
            if process2.poll() is None:
                process2.terminate()
                try:
                    process2.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process2.kill()
                    process2.wait(timeout=5)

    def test_stop_endpoint_kills_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            ctx = {"workspacePath": str(workspace), "effectiveMode": "full_access"}
            config = normalize_config({})
            port = self._free_port()
            result = start_endpoint({"port": port, "model_id": "test-model"}, ctx, config)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "running")
            try:
                result2 = stop_endpoint({"name": "default"}, ctx, config)
                self.assertTrue(result2["ok"])
                self.assertEqual(result2["status"], "stopped")
            finally:
                try:
                    stop_endpoint({"name": "default"}, ctx, config)
                except Exception:
                    pass
            state = _load_endpoint_state(ctx, "default")
            self.assertIsNotNone(state)
            self.assertEqual(state["status"], "stopped")

    def test_rotate_token_changes_token(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            ctx = {"workspacePath": str(workspace), "effectiveMode": "full_access"}
            config = normalize_config({})
            port = self._free_port()
            result = start_endpoint({"name": "ep1", "port": port, "model_id": "test-model"}, ctx, config)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "running")
            original_token = result["api_key"]
            try:
                rotate_result = rotate_token({"name": "ep1"}, ctx, config)
                self.assertTrue(rotate_result["ok"])
                new_token = rotate_result["token"]
                self.assertNotEqual(new_token, original_token)

                req_new = self._request(f"http://127.0.0.1:{port}/v1/models", token=new_token)
                with urllib.request.urlopen(req_new, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    body = json.loads(response.read())
                    self.assertEqual(body["data"][0]["id"], "test-model")

                req_old = self._request(f"http://127.0.0.1:{port}/v1/models", token=original_token)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(req_old, timeout=2)
                self.assertEqual(caught.exception.code, 401)
            finally:
                stop_endpoint({"name": "ep1"}, ctx, config)

    def test_show_config_redacts_token_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            ctx = {"workspacePath": str(workspace), "effectiveMode": "full_access"}
            config = normalize_config({})
            port = self._free_port()
            result = start_endpoint({"name": "ep1", "port": port, "model_id": "test-model"}, ctx, config)
            self.assertTrue(result["ok"])
            try:
                result_default = show_config({"name": "ep1"}, ctx, config)
                self.assertTrue(result_default["ok"])
                self.assertEqual(result_default["api_key"], "***")

                result_secret = show_config({"name": "ep1", "include_secret": True}, ctx, config)
                self.assertTrue(result_secret["ok"])
                self.assertEqual(result_secret["api_key"], result["api_key"])
            finally:
                stop_endpoint({"name": "ep1"}, ctx, config)

    def test_endpoint_status_reports_running(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            ctx = {"workspacePath": str(workspace), "effectiveMode": "full_access"}
            config = normalize_config({})
            port = self._free_port()
            result = start_endpoint({"name": "ep1", "port": port, "model_id": "test-model"}, ctx, config)
            self.assertTrue(result["ok"])
            try:
                status = endpoint_status({"name": "ep1"}, ctx, config)
                self.assertTrue(status["ok"])
                self.assertEqual(len(status["endpoints"]), 1)
                self.assertEqual(status["endpoints"][0]["status"], "running")
                self.assertEqual(status["endpoints"][0]["name"], "ep1")
            finally:
                stop_endpoint({"name": "ep1"}, ctx, config)

    def test_start_endpoint_port_zero_auto_picks_free_port(self):
        context = {
            "effectiveMode": "full_access",
            "workspacePath": str(self.runtime_root),
        }
        config = normalize_config({
            "port_range": [self._free_port(), self._free_port()],
            "request_timeout_seconds": 5,
        })
        low = min(config["port_range"])
        high = max(config["port_range"])
        config["port_range"] = [low, high]
        result = start_endpoint({"name": "zeroport", "port": 0}, context, config)
        try:
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "running")
            self.assertGreaterEqual(int(result["base_url"].split(":")[-1].split("/")[0]), low)
            self.assertLessEqual(int(result["base_url"].split(":")[-1].split("/")[0]), high)
        finally:
            stop_endpoint({"name": "zeroport"}, context, config)

    def test_already_running_returns_already_status(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            ctx = {"workspacePath": str(workspace), "effectiveMode": "full_access"}
            config = normalize_config({})
            port = self._free_port()
            result1 = start_endpoint({"name": "ep1", "port": port, "model_id": "test-model"}, ctx, config)
            self.assertTrue(result1["ok"])
            try:
                result2 = start_endpoint({"name": "ep1", "port": port, "model_id": "test-model"}, ctx, config)
                self.assertTrue(result2["ok"])
                self.assertEqual(result2["status"], "already_running")
            finally:
                stop_endpoint({"name": "ep1"}, ctx, config)


if __name__ == "__main__":
    unittest.main()
