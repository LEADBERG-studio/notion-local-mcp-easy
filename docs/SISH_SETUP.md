# Self-hosted sish relay for Notion Local MCP Easy

Use this path when you want the launcher to open an SSH reverse tunnel to **your own sish relay** instead of Serveo or Tunnellio.

## What this mode does

- launcher starts a built-in SSH reverse tunnel
- the tunnel target is your own `sish` relay
- the public MCP URL is derived as `https://<serveo_hostname>.<tunnel_domain>`
- unlike reverse-proxy mode, the launcher still owns the SSH tunnel lifecycle

## What you need

- a reachable `sish` relay
- the relay SSH endpoint host
- the relay public wildcard domain
- a reserved subdomain label
- a private SSH key accepted by the relay
- Windows OpenSSH client (`ssh.exe`)

## Setup flow

1. Run `START.bat`.
2. Choose **Self-hosted sish relay (SSH reverse tunnel)** in tunnel mode.
3. Enter:
   - `sish SSH endpoint host` — for example `relay.example.com`
   - `sish SSH port` — usually `22` unless your relay uses another port
   - `Public wildcard base domain` — for example `example.com`
   - `Reserved subdomain label` — for example `mcp`
   - private SSH key path

The resulting public MCP base URL becomes:

```text
https://mcp.example.com
```

## Config fields

The launcher stores these fields in `%LOCALAPPDATA%\NotionMcpEasy\config.json`:

```json
{
  "tunnel_backend": "sish",
  "tunnel_mode_preference": "sish",
  "tunnel_host": "relay.example.com",
  "tunnel_ssh_port": "2222",
  "tunnel_domain": "example.com",
  "serveo_hostname": "mcp",
  "ssh_key": "C:\\Users\\you\\.ssh\\sish_local_mcp"
}
```

## Command shape

The launcher builds a command in this form:

```bat
ssh -T -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -i C:\path\to\key -o IdentitiesOnly=yes -p 2222 -R mcp:80:127.0.0.1:8765 relay.example.com
```

## Notes

- OAuth and dual mode rely on the derived stable HTTPS origin.
- If the tunnel disconnects, launcher reconnect logic still applies because this mode is a built-in tunnel backend.
- Keep the SSH key private and expose the relay only on infrastructure you control.
