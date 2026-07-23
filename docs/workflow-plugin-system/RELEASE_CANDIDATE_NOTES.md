# Release Candidate Notes

## Status
Release-candidate discussion package for TASK-014.

## What this RC now includes
- PATH[N] evolved into a profile-aware workspace model without breaking legacy launcher/config/runtime behavior.
- DEFAULT/CUSTOM environment state is now explicit per workspace profile.
- Global and current-area plugin attach are both implemented and differentiated.
- Universal plugin registry contract is active.
- Diagnostics expose active profile state, plugin scope, config source, and failure visibility.
- Multi-database plugin foundation is proven by SQLite + PostgreSQL.
- External AI/subagent plugin foundation is proven by the OpenAI-compatible family.
- Self-service plugin authoring docs are aligned with the real contract and implemented families.
- Hardening docs define regression gates, migration checks, and resume safety.

## Discussion points before publication
- Whether PostgreSQL should keep the current `psql` CLI path for the first public release or gain a native driver first.
- Whether MySQL/MariaDB should remain backlog or be pulled into the first post-RC cycle.
- Whether dynamic plugin-registry rebuild without restart is worth prioritizing after release.
- Whether the release pipeline should require `.venv`-based lint execution when `ruff` is not available globally.

## Explicitly not done in this run
- No repository history changes.
- No remote publication.
- No tags or release uploads.
