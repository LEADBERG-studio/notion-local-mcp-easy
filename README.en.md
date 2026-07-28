# Notion Local MCP Easy 1.8.7

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


## IDE Gateway plugin — bridge between IDE and the model

Version 1.8.0 adds the `ide_gateway` plugin, a full OpenAI-compatible API gateway (`/v1/chat/completions` stream + non-stream, `/v1/responses` stream + non-stream, `/v1/models`, `/v1/files`, `/v1/images/*`, `/v1/audio/*`, `/v1/embeddings`, `/v1/moderations`, `/v1/tools`). The gateway starts a local endpoint on `127.0.0.1:8787` that the IDE sees as a regular OpenAI provider, but behind it is the active MCP model with access to your files, shell, and web.

The bridge works via long-poll: the model in chat starts a `ide_gateway_wait_request` loop once and keeps it open. When the IDE sends a request, it reaches the model instantly; the model processes it with its MCP tools and replies through `ide_gateway_send_response`. No manual pings, no chat noise.

Quick flow:

1. Run `plugins\ide_gateway\SETUP.bat` in trusted developer mode. Choose `current` scope, `full_access` mode, defaults.
2. Restart MCP — the endpoint starts on `127.0.0.1:8787`.
3. Call `ide_gateway_bridge_prompt` and paste the prompt template into the Notion Agent system prompt (one-time).
4. The model starts the `wait_request` loop — the bridge stays up permanently.
5. In your IDE, add an OpenAI-compatible provider: `base_url = http://127.0.0.1:8787/v1`, `api_key` and `model` from `ide_gateway_show_config`.
6. The IDE sends requests — the model serves them directly through the bridge.

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


### IDE Gateway defaults note

\n\n### IDE Gateway is a PromptQL bridge\n\n`plugins\\ide_gateway\\ENABLE.bat` configures a local IDE-facing `/v1` endpoint and generated `ideg_...` key. It does **not** ask for a local OpenAI/Ollama upstream: IDE requests are queued for the active PromptQL/Notion agent, and the real model is selected in PromptQL chat/project settings. Until reverse callback automation exists, queued requests are completed through the bridge tools (`ide_gateway_wait_request` / `ide_gateway_send_response`).\n
Current IDE Gateway note: the local config model is only an IDE-facing alias (`ide-gateway`). The real model is selected by the active PromptQL/Notion chat or project settings; normal setup does not ask for an upstream base URL or upstream model.
