"""Per-circuit tests for the 2.4.0 connection rebuild.

The central guarantee under test is isolation: a circuit must never accept,
reuse or emit another circuit's settings. That is the failure mode that took
production down when Serveo had certificate trouble.
"""

import io
import os
import queue
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from connections import blueprints, store  # noqa: E402
from connections.base import ConnectionConfigError, RuntimeContext  # noqa: E402
from connections.circuits import _tunnellio_client as tclient  # noqa: E402

FOREIGN_KEYS = {
    "serveo_hostname": "leak",
    "tunnellio_token": "leak",
    "tunnellio_domain": "leak",
    "tunnel_host": "leak",
    "tunnel_domain": "leak",
    "public_url": "https://leak.example.com",
    "api_token": "leak",
    "subdomain": "leak",
    "hostname": "leak",
}


def ctx(port=8765, script_dir=None, config_dir=None):
    base = Path(script_dir or PROJECT)
    return RuntimeContext(
        local_port=port,
        auth_mode="legacy",
        script_dir=base,
        config_dir=Path(config_dir or base),
        workspace=base,
        runtime_name="unit-test-mcp",
    )


class BlueprintTests(unittest.TestCase):
    def test_every_circuit_has_a_shipped_blueprint(self):
        for circuit_id in blueprints.available_ids():
            data = blueprints.load_blueprint(circuit_id)
            self.assertEqual(data["id"], circuit_id)
            self.assertIsInstance(data["settings"], dict)
            self.assertTrue(data.get("title"))

    def test_blueprint_is_read_only_from_the_caller_side(self):
        first = blueprints.load_blueprint("serveo_stable")
        first["settings"]["hostname"] = "mutated"
        second = blueprints.load_blueprint("serveo_stable")
        self.assertEqual(second["settings"]["hostname"], "")

    def test_blueprint_files_are_never_written_by_saving_a_profile(self):
        path = blueprints.blueprint_path("serveo_stable")
        before = path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            store.create_profile(
                "serveo_stable",
                "Unit",
                {"hostname": "unit", "ssh_key": "x"},
                path=Path(tmp) / "profiles.json",
            )
        self.assertEqual(path.read_bytes(), before)


class IsolationTests(unittest.TestCase):
    """No circuit may absorb a field it does not own."""

    def test_merge_drops_every_foreign_key(self):
        for circuit_id in blueprints.available_ids():
            circuit = store.circuit(circuit_id)
            own = set(circuit.default_settings())
            merged = circuit.merge(dict(FOREIGN_KEYS))
            self.assertEqual(set(merged), own, circuit_id)
            for key, value in FOREIGN_KEYS.items():
                if key in own:
                    continue
                self.assertNotIn(key, merged, f"{circuit_id} absorbed {key}")
            self.assertNotIn("leak", [v for k, v in merged.items() if k not in own])

    def test_legacy_export_never_mentions_another_circuit_value(self):
        serveo = store.circuit("serveo_stable")
        exported = serveo.legacy_export(
            serveo.merge({"hostname": "mine", "ssh_key": "k", "tunnellio_token": "leak"})
        )
        self.assertNotIn("tunnellio_token", exported)
        self.assertEqual(exported["serveo_hostname"], "mine")


class ServeoTemporaryTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("serveo_temporary")

    def test_needs_no_operator_input(self):
        self.assertEqual(self.circuit.questions(), [])
        self.assertTrue(self.circuit.is_configured({}))

    @mock.patch("connections.base.shutil.which", return_value="ssh")
    def test_command_has_no_key_and_no_hostname(self, _which):
        command = self.circuit.build_command({}, ctx())
        self.assertEqual(command[0], "ssh")
        self.assertNotIn("-i", command)
        self.assertIn("80:127.0.0.1:8765", command)
        self.assertEqual(command[-1], "serveo.net")

    def test_url_is_read_from_the_process_output(self):
        lines = queue.Queue()
        lines.put("Forwarding HTTP traffic from https://abc123.serveousercontent.com\n")
        url = self.circuit.resolve_url({}, ctx(), process=None, lines=lines)
        self.assertEqual(url, "https://abc123.serveousercontent.com")

    def test_missing_url_is_reported_not_guessed(self):
        settings = self.circuit.merge({"url_timeout_seconds": 0.2})
        with self.assertRaises(ConnectionConfigError):
            self.circuit.resolve_url(settings, ctx(), process=None, lines=queue.Queue())


class ServeoStableTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("serveo_stable")
        self._tmp = tempfile.TemporaryDirectory()
        self.key = Path(self._tmp.name) / "serveo_key"
        self.key.write_text("KEY", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def settings(self, **extra):
        base = {"hostname": "my-mcp", "ssh_key": str(self.key)}
        base.update(extra)
        return base

    def test_requires_hostname_and_key(self):
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate({})
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate({"hostname": "my-mcp"})

    def test_full_url_is_reduced_to_the_reserved_label(self):
        merged = self.circuit.validate(
            self.settings(hostname="https://my-mcp.serveousercontent.com")
        )
        self.assertEqual(merged["hostname"], "my-mcp")

    def test_public_key_is_rejected_when_no_private_key_exists(self):
        pub = Path(self._tmp.name) / "lonely.pub"
        pub.write_text("PUB", encoding="utf-8")
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate(self.settings(ssh_key=str(pub)))

    def test_public_key_is_corrected_to_its_private_twin(self):
        pub = Path(str(self.key) + ".pub")
        pub.write_text("PUB", encoding="utf-8")
        merged = self.circuit.validate(self.settings(ssh_key=str(pub)))
        self.assertEqual(merged["ssh_key"], str(self.key.resolve()))

    @mock.patch("connections.base.shutil.which", return_value="ssh")
    def test_command_binds_the_reserved_hostname(self, _which):
        command = self.circuit.build_command(self.settings(), ctx())
        self.assertIn("my-mcp:80:127.0.0.1:8765", command)
        self.assertIn(str(self.key.resolve()), command)
        self.assertEqual(command[-1], "serveo.net")

    @mock.patch("connections.base.shutil.which", return_value="ssh")
    def test_batch_mode_stays_off(self, _which):
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate(self.settings(batch_mode=True))

    def test_static_url(self):
        self.assertEqual(
            self.circuit.static_url(self.settings()),
            "https://my-mcp.serveousercontent.com",
        )


class TunnellioStableTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("tunnellio_stable")
        self._tmp = tempfile.TemporaryDirectory()
        self.key = Path(self._tmp.name) / "tunnellio_key"
        self.key.write_text("KEY", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def settings(self, **extra):
        base = {"domain": "my-mcp", "ssh_key": str(self.key)}
        base.update(extra)
        return base

    @mock.patch("connections.base.shutil.which", return_value="ssh")
    def test_direct_ssh_command_targets_the_tunnellio_edge(self, _which):
        command = self.circuit.build_command(self.settings(), ctx())
        self.assertEqual(command[0], "ssh")
        self.assertIn("-N", command)
        self.assertIn("my-mcp:80:127.0.0.1:8765", command)
        self.assertIn("2222", command)
        self.assertEqual(command[-1], "tunnel@tunnellio.site")

    def test_cli_transport_is_supported_from_client_060(self):
        merged = self.circuit.validate(self.settings(transport="cli"))
        self.assertEqual(merged["transport"], "cli")

    def test_unknown_transport_is_still_refused(self):
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate(self.settings(transport="carrier-pigeon"))

    def test_cli_command_never_sends_an_api_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "tunnellio.exe"
            binary.write_text("", encoding="utf-8")
            command = self.circuit.build_command(
                self.settings(transport="cli", client_path=str(binary)),
                ctx(script_dir=tmp, config_dir=tmp),
            )
        self.assertIn("connect", command)
        self.assertNotIn("--token", command)
        self.assertIn("existing:my-mcp", command)
        self.assertIn("--transport", command)

    def test_cli_transport_falls_back_to_direct_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "tunnellio.exe"
            binary.write_text("", encoding="utf-8")
            fallback = self.circuit.build_fallback_command(
                self.settings(transport="cli", client_path=str(binary)),
                ctx(script_dir=tmp, config_dir=tmp),
            )
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback[0], "ssh")
        self.assertIn("my-mcp:80:127.0.0.1:8765", fallback)

    def test_ssh_transport_has_no_fallback(self):
        self.assertIsNone(self.circuit.build_fallback_command(self.settings(), ctx()))

    def test_process_match_follows_the_transport(self):
        self.assertEqual(self.circuit.process_match(self.settings()), "ssh")
        self.assertEqual(
            self.circuit.process_match(self.settings(transport="cli")), "tunnellio.exe"
        )

    def test_static_url(self):
        self.assertEqual(
            self.circuit.static_url(self.settings()), "https://my-mcp.tunnellio.site"
        )

    def test_no_api_token_field_exists_at_all(self):
        self.assertNotIn("api_token", self.circuit.default_settings())


class TunnellioRandomTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("tunnellio_random")

    def test_token_is_required(self):
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate({})

    def test_verify_accepts_a_token_the_server_confirms(self):
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        with mock.patch.object(tclient.urllib.request, "urlopen", return_value=response):
            ok, message = self.circuit.verify({"api_token": "real-token"})
        self.assertTrue(ok)
        self.assertIn("accepted", message.lower())

    def test_verify_accepts_plan_required_because_the_token_authenticated(self):
        error = urllib.error.HTTPError(
            url="https://api.tunnellio.ru/v1/meta",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"plan_required"}'),
        )
        with mock.patch.object(tclient.urllib.request, "urlopen", side_effect=error):
            ok, _ = self.circuit.verify({"api_token": "free-plan-token"})
        self.assertTrue(ok)

    def test_verify_rejects_an_unauthorized_token(self):
        error = urllib.error.HTTPError(
            url="https://api.tunnellio.ru/v1/meta",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"invalid_token"}'),
        )
        with mock.patch.object(tclient.urllib.request, "urlopen", side_effect=error):
            ok, message = self.circuit.verify({"api_token": "bogus"})
        self.assertFalse(ok)
        self.assertIn("rejected", message.lower())

    def test_verify_reports_an_unreachable_server(self):
        with mock.patch.object(
            tclient.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("no route"),
        ):
            ok, message = self.circuit.verify({"api_token": "whatever"})
        self.assertFalse(ok)
        self.assertIn("reach", message.lower())

    def test_command_uses_connect_and_carries_the_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "tunnellio.exe"
            binary.write_text("", encoding="utf-8")
            command = self.circuit.build_command(
                {"api_token": "t0ken", "client_path": str(binary)},
                ctx(script_dir=tmp, config_dir=tmp),
            )
        self.assertIn("connect", command)
        self.assertIn("t0ken", command)
        self.assertNotIn("--domain", command)

    def test_missing_client_binary_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConnectionConfigError):
                self.circuit.build_command(
                    {"api_token": "t0ken", "client_path": str(Path(tmp) / "nope.exe")},
                    ctx(script_dir=tmp, config_dir=tmp),
                )


class TunnellioBridgeTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("tunnellio_bridge")
        self._tmp = tempfile.TemporaryDirectory()
        self.binary = Path(self._tmp.name) / "tunnellio.exe"
        self.binary.write_text("", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _ctx(self):
        return ctx(script_dir=self._tmp.name, config_dir=self._tmp.name)

    def test_zero_configuration_is_a_valid_profile(self):
        self.assertTrue(self.circuit.is_configured({}))
        self.circuit.validate({})

    def test_random_domain_command_is_keyless_and_domainless(self):
        command = self.circuit.build_command(
            {"client_path": str(self.binary)}, self._ctx()
        )
        self.assertIn("bridge", command)
        self.assertNotIn("--domain", command)
        self.assertNotIn("--token", command)
        self.assertNotIn("-i", command)

    def test_reserved_domain_is_pinned_when_provided(self):
        command = self.circuit.build_command(
            {"client_path": str(self.binary), "domain": "fixed-one"}, self._ctx()
        )
        self.assertIn("--domain", command)
        self.assertIn("fixed-one", command)

    def test_token_is_only_sent_when_the_plan_needs_it(self):
        command = self.circuit.build_command(
            {"client_path": str(self.binary), "api_token": "tok"}, self._ctx()
        )
        self.assertIn("--token", command)
        self.assertIn("tok", command)

    def test_expired_domain_falls_back_to_a_fresh_random_one(self):
        fallback = self.circuit.build_fallback_command(
            {"client_path": str(self.binary), "domain": "stale"}, self._ctx()
        )
        self.assertIsNotNone(fallback)
        self.assertNotIn("--domain", fallback)

    def test_no_fallback_when_the_operator_disabled_it(self):
        fallback = self.circuit.build_fallback_command(
            {
                "client_path": str(self.binary),
                "domain": "stale",
                "expired_domain_fallback": False,
            },
            self._ctx(),
        )
        self.assertIsNone(fallback)

    def test_no_fallback_for_an_already_random_profile(self):
        self.assertIsNone(
            self.circuit.build_fallback_command(
                {"client_path": str(self.binary)}, self._ctx()
            )
        )

    def test_keyless_profile_verifies_without_touching_the_network(self):
        ok, message = self.circuit.verify({})
        self.assertTrue(ok)
        self.assertIn("random", message.lower())


class SishTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("sish")
        self._tmp = tempfile.TemporaryDirectory()
        self.key = Path(self._tmp.name) / "sish_key"
        self.key.write_text("KEY", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def settings(self, **extra):
        base = {
            "ssh_host": "relay.example.com",
            "ssh_port": "2222",
            "wildcard_domain": "tun.example.com",
            "subdomain": "mymcp",
            "ssh_key": str(self.key),
        }
        base.update(extra)
        return base

    def test_all_relay_details_are_required(self):
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate({})
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate(self.settings(wildcard_domain=""))

    def test_non_numeric_port_is_refused(self):
        with self.assertRaises(ConnectionConfigError):
            self.circuit.validate(self.settings(ssh_port="http"))

    @mock.patch("connections.base.shutil.which", return_value="ssh")
    def test_command_and_url(self, _which):
        command = self.circuit.build_command(self.settings(), ctx())
        self.assertIn("mymcp:80:127.0.0.1:8765", command)
        self.assertEqual(command[-1], "relay.example.com")
        self.assertEqual(
            self.circuit.static_url(self.settings()), "https://mymcp.tun.example.com"
        )

    @mock.patch("connections.base.shutil.which", return_value="ssh")
    def test_optional_user_is_prefixed(self, _which):
        command = self.circuit.build_command(self.settings(ssh_user="tun"), ctx())
        self.assertEqual(command[-1], "tun@relay.example.com")


class ReverseProxyTests(unittest.TestCase):
    def setUp(self):
        self.circuit = store.circuit("reverse_proxy")

    def test_it_never_starts_a_process(self):
        self.assertFalse(self.circuit.starts_process)
        with self.assertRaises(ConnectionConfigError):
            self.circuit.build_command({"public_url": "https://a.example.com"}, ctx())

    def test_origin_only(self):
        merged = self.circuit.validate({"public_url": "https://mcp.example.com/"})
        self.assertEqual(merged["public_url"], "https://mcp.example.com")
        for bad in ("", "ftp://x", "https://x/path", "https://x?q=1", "nohost"):
            with self.assertRaises(ConnectionConfigError, msg=bad):
                self.circuit.validate({"public_url": bad})


class SshPlumbingTests(unittest.TestCase):
    @mock.patch("connections.base.shutil.which", return_value=None)
    def test_missing_openssh_is_explained_once_for_every_ssh_circuit(self, _which):
        for circuit_id in ("serveo_temporary", "serveo_stable", "tunnellio_stable", "sish"):
            circuit = store.circuit(circuit_id)
            with self.assertRaises(ConnectionConfigError) as caught:
                try:
                    circuit.build_command(circuit.default_settings(), ctx())
                except ConnectionConfigError:
                    raise
            self.assertIsInstance(caught.exception, ConnectionConfigError)


if __name__ == "__main__":
    unittest.main()
