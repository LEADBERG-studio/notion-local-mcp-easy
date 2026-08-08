"""Credentials belong to the channel, not to the folder.

The connection profile is the public address. Two folders on one profile are
reached at the same URL, so giving each its own Bearer meant that merely
switching folders invalidated whatever the operator had configured in their MCP
client, and the only cure was editing the config by hand.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import connection_runtime as runtime
from connections import store


class CredentialsFollowTheChannel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("a", "b", "c"):
            (self.root / name).mkdir()
        patch = mock.patch.object(store, "CURRENT_FILE", self.root / "current.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.current = store.load_current()

    def area(self, name, profile):
        return store.upsert_area(
            self.current, workspace=self.root / name, connection_profile=profile
        )

    def test_two_folders_on_one_profile_share_the_token(self):
        first = self.area("a", "mcp")
        second = self.area("b", "mcp")
        self.assertEqual(
            store.effective_auth(self.current, first)["token"],
            store.effective_auth(self.current, second)["token"],
        )

    def test_different_profiles_keep_separate_tokens(self):
        first = self.area("a", "mcp")
        other = self.area("c", "second-channel")
        self.assertNotEqual(
            store.effective_auth(self.current, first)["token"],
            store.effective_auth(self.current, other)["token"],
        )

    def test_an_existing_token_is_never_replaced(self):
        first = self.area("a", "mcp")
        store.effective_auth(self.current, first)
        second = self.area("b", "mcp")
        second["auth"]["token"] = "bridge-secret-token-MINE"
        self.assertEqual(
            store.effective_auth(self.current, second)["token"],
            "bridge-secret-token-MINE",
        )

    def test_a_folder_with_no_profile_gets_its_own_token(self):
        lonely = self.area("a", "")
        self.assertTrue(store.effective_auth(self.current, lonely)["token"])

    def test_shared_auth_areas_are_not_used_as_a_source(self):
        first = self.area("a", "mcp")
        store.effective_auth(self.current, first)
        first["useGlobalAuth"] = True
        second = self.area("b", "mcp")
        self.assertNotEqual(
            store.effective_auth(self.current, second)["token"],
            first["auth"]["token"],
        )


class ReportingOldInstallations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("a", "b"):
            (self.root / name).mkdir()
        patch = mock.patch.object(store, "CURRENT_FILE", self.root / "current.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.current = store.load_current()
        self.first = store.upsert_area(
            self.current, workspace=self.root / "a", connection_profile="mcp"
        )
        self.second = store.upsert_area(
            self.current, workspace=self.root / "b", connection_profile="mcp"
        )
        store.effective_auth(self.current, self.first)
        store.effective_auth(self.current, self.second)

    def diverge(self):
        self.second["auth"]["token"] = "bridge-secret-token-OLD"

    def test_a_consistent_installation_reports_nothing(self):
        self.assertEqual(store.areas_with_conflicting_auth(self.current), [])

    def test_divergence_is_reported(self):
        self.diverge()
        self.assertEqual(store.areas_with_conflicting_auth(self.current), [["a", "b"]])

    def test_setup_offers_to_align_and_does_when_accepted(self):
        self.diverge()
        with mock.patch.object(runtime.flow, "ask_yes_no", return_value=True):
            runtime._offer_to_align_auth(self.current, self.root / "b")
        self.assertEqual(store.areas_with_conflicting_auth(self.current), [])

    def test_declining_leaves_the_token_alone(self):
        self.diverge()
        with mock.patch.object(runtime.flow, "ask_yes_no", return_value=False):
            runtime._offer_to_align_auth(self.current, self.root / "b")
        self.assertEqual(self.second["auth"]["token"], "bridge-secret-token-OLD")

    def test_nothing_is_asked_when_there_is_no_conflict(self):
        with mock.patch.object(runtime.flow, "ask_yes_no") as asked:
            runtime._offer_to_align_auth(self.current, self.root / "b")
        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
