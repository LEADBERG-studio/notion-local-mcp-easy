#!/usr/bin/env bash
# Connection profile setup: the only script that writes connection profiles.
set -euo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

exec "$PYTHON" profiles_setup.py "$@"
