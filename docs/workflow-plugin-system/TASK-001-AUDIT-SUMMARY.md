# TASK-001 Audit Summary

## Scope
Baseline audit of the current `PATH[N]` launcher/config/runtime model before any profile-aware or plugin-aware changes.

## Current persisted state

### 1. Root `connections.cfg`
`connections.cfg` is the only persisted source for saved workspace slots.

Current shape:
- `MENU = on|off`
- `PATH[1]..PATH[N] = <workspace path>`

Properties of the current model:
- `PATH[N]` stores only a filesystem path.
- Slot selection does **not** persist per-slot access mode.
- Slot selection does **not** persist per-slot environment config.
- Slot selection does **not** persist any plugin state.
- Empty slots are represented by missing or blank values.
- Slots beyond 9 are allowed and already supported by launcher/tests.

### 2. Local app config `%LOCALAPPDATA%/NotionMcpEasy/config.json`
`launcher.py` persists the active runtime configuration in `config.json`.

Current fields observed in setup/runtime flow:
- `version`
- `token`
- `workspace`
- `port`
- `allow_commands`
- `serveo_hostname`
- `ssh_key`
- `allowed_commands`

Properties of the current model:
- `workspace` is the currently active area.
- `allow_commands` is a **global current-session/server mode**, not a per-saved-workspace mode.
- Token reuse is anchored here, which is why workspace switching does not require MCP reconnection.

### 3. Runtime/session files
Additional local runtime files are created outside the repo:
- `runtime.json` — active server/tunnel PID state and URL.
- `connection.txt` — operator-facing connection summary.
- `server.log`, `tunnel.log` — launcher/runtime logs.

### 4. Git local policy file
Inside the active repo/workspace root, `server.py` may use:
- `agent-repo-config.local.json`

This is already an example of **area-local persisted policy**, but only for git safety, not for workflow profiles.

## Current lifecycle

### Setup lifecycle
1. `launcher.setup()` ensures `connections.cfg` exists.
2. On first setup, the operator chooses a workspace folder.
3. The operator chooses whether trusted developer mode is enabled.
4. Optional Serveo stable-host settings are collected.
5. `config.json` is written.
6. Chosen workspace is also saved into `connections.cfg`, preferring slot 1.

### Startup lifecycle with existing config
1. `launcher.setup(force=False)` loads `config.json`.
2. `choose_workspace_from_connections()` bootstraps current `config["workspace"]` into `connections.cfg` if missing.
3. If `MENU = off`, startup keeps the current config unchanged.
4. If `MENU = on`, launcher displays saved slots from `connections.cfg`.
5. User can:
   - keep current workspace,
   - switch to another saved slot,
   - add a new workspace and save it into a slot,
   - disable the menu.
6. Any switch updates only `config.json.workspace`.
7. Token and other runtime values are preserved.

### Runtime lifecycle
1. `launcher.start_server()` exports runtime env vars from `config.json`.
2. `server.py` reads:
   - `MCP_BASE_DIR`
   - `MCP_ALLOW_COMMANDS`
   - `MCP_ALLOWED_COMMANDS`
   - `MCP_PORT`
   - `MCP_SERVEO_HOSTNAME`
   - `MCP_TOKEN`
3. Server starts with one active workspace root and one effective safety mode.
4. Launcher opens/reopens the Serveo tunnel and publishes `connection.txt` + `runtime.json`.

## Where `PATH[N]` is currently interpreted as "only a path"

### `launcher.py`
Primary path-only logic lives in:
- `connections_cfg_template()`
- `load_connections_cfg()`
- `save_connections_cfg()`
- `first_free_connection_slot()`
- `find_connection_slot()`
- `remember_workspace_path()`
- `bootstrap_workspace_in_connections()`
- `choose_workspace_from_connections()`

Current semantics:
- slot identity == saved path identity
- path switch == overwrite only `config.json.workspace`
- no profile object exists yet

## Current access-mode model

Current access-mode behavior is global/current, not profile-aware:
- `allow_commands = false` => file-only mode
- `allow_commands = true` => trusted developer mode

This value is collected in setup and stored once in `config.json`.
It is **not** attached to a specific `PATH[N]` slot.

## Current diagnostics/status surface

### Launcher/operator-facing diagnostics
- startup menu in `launcher.py`
- `SHOW_CONNECTION.bat` / `launcher.show_connection()`
- `connection.txt`
- `runtime.json`
- tunnel/server logs

### Server/tool diagnostics
- `workspace_info()`
- `repo_context_status()`
- `inspect_git_repository()`

Current `workspace_info()` reports:
- active workspace path
- file-only vs trusted developer mode
- allowed commands
- max text file size
- repo context filename
- git repo overview (root + nested repos)

### What diagnostics do **not** currently expose
- profile identity
- slot-to-profile mapping
- environment mode (`DEFAULT/CUSTOM`)
- plugin registry state
- global plugin attach vs area-specific attach
- source of effective plugin config

## Existing compatibility constraints that must not break

### Launcher/config invariants
1. Existing `connections.cfg` files must keep working unchanged.
2. Existing `config.json` files must keep working unchanged.
3. Workspace switching must continue to preserve the Bearer token.
4. Menu disable mode (`MENU = off`) must continue to work.
5. Extended slots (`PATH[10]+`) must continue to work.
6. Broken/missing saved folders must remain recoverable from the launcher menu.

### Runtime invariants
1. `server.py` must still work with file-only mode and no plugins.
2. Trusted mode must remain globally safety-gated.
3. `full_access` behavior must never become available outside trusted mode.
4. Existing repo-context safety model must remain intact.
5. Existing file/path security guarantees from `core.py` must remain intact.

### Packaging invariants
1. Local-only files must stay out of the release archive.
2. Config/token/runtime files must remain excluded.
3. Plugin runtime/local state must follow the same release-exclusion discipline.

## Architectural gaps relative to the new initiative

### Gap 1 — no workflow profile object
There is no persisted object that expands one `PATH[N]` into:
- workspace path
- per-area access mode
- environment mode
- profile metadata
- plugin overrides

### Gap 2 — path selection does not select access mode/environment
Current behavior:
- choosing a slot selects only `workspace`
- access mode comes from global `config.json.allow_commands`
- no environment config is switched with the area

Required future behavior:
- choosing a saved area must activate a full profile derived from `PATH[N]`

### Gap 3 — no `DEFAULT/CUSTOM` state machine
Current code has no concept of:
- `DEFAULT` area state
- `CUSTOM` area state
- transitions caused by area-scoped plugin settings

### Gap 4 — no plugin registry/loader layer
Current server has:
- direct built-in tool registration in `server.py`
- no `plugins/` scanning
- no manifest contract
- no entrypoint-based attach/install flow
- no plugin diagnostics

### Gap 5 — no global vs area-specific attach model
Current storage cannot express:
- global plugin attachment
- area-local plugin attachment
- area-local saved plugin config
- effective-mode resolution across global + area scopes

## Sensitive integration points for future tasks

### Files that will be touched carefully
- `launcher.py`
- `server.py`
- `README.md`
- `build_release.py`
- `connections.cfg` template/behavior
- test suite under `tests/`

### Files likely to be added
- profile storage module/file(s)
- plugin registry/loader module(s)
- plugin manifest/runtime files
- diagnostics helpers
- migration helpers

## Baseline conclusion
Current implementation is intentionally simple:
- `connections.cfg` stores saved paths only
- `config.json` stores the active workspace and global runtime mode
- `server.py` runs against one active workspace and one effective safety mode
- diagnostics understand workspace + git, but not profiles/plugins

Therefore the main architectural move must be:
1. keep `PATH[N]` as the user-visible saved-area anchor;
2. add a profile-aware layer **around** it;
3. preserve old config behavior through migration/default synthesis;
4. make plugin state additive and diagnostics-visible without disturbing file-only startup.

## Files audited
- `docs/workflow-plugin-system/*`
- `README.md`
- `launcher.py`
- `server.py`
- `server_safe.py`
- `core.py`
- `connections.cfg`
- `build_release.py`
- `tests/test_launcher.py`
- `tests/test_server_smoke.py`
- `tests/test_core.py`
- `tests/test_process_limits.py`
- `tests/test_repo_context.py`
