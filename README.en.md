# Notion Local MCP Easy 2.4.5

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

4. Configure a connection profile, then pick a workspace and access mode.

```bash
python profiles_setup.py       # build or repair a connection profile
python launcher.py --setup     # folder, access mode, connection profile
python launcher.py             # start: only asks which work area to run
```

## Connection profiles (2.4.0)

Connection handling was rebuilt in 2.4.0. Every connection technology is now an
independent circuit with its own settings namespace, its own validation and its
own runtime path. No circuit can read, require or rewrite another circuit's
fields, so a problem in one channel cannot take the others down with it.

The work is split into three steps:

| Script | Purpose | Prompts |
| --- | --- | --- |
| `PROFILES.bat` / `profiles_setup.py` | Build connection profiles | Only what that circuit needs |
| `SETUP.bat` / `--setup` | Configure a work area | Folder, access mode, profile |
| `START.bat` / `launcher.py` | Run the server | Work area only |

A connection profile is a **named instance** of a protocol, not one profile per
protocol. The same protocol can be saved several times with different domains
and keys, so `Prod MCP` and `Staging MCP` can both be Tunnellio stable against
different reservations. All saved profiles live in one flat numbered list, so a
work area connects with a single `1-N` choice, and every list shows the real
settings rather than just a name.

Seven protocols ship with the product:

1. **Serveo stable domain** — you generate the SSH key and reserve the hostname yourself; the profile asks for those two values only.
2. **Serveo temporary domain** — asks nothing. Random domain, may change on reconnect.
3. **Tunnellio stable domain** — you generate the SSH key and reserve the domain yourself; the profile asks for those two values only. Nothing else is required for this mode.
4. **Tunnellio random domain** — API token only. The setup script validates it against the Tunnellio server and refuses to save an unconfirmed token.
5. **Tunnellio direct TCP bridge** — keyless native bridge. Valid with zero input: the server issues a random domain. A reserved domain and an API token are optional.
6. **Self-hosted sish relay** — your own relay host, port, wildcard domain, subdomain and key.
7. **Custom public URL / reverse proxy** — public origin only, no built-in tunnel.

Key properties:

- Each profile has an immutable blueprint in `connections/defaults/<id>.json`. Blueprints ship with the release, are never hand-edited and supply every default, so a freshly configured profile is already complete.
- Configured profiles live in `%LOCALAPPDATA%\NotionMcpEasy\connection-profiles.v2.json` and **survive product upgrades**. They change only when the profile setup script runs.
- Work areas and the active selection live in one permanent file, `current-connection.json`.
- There is no silent failover between circuits. Switching channels is always an explicit operator action.
- The legacy `config.json` is still written for backward compatibility, but it is a generated mirror of the active profile. Fields belonging to inactive circuits are always empty.
- Per-area MCP tokens and OAuth owner codes by default, with an opt-in shared-credentials flag so channels can be swapped without re-authorizing clients.

## Editing tools

| Tool | Why it exists |
| --- | --- |
| `read_many_files` | One request for a list of files. A burst of small requests is the traffic shape that breaks a tunnel. |
| `apply_patch` | Unified diff instead of rewriting a file. A diff costs the change; a rewrite costs the file twice. |
| `search_and_replace` | Project-wide replacement, previewing by default with a sample line per file. |
| `tail_file` | The end of a text file, for logs. |

`apply_patch` applies every hunk or none, so a file never lands in a state nobody
described. Line numbers are hints: the context is searched for nearby, so a
slightly stale diff still applies.

## Transport stability

The server is almost always reached through a tunnel, which is a single TCP path
with a proxy at each end.

- **Connections are held open** for 120 seconds. The web server default is 5, and
  a client reusing its connection could send on a socket the server was closing;
  the relay answered `502 Bad Gateway`.
- **Responses are gzipped** above 1 KB. JSON-RPC is text and shrinks by roughly an
  order of magnitude.
- **Retries are deduplicated.** A resent request is replayed from a short window
  instead of executed twice.
- **Repeated reads are cached** for a few seconds. Any mutation clears that cache,
  so a read after a write is never stale.
- **Admission is bounded.** Beyond the limit the server refuses with `Retry-After`
  rather than queueing work invisibly.
- **Output is capped** for core tools, plugin tools and database results.

Use the `transport_health` tool to tell a real error apart from load shedding.

## Tunnel diagnostics

Killing a launcher window can leave its `ssh` or `tunnellio` child alive, still
holding a relay port and invisible in a raw task list.

```bash
DOCTOR.bat              # or: python launcher.py --doctor
DOCTOR.bat --cleanup    # offer to stop orphaned processes one by one
```

The report names every tunnel process, what it forwards, when it started, and
which one the running launcher owns. The live tunnel is never reported as an
orphan and is never terminated.

## Shell helpers

On POSIX-like environments, the `.sh` wrappers mirror the Python launcher commands:

```bash
./setup.sh
./start.sh
./show_connection.sh
./profiles.sh
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
