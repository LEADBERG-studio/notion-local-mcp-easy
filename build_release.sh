#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

python -m py_compile server.py launcher.py core.py profiles.py plugin_runtime.py
python -m unittest discover -s tests -v
python build_release.py "$@"
