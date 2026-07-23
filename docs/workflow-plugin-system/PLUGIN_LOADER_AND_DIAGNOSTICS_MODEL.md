# Plugin Loader, Effective Mode, and Diagnostics Model

## Status
Covers TASK-008 and TASK-009.

## Loader lifecycle
1. Discover plugin folders under `plugins/`.
2. Read `plugin.json`.
3. Validate manifest shape and declarative tool descriptors.
4. Resolve active profile context.
5. If launcher/profile env is unavailable, build a legacy synthetic profile context from the current workspace and safety mode.
6. Determine whether the plugin is attached:
   - not attached
   - attached globally
   - attached to current profile
   - attached in both scopes
7. Compute effective config source order.
8. Compute effective mode.
9. Load runtime from plugin entrypoint.
10. Validate plugin config.
11. Run healthcheck.
12. Register only allowed tools.
13. Publish diagnostics.

## Effective config order
1. global plugin config
2. current-profile plugin config

Current-profile config overrides global config key-by-key when both scopes are attached.
Manifest defaults are currently owned by the plugin entrypoint/runtime layer, not merged by the registry layer.

## Effective mode formula
1. server safety ceiling
2. active profile `accessMode`
3. plugin attachment requested mode
4. plugin supported modes
5. final effective mode

### Mapping
- profile `file_only` => plugin max mode is `read_only`
- profile `trusted` => plugin may reach `full_access` only if requested and supported
- requested `full_access` under file-only mode is downgraded to `read_only`
- `full_access` tools are skipped unless the effective mode remains `full_access`
- unsupported requested mode yields `disabled` instead of silently escalating

## Required vs optional plugin policy
- `required: false` plugin failures stay in diagnostics as `failed` and do not crash server startup.
- `required: true` plugin failures are treated as startup-blocking and are re-raised.
- Invalid manifests are surfaced as failed plugin states with explicit manifest errors.

## Registry states
- `not_attached`
- `discoverable`
- `loaded`
- `disabled`
- `failed`
- `warning`

## Rebuild rule
- Profile switch must rebuild plugin registry state.
- Current implementation achieves this by starting the MCP server with the selected profile env (`MCP_PROFILE_STORAGE`, `MCP_PROFILE_ID`) so each start rebuilds the registry for the active profile.
- Attach/detach persists immediately, but live tool registration is refreshed on next MCP start rather than hot-reloaded in-process.

## Current operator-facing surfaces
### `workspace_info`
Shows:
- active profile id
- active path slot
- active access mode
- active environment mode
- profile storage path
- plugin discovery count
- plugin loaded count

### `list_plugins`
Shows per plugin:
- plugin id / display name
- status
- attach scope
- effective mode

### `plugin_status`
Shows:
- active profile id
- active path slot
- active workspace path
- active access mode
- active environment mode
- profile storage path
- last startup error summary
- per-plugin status
- per-plugin scope
- per-plugin requested mode
- per-plugin effective mode
- per-plugin config source
- per-plugin manifest path
- per-plugin entrypoint path
- per-plugin health payload
- per-plugin load error, when present

## Backward compatibility behavior
- Without stored workflow profile env, loader falls back to a legacy synthetic profile derived from the current workspace and server safety mode.
- This preserves base no-plugin behavior for old launcher/config/runtime flows.
- Plugin attach/detach still requires stored workflow profiles and therefore stays unavailable in legacy synthetic mode.

## Failure paths covered in code/tests
- invalid manifest JSON or schema
- missing plugin entrypoint
- invalid attached plugin config
- requested full access under file-only profile/runtime ceiling
- stored current-scope vs global-scope config merge

## Open observability gaps
- aggregated health/severity rollup across multiple plugins
- explicit startup-history retention beyond the latest process state
- richer operator guidance for restart-required changes after attach/detach
