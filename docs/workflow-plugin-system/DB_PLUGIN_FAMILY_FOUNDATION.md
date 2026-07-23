# DB Plugin Family Foundation

## Status
Primary implementation artifact for TASK-010.

## Goal
Provide a reusable DB-family layer that supports more than one database plugin without changing the registry contract.

## Shared foundation in code
The reusable layer lives in `plugins/db_shared.py` and currently provides:
- connection-list normalization and alias validation;
- shared config parsing for `config.connections`;
- shared `params_json` parsing contract;
- workspace-scoped path resolution for file-based databases;
- sanitized diagnostics payload generation;
- shared PostgreSQL CLI helpers and TSV result parsing.

## Confirmed DB families
### 1. SQLite
Files:
- `plugins/sqlite/plugin.json`
- `plugins/sqlite/plugin.py`

Family proof:
- uses the shared connection-normalization helpers;
- keeps the canonical DB tool set;
- preserves workspace-scoped file safety;
- retains `read_only` vs `full_access` split.

### 2. PostgreSQL
Files:
- `plugins/postgres/plugin.json`
- `plugins/postgres/plugin.py`

Family proof:
- plugs into the same manifest/registry contract without changing loader code;
- uses the shared DB helpers for config normalization, diagnostics, and command parsing;
- exposes the same canonical DB tool family;
- supports explicit global/current attachment through the existing profile storage model.

## Canonical DB tool family
Every DB plugin family should implement:
- `*_list_connections`
- `*_list_tables`
- `*_describe_table`
- `*_query`
- `*_execute` only when `effectiveMode = full_access`

## Connector order
Implementation order is now explicitly:
1. SQLite
2. PostgreSQL
3. MySQL / MariaDB

SQLite and PostgreSQL are the two confirmed family-level proofs in code.
MySQL/MariaDB remains the next connector in the queue and should reuse the same family contract.

## Global vs area-specific attach behavior
- Global attach stores DB plugin config in `globalPlugins[pluginId]`.
- Current-area attach stores DB plugin config in `profiles[profileId].plugins[pluginId]`.
- Current-area config overrides global config key-by-key when both exist.
- Global-only DB attach does not promote the area from `DEFAULT` to `CUSTOM`.
- Area-specific DB config promotes the area to `CUSTOM`.

## Family-level safety rules
- Loader gating still applies before any DB tool registration.
- `full_access` DB tools are never registered outside trusted mode.
- File-based DB paths stay scoped to the active workspace.
- PostgreSQL secret handling is env-reference based (`password_env`) and diagnostics expose only the env variable name/presence, never the secret value.

## Verification evidence
- `tests/test_db_plugin_foundation.py`
- `tests/test_workflow_profiles.py`

Covered proofs:
- second DB family is discoverable through the existing registry contract;
- PostgreSQL tools register without changing loader code;
- global + current DB scope merge remains explicit;
- shared family helpers work for both file-based and server-based DB plugins.

## Remaining DB-specific follow-up
- MySQL / MariaDB implementation is still future work.
- PostgreSQL currently uses the `psql` CLI path for runtime execution; a native driver path can be added later without changing the registry contract.
- Parameterized query support for PostgreSQL CLI is intentionally conservative for now and rejects non-empty `params_json`.
