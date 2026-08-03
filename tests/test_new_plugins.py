"""MySQL and remote-subagent plugins (2.4.2)."""

import io
import json as _json
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import plugins.mysql.plugin as mysql  # noqa: E402
import plugins.subagent.plugin as subagent  # noqa: E402
from plugins import db_shared  # noqa: E402

MYSQL_CONFIG = {
    "connections": [
        {"name": "main", "database": "app", "user": "root", "password_env": "APP_DB_PW"}
    ]
}
CTX = {"pluginConfig": MYSQL_CONFIG, "effectiveMode": "read_only", "workspacePath": str(PROJECT)}
TRUSTED = {**CTX, "effectiveMode": "full_access"}


class MysqlConfigTests(unittest.TestCase):
    def test_config_requires_a_named_database(self):
        with self.assertRaises(ValueError):
            mysql.validate_config({"connections": [{"name": "x"}]}, CTX)
        with self.assertRaises(ValueError):
            mysql.validate_config({"connections": []}, CTX)

    def test_duplicate_aliases_are_refused(self):
        with self.assertRaises(ValueError):
            mysql.validate_config(
                {"connections": [{"name": "a", "database": "d"}, {"name": "a", "database": "e"}]},
                CTX,
            )

    def test_list_connections_needs_no_database(self):
        self.assertEqual(
            mysql.invoke("mysql_list_connections", {}, CTX), {"connections": ["main"]}
        )

    def test_unknown_alias_is_explicit(self):
        with self.assertRaises(ValueError):
            mysql.invoke("mysql_query", {"connection": "nope", "sql": "SELECT 1"}, CTX)

    def test_healthcheck_never_returns_a_password(self):
        payload = mysql.healthcheck(CTX)
        self.assertEqual(payload["provider"], "mysql")
        self.assertIn("password_envPresent", repr(payload))


class MysqlSafetyTests(unittest.TestCase):
    def test_query_refuses_write_statements(self):
        for sql in (
            "DELETE FROM users",
            "drop table users",
            "UPDATE users SET a=1",
            "  insert into users values (1)",
            "TRUNCATE users",
        ):
            with self.assertRaises(ValueError, msg=sql):
                mysql.invoke("mysql_query", {"connection": "main", "sql": sql}, TRUSTED)

    def test_query_refuses_stacked_statements(self):
        with self.assertRaises(ValueError):
            mysql.invoke(
                "mysql_query", {"connection": "main", "sql": "SELECT 1; SELECT 2"}, TRUSTED
            )

    def test_execute_requires_full_access(self):
        with self.assertRaises(ValueError) as caught:
            mysql.invoke("mysql_execute", {"connection": "main", "sql": "DELETE FROM t"}, CTX)
        self.assertIn("full_access", str(caught.exception))

    def test_query_caps_rows(self):
        rows = "id\n" + "\n".join(str(index) for index in range(500))
        with mock.patch.object(mysql, "run_mysql_sql", return_value=rows):
            result = mysql.invoke(
                "mysql_query",
                {"connection": "main", "sql": "SELECT id FROM t", "row_limit": 5},
                CTX,
            )
        self.assertEqual(result["rowCount"], 5)
        self.assertTrue(result["truncated"])

    def test_password_is_never_placed_on_the_command_line(self):
        """argv is world-readable, so a password there is a leak."""
        with mock.patch.dict("os.environ", {"APP_DB_PW": "sup3rs3cret"}):
            args, temp_path = db_shared.mysql_defaults_file({"password_env": "APP_DB_PW"})
            try:
                command = db_shared.mysql_cli_args({"database": "app"}, "SELECT 1", args)
                self.assertNotIn("sup3rs3cret", " ".join(command))
                self.assertTrue(
                    any(item.startswith("--defaults-extra-file=") for item in command)
                )
                self.assertIn("sup3rs3cret", Path(temp_path).read_text(encoding="utf-8"))
            finally:
                if temp_path is not None:
                    Path(temp_path).unlink(missing_ok=True)

    def test_a_missing_password_env_is_reported(self):
        with self.assertRaises(ValueError):
            db_shared.mysql_defaults_file({"password_env": "DEFINITELY_NOT_SET_XYZ"})


SUB_CONFIG = {
    "base_url": "https://models.example.com/v1",
    "model": "secret-model-x",
    "api_key": "sk-supersecret",
    "reply_char_limit": 100,
}
SUB_CTX = {"pluginConfig": SUB_CONFIG}


def fake_reply(text, usage=None):
    payload = {"choices": [{"message": {"content": text}}]}
    if usage:
        payload["usage"] = usage
    return payload


def urlopen_returning(payload):
    response = mock.MagicMock()
    response.read.return_value = _json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    return mock.patch.object(subagent.urllib.request, "urlopen", return_value=response)


class SubagentSecrecyTests(unittest.TestCase):
    """The whole point: answers travel back, the way to reach them does not."""

    def setUp(self):
        subagent._SESSIONS.clear()

    def test_status_hides_endpoint_key_and_model(self):
        status = subagent.invoke("subagent_status", {}, SUB_CTX)
        text = repr(status)
        self.assertNotIn("models.example.com", text)
        self.assertNotIn("sk-supersecret", text)
        self.assertNotIn("secret-model-x", text)
        self.assertTrue(status["configured"])
        self.assertTrue(status["keyPresent"])

    def test_model_is_only_revealed_when_explicitly_allowed(self):
        status = subagent.invoke(
            "subagent_status", {}, {"pluginConfig": {**SUB_CONFIG, "expose_model": True}}
        )
        self.assertEqual(status["model"], "secret-model-x")

    def test_a_leaky_remote_reply_is_redacted(self):
        leak = "use https://models.example.com/v1 with sk-supersecret on secret-model-x"
        cleaned = subagent._redact(leak, subagent._target(SUB_CONFIG))
        self.assertNotIn("models.example.com", cleaned)
        self.assertNotIn("sk-supersecret", cleaned)
        self.assertNotIn("secret-model-x", cleaned)

    def test_an_unconfigured_plugin_says_so_without_breaking_startup(self):
        self.assertEqual(subagent.validate_config({}, {}), {})
        self.assertFalse(subagent.healthcheck({"pluginConfig": {}})["configured"])
        with self.assertRaises(ValueError):
            subagent.invoke("subagent_ask", {"prompt": "hi"}, {"pluginConfig": {}})

    def test_a_relative_base_url_is_refused(self):
        with self.assertRaises(ValueError):
            subagent._target({"base_url": "models.example.com", "model": "m"})

    def test_a_missing_key_env_is_reported_without_the_variable_value(self):
        with self.assertRaises(ValueError) as caught:
            subagent._target(
                {"base_url": "https://x/v1", "model": "m", "api_key_env": "NOT_SET_XYZ"}
            )
        self.assertIn("missing", str(caught.exception).lower())


class SubagentConversationTests(unittest.TestCase):
    def setUp(self):
        subagent._SESSIONS.clear()

    def test_ask_returns_the_reply_and_usage(self):
        with urlopen_returning(fake_reply("pong", {"total_tokens": 7})):
            result = subagent.invoke("subagent_ask", {"prompt": "ping"}, SUB_CTX)
        self.assertEqual(result["reply"], "pong")
        self.assertEqual(result["usage"]["total_tokens"], 7)

    def test_a_long_reply_is_capped(self):
        with urlopen_returning(fake_reply("q" * 5_000)):
            result = subagent.invoke("subagent_ask", {"prompt": "ping"}, SUB_CTX)
        self.assertEqual(len(result["reply"]), 100)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["replyChars"], 5_000)

    def test_a_session_carries_history_and_the_system_prompt(self):
        started = subagent.invoke("subagent_start", {"system_prompt": "be terse"}, SUB_CTX)
        session_id = started["session"]
        with urlopen_returning(fake_reply("one")):
            subagent.invoke("subagent_say", {"session": session_id, "prompt": "first"}, SUB_CTX)
        with urlopen_returning(fake_reply("two")) as patched:
            result = subagent.invoke(
                "subagent_say", {"session": session_id, "prompt": "second"}, SUB_CTX
            )
            sent = patched.call_args[0][0].data.decode("utf-8")
        self.assertEqual(result["turns"], 2)
        self.assertIn("first", sent)
        self.assertIn("be terse", sent)

    def test_history_window_is_bounded(self):
        ctx = {"pluginConfig": {**SUB_CONFIG, "history_turns": 2}}
        started = subagent.invoke("subagent_start", {}, ctx)
        for index in range(5):
            with urlopen_returning(fake_reply(f"r{index}")):
                subagent.invoke(
                    "subagent_say", {"session": started["session"], "prompt": f"p{index}"}, ctx
                )
        self.assertLessEqual(len(subagent._SESSIONS[started["session"]]["turns"]), 4)

    def test_an_unknown_session_is_explicit(self):
        with self.assertRaises(ValueError):
            subagent.invoke("subagent_say", {"session": "sub-nope", "prompt": "x"}, SUB_CTX)

    def test_end_discards_the_session(self):
        started = subagent.invoke("subagent_start", {}, SUB_CTX)
        closed = subagent.invoke("subagent_end", {"session": started["session"]}, SUB_CTX)
        self.assertTrue(closed["closed"])
        self.assertEqual(subagent._SESSIONS, {})

    def test_session_count_is_bounded(self):
        for _ in range(subagent.MAX_SESSIONS + 4):
            subagent.invoke("subagent_start", {}, SUB_CTX)
        self.assertLessEqual(len(subagent._SESSIONS), subagent.MAX_SESSIONS)

    def test_unauthorized_remote_does_not_echo_the_endpoint(self):
        error = subagent.urllib.error.HTTPError(
            url="https://models.example.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b"bad key"),
        )
        with mock.patch.object(subagent.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ValueError) as caught:
                subagent.invoke("subagent_ask", {"prompt": "ping"}, SUB_CTX)
        message = str(caught.exception)
        self.assertNotIn("models.example.com", message)
        self.assertIn("credentials", message)


if __name__ == "__main__":
    unittest.main()
