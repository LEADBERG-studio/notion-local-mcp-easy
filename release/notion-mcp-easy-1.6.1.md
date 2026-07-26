Date: 2026-07-26

## Summary
This release closes TASK-018 from the OAuth/perimeter merge plan by turning self-hosted `sish` publishing into a first-class launcher backend. Operators can now keep tunnel lifecycle inside the product while using their own SSH relay and stable wildcard domain.

## What changed
- Added `sish` as an explicit launcher tunnel backend alongside Tunnellio, Serveo, and custom reverse-proxy mode.
- Extended `SETUP.bat` / launcher setup flow to collect `tunnel_host`, `tunnel_ssh_port`, `tunnel_domain`, reserved subdomain label, and SSH key.
- Derived the stable public MCP URL for `sish` as `https://<serveo_hostname>.<tunnel_domain>` and used it for connection/runtime output.
- Added self-hosted relay command generation and early-startup URL resolution for the `sish` backend.
- Added operator documentation in `docs/SISH_SETUP.md` and refreshed README / SECURITY / workflow handoff docs.

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

## Package output
- Release archive: `release/notion-mcp-easy-1.6.1.zip`
- Accompanying note: `release/notion-mcp-easy-1.6.1.md`

## Notes
- This release completes TASK-018 from the perimeter merge plan.
- The next milestone is TASK-019: perimeter convergence and the full publication gate.
