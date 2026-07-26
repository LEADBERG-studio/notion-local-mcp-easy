#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
exec python launcher.py show_connection "$@"
