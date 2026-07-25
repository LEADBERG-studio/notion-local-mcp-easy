# TASK-016 — OAuth launcher UX and BYO client registration

Status: DONE
Target release: 1.5.1

## Objective
Expose embedded OAuth through operator-friendly launcher/setup flows.

## Scope
- donor launcher OAuth commands
- owner code generation/display/storage
- `--register-oauth-client`
- wrapper scripts for Windows and POSIX
- connection info updates

## Deliverables
- `OAUTH_SETUP.bat`
- `oauth_setup.sh`
- `REGISTER_OAUTH_CLIENT.bat`
- `register_oauth_client.sh`
- launcher config schema updates
- docs refresh for operator flow

## Checkpoints
- CP1: launcher accepts OAuth mode configuration
- CP2: owner code generated/stored safely
- CP3: BYO client registration works
- CP4: connection display shows required OAuth values
- CP5: launcher tests pass