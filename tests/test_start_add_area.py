"""Adding a folder during START must not decide things behind your back.

add_area() used to hardcode access_mode="file_only" and useGlobalAuth=False.
So a folder added from START was silently read-only, and resolving it minted a
brand new Bearer token, which broke every client already pointed at this server.
The only way out was editing the config files by hand.
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


class AddAreaDuringStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.first = root / "first"
        self.first.mkdir()
        self.second = root / "second"
        self.second.mkdir()
        patch = mock.patch.object(store, "CURRENT_FILE", root / "current.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.current = store.load_current()
        store.upsert_area(
            self.current,
            workspace=self.first,
            access_mode="trusted",
            connection_profile="profile-1",
        )

    def add(self, answer, reuse=True):
        def answer_yes_no(question, default=True):
            # Two different questions reach this prompt; only the token one
            # varies per test.
            return reuse if "Reuse" in question else True

        with mock.patch.object(runtime, "prompt_existing_folder", return_value=self.second), \
             mock.patch.object(runtime.flow, "default_prompt", return_value=answer), \
             mock.patch.object(runtime.flow, "ask_yes_no", side_effect=answer_yes_no), \
             mock.patch.object(store, "default_profile_id", return_value="profile-1"), \
             mock.patch.object(store, "get_profile", return_value={"name": "Profile 1"}):
            return runtime.add_area(self.current, Path(self.tmp.name))

    def test_the_access_mode_is_asked_not_assumed(self):
        area = self.add("2")
        self.assertEqual(area["accessMode"], "trusted")

    def test_file_only_is_still_available(self):
        area = self.add("1")
        self.assertEqual(area["accessMode"], "file_only")

    def test_the_prompt_defaults_to_what_is_already_in_use(self):
        self.assertEqual(runtime._inherited_access_mode(self.current), "trusted")

    def test_no_new_bearer_token_appears(self):
        first = self.current["areas"][store.area_id_for(self.first)]
        existing = store.effective_auth(self.current, first)["token"]
        area = self.add("2")
        self.assertEqual(store.effective_auth(self.current, area)["token"], existing)

    def test_a_folder_on_another_channel_gets_its_own_token(self):
        """Sharing is per connection profile, not across the whole install.

        Folders on one profile answer at one address and must share a token.
        A folder on a different profile is a different address, so it gets its
        own credentials.
        """
        first = self.current["areas"][store.area_id_for(self.first)]
        existing = store.effective_auth(self.current, first)["token"]
        area = self.add("2")
        area["connectionProfile"] = "profile-2"
        area["auth"]["token"] = ""
        self.assertNotEqual(store.effective_auth(self.current, area)["token"], existing)

    def test_the_standing_profile_is_inherited(self):
        area = self.add("2")
        self.assertEqual(area["connectionProfile"], "profile-1")

    def test_an_area_already_on_shared_auth_is_followed_without_asking(self):
        for area in self.current["areas"].values():
            area["useGlobalAuth"] = True
        with mock.patch.object(runtime.flow, "ask_yes_no") as asked:
            added = self.add("2")
        self.assertTrue(added["useGlobalAuth"])
        self.assertFalse(
            any("Reuse" in str(call) for call in asked.call_args_list),
            "should not ask about a switch that is already on",
        )


if __name__ == "__main__":
    unittest.main()
