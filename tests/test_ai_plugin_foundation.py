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

    def test_ai_plugin_registers_generate_text_but_not_subagent_in_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "openai_compat": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "providers": [
                                {
                                    "name": "main",
                                    "base_url": "https://example.test/v1",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "default_model": "demo-model",
                                }
                            ]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=False)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("openai_compat_generate_text", names)
            self.assertNotIn("openai_compat_run_subagent", names)
            self.assertEqual(manager.states["openai_compat"]["effectiveMode"], "read_only")

    def test_ai_plugin_registers_subagent_tool_only_in_trusted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "openai_compat": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "providers": [
                                {
                                    "name": "main",
                                    "base_url": "https://example.test/v1",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "default_model": "demo-model",
                                    "subagent_model": "demo-subagent",
                                }
                            ]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=True)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("openai_compat_generate_text", names)
            self.assertIn("openai_compat_run_subagent", names)
            self.assertEqual(manager.states["openai_compat"]["effectiveMode"], "full_access")

    def test_generate_text_uses_env_secret_without_leaking_it_to_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "openai_compat": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {
                            "providers": [
                                {
                                    "name": "main",
                                    "base_url": "https://example.test/v1",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "default_model": "demo-model",
                                    "models": ["demo-model", "backup-model"],
                                }
                            ]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "super-secret"}, clear=False):
                manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=False)
                handler = next(item["fn"] for item in mcp.registered if item["name"] == "openai_compat_generate_text")
                fake_response = mock.MagicMock()
                fake_response.read.return_value = json.dumps(
                    {
                        "choices": [{"message": {"content": "Hello from model"}}],
                        "usage": {"total_tokens": 12},
                    }
                ).encode("utf-8")
                fake_urlopen = mock.MagicMock()
                fake_urlopen.__enter__.return_value = fake_response
                with mock.patch("plugins.ai_shared.urllib.request.urlopen", return_value=fake_urlopen) as urlopen:
                    payload = asyncio.run(handler(prompt="Hello", provider="main", model="demo-model"))
                request = urlopen.call_args.args[0]
                self.assertEqual(request.headers["Authorization"], "Bearer super-secret")
                data = json.loads(payload)
                self.assertEqual(data["output"], "Hello from model")
                health_text = json.dumps(manager.states["openai_compat"].get("health", {}), ensure_ascii=False)
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
                    "openai_compat": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "providers": [
                                {
                                    "name": "main",
                                    "base_url": "https://area.test/v1",
                                    "api_key_env": "AREA_KEY",
                                    "default_model": "area-model",
                                }
                            ],
                            "default_provider": "main",
                            "default_model": "area-model"
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
                global_plugins={
                    "openai_compat": {
                        "scope": "global",
                        "requestedMode": "read_only",
                        "config": {
                            "providers": [
                                {
                                    "name": "main",
                                    "base_url": "https://global.test/v1",
                                    "api_key_env": "GLOBAL_KEY",
                                    "default_model": "global-model",
                                }
                            ],
                            "default_provider": "main",
                            "default_model": "global-model"
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=True)
            self.assertEqual(manager.states["openai_compat"]["attachScope"], "both")
            self.assertEqual(manager.states["openai_compat"]["configSource"], "merged")
            self.assertEqual(manager.states["openai_compat"]["config"]["default_model"], "area-model")


if __name__ == "__main__":
    unittest.main()
