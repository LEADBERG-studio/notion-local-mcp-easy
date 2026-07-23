# Migration and Regression Gate

## Status
Supporting hardening artifact for TASK-013.

## Migration safety summary
The current implementation preserves backward compatibility through these rules:
- `PATH[N]` remains the anchor format for saved workspaces.
- Legacy launcher/config/runtime behavior is preserved by mirroring the active workflow profile back into legacy `config.json`.
- Legacy path storage can be migrated into `%LOCALAPPDATA%\\NotionMcpEasy\\workflow-profiles.json` without losing active workspace identity.
- Legacy synthetic profile fallback remains available for older runtime contexts that do not yet expose a persisted profile object.

## Migration checks already covered
- slot migration from legacy path storage;
- legacy config mirroring after profile activation;
- migrated default-profile visibility in server diagnostics;
- no automatic CUSTOM promotion from global-only plugin attach.

## Required release gate
A release-candidate discussion is blocked unless all are green:
- syntax compilation batch;
- workflow/profile/server regression batch;
- smoke/core/process-limit batch;
- full `unittest discover -s tests -v` batch;
- release docs set updated;
- final task matrix updated to all `DONE`.

## Resume discipline
If work pauses at any point, the next session must read in this order:
1. `docs/workflow-plugin-system/HANDOFF_CURRENT.md`
2. `docs/workflow-plugin-system/TEST_MATRIX_AND_RESUME_SAFETY.md`
3. task file for the active task
4. task-specific implementation docs referenced by the handoff

## Blocker rule
If any regression batch fails, the initiative reverts from release-candidate consideration back to the owning task and cannot be described as complete.
