# Current handoff

Phase: Milestone 6 — Release candidate hardening
Task ID: TASK-014
Task title: Release-candidate docs, gates, and final handoff
Status: DONE
Date: 2026-07-25
Target release: 1.4.5

## What was completed
- Setup wizard now exposes three operator-visible tunnel choices:
  - `Tunnellio managed runtime`
  - `Serveo temporary domain`
  - `Serveo stable domain` with reserved hostname and SSH key
- Serveo remained a first-class configuration path instead of a hidden fallback-only path.
- Background command jobs were hardened so noisy command output no longer starves transport-facing status calls.
- `VERSION` was advanced to `1.4.5`.
- Final release note draft was prepared at `release/notion-mcp-easy-1.4.5.md`.

## Verification evidence
- `python -m py_compile launcher.py server.py tests/test_launcher.py tests/test_command_jobs.py tests/test_process_limits.py tests/test_server_smoke.py` -> OK
- `python -m unittest tests.test_launcher -q` -> OK (`Ran 27 tests`)
- `python -m unittest tests.test_process_limits tests.test_server_smoke -q` -> OK (`Ran 12 tests`)
- `python -m unittest tests.test_ai_plugin_foundation tests.test_db_plugin_foundation tests.test_workflow_profiles -q` -> OK (`Ran 19 tests`)
- `python -m unittest tests.test_core -q` -> OK (`Ran 12 tests`)
- `python -m unittest tests.test_server_profiles -q` -> OK (`Ran 5 tests`)
- `python -m unittest tests.test_repo_context -q` -> OK (`Ran 14 tests`)
- `python -m unittest discover -s tests -q` -> OK (`Ran 98 tests`)
- local package rebuild -> `release/notion-mcp-easy-1.4.5.zip`

## Remaining blockers
- None at the code/docs/test gate level.

## Approval-only next steps
1. Review the final diff.
2. Decide whether to commit/push `stablefix` as 1.4.5.
3. Decide whether to tag and publish the 1.4.5 release archive.
