Phase: Milestone 7 — OAuth / perimeter convergence
Task ID: TASK-018
Task title: Self-hosted sish backend
Status: DONE
Date: 2026-07-26
Target release: 1.6.1

## Current objective
Close the next perimeter follow-up milestone by making a self-hosted `sish` relay a first-class tunnel backend.

## What has been documented
- master plan: `docs/workflow-plugin-system/OAUTH_PERIMETER_MERGE_PLAN.md`
- task breakdown: TASK-015 ... TASK-019 under `docs/workflow-plugin-system/tasks/`

## Immediate next steps
1. DONE: launcher now recognizes `sish` as an explicit tunnel backend and preserves it even when `public_url` is set.
2. DONE: launcher setup can collect `sish` relay host/port/user/alias plus stable `public_url`.
3. DONE: built-in SSH tunnel command generation now supports self-hosted `sish` relays.
4. DONE: operator docs were added/adapted (`docs/SISH_SETUP.md`, README, SECURITY).
5. NEXT: move to TASK-019 for perimeter convergence and the full regression gate.

## Resume rule
On restart, continue from the first non-DONE task after TASK-018. Do not redesign the perimeter layer from scratch; converge the imported perimeter pieces under TASK-019 and keep the full regression gate green.