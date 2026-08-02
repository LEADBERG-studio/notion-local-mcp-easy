# Notion Local MCP Easy 2.1.0

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


## IDE Gateway plugin — IDE to sandbox model bridge

`ide_gateway` exposes an OpenAI-compatible `/v1` API for IDEs. In the default **sandbox** mode, the model only needs one command after the user says “start the bridge”: `sandbox_bootstrap.py` captures this sandbox's internal model endpoint/key, starts a resident `sandbox_server.py`, and launches a keyless Tunnellio TCP bridge with `tunnellio bridge --run --watch`. No SSH keys are generated.

Modes:

- **sandbox** (default) — public Tunnellio URL → sandbox server → this sandbox's LLM egress. Best path for IDE usage.
- **bridge** — compatibility fallback where the model serves a queue with `bridge_step.py poll/complete`.
- **external** — local worker calls an OpenAI-compatible provider directly.

Stable parts:

- the local `ideg_...` API key is stored in plugin config and survives normal upgrades;
- `ide_gateway_show_config(include_secret=true)` returns the IDE settings; after bootstrap, `base_url` is the public Tunnellio URL;
- ephemeral hostnames are cached and reused while the one-day domain is alive; if expired, bootstrap creates and saves a new one;
- custom hostnames from setup are reused permanently;
- daemonized processes plus the Tunnellio watch loop keep the server and tunnel alive beyond short MCP tool-call timeouts.

Quick flow:

1. Run `plugins\ide_gateway\SETUP.bat` in trusted developer mode. Choose `current`, `full_access`, `sandbox`, usually `ephemeral`.
2. Restart MCP.
3. Call `ide_gateway_bridge_prompt`; it contains the one bootstrap command.
4. Tell the model: “start the bridge”. It returns the public `base_url`.
5. Configure your IDE as OpenAI-compatible using `base_url`, `api_key`, and `model` from `ide_gateway_show_config`.

Diagnostics: `ide_gateway_status`, `ide_gateway_get_logs`, `ide_gateway_show_config`.


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
