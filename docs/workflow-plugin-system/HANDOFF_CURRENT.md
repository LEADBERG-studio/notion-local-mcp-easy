Phase: Milestone 8 — Connection circuit rebuild
Task ID: TASK-020
Task title: One permanent config, isolated connection circuits, standalone profile setup
Status: DONE
Date: 2026-08-03
Shipped release: 2.4.0

## Why this work existed
A production upgrade broke every connection path at once. Serveo had a
temporary certificate problem, and because all modes shared one flat config,
one guessing layer and one runtime path, no other circuit could take over.
Recovery took hours of hand-editing code and configs.

Root causes found in the 2.3.0 tree:
1. Four competing state stores plus a growing pile of `config.backup.*.json`.
2. `default_tunnel_backend()` inferred Tunnellio simply because `tunnellio.exe`
   existed on disk, and Serveo from any leftover hostname.
3. `heal_legacy_config()` restored "missing" sensitive fields from the newest
   backup regardless of which mode they belonged to.
4. Shared field names across modes.
5. One command builder, one validation and one retry policy for all modes.
6. `build_release.py` did not exclude `connections.cfg`, so upgrades wiped it.
7. `START.bat` ran the full setup wizard on every start.
8. The Tunnellio keyless TCP bridge lived only inside the `ide_gateway` plugin.

## What shipped in 2.4.0
- `connections/` package: `base.py`, `blueprints.py`, `store.py`,
  `setup_flow.py`, `diagnostics.py`, seven blueprints in `defaults/` and seven
  circuits in `circuits/`. Isolation is enforced by `Circuit.merge()`, which
  drops any key absent from that circuit's own blueprint.
- One permanent record in `current-connection.json`; configured profiles in
  `connection-profiles.v2.json`, both excluded from the release archive so an
  upgrade cannot erase them.
- `profiles_setup.py` / `PROFILES.bat` / `profiles.sh` / `--profiles`: the only
  writer of connection profiles.
- `connection_runtime.py`: START picks a work area only; SETUP picks folder,
  access mode and an already-configured profile.
- `tunnellio_stable` supports two transports: `ssh` (direct, default) and `cli`
  (managed client, enabled by Tunnellio client 0.6.0), with a one-shot fallback
  to direct SSH when the managed client cannot start.
- `tunnellio_random` validates its API token live and refuses to save an
  unconfirmed one.
- `tunnellio_bridge`: keyless TCP bridge, valid with zero configuration, with
  one-shot recovery to a fresh domain when a cached ephemeral domain expired.
- `DOCTOR.bat` / `--doctor`: lists tunnel processes, marks the live one and
  names orphans; `--cleanup` stops orphans only.
- The bridge logic now exists once: `ide_gateway` reuses
  `connections/circuits/_tunnellio_client.py` and keeps local fallbacks only
  for the standalone copy that runs inside a model sandbox.
- Bundled Tunnellio client updated to 0.6.0.
- Single rolling `config.json.bak`; the timestamped pile is gone.
- Fixed an infinite prompt loop when stdin is closed.
- Removed a stray project-root `config.json` with a hardcoded token.
- Docs: README.md, README.en.md, CHANGELOG, release notes and the Russian doc
  site (connections, profiles, quickstart, tunnels, troubleshooting,
  reference).
- Tests: 392 pass, including `test_connection_circuits.py`,
  `test_connection_store.py` and `test_tunnel_diagnostics.py`.

## Upstream work completed alongside
Tunnellio client 0.6.0 was fixed and released in its own repository
(`LEADBERG-studio/tunnellio-api-client`, branch `main`):
- `403 plan_required` is no longer reported as an authentication failure;
  `meta` and `capabilities` became advisory and degrade gracefully.
- Credential-aware modes: an API token unlocks everything, an SSH key alone
  still allows `ssh_stable` / `tcp_stable` / `tcp_random`, and with no
  credentials the two keyless bridge modes still work. The API token is never
  read in those three modes.
- Fixed `bridge --save-profile`, which always crashed.

## Rules to preserve
- The circuit layer is the source of truth. Do not reintroduce shared tunnel
  fields, do not infer a mode from files on disk, and do not restore sensitive
  values from backups across modes.
- No silent failover between circuits. Switching channels is an explicit
  operator action through SETUP.
- Blueprints under `connections/defaults/` are read-only examples. Profiles are
  written only by the profile setup script.

## Possible next steps
- Wire the plugin enable/disable checkboxes and the profile editor windows into
  a UI, now that the underlying model supports them.
- Consider surfacing `DOCTOR` output in that UI as a health panel.
