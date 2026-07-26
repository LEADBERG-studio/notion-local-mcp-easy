# TASK-019 — Perimeter convergence and full regression gate

Status: DONE
Target release: 1.7.0

## Objective
Unify imported perimeter/auth code with the current local runtime platform into one publication-ready line.

## Scope
- config cleanup
- doc convergence
- consolidated connection/help text
- full regression matrix across auth and tunnel modes
- release packaging alignment

## Checkpoints
- CP1: config schema stabilized (DONE)
- CP2: docs aligned across README/SECURITY/setup guides (DONE)
- CP3: targeted auth/tunnel/profile/plugin/regression suites pass (DONE)
- CP4: full test discovery gate passes (DONE via complete split-batch coverage across all files under `tests/`)
- CP5: release package rebuilt (DONE)