# Notion Local MCP Easy 1.8.3

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

Version 1.7.8 adds the `ide_provider` active-bridge plugin. It starts a local OpenAI-compatible endpoint on `127.0.0.1` so an IDE can send chat-completion requests to the active MCP model. The model serves those requests through `ide_provider_wait_request` and `ide_provider_send_response` while still using Local MCP Easy tools.

Use it only in trusted developer mode and only with IDEs/workspaces you trust. See the Russian beginner guide at `docs/ru/ide-provider.html`.


## IDE Gateway plugin with autonomous responder

Version 1.8.0 adds the `ide_gateway` plugin, a full OpenAI-compatible API gateway (`/v1/chat/completions`, `/v1/responses`, `/v1/models`, `/v1/files`, `/v1/images/*`, `/v1/audio/*`, `/v1/embeddings`, `/v1/moderations`, `/v1/tools`). Version 1.8.3 adds an autonomous responder loop that claims queued IDE requests and forwards them to a configured OpenAI-compatible upstream, so the IDE receives answers without manual `wait_request/send_response` calls.

Quick flow:

1. Run `plugins\ide_gateway\SETUP.bat` in trusted developer mode.
2. Answer `yes` to endpoint autostart and `yes` to autonomous responder, pick the backend (`openai_compatible`), and provide the upstream base URL, API key, and model (or choose `manual` for the hand-bridge).
3. Restart MCP — the endpoint starts on `127.0.0.1:8787` and the responder starts automatically.
4. Call `ide_gateway_show_config` (`include_secret=true`) and copy `base_url`, `api_key`, `model` into your IDE.
5. The IDE sends requests; the responder claims them and returns upstream answers automatically.
6. Diagnostics: `ide_gateway_status`, `ide_gateway_responder_status`, `ide_gateway_responder_logs`.


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

`plugins\\ide_gateway\\ENABLE.bat` applies safe working defaults without questions: endpoint autostart is enabled and a local `ideg_...` key is generated, but the autonomous responder remains disabled/manual until `SETUP.bat` is used to configure a real upstream base URL, API key (if needed), and model. This prevents requests from being claimed by a responder that has nowhere to send them.
