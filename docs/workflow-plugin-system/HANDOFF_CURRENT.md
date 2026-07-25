Phase: Milestone 7 — OAuth / perimeter convergence
Task ID: TASK-016
Task title: Launcher OAuth UX and BYO client registration
Status: RELEASE CANDIDATE
Date: 2026-07-25
Target release: 1.5.0

## Current objective
Close the second OAuth milestone by exposing the embedded OAuth server through launcher/operator flows, BYO client registration, and release packaging/documentation.

## What has been documented
- master plan: `docs/workflow-plugin-system/OAUTH_PERIMETER_MERGE_PLAN.md`
- task breakdown: TASK-015 ... TASK-019 under `docs/workflow-plugin-system/tasks/`

## Immediate next steps
1. DONE: full stage-1 embedded OAuth core landed and the complete first-stage regression batch passed, including `tests.test_repo_context`.
2. DONE: launcher now supports OAuth operator flows via `--oauth` and `--register-oauth-client`.
3. DONE: wrapper scripts added for Windows and POSIX (`OAUTH_SETUP.*`, `REGISTER_OAUTH_CLIENT.*`).
4. DONE: launcher tests cover owner-code handling, masked connection display, and BYO client registration.
5. NEXT: cut the 1.5.1 release package, publish the archive and release note, and then continue with custom public URL / reverse proxy work.

## Resume rule
On restart, continue TASK-015 from the first incomplete checkpoint. Do not redesign the perimeter layer from scratch; transfer donor code first, adapt second.