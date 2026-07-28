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
from plugin_setup import collect_ide_gateway_config, collect_config, local_config_path, remove_local_plugin_config, write_local_plugin_config

class DummyMCP:
    def __init__(self):
        self.registered = []
    def tool(self, name=None, title=None, description=None, **kwargs):
        def decorator(fn):
            self.registered.append({"name": name or fn.__name__, "fn": fn})
            return fn
        return decorator

def write_dummy_plugin(root: Path) -> Path:
    plugin_dir = root / "plugins" / "dummy"
    plugin_dir.mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "dummy",
        "display_name": "Dummy",
        "version": "1.0.0",
        "entrypoint": "plugin.py",
        "supported_modes": ["read_only", "full_access"],
        "tools": [
            {"name": "dummy_ping", "title": "Ping", "description": "Ping", "mode_required": "read_only", "input_schema": {"type": "object", "properties": {}}, "output_schema": {"type": "object"}, "handler_ref": "dummy.ping"},
            {"name": "dummy_write", "title": "Write", "description": "Write", "mode_required": "full_access", "input_schema": {"type": "object", "properties": {}}, "output_schema": {"type": "object"}, "handler_ref": "dummy.write"}
        ]
    }), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "def validate_config(config, context):\n    return dict(config)\n\n"
        "def healthcheck(context):\n    return {'ok': True, 'config': context.get('pluginConfig')}\n\n"
        "def invoke(tool_name, arguments, context):\n    return {'tool': tool_name, 'config': context.get('pluginConfig')}\n",
        encoding="utf-8"
    )
    return plugin_dir

def write_profiles(path: Path, workspace: Path, access_mode: str = "trusted") -> None:
    path.write_text(json.dumps({
        "schemaVersion": 1,
        "activeProfileId": "profile-1",
        "profiles": {
            "profile-1": {
                "profileId": "profile-1",
                "pathSlot": 1,
                "workspacePath": str(workspace.resolve()),
                "accessMode": access_mode,
                "environmentMode": "DEFAULT",
                "displayName": workspace.name,
                "createdAt": "2026-07-27T12:00:00",
                "updatedAt": "2026-07-27T12:00:00",
                "metadata": {"createdFrom": "test", "lastSelectedAt": "", "lastKnownGood": True, "notes": ""},
                "plugins": {}
            }
        },
        "globalPlugins": {}
    }), encoding="utf-8")

class PluginLocalSetupTests(unittest.TestCase):
    def test_current_local_config_loads_plugin_without_profile_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            plugin_dir = write_dummy_plugin(root)
            profiles = root / "workflow-profiles.json"
            write_profiles(profiles, workspace)
            path = write_local_plugin_config(plugin_dir, "current", "full_access", {"answer": 42}, profiles)
            self.assertEqual(path, local_config_path(plugin_dir, "current", "profile-1"))
            storage = json.loads(profiles.read_text(encoding="utf-8"))
            self.assertEqual(storage["profiles"]["profile-1"]["plugins"], {})
            with mock.patch.dict(os.environ, {"MCP_PROFILE_STORAGE": str(profiles), "MCP_PROFILE_ID": "profile-1"}, clear=False):
                mcp = DummyMCP()
                manager = PluginManager(server_dir=root, base_dir=workspace, allow_commands=True, mcp=mcp)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("dummy_ping", names)
            self.assertIn("dummy_write", names)
            self.assertEqual(manager.states["dummy"]["status"], "loaded")
            self.assertEqual(manager.states["dummy"]["attachScope"], "current")
            self.assertEqual(manager.states["dummy"]["configSource"], "plugin_local_current")
            self.assertEqual(manager.states["dummy"]["config"], {"answer": 42})
            self.assertEqual(manager.active_profile["environmentMode"], "CUSTOM")

    def test_global_local_config_loads_without_customizing_active_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            plugin_dir = write_dummy_plugin(root)
            profiles = root / "workflow-profiles.json"
            write_profiles(profiles, workspace, access_mode="file_only")
            write_local_plugin_config(plugin_dir, "global", "full_access", {"shared": True}, profiles)
            with mock.patch.dict(os.environ, {"MCP_PROFILE_STORAGE": str(profiles), "MCP_PROFILE_ID": "profile-1"}, clear=False):
                mcp = DummyMCP()
                manager = PluginManager(server_dir=root, base_dir=workspace, allow_commands=False, mcp=mcp)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("dummy_ping", names)
            self.assertNotIn("dummy_write", names)
            self.assertEqual(manager.states["dummy"]["attachScope"], "global")
            self.assertEqual(manager.states["dummy"]["effectiveMode"], "read_only")
            self.assertEqual(manager.states["dummy"]["configSource"], "plugin_local_global")
            self.assertEqual(manager.active_profile["environmentMode"], "DEFAULT")

    def test_disable_removes_current_local_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            plugin_dir = write_dummy_plugin(root)
            profiles = root / "workflow-profiles.json"
            write_profiles(profiles, workspace)
            path = write_local_plugin_config(plugin_dir, "current", "read_only", {}, profiles)
            self.assertTrue(path.is_file())
            removed = remove_local_plugin_config(plugin_dir, "current", profiles)
            self.assertEqual(removed, path)
            self.assertFalse(path.exists())


    def test_ide_gateway_enable_uses_safe_defaults_without_upstream(self):
        # ENABLE path goes through collect_config with default (empty) inputs.
        # The bridge is served by the model through a long-lived wait_request;
        # no responder config is produced.
        with mock.patch("builtins.input", return_value=""):
            config = collect_config("ide_gateway", {})
        self.assertRegex(config["default_api_key"], r"^ideg_[A-Za-z0-9_-]{16,}$")
        self.assertTrue(config["autostart"])
        self.assertEqual(config["default_port"], 8787)
        self.assertNotIn("responder_enabled", config)

    def test_packaged_plugins_have_bat_wrappers(self):
        for manifest in sorted((PROJECT / "plugins").glob("*/plugin.json")):
            plugin_dir = manifest.parent
            for name in ["SETUP.bat", "ENABLE.bat", "DISABLE.bat", "STATUS.bat"]:
                wrapper = plugin_dir / name
                self.assertTrue(wrapper.is_file(), f"{plugin_dir.name} missing {name}")
                text = wrapper.read_text(encoding="utf-8")
                self.assertIn('set "PLUGIN_DIR=%~dp0."', text)
                self.assertIn('--plugin-dir "%PLUGIN_DIR%"', text)

    def test_ide_gateway_setup_generates_api_key_with_default_preset(self):
        with mock.patch("builtins.input", return_value=""):
            config = collect_ide_gateway_config({})
        self.assertRegex(config["default_api_key"], r"^ideg_[A-Za-z0-9_-]{16,}$")
        self.assertTrue(config["autostart"])
        self.assertEqual(config["default_port"], 8787)
        self.assertNotIn("responder_enabled", config)

if __name__ == "__main__":
    unittest.main()
