Date: 2026-07-26

## Summary
This release closes TASK-019 and completes the TASK-015 ... TASK-019 perimeter merge line. The local product now has one publication-ready operator surface across embedded OAuth, Serveo, Tunnellio, reverse proxy, and self-hosted `sish` publishing.

## What changed
- Converged operator-facing docs across `README.md`, `SECURITY.md`, `REVERSE_PROXY.md`, and `docs/SISH_SETUP.md`.
- Finalized workflow handoff/task docs so the perimeter merge sequence is marked complete.
- Kept the `sish` backend aligned with the shipped config schema (`tunnel_host`, `tunnel_ssh_port`, `tunnel_domain`, `serveo_hostname`, `ssh_key`).
- Promoted the tree to release version `1.7.0` and rebuilt the release archive.

## Validation captured
- `python -m py_compile launcher.py tests/test_launcher.py` — OK
- `python temp/validate_task018.py` — OK
- `python temp/run_test_batch.py tests/test_core.py tests/test_command_jobs.py tests/test_process_limits.py tests/test_workflow_profiles.py tests/test_ai_plugin_foundation.py tests/test_db_plugin_foundation.py` — OK (`Ran 53 tests`)
- `python temp/run_test_batch.py tests/test_server_smoke.py` — OK (`Ran 1 test`)
- `python temp/run_test_batch.py tests/test_server_profiles.py` — OK (`Ran 5 tests`)
- `python temp/run_named_tests.py tests/test_repo_context.py ...` — OK across split batches (`Ran 14 tests`)
- `python temp/run_test_batch.py tests/test_launcher.py` — OK (`Ran 36 tests`)
- `python temp/run_test_batch.py tests/test_oauth_flow.py` — OK (`Ran 18 tests`)
- `python temp/run_test_batch.py tests/test_oauth_store.py` — OK (`Ran 63 tests`)
- Aggregate result across all files under `tests/`: **190 tests OK**

## Package output
- Release archive: `release/notion-mcp-easy-1.7.0.zip`
- Accompanying note: `release/notion-mcp-easy-1.7.0.md`

## Notes
- TASK-015, TASK-016, TASK-017, TASK-018, and TASK-019 are now complete in this line.
- Future work should start from this 1.7.0 publication-ready baseline.
