Phase: Milestone 7 — OAuth / perimeter convergence
Task ID: TASK-015
Task title: Embedded OAuth core import from donor branch
Status: IN PROGRESS
Date: 2026-07-25
Target release: 1.5.0

## Current objective
Import the donor project's embedded OAuth/auth-perimeter layer into this repo while preserving our existing profiles/plugin/git/runtime architecture.

## What has been documented
- master plan: `docs/workflow-plugin-system/OAUTH_PERIMETER_MERGE_PLAN.md`
- task breakdown: TASK-015 ... TASK-019 under `docs/workflow-plugin-system/tasks/`

## Immediate next steps
1. DONE: donor `auth/` package imported and `auth/oauth.py` replaced with the real embedded OAuth provider/store implementation.
2. DONE: server OAuth bootstrap wired in (`legacy` / `oauth` / `dual`, provider init, consent route, protected-resource alias, middleware, scoped tools).
3. DONE: donor OAuth tests transferred and passing (`tests.test_oauth_store`, `tests.test_oauth_flow`).
4. DONE: boot checks passed for `legacy`, `oauth`, and `dual`; targeted regressions passed for core/command-jobs/process-limits, launcher, server-smoke, server-profiles, AI/DB/workflow plugin suites.
5. NEXT: rerun the remaining repo-context regression once the local transport stops returning intermittent MCP HTTP errors, then close TASK-015 and move to launcher/operator flows.

## Resume rule
On restart, continue TASK-015 from the first incomplete checkpoint. Do not redesign the perimeter layer from scratch; transfer donor code first, adapt second.