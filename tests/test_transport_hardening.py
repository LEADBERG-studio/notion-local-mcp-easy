"""Transport hardening and plugin output guards (2.4.2).

The symptom being fixed: bursts of small calls and single large responses both
killed the connection through a tunnel. Two independent causes, two guards.
"""

import os
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("MCP_TOKEN", "unit-test-token")
os.environ.setdefault("MCP_BASE_DIR", str(PROJECT))
os.environ.setdefault("MCP_ALLOW_COMMANDS", "0")
os.environ.setdefault("MCP_AUTH_MODE", "legacy")

import plugin_runtime  # noqa: E402
import server  # noqa: E402
from plugins import db_shared  # noqa: E402


class KeepAliveTests(unittest.TestCase):
    """Uvicorn defaults to a 5 second keep-alive.

    A client reusing its connection for a burst of small calls can send on a
    socket the server is closing, and the relay answers 502. Holding the
    connection open is what removes that race.
    """

    def test_keep_alive_is_far_above_the_uvicorn_default(self):
        self.assertGreaterEqual(server.KEEP_ALIVE_SECONDS, 60)

    def test_concurrency_is_bounded_so_load_is_refused_not_queued_invisibly(self):
        self.assertGreaterEqual(server.LIMIT_CONCURRENCY, 8)
        self.assertLessEqual(server.LIMIT_CONCURRENCY, 4096)

    def test_backlog_and_header_room_are_sane(self):
        self.assertGreaterEqual(server.SOCKET_BACKLOG, 128)
        self.assertGreaterEqual(server.H11_MAX_INCOMPLETE_EVENT_SIZE, 16 * 1024)

    def test_every_knob_is_overridable_from_the_environment(self):
        for name in (
            "MCP_KEEP_ALIVE_SECONDS",
            "MCP_LIMIT_CONCURRENCY",
            "MCP_SOCKET_BACKLOG",
            "MCP_MAX_HEADER_BYTES",
            "MCP_GZIP_MIN_SIZE",
        ):
            self.assertIn(name, Path(server.__file__).read_text(encoding="utf-8"))


class CoreClipTests(unittest.TestCase):
    def test_core_output_is_clipped_with_an_actionable_message(self):
        clipped = server._clip("x" * (server.MAX_OUTPUT_CHARS + 500))
        self.assertLess(len(clipped), server.MAX_OUTPUT_CHARS + 400)
        self.assertIn("truncated", clipped)
        self.assertIn("offset", clipped)

    def test_small_output_is_untouched(self):
        self.assertEqual(server._clip("hello"), "hello")

    def test_empty_and_none_are_explicit(self):
        self.assertEqual(server._clip(""), "(empty result)")
        self.assertEqual(server._clip(None), "(no output)")


class PluginClipTests(unittest.TestCase):
    """Plugin output used to reach the transport unclipped."""

    def test_plugin_output_is_capped(self):
        import asyncio

        def handler(**kwargs):
            return "y" * (plugin_runtime.PLUGIN_OUTPUT_CHAR_LIMIT + 5_000)

        wrapped = plugin_runtime.clip_plugin_output(handler, plugin_id="demo", name="demo_tool")
        result = asyncio.run(wrapped())
        self.assertLessEqual(len(result), plugin_runtime.PLUGIN_OUTPUT_CHAR_LIMIT + 300)
        self.assertIn("demo_tool output truncated", result)

    def test_short_plugin_output_is_untouched(self):
        import asyncio

        wrapped = plugin_runtime.clip_plugin_output(
            lambda **kwargs: "ok", plugin_id="demo", name="demo_tool"
        )
        self.assertEqual(asyncio.run(wrapped()), "ok")

    def test_non_string_results_pass_through(self):
        import asyncio

        wrapped = plugin_runtime.clip_plugin_output(
            lambda **kwargs: {"rows": [1, 2]}, plugin_id="demo", name="demo_tool"
        )
        self.assertEqual(asyncio.run(wrapped()), {"rows": [1, 2]})


class DbRowCapTests(unittest.TestCase):
    """A broad SELECT is the easiest way to push megabytes through a tunnel."""

    def test_rows_are_capped_and_the_cut_is_reported(self):
        rows = [{"id": str(index)} for index in range(500)]
        payload = db_shared.cap_rows(rows, 10)
        self.assertEqual(payload["rowCount"], 10)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["totalRowsSeen"], 500)
        self.assertIn("LIMIT", payload["hint"])

    def test_a_small_result_is_not_marked_truncated(self):
        payload = db_shared.cap_rows([{"id": "1"}], 10)
        self.assertNotIn("truncated", payload)

    def test_huge_cells_are_trimmed(self):
        payload = db_shared.cap_rows([{"blob": "z" * 5_000}], 10)
        self.assertLess(len(payload["rows"][0]["blob"]), 1_000)
        self.assertIn("chars]", payload["rows"][0]["blob"])

    def test_row_limit_is_validated_and_ceilinged(self):
        self.assertEqual(db_shared.resolve_row_limit({}), db_shared.DEFAULT_ROW_LIMIT)
        self.assertEqual(db_shared.resolve_row_limit({"row_limit": "5"}), 5)
        self.assertEqual(
            db_shared.resolve_row_limit({"row_limit": 10 ** 9}), db_shared.MAX_ROW_LIMIT
        )
        for bad in ("abc", "0", "-3"):
            with self.assertRaises(ValueError, msg=bad):
                db_shared.resolve_row_limit({"row_limit": bad})


if __name__ == "__main__":
    unittest.main()
