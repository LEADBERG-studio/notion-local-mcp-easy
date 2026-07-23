import asyncio
import importlib
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


class DatabasePluginFoundationTests(unittest.TestCase):
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

    def test_plugin_manager_discovers_sqlite_and_postgres_families(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(workspace, profiles_file, access_mode="file_only")
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=False)
            self.assertIn("sqlite", manager.manifests)
            self.assertIn("postgres", manager.manifests)
            self.assertEqual(manager.states["sqlite"]["status"], "not_attached")
            self.assertEqual(manager.states["postgres"]["status"], "not_attached")

    def test_postgres_plugin_registers_through_shared_registry_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="file_only",
                plugins={
                    "postgres": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "connections": [{"name": "analytics", "database": "analytics"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=False)
            names = {item["name"] for item in mcp.registered}
            self.assertIn("postgres_query", names)
            self.assertNotIn("postgres_execute", names)
            self.assertEqual(manager.states["postgres"]["effectiveMode"], "read_only")
            self.assertEqual(manager.states["postgres"]["status"], "loaded")

    def test_postgres_plugin_query_handler_uses_cli_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "postgres": {
                        "scope": "current",
                        "requestedMode": "read_only",
                        "config": {
                            "connections": [{"name": "analytics", "database": "analytics"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, mcp = self._build_manager(workspace, profiles_file, allow_commands=True)
            handler = next(item["fn"] for item in mcp.registered if item["name"] == "postgres_query")
            fake_result = mock.Mock(returncode=0, stdout="id\tname\n1\tAlice\n2\tBob\n", stderr="")
            with (
                mock.patch("plugins.db_shared.shutil.which", return_value="psql"),
                mock.patch("plugins.db_shared.subprocess.run", return_value=fake_result),
            ):
                payload = asyncio.run(handler(connection="analytics", sql="SELECT * FROM demo", params_json="[]"))
            data = json.loads(payload)
            self.assertEqual(data["connection"], "analytics")
            self.assertEqual(data["rows"][0]["name"], "Alice")
            self.assertEqual(data["rows"][1]["id"], "2")
            self.assertEqual(manager.states["postgres"]["status"], "loaded")

    def test_postgres_global_and_current_scope_merge_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            profiles_file = Path(directory) / "workflow-profiles.json"
            self._write_storage(
                workspace,
                profiles_file,
                access_mode="trusted",
                plugins={
                    "postgres": {
                        "scope": "current",
                        "requestedMode": "full_access",
                        "config": {
                            "connections": [{"name": "analytics", "database": "area_db"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
                global_plugins={
                    "postgres": {
                        "scope": "global",
                        "requestedMode": "read_only",
                        "config": {
                            "connections": [{"name": "analytics", "database": "global_db", "host": "db.internal"}]
                        },
                        "attachedAt": "2026-07-20T22:00:00",
                    }
                },
            )
            manager, _ = self._build_manager(workspace, profiles_file, allow_commands=True)
            self.assertEqual(manager.states["postgres"]["attachScope"], "both")
            self.assertEqual(manager.states["postgres"]["configSource"], "merged")
            self.assertEqual(
                manager.states["postgres"]["config"]["connections"][0]["database"],
                "area_db",
            )


if __name__ == "__main__":
    unittest.main()
