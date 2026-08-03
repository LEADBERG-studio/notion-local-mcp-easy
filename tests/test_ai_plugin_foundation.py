import asyncio
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


class AiPluginFoundationTests(unittest.TestCase):
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

    def test_subagent_tools_load_in_file_only_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "subagent": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "base_url": "https://example.test/v1",
                            "model": "demo-model",
                            "api_key_env": "OPENAI_API_KEY",
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=False)
            names = {item["name"] for item in mcp.registered}
            # Both tools are read-only: talking to a remote model does not touch
            # the workspace. What the access mode gates is the effective plugin
            # mode, not the tool set.
            self.assertIn("subagent_ask", names)
            self.assertIn("subagent_say", names)
            self.assertEqual(manager.states["subagent"]["effectiveMode"], "read_only")

    def test_subagent_tools_load_in_trusted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "subagent": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "base_url": "https://example.test/v1",
                            "model": "demo-model",
                            "api_key_env": "OPENAI_API_KEY",
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=True)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("subagent_ask", names)
            self.assertIn("subagent_say", names)
            self.assertEqual(manager.states["subagent"]["effectiveMode"], "full_access")

    def test_subagent_uses_env_secret_without_leaking_it(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "subagent": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {
                            "base_url": "https://example.test/v1",
                            "model": "demo-model",
                            "api_key_env": "OPENAI_API_KEY",
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "super-secret"}, clear=False):
                manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=False)
                handler = next(item["fn"] for item in mcp.registered if item["name"] == "subagent_ask")
                fake_response = mock.MagicMock()
                fake_response.read.return_value = json.dumps(
                    {
                        "choices": [{"message": {"content": "Hello from model"}}],
                        "usage": {"total_tokens": 12},
                    }
                ).encode("utf-8")
                fake_urlopen = mock.MagicMock()
                fake_urlopen.__enter__.return_value = fake_response
                with mock.patch("plugins.subagent.plugin.urllib.request.urlopen", return_value=fake_urlopen) as urlopen:
                    payload = asyncio.run(handler(prompt="Hello"))
                request = urlopen.call_args.args[0]
                self.assertEqual(request.headers["Authorization"], "Bearer super-secret")
                data = json.loads(payload)
                self.assertEqual(data["reply"], "Hello from model")
                # The endpoint and the model id must not travel back.
                self.assertNotIn("example.test", payload)
                health_text = json.dumps(manager.states["subagent"].get("health", {}), ensure_ascii=False)
                self.assertIn("OPENAI_API_KEY", health_text)
                self.assertNotIn("super-secret", health_text)

    def test_ai_plugin_global_and_current_scope_merge_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "subagent": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "base_url": "https://example.test/v1",
                            "model": "area-model",
                            "api_key_env": "OPENAI_API_KEY",
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
                global_plugins={
                    "subagent": {
                        "scope": "global",
                        "requestedMode": "read_only",
                        "config": {
                            "base_url": "https://example.test/v1",
                            "model": "global-model",
                            "api_key_env": "OPENAI_API_KEY",
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=True)
            self.assertEqual(manager.states["subagent"]["attachScope"], "both")
            self.assertEqual(manager.states["subagent"]["configSource"], "merged")
            self.assertEqual(manager.states["subagent"]["config"]["model"], "area-model")


if __name__ == "__main__":
    unittest.main()
