# Backward Compatibility and Migration Summary

## Status
Release-candidate compatibility summary for TASK-014.

## Backward compatibility guarantees
- `PATH[N]` remains the base saved-workspace model. The initiative extends it into a profile-aware system instead of replacing it.
- The active workflow profile is mirrored back into legacy `config.json`, preserving launcher/config/runtime expectations.
- Global plugin attach does not silently convert a workspace to `CUSTOM`; area-specific plugin state remains the trigger.
- Legacy synthetic profile fallback remains available for older runtime contexts.

## Migration safety guarantees
- Legacy saved paths can be synchronized into `%LOCALAPPDATA%\\NotionMcpEasy\\workflow-profiles.json`.
- The active workspace identity, access mode, and environment state stay inspectable after migration.
- Diagnostics explicitly surface migrated/default profile state and plugin failure state.
- Plugin families were added without changing the universal registry contract.

## Evidence anchors
- `tests/test_workflow_profiles.py`
- `tests/test_server_profiles.py`
- `docs/workflow-plugin-system/MIGRATION_AND_REGRESSION_GATE.md`
- `docs/workflow-plugin-system/TEST_MATRIX_AND_RESUME_SAFETY.md`

## Residual compatibility risks
- PostgreSQL runtime currently depends on `psql` availability in trusted mode.
- Manual operator walkthroughs are still part of the RC gate for launcher/profile switching confidence.
- Registry rebuild after attach/detach still requires restart and is not yet a live dynamic reload path.
