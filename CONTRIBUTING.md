# Contributing

Thanks for helping improve Notion Local MCP Easy.

## Development setup

1. Use Python 3.11 or newer.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Test gates

Before opening a pull request, run:

```bash
python -m py_compile server.py launcher.py core.py profiles.py plugin_runtime.py
python -m unittest discover -s tests -v
```

If your change touches file writes, command execution, git operations, tunnel setup, auth, or logging, add or update regression tests in `tests/`.

## Security expectations

This project can expose local files and trusted-developer commands to MCP clients. Treat security-sensitive changes conservatively:

- keep file operations anchored to the configured workspace;
- keep secrets out of logs, subprocess environments, and committed files;
- avoid widening allowed commands without tests and documentation;
- prefer exact dependency pins.

## Pull requests

Use the pull request template and include a concise description of behavior changes, test results, and any manual smoke testing.
