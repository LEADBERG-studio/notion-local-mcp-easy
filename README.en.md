# Notion Local MCP Easy

Notion Local MCP Easy runs a local MCP server for a selected workspace and exposes file, git, and trusted-developer tools to compatible MCP clients.

The project is designed for local-first workflows:

- file-only mode for constrained workspace access;
- trusted developer mode for allow-listed commands;
- Streamable HTTP transport for modern MCP clients;
- legacy bearer-token and OAuth modes;
- tunnel options including Tunnellio, Serveo, sish, and custom reverse proxies;
- workflow profiles and plugin runtime foundations for advanced local automation.

## Quick start

1. Install Python 3.11 or newer.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Run setup:

```bash
python launcher.py
```

4. Follow the prompts to choose a workspace, auth mode, and tunnel mode.

## Shell helpers

On POSIX-like environments, the `.sh` wrappers mirror the Python launcher commands:

```bash
./setup.sh
./start.sh
./show_connection.sh
./tunnel_setup.sh
./register_oauth_client.sh
./stop.sh
```

On Windows, use the existing `.bat` files or run `python launcher.py` directly.

## Modes

### File-only mode

File-only mode keeps MCP file tools inside the selected workspace.

### Trusted developer mode

Trusted developer mode enables allow-listed Python, Git, and Node commands with the local user's privileges. Use this only for workspaces and clients you trust.

### Auth modes

- `legacy`: static bearer token.
- `oauth`: OAuth 2.1 flow.
- `dual`: both legacy token and OAuth on the same `/mcp` endpoint.


## IDE Provider plugin

Version 1.7.6 adds the `ide_provider` active-bridge plugin. It starts a local OpenAI-compatible endpoint on `127.0.0.1` so an IDE can send chat-completion requests to the active MCP model. The model serves those requests through `ide_provider_wait_request` and `ide_provider_send_response` while still using Local MCP Easy tools.

Use it only in trusted developer mode and only with IDEs/workspaces you trust. See the Russian beginner guide at `docs/ru/ide-provider.html`.


## Safety model

The server is local-first but powerful. Keep these rules in mind:

- do not expose the server publicly without an auth mode and a trusted tunnel/proxy setup;
- keep tokens and runtime files out of version control;
- keep allowed commands narrow;
- review changes to file, command, git, auth, and tunnel behavior with tests.

See `SECURITY.md` for details.

## Development

Run the standard gates before publishing changes:

```bash
python -m py_compile server.py launcher.py core.py profiles.py plugin_runtime.py
python -m unittest discover -s tests -v
```

## Documentation

- `SECURITY.md` — security model and reporting policy.
- `REVERSE_PROXY.md` — custom public URL / reverse proxy setup.
- `SERVEO_SETUP.md` — Serveo setup.
- `SISH_SETUP.md` — self-hosted sish relay setup.
- `CHANGELOG.md` — release notes.
