"""Tests for the permanent connection state introduced in 2.4.0.

Covers the three promises made to the operator:

1. One permanent record of work areas and their connection profile.
2. Configured profiles survive a product upgrade untouched.
3. Legacy installs are imported without guessing and without rotating secrets.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import build_release  # noqa: E402
from connections import store  # noqa: E402
from connections.store import ConnectionStoreError  # noqa: E402


class StoreTestCase(unittest.TestCase):
    """Every test runs against throwaway state files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.current_file = self.root / "current-connection.json"
        self.profiles_file = self.root / "connection-profiles.v2.json"
        self.legacy_file = self.root / "config.json"
        self.workspace = self.root / "area-one"
        self.workspace.mkdir()
        self._patches = [
            mock.patch.object(store, "CURRENT_FILE", self.current_file),
            mock.patch.object(store, "PROFILES_FILE", self.profiles_file),
            mock.patch.object(store, "LEGACY_CONFIG_FILE", self.legacy_file),
            mock.patch.object(store, "CONFIG_DIR", self.root),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()

    def configure_bridge(self, name="Bridge", **settings):
        # Profiles are named instances now, so the id is passed explicitly here
        # to keep these tests readable.
        return store.create_profile(
            "tunnellio_bridge", name, settings, verified=True, profile_id="tunnellio_bridge"
        )

    def configure_serveo(self, name="Serveo stable", hostname="my-mcp", profile_id="serveo_stable"):
        key = self.root / "serveo_key"
        key.write_text("KEY", encoding="utf-8")
        # Mirrors what PROFILES.bat does: a profile is only stored after its
        # own verification step passed.
        return store.create_profile(
            "serveo_stable",
            name,
            {"hostname": hostname, "ssh_key": str(key)},
            verified=True,
            profile_id=profile_id,
        )


class ProfileStorageTests(StoreTestCase):
    def test_a_fresh_install_has_no_profiles(self):
        self.assertEqual(store.load_profiles()["profiles"], {})

    def test_unconfigured_profile_still_reads_as_its_blueprint(self):
        settings = store.profile_settings("serveo_stable")
        self.assertEqual(settings["ssh_host"], "serveo.net")
        self.assertEqual(settings["hostname"], "")

    def test_save_and_reload_round_trip(self):
        self.configure_serveo()
        reloaded = store.profile_settings("serveo_stable")
        self.assertEqual(reloaded["hostname"], "my-mcp")
        self.assertTrue(store.is_configured("serveo_stable"))

    def test_saving_records_verification_and_timestamps(self):
        entry = self.configure_serveo()
        self.assertTrue(entry["createdAt"])
        self.assertTrue(entry["updatedAt"])
        self.assertTrue(entry["verifiedAt"])
        self.assertEqual(entry["blueprintVersion"], 1)

    def test_reset_returns_to_the_shipped_blueprint(self):
        self.configure_serveo()
        store.reset_profile("serveo_stable")
        self.assertFalse(store.is_configured("serveo_stable"))
        self.assertEqual(store.profile_settings("serveo_stable")["hostname"], "")

    def test_zero_input_circuits_count_as_ready(self):
        self.assertTrue(store.is_configured("serveo_temporary"))
        self.assertTrue(store.is_configured("tunnellio_bridge"))
        self.assertFalse(store.is_configured("sish"))

    def test_foreign_keys_in_stored_json_are_dropped_on_load(self):
        # A v2 file: one profile per circuit, keyed by circuit id.
        self.profiles_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "profiles": {
                        "serveo_stable": {
                            "id": "serveo_stable",
                            "settings": {
                                "hostname": "mine",
                                "tunnellio_token": "leak",
                                "public_url": "https://leak",
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        settings = store.profile_settings("serveo_stable")
        self.assertEqual(settings["hostname"], "mine")
        self.assertNotIn("tunnellio_token", settings)
        self.assertNotIn("public_url", settings)

    def test_unknown_circuit_ids_in_storage_are_ignored(self):
        self.profiles_file.write_text(
            json.dumps({"schemaVersion": 3, "profiles": {"x": {"circuit": "ngrok", "settings": {}}}}),
            encoding="utf-8",
        )
        self.assertEqual(store.load_profiles()["profiles"], {})

    def test_one_rolling_backup_not_a_pile(self):
        store.create_profile(
            "reverse_proxy", "Proxy", {"public_url": "https://n0.example.com"}, profile_id="proxy"
        )
        for index in range(1, 4):
            store.update_profile("proxy", settings={"public_url": f"https://n{index}.example.com"})
        backups = list(self.root.glob("connection-profiles.v2.json*"))
        names = sorted(path.name for path in backups)
        self.assertEqual(
            names, ["connection-profiles.v2.json", "connection-profiles.v2.json.bak"]
        )


class AreaTests(StoreTestCase):
    def test_area_records_folder_mode_and_profile(self):
        self.configure_bridge()
        current = store.load_current()
        area = store.upsert_area(
            current,
            workspace=self.workspace,
            access_mode="trusted",
            connection_profile="tunnellio_bridge",
        )
        store.save_current(current)
        reloaded = store.load_current()
        self.assertEqual(reloaded["activeAreaId"], area["id"])
        saved = reloaded["areas"][area["id"]]
        self.assertEqual(saved["accessMode"], "trusted")
        self.assertEqual(saved["connectionProfile"], "tunnellio_bridge")

    def test_the_same_folder_is_never_duplicated(self):
        """The same folder spelled differently must resolve to one area.

        Spellings that normalise on every platform: a trailing '.', and a walk
        through a child and back up. Case folding is deliberately not used here,
        because it only holds on Windows: on a case-sensitive filesystem an
        upper-cased path is a genuinely different path, and asserting otherwise
        turned the Linux CI jobs red while Windows stayed green.
        """
        current = store.load_current()
        store.upsert_area(current, workspace=self.workspace)
        store.upsert_area(current, workspace=str(self.workspace) + os.sep + ".")
        store.upsert_area(current, workspace=str(self.workspace / "child" / ".."))
        self.assertEqual(len(current["areas"]), 1)

    @unittest.skipUnless(os.name == "nt", "path case folding is a Windows property")
    def test_case_differences_are_folded_on_windows(self):
        current = store.load_current()
        store.upsert_area(current, workspace=self.workspace)
        store.upsert_area(current, workspace=str(self.workspace).upper())
        self.assertEqual(len(current["areas"]), 1)

    def test_each_area_keeps_its_own_credentials_by_default(self):
        current = store.load_current()
        first = store.upsert_area(current, workspace=self.workspace)
        second_path = self.root / "area-two"
        second_path.mkdir()
        second = store.upsert_area(current, workspace=second_path)
        token_one = store.effective_auth(current, first)["token"]
        token_two = store.effective_auth(current, second)["token"]
        self.assertTrue(token_one and token_two)
        self.assertNotEqual(token_one, token_two)

    def test_the_global_flag_shares_one_credential_across_areas(self):
        current = store.load_current()
        first = store.upsert_area(current, workspace=self.workspace, use_global_auth=True)
        second_path = self.root / "area-two"
        second_path.mkdir()
        second = store.upsert_area(current, workspace=second_path, use_global_auth=True)
        self.assertEqual(
            store.effective_auth(current, first)["token"],
            store.effective_auth(current, second)["token"],
        )

    def test_switching_profile_does_not_change_the_token(self):
        self.configure_bridge()
        self.configure_serveo()
        current = store.load_current()
        area = store.upsert_area(
            current,
            workspace=self.workspace,
            connection_profile="tunnellio_bridge",
        )
        before = store.effective_auth(current, area)["token"]
        store.upsert_area(
            current, workspace=self.workspace, connection_profile="serveo_stable"
        )
        after = store.effective_auth(current, current["areas"][area["id"]])["token"]
        self.assertEqual(before, after)

    def test_an_oauth_area_gets_an_owner_code(self):
        current = store.load_current()
        area = store.upsert_area(current, workspace=self.workspace)
        area["auth"]["mode"] = "oauth"
        auth = store.effective_auth(current, area)
        self.assertTrue(auth["oauthOwnerCode"])


class ResolveTests(StoreTestCase):
    def test_resolve_refuses_when_nothing_is_set_up(self):
        with self.assertRaises(ConnectionStoreError):
            store.resolve(store.load_current(), script_dir=PROJECT)

    def test_resolve_refuses_an_area_without_a_profile(self):
        current = store.load_current()
        store.upsert_area(current, workspace=self.workspace)
        with self.assertRaises(ConnectionStoreError) as caught:
            store.resolve(current, script_dir=PROJECT)
        self.assertIn("no connection profile", str(caught.exception).lower())

    def test_resolve_refuses_an_unconfigured_profile(self):
        current = store.load_current()
        store.upsert_area(
            current, workspace=self.workspace, connection_profile="serveo_stable"
        )
        with self.assertRaises(ConnectionStoreError) as caught:
            store.resolve(current, script_dir=PROJECT)
        self.assertIn("profiles.bat", str(caught.exception).lower())

    def test_resolve_returns_a_validated_circuit(self):
        self.configure_serveo()
        current = store.load_current()
        store.upsert_area(
            current,
            workspace=self.workspace,
            access_mode="trusted",
            connection_profile="serveo_stable",
        )
        resolved = store.resolve(current, script_dir=PROJECT)
        self.assertEqual(resolved.circuit.id, "serveo_stable")
        self.assertEqual(resolved.public_url, "https://my-mcp.serveousercontent.com")
        self.assertEqual(resolved.context.local_port, 8765)
        self.assertTrue(resolved.auth["token"])


class LegacyMirrorTests(StoreTestCase):
    def _resolved(self):
        self.configure_serveo()
        current = store.load_current()
        store.upsert_area(
            current,
            workspace=self.workspace,
            access_mode="trusted",
            connection_profile="serveo_stable",
        )
        return store.resolve(current, script_dir=PROJECT)

    def test_mirror_carries_the_active_circuit_only(self):
        mirror = store.legacy_mirror(self._resolved(), version="2.4.0")
        self.assertEqual(mirror["tunnel_backend"], "serveo")
        self.assertEqual(mirror["serveo_hostname"], "my-mcp")
        self.assertEqual(mirror["connection_profile"], "serveo_stable")

    def test_every_foreign_legacy_field_is_written_empty(self):
        mirror = store.legacy_mirror(self._resolved(), version="2.4.0")
        for key in (
            "public_url",
            "tunnel_host",
            "tunnel_domain",
            "tunnellio_token",
            "tunnellio_domain",
        ):
            self.assertEqual(mirror[key], "", key)

    def test_mirror_is_written_to_the_legacy_path(self):
        store.write_legacy_mirror(store.legacy_mirror(self._resolved(), version="2.4.0"))
        written = json.loads(self.legacy_file.read_text(encoding="utf-8"))
        self.assertEqual(written["version"], "2.4.0")
        self.assertTrue(written["allow_commands"])


class LegacyMigrationTests(StoreTestCase):
    def test_explicit_serveo_config_maps_to_the_stable_circuit(self):
        self.assertEqual(
            store.circuit_from_legacy(
                {"tunnel_backend": "serveo", "serveo_hostname": "h"}
            ),
            "serveo_stable",
        )

    def test_serveo_without_hostname_maps_to_temporary(self):
        self.assertEqual(
            store.circuit_from_legacy({"tunnel_backend": "serveo"}), "serveo_temporary"
        )

    def test_tunnellio_split_by_the_evidence_present(self):
        self.assertEqual(
            store.circuit_from_legacy(
                {"tunnel_backend": "tunnellio", "tunnellio_domain": "d"}
            ),
            "tunnellio_stable",
        )
        self.assertEqual(
            store.circuit_from_legacy(
                {"tunnel_backend": "tunnellio", "tunnellio_token": "t"}
            ),
            "tunnellio_random",
        )

    def test_an_empty_config_is_never_guessed_into_a_mode(self):
        self.assertEqual(store.circuit_from_legacy({}), "")
        self.assertEqual(store.circuit_from_legacy({"tunnel_backend": "tunnellio"}), "")

    def test_migration_imports_the_area_and_the_profile(self):
        legacy = {
            "workspace": str(self.workspace),
            "token": "keep-this-token",
            "auth_mode": "legacy",
            "allow_commands": True,
            "port": 8765,
            "tunnel_backend": "serveo",
            "serveo_hostname": "old-host",
            "ssh_key": "C:/keys/old",
        }
        current = store.migrate_legacy(legacy)
        area = next(iter(current["areas"].values()))
        self.assertEqual(area["connectionProfile"], "serveo_stable")
        self.assertEqual(area["accessMode"], "trusted")
        self.assertEqual(area["auth"]["token"], "keep-this-token")
        self.assertEqual(
            store.profile_settings("serveo_stable")["hostname"], "old-host"
        )

    def test_migration_never_rotates_an_existing_token(self):
        legacy = {
            "workspace": str(self.workspace),
            "token": "production-token",
            "tunnel_backend": "serveo",
        }
        current = store.migrate_legacy(legacy)
        area = next(iter(current["areas"].values()))
        self.assertEqual(area["auth"]["token"], "production-token")

    def test_migration_runs_only_once(self):
        legacy = {"workspace": str(self.workspace), "tunnel_backend": "serveo"}
        store.migrate_legacy(legacy)
        store.create_profile("serveo_temporary", "Temp", {})
        second = store.migrate_legacy(
            {"workspace": str(self.root / "other"), "tunnel_backend": "serveo"}
        )
        self.assertEqual(len(second["areas"]), 1)

    def test_migration_does_not_invent_a_mode_from_a_bare_config(self):
        current = store.migrate_legacy({"workspace": str(self.workspace)})
        area = next(iter(current["areas"].values()))
        self.assertEqual(area["connectionProfile"], "")


class UpgradeSafetyTests(unittest.TestCase):
    """A new release unzipped over an old install must not erase operator state."""

    def test_release_archive_excludes_all_operator_state(self):
        for name in (
            "config.json",
            "config.json.bak",
            "connections.cfg",
            "connection-profiles.json",
            "connection-profiles.v2.json",
            "current-connection.json",
            "workflow-profiles.json",
            "runtime.json",
            "connection.txt",
        ):
            self.assertIn(name, build_release.EXCLUDED_FILES, name)

    def test_release_archive_ships_the_blueprints(self):
        packaged = {
            destination.as_posix() for _source, destination in build_release.included_files()
        }
        for circuit_id in (
            "serveo_stable",
            "serveo_temporary",
            "tunnellio_stable",
            "tunnellio_random",
            "tunnellio_bridge",
            "sish",
            "reverse_proxy",
        ):
            self.assertTrue(
                any(
                    path.endswith(f"connections/defaults/{circuit_id}.json")
                    for path in packaged
                ),
                circuit_id,
            )

    def test_release_archive_ships_the_profile_setup_entry_points(self):
        packaged = {
            destination.as_posix() for _source, destination in build_release.included_files()
        }
        self.assertTrue(any(path.endswith("profiles_setup.py") for path in packaged))
        self.assertTrue(any(path.endswith("PROFILES.bat") for path in packaged))


if __name__ == "__main__":
    unittest.main()
