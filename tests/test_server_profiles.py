import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from profiles import default_storage, save_profiles, sync_profiles_with_slots

PROJECT = Path(__file__).resolve().parents[1]


def load_server(workspace: Path, profiles_file: Path):
    os.environ["MCP_TOKEN"] = "profile-test-token"
    os.environ["MCP_BASE_DIR"] = str(workspace)
    os.environ["MCP_PORT"] = "8765"
    os.environ["MCP_ALLOW_COMMANDS"] = "0"
    os.environ["MCP_ALLOWED_COMMANDS"] = "git,python"
    os.environ["MCP_SERVEO_HOSTNAME"] = ""
    os.environ["MCP_PROFILE_STORAGE"] = str(profiles_file)
    os.environ["MCP_PROFILE_ID"] = "profile-1"
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class ServerProfileTests(unittest.TestCase):
    def _write_storage(self, profiles_file: Path, workspace: Path, *, plugins=None, global_plugins=None):
        storage = {
            "schemaVersion": 1,
            "activeProfileId": "profile-1",
            "profiles": {
                "profile-1": {
                    "profileId": "profile-1",
                    "pathSlot": 1,
                    "workspacePath": str(workspace.resolve()),
                    "accessMode": "file_only",
                    "environmentMode": "CUSTOM" if plugins else "DEFAULT",
                    "displayName": workspace.name,
                    "createdAt": "2026-07-20T22:00:00",
                    "updatedAt": "2026-07-20T22:00:00",
                    "metadata": {
                        "createdFrom": "test",
                        "lastSelectedAt": "",
                        "lastKnownGood": True,
                        "notes": "",
                    },
                    "plugins": plugins or {},
                }
            },
            "globalPlugins": global_plugins or {},
        }
        profiles_file.write_text(json.dumps(storage), encoding="utf-8")

    def test_workspace_info_reports_profile_and_plugin_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            self._write_storage(
                profiles_file,
                workspace,
                plugins={
                    "sqlite": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {"connections": [{"name": "main", "path": "main.db"}]},
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            server = load_server(workspace, profiles_file)
            text = asyncio.run(server.workspace_info())
            self.assertIn("active profile id: profile-1", text)
            self.assertIn("active path slot: 1", text)
            self.assertIn("active access mode: file_only", text)
            self.assertIn("active environment mode: CUSTOM", text)
            self.assertIn("plugins discovered:", text)
            self.assertIn("plugins loaded: 1", text)

    def test_plugin_status_shows_global_scope_without_promoting_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            self._write_storage(
                profiles_file,
                workspace,
                global_plugins={
                    "sqlite": {
                        "scope": "global",
                        "requestedMode": "read_only",
                        "config": {"connections": [{"name": "shared", "path": "shared.db"}]},
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            server = load_server(workspace, profiles_file)
            text = asyncio.run(server.plugin_status())
            self.assertIn("active environment mode: DEFAULT", text)
            self.assertIn("last startup error: (none)", text)
            self.assertIn("[sqlite] scope: global", text)
            self.assertIn("[sqlite] requested mode: read_only", text)
            self.assertIn("[sqlite] effective mode: read_only", text)
            self.assertIn("[sqlite] config source: global", text)
            self.assertIn("[sqlite] manifest path:", text)
            self.assertIn("[sqlite] entrypoint path:", text)
            self.assertIn("[sqlite] health:", text)

    def test_list_plugins_reports_discoverable_but_not_attached_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            self._write_storage(profiles_file, workspace)
            server = load_server(workspace, profiles_file)
            text = asyncio.run(server.list_plugins())
            self.assertIn("plugins root:", text)
            self.assertIn("sqlite: SQLite", text)
            self.assertIn("status=not_attached", text)
            self.assertIn("scope=none", text)


    def test_plugin_status_surfaces_failed_plugin_startup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            self._write_storage(
                profiles_file,
                workspace,
                plugins={
                    "sqlite": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {},
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            server = load_server(workspace, profiles_file)
            text = asyncio.run(server.plugin_status())
            self.assertIn("last startup error:", text)
            self.assertIn("[sqlite] status: failed", text)
            self.assertIn("[sqlite] error: SQLite plugin requires config.connections", text)

    def test_workspace_info_after_migration_reports_default_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            storage = default_storage()
            updated, active_profile, _ = sync_profiles_with_slots(
                storage,
                {1: str(workspace)},
                legacy_allow_commands=False,
                active_workspace=str(workspace),
                created_from="migration-test",
            )
            profile_id = active_profile["profileId"]
            migrated_profile = updated["profiles"].pop(profile_id)
            migrated_profile["profileId"] = "profile-1"
            updated["profiles"] = {"profile-1": migrated_profile}
            updated["activeProfileId"] = "profile-1"
            save_profiles(updated, profiles_file)
            server = load_server(workspace, profiles_file)
            text = asyncio.run(server.workspace_info())
            self.assertIn("active profile id: profile-1", text)
            self.assertIn("active path slot: 1", text)
            self.assertIn("active environment mode: DEFAULT", text)


if __name__ == "__main__":
    unittest.main()
