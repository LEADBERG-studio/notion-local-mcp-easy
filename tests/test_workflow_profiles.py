import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugin_runtime import PluginManager
from profiles import (
    apply_profile_to_legacy_config,
    attach_plugin_record,
    build_profile,
    default_storage,
    detach_plugin_record,
    load_profiles,
    normalize_profile,
    save_profiles,
    sync_profiles_with_slots,
)


class DummyMCP:
    def __init__(self):
        self.registered = []

    def tool(self, name=None, title=None, description=None, **kwargs):
        def decorator(fn):
            self.registered.append(
                {
                    "name": name or fn.__name__,
                    "title": title,
                    "description": description,
                    "fn": fn,
                }
            )
            return fn

        return decorator


class ProfileStorageTests(unittest.TestCase):
    def test_sync_profiles_with_slots_migrates_legacy_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace_one = root / "workspace-one"
            workspace_two = root / "workspace-two"
            workspace_one.mkdir()
            workspace_two.mkdir()
            storage = default_storage()
            updated, active_profile, changed = sync_profiles_with_slots(
                storage,
                {1: str(workspace_one), 2: str(workspace_two)},
                legacy_allow_commands=True,
                active_workspace=str(workspace_two),
                created_from="migration",
            )
            self.assertTrue(changed)
            self.assertEqual(len(updated["profiles"]), 2)
            self.assertIsNotNone(active_profile)
            assert active_profile is not None
            self.assertEqual(active_profile["workspacePath"], str(workspace_two.resolve()))
            self.assertEqual(active_profile["accessMode"], "trusted")
            self.assertEqual(active_profile["environmentMode"], "DEFAULT")

    def test_global_plugin_keeps_default_but_current_plugin_sets_custom(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            storage = default_storage()
            profile = build_profile(
                profile_id="profile-1",
                path_slot=1,
                workspace_path=workspace,
                access_mode="file_only",
                created_from="test",
            )
            storage["profiles"] = {"profile-1": profile}
            storage["activeProfileId"] = "profile-1"

            updated = attach_plugin_record(
                storage,
                profile_id="profile-1",
                plugin_id="sqlite",
                scope="global",
                requested_mode="read_only",
                config={"connections": [{"name": "shared", "path": "shared.db"}]},
            )
            self.assertIn("sqlite", updated["globalPlugins"])
            self.assertEqual(
                updated["profiles"]["profile-1"]["environmentMode"], "DEFAULT"
            )

            updated = attach_plugin_record(
                updated,
                profile_id="profile-1",
                plugin_id="sqlite",
                scope="current",
                requested_mode="read_only",
                config={"connections": [{"name": "main", "path": "main.db"}]},
            )
            self.assertEqual(
                updated["profiles"]["profile-1"]["environmentMode"], "CUSTOM"
            )

            updated = detach_plugin_record(
                updated,
                profile_id="profile-1",
                plugin_id="sqlite",
                scope="current",
            )
            self.assertEqual(
                updated["profiles"]["profile-1"]["environmentMode"], "DEFAULT"
            )

    def test_apply_profile_to_legacy_config_mirrors_workspace_and_mode(self):
        profile = {
            "workspacePath": str(PROJECT),
            "accessMode": "trusted",
        }
        config = {
            "token": "fixed-token",
            "workspace": "old",
            "allow_commands": False,
        }
        updated = apply_profile_to_legacy_config(config, profile)
        self.assertEqual(updated["workspace"], str(PROJECT))
        self.assertTrue(updated["allow_commands"])
        self.assertEqual(updated["token"], "fixed-token")



    def test_connection_type_survives_profile_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = build_profile(
                profile_id="p1", path_slot=1, workspace_path=directory,
                access_mode="trusted", created_from="test",
                metadata={"connectionType": "serveo_stable"},
            )
            profile["connectionType"] = "serveo_stable"
            normalized = normalize_profile(profile, fallback_access_mode="file_only")
        self.assertEqual(normalized["connectionType"], "serveo_stable")
        self.assertEqual(normalized["metadata"]["connectionType"], "serveo_stable")

class PluginManagerTests(unittest.TestCase):
    def _write_storage(self, workspace: Path, profiles_file: Path, *, access_mode: str, plugins=None, global_plugins=None):
        storage = {
            "schemaVersion": 1,
            "activeProfileId": "profile-1",
            "profiles": {
                "profile-1": {
                    "profileId": "profile-1",
                    "pathSlot": 1,
                    "workspacePath": str(workspace.resolve()),
                    "accessMode": access_mode,
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

    def test_plugin_manager_registers_full_access_tools_only_in_trusted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "sqlite": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "connections": [{"name": "main", "path": "main.db"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                mcp = DummyMCP()
                manager = PluginManager(
                    server_dir=PROJECT,
                    base_dir=workspace,
                    allow_commands=True,
                    mcp=mcp,
                )
            names = {item["name"] for item in mcp.registered}
            self.assertIn("sqlite_query", names)
            self.assertIn("sqlite_execute", names)
            self.assertEqual(manager.states["sqlite"]["status"], "loaded")
            self.assertEqual(manager.states["sqlite"]["effectiveMode"], "full_access")

    def test_plugin_manager_downgrades_full_access_request_under_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "sqlite": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "connections": [{"name": "main", "path": "main.db"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                mcp = DummyMCP()
                manager = PluginManager(
                    server_dir=PROJECT,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=mcp,
                )
            names = {item["name"] for item in mcp.registered}
            self.assertIn("sqlite_query", names)
            self.assertNotIn("sqlite_execute", names)
            self.assertEqual(manager.states["sqlite"]["status"], "loaded")
            self.assertEqual(manager.states["sqlite"]["effectiveMode"], "read_only")

    def test_attach_plugin_persists_current_and_global_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            save_profiles(
                {
                    "schemaVersion": 1,
                    "activeProfileId": "profile-1",
                    "profiles": {
                        "profile-1": build_profile(
                            profile_id="profile-1",
                            path_slot=1,
                            workspace_path=workspace,
                            access_mode="file_only",
                            created_from="test",
                        )
                    },
                    "globalPlugins": {},
                },
                profiles_file,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                mcp = DummyMCP()
                manager = PluginManager(
                    server_dir=PROJECT,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=mcp,
                )
                manager.attach_plugin(
                    "sqlite",
                    "current",
                    "read_only",
                    {"connections": [{"name": "main", "path": "main.db"}]},
                )
                manager.attach_plugin(
                    "sqlite",
                    "global",
                    "read_only",
                    {"connections": [{"name": "shared", "path": "shared.db"}]},
                )
            saved = load_profiles(profiles_file)
            self.assertIn("sqlite", saved["profiles"]["profile-1"]["plugins"])
            self.assertIn("sqlite", saved["globalPlugins"])
            self.assertEqual(
                saved["profiles"]["profile-1"]["environmentMode"], "CUSTOM"
            )


    def test_plugin_manager_merges_global_and_current_scope_configs(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "sqlite": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {
                            "connections": [{"name": "main", "path": "current.db"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
                global_plugins={
                    "sqlite": {
                        "scope": "global",
                        "requestedMode": "read_only",
                        "config": {
                            "connections": [{"name": "shared", "path": "global.db"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                manager = PluginManager(
                    server_dir=PROJECT,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=DummyMCP(),
                )
            self.assertEqual(manager.states["sqlite"]["attachScope"], "both")
            self.assertEqual(manager.states["sqlite"]["configSource"], "merged")
            self.assertEqual(
                manager.states["sqlite"]["config"]["connections"][0]["path"],
                "current.db",
            )

    def test_plugin_manager_marks_invalid_attached_plugin_as_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "sqlite": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {},
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                manager = PluginManager(
                    server_dir=PROJECT,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=DummyMCP(),
                )
            self.assertEqual(manager.states["sqlite"]["status"], "failed")
            self.assertIn("config.connections", manager.states["sqlite"]["error"])

    def test_plugin_manager_marks_invalid_manifest_as_failed_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            server_dir = root / "server"
            broken_plugin_dir = server_dir / "plugins" / "broken"
            broken_plugin_dir.mkdir(parents=True)
            (broken_plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": "broken",
                        "display_name": "Broken plugin",
                        "supported_modes": ["read_only"],
                        "tools": [],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                manager = PluginManager(
                    server_dir=server_dir,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=DummyMCP(),
                )
            self.assertIn("broken", manager.manifests)
            self.assertEqual(manager.states["broken"]["status"], "failed")
            self.assertIn("invalid manifest", manager.states["broken"]["error"])

    def test_attach_plugin_rejects_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            profiles_file = root / "workflow-profiles.json"
            save_profiles(
                {
                    "schemaVersion": 1,
                    "activeProfileId": "profile-1",
                    "profiles": {
                        "profile-1": build_profile(
                            profile_id="profile-1",
                            path_slot=1,
                            workspace_path=workspace,
                            access_mode="file_only",
                            created_from="test",
                        )
                    },
                    "globalPlugins": {},
                },
                profiles_file,
            )
            server_dir = root / "server"
            broken_plugin_dir = server_dir / "plugins" / "broken"
            broken_plugin_dir.mkdir(parents=True)
            (broken_plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": "broken",
                        "display_name": "Broken plugin",
                        "supported_modes": ["read_only"],
                        "tools": [],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": str(profiles_file),
                    "MCP_PROFILE_ID": "profile-1",
                },
                clear=False,
            ):
                manager = PluginManager(
                    server_dir=server_dir,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=DummyMCP(),
                )
                with self.assertRaisesRegex(ValueError, "invalid manifest"):
                    manager.attach_plugin("broken", "current", "read_only", {})

    def test_plugin_manager_supports_legacy_synthetic_profile_context(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "MCP_PROFILE_STORAGE": "",
                    "MCP_PROFILE_ID": "",
                },
                clear=False,
            ):
                manager = PluginManager(
                    server_dir=PROJECT,
                    base_dir=workspace,
                    allow_commands=False,
                    mcp=DummyMCP(),
                )
            self.assertEqual(manager.profile_context["profileMode"], "legacy")
            self.assertEqual(manager.active_profile["profileId"], "legacy-synthetic")
            self.assertEqual(manager.active_profile["environmentMode"], "DEFAULT")
            self.assertEqual(manager.states["sqlite"]["status"], "not_attached")


if __name__ == "__main__":
    unittest.main()
