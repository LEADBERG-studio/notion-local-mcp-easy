"""Transport guard: dedup, cache, admission control (2.4.3).

The guard sits in front of the tool layer, so its correctness is a security
property and not only a performance one. The most important test here is that a
cached success is never replayed to a different credential.
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import transport_guard  # noqa: E402
from transport_guard import TransportGuardMiddleware, describe_request  # noqa: E402


def body(method: str, name: str = "", rpc_id: int = 1) -> bytes:
    payload: dict = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if name:
        payload["params"] = {"name": name, "arguments": {}}
    return json.dumps(payload).encode("utf-8")


class FakeURL:
    def __init__(self, path: str) -> None:
        self.path = path


class FakeRequest:
    def __init__(self, payload: bytes, *, token: str = "good", path: str = "/mcp") -> None:
        self._payload = payload
        self.method = "POST"
        self.url = FakeURL(path)
        self.headers = {"authorization": f"Bearer {token}"}

    async def body(self) -> bytes:
        return self._payload


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.body = payload
        self.status_code = status
        self.headers: dict[str, str] = {"content-type": "application/json"}


def build_guard() -> TransportGuardMiddleware:
    return TransportGuardMiddleware(app=lambda *args, **kwargs: None)


class ClassificationTests(unittest.TestCase):
    def test_reads_are_cacheable(self):
        rpc_id, label, ttl = describe_request(body("tools/call", "read_file"))
        self.assertEqual(rpc_id, "1")
        self.assertEqual(label, "read_file")
        self.assertGreater(ttl, 0)

    def test_mutations_are_never_cacheable(self):
        for name in ("write_file", "edit_file", "delete_file", "run_command", "mysql_execute"):
            _id, _label, ttl = describe_request(body("tools/call", name))
            self.assertEqual(ttl, 0.0, name)

    def test_protocol_chatter_is_cacheable(self):
        for method in ("tools/list", "initialize"):
            _id, _label, ttl = describe_request(body(method))
            self.assertGreater(ttl, 0, method)

    def test_batches_are_never_cached(self):
        payload = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]).encode()
        _id, label, ttl = describe_request(payload)
        self.assertEqual(label, "batch")
        self.assertEqual(ttl, 0.0)

    def test_garbage_is_not_classified(self):
        self.assertEqual(describe_request(b"not json"), ("", "", 0.0))


class CredentialIsolationTests(unittest.TestCase):
    """A cache that ignores credentials is an authentication bypass."""

    def test_two_credentials_never_share_a_cache_entry(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        good = FakeRequest(payload, token="good")
        bad = FakeRequest(payload, token="stolen")
        self.assertNotEqual(guard._key(good, payload, "1"), guard._key(bad, payload, "1"))

    def test_the_same_credential_reuses_its_entry(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        first = FakeRequest(payload, token="good")
        second = FakeRequest(payload, token="good")
        self.assertEqual(guard._key(first, payload, "1"), guard._key(second, payload, "1"))

    def test_the_credential_itself_is_not_stored_in_the_key(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        key = guard._key(FakeRequest(payload, token="super-secret-token"), payload, "1")
        self.assertNotIn("super-secret-token", key)


class CachingTests(unittest.TestCase):
    def setUp(self):
        transport_guard.STATS.__init__()

    def test_an_identical_read_is_served_from_cache(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(b'{"ok":true}')

        async def scenario():
            first = await guard.dispatch(FakeRequest(payload), call_next)
            second = await guard.dispatch(FakeRequest(payload), call_next)
            return first, second

        first, second = asyncio.run(scenario())
        self.assertEqual(len(calls), 1)
        self.assertEqual(second.body, b'{"ok":true}')
        self.assertEqual(second.headers["x-mcp-cache"], "hit")
        self.assertIsNone(first.headers.get("x-mcp-cache"))

    def test_a_repeated_read_hits_even_with_a_new_rpc_id(self):
        """Regression: the read cache used to be keyed on the JSON-RPC id.

        Real clients increment that id on every call, so the cache stored
        everything and served nothing.
        """
        guard = build_guard()
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(b'{"ok":true}')

        async def scenario():
            await guard.dispatch(FakeRequest(body("tools/call", "read_file", rpc_id=1)), call_next)
            return await guard.dispatch(
                FakeRequest(body("tools/call", "read_file", rpc_id=99)), call_next
            )

        second = asyncio.run(scenario())
        self.assertEqual(len(calls), 1)
        self.assertEqual(second.headers["x-mcp-cache"], "hit")

    def test_a_mutation_invalidates_cached_reads(self):
        """Regression: an agent could write a file and read back stale content."""
        guard = build_guard()
        answers = [b'{"result":"before"}', b'{"result":"written"}', b'{"result":"after"}']
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(answers[min(len(calls) - 1, len(answers) - 1)])

        async def scenario():
            first = await guard.dispatch(
                FakeRequest(body("tools/call", "read_file", rpc_id=1)), call_next
            )
            await guard.dispatch(
                FakeRequest(body("tools/call", "write_file", rpc_id=2)), call_next
            )
            third = await guard.dispatch(
                FakeRequest(body("tools/call", "read_file", rpc_id=3)), call_next
            )
            return first, third

        first, third = asyncio.run(scenario())
        self.assertEqual(first.body, b'{"result":"before"}')
        self.assertEqual(third.body, b'{"result":"after"}')
        self.assertEqual(len(calls), 3)

    def test_reads_by_different_credentials_stay_separate(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        self.assertNotEqual(
            guard._read_key(FakeRequest(payload, token="a"), payload),
            guard._read_key(FakeRequest(payload, token="b"), payload),
        )

    def test_a_resent_mutation_is_replayed_not_executed_twice(self):
        guard = build_guard()
        payload = body("tools/call", "write_file", rpc_id=7)
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(b'{"ok":true}')

        async def scenario():
            await guard.dispatch(FakeRequest(payload), call_next)
            return await guard.dispatch(FakeRequest(payload), call_next)

        second = asyncio.run(scenario())
        self.assertEqual(len(calls), 1)
        self.assertEqual(second.headers["x-mcp-cache"], "replay")

    def test_a_different_id_runs_again(self):
        guard = build_guard()
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(b'{"ok":true}')

        async def scenario():
            await guard.dispatch(
                FakeRequest(body("tools/call", "write_file", rpc_id=1)), call_next
            )
            await guard.dispatch(
                FakeRequest(body("tools/call", "write_file", rpc_id=2)), call_next
            )

        asyncio.run(scenario())
        self.assertEqual(len(calls), 2)

    def test_errors_are_not_cached(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        calls = []

        async def failing(request):
            calls.append(1)
            return FakeResponse(b'{"error":true}', status=500)

        async def scenario():
            await guard.dispatch(FakeRequest(payload), failing)
            await guard.dispatch(FakeRequest(payload), failing)

        asyncio.run(scenario())
        self.assertEqual(len(calls), 2)

    def test_concurrent_duplicates_are_joined_not_repeated(self):
        """A tunnel hiccup makes clients resend. Do the work once."""
        guard = build_guard()
        payload = body("tools/call", "grep_files")
        started = []

        async def slow(request):
            started.append(1)
            await asyncio.sleep(0.05)
            return FakeResponse(b'{"ok":true}')

        async def scenario():
            return await asyncio.gather(
                guard.dispatch(FakeRequest(payload), slow),
                guard.dispatch(FakeRequest(payload), slow),
                guard.dispatch(FakeRequest(payload), slow),
            )

        results = asyncio.run(scenario())
        self.assertEqual(len(started), 1)
        self.assertEqual({response.body for response in results}, {b'{"ok":true}'})
        self.assertGreaterEqual(transport_guard.STATS.joined, 1)

    def test_non_mcp_paths_are_left_alone(self):
        guard = build_guard()
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(b"ok")

        request = FakeRequest(body("tools/list"), path="/health")
        asyncio.run(guard.dispatch(request, call_next))
        asyncio.run(guard.dispatch(request, call_next))
        self.assertEqual(len(calls), 2)

    def test_oversized_responses_are_not_kept(self):
        guard = build_guard()
        payload = body("tools/call", "read_file")
        big = b"x" * (transport_guard.CACHE_MAX_BODY_BYTES + 10)
        calls = []

        async def call_next(request):
            calls.append(1)
            return FakeResponse(big)

        async def scenario():
            await guard.dispatch(FakeRequest(payload), call_next)
            await guard.dispatch(FakeRequest(payload), call_next)

        asyncio.run(scenario())
        self.assertEqual(len(calls), 2)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        transport_guard.STATS.__init__()

    def test_overload_is_refused_with_retry_after(self):
        guard = build_guard()
        guard._semaphore = asyncio.Semaphore(0)

        async def call_next(request):
            return FakeResponse(b"never")

        with mock.patch.object(transport_guard, "ADMISSION_WAIT_SECONDS", 1):
            response = asyncio.run(
                guard.dispatch(FakeRequest(body("tools/call", "write_file")), call_next)
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.headers["Retry-After"], str(transport_guard.RETRY_AFTER_SECONDS)
        )
        self.assertGreaterEqual(transport_guard.STATS.rejected, 1)

    def test_stats_are_reportable(self):
        snapshot = transport_guard.stats()
        for key in ("served", "joinedDuplicates", "replayedRetries", "cacheHits", "limits"):
            self.assertIn(key, snapshot)


if __name__ == "__main__":
    unittest.main()
