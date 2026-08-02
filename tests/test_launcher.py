import json

import os

import queue

import socket

from io import StringIO

import sys

import tempfile

import unittest

from pathlib import Path

from unittest import mock



PROJECT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PROJECT))



import launcher





class LauncherTests(unittest.TestCase):

    def test_config_public_url_prefers_custom_value(self):

        self.assertEqual(

            launcher.config_public_url({"public_url": "https://mcp.example.com/"}),

            "https://mcp.example.com",

        )



    def test_config_public_url_uses_stable_serveo_hostname(self):

        self.assertEqual(

            launcher.config_public_url({"serveo_hostname": "stable-name"}),

            "https://stable-name.serveousercontent.com",

        )



    def test_validate_public_base_url_rejects_paths(self):

        with self.assertRaisesRegex(ValueError, "base origin"):

            launcher.validate_public_base_url("https://mcp.example.com/mcp")



    def test_current_pid_exists(self):

        self.assertTrue(launcher.pid_exists(os.getpid()))



    def test_connections_cfg_is_created_with_menu_and_slots(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            connections_file = root / "connections.cfg"

            with mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file):

                launcher.ensure_connections_cfg_exists()

                text = connections_file.read_text(encoding="utf-8")

            self.assertIn("MENU = on", text)

            self.assertIn("PATH[1] =", text)

            self.assertIn("PATH[9] =", text)




    def test_heal_legacy_config_restores_workspace_token_and_auth_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace-one"
            workspace.mkdir()
            config_file = root / "config.json"
            backup = root / "config.backup.20260101-000000.test.json"
            backup.write_text(json.dumps({
                "workspace": str(workspace.resolve()),
                "token": "fixed-token",
                "auth_mode": "legacy",
                "tunnel_backend": "serveo",
            }), encoding="utf-8")
            connections_file = root / "connections.cfg"
            with (
                mock.patch.object(launcher, "CONFIG_FILE", config_file),
                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),
            ):
                launcher.save_connections_cfg(True, {1: str(workspace.resolve())})
                healed = launcher.heal_legacy_config({}, persist=True)
                stored = json.loads(config_file.read_text(encoding="utf-8"))

        self.assertEqual(healed["workspace"], str(workspace.resolve()))
        self.assertEqual(healed["token"], "fixed-token")
        self.assertEqual(healed["auth_mode"], "legacy")
        self.assertEqual(stored["tunnel_backend"], "serveo")

    def test_start_and_resolve_tunnel_retries_when_relay_port_is_busy(self):
        class Proc:
            pid = 12345
            def poll(self):
                return 255
            def terminate(self):
                pass

        calls = {"count": 0}

        def fake_start(config):
            return Proc(), queue.Queue()

        def fake_resolve(config, tunnel, lines):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("SSH tunnel exited with code 255")
            return "https://retry.serveousercontent.com"

        with (
            mock.patch.object(launcher, "start_tunnel", side_effect=fake_start),
            mock.patch.object(launcher, "resolve_tunnel_url", side_effect=fake_resolve),
            mock.patch.object(launcher, "tunnel_log_suggests_remote_port_busy", return_value=True),
            mock.patch.object(launcher, "stop_previous_tunnel_runtime"),
            mock.patch.object(launcher, "stop_pid"),
            mock.patch.object(launcher.time, "sleep"),
        ):
            tunnel, lines, url = launcher.start_and_resolve_tunnel({"tunnel_backend": "serveo"}, attempts=2)

        self.assertEqual(url, "https://retry.serveousercontent.com")
        self.assertEqual(calls["count"], 2)

    def test_heal_legacy_config_does_not_guess_tunnel_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.json"
            with mock.patch.object(launcher, "CONFIG_FILE", config_file):
                healed = launcher.heal_legacy_config({"workspace": "x", "token": "t", "auth_mode": "legacy"}, persist=False)
        self.assertNotIn("tunnel_backend", healed)

    def test_normalize_serveo_hostname_accepts_full_url_but_returns_label(self):
        self.assertEqual(
            launcher.normalize_serveo_hostname("https://my-notion-mcp.serveousercontent.com/mcp"),
            "my-notion-mcp",
        )

    def test_start_and_resolve_tunnel_does_not_retry_configuration_errors(self):
        class Proc:
            pid = 12345
            def poll(self):
                return 3
            def terminate(self):
                pass

        with (
            mock.patch.object(launcher, "start_tunnel", return_value=(Proc(), queue.Queue())),
            mock.patch.object(launcher, "resolve_tunnel_url", side_effect=RuntimeError("API token is required")),
            mock.patch.object(launcher, "tunnel_log_suggests_remote_port_busy", return_value=False),
            mock.patch.object(launcher, "stop_previous_tunnel_runtime"),
            mock.patch.object(launcher.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "API token is required"):
                launcher.start_and_resolve_tunnel({"tunnel_backend": "tunnellio"}, attempts=4)
        sleep.assert_not_called()


    def test_save_config_blocks_sensitive_rewrite_outside_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = root / "config.json"
            original = {
                "workspace": "old",
                "token": "fixed-token",
                "auth_mode": "legacy",
                "tunnel_backend": "serveo",
                "serveo_hostname": "my-notion-mcp",
            }
            config_file.write_text(json.dumps(original), encoding="utf-8")
            with mock.patch.object(launcher, "CONFIG_FILE", config_file):
                changed = dict(original, tunnel_backend="tunnellio")
                with self.assertRaisesRegex(RuntimeError, "tunnel_backend"):
                    launcher.save_config(changed, reason="profile-activate")
                backups = list(root.glob("config.backup.*.json"))
        self.assertEqual(backups, [])


    def test_legacy_config_without_backend_migrates_to_serveo_temporary(self):
        self.assertEqual(
            launcher.selected_mode_from_config({"workspace": "x", "token": "t"}),
            "serveo_temporary",
        )

    def test_connection_profiles_keep_modes_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = root / "config.json"
            with mock.patch.object(launcher, "CONFIG_FILE", config_file):
                launcher.save_connection_profile("serveo_stable", {
                    "tunnel_backend": "serveo",
                    "tunnel_mode_preference": "serveo_stable",
                    "serveo_hostname": "stable-one",
                    "ssh_key": "key-one",
                })
                launcher.save_connection_profile("reverse_proxy", {
                    "tunnel_backend": "custom_proxy",
                    "tunnel_mode_preference": "reverse_proxy",
                    "public_url": "https://mcp.example.com",
                })
                serveo = launcher.connection_profile_for("serveo_stable")
                reverse = launcher.connection_profile_for("reverse_proxy")
        self.assertEqual(serveo["serveo_hostname"], "stable-one")
        self.assertNotIn("public_url", serveo)
        self.assertEqual(reverse["public_url"], "https://mcp.example.com")
        self.assertNotIn("serveo_hostname", reverse)

    def test_sanitize_active_mode_clears_inactive_connection_fields(self):
        source = {
            "public_url": "https://old.example.com",
            "serveo_hostname": "old-host",
            "ssh_key": "old-key",
            "tunnel_host": "old-relay",
            "tunnel_domain": "old.example",
        }
        result = launcher.sanitize_active_connection_config(source, "serveo_temporary")
        self.assertEqual(result["tunnel_backend"], "serveo")
        self.assertEqual(result["serveo_hostname"], "")
        self.assertEqual(result["public_url"], "")
        self.assertEqual(result["tunnel_host"], "")

    def test_config_backups_are_limited_to_five(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = root / "config.json"
            config_file.write_text(json.dumps({"workspace": "x", "token": "t"}), encoding="utf-8")
            with mock.patch.object(launcher, "CONFIG_FILE", config_file):
                for index in range(8):
                    config = {"workspace": f"x-{index}", "token": "t"}
                    launcher.save_config(config, reason=f"test-{index}")
                backups = list(root.glob("config.backup.*.json"))
        self.assertLessEqual(len(backups), 5)

    def test_rotate_log_file_keeps_five_files_total(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            for index in range(8):
                log.write_text(str(index), encoding="utf-8")
                launcher.rotate_log_file(log, keep_files=5)
            files = [item for item in log.parent.iterdir() if item.name.startswith("server.log")]
        self.assertLessEqual(len(files), 5)


    def test_tunnellio_runtime_names_do_not_collide_for_same_workspace_basename(self):
        one = launcher.tunnellio_runtime_name({"workspace": "C:/one/project"})
        two = launcher.tunnellio_runtime_name({"workspace": "C:/two/project"})
        self.assertNotEqual(one, two)
        self.assertTrue(one.startswith("project-mcp-"))

    def test_setup_saves_first_workspace_to_first_slot(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace = root / "workspace-one"

            workspace.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch("launcher.input", side_effect=[str(workspace)]),

                mock.patch("launcher.yes_no", side_effect=[False, False]),

            ):

                config = launcher.setup(force=False)

                saved = launcher.load_connections_cfg()

            self.assertEqual(config["workspace"], str(workspace.resolve()))

            self.assertEqual(saved["paths"][1], str(workspace.resolve()))



    def test_start_menu_switches_to_saved_workspace_without_changing_token(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace_one = root / "workspace-one"

            workspace_two = root / "workspace-two"

            workspace_one.mkdir()

            workspace_two.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            config = {

                "token": "fixed-token",

                "workspace": str(workspace_one.resolve()),

                "port": 8765,

            }

            config_file.write_text(json.dumps(config), encoding="utf-8")

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

            ):

                launcher.save_connections_cfg(

                    True,

                    {1: str(workspace_one.resolve()), 2: str(workspace_two.resolve())},

                )

                with mock.patch("launcher.input", side_effect=["2"]):

                    updated = launcher.setup(force=False)

            self.assertEqual(updated["workspace"], str(workspace_two.resolve()))

            self.assertEqual(updated["token"], "fixed-token")

            stored = json.loads(config_file.read_text(encoding="utf-8"))

            self.assertEqual(stored["workspace"], str(workspace_two.resolve()))



    def test_setup_creates_workflow_profiles_storage(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace = root / "workspace-one"

            workspace.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch("launcher.input", side_effect=[str(workspace)]),

                mock.patch("launcher.yes_no", side_effect=[False, False]),

            ):

                launcher.setup(force=False)

            profiles_file = root / "workflow-profiles.json"

            self.assertTrue(profiles_file.is_file())

            data = json.loads(profiles_file.read_text(encoding="utf-8"))

            self.assertTrue(data["activeProfileId"])

            self.assertEqual(len(data["profiles"]), 1)

            profile = next(iter(data["profiles"].values()))

            self.assertEqual(profile["workspacePath"], str(workspace.resolve()))

            self.assertEqual(profile["accessMode"], "file_only")

            self.assertEqual(profile["environmentMode"], "DEFAULT")



    def test_switch_activates_profile_access_mode(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace_one = root / "workspace-one"

            workspace_two = root / "workspace-two"

            workspace_one.mkdir()

            workspace_two.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            profiles_file = root / "workflow-profiles.json"

            config = {

                "token": "fixed-token",

                "workspace": str(workspace_one.resolve()),

                "port": 8765,

                "allow_commands": False,

            }

            config_file.write_text(json.dumps(config), encoding="utf-8")

            profiles = {

                "schemaVersion": 1,

                "activeProfileId": "profile-1",

                "profiles": {

                    "profile-1": {

                        "profileId": "profile-1",

                        "pathSlot": 1,

                        "workspacePath": str(workspace_one.resolve()),

                        "accessMode": "file_only",

                        "environmentMode": "DEFAULT",

                        "displayName": "workspace-one",

                        "createdAt": "2026-07-20T22:00:00",

                        "updatedAt": "2026-07-20T22:00:00",

                        "metadata": {

                            "createdFrom": "test",

                            "lastSelectedAt": "",

                            "lastKnownGood": True,

                            "notes": "",

                        },

                        "plugins": {},

                    },

                    "profile-2": {

                        "profileId": "profile-2",

                        "pathSlot": 2,

                        "workspacePath": str(workspace_two.resolve()),

                        "accessMode": "trusted",

                        "environmentMode": "DEFAULT",

                        "displayName": "workspace-two",

                        "createdAt": "2026-07-20T22:00:00",

                        "updatedAt": "2026-07-20T22:00:00",

                        "metadata": {

                            "createdFrom": "test",

                            "lastSelectedAt": "",

                            "lastKnownGood": True,

                            "notes": "",

                        },

                        "plugins": {},

                    },

                },

                "globalPlugins": {},

            }

            profiles_file.write_text(json.dumps(profiles), encoding="utf-8")

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

            ):

                launcher.save_connections_cfg(

                    True,

                    {1: str(workspace_one.resolve()), 2: str(workspace_two.resolve())},

                )

                with mock.patch("launcher.input", side_effect=["2"]):

                    updated = launcher.setup(force=False)

            self.assertEqual(updated["workspace"], str(workspace_two.resolve()))

            self.assertTrue(updated["allow_commands"])

            stored_profiles = json.loads(profiles_file.read_text(encoding="utf-8"))

            self.assertEqual(stored_profiles["activeProfileId"], "profile-2")



    def test_start_menu_can_save_new_workspace_to_free_slot(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace_one = root / "workspace-one"

            workspace_two = root / "workspace-two"

            workspace_one.mkdir()

            workspace_two.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            config = {

                "token": "fixed-token",

                "workspace": str(workspace_one.resolve()),

                "port": 8765,

            }

            config_file.write_text(json.dumps(config), encoding="utf-8")

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch("launcher.yes_no", return_value=False),

            ):

                launcher.save_connections_cfg(True, {1: str(workspace_one.resolve())})

                with mock.patch("launcher.input", side_effect=["0", str(workspace_two)]):

                    updated = launcher.setup(force=False)

                saved = launcher.load_connections_cfg()

            self.assertEqual(updated["workspace"], str(workspace_two.resolve()))

            self.assertEqual(saved["paths"][2], str(workspace_two.resolve()))



    def test_start_menu_can_extend_slots_beyond_nine(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            current_workspace = root / "workspace-current"

            new_workspace = root / "workspace-new"

            current_workspace.mkdir()

            new_workspace.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            config = {

                "token": "fixed-token",

                "workspace": str(current_workspace.resolve()),

                "port": 8765,

            }

            config_file.write_text(json.dumps(config), encoding="utf-8")

            occupied = {}

            for index in range(1, 10):

                path = root / f"saved-{index}"

                path.mkdir()

                occupied[index] = str(path.resolve())

            occupied[1] = str(current_workspace.resolve())

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch("launcher.yes_no", return_value=False),

            ):

                launcher.save_connections_cfg(True, occupied)

                with mock.patch("launcher.input", side_effect=["0", str(new_workspace), "10"]):

                    updated = launcher.setup(force=False)

                saved = launcher.load_connections_cfg()

            self.assertEqual(updated["workspace"], str(new_workspace.resolve()))

            self.assertEqual(saved["paths"][10], str(new_workspace.resolve()))



    def test_start_menu_can_disable_itself(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace_one = root / "workspace-one"

            workspace_one.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            config = {

                "token": "fixed-token",

                "workspace": str(workspace_one.resolve()),

                "port": 8765,

            }

            config_file.write_text(json.dumps(config), encoding="utf-8")

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

            ):

                launcher.save_connections_cfg(True, {1: str(workspace_one.resolve())})

                with mock.patch("launcher.input", side_effect=["q"]):

                    updated = launcher.setup(force=False)

                saved = launcher.load_connections_cfg()

            self.assertEqual(updated["workspace"], str(workspace_one.resolve()))

            self.assertFalse(saved["menu_on"])



    def test_missing_pid_does_not_exist(self):

        self.assertFalse(launcher.pid_exists(99_999_999))



    def test_occupied_port_is_detected(self):

        with socket.socket() as listener:

            listener.bind(("127.0.0.1", 0))

            listener.listen()

            port = listener.getsockname()[1]

            self.assertTrue(launcher.port_is_open(port))



    def test_free_port_is_not_reported_as_open(self):

        with socket.socket() as probe:

            probe.bind(("127.0.0.1", 0))

            port = probe.getsockname()[1]

        self.assertFalse(launcher.port_is_open(port))



    def test_setup_can_choose_serveo_temporary_mode(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace = root / "workspace-one"

            workspace.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch("launcher.input", side_effect=[str(workspace), "2"]),

                mock.patch("launcher.yes_no", side_effect=[False]),

            ):

                config = launcher.setup(force=False)

            self.assertEqual(config["tunnel_backend"], "serveo")

            self.assertEqual(config["tunnel_mode_preference"], "serveo_temporary")

            self.assertEqual(config["serveo_hostname"], "")

            self.assertEqual(config["ssh_key"], "")



    def test_setup_can_choose_serveo_stable_mode(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace = root / "workspace-one"

            workspace.mkdir()

            key = root / "serveo_key"

            key.write_text("test", encoding="utf-8")

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch(

                    "launcher.input",

                    side_effect=[str(workspace), "3", "my-notion-mcp", str(key)],

                ),

                mock.patch("launcher.yes_no", side_effect=[False]),

            ):

                config = launcher.setup(force=False)

            self.assertEqual(config["tunnel_backend"], "serveo")

            self.assertEqual(config["tunnel_mode_preference"], "serveo_stable")

            self.assertEqual(config["serveo_hostname"], "my-notion-mcp")

            self.assertEqual(config["ssh_key"], str(key.resolve()))



    def test_setup_can_choose_reverse_proxy_mode(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace = root / "workspace-one"

            workspace.mkdir()

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch(

                    "launcher.input",

                    side_effect=[str(workspace), "4", "https://mcp.example.com"],

                ),

                mock.patch("launcher.yes_no", side_effect=[False]),

            ):

                config = launcher.setup(force=False)

            self.assertEqual(config["tunnel_backend"], "custom_proxy")

            self.assertEqual(config["tunnel_mode_preference"], "reverse_proxy")

            self.assertEqual(config["public_url"], "https://mcp.example.com")

            self.assertEqual(config["serveo_hostname"], "")

            self.assertEqual(config["ssh_key"], "")



    def test_setup_can_choose_sish_mode(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            workspace = root / "workspace-one"

            workspace.mkdir()

            key = root / "sish_key"

            key.write_text("test", encoding="utf-8")

            config_file = root / "config.json"

            connections_file = root / "connections.cfg"

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTIONS_FILE", connections_file),

                mock.patch(

                    "launcher.input",

                    side_effect=[

                        str(workspace),

                        "5",

                        "relay.example.com",

                        "2222",

                        "example.com",

                        "mcp",

                        str(key),

                    ],

                ),

                mock.patch("launcher.yes_no", side_effect=[False]),

            ):

                config = launcher.setup(force=False)

            self.assertEqual(config["tunnel_backend"], "sish")

            self.assertEqual(config["tunnel_mode_preference"], "sish")

            self.assertEqual(config["public_url"], "")

            self.assertEqual(config["tunnel_host"], "relay.example.com")

            self.assertEqual(config["tunnel_ssh_port"], "2222")

            self.assertEqual(config["tunnel_domain"], "example.com")

            self.assertEqual(config["serveo_hostname"], "mcp")

            self.assertEqual(config["ssh_key"], str(key.resolve()))



    @mock.patch("launcher.shutil.which", return_value="ssh.exe")

    def test_temporary_tunnel_command(self, _which):

        command = launcher.build_tunnel_command({"port": 8765})

        self.assertIn("80:127.0.0.1:8765", command)

        # BatchMode must stay OFF here: Serveo anonymous auth is keyboard-interactive.

        self.assertNotIn("BatchMode=yes", command)

        self.assertNotIn("-i", command)



    @mock.patch("launcher.shutil.which", return_value="ssh.exe")

    def test_stable_tunnel_command(self, _which):

        with tempfile.TemporaryDirectory() as directory:

            key = Path(directory) / "serveo_key"

            key.write_text("test", encoding="utf-8")

            command = launcher.build_tunnel_command(

                {

                    "port": 8765,

                    "serveo_hostname": "my-notion-mcp",

                    "ssh_key": str(key),

                }

            )

        self.assertIn("-i", command)

        # Serveo needs keyboard-interactive even with a registered key.

        self.assertNotIn("BatchMode=yes", command)

        self.assertIn("IdentitiesOnly=yes", command)

        self.assertIn("my-notion-mcp:80:127.0.0.1:8765", command)



    @mock.patch("launcher.shutil.which", return_value="ssh.exe")

    def test_sish_tunnel_command(self, _which):

        with tempfile.TemporaryDirectory() as directory:

            key = Path(directory) / "sish_key"

            key.write_text("test", encoding="utf-8")

            command = launcher.build_tunnel_command(

                {

                    "tunnel_backend": "sish",

                    "tunnel_backend": "sish",

                    "tunnel_host": "relay.example.com",

                    "tunnel_ssh_port": "2222",

                    "tunnel_domain": "example.com",

                    "serveo_hostname": "mcp",

                    "ssh_key": str(key),

                    "port": 8765,

                }

            )

        self.assertIn("-p", command)

        self.assertIn("2222", command)

        self.assertIn("-i", command)

        self.assertIn("IdentitiesOnly=yes", command)

        self.assertIn("mcp:80:127.0.0.1:8765", command)

        self.assertEqual(command[-1], "relay.example.com")



    def test_stable_url_does_not_require_ssh_output(self):

        class RunningProcess:

            returncode = None



            @staticmethod

            def poll():

                return None



        url = launcher.resolve_tunnel_url(

            {"serveo_hostname": "my-notion-mcp"},

            RunningProcess(),

            queue.Queue(),

            startup_grace=0,

        )

        self.assertEqual(

            url, "https://my-notion-mcp.serveousercontent.com"

        )



    def test_stable_url_reports_early_ssh_failure(self):

        class FailedProcess:

            returncode = 255



            @staticmethod

            def poll():

                return 255



        with self.assertRaisesRegex(RuntimeError, "SSH tunnel exited with code 255"):

            launcher.resolve_tunnel_url(

                {"serveo_hostname": "my-notion-mcp"},

                FailedProcess(),

                queue.Queue(),

                startup_grace=0,

            )



    @mock.patch("launcher.wait_for_url", return_value="https://temporary.serveousercontent.com")

    def test_temporary_url_still_uses_ssh_announcement(self, wait_for_url):

        process = mock.Mock()

        lines = queue.Queue()

        url = launcher.resolve_tunnel_url({}, process, lines)

        self.assertEqual(url, "https://temporary.serveousercontent.com")

        wait_for_url.assert_called_once_with(process, lines)



    def test_sish_url_uses_configured_public_url(self):

        process = mock.Mock()

        process.poll.return_value = None

        url = launcher.resolve_tunnel_url(

            {

                "tunnel_backend": "sish",

                "serveo_hostname": "mcp",

                "tunnel_domain": "example.com",

            },

            process,

            queue.Queue(),

            startup_grace=0,

        )

        self.assertEqual(url, "https://mcp.example.com")



    def test_run_skips_built_in_tunnel_when_custom_public_url_is_configured(self):

        config = {

            "workspace": "C:/workspace",

            "token": "secret-token",

            "port": 8765,

            "public_url": "https://mcp.example.com",

            "allow_commands": False,

        }

        server = mock.Mock()

        server.pid = 321

        server.poll.side_effect = [None, 1, 1]

        server_log = mock.Mock()

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            runtime_file = root / "runtime.json"

            connection_file = root / "connection.txt"

            with (

                mock.patch.object(launcher, "RUNTIME_FILE", runtime_file),

                mock.patch.object(launcher, "CONNECTION_FILE", connection_file),

                mock.patch("launcher.setup", return_value=config),

                mock.patch("launcher.validate_runtime_config", side_effect=lambda value: value),

                mock.patch("launcher.load_json", return_value={}),

                mock.patch("launcher.start_server", return_value=(server, server_log)),

                mock.patch("launcher.start_tunnel") as start_tunnel,

                mock.patch("launcher.public_health_ok", return_value=True),

                mock.patch("launcher.publish_connection") as publish_connection,

                mock.patch("launcher.stop_pid", return_value=True),

            ):

                result = launcher.run()

        self.assertEqual(result, 1)

        start_tunnel.assert_not_called()

        publish_connection.assert_called_once_with(

            config, "https://mcp.example.com", 321, 0

        )



    def test_tunnellio_tunnel_command_uses_runtime_contract(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            exe = root / "tunnellio.exe"

            exe.write_text("binary", encoding="utf-8")

            state_dir = root / "state"

            command = launcher.build_tunnel_command(

                {

                    "tunnel_backend": "tunnellio",

                    "tunnellio_path": str(exe),

                    "tunnellio_state_dir": str(state_dir),

                    "tunnellio_runtime_name": "prod-api",

                    "tunnellio_base_url": "https://api.tunnellio.example",

                    "tunnellio_token": "secret-token",

                    "tunnellio_domain": "random",

                    "tunnellio_key": "existing:mcp",

                    "tunnellio_connection_mode": "cloud_proxy",

                    "tunnellio_oauth_client_policy": "shared",

                    "tunnellio_use_discovery": True,

                    "tunnellio_enable_pkce": True,

                    "auth_mode": "oauth",

                    "port": 8765,

                }

            )

        self.assertEqual(command[0], str(exe.resolve()))

        self.assertIn("--state-dir", command)

        self.assertIn(str(state_dir.resolve()), command)

        self.assertIn("--base-url", command)

        self.assertIn("https://api.tunnellio.example", command)

        self.assertIn("--token", command)

        self.assertIn("secret-token", command)

        self.assertIn("connect", command)

        self.assertIn("--run", command)

        self.assertIn("--no-watch", command)

        self.assertIn("--requested-auth-mode", command)

        self.assertIn("oauth", command)

        self.assertIn("--runtime-name", command)

        self.assertIn("prod-api", command)



    @mock.patch(

        "launcher.load_tunnellio_runtime_snapshot",

        return_value={"transport": {"publicUrl": "https://prod.example.com"}},

    )

    def test_resolve_tunnellio_url_uses_runtime_snapshot(self, load_snapshot):

        process = mock.Mock()

        process.poll.return_value = None

        url = launcher.resolve_tunnel_url(

            {

                "tunnel_backend": "tunnellio",

                "tunnellio_runtime_name": "prod-api",

            },

            process,

            queue.Queue(),

        )

        self.assertEqual(url, "https://prod.example.com")

        load_snapshot.assert_called()



    def test_tunnellio_url_reports_early_client_failure(self):

        class FailedProcess:

            returncode = 2



            @staticmethod

            def poll():

                return 2



        with self.assertRaisesRegex(RuntimeError, "Tunnellio client exited with code 2"):

            launcher.resolve_tunnel_url(

                {

                    "tunnel_backend": "tunnellio",

                    "tunnellio_runtime_name": "prod-api",

                },

                FailedProcess(),

                queue.Queue(),

            )



    def test_mask_token(self):

        self.assertEqual(launcher.mask_token("abcdEFGHijklMNOP"), "abcd...MNOP")

        self.assertEqual(launcher.mask_token("short"), "*****")



    def test_public_health_fails_fast_on_unreachable_url(self):

        self.assertFalse(

            launcher.public_health_ok(

                "http://127.0.0.1:1/", "token", attempts=1, delay=0

            )

        )



    @mock.patch("launcher.urllib.request.urlopen")

    def test_public_health_accepts_ok_payload(self, urlopen):

        response = mock.MagicMock()

        response.status = 200

        response.read.return_value = b'{"status": "ok"}'

        urlopen.return_value.__enter__.return_value = response

        self.assertTrue(

            launcher.public_health_ok(

                "https://x.serveousercontent.com", "token", attempts=1, delay=0

            )

        )



    def test_oauth_setup_saves_mode_and_owner_code(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            config_file = root / "config.json"

            config_file.write_text(json.dumps({

                "token": "fixed-token",

                "workspace": str(root),

                "auth_mode": "legacy",

                "serveo_hostname": "stable-name",

            }), encoding="utf-8")

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch("launcher.input", side_effect=["3"]),

            ):

                result = launcher.oauth_setup()

            self.assertEqual(result, 0)

            updated = json.loads(config_file.read_text(encoding="utf-8"))

            self.assertEqual(updated["auth_mode"], "dual")

            self.assertTrue(updated["oauth_owner_code"])



    def test_show_connection_masks_owner_code(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            config_file = root / "config.json"

            connection_file = root / "connection.txt"

            config_file.write_text(json.dumps({

                "token": "super-secret-token",

                "oauth_owner_code": "owner-secret-code",

            }), encoding="utf-8")

            connection_file.write_text(

                "Authorization=Bearer super-secret-token (Bearer token)\nOAuth owner code: owner-secret-code\n",

                encoding="utf-8",

            )

            stdout = StringIO()

            with (

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch.object(launcher, "CONNECTION_FILE", connection_file),

                mock.patch("sys.stdout", stdout),

            ):

                result = launcher.show_connection(full=False)

            self.assertEqual(result, 0)

            rendered = stdout.getvalue()

            self.assertNotIn("super-secret-token", rendered)

            self.assertNotIn("owner-secret-code", rendered)

            self.assertIn("Secrets are masked", rendered)



    def test_register_oauth_client_stores_byo_client(self):

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)

            config_dir = root / "cfg"

            config_dir.mkdir()

            config_file = config_dir / "config.json"

            config_file.write_text(json.dumps({

                "token": "fixed-token",

                "workspace": str(root),

                "auth_mode": "dual",

            }), encoding="utf-8")

            with (

                mock.patch.object(launcher, "CONFIG_DIR", config_dir),

                mock.patch.object(launcher, "CONFIG_FILE", config_file),

                mock.patch("launcher.input", side_effect=["https://client.example/callback", ""]),

                mock.patch("launcher.yes_no", return_value=True),

            ):

                result = launcher.register_oauth_client()

            self.assertEqual(result, 0)

            state = json.loads((config_dir / "oauth_state.json").read_text(encoding="utf-8"))

            self.assertTrue(state["clients"])

            client = next(iter(state["clients"].values()))

            self.assertEqual(client["redirect_uris"], ["https://client.example/callback"])

            self.assertEqual(client["token_endpoint_auth_method"], "none")





if __name__ == "__main__":

    unittest.main()

