# Notion Local MCP Easy 2.2.0

## Главное

`ide_gateway` и queue/poll режим теперь разделены. Новый плагин `ide_bridge` забирает старый poll-loop сценарий: отдельный endpoint, отдельный ключ, отдельные tools и отдельная настройка в IDE.

## Что изменилось

- `ide_gateway`: sandbox TCP bridge / direct serving, без poll tools.
- `ide_bridge`: queue/poll bridge через `bridge_step.py`, порт `8797`, ключ `ideb_...`, модель `ide-bridge`.
- Оба плагина можно включить одновременно и добавить в IDE как два разных OpenAI-compatible provider.
- Setup wizard теперь знает `ide_bridge`; `ide_gateway` больше не предлагает bridge mode.

## Проверка

- `python -m py_compile ...` OK
- `python tests/test_ide_gateway_bridge.py` OK

## Hotfix после публикации

- Launcher больше не падает, если общий `%LOCALAPPDATA%\NotionMcpEasy\config.json` потерял `workspace`, `token`, `auth_mode` или `tunnel_backend`: поля восстанавливаются автоматически.
- При `SSH tunnel exited with code 255` / `remote port forwarding failed for listen port 80` launcher чистит старый runtime и делает retry с backoff, вместо мгновенного падения.
- Добавлены regression tests для обоих сценариев.

## CI hotfix

- Added `.gitattributes` with `VERSION text eol=crlf`, so GitHub Actions on Linux checks out `VERSION` with CRLF and the byte-exact version test passes.

## Windows CI hotfix

- Launcher and plugin setup now configure stdout/stderr with `errors=replace`, so cp1252 Windows runners do not crash on Cyrillic messages.
- `safe_path()` keeps caller-facing 8.3/short path spelling while using resolved paths for security checks.
- Plugin local config writes no longer force-resolve plugin paths, avoiding short-vs-long path mismatches in Windows CI.

## Repo-context Windows path hotfix

- Repo-context save/disable messages now format paths through a safe display helper instead of direct `Path.relative_to(BASE_DIR)`, fixing Windows short-path aliases such as `RUNNER~1` in CI.

## Tunnel config safety hotfix

- Self-heal no longer guesses or rewrites `tunnel_backend`, so existing Serveo installs are not silently switched to Tunnellio.
- Serveo stable hostname accepts accidental full URLs and normalizes them to the reserved label.
- Serveo `.pub` key paths are rejected or auto-corrected to the private key.
- Tunnel retry is limited to real remote-port-busy relay errors; config errors fail immediately with a clear message.
