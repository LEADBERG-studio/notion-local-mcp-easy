# Workflow Profile Model

## Status
Covers TASK-002.

## Core decision
`PATH[N]` remains the user-visible saved-area anchor in `connections.cfg`.
A workflow profile is a profile-aware object that is **derived from and linked to** one saved `PATH[N]` entry.

The system does not replace `PATH[N]` with a new primary concept.
Instead, it expands each saved path into a richer profile object.

## Canonical interpretation
Selecting a saved area means selecting all of the following together:
1. workspace path
2. access mode
3. environment/profile state
4. plugin overlays that apply to that area

## Persistent vs runtime state

### Persistent state
Stored on disk and recovered after restart:
- profile identity
- `pathSlot`
- `workspacePath`
- `accessMode`
- `environmentMode`
- metadata
- area-specific plugin state
- global plugin state (outside the profile, but in the same storage domain)
- active profile reference

### Runtime-only state
Recomputed on startup or switch:
- effective plugin mode after safety gating
- loaded / disabled / failed registry state
- resolved plugin entrypoint health
- warnings based on missing folders, invalid configs, or failed validation
- process/tunnel/runtime PID state

## Canonical profile fields

```json
{
  "profileId": "workspace-1",
  "pathSlot": 1,
  "workspacePath": "D:/work/project-one",
  "accessMode": "trusted",
  "environmentMode": "DEFAULT",
  "displayName": "project-one",
  "createdAt": "2026-07-20T22:00:00",
  "updatedAt": "2026-07-20T22:00:00",
  "metadata": {
    "createdFrom": "migration|setup|menu_add|manual_sync",
    "lastSelectedAt": "2026-07-20T22:00:00",
    "lastKnownGood": true,
    "notes": ""
  },
  "plugins": {
    "sqlite": {
      "scope": "current",
      "requestedMode": "read_only",
      "config": {
        "connections": [
          { "name": "main", "path": "data/app.db" }
        ]
      },
      "attachedAt": "2026-07-20T22:00:00"
    }
  }
}
```

## Field definitions

### `profileId`
Stable internal identifier for the profile.
It is not the replacement for `PATH[N]`; it exists so profile data can survive:
- slot renumbering,
- manual `connections.cfg` edits,
- migration repair.

### `pathSlot`
Integer slot currently associated with this profile in `connections.cfg`.
This keeps the direct relationship to `PATH[N]` explicit.

### `workspacePath`
Resolved filesystem path for the area.
This must remain consistent with the matching `PATH[N]` entry.

### `accessMode`
Area-level runtime mode selected with the workspace.
Allowed values:
- `file_only`
- `trusted`

This field is the profile-aware replacement for the old global-only interpretation of `config.json.allow_commands`.
Launcher still mirrors the active value into `config.json.allow_commands` for backward compatibility.

### `environmentMode`
Allowed values:
- `DEFAULT`
- `CUSTOM`

This is persisted for visibility but always validated/repaired from area-specific plugin state.
If persisted value and computed value differ, the computed value wins and the storage is normalized.

### `displayName`
Optional operator-facing label.
Default is derived from the last path segment.

### `metadata`
Non-functional operator metadata.
Minimal fields:
- `createdFrom`
- `lastSelectedAt`
- `lastKnownGood`
- optional notes / warning hints

### `plugins`
Area-specific plugin records for the profile.
If a plugin is attached globally only, it does **not** appear here.
That distinction is what allows a profile to remain `DEFAULT` while still inheriting global plugins.

## Global plugin state
Global plugins are intentionally outside individual profiles:

```json
{
  "globalPlugins": {
    "sqlite": {
      "scope": "global",
      "requestedMode": "read_only",
      "config": {
        "connections": [
          { "name": "shared", "path": "shared.db" }
        ]
      },
      "attachedAt": "2026-07-20T22:00:00"
    }
  }
}
```

This ensures:
- global attach does not silently create area-specific state;
- global attach alone does not force `CUSTOM`;
- diagnostics can show config source unambiguously.

## Profile resolution rules

When the launcher or server needs an active profile:
1. load saved slots from `connections.cfg`
2. load profile storage
3. match by `pathSlot` first
4. if slot changed manually, match by normalized `workspacePath`
5. create a repaired/default profile if no match exists
6. normalize `environmentMode`
7. mark the selected profile active

## Required fields by mode

### Required for every profile
- `profileId`
- `pathSlot`
- `workspacePath`
- `accessMode`
- `environmentMode`
- timestamps / minimal metadata

### Additional meaning for `DEFAULT`
- `plugins` must be empty or absent
- or only global plugins exist outside the profile

### Additional meaning for `CUSTOM`
- `plugins` contains at least one area-specific plugin record
- or another future area-specific override exists

## Backward compatibility rule
When no profile storage exists, the system synthesizes profiles from legacy state:
- `connections.cfg` provides slot/path anchors
- `config.json.allow_commands` seeds migrated `accessMode`
- `environmentMode` starts as `DEFAULT`
- `plugins` starts empty

This preserves existing behavior while introducing the new model.
