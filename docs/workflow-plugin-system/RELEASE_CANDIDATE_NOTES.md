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
- Tunnel setup is explicit and operator-facing: setup offers `Tunnellio managed runtime`, `Serveo temporary domain`, and `Serveo stable domain`.
- Background command jobs are hardened for heavier output so transport-facing polling remains responsive.

## Publication discussion points
- Whether to commit and push the current `stablefix` tree as `1.4.5`.
- Whether to tag and publish `release/notion-mcp-easy-1.4.5.zip` immediately after review.
- Whether the superseded `release/notion-mcp-easy-1.4.4.md` draft should be kept as local history or removed before publication.

## Explicitly not done in this run
- No repository history changes.
- No remote publication.
- No tags or release uploads.
