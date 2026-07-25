# OAuth / perimeter merge plan

Date: 2026-07-25
Status: active execution plan
Source baseline: `oleg494/local-mcp-easy` parallel branch
Target branch: `stablefix`

## Goal
Directly transfer the donor project's public-auth/perimeter layer into this repo while preserving our local runtime strengths:
- workflow profiles
- plugin runtime and plugin families
- git setup-flow / repo guard layer
- command-job hardening
- Tunnellio integration path

## Integration rule
- Donor repo is the source of truth for: embedded OAuth, dual mode, DCR/BYO OAuth client registration, reverse-proxy mode, self-hosted tunnel backends, public URL invariants, host allowlist behavior, and related docs/tests.
- Current repo remains the source of truth for: workspace/profile/runtime/plugin architecture, repo-context policy, trusted developer mode gating, and local tool surface.
- Prefer direct code transfer over reimplementation.

## Release sequence

### 1.5.0 — embedded OAuth core
Deliver a working server-side embedded OAuth provider inside this repo.
Includes:
- `auth/` package transfer
- server integration for `legacy` / `oauth` / `dual`
- discovery, protected-resource metadata, authorize/token/register/revoke/consent flow
- persisted OAuth state
- per-tool scope enforcement
Checkpoint: server boots in all auth modes and legacy clients still work.

### 1.5.1 — operator OAuth UX
Deliver launcher/operator flows for OAuth without manual JSON surgery.
Includes:
- `--oauth`
- `--register-oauth-client`
- owner code flow
- wrapper scripts for Windows and POSIX
Checkpoint: operator can enable OAuth/dual and pre-register a client.

### 1.6.0 — custom public URL / reverse proxy mode
Deliver first-class support for own domain and own reverse proxy.
Includes:
- `public_url`
- no-tunnel mode / custom-ssh mode
- reverse proxy docs
- stable URL validation
Checkpoint: MCP works behind nginx/Caddy/Traefik with exact issuer/discovery alignment.

### 1.6.1 — self-hosted sish backend
Deliver a self-hosted SSH reverse-tunnel backend.
Includes:
- `sish` backend support
- launcher integration
- setup docs
- backend tests
Checkpoint: operator can run with self-hosted relay instead of Serveo/Tunnellio.

### 1.7.0 — perimeter convergence / full gate
Deliver one coherent product line.
Includes:
- config cleanup
- docs convergence
- full regression matrix across auth/tunnel modes
Checkpoint: one publication-ready line with embedded OAuth + local runtime features.

## Donor files to transfer first
### Mandatory auth/server files
- `auth/__init__.py`
- `auth/base.py`
- `auth/consent.py`
- `auth/discovery.py`
- `auth/legacy.py`
- `auth/oauth.py`
- donor `server.py` auth/perimeter sections

### Mandatory launcher/operator files
- donor `launcher.py` OAuth/tunnel backend sections
- `OAUTH_SETUP.bat`
- `oauth_setup.sh`
- `REGISTER_OAUTH_CLIENT.bat`
- `register_oauth_client.sh`

### Mandatory docs
- `REVERSE_PROXY.md`
- `SISH_SETUP.md`
- auth-related README and SECURITY sections

### Mandatory tests
- `tests/test_oauth_flow.py`
- `tests/test_oauth_store.py`
- `tests/test_tunnel_backends.py`
- donor launcher/auth regressions as applicable

## Resume protocol
If the session is interrupted:
1. Read `HANDOFF_CURRENT.md`.
2. Continue the first non-DONE task file under `docs/workflow-plugin-system/tasks/` from TASK-015 onward.
3. Preserve the rule: copy donor perimeter code first, adapt second.
4. Do not redesign scopes/backends before transferred tests exist locally.

## Current execution decision
Proceed with TASK-015 first. Start by importing the donor `auth/` package into this repo before touching launcher/tunnel docs.