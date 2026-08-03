"""The transport guard and GZipMiddleware must be layered in one exact order.

Starlette makes the middleware added LAST the outermost one, so gzip has to be
registered after the guard to end up on the outside. When it was registered
before, the guard wrapped gzip instead: it received an already-compressed body,
rebuilt the response, and dropped content-encoding while doing so. Every response
above the gzip threshold then arrived at the client as binary labelled as JSON,
while short replies kept working. That looks like a flaky tunnel rather than a
layering mistake, which is what makes it worth a test.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from transport_guard import TransportGuardMiddleware

BIG = "x" * 20000
HEADERS = {"authorization": "Bearer unit-test", "accept-encoding": "gzip"}


def read_request(rpc_id=1):
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "big.txt"}},
    }


def build_app():
    async def endpoint(request):
        await request.body()
        return JSONResponse({"result": BIG})

    app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
    # Exactly as server.py registers them: guard first, gzip second, which puts
    # gzip on the outside.
    app.add_middleware(TransportGuardMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    return app


class GzipAndGuardTogether(unittest.TestCase):
    def test_a_large_response_is_compressed_and_still_parses(self):
        client = TestClient(build_app())
        response = client.post("/mcp", json=read_request(), headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        # httpx decodes transparently, so a stripped header would surface here as
        # a JSON error on binary content.
        self.assertEqual(json.loads(response.content)["result"], BIG)

    def test_a_replayed_large_response_is_also_intact(self):
        client = TestClient(build_app())
        first = client.post("/mcp", json=read_request(1), headers=HEADERS)
        second = client.post("/mcp", json=read_request(2), headers=HEADERS)
        self.assertEqual(second.headers.get("x-mcp-cache"), "hit")
        self.assertEqual(json.loads(first.content), json.loads(second.content))

    def test_a_client_without_gzip_support_gets_plain_json(self):
        client = TestClient(build_app())
        response = client.post(
            "/mcp",
            json=read_request(),
            headers={"authorization": "Bearer unit-test", "accept-encoding": "identity"},
        )
        self.assertIsNone(response.headers.get("content-encoding"))
        self.assertEqual(json.loads(response.content)["result"], BIG)


class MiddlewareRegistrationOrder(unittest.TestCase):
    """Assert the source order too: the runtime symptom is easy to misread."""

    def test_gzip_is_registered_after_the_guard(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        guard_at = source.index("app.add_middleware(TransportGuardMiddleware)")
        gzip_at = source.index("app.add_middleware(GZipMiddleware")
        self.assertLess(
            guard_at,
            gzip_at,
            "GZipMiddleware must be added after TransportGuardMiddleware so gzip is "
            "the outer layer and the guard sees uncompressed bodies",
        )

    def test_nothing_is_layered_between_them(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        guard_call = "app.add_middleware(TransportGuardMiddleware)"
        start = source.index(guard_call) + len(guard_call)
        end = source.index("app.add_middleware(GZipMiddleware")
        window = source[start:end]
        self.assertIsNone(
            re.search(r"add_middleware\(", window),
            "nothing may be layered between the guard and gzip",
        )


if __name__ == "__main__":
    unittest.main()
