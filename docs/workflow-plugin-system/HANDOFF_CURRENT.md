Phase: Milestone 7 — OAuth / perimeter convergence
Task ID: TASK-019
Task title: Perimeter convergence and full regression gate
Status: DONE
Date: 2026-07-26
Target release: 1.7.0

## Current objective
Close the perimeter merge line by aligning config/docs/release packaging and by proving the full regression matrix on the imported auth+tunnel platform.

## What has been documented
- master plan: `docs/workflow-plugin-system/OAUTH_PERIMETER_MERGE_PLAN.md`
- task breakdown: TASK-015 ... TASK-019 under `docs/workflow-plugin-system/tasks/`

## Immediate next steps
1. DONE: config/docs were converged around the shipped tunnel/auth model, including reverse proxy and self-hosted `sish` guidance.
2. DONE: release/version files were aligned to the publication-ready 1.7.0 line.
3. DONE: targeted auth/tunnel/profile/plugin/regression suites passed across all test files under `tests/`.
4. DONE: the release package was rebuilt from the aligned tree.
5. NEXT: no further tasks remain in the TASK-015 ... TASK-019 perimeter merge sequence.

## Resume rule
The TASK-015 ... TASK-019 perimeter merge sequence is complete. On restart, treat this line as the publication-ready baseline and start any future work from the current released tree rather than replaying the donor import sequence.