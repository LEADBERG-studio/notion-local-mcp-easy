# AI Plugin Authoring Superprompt

## Status
Verified superprompt for TASK-012.

## What the generated result must include
The external model must return a complete compatibility package, not just code:
- plugin folder structure;
- `plugin.json` manifest;
- `plugin.py` runtime entrypoint;
- any justified shared helper file(s);
- config schema and examples;
- diagnostics / health behavior;
- tests;
- operator-facing documentation;
- explicit global attach and current-area attach examples;
- known limitations.

## Source-of-truth alignment
This prompt is verified against:
- `PLUGIN_REGISTRATION_CONTRACT.md`
- `plugins/sqlite/plugin.json`
- `plugins/postgres/plugin.json`
- `plugins/openai_compat/plugin.json`
- `plugins/db_shared.py`
- `plugins/ai_shared.py`
- `PLUGIN_AUTHORING_CHECKLIST.md`
- `PLUGIN_AUTHORING_PACKAGE_EXAMPLES.md`

## Verified superprompt
```text
You are developing a local plugin for notion-local-mcp-easy.
Generate a plugin package that is fully compatible with the repo’s current universal plugin registry contract and current loader/runtime behavior.

You must follow these rules:

Architecture and profile model
- PATH[N] remains the saved workspace mechanism, but runtime behavior is profile-aware.
- Selecting a saved path selects workspace path, access mode, and environment configuration together.
- A workspace remains in DEFAULT until it has area-specific plugin state.
- Global-only plugin attach must not silently promote a workspace to CUSTOM.
- Plugin registry state must be rebuild-safe after profile switch.

Manifest contract
- Create plugins/<plugin_id>/plugin.json.
- Required manifest fields:
  - id
  - display_name
  - version
  - entrypoint
  - supported_modes
  - capabilities
  - config_schema_version
  - required_env
  - dependencies
  - required
  - install_scope_support
  - tools
- install_scope_support must be one of current, global, both.
- Every tool descriptor must include:
  - name
  - title
  - description
  - capability
  - mode_required
  - input_schema
  - output_schema
  - handler_ref
  - safety_tags
  - diagnostic_visibility

Namespace and naming
- One plugin = one namespace root.
- All tool names must be stable and namespaced.
- Do not create names that collide with existing plugins.
- handler_ref values must remain inside the plugin namespace.

Runtime contract
- Create plugins/<plugin_id>/plugin.py.
- The runtime file must implement:
  - validate_config(config, context)
  - healthcheck(context)
  - invoke(tool_name, arguments, context)
- validate_config must normalize and validate operator-provided config.
- healthcheck must return diagnostics-safe status without leaking secrets.
- invoke must route tools through one plugin-controlled dispatch path.
- Return JSON-safe dict/list/scalar payloads only.

Scope and config behavior
- Show both global attach and current-area attach examples.
- Global attach maps to globalPlugins[pluginId].
- Current-area attach maps to profiles[profileId].plugins[pluginId].
- If both exist, current-area config overrides global config key-by-key.
- Do not assume hidden migration or hidden install steps.

Effective mode and safety
- Tools must be explicitly split into read_only and full_access.
- full_access tools must never become usable outside trusted mode.
- Runtime must defensively check effectiveMode, not only rely on manifest declarations.
- No silent privilege escalation.
- If the plugin handles secrets, keep only references in config, and load actual secret values from env or another external secret source at runtime.
- Diagnostics may expose secret reference names and presence/health only, never secret values.

Plugin family guidance
- For DB plugins, use the canonical family when applicable:
  - *_list_connections
  - *_list_tables
  - *_describe_table
  - *_query
  - *_execute only for full_access
- For AI/subagent plugins, use the canonical family when applicable:
  - *_list_models
  - *_describe_provider
  - *_generate_text
  - *_run_subagent
- Subagent-capable plugins must carry active workspace / access-mode / effective-mode context into delegated prompting and must not bypass profile rules.

Tests and docs
- Generate at least one test file under tests/.
- The tests must cover:
  - manifest discovery under the existing loader
  - config validation success/failure
  - tool registration
  - mode gating
  - global vs current scope merge behavior
  - diagnostics / health behavior
  - secret safety behavior when applicable
- Generate operator-facing docs that explain:
  - plugin purpose
  - config shape
  - dependencies
  - safety boundaries
  - global attach example
  - current-area attach example
  - known limitations

Output format
Return these sections in order:
1. Summary
2. File tree
3. File contents
4. Config schema
5. Global attach example
6. Current-area attach example
7. Diagnostics model
8. Test matrix
9. Operator notes
10. Known limitations

Hard constraints
- Do not register tools outside the registry contract.
- Do not rely on undocumented manual repo edits.
- Do not embed raw secrets in tracked files.
- Do not claim compatibility unless manifest, runtime, tests, and docs are all provided.
- Do not describe an aspirational architecture that disagrees with the current repo contract.
```

## Acceptance check before using generated output
Reject the generated plugin package unless all are true:
- manifest contains every required contract field;
- runtime implements `validate_config`, `healthcheck`, and `invoke`;
- tool names are namespaced;
- read-only vs full-access split is explicit;
- global and current-area attach examples are present;
- diagnostics are present and secret-safe;
- tests are present;
- operator docs are present;
- no hidden manual integration steps remain.
