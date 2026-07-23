# Plugin Manifest and Registry Model

## Status
Covers TASK-007.

## Attach/install scope model
Plugin attachment is an explicit operator action with explicit scope selection:
- `current`
- `global`

No plugin may be attached implicitly just because its folder exists.
Folder presence only makes the plugin discoverable.

## Current attach flow
1. User chooses a discoverable plugin by `plugin_id`.
2. User chooses scope:
   - `current`
   - `global`
3. Server loads the plugin entrypoint.
4. Entrypoint validates incoming config and returns normalized attachment payload.
5. Server persists the payload into:
   - `profiles[activeProfileId].plugins[plugin_id]` for `current`
   - `globalPlugins[plugin_id]` for `global`
6. Active profile storage is refreshed in memory.
7. Tool registry is rebuilt on the next MCP start.
8. Diagnostics reflect the persisted/effective state for the current process.

## Important runtime boundary
- Attach/detach is persisted immediately.
- Dynamic hot-reload of plugin tools is not implemented.
- Current operator contract is: **attach/detach now, restart MCP to rebuild the runtime tool registry**.

## Manifest schema

```json
{
  "id": "sqlite",
  "display_name": "SQLite",
  "version": "1.0.0",
  "entrypoint": "plugin.py",
  "supported_modes": ["read_only", "full_access"],
  "capabilities": ["database", "sql", "query"],
  "config_schema_version": 1,
  "required_env": [],
  "dependencies": [],
  "required": false,
  "install_scope_support": "both"
}
```

## Tool descriptor schema

```json
{
  "name": "sqlite_query",
  "title": "SQLite query",
  "description": "Run a read-only SQL query against a configured SQLite connection.",
  "capability": "database.query",
  "mode_required": "read_only",
  "input_schema": {"type": "object"},
  "output_schema": {"type": "object"},
  "handler_ref": "sqlite.query",
  "safety_tags": ["db", "read_only"],
  "diagnostic_visibility": "full"
}
```

## Registry invariants
- one plugin = one namespace root
- plugin tools must be declared, not silently injected
- effective mode must be computed before registration
- `full_access` tools never register outside trusted mode
- required plugin failure can be escalated, optional plugin failure stays isolated
- current-profile plugin config overrides global config for overlapping keys
- global attach alone must not force profile `environmentMode = CUSTOM`

## Capability groups
- `database.*`
- `api.*`
- `ai.provider.*`
- `ai.subagent.*`
- `import.*`
- `export.*`
- `workspace.*`
