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


class IdeProviderManifestTests(unittest.TestCase):
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

    def _build_manager(self, workspace: Path, profiles_file: Path, *, allow_commands: bool) -> tuple[PluginManager, DummyMCP]:
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
                allow_commands=allow_commands,
                mcp=mcp,
            )
        return manager, mcp

    def test_plugin_manager_discovers_ide_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=False)
            self.assertIn("ide_provider", manager.manifests)
            self.assertEqual(manager.states["ide_provider"]["status"], "not_attached")

    def test_manifest_has_all_nine_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=False)
            manifest = manager.manifests["ide_provider"]
            tool_names = {tool["name"] for tool in manifest["tools"]}
            expected = {
                "ide_provider_start",
                "ide_provider_stop",
                "ide_provider_status",
                "ide_provider_show_config",
                "ide_provider_wait_request",
                "ide_provider_send_response",
                "ide_provider_fail_request",
                "ide_provider_get_logs",
                "ide_provider_rotate_token",
            }
            self.assertEqual(tool_names, expected)

    def test_all_tool_names_use_namespace_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=False)
            for tool in manager.manifests["ide_provider"]["tools"]:
                self.assertTrue(tool["name"].startswith("ide_provider_"))

    def test_full_access_tools_are_start_stop_rotate_token(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=False)
            full_access_tools = {
                tool["name"]
                for tool in manager.manifests["ide_provider"]["tools"]
                if tool["mode_required"] == "full_access"
            }
            self.assertEqual(full_access_tools, {
                "ide_provider_start",
                "ide_provider_stop",
                "ide_provider_rotate_token",
            })

    def test_read_only_tools_register_in_file_only_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "ide_provider": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {},
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=False)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("ide_provider_status", names)
            self.assertIn("ide_provider_show_config", names)
            self.assertIn("ide_provider_wait_request", names)
            self.assertIn("ide_provider_send_response", names)
            self.assertIn("ide_provider_fail_request", names)
            self.assertIn("ide_provider_get_logs", names)
            self.assertNotIn("ide_provider_start", names)
            self.assertNotIn("ide_provider_stop", names)
            self.assertNotIn("ide_provider_rotate_token", names)
            self.assertEqual(manager.states["ide_provider"]["effectiveMode"], "read_only")

    def test_full_access_tools_register_in_trusted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "ide_provider": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {},
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=True)
            names = {item["name"] for item in mcp.registered}
            expected = {
                "ide_provider_start",
                "ide_provider_stop",
                "ide_provider_status",
                "ide_provider_show_config",
                "ide_provider_wait_request",
                "ide_provider_send_response",
                "ide_provider_fail_request",
                "ide_provider_get_logs",
                "ide_provider_rotate_token",
            }
            self.assertTrue(expected.issubset(names))
            self.assertEqual(manager.states["ide_provider"]["effectiveMode"], "full_access")

    def test_required_fields_present(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=False)
            manifest = manager.manifests["ide_provider"]
            self.assertIn("id", manifest)
            self.assertIn("entrypoint", manifest)
            self.assertIn("supported_modes", manifest)
            self.assertIsInstance(manifest["supported_modes"], list)
            self.assertGreater(len(manifest["supported_modes"]), 0)
            for mode in manifest["supported_modes"]:
                self.assertIn(mode, {"read_only", "full_access"})
            self.assertIn("install_scope_support", manifest)
            self.assertIn(manifest["install_scope_support"], {"current", "global", "both"})
            self.assertIn("tools", manifest)
            self.assertIsInstance(manifest["tools"], list)


if __name__ == "__main__":
    unittest.main()
