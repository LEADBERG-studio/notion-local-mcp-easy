# DEFAULT / CUSTOM State Machine

## Status
Covers TASK-003.

## Core rule
An area is `DEFAULT` until both conditions become true:
1. at least one plugin is attached specifically to that area
2. area-specific saved state exists for that plugin in the profile

A global plugin attach alone does **not** move an area to `CUSTOM`.

## Meanings

### `DEFAULT`
The area already has:
- a selected workspace path
- a selected access mode
- a resolved profile object

But it does **not** yet have area-specific plugin state.

Allowed sources while remaining `DEFAULT`:
- built-in server tools
- global plugin attachments
- legacy migrated profile with no area-specific plugins

### `CUSTOM`
The area has at least one persisted area-specific override, currently defined as:
- an area-scoped plugin attachment record in `profile.plugins`
- or another future area-scoped override

## Transition table

| Event | Before | After | Notes |
|---|---|---|---|
| Create profile from setup | none | DEFAULT | New area always starts default |
| Migrate legacy slot | none | DEFAULT | No area-specific plugin state exists yet |
| Attach plugin globally | DEFAULT | DEFAULT | Global state stays outside profile |
| Attach plugin to current area | DEFAULT | CUSTOM | Area-specific plugin record is created |
| Save area plugin config | DEFAULT | CUSTOM | Even if plugin was previously global-only |
| Remove last area-specific plugin record | CUSTOM | DEFAULT | Only if no other area override remains |
| Switch to another untouched profile | CUSTOM/DEFAULT | DEFAULT | Depends on target profile state |
| Broken area plugin config on load | CUSTOM | CUSTOM with warning or repaired DEFAULT | Depends on recovery policy |

## Recovery policy

### Invalid/missing plugin payload in current area
If a profile says `CUSTOM` but the area-specific plugin payload is invalid:
1. diagnostics must report the plugin as failed/invalid
2. system must not silently escalate privileges
3. built-in tools must still load
4. if the invalid payload cannot be repaired, the profile stays readable but with warning state
5. `environmentMode` is recomputed from surviving valid area-specific records

### Broken plugin entrypoint for area-scoped plugin
If entrypoint execution/import fails:
- profile remains `CUSTOM`
- plugin registry marks plugin `failed`
- diagnostics show the last startup error
- launcher/runtime do not drop the saved attachment silently

### Missing plugin folder
If a profile references a plugin that no longer exists:
- profile remains readable
- plugin registry marks it `missing`
- diagnostics show missing manifest/entrypoint
- operator can detach or repair later

## No hidden inheritance rule
Area-specific state must never be inferred from another profile.

This means:
- Profile A becoming `CUSTOM` does not affect Profile B.
- Global plugin state is inherited explicitly as global, not re-persisted into each area.
- `CUSTOM` must come from persisted state for that same area.

## Detach semantics

### Detach global plugin
- removes entry from `globalPlugins`
- does not affect `environmentMode` of an area unless that area depended only on global state
- areas that had their own local plugin records remain `CUSTOM`

### Detach current-area plugin
- removes entry from `profile.plugins`
- recompute `environmentMode`
- if no area-specific entries remain, switch to `DEFAULT`

## Effective mode interaction
`CUSTOM` does not imply `full_access`.

Final access is always gated by:
1. server safety mode / current runtime trusted state
2. active profile `accessMode`
3. plugin requested mode
4. plugin supported modes

Therefore:
- `CUSTOM` + file-only profile => still no `full_access`
- `CUSTOM` can change config source, but not safety ceilings

## Canonical computed rule
Use this rule whenever profile state is loaded or repaired:

```text
if profile.plugins contains at least one area-specific plugin record:
    environmentMode = CUSTOM
else:
    environmentMode = DEFAULT
```

Future area-specific override types may extend the condition, but global plugin state alone must never satisfy it.
