# Notion Local MCP Easy 2.3.0

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


## IDE Gateway plugin: IDE to model sandbox bridge

Version 2.3.0 makes the normal flow literal: after one-time setup, tell the model **“start the bridge”**. It calls `ide_gateway_bridge_prompt`, resolves its own sandbox's internal endpoint, key, API style, and exact model IDs, then runs one generated command in its own sandbox shell.

```text
IDE -> https://<hostname>.tunnellio.site/v1
    -> keyless Tunnellio TCP bridge
    -> resident OpenAI-compatible server in the model sandbox
    -> that sandbox's internal LLM egress
```

There is no sandbox SSH tunnel. The self-contained installer protects internal credentials, detaches the resident processes from the initiating tool call, reconnects automatically, reuses a live route, and treats temporary public 404/502/504 responses as propagation rather than a reason to kill healthy processes.

Quick flow:

1. Enable trusted developer mode for the workspace.
2. Run `plugins\ide_gateway\SETUP.bat`: choose `current`, `full_access`, `sandbox`, and usually `ephemeral` for the first run.
3. Restart MCP.
4. Tell the model: **“start the bridge”**. No internal credentials should be requested from the user.
5. Configure an OpenAI-compatible IDE provider with the returned public `base_url`, `ideg_...` key, and an exact model ID from `/v1/models`.

If the short command is misunderstood, use the dedicated fallback prompt in [`docs/IDE_GATEWAY_FALLBACK_PROMPT.md`](docs/IDE_GATEWAY_FALLBACK_PROMPT.md). Full operations and security guidance: [`docs/IDE_GATEWAY_SANDBOX.md`](docs/IDE_GATEWAY_SANDBOX.md).

Sandbox recovery commands:

```bash
python3 ~/.ide_gateway/sandbox_installer.py status
python3 ~/.ide_gateway/sandbox_installer.py repair
python3 ~/.ide_gateway/sandbox_installer.py stop
```

## IDE Bridge plugin: separate queue/poll bridge

`ide_bridge` is the compatibility queue/poll path. It has separate tools, runtime state, local URL `http://127.0.0.1:8797/v1`, `ideb_...` token, and `ide-bridge` model alias. Use `ide_gateway` for the direct sandbox TCP bridge and `ide_bridge` only when the active model must poll and complete queued IDE requests.


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
