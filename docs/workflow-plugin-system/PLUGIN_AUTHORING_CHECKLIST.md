# Plugin Authoring Checklist

## Status
Primary authoring checklist for TASK-012.

## Required package shape
A generated plugin package must include:
- `plugins/<plugin_id>/plugin.json`
- `plugins/<plugin_id>/plugin.py`
- any shared helper module only when reuse is justified and documented;
- at least one test file under `tests/` covering the new plugin contract;
- operator-facing documentation under `docs/workflow-plugin-system/` or plugin-local README content.

## Manifest checklist
`plugin.json` must include all required contract fields:
- `id`
- `display_name`
- `version`
- `entrypoint`
- `supported_modes`
- `capabilities`
- `config_schema_version`
- `required_env`
- `dependencies`
- `required`
- `install_scope_support`
- `tools`

Each tool descriptor must include:
- `name`
- `title`
- `description`
- `capability`
- `mode_required`
- `input_schema`
- `output_schema`
- `handler_ref`
- `safety_tags`
- `diagnostic_visibility`

## Naming and namespace checklist
- One plugin = one namespace root.
- Every tool name must be stable and namespaced.
- Handler refs must remain inside the plugin namespace.
- Tool names must not collide with existing plugins.

## Runtime checklist
Every generated plugin must implement:
- `validate_config(config, context)`
- `healthcheck(context)`
- `invoke(tool_name, arguments, context)`

Recommended internal structure:
- normalize config first;
- compute or read effective runtime context from `context`;
- fail fast on invalid aliases, missing secrets, or unsupported mode;
- return structured JSON-safe payloads.

## Scope and profile checklist
The generated plugin must explicitly support:
- global attach via `globalPlugins[pluginId]`;
- current-area attach via `profiles[profileId].plugins[pluginId]`;
- merged config behavior when both scopes exist;
- no silent promotion of a workspace from `DEFAULT` to `CUSTOM` on global-only attach.

## Effective mode checklist
The generated plugin must respect:
- `read_only` vs `full_access` tool split;
- trusted-mode gating for any `full_access` tool;
- runtime defensive checks, not only manifest declarations;
- no bypass of profile/effective-mode boundaries.

## Diagnostics checklist
Diagnostics must expose, without leaking secrets:
- effective mode;
- enabled/disabled or loaded/failed state;
- attach scope;
- config validation status;
- dependency health;
- source of current config;
- last startup error when applicable.

## Secret handling checklist
- Never store raw secrets in tracked repo files.
- Store only secret references in plugin config.
- Read secret values from env or other external secret sources at runtime.
- Diagnostics may expose only reference names and presence/health, never secret values.

## Tests checklist
Every generated plugin must include tests for:
- manifest discovery under the existing loader;
- config validation success/failure;
- tool registration;
- read-only vs full-access mode gating;
- global vs current scope merge behavior;
- diagnostics / health output;
- secret safety behavior when applicable.

## Operator docs checklist
The generated package must document:
- purpose and capability family;
- config shape;
- global attach example;
- current-area attach example;
- required env vars or local dependencies;
- safety boundaries;
- known limitations.

## Release-readiness rule
A generated plugin is not ready unless it can be dropped into the repo and pass the required tests without hidden manual integration work.
