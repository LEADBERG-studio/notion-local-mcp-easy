# Reverse proxy mode

`notion-local-mcp-easy` can run behind your own stable domain instead of the built-in Serveo/Tunnellio tunnel.

This mode is intended for operators who already have a reverse proxy such as **nginx**, **Caddy**, **Traefik**, or another HTTPS edge in front of the local machine.

## What this mode does

- MCP still listens locally on `127.0.0.1:<port>`.
- Launcher does **not** start Serveo or Tunnellio.
- `public_url` becomes the canonical public origin for:
  - OAuth issuer
  - discovery metadata
  - protected-resource metadata
  - connection output shown by the launcher
- Host allowlist accepts the configured public host in addition to localhost.

## Setup

1. Run `SETUP.bat`.
2. Choose **Custom public URL / reverse proxy (no built-in tunnel)**.
3. Enter the public base URL, for example:

   ```text
   https://mcp.example.com
   ```

4. Configure your reverse proxy to forward that host to the local MCP port on `127.0.0.1`.
5. Start the server with `START.bat`.

The launcher writes the same public URL into connection output and OAuth metadata. Use exactly that URL in the MCP client.

## Important invariant

The configured `public_url` must be the **exact public origin** that clients use.

Do **not**:

- append `/mcp` inside `public_url`
- add a path prefix like `/api/mcp`
- mix one hostname in the launcher and another hostname in the reverse proxy
- point discovery to a different origin than the MCP endpoint

Correct:

```text
public_url = https://mcp.example.com
MCP endpoint = https://mcp.example.com/mcp
issuer = https://mcp.example.com
protected resource = https://mcp.example.com/mcp
```

Incorrect:

```text
public_url = https://mcp.example.com/mcp
public_url = https://gateway.example.com/proxy/mcp
issuer = https://auth.example.com
resource = https://mcp.example.com/mcp
```

## Reverse proxy requirements

Your proxy should:

- terminate HTTPS on the public side
- forward requests to `127.0.0.1:<port>`
- preserve the public `Host` header
- expose the following paths unchanged:
  - `/mcp`
  - `/health`
  - `/.well-known/oauth-authorization-server`
  - `/.well-known/oauth-protected-resource/mcp`
  - `/authorize`
  - `/oauth/token`
  - `/oauth/register`
  - `/oauth/revoke`
  - `/oauth/introspect`
  - `/consent`

## Validation notes

- In OAuth or dual mode, production use requires a stable `https://` public URL.
- For local-only testing, `127.0.0.1` may still be used as the issuer.
- Launcher skips built-in tunnel startup in reverse-proxy mode. If the public URL is not reachable yet, the server still starts and warns you to finish proxy wiring.

## Security notes

- Treat the public MCP URL and bearer token as secrets.
- Keep TLS termination on a trusted proxy you control.
- Do not expose the local port directly on `0.0.0.0`; the server is designed to stay bound to localhost with the proxy in front.
