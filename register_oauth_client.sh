#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
exec python launcher.py register_oauth_client "$@"
