# Test Matrix and Resume Safety

## Status
Primary hardening artifact for TASK-013.

## Coverage matrix

| Area | Critical scenario | Evidence | Status |
|---|---|---|---|
| Legacy PATH[N] migration | Legacy saved paths are migrated into profile-aware storage | `tests/test_workflow_profiles.py::test_sync_profiles_with_slots_migrates_legacy_paths` | DONE |
| Legacy config mirroring | Active profile mirrors workspace/mode back into legacy config | `tests/test_workflow_profiles.py::test_apply_profile_to_legacy_config_mirrors_workspace_and_mode` | DONE |
| DEFAULT vs CUSTOM environment | Global-only plugin attach keeps DEFAULT, area-specific attach creates CUSTOM | `tests/test_workflow_profiles.py::test_global_plugin_keeps_default_but_current_plugin_sets_custom` | DONE |
| Current/global attach persistence | Attach flow persists both current and global plugin scopes | `tests/test_workflow_profiles.py::test_attach_plugin_persists_current_and_global_scope` | DONE |
| Loader effective-mode gating | Full-access plugin requests are downgraded in file-only mode | `tests/test_workflow_profiles.py::test_plugin_manager_downgrades_full_access_request_under_file_only` | DONE |
| Trusted-only tool registration | Full-access tools register only in trusted mode | `tests/test_workflow_profiles.py::test_plugin_manager_registers_full_access_tools_only_in_trusted_mode` | DONE |
| Invalid attached plugin handling | Broken attached plugin becomes failed/visible rather than silently loading | `tests/test_workflow_profiles.py::test_plugin_manager_marks_invalid_attached_plugin_as_failed` | DONE |
| Global/current merged config | Area config overrides global config explicitly | `tests/test_workflow_profiles.py::test_plugin_manager_merges_global_and_current_scope_configs` | DONE |
| Legacy synthetic fallback | Old runtime context still produces usable plugin state | `tests/test_workflow_profiles.py::test_plugin_manager_supports_legacy_synthetic_profile_context` | DONE |
| Launcher workspace switching | Launcher saves, switches, and extends PATH slot model safely | `tests/test_launcher.py` batch | DONE |
| Diagnostics after plugin failure | `plugin_status` surfaces startup error | `tests/test_server_profiles.py::test_plugin_status_surfaces_failed_plugin_startup_error` | DONE |
| Diagnostics after migration | `workspace_info` shows migrated/default profile state | `tests/test_server_profiles.py::test_workspace_info_after_migration_reports_default_profile` | DONE |
| DB family discovery | SQLite + PostgreSQL discover through existing loader/registry contract | `tests/test_db_plugin_foundation.py::test_plugin_manager_discovers_sqlite_and_postgres_families` | DONE |
| DB scope merge | DB current scope overrides global scope | `tests/test_db_plugin_foundation.py::test_postgres_global_and_current_scope_merge_is_explicit` | DONE |
| AI provider scope merge | AI provider current scope overrides global scope | `tests/test_ai_plugin_foundation.py::test_ai_plugin_global_and_current_scope_merge_is_explicit` | DONE |
| AI secret safety | Env-backed secret is used at runtime but not leaked in diagnostics | `tests/test_ai_plugin_foundation.py::test_generate_text_uses_env_secret_without_leaking_it_to_diagnostics` | DONE |
| AI subagent gating | Subagent tool is absent in file-only mode and present only in trusted mode | `tests/test_ai_plugin_foundation.py::test_ai_plugin_registers_generate_text_but_not_subagent_in_file_only`; `tests/test_ai_plugin_foundation.py::test_ai_plugin_registers_subagent_tool_only_in_trusted_mode` | DONE |
| Server/runtime safety | Auth, temp-output chunking, process guardrails, path safety | `tests/test_server_smoke.py`; `tests/test_core.py`; `tests/test_process_limits.py` | DONE |
| Repo safety | Local repo bind/init/disable/mismatch guard and nested repo reporting | `tests/test_repo_context.py` | DONE |

## Required automated regression commands
The following commands now form the required regression gate before release-candidate discussion:

```bat
python -m py_compile launcher.py server.py profiles.py plugin_runtime.py plugins/db_shared.py plugins/ai_shared.py plugins/sqlite/plugin.py plugins/postgres/plugin.py plugins/openai_compat/plugin.py tests/test_db_plugin_foundation.py tests/test_ai_plugin_foundation.py tests/test_launcher.py tests/test_workflow_profiles.py tests/test_server_profiles.py
python -m unittest tests.test_db_plugin_foundation tests.test_ai_plugin_foundation tests.test_launcher tests.test_workflow_profiles tests.test_server_profiles -q
python -m unittest tests.test_server_smoke tests.test_core tests.test_process_limits -q
python -m unittest discover -s tests -v
```

## Manual walkthrough checklist
The following operator walkthroughs remain the minimum manual checks for high-confidence resume-safe operation:
1. Start launcher with `MENU = on` and verify selecting an existing path changes active workspace without regenerating the MCP token.
2. Save a new workspace from the startup menu and verify it occupies the next free `PATH[N]` slot.
3. Attach one plugin globally and verify the workspace remains `DEFAULT` until an area-specific plugin config is stored.
4. Attach an area-specific plugin config and verify the workspace becomes `CUSTOM`.
5. Restart MCP and verify `workspace_info`, `list_plugins`, and `plugin_status` reflect the same active profile and plugin scope.
6. Switch to another workspace profile and verify registry rebuild shows only the correct effective tools.

## Resume-safety proof
Resume safety is considered demonstrated because:
- the canonical resume source is `docs/workflow-plugin-system/HANDOFF_CURRENT.md`;
- task-specific implementation docs now exist for diagnostics, DB-family plugins, AI/subagent plugins, authoring kit, and test hardening;
- the regression gate is explicit and reproducible;
- no task now depends on hidden session memory to understand state, outputs, or next steps.

## Remaining gaps
- Manual walkthrough evidence is documented but not fully automatable in the current test harness.
- `ruff check .` remains recommended, but the required release gate currently relies on the reproducible Python test/compile batches above.
