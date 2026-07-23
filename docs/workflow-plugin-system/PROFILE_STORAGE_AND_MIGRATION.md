# Profile Storage and Migration

## Status
Covers TASK-004.

## Storage decision
Use a **mixed model**:

### Keep `connections.cfg` as the slot anchor
`connections.cfg` remains the user-facing saved area list:
- menu toggle
- `PATH[N]` slot mapping

### Add a new local profile storage file
Create a new local file alongside the existing app config under `%LOCALAPPDATA%/NotionMcpEasy/`:
- `workflow-profiles.json`

This file becomes the canonical storage for profile-aware metadata, while `connections.cfg` remains the canonical source for the saved slot list.

## Why this mixed model is safest

### Why not store everything only in `connections.cfg`
Rejected because:
- plugin state will outgrow an INI-like line format quickly
- diagnostics metadata becomes hard to evolve safely
- manual edits become fragile
- migration/repair logic becomes harder

### Why not abandon `connections.cfg`
Rejected because:
- existing users already depend on `MENU` + `PATH[N]`
- launcher/menu semantics are already built around it
- task invariants explicitly require building on top of `PATH[N]`

### Why separate JSON works
Accepted because:
- preserves old user workflow unchanged
- allows structured profile/plugin state
- keeps migration repairable and versioned
- keeps release/package exclusions aligned with existing local config behavior

## Canonical file set after migration

### Repo-root file
- `connections.cfg`

### Local app files
- `config.json`
- `runtime.json`
- `connection.txt`
- `workflow-profiles.json`  ← new

### Per-repo local file
- `agent-repo-config.local.json`

## `workflow-profiles.json` structure

```json
{
  "schemaVersion": 1,
  "activeProfileId": "workspace-1",
  "profiles": {
    "workspace-1": {
      "profileId": "workspace-1",
      "pathSlot": 1,
      "workspacePath": "D:/work/project-one",
      "accessMode": "trusted",
      "environmentMode": "DEFAULT",
      "displayName": "project-one",
      "createdAt": "2026-07-20T22:00:00",
      "updatedAt": "2026-07-20T22:00:00",
      "metadata": {
        "createdFrom": "migration",
        "lastSelectedAt": "2026-07-20T22:00:00",
        "lastKnownGood": true,
        "notes": ""
      },
      "plugins": {}
    }
  },
  "globalPlugins": {}
}
```

## Read/write responsibilities

### `connections.cfg`
Still owns:
- `MENU`
- slot numbers
- slot-to-path mapping

### `workflow-profiles.json`
Owns:
- profile IDs
- slot-to-profile mapping metadata
- per-area access mode
- per-area environment mode
- per-area plugin state
- global plugin state
- active profile ID
- profile timestamps / metadata

### `config.json`
Remains backward-compatible mirror for the active runtime session
It continues to expose old fields used by launcher/runtime, especially:
- `workspace`
- `allow_commands`
- `token`
- networking/tunnel settings

After profile activation, launcher mirrors the active profile into `config.json` so legacy code paths keep working.

## Migration strategy

### Trigger
Migration happens lazily when launcher or runtime needs profile state and `workflow-profiles.json` is missing or incomplete.

### Inputs for migration
- `connections.cfg`
- `config.json`

### Migration rules
1. Load every non-empty `PATH[N]` from `connections.cfg`.
2. Normalize the path.
3. Create one profile per saved slot.
4. Set `pathSlot` from the slot number.
5. Set `workspacePath` from the saved path.
6. Seed `accessMode` from legacy `config.json.allow_commands`:
   - `true` -> `trusted`
   - `false` -> `file_only`
7. Set `environmentMode = DEFAULT`.
8. Initialize `plugins = {}`.
9. Choose `activeProfileId` by matching legacy `config.json.workspace`.
10. If `config.json.workspace` exists but is missing from slots, bootstrap it into `connections.cfg` first, then migrate.

## Why accessMode is cloned into every migrated profile
Legacy behavior had a single global trusted/file-only flag.
The least surprising migration is to copy that legacy mode into each created profile.
That preserves historical behavior better than forcing all migrated profiles to `file_only`.

## Repair / sync rules after migration

### Slot moved manually in `connections.cfg`
On load:
1. try match profile by `pathSlot`
2. if mismatch, try match by normalized `workspacePath`
3. if matched by path, update `pathSlot`
4. if no match, create a new default profile for that slot

### Slot deleted from `connections.cfg`
If a profile no longer has a backing `PATH[N]` entry:
- keep the profile record, but mark it as detached/orphaned only if needed for diagnostics
- do not auto-delete without explicit operator action
- do not make it active automatically

### Workspace path changed manually in `connections.cfg`
If slot exists but path differs:
- treat as a new area anchored by the same slot
- previous profile can only be reused automatically if path matches exactly after normalization
- otherwise create a fresh DEFAULT profile for the new path and keep old record as orphan-candidate if necessary

## Rollback / failure behavior

### If migration fails before writing
- keep legacy files untouched
- run in legacy-synthesized runtime mode if possible
- report migration warning in diagnostics/handoff

### If migration file write fails
- do not partially overwrite existing `workflow-profiles.json`
- use atomic write
- leave `connections.cfg` and `config.json` untouched

### If new storage is invalid JSON later
- launcher/server must report invalid profile storage
- attempt read-only recovery from `connections.cfg` + `config.json`
- never silently destroy the broken file before a successful rewritten replacement is ready

## Global vs area-specific plugin storage

### Global plugin settings
Stored in:
- top-level `globalPlugins`

### Area-specific plugin settings
Stored in:
- `profiles[profileId].plugins`

This separation is mandatory for preserving `DEFAULT` semantics.

## Backward compatibility summary
The compatible path is:
- keep old `connections.cfg`
- keep old `config.json`
- add `workflow-profiles.json`
- mirror active profile back into `config.json`
- derive old runtime env from the active profile

This lets old launcher/runtime behavior survive while the new model becomes the real structured layer.
