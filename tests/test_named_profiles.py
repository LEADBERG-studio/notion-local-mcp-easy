"""Named connection profiles (2.4.1).

A profile is a named instance of a circuit, not the circuit itself. The case
that drove this: two folders using the same protocol against different domains
with different keys. Both must coexist, be told apart at a glance, and start in
one keypress.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import connection_runtime  # noqa: E402
from connections import store  # noqa: E402
from connections.base import ConnectionConfigError  # noqa: E402
from connections.store import ConnectionStoreError  # noqa: E402


class NamedProfileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.current_file = self.root / "current-connection.json"
        self.profiles_file = self.root / "connection-profiles.v2.json"
        self.key = self.root / "id_key"
        self.key.write_text("KEY", encoding="utf-8")
        self._patches = [
            mock.patch.object(store, "CURRENT_FILE", self.current_file),
            mock.patch.object(store, "PROFILES_FILE", self.profiles_file),
            mock.patch.object(store, "LEGACY_CONFIG_FILE", self.root / "config.json"),
            mock.patch.object(store, "CONFIG_DIR", self.root),
        ]
        for patch in self._patches:
            patch.start()
        connection_runtime.reset_active()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()
        connection_runtime.reset_active()

    def folder(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(exist_ok=True)
        return path

    def stable(self, name: str, domain: str, key: Path | None = None):
        return store.create_profile(
            "tunnellio_stable",
            name,
            {"domain": domain, "ssh_key": str(key or self.key)},
            verified=True,
        )


class InstanceTests(NamedProfileTestCase):
    def test_two_profiles_share_one_protocol(self):
        first = self.stable("Prod MCP", "prod-mcp")
        second = self.stable("Staging MCP", "staging")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["circuit"], second["circuit"])
        self.assertEqual(len(store.profiles_for_circuit("tunnellio_stable")), 2)

    def test_two_profiles_keep_their_own_keys(self):
        other_key = self.root / "other_key"
        other_key.write_text("OTHER", encoding="utf-8")
        first = self.stable("Prod MCP", "prod-mcp")
        second = self.stable("Staging MCP", "staging", other_key)
        self.assertNotEqual(first["settings"]["ssh_key"], second["settings"]["ssh_key"])

    def test_duplicate_names_get_distinct_ids(self):
        first = self.stable("Same Name", "one")
        second = self.stable("Same Name", "two")
        self.assertEqual(first["id"], "same-name")
        self.assertEqual(second["id"], "same-name-2")

    def test_ids_are_readable_slugs(self):
        entry = self.stable("Prod MCP / EU", "prod-eu")
        self.assertEqual(entry["id"], "prod-mcp-eu")

    def test_suggested_name_mentions_what_makes_it_distinct(self):
        name = store.suggest_profile_name("tunnellio_stable", {"domain": "prod-mcp"})
        self.assertIn("prod-mcp", name)
        self.assertEqual(
            store.suggest_profile_name("tunnellio_bridge", {}),
            store.circuit("tunnellio_bridge").title,
        )

    def test_update_changes_settings_but_keeps_identity(self):
        entry = self.stable("Prod MCP", "prod-mcp")
        updated = store.update_profile(entry["id"], settings={**entry["settings"], "domain": "moved"})
        self.assertEqual(updated["id"], entry["id"])
        self.assertEqual(updated["name"], "Prod MCP")
        self.assertEqual(store.profile_settings(entry["id"])["domain"], "moved")

    def test_rename_keeps_settings(self):
        entry = self.stable("Prod MCP", "prod-mcp")
        updated = store.update_profile(entry["id"], name="Production")
        self.assertEqual(updated["name"], "Production")
        self.assertEqual(updated["settings"]["domain"], "prod-mcp")

    def test_delete_removes_only_the_target(self):
        first = self.stable("Prod MCP", "prod-mcp")
        self.stable("Staging MCP", "staging")
        self.assertTrue(store.delete_profile(first["id"]))
        remaining = [entry["name"] for entry in store.list_profiles()]
        self.assertEqual(remaining, ["Staging MCP"])
        self.assertFalse(store.delete_profile("nope"))

    def test_reset_clears_answers_but_keeps_the_profile(self):
        entry = self.stable("Prod MCP", "prod-mcp")
        store.reset_profile(entry["id"])
        self.assertEqual(store.profile_settings(entry["id"])["domain"], "")
        self.assertIsNotNone(store.get_profile(entry["id"]))
        self.assertFalse(store.is_configured(entry["id"]))

    def test_unknown_profile_is_an_explicit_error(self):
        with self.assertRaises(ConnectionConfigError):
            store.profile_settings("ghost")
        with self.assertRaises(ConnectionConfigError):
            store.update_profile("ghost", name="x")


class ListingTests(NamedProfileTestCase):
    def test_listing_is_ordered_and_numberable(self):
        self.stable("Zeta", "zeta")
        self.stable("Alpha", "alpha")
        store.create_profile("tunnellio_bridge", "Bridge", {}, verified=True)
        names = [entry["name"] for entry in store.list_profiles()]
        # Stable circuits come before bridge; names sort within a circuit.
        self.assertEqual(names, ["Alpha", "Zeta", "Bridge"])

    def test_every_listed_profile_exposes_its_settings(self):
        """Setup must show content, not just a name."""
        self.stable("Prod MCP", "prod-mcp")
        entry = store.list_profiles()[0]
        lines = store.describe_profile(entry)
        self.assertTrue(any("prod-mcp" in line for line in lines))
        self.assertTrue(any("SSH key" in line for line in lines))

    def test_find_by_id_and_by_name(self):
        entry = self.stable("Prod MCP", "prod-mcp")
        self.assertEqual(store.find_profile(entry["id"])["id"], entry["id"])
        self.assertEqual(store.find_profile("prod mcp")["id"], entry["id"])
        self.assertIsNone(store.find_profile("nothing like this"))
        self.assertIsNone(store.find_profile(""))

    def test_a_bare_circuit_id_resolves_only_when_unambiguous(self):
        entry = self.stable("Prod MCP", "prod-mcp")
        self.assertEqual(store.find_profile("tunnellio_stable")["id"], entry["id"])
        self.stable("Staging MCP", "staging")
        self.assertIsNone(store.find_profile("tunnellio_stable"))


class AreaBindingTests(NamedProfileTestCase):
    def test_two_folders_one_protocol_two_domains(self):
        """The scenario that motivated named profiles."""
        prod = self.stable("Prod MCP", "prod-mcp")
        staging = self.stable("Staging MCP", "staging")
        current = store.load_current()
        store.upsert_area(current, workspace=self.folder("a"), connection_profile=prod["id"])
        store.upsert_area(current, workspace=self.folder("b"), connection_profile=staging["id"])
        store.save_current(current)

        current = store.load_current()
        urls = set()
        for area_id in current["areas"]:
            resolved = store.resolve(current, script_dir=PROJECT, area_id=area_id)
            urls.add(resolved.public_url)
        self.assertEqual(
            urls,
            {"https://prod-mcp.tunnellio.site", "https://staging.tunnellio.site"},
        )

    def test_resolved_connection_exposes_the_profile_name(self):
        prod = self.stable("Prod MCP", "prod-mcp")
        current = store.load_current()
        store.upsert_area(current, workspace=self.folder("a"), connection_profile=prod["id"])
        resolved = store.resolve(current, script_dir=PROJECT)
        self.assertEqual(resolved.profile_name, "Prod MCP")
        self.assertIn("Prod MCP", connection_runtime.describe(resolved))

    def test_mirror_records_which_profile_was_used(self):
        prod = self.stable("Prod MCP", "prod-mcp")
        current = store.load_current()
        store.upsert_area(current, workspace=self.folder("a"), connection_profile=prod["id"])
        mirror = store.legacy_mirror(store.resolve(current, script_dir=PROJECT), version="2.4.1")
        self.assertEqual(mirror["connection_profile_name"], "Prod MCP")
        self.assertEqual(mirror["connection_profile_id"], prod["id"])
        self.assertEqual(mirror["tunnellio_domain"], "prod-mcp")

    def test_a_deleted_profile_is_reported_not_guessed(self):
        prod = self.stable("Prod MCP", "prod-mcp")
        current = store.load_current()
        store.upsert_area(current, workspace=self.folder("a"), connection_profile=prod["id"])
        store.save_current(current)
        store.delete_profile(prod["id"])
        with self.assertRaises(ConnectionStoreError) as caught:
            store.resolve(store.load_current(), script_dir=PROJECT)
        self.assertIn("no longer exists", str(caught.exception))

    def test_switching_the_profile_keeps_the_area_token(self):
        prod = self.stable("Prod MCP", "prod-mcp")
        staging = self.stable("Staging MCP", "staging")
        current = store.load_current()
        area = store.upsert_area(current, workspace=self.folder("a"), connection_profile=prod["id"])
        before = store.effective_auth(current, area)["token"]
        store.upsert_area(current, workspace=self.folder("a"), connection_profile=staging["id"])
        after = store.effective_auth(current, current["areas"][area["id"]])["token"]
        self.assertEqual(before, after)


class V2MigrationTests(NamedProfileTestCase):
    def test_a_v2_file_becomes_a_named_instance(self):
        self.profiles_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "profiles": {
                        "tunnellio_stable": {
                            "id": "tunnellio_stable",
                            "settings": {"domain": "legacy-one", "ssh_key": str(self.key)},
                            "verifiedAt": "2026-08-03T10:00:00",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        profiles = store.list_profiles()
        self.assertEqual(len(profiles), 1)
        entry = profiles[0]
        # The circuit id is kept as the instance id so existing areas resolve.
        self.assertEqual(entry["id"], "tunnellio_stable")
        self.assertEqual(entry["circuit"], "tunnellio_stable")
        self.assertEqual(entry["name"], store.circuit("tunnellio_stable").title)
        self.assertEqual(entry["settings"]["domain"], "legacy-one")

    def test_an_area_pointing_at_a_circuit_id_still_starts(self):
        self.profiles_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "profiles": {
                        "tunnellio_stable": {
                            "settings": {"domain": "legacy-one", "ssh_key": str(self.key)}
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        current = store.load_current()
        store.upsert_area(
            current, workspace=self.folder("a"), connection_profile="tunnellio_stable"
        )
        resolved = store.resolve(current, script_dir=PROJECT)
        self.assertEqual(resolved.public_url, "https://legacy-one.tunnellio.site")

    def test_flat_config_migration_creates_a_named_profile(self):
        legacy = {
            "workspace": str(self.folder("a")),
            "token": "keep-me",
            "tunnel_backend": "serveo",
            "serveo_hostname": "old-host",
            "ssh_key": str(self.key),
        }
        current = store.migrate_legacy(legacy)
        area = next(iter(current["areas"].values()))
        entry = store.find_profile(area["connectionProfile"])
        self.assertIsNotNone(entry)
        self.assertIn("old-host", entry["name"])
        self.assertEqual(entry["settings"]["hostname"], "old-host")
        self.assertEqual(area["auth"]["token"], "keep-me")


class QuickStartTests(NamedProfileTestCase):
    """START must be one keypress when the area already has a profile."""

    def prepare_area(self, profile_id: str = ""):
        current = store.load_current()
        store.upsert_area(
            current, workspace=self.folder("only"), connection_profile=profile_id or None
        )
        store.save_current(current)

    def test_start_asks_for_the_folder_and_nothing_else(self):
        """The folder is the question worth asking.

        The usual setup is one outbound channel registered once, with many
        folders pointed at it, so START opens on the folder list. Pressing Enter
        takes the last used one and connects; the protocol is never re-asked.
        """
        prod = self.stable("Prod MCP", "prod-mcp")
        self.prepare_area(prod["id"])
        asked = []

        def record(text):
            asked.append(text)
            return ""  # Enter: keep the highlighted work area

        with mock.patch.object(connection_runtime.flow, "default_prompt", record):
            resolved = connection_runtime.start_flow(PROJECT)
        self.assertEqual(resolved.profile_name, "Prod MCP")
        self.assertEqual(len(asked), 1)
        self.assertIn("work area", asked[0].lower())

    def test_an_area_without_a_profile_asks_once(self):
        prod = self.stable("Prod MCP", "prod-mcp")
        self.prepare_area()
        # Folder choice, then the profile question for an area that has none.
        answers = iter(["", "1"])
        with mock.patch.object(
            connection_runtime.flow, "default_prompt", lambda _text: next(answers, "1")
        ):
            resolved = connection_runtime.start_flow(PROJECT)
        self.assertEqual(resolved.profile_name, prod["name"])
        # The answer is remembered, so the next start is a quick start.
        saved = next(iter(store.load_current()["areas"].values()))
        self.assertEqual(saved["connectionProfile"], prod["id"])

    def test_choosing_between_two_areas(self):
        prod = self.stable("Prod MCP", "prod-mcp")
        staging = self.stable("Staging MCP", "staging")
        current = store.load_current()
        store.upsert_area(current, workspace=self.folder("a"), connection_profile=prod["id"])
        store.upsert_area(current, workspace=self.folder("b"), connection_profile=staging["id"])
        store.save_current(current)
        with mock.patch.object(connection_runtime.flow, "default_prompt", lambda _text: "1"):
            resolved = connection_runtime.start_flow(PROJECT)
        self.assertIn(resolved.profile_name, {"Prod MCP", "Staging MCP"})

    def test_nothing_configured_is_a_clear_message(self):
        with self.assertRaises(ConnectionStoreError):
            connection_runtime.start_flow(PROJECT)


class ChoiceTests(NamedProfileTestCase):
    def test_an_incomplete_profile_cannot_be_selected(self):
        broken = store.create_profile("serveo_stable", "Half Done", {}, profile_id="half")
        good = self.stable("Prod MCP", "prod-mcp")
        self.assertFalse(store.is_configured(broken["id"]))
        answers = iter(["1", "2"])
        with mock.patch.object(
            connection_runtime.flow, "default_prompt", lambda _text: next(answers)
        ):
            chosen = connection_runtime.choose_profile()
        self.assertEqual(chosen, good["id"])

    def test_a_profile_can_be_chosen_by_name(self):
        self.stable("Prod MCP", "prod-mcp")
        with mock.patch.object(
            connection_runtime.flow, "default_prompt", lambda _text: "Prod MCP"
        ):
            chosen = connection_runtime.choose_profile()
        self.assertEqual(chosen, "prod-mcp")


if __name__ == "__main__":
    unittest.main()
