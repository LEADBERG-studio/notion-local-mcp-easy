# Self-hosted sish relay setup

The sish tunnel mode is for operators who want to use their own SSH reverse-tunnel relay instead of a managed tunnel or Serveo.

## When to use this mode

Use sish when you need:

- a stable public hostname under infrastructure you control;
- SSH reverse-tunnel semantics;
- more control over relay configuration, logging, and network policy.

If you already have a reverse proxy in front of the local server, use custom public URL / reverse proxy mode instead.

## Prerequisites

- A reachable sish server.
- An SSH private key that can authenticate to the relay.
- A public hostname configured for the relay.
- Local MCP auth configured (`legacy`, `oauth`, or `dual`).

## Setup

Run the tunnel setup flow and select the self-hosted sish relay option:

```bash
python launcher.py tunnel_setup
```

or, on POSIX-like systems:

```bash
./tunnel_setup.sh
```

Provide:

- relay host;
- public hostname;
- SSH key path;
- local server port.

## Validation

After starting the server, verify:

1. the SSH tunnel process is running;
2. the public URL resolves to the local MCP endpoint;
3. `/mcp` is reachable only with the configured auth flow;
4. no local-only runtime files are committed.

## Troubleshooting

- If the public URL does not resolve, check DNS and relay host configuration.
- If SSH exits immediately, check key permissions and relay authorization.
- If MCP clients receive auth errors, verify the selected auth mode and token/OAuth configuration.
- If a tunnel process is left behind, stop it with the project stop command and verify with your OS process tools.
