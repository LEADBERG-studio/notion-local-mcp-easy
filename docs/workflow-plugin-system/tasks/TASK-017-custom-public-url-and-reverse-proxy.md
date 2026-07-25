# TASK-017 — Custom public URL and reverse proxy mode

Status: TODO
Target release: 1.6.0

## Objective
Support operators who publish MCP through their own stable domain and proxy stack.

## Scope
- `public_url`
- no built-in tunnel path / custom proxy path
- host allowlist support for public host
- exact issuer/discovery/audience invariant
- reverse proxy documentation

## Donor sources
- donor launcher `public_url` / `custom-ssh` logic
- donor server host/public URL logic
- `REVERSE_PROXY.md`
- related SECURITY/README sections

## Checkpoints
- CP1: server boot validation for stable public URL
- CP2: launcher skips tunnel when `public_url` is configured
- CP3: reverse-proxy docs imported/adapted
- CP4: targeted proxy/backend tests pass