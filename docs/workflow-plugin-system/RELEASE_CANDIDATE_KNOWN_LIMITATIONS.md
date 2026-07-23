# Release Candidate Known Limitations

## Status
Release-candidate known-limitations set for TASK-014.

## Runtime and operator limits
- Trusted developer mode is not a sandbox. Python, Node, Git hooks, npm scripts, and other allowed processes still run with the current Windows user rights.
- Plugin registry rebuild is restart-based after attach/detach. This is documented behavior, not a hidden hot-reload guarantee.
- Recursive directory delete/move is still intentionally unavailable through MCP file tools.
- Large outputs still rely on temp-file continuation through `read_file()`.

## DB plugin limits
- PostgreSQL currently executes through the `psql` CLI path rather than a native Python driver.
- PostgreSQL currently rejects non-empty `params_json`; parameterized query support is intentionally conservative for this RC.
- MySQL / MariaDB is not yet implemented, although the DB-family contract is prepared for it.

## AI/subagent limits
- The current AI provider path targets OpenAI-compatible `/chat/completions` endpoints.
- Model discovery is config-driven today rather than live provider introspection.
- Subagent execution is prompt-based and intentionally bound to the same trusted/effective-mode rules as the loader.

## Verification environment limits
- `ruff` was not available from the system interpreter in the current verification environment (`python -m ruff check .` -> module not found). The project still documents linting through the local virtual environment.
- Manual launcher/menu walkthroughs remain documented RC checks but are not fully automated by the current unit-test harness.

## Non-goals for this RC
- No commit, push, tag, or publication was performed.
- No workspace-admin or remote policy changes were made.
