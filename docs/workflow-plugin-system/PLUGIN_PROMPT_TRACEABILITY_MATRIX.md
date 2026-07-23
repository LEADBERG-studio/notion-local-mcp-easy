# Plugin Prompt Traceability Matrix

## Status
Traceability evidence for TASK-012.

| Prompt requirement | Real contract / code path | Verified by |
|---|---|---|
| Manifest must include core fields | `PLUGIN_REGISTRATION_CONTRACT.md`; `plugins/sqlite/plugin.json`; `plugins/postgres/plugin.json`; `plugins/openai_compat/plugin.json` | Manual contract check |
| Tool descriptors must include declarative metadata | `PLUGIN_REGISTRATION_CONTRACT.md`; plugin manifests under `plugins/` | Manual contract check |
| One plugin = one namespace root | `PLUGIN_REGISTRATION_CONTRACT.md`; tool names in sqlite/postgres/openai_compat manifests | Existing manifests |
| Runtime must expose `validate_config`, `healthcheck`, `invoke` | `plugins/sqlite/plugin.py`; `plugins/postgres/plugin.py`; `plugins/openai_compat/plugin.py` | Runtime implementation review |
| DB plugins should follow canonical tool family | `plugins/sqlite/plugin.json`; `plugins/postgres/plugin.json`; `DB_PLUGIN_FAMILY_FOUNDATION.md` | TASK-010 implementation |
| AI/subagent plugins should follow canonical tool family | `plugins/openai_compat/plugin.json`; `EXTERNAL_AI_AND_SUBAGENT_PLUGIN_MODEL.md` | TASK-011 implementation |
| Global attach and current-area attach must both be documented | `PLUGIN_AUTHORING_PACKAGE_EXAMPLES.md`; `PLUGIN_AUTHORING_CHECKLIST.md` | TASK-012 docs |
| Current-area config overrides global config | `DB_PLUGIN_FAMILY_FOUNDATION.md`; `EXTERNAL_AI_AND_SUBAGENT_PLUGIN_MODEL.md`; test suites | `tests/test_db_plugin_foundation.py`; `tests/test_ai_plugin_foundation.py` |
| Global-only attach must not silently create CUSTOM area state | `PLUGIN_REGISTRATION_CONTRACT.md`; prior workflow-profile tests | `tests/test_workflow_profiles.py` |
| full_access must be gated by trusted mode and effective mode | `PLUGIN_REGISTRATION_CONTRACT.md`; `plugins/sqlite/plugin.py`; `plugins/postgres/plugin.py`; `plugins/openai_compat/plugin.py` | Existing runtime checks + tests |
| Secrets must stay external and not leak via diagnostics | `plugins/db_shared.py`; `plugins/ai_shared.py`; `EXTERNAL_AI_AND_SUBAGENT_PLUGIN_MODEL.md` | `tests/test_ai_plugin_foundation.py` |
| Generated plugin must include tests and docs | `TASK-012-plugin-authoring-kit-and-superprompt.md`; `PLUGIN_AUTHORING_CHECKLIST.md` | TASK-012 docs |
| Prompt must avoid outdated assumptions | Updated prompt now references current runtime shape instead of generic aspirational entrypoint language | Manual prompt review |

## Outdated assumptions removed
- Replaced vague “entrypoint / executable” language with the real repo package shape: `plugin.json` + `plugin.py` runtime contract.
- Replaced abstract “registry integration” wording with explicit manifest fields and runtime functions.
- Added current real plugin families from implemented code: SQLite, PostgreSQL, OpenAI-compatible provider.
- Added explicit rule that global attach alone must not promote a workspace to CUSTOM.
- Added defensive runtime `effectiveMode` checks as a mandatory requirement, matching current implementations.

## Required generated test coverage
Every generated plugin package must include tests for:
- manifest discovery;
- config validation success/failure;
- tool registration;
- mode gating;
- global/current scope merge;
- diagnostics behavior;
- secret safety when applicable.
