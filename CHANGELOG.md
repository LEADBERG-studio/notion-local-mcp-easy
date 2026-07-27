## 1.7.6 - 2026-07-27

- Fixed plugin BAT wrappers so `--plugin-dir` is passed as a safe normalized folder path instead of a raw trailing-backslash `%~dp0` value.
- Improved IDE Provider setup with a simple `default` / `custom` endpoint-defaults choice.
- IDE Provider setup now generates and saves a local `idep_...` API key in the plugin-local config during installation, and endpoint start reuses that key by default.
- Added regression coverage for safe BAT wrapper arguments, IDE Provider setup API key generation, and default API key validation.

## 1.7.5 - 2026-07-27

- Added plugin-local setup UX: every bundled plugin now ships `SETUP.bat`, `ENABLE.bat`, `DISABLE.bat`, and `STATUS.bat`.
- Added `plugin_setup.py`, an interactive helper that asks for `current` vs `global` scope, collects plugin-specific configuration, and writes local `plugin.local.*.json` files.
- Updated plugin runtime to load plugin-local configs on startup, so users no longer need to edit workflow profile storage manually.
- Added tests for plugin-local current/global config loading, disable cleanup, and packaged BAT wrappers.
- Updated beginner plugin/IDE Provider docs for the BAT-based workflow and excluded `plugin.local.*.json` from Git/release archives.

## 1.7.4 - 2026-07-27

### IDE Provider

- Added the `ide_provider` active-bridge plugin: a local OpenAI-compatible endpoint that lets IDEs send chat-completion requests to the active MCP model.
- Implemented the request lifecycle through `ide_provider_wait_request`, `ide_provider_send_response`, and `ide_provider_fail_request`, backed by a file-based queue under ignored runtime storage.
- Added local `idep_` token auth, redacted logs, loopback-only binding, duplicate-bind prevention, idempotent already-running start behavior, worker cleanup, and POSIX reap handling.
- Added manifest/config/security/queue/worker regression coverage plus a hardened active-bridge smoke script.
- Updated project README files, Russian beginner docs, and release notes for the IDE Provider workflow.

## 1.7.3 - 2026-07-26

### Documentation

- Expanded and corrected the Russian beginner documentation for Tunnellio.
- Added a standard-layout `docs/ru/tunnellio.html` page with the same header, shell, left navigation, and footer style as the rest of the local documentation.
- Added a collapsible Tunnellio submenu in the left navigation.
- Reworked the content as public client-facing documentation: cabinet URL, public tunnel URL pattern, API documentation link, Local MCP Easy connection steps, Tunnellio CLI usage, OAuth guidance, API token guidance, and troubleshooting.
- Removed internal implementation/repository/deployment details from public Tunnellio documentation.
- Clarified the production service addresses: `https://console.tunnellio.ru`, `https://api.tunnellio.ru/docs`, and `https://*.tunnellio.site`.

## 1.7.2 - 2026-07-26

- Changed launcher connection output from `Bearer token: <token>` to `Authorization=Bearer <token> (Bearer token)` so users see the exact copy-ready authorization form.
- Published the post-1.7.1 cloud/release state with GitHub Actions CI and the Russian beginner documentation site included.
- Bumped canonical version metadata to `1.7.2` and rebuilt the release package after compile, targeted, full unittest, diff hygiene, and source-marker checks.

## 1.7.1 - 2026-07-26

- Hardened workspace write/copy/move/delete paths, git command policy, child-process environment handling, temporary-file cleanup, newline-preserving edits, and bounded process capture.

- Added regression coverage for command allowlists, async filesystem tools, trust-anchor protection, copy/move limits, CRLF preservation, environment sanitization, git hardening, logging hygiene, process capture, temp cleanup, tunnel backend helpers, dependency expectations, and version consistency.

- Added dependency update configuration, issue/PR templates, community files, English quick-start documentation, POSIX helper scripts, and self-hosted sish setup guidance.

- Rebuilt the 1.7.1 release package after compile, targeted regression, full unittest, diff hygiene, and source-marker checks.

## 1.7.0 — 2026-07-26

- Finalized the perimeter merge line by converging configuration, operator docs, and release packaging across embedded OAuth, Tunnellio, Serveo, reverse-proxy, and self-hosted `sish` paths.

- Completed TASK-019: the workflow handoff and task docs now mark the TASK-015 ... TASK-019 sequence done and treat the current tree as the publication-ready baseline.

- Revalidated the full regression surface across launcher, oauth, repo-context, server-smoke, server-profiles, core/process/plugin/workflow suites, covering every test file under `tests/`.

- Rebuilt the aligned 1.7.0 release package and accompanying publication note.

## 1.6.1 — 2026-07-26

- Added a first-class self-hosted `sish` tunnel backend to the launcher, including setup flow, stable public URL derivation, SSH command generation, runtime connection metadata, and reconnect-safe URL resolution.

- Imported and adapted operator documentation for self-hosted relay publishing: `docs/SISH_SETUP.md`, refreshed `README.md`, and expanded `SECURITY.md` guidance for relay ownership and SSH-key handling.

- Marked TASK-018 done in the workflow handoff/task docs and advanced the perimeter plan toward TASK-019.

- Re-ran the launcher/auth/repo-context regression batches and kept the targeted TASK-018 validation green before cutting the release package.

## 1.5.1 — 2026-07-26

- Imported the donor embedded OAuth core into the local product: `auth/` package, embedded provider/store, discovery/protected-resource metadata, consent flow, and `legacy` / `oauth` / `dual` server modes.

- Added operator-facing OAuth launcher flows: `--oauth`, `--register-oauth-client`, owner-code generation/storage/display, and wrapper scripts `OAUTH_SETUP.bat`, `REGISTER_OAUTH_CLIENT.bat`, `oauth_setup.sh`, and `register_oauth_client.sh`.

- Extended launcher connection output so OAuth/dual runs expose discovery metadata and keep both Bearer token and OAuth owner code masked by default in `SHOW_CONNECTION`.

- Hardened transport-sensitive command execution by steering long risky invocations away from synchronous `run_command` and toward background command jobs.

- Added and passed regression coverage for embedded OAuth, launcher OAuth flows, command-job guidance, repo-context gate, and the stage-1 / stage-2 release path.

# Changelog







## 1.4.6 — 2026-07-25

- Updated operator documentation for the production OAuth path through Tunnellio after the upstream server/client rollout.

- Added explicit guidance for `auth_mode = oauth` / `dual`, stable Tunnellio domains, redirect URI registration, scopes, and public-vs-confidential client setup.

- Refreshed the local release bundle to include the updated root-level `tunnellio.exe` together with the existing Python launcher/runtime changes from 1.4.5.

- Added a dedicated OAuth setup guide for Tunnellio-backed MCP publishing and updated the release notes for the bumped package version.

## 1.4.5 — 2026-07-25







- Launcher now detects a working root-level `tunnellio.exe`, recommends Tunnellio managed runtime in setup, and still lets the operator explicitly choose Tunnellio, Serveo temporary domain, or Serveo stable domain.



- Public tunnel recovery for the Tunnellio path now resolves the authoritative public URL from `show-config --name <runtime>` / runtime snapshot data instead of parsing SSH output.



- Runtime metadata now persists the selected tunnel backend, Tunnellio runtime name, executable path, and state directory so stop/reconnect logic can address the correct client instance.



- Background command jobs now capture noisy stdout/stderr through blocking subprocesses plus reader threads so transport-facing status calls stay responsive under heavier load.



- Added regression coverage for background-job responsiveness and completed the full `python -m unittest discover -s tests -q` gate for the 1.4.5 release candidate.



- Added launcher regression coverage for Tunnellio command generation and runtime-snapshot URL resolution while keeping the existing Serveo regressions green.







## 1.4.3 — 2026-07-24







- Added background command jobs for trusted developer mode via `start_command()`, `get_command_status()`, `cancel_command()`, and `list_commands()`.



- Long-running and verbose commands now use retained per-job capture files so transport responses stay bounded without killing successful jobs just because output is large.



- Final background-job results now preserve job status context for cancelled, timed-out, and failed runs instead of collapsing everything into a plain exit-code-only response.



- Added focused regression coverage for command jobs and refreshed process-limit tests so mixed command-enabled and command-disabled suites reload server state cleanly.



- Verified the transport-focused test set for command jobs, process limits, and server smoke coverage before cutting the release.







## 1.4.2 — 2026-07-20







- Added a root-level `connections.cfg` file with documented Russian comments, `MENU = on` by default, and pre-created `PATH[1]`–`PATH[9]` workspace slots.



- Added a startup workspace-selection menu that can switch projects by updating only `workspace` in `config.json`, without regenerating the MCP token or forcing the agent to reconnect.



- First-time setup now saves the chosen workspace both to `config.json` and to the first available slot in `connections.cfg`.



- Added support for saving new workspaces from the startup menu, reusing existing slots, extending beyond slot 9 when needed, and disabling the menu while keeping the last selected workspace as the default.



- Updated launcher messaging and README documentation so users can see where `connections.cfg` and `config.json` live and edit them manually.



- Added launcher regression tests for config bootstrap, workspace switching, saving new paths, extended slot numbers, and menu disable mode.



- Added profile-aware workflow storage in `%LOCALAPPDATA%\\NotionMcpEasy\\workflow-profiles.json` while preserving legacy `config.json` mirroring for backward compatibility.



- Added a universal plugin registry contract with profile-aware effective-mode gating, diagnostics, and global-vs-current attach behavior.



- Added a reusable DB plugin family foundation, refactored внутреннее хранилище onto it, and added PostgreSQL as the second confirmed DB-family implementation.



- Added an OpenAI-compatible AI/subagent plugin family with env-backed secret refs and trusted-only subagent execution.



- Added verified plugin-authoring docs: authoring superprompt, compatibility checklist, package examples, and prompt-to-code traceability matrix.



- Added hardening docs for regression gates, migration safety, resume safety, known limitations, and final task-status tracking.



- Updated launcher messaging, README documentation, release packaging, and regression coverage for the workflow-profile/plugin-system initiative.







## 1.4.1 — 2026-07-19







- Tightened git policy so ordinary mutating git commands such as `reset`, `checkout -B`, `tag`, `config`, and `remote set-url` no longer bypass the repo guard-layer.



- Added an explicit consent-layer for git setup changes: defaults now require confirmation, and changing an existing repo binding requires `confirm_reconfigure`.



- `workspace_info()` now shows a compact root-repo plus nested-repo overview instead of only a single-layer summary.



- Fixed repo-context handling for nested repositories and extended the repo-context regression coverage.



- Added tests for consent-layer flows, nested repo summaries, mutating git command blocking, large-file / long-line safety, and `git -C` target validation.







## 1.4.0 — 2026-07-18







- Added a full git setup-flow for MCP workspaces instead of a guard-only model.



- Added `setup_git_context()` with explicit modes for `bind_existing_repo`, `init_new_repo`, `attach_to_remote`, and `disable_git`.



- Added `inspect_git_repository()` and expanded `repo_context_status()` / `workspace_info()` so agents can see the current state and the next safe action after restart.



- Git commands are now blocked until the user-facing setup choice is completed for the folder, including the explicit “disable git for now” path when `.git` is absent.



- Repo context now stores persisted local policy in `agent-repo-config.local.json`, including configured/disabled state, last detected origin, branch, fork metadata, and explicit commit branch policy.



- MCP now refuses git whenever the detected `remote.origin.url` no longer matches the saved local binding after restart, and blocks commit/push/merge/rebase outside the configured branch target.



- Added tests for repo bootstrap, disable mode, origin mismatch, and URL normalization.



- Release archives are now built into a local `release/` folder inside the project; that folder is excluded from Git and from the archive contents themselves.







## 1.3.5 — 2026-07-18







- Added mandatory local repo context file `agent-repo-config.local.json` for Git work in each workspace.



- Added `configure_repo_context()` so the client must explicitly store `repository_url` and `is_fork` before Git is allowed through MCP.



- Added `repo_context_status()` and extended `workspace_info()` so the saved repo binding is visible after MCP restarts.



- `run_command()` now blocks `git` when repo context is missing, invalid, or mismatched against the detected `remote.origin.url`.



- Added local packaging / ignore rules so repo-context files stay out of Git and release archives.







## 1.3.3 — 2026-07-18







- `server.py` now delivers large outputs in safe chunks instead of sending oversized responses directly to the model.



- `read_file()` now returns chunked output with line ranges, total line count and continuation offsets.



- Chunking now prioritizes a safe character budget while preserving whole lines.



- Added adaptive chunk reflow: if wrapper text makes a chunk exceed the hard output limit, the chunk is rebuilt with a smaller working character budget.



- `run_command()`, `grep_files()` and `list_dir()` now return small results directly and spill large results to temp files for continued reading via `read_file()`.



- Added temp output storage in `temp/` next to `server.py`, outside `BASE_DIR`.



- Added `@temp/...` virtual paths so long temporary outputs can be continued through `read_file()`.



- Temp output files are deleted automatically after the final read.



- Leftover temp output files are cleaned up on server startup.



- `edit_file()` now refuses to edit files that look binary.



- Empty outputs are now normalized to safe non-empty responses.







## 1.3.2 — 2026-07-17







- Atomic writes now use a unique temp file per call (`tempfile.mkstemp`): parallel `write_file`/`edit_file` calls on the same file no longer race on a shared temp name; temp files are cleaned up on failure.



- Tests no longer inherit `MCP_SERVEO_HOSTNAME` from an active MCP session — the suite is reproducible regardless of where it runs.



- Public health polling stops early if the SSH process dies instead of polling to timeout.



- Reconnect no longer claims the stable tunnel is restored when its health check has not passed yet.







## 1.3.1 — 2026-07-17







- Fixed: removed `BatchMode=yes` from the SSH command. Serveo completes auth via keyboard-interactive with an empty challenge even for registered keys (the key only authorizes the reserved hostname), so BatchMode broke both temporary and stable tunnels with `Permission denied`. Verified live.



- Dead server/tunnel processes are no longer "stopped" on shutdown, removing a false "Refusing to stop PID" warning after PID reuse.







## 1.3.0 — 2026-07-17







- SSH tunnel briefly used `BatchMode=yes` — reverted in 1.3.1, see above.



- Startup now polls the public `https://.../health` endpoint and reports success only after the tunnel actually serves traffic.



- Tunnel errors now print the last lines of `tunnel.log` directly in the console.



- `write_file` and `edit_file` write atomically (temp file + replace) to survive crashes and OneDrive sync races.



- Host header check restored in `SecurityMiddleware`: only `localhost`, `127.0.0.1` and `*.serveousercontent.com` are accepted (in stable mode — only the reserved hostname); other hosts get HTTP 403.



- `SHOW_CONNECTION.bat` masks the Bearer token by default; pass `--full` to reveal it.



- Added tests: symlink escape from workspace, Cyrillic paths, atomic write, Host check, `BatchMode`, public health-check, token masking.







## 1.2.2 — 2026-07-17







- Fixed reserved-hostname startup when Serveo keeps SSH output silent.



- Stable mode now derives its known public URL instead of waiting for an announcement.



- Added regression tests for silent and failed stable SSH processes.







## 1.2.1 — 2026-07-17







- Added a standalone full Serveo setup guide for temporary and stable URLs.



- Added step-by-step SSH key, reserved hostname, Notion connection and troubleshooting instructions.



- Added a ready-to-use prompt for AI-assisted installation.







## 1.2.0 — 2026-07-17







- Added optional stable Serveo mode with a reserved hostname and dedicated SSH key.



- Setup wizard now supports both zero-config temporary URLs and persistent URLs.



- Stable reconnects reuse the same URL and no longer require editing Notion.



- Added live verification and unit tests for the reserved-hostname SSH command.







## 1.1.0 — 2026-07-17







- File-only mode is now the default for new installations.



- Renamed command access to trusted developer mode and documented that it is not a sandbox.



- Added bounded streaming capture for command output; the process tree is stopped at the limit.



- Added authenticated `/health` checks and wrong-token tests.



- Launcher now rejects an occupied port before creating a tunnel.



- Runtime process IDs are checked against expected command lines before stopping them.



- Append operations now enforce the final file-size limit.



- Directory moves are no longer supported by `move_file`.



- Regex search mode is disabled to avoid pathological expressions.



- Reconnect output now explicitly tells the user to update the URL in Notion.



- Added launcher, process-limit, authentication and occupied-port tests.







## 1.0.0 — 2026-07-16







- Initial one-click Windows fork with FastMCP, Serveo launcher, workspace file tools and optional developer commands.



