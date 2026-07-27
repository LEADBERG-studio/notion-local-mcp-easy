import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_provider import queue as q


class IdeProviderQueueTests(unittest.TestCase):
    def test_enqueue_creates_pending_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=5,
                max_pending=8,
            )
            self.assertTrue(req_id.startswith("req_"))
            counts = q.request_counts(root, "ep1")
            self.assertEqual(counts["pending"], 1)

    def test_claim_marks_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=5,
                max_pending=8,
            )
            req = q.claim_next_request(root, "ep1", wait_timeout=1, request_timeout=5)
            self.assertIsNotNone(req)
            self.assertEqual(req["request_id"], req_id)
            self.assertEqual(req["status"], "claimed")

    def test_complete_request_sets_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=5,
                max_pending=8,
            )
            q.claim_next_request(root, "ep1", wait_timeout=1, request_timeout=5)
            result = q.complete_request(root, req_id, "hello", "stop", max_response_bytes=1024)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "completed")
            req_file = q._read_json(q.request_path(root, "ep1", req_id))
            self.assertEqual(req_file["status"], "completed")
            self.assertEqual(req_file["response"]["content"], "hello")
            self.assertEqual(req_file["response"]["finish_reason"], "stop")
            counts = q.request_counts(root, "ep1")
            self.assertEqual(counts["completed"], 1)

    def test_fail_request_sets_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=5,
                max_pending=8,
            )
            result = q.fail_request_by_id(root, req_id, "err", "boom")
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "failed")
            req_file = q._read_json(q.request_path(root, "ep1", req_id))
            self.assertEqual(req_file["error"]["code"], "err")
            self.assertEqual(req_file["error"]["message"], "boom")

    def test_expired_request_not_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=1,
                max_pending=8,
            )
            time.sleep(1.5)
            req = q.claim_next_request(root, "ep1", wait_timeout=1, request_timeout=5)
            self.assertIsNone(req)

    def test_complete_unknown_request_returns_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = q.complete_request(root, "req_nonexistent", "x", None, max_response_bytes=1024)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "not_found")

    def test_complete_already_completed_returns_not_claimable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=5,
                max_pending=8,
            )
            q.claim_next_request(root, "ep1", wait_timeout=1, request_timeout=5)
            first = q.complete_request(root, req_id, "hello", "stop", max_response_bytes=1024)
            self.assertTrue(first["ok"])
            second = q.complete_request(root, req_id, "hello", "stop", max_response_bytes=1024)
            self.assertFalse(second["ok"])
            self.assertEqual(second["status"], "not_claimable")
            self.assertEqual(second["current"], "completed")

    def test_oversize_response_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=5,
                max_pending=8,
            )
            q.claim_next_request(root, "ep1", wait_timeout=1, request_timeout=5)
            result = q.complete_request(root, req_id, "x" * 10, None, max_response_bytes=4)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "too_large")

    def test_queue_full_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for _ in range(2):
                q.enqueue_request(
                    root,
                    "ep1",
                    {"model": "ide-provider", "messages": []},
                    timeout_seconds=5,
                    max_pending=2,
                )
            with self.assertRaisesRegex(ValueError, "queue full"):
                q.enqueue_request(
                    root,
                    "ep1",
                    {"model": "ide-provider", "messages": []},
                    timeout_seconds=5,
                    max_pending=2,
                )

    def test_two_waiters_cannot_claim_same_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=60,
                max_pending=8,
            )
            results: list = []

            def claim():
                results.append(q.claim_next_request(root, "ep1", wait_timeout=2, request_timeout=5))

            threads = [threading.Thread(target=claim), threading.Thread(target=claim)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            claims = [r for r in results if r is not None]
            self.assertEqual(len(claims), 1)

    def test_claimed_request_expires_on_wait_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req_id = q.enqueue_request(
                root,
                "ep1",
                {"model": "ide-provider", "messages": []},
                timeout_seconds=1,
                max_pending=8,
            )
            req = q.claim_next_request(root, "ep1", wait_timeout=1, request_timeout=1)
            self.assertIsNotNone(req)
            self.assertEqual(req["request_id"], req_id)
            time.sleep(1.5)
            result = q.wait_for_completion(root, "ep1", req_id, timeout=1)
            self.assertEqual(result["status"], "timeout")
            req_file = q._read_json(q.request_path(root, "ep1", req_id))
            self.assertEqual(req_file["status"], "expired")


if __name__ == "__main__":
    unittest.main()

