# TASK-015 — Embedded OAuth core

Status: IN PROGRESS
Target release: 1.5.0

## Objective
Import the donor embedded OAuth server capability into this repo so OAuth no longer depends on Tunnellio's external auth plane.

## Scope
- copy donor `auth/` package into this repo
- integrate server auth modes `legacy / oauth / dual`
- add discovery + protected-resource metadata
- add DCR / authorize / token / revoke / consent flow
- add persisted OAuth state
- add per-tool scope enforcement
- preserve current legacy token behavior for classic clients

## Donor sources
- `auth/__init__.py`
- `auth/base.py`
- `auth/consent.py`
- `auth/discovery.py`
- `auth/legacy.py`
- `auth/oauth.py`
- donor `server.py` auth/perimeter blocks
- donor tests `test_oauth_flow.py`, `test_oauth_store.py`

## Step plan
1. Create local `auth/` package from donor code.
2. Wire imports into current `server.py` without removing current local runtime/tool logic.
3. Introduce auth-mode parsing and server bootstrap invariants.
4. Add host/public URL logic needed by embedded OAuth.
5. Add scope mapping to current tools.
6. Transfer/adapt OAuth tests.
7. Run targeted auth + legacy regression batches.

## Checkpoints
- CP1: `auth/` package shell copied into repo (DONE: `__init__`, `base`, `legacy`, `discovery`, `consent`, temporary `oauth` scaffold added)
- CP2: server imports compile (DONE: auth bootstrap + optional scope wrapper compile successfully)
- CP3: server starts in `legacy` (DONE)
- CP4: server starts in `dual` and `oauth` (DONE)
- CP5: OAuth flow tests pass (DONE: `tests.test_oauth_store` and `tests.test_oauth_flow`)
- CP6: legacy and repo/profile/plugin regressions still pass (MOSTLY DONE: targeted suites passed; one repo-context rerun still blocked intermittently by MCP transport errors)

## Done when
- embedded OAuth works locally in this repo
- `legacy`, `oauth`, and `dual` all boot correctly
- OAuth tests pass
- existing local product behavior is not regressed