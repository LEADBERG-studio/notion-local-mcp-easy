## 2.4.3 - 2026-08-04

### Transport: dedup, cache and a real queue

Keep-alive and gzip fixed the two obvious causes in 2.4.2. This release adds the
things that actually make a tunnelled server survive a busy agent.

- **Retry deduplication.** A tunnel hiccup makes a client resend a request that
  already arrived. Every JSON-RPC request carries an id, so an identical resend
  is now replayed from a short window instead of executed twice. Writing the
  same file twice because the transport stuttered was the worst failure mode
  here, and it is gone.
- **In-flight joining.** Two identical requests arriving together share one
  execution. The second waits for the first instead of competing with it for the
  same connection.
- **Read cache.** Identical read-only calls (`read_file`, `list_dir`,
  `grep_files`, `tools/list`, ...) are answered from memory for a few seconds.
  Agents re-read the same file constantly while reasoning; that traffic no
  longer reaches the disk or the event loop. Mutations are never cached.
- **Admission control with `Retry-After`.** Beyond a bounded number of in-flight
  requests the server refuses work explicitly instead of queueing it where
  nobody can see it. A refusal is recoverable; a dead transport is not.
- **The cache is credential-scoped.** Entries are keyed on a hash of the
  presented credential, so a cached success can never be replayed to a different
  caller. The OAuth suite caught this the moment it was missing, which is
  exactly why it is a test and not a comment.
- New `transport_health` tool reports limits and counters, so an intermittent
  failure can be told apart from load shedding: `rejectedOverload` above zero
  means requests were refused on purpose, a high `replayedRetries` means the
  client is resending.
- All of it is tunable: `MCP_DEDUP_TTL_SECONDS`, `MCP_READ_CACHE_TTL_SECONDS`,
  `MCP_MAX_INFLIGHT`, `MCP_ADMISSION_WAIT_SECONDS`, `MCP_CACHE_MAX_ENTRIES`,
  `MCP_CACHE_MAX_BODY`.

### Database plugins are now complete

Both plugins went from five tools to ten, and read-only became a guarantee
rather than a guess.

- **Server-enforced read-only.** PostgreSQL reads run with
  `default_transaction_read_only=on`; MySQL reads run with
  `SET SESSION TRANSACTION READ ONLY`. Inspecting the SQL text cannot catch a
  write hidden inside a function, a CTE or a routine. The server can.
- **In-database timeouts.** PostgreSQL gets `statement_timeout`, so a runaway
  query is cancelled by the database instead of merely abandoned locally.
- New tools for both: `*_server_info` (version and a compatibility verdict),
  `*_list_schemas`, `*_count_rows` (exact, with an optional WHERE),
  `*_sample_rows`, `*_explain`. `*_describe_table` now returns indexes and
  constraints or foreign keys, and `*_list_tables` returns on-disk size.
- **Fixed a hang.** `psql` prompts for a password on stdin when one is missing,
  which in a server context is an indefinitely hung tool call. It now runs with
  `-w` and reports an actionable error instead. Found by testing against a real
  PostgreSQL 17 server, not by reading the code.
- **Fixed unreadable errors.** On a localised Windows the clients emitted
  OEM-codepage text that arrived as mojibake. Messages are now untranslated and
  UTF-8, so a real error is readable.
- MySQL 8 specifics: `utf8mb4` on the client, and a `caching_sha2_password`
  failure now explains that it needs TLS and points at `ssl_mode`.
- Verified against PostgreSQL 15+ (tested with a live 17.9 server and the 17.9
  client) and the MySQL 8.4 client.

### openai_compat removed

It overlapped the subagent plugin and it leaked: `describe_provider` returned
the provider endpoint and model list to the model. The subagent plugin covers
the same ground without exposing anything, so `openai_compat` and its
`ai_shared` helper are gone.

What moved across: the subagent plugin now supports **several named targets**,
so one chat can compare models or delegate different jobs to different ones.
`subagent_list_targets` lists names only. A session stays bound to the target it
was opened against, so a follow-up cannot silently land on a different model.

### Fixes

- A missing key environment variable no longer stops the subagent plugin from
  loading. It reported "failed" and took every tool away, hiding the reason.
  Diagnostics now say `keyPresent: false` and name the variable, and only a real
  call insists on the key.

### Tests

- Added `tests/test_transport_guard.py`; retargeted the AI plugin suite to the
  subagent plugin.

## 2.4.2 - 2026-08-03

### Transport stability

Two separate causes were killing connections through a tunnel, and neither was a
bug in the tool code.

- **Bursts of small calls.** Uvicorn's default keep-alive is 5 seconds. A client
  reusing its HTTP/1.1 connection could send a request on a socket the server was
  closing at that exact moment; the relay had nothing to forward it to and
  answered `502 Bad Gateway`. Keep-alive is now 120 seconds by default, which
  removes the race. Override with `MCP_KEEP_ALIVE_SECONDS`.
- **Large single responses.** Responses are now gzipped above 1 KB. JSON-RPC
  payloads are text and shrink by roughly an order of magnitude, so the same
  answer spends far less time on the wire. Override with `MCP_GZIP_MIN_SIZE`.
- Added bounded concurrency (`MCP_LIMIT_CONCURRENCY`, default 64), a larger
  socket backlog (`MCP_SOCKET_BACKLOG`), more header room for proxy metadata
  (`MCP_MAX_HEADER_BYTES`) and an explicit graceful shutdown window. A tunnel is
  one TCP path, so refusing excess work is recoverable while a dead transport is
  not.
- **Plugin output is now clipped.** Core tools were capped by the server's own
  decorator, but plugin tools reached the transport unclipped, so one broad query
  could push megabytes through the tunnel. Same ceiling now applies
  (`MCP_PLUGIN_OUTPUT_CHARS`).
- **Database results are capped by default.** 200 rows, 500 characters per cell,
  with the withheld amount reported and a hint to page. A broad `SELECT` was the
  easiest way to take the transport down by accident.

### START asks about the folder

The normal setup is one outbound channel registered once in the cloud project,
with many folders pointed at it. So the folder is the question worth asking.

- START always opens with the work area list and an "add another folder" entry.
- A folder added at START inherits the standing connection profile instead of
  asking about protocols again.
- SETUP asks once whether the chosen profile should become the standing default
  for folders added later.

### New plugins

- **MySQL / MariaDB** (`plugins/mysql`): connection aliases, table listing,
  column description, read-only query and a separate `full_access` execute. The
  read-only tool refuses write statements and stacked statements outright. The
  password is passed through a private defaults file, never on a command line
  where any process could read it.
- **Remote model subagent** (`plugins/subagent`): lets the model in chat talk to
  another model for prompt and tool testing, or as a delegate. One-shot
  `subagent_ask`, plus `subagent_start` / `subagent_say` / `subagent_end` for
  multi-turn work. Endpoint, key and model id live in local config and are never
  returned by any tool, error or log line; the model id is hidden unless
  explicitly exposed. Replies are capped, session history is a bounded rolling
  window, and token usage is reported because that is what costs money.
- PostgreSQL gained the same row caps and `row_limit` argument.
- Both new plugins have setup flows in `plugin_setup.py`.

### Tests

- Added `tests/test_transport_hardening.py` and `tests/test_new_plugins.py`.

## 2.4.1 - 2026-08-03

### Named connection profiles

A connection profile is now a **named instance** of a protocol, not one profile
per protocol. The case that drove this: two folders speaking the same protocol
against different domains, with different keys.

- Profiles have their own names and ids, so `Prod MCP` and `Staging MCP` can both
  be `tunnellio_stable` with different domains and keys.
- One flat numbered list everywhere: pick `1-N` and connect in a single move.
- Every list shows the actual settings, not just a name. Choosing between two
  profiles is impossible if you cannot see which domain and key each carries.
- `PROFILES.bat` gained create, edit, rename, duplicate, delete, verify, reset
  and blueprint view. Duplicate is the fast path for "same protocol, different
  domain": copy, change what differs, save under a new name.
- New profiles are named for you from what makes them distinct, for example
  `Tunnellio stable domain - prod-mcp`. The name is editable.
- A profile can be selected by number or by name.
- An incomplete profile can never be selected for a work area.
- `START.bat` is a quick start: an area that already has a profile asks nothing.
  An area without one asks a single question, offering the saved profiles plus
  "create a new profile" inline.
- The active profile name is shown at startup and written into the connection
  info, so it is obvious which channel is live.
- Deleting a profile that a work area uses is reported, and that area asks for a
  new profile at the next start instead of failing silently.

### Compatibility

- Profile storage moved to schema 3. Version 2 files, which held one profile per
  circuit keyed by circuit id, are imported automatically: the circuit id is
  kept as the instance id so existing work areas keep resolving.
- A work area still pointing at a bare circuit id resolves when exactly one
  profile exists for that circuit, and asks for an explicit choice otherwise.
  Ambiguity is never guessed.
- The legacy mirror gained `connection_profile_id` and `connection_profile_name`.

### Tests

- Added `tests/test_named_profiles.py`: instances, listing, area binding, v2
  migration, quick start and selection rules.

## 2.4.0 - 2026-08-03

### Connection rebuild

- **One permanent connection record.** `current-connection.json` now holds every work area, its access mode, its connection profile and its credentials. The old mix of `config.json`, `connection-profiles.json`, `workflow-profiles.json` and `connections.cfg` no longer competes for authority.
- **Independent circuits.** Every connection technology lives in its own module under `connections/circuits/` with a private settings namespace, its own validation, command builder, URL resolution, health path, retry policy and shutdown. A circuit cannot read, require or rewrite another circuit's fields.
- **Immutable blueprints.** Each circuit ships a read-only example in `connections/defaults/<id>.json`. A profile is created by cloning its blueprint, so a freshly configured profile is already complete and operators are never asked for plumbing values.
- **Standalone profile setup.** New `PROFILES.bat` / `profiles_setup.py` is the only writer of connection profiles: configure, verify, inspect the read-only blueprint, reset to blueprint. Also available as `launcher.py --profiles` and `profiles.sh`.
- **Split responsibilities.** `START.bat` now only asks which work area to run. `SETUP.bat` handles folder, access mode and which configured profile the area uses.
- **Profiles survive upgrades.** `build_release.py` excludes `connections.cfg`, `connection-profiles*.json` and `current-connection.json`, so unzipping a release over an existing install no longer wipes operator state.

### Tunnel diagnostics

- Added `DOCTOR.bat` / `launcher.py --doctor`: lists every running tunnel process, names what each one forwards, and marks the one the running launcher owns. Killing a launcher window used to leave its `ssh`/`tunnellio` child alive holding a relay port, invisible in a raw task list.
- `--doctor --cleanup` offers to stop orphaned processes one by one. The live tunnel is never reported as an orphan and is never terminated.

### New and reworked circuits

- Added **Tunnellio direct TCP bridge** to the launcher: keyless native transport, valid with zero configuration, server-issued random domain with no extra conditions, optional reserved domain and optional API token, plus one-shot recovery to a fresh domain when a cached ephemeral domain has expired.
- **Tunnellio random domain** validates the API token live against the server during setup and refuses to save an unconfirmed token. An authenticated `plan_required` answer counts as valid; only `401`/`invalid_token` is rejected.
- **Tunnellio stable domain** supports two transports. `ssh` (default) is a direct reverse forward to the Tunnellio edge: no client binary, no API call, maximum robustness. `cli` uses the managed `tunnellio.exe connect` path for supervision and health checks, and is available now that Tunnellio client 0.6.0 stopped demanding an API token for a reserved domain and stopped treating `403 plan_required` as an auth failure. If the managed client cannot start, the circuit falls back to direct SSH once rather than costing the operator their tunnel.
- **Serveo stable** and **Serveo temporary** are both retained as separate circuits. Temporary needs no input at all; stable asks only for the reserved hostname and the SSH key.
- **sish** and **reverse proxy** keep their own fields instead of borrowing `serveo_hostname` and `tunnel_domain`.

### Fixes

- Mode is never inferred from files on disk. The presence of `tunnellio.exe` no longer selects the Tunnellio backend, and a leftover hostname no longer selects Serveo.
- Sensitive values are never restored across modes. The old self-heal read the newest timestamped backup regardless of which mode the values belonged to, which is how a key or domain from one circuit leaked into another.
- Config backups reduced to a single rolling `config.json.bak`; the timestamped pile is pruned.
- Interactive prompts no longer spin forever when stdin is closed (service start, CI, piped launch). They abort with a clear message.
- Removed a stray project-root `config.json` that carried a hardcoded access token.
- The Tunnellio bridge logic existed twice: once in the launcher circuits and once inside the `ide_gateway` plugin. The plugin now reuses `connections/circuits/_tunnellio_client.py` when importable, keeping its local fallbacks only for the standalone copy that runs inside a model sandbox.
- Bundled Tunnellio client updated to 0.6.0.
- `TUNNEL_SETUP` now defers to the profile setup script instead of hand-editing tunnel fields in the flat config.

### Compatibility

- The legacy flat `config.json` is still written for older consumers, but purely as a generated mirror of the active profile. Fields belonging to inactive circuits are always empty.
- `connections.cfg` remains and stays hand-editable; it is now a mirror of the known work areas.
- Pre-2.4.0 installations are imported once, conservatively, without rotating any secret and without guessing a mode from ambiguous evidence.
- Per-area MCP tokens and OAuth owner codes by default, plus an opt-in shared-credentials flag so connection channels can be swapped without re-authorizing MCP clients.

### Tests

- Added `tests/test_connection_circuits.py` (42 tests): per-circuit commands, validation, URL resolution and cross-circuit isolation.
- Added `tests/test_tunnel_diagnostics.py`: live-versus-orphan classification and safe cleanup.
- Added `tests/test_connection_store.py` (33 tests): areas, per-area vs shared auth, legacy migration, upgrade safety and release packaging.
- Full suite: 392 tests OK.

## 2.3.0 - 2026-08-03

- `ide_gateway` plugin manifest is bumped to `0.4.0` for the new one-command sandbox installer and native Tunnellio transport.
- `ide_gateway_bridge_prompt` now embeds a self-contained installer that the model runs with one command inside its own sandbox after `подними мост` / `start the bridge`.
- The installer stores the sandbox's internal endpoint/key in protected local state, advertises exact discovered model IDs, proxies OpenAI- and Anthropic-style upstreams, and never exposes internal credentials in the final report.
- Replaced the sandbox SSH path with the keyless native Tunnellio TCP bridge protocol. No SSH key generation, public-key registration, or cloud API token is required in the sandbox.
- Resident server and bridge processes are detached from the initiating tool call, reconnect automatically, reuse a live route, retain only five logs, and survive public edge propagation delays without being killed.
- Added idempotent `install`, `status`, `repair`, and `stop` operations plus local/public health and `/v1/models` verification.
- Added dedicated canonical and fallback tunnel prompts, a full beginner walkthrough, security rules, troubleshooting, recovery commands, and synchronized Russian/English documentation.
- Live verification passed through a real `*.tunnellio.site` route: exact models were listed and a chat completion preserved the requested model ID.

## 2.2.0 - 2026-08-02
- Production upgrade audit: legacy flat configs are imported conservatively; missing tokens restore from backups or require explicit setup, never silent rotation.
- Startup now confirms whether to keep the active connection mode; switching can reuse per-mode settings or enter explicit setup.
- Runtime preflight validates workspace, keys, URLs, trusted command defaults, and Tunnellio credentials before processes start.
- Server/tunnel logs and config backups are capped at the latest 5 files; Tunnellio runtime names include a path hash to avoid collisions.
- Startup health hardening: a public `/health` miss after the tunnel URL is known is now a warning, not a fatal launcher stop.
- Connection profiles: each tunnel mode now keeps its own last-known settings in `connection-profiles.json`; setup asks whether to keep the mode and whether to reuse saved settings.
- Config safety: every config write now creates a timestamped backup and refuses sensitive production-field rewrites outside explicit setup.
- CI hotfix: repo-context status messages now use robust display paths, avoiding Windows 8.3 short-path `relative_to()` crashes.
- CI hotfix: Windows cp1252 stdout no longer crashes on Cyrillic setup messages; safe path tests keep caller path spelling while still checking resolved containment.
- CI hotfix: added `.gitattributes` rule `VERSION text eol=crlf` so Linux checkout preserves byte-exact VERSION CRLF invariant.
- Hotfix: launcher self-heals shared config missing `workspace`, `token`, or `auth_mode`, but never guesses/rewrites `tunnel_backend`.
- Hotfix: Serveo hostname is normalized from accidental full URLs to the reserved label, `.pub` ssh keys are rejected/auto-corrected to private keys, and retry is limited to real relay port-busy errors.
- Split the queue/poll IDE mode out of `ide_gateway` into a standalone `ide_bridge` plugin.
- `ide_gateway` is now focused on sandbox/external direct serving; sandbox remains the keyless Tunnellio TCP bridge path.
- Added separate IDE Bridge defaults: port `8797`, model alias `ide-bridge`, token prefix `ideb_`, runtime `temp/ide_bridge_runtime`, and `ide_bridge_*` tools.
- Added `ide_bridge_bridge_prompt` with the run_program poll loop prompt; the two plugins can be enabled together as separate IDE providers.
- Updated setup flow, docs, and tests for the split.

## 2.1.0 - 2026-08-02

- `ide_gateway` sandbox mode now boots with one command via `sandbox_bootstrap.py`: captures sandbox-local LLM endpoint/key, starts `sandbox_server.py`, and launches the public tunnel.
- Replaced sandbox SSH reverse tunnel with keyless Tunnellio TCP bridge (`tunnellio bridge --run --watch`); no SSH keys and no embedded cloud API token path.
- IDE connection settings are more stable: `ideg_...` API key persists, public URL is written back to endpoint state, cached ephemeral hostnames are reused while alive, and custom hostnames stay fixed.
- Added stronger sandbox model discovery from env vars and `/models`, preferring real sandbox model names over fallback aliases.
- Updated Russian beginner docs plus README/README.en for the new one-command sandbox flow.
- Tests: `python tests/test_ide_gateway_bridge.py` → 12 tests OK.

## 2.0.1 - 2026-07-29

- Fixed `provision_sandbox_domain()`: API возвращает `data.key.id` (а не `data.id`), `id` — число.
- Приведение `key_id` и `domain_id` к строке для сохранения в config.
- Проверено live: provision → ephemeral domain → `check_domain_status` → `ensure_domain` — всё работает.
- 276 тестов OK.

## 2.0.0 - 2026-07-29

- **sandbox — режим по умолчанию**. `gateway_mode` в DEFAULT_CONFIG и `plugin_setup.py` теперь `sandbox`.
- `ensure_domain()`: для persistent пересоздаёт с тем же hostname; для ephemeral — новая session.
- Tunnellio-настройки сохраняются в config (`tunnellio_hostname`, `tunnellio_custom_hostname`).
- Все Tunnellio-поля проходят через `normalize_config`.
- `startup()` хук проверяет/пересоздаёт домен при старте MCP.
- Три режима полностью работают: sandbox (Tunnellio tunnel + LLM egress), bridge (poll-loop), external (direct provider).
- 276 тестов OK.

## 1.9.5 - 2026-07-29

- `startup()` хук: при sandbox mode проверяет жив ли Tunnellio домен (`check_domain_status`), пересоздаёт если истёк (`ensure_domain`).
- `check_domain_status()` в `backend.py`: проверяет статус домена через Tunnellio API.
- `ensure_domain()` в `backend.py`: если домен истёк/удалён — автоматически провижинит новый и обновляет config.
- 275 тестов OK.

## 1.9.4 - 2026-07-29

- Domain provisioning at plugin setup: `provision_sandbox_domain()` in `backend.py` creates Tunnellio domain (ephemeral or persistent) via API at SETUP time.
- Default Tunnellio token (free, 1-day ephemeral domains) is built-in; users with paid plans can use their own token for persistent/custom domains.
- `plugin_setup.py`: sandbox mode creates domain automatically — asks for own token or uses default, ephemeral or custom hostname.
- `sandbox_tunnel.py`: simplified — reads domain/key/SSH config from state file, just launches SSH reverse tunnel.
- `start_endpoint`: saves all Tunnellio config (domain_id, key_id, public_url, ssh_host/port/user, private_key) into endpoint state.
- `bridge_prompt`: sandbox prompt includes the provisioned public URL from state.
- 275 tests pass.

## 1.9.3 - 2026-07-29

- Added `sandbox_tunnel.py`: Tunnellio reverse SSH tunnel for sandbox environments — registers SSH key, creates ephemeral/persistent domain via API, launches SSH reverse tunnel, returns public HTTPS URL.
- Updated `bridge_prompt` for sandbox: two-step launch (sandbox_server.py + sandbox_tunnel.py).
- Sandbox mode now fully functional: sandbox_server + Tunnellio tunnel = public URL for IDE.

## 1.9.2 - 2026-07-29

- Sandbox reverse proxy: worker на Windows проксирует к sandbox_server.

## 1.9.1 - 2026-07-29

- `start_endpoint` теперь автоматически запускает `sandbox_server.py` для режимов sandbox/external и `worker.py` для bridge.
- `sandbox_server.py` принимает `--state` (читает настройки из endpoint state-файла).
- `bridge_prompt` упрощён: короткие промты на русском — «подними мост» вместо длинных английских инструкций.
- 275 тестов OK.

## 1.9.0 - 2026-07-29

- Added universal backend layer (`backend.py`): model discovery from env vars, OpenAI ⇄ Anthropic translation, streaming passthrough.
- Added `sandbox_server.py` — standalone server for sandbox environments: serves IDE requests directly through the LLM egress (no queue, no poll-loop, no MCP tool-call blocking).
- Added three gateway modes: `bridge` (model in chat, poll loop), `sandbox` (resident egress server), `external` (direct OpenAI-compatible provider).
- Updated `worker.py` with external mode: calls upstream directly, skips the queue entirely.
- Updated `bridge_prompt` to return mode-specific instructions (bridge/sandbox/external).
- Updated `plugin_setup.py` with mode selection in setup wizard.
- Anthropic translation (`openai_to_anthropic`, `anthropic_to_openai`) handles Claude-family models automatically.
- Dynamic model discovery reads `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`ANTHROPIC_BASE_URL` from the environment.
- 275 tests pass.

## 1.8.7 - 2026-07-28

- Updated `bridge_step.py` with prompt classification: `poll` now returns `prompt_type` ("chat" | "memory_extraction" | "other"), `user_message` (extracted last user message), and `prompt_tail` (last 3000 chars for context).
- Changed `poll` default timeout from 30s to 3s to avoid 502 proxy timeout on Notion Agent's `run_program` layer.
- Added `classify_prompt()` function that extracts the last real user message from flattened prompts or messages arrays and detects PromptQL memory-extraction requests.
- Updated `ide_gateway_bridge_prompt` to return a routing instruction with `prompt_type` branches: chat → answer the question; memory_extraction → noop; other → empty complete.
- 275 tests pass (4 new classify_prompt tests added).

## 1.8.6 - 2026-07-28

- Added `plugins/ide_gateway/bridge_step.py` — a CLI helper that lets the active model serve IDE requests via `run_program` (poll/complete/fail/status), working with the queue files directly instead of long-lived MCP tool-calls that timeout.
- Updated `ide_gateway_bridge_prompt` to return a `run_program`-based loop instruction using `bridge_step.py` instead of `ide_gateway_wait_request` as a blocking MCP tool-call.
- This fixes the core issue: Notion Agent cannot hold a single MCP tool-call open for long-poll, but it can run `bridge_step.py poll` repeatedly in short iterations.
- Updated README.md, README.en.md, and docs/ru/ide-gateway.html with the `run_program` + `bridge_step.py` bridge architecture.
- 271 tests pass; `bridge_step.py` verified end-to-end (poll finds request, complete delivers answer to IDE).

## 1.8.5 - 2026-07-28

- Removed the `ide_provider` plugin entirely; `ide_gateway` is now the sole IDE bridge.
- Removed the subprocess responder (`responder.py`) and its four tools (`ide_gateway_responder_start/stop/status/logs`); the canonical IDE<->model bridge is a long-poll served by the model itself through `ide_gateway_wait_request`, not an external upstream.
- Added `ide_gateway_bridge_prompt` tool that returns a copy-paste system-prompt snippet turning the active MCP model into a persistent long-poll bridge serving IDE requests without chat noise.
- Cleaned `ide_gateway` config: removed the entire responder config section (`responder_enabled`, `responder_autostart`, `responder_upstream_*`, `responder_request_timeout_seconds`, `responder_poll_interval_seconds`, `responder_max_concurrent_requests`).
- Cleaned `plugin_setup.py`: removed `collect_ide_provider_config`, `generate_ide_provider_api_key`, `ide_gateway_default_config`, and `collect_enable_config`; `ENABLE.bat` now goes through `collect_config`.
- Updated README.md, README.en.md, and all docs/ru pages to document the long-poll bridge architecture and remove `ide_provider` references.
- Added `docs/ru/ide-gateway.html` beginner guide.
- Updated VERSION to 1.8.5 (CRLF byte-exact) and launcher version constant.
- Added `tests/test_ide_gateway_bridge.py` with 5 end-to-end long-poll bridge tests (chat non-stream, chat stream, setup defaults, bridge_prompt, no key leak).
- Removed 6 `ide_provider` test files and `test_ide_gateway_responder.py`; 271 tests pass.

## 1.8.4 - 2026-07-28

- Corrected `ide_gateway` setup semantics: the standard gateway is a transport bridge to the active PromptQL/Notion agent, not a proxy to a local Ollama/OpenAI upstream.
- Removed external upstream URL/API key/model questions from normal `ide_gateway` SETUP; the displayed model is only the IDE-facing alias (`ide-gateway`), while the real model is selected in PromptQL chat/project settings.
- `ENABLE.bat` and default SETUP now configure `responder_upstream_type=promptql_bridge`, with responder autostart disabled so local code does not claim requests without the PromptQL agent.
- `ide_gateway_responder_start` refuses to claim requests in `promptql_bridge`/manual mode and explains that queued requests must be handled by the active PromptQL bridge until callback automation is implemented.
- Left external OpenAI-compatible responder internals as non-default experimental plumbing only; they are not part of the normal user setup path.
- 1.8.4 supersedes the 1.8.3 external-upstream wording: normal `ide_gateway` configuration must not point users to Ollama/OpenAI or ask for an upstream model.

## 1.8.3 - 2026-07-28

- Changed plugin-local `ENABLE.bat` semantics: `enable` now applies safe working defaults without interactive questions; `SETUP.bat` remains the interactive path for changing settings.
- Fixed `ide_gateway` defaults so the autonomous responder is not autostarted in `openai_compatible` mode with an empty upstream URL/model.
- `ide_gateway` ENABLE now creates a safe manual-bridge profile: endpoint autostart enabled, local `ideg_...` API key generated, responder disabled/manual until SETUP supplies a real upstream.
- `ide_gateway` SETUP now requires upstream base URL and model when autonomous OpenAI-compatible responder mode is enabled.
- `ide_gateway_responder_start` now fails fast with `not_configured` instead of claiming requests and failing them when upstream settings are missing.

## 1.8.2 - 2026-07-28

- Added an autonomous responder loop to `ide_gateway` so IDE requests are served automatically without manual `ide_gateway_wait_request` / `ide_gateway_send_response` calls.
- Added four new `ide_gateway` tools: `ide_gateway_responder_start`, `ide_gateway_responder_stop`, `ide_gateway_responder_status`, `ide_gateway_responder_logs`.
- The responder runs as a separate subprocess, polls the request queue, forwards requests to a configured OpenAI-compatible upstream (or stays in `manual` mode for the hand-bridge), and completes them with content, `stream_chunks`, or a structured `payload` for tool calls.
- Streaming requests keep the 1.8.1 lifecycle: when the upstream streams, the responder captures token deltas as `stream_chunks` so the worker can emit the full `chat.completion.chunk` / `response.output_text.delta` events followed by `data: [DONE]`.
- Extended `ide_gateway` plugin-local config with a `responder` section (autostart, upstream type/base URL/API key/model, poll interval, max concurrent requests, request timeout).
- Extended `collect_ide_gateway_config` setup wizard with simple choices for responder autostart, backend type, upstream base URL, API key, and model.
- The `ide_gateway` `startup()` hook now autostarts both the endpoint (preferred port 8787) and the responder when the profile is in `full_access`.
- Added 12 regression tests covering responder start/stop/idempotency, `/v1/responses` and `/v1/chat/completions` (stream and non-stream) completion, upstream failure handling, no API key leakage in logs, setup autostart, and responder config validation.
- Updated VERSION to 1.8.2 (CRLF byte-exact) and launcher version constant.

## 1.7.9 - 2026-07-27

## 1.8.1 - 2026-07-27

- Expanded `ide_gateway` `/v1/responses` streaming to emit the full Responses API text lifecycle (`response.in_progress`, output item/content part added/done, output text delta/done, `response.completed`, and final `data: [DONE]`).
- Improves compatibility with IDE clients that create visible chat messages only after seeing output item/content part lifecycle events.

\n## 1.8.0 - 2026-07-27\n\n- Added `ide_gateway`, a full IDE API Gateway plugin exposing OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/models`, files, images, audio, embeddings, and moderation surfaces over the active MCP bridge.\n- Added plugin-local setup support for `ide_gateway`, including BAT wrappers, autostart defaults, and generated local `ideg_...` API keys.\n- Added a plugin startup hook so full-access plugins can run startup initialization after their tools are registered.\n- Fixed completed streaming responses to explicitly terminate with `data: [DONE]`.\n- Added regression coverage for IDE Gateway streaming completion and plugin-local setup.\n\n
- Fixed IDE Provider streaming transport visibility: `stream: true` now opens SSE headers immediately, sends an initial assistant chunk/keepalives while waiting for the active bridge responder, then sends content and `data: [DONE]`.
- Fixed OpenAI-compatible streaming chunk IDs to use the `chatcmpl-...` prefix.
- Added regression coverage for streaming responses before responder completion and streaming timeout SSE behavior.

## 1.7.8 - 2026-07-27

- IDE Provider now accepts `stream: true` OpenAI-compatible chat-completion requests.
- Streaming clients receive `text/event-stream` SSE output with OpenAI-style completion chunks and `[DONE]`; token-by-token streaming is still not implemented, but IDE clients that require streaming mode no longer fail.
- Added worker regression coverage for the streaming transport path.
- Hardened IDE Provider queue writes on Windows with unique temp files and bounded retries around `os.replace()`.

## 1.7.7 - 2026-07-27

- Fixed `ide_provider_start` with no explicit port: MCP tool schema supplies `port: 0` by default, and the plugin now treats `0` as auto-pick from `port_range`.
- Added a regression test covering `port=0` auto-pick so the beginner/default start path keeps working.

## 1.7.6 - 2026-07-27

- Fixed plugin BAT wrappers so `--plugin-dir` is passed as a safe normalized folder path instead of a raw trailing-backslash `%~dp0` value.
- Improved IDE Provider setup with a simple `default` / `custom` endpoint-defaults choice.
- IDE Provider setup now generates and saves a local `idep_...` API key in the plugin-local config during installation, and endpoint start reuses that key by default.
- Added regression coverage for safe BAT wrapper arguments, IDE Provider setup API key generation, and default API key validation.

## 1.7.5 - 2026-07-27

- Added plugin-local setup UX: every bundled plugin now ships `SETUP.bat`, `ENABLE.bat`, `DISABLE.bat`, and `STATUS.bat`.
- Added `plugin_setup.py`, an interactive helper that asks for `current` vs `global` scope, collects plugin-specific configuration, and writes local `plugin.local.*.json` files.
- Updated plugin runtime to load plugin-local configs on startup, so users no longer need to edit workflow profile storage manually.
- Added tests for plugin-local current/global config loading, disable cleanup, and packaged BAT wrappers.
- Updated beginner plugin/IDE Provider docs for the BAT-based workflow and excluded `plugin.local.*.json` from Git/release archives.

## 1.7.4 - 2026-07-27

### IDE Provider

- Added the `ide_provider` active-bridge plugin: a local OpenAI-compatible endpoint that lets IDEs send chat-completion requests to the active MCP model.
- Implemented the request lifecycle through `ide_provider_wait_request`, `ide_provider_send_response`, and `ide_provider_fail_request`, backed by a file-based queue under ignored runtime storage.
- Added local `idep_` token auth, redacted logs, loopback-only binding, duplicate-bind prevention, idempotent already-running start behavior, worker cleanup, and POSIX reap handling.
- Added manifest/config/security/queue/worker regression coverage plus a hardened active-bridge smoke script.
- Updated project README files, Russian beginner docs, and release notes for the IDE Provider workflow.

## 1.7.3 - 2026-07-26

### Documentation

- Expanded and corrected the Russian beginner documentation for Tunnellio.
- Added a standard-layout `docs/ru/tunnellio.html` page with the same header, shell, left navigation, and footer style as the rest of the local documentation.
- Added a collapsible Tunnellio submenu in the left navigation.
- Reworked the content as public client-facing documentation: cabinet URL, public tunnel URL pattern, API documentation link, Local MCP Easy connection steps, Tunnellio CLI usage, OAuth guidance, API token guidance, and troubleshooting.
- Removed internal implementation/repository/deployment details from public Tunnellio documentation.
- Clarified the production service addresses: `https://console.tunnellio.ru`, `https://api.tunnellio.ru/docs`, and `https://*.tunnellio.site`.

## 1.7.2 - 2026-07-26

- Changed launcher connection output from `Bearer token: <token>` to `Authorization=Bearer <token> (Bearer token)` so users see the exact copy-ready authorization form.
- Published the post-1.7.1 cloud/release state with GitHub Actions CI and the Russian beginner documentation site included.
- Bumped canonical version metadata to `1.7.2` and rebuilt the release package after compile, targeted, full unittest, diff hygiene, and source-marker checks.

## 1.7.1 - 2026-07-26

- Hardened workspace write/copy/move/delete paths, git command policy, child-process environment handling, temporary-file cleanup, newline-preserving edits, and bounded process capture.

- Added regression coverage for command allowlists, async filesystem tools, trust-anchor protection, copy/move limits, CRLF preservation, environment sanitization, git hardening, logging hygiene, process capture, temp cleanup, tunnel backend helpers, dependency expectations, and version consistency.

- Added dependency update configuration, issue/PR templates, community files, English quick-start documentation, POSIX helper scripts, and self-hosted sish setup guidance.

- Rebuilt the 1.7.1 release package after compile, targeted regression, full unittest, diff hygiene, and source-marker checks.

## 1.7.0 — 2026-07-26

- Finalized the perimeter merge line by converging configuration, operator docs, and release packaging across embedded OAuth, Tunnellio, Serveo, reverse-proxy, and self-hosted `sish` paths.

- Completed TASK-019: the workflow handoff and task docs now mark the TASK-015 ... TASK-019 sequence done and treat the current tree as the publication-ready baseline.

- Revalidated the full regression surface across launcher, oauth, repo-context, server-smoke, server-profiles, core/process/plugin/workflow suites, covering every test file under `tests/`.

- Rebuilt the aligned 1.7.0 release package and accompanying publication note.

## 1.6.1 — 2026-07-26

- Added a first-class self-hosted `sish` tunnel backend to the launcher, including setup flow, stable public URL derivation, SSH command generation, runtime connection metadata, and reconnect-safe URL resolution.

- Imported and adapted operator documentation for self-hosted relay publishing: `docs/SISH_SETUP.md`, refreshed `README.md`, and expanded `SECURITY.md` guidance for relay ownership and SSH-key handling.

- Marked TASK-018 done in the workflow handoff/task docs and advanced the perimeter plan toward TASK-019.

- Re-ran the launcher/auth/repo-context regression batches and kept the targeted TASK-018 validation green before cutting the release package.

## 1.5.1 — 2026-07-26

- Imported the donor embedded OAuth core into the local product: `auth/` package, embedded provider/store, discovery/protected-resource metadata, consent flow, and `legacy` / `oauth` / `dual` server modes.

- Added operator-facing OAuth launcher flows: `--oauth`, `--register-oauth-client`, owner-code generation/storage/display, and wrapper scripts `OAUTH_SETUP.bat`, `REGISTER_OAUTH_CLIENT.bat`, `oauth_setup.sh`, and `register_oauth_client.sh`.

- Extended launcher connection output so OAuth/dual runs expose discovery metadata and keep both Bearer token and OAuth owner code masked by default in `SHOW_CONNECTION`.

- Hardened transport-sensitive command execution by steering long risky invocations away from synchronous `run_command` and toward background command jobs.

- Added and passed regression coverage for embedded OAuth, launcher OAuth flows, command-job guidance, repo-context gate, and the stage-1 / stage-2 release path.

# Changelog







## 1.4.6 — 2026-07-25

- Updated operator documentation for the production OAuth path through Tunnellio after the upstream server/client rollout.

- Added explicit guidance for `auth_mode = oauth` / `dual`, stable Tunnellio domains, redirect URI registration, scopes, and public-vs-confidential client setup.

- Refreshed the local release bundle to include the updated root-level `tunnellio.exe` together with the existing Python launcher/runtime changes from 1.4.5.

- Added a dedicated OAuth setup guide for Tunnellio-backed MCP publishing and updated the release notes for the bumped package version.

## 1.4.5 — 2026-07-25







- Launcher now detects a working root-level `tunnellio.exe`, recommends Tunnellio managed runtime in setup, and still lets the operator explicitly choose Tunnellio, Serveo temporary domain, or Serveo stable domain.



- Public tunnel recovery for the Tunnellio path now resolves the authoritative public URL from `show-config --name <runtime>` / runtime snapshot data instead of parsing SSH output.



- Runtime metadata now persists the selected tunnel backend, Tunnellio runtime name, executable path, and state directory so stop/reconnect logic can address the correct client instance.



- Background command jobs now capture noisy stdout/stderr through blocking subprocesses plus reader threads so transport-facing status calls stay responsive under heavier load.



- Added regression coverage for background-job responsiveness and completed the full `python -m unittest discover -s tests -q` gate for the 1.4.5 release candidate.



- Added launcher regression coverage for Tunnellio command generation and runtime-snapshot URL resolution while keeping the existing Serveo regressions green.







## 1.4.3 — 2026-07-24







- Added background command jobs for trusted developer mode via `start_command()`, `get_command_status()`, `cancel_command()`, and `list_commands()`.



- Long-running and verbose commands now use retained per-job capture files so transport responses stay bounded without killing successful jobs just because output is large.



- Final background-job results now preserve job status context for cancelled, timed-out, and failed runs instead of collapsing everything into a plain exit-code-only response.



- Added focused regression coverage for command jobs and refreshed process-limit tests so mixed command-enabled and command-disabled suites reload server state cleanly.



- Verified the transport-focused test set for command jobs, process limits, and server smoke coverage before cutting the release.







## 1.4.2 — 2026-07-20







- Added a root-level `connections.cfg` file with documented Russian comments, `MENU = on` by default, and pre-created `PATH[1]`–`PATH[9]` workspace slots.



- Added a startup workspace-selection menu that can switch projects by updating only `workspace` in `config.json`, without regenerating the MCP token or forcing the agent to reconnect.



- First-time setup now saves the chosen workspace both to `config.json` and to the first available slot in `connections.cfg`.



- Added support for saving new workspaces from the startup menu, reusing existing slots, extending beyond slot 9 when needed, and disabling the menu while keeping the last selected workspace as the default.



- Updated launcher messaging and README documentation so users can see where `connections.cfg` and `config.json` live and edit them manually.



- Added launcher regression tests for config bootstrap, workspace switching, saving new paths, extended slot numbers, and menu disable mode.



- Added profile-aware workflow storage in `%LOCALAPPDATA%\\NotionMcpEasy\\workflow-profiles.json` while preserving legacy `config.json` mirroring for backward compatibility.



- Added a universal plugin registry contract with profile-aware effective-mode gating, diagnostics, and global-vs-current attach behavior.



- Added a reusable DB plugin family foundation, refactored внутреннее хранилище onto it, and added PostgreSQL as the second confirmed DB-family implementation.



- Added an OpenAI-compatible AI/subagent plugin family with env-backed secret refs and trusted-only subagent execution.



- Added verified plugin-authoring docs: authoring superprompt, compatibility checklist, package examples, and prompt-to-code traceability matrix.



- Added hardening docs for regression gates, migration safety, resume safety, known limitations, and final task-status tracking.



- Updated launcher messaging, README documentation, release packaging, and regression coverage for the workflow-profile/plugin-system initiative.







## 1.4.1 — 2026-07-19







- Tightened git policy so ordinary mutating git commands such as `reset`, `checkout -B`, `tag`, `config`, and `remote set-url` no longer bypass the repo guard-layer.



- Added an explicit consent-layer for git setup changes: defaults now require confirmation, and changing an existing repo binding requires `confirm_reconfigure`.



- `workspace_info()` now shows a compact root-repo plus nested-repo overview instead of only a single-layer summary.



- Fixed repo-context handling for nested repositories and extended the repo-context regression coverage.



- Added tests for consent-layer flows, nested repo summaries, mutating git command blocking, large-file / long-line safety, and `git -C` target validation.







## 1.4.0 — 2026-07-18







- Added a full git setup-flow for MCP workspaces instead of a guard-only model.



- Added `setup_git_context()` with explicit modes for `bind_existing_repo`, `init_new_repo`, `attach_to_remote`, and `disable_git`.



- Added `inspect_git_repository()` and expanded `repo_context_status()` / `workspace_info()` so agents can see the current state and the next safe action after restart.



- Git commands are now blocked until the user-facing setup choice is completed for the folder, including the explicit “disable git for now” path when `.git` is absent.



- Repo context now stores persisted local policy in `agent-repo-config.local.json`, including configured/disabled state, last detected origin, branch, fork metadata, and explicit commit branch policy.



- MCP now refuses git whenever the detected `remote.origin.url` no longer matches the saved local binding after restart, and blocks commit/push/merge/rebase outside the configured branch target.



- Added tests for repo bootstrap, disable mode, origin mismatch, and URL normalization.



- Release archives are now built into a local `release/` folder inside the project; that folder is excluded from Git and from the archive contents themselves.







## 1.3.5 — 2026-07-18







- Added mandatory local repo context file `agent-repo-config.local.json` for Git work in each workspace.



- Added `configure_repo_context()` so the client must explicitly store `repository_url` and `is_fork` before Git is allowed through MCP.



- Added `repo_context_status()` and extended `workspace_info()` so the saved repo binding is visible after MCP restarts.



- `run_command()` now blocks `git` when repo context is missing, invalid, or mismatched against the detected `remote.origin.url`.



- Added local packaging / ignore rules so repo-context files stay out of Git and release archives.







## 1.3.3 — 2026-07-18







- `server.py` now delivers large outputs in safe chunks instead of sending oversized responses directly to the model.



- `read_file()` now returns chunked output with line ranges, total line count and continuation offsets.



- Chunking now prioritizes a safe character budget while preserving whole lines.



- Added adaptive chunk reflow: if wrapper text makes a chunk exceed the hard output limit, the chunk is rebuilt with a smaller working character budget.



- `run_command()`, `grep_files()` and `list_dir()` now return small results directly and spill large results to temp files for continued reading via `read_file()`.



- Added temp output storage in `temp/` next to `server.py`, outside `BASE_DIR`.



- Added `@temp/...` virtual paths so long temporary outputs can be continued through `read_file()`.



- Temp output files are deleted automatically after the final read.



- Leftover temp output files are cleaned up on server startup.



- `edit_file()` now refuses to edit files that look binary.



- Empty outputs are now normalized to safe non-empty responses.







## 1.3.2 — 2026-07-17







- Atomic writes now use a unique temp file per call (`tempfile.mkstemp`): parallel `write_file`/`edit_file` calls on the same file no longer race on a shared temp name; temp files are cleaned up on failure.



- Tests no longer inherit `MCP_SERVEO_HOSTNAME` from an active MCP session — the suite is reproducible regardless of where it runs.



- Public health polling stops early if the SSH process dies instead of polling to timeout.



- Reconnect no longer claims the stable tunnel is restored when its health check has not passed yet.







## 1.3.1 — 2026-07-17







- Fixed: removed `BatchMode=yes` from the SSH command. Serveo completes auth via keyboard-interactive with an empty challenge even for registered keys (the key only authorizes the reserved hostname), so BatchMode broke both temporary and stable tunnels with `Permission denied`. Verified live.



- Dead server/tunnel processes are no longer "stopped" on shutdown, removing a false "Refusing to stop PID" warning after PID reuse.







## 1.3.0 — 2026-07-17







- SSH tunnel briefly used `BatchMode=yes` — reverted in 1.3.1, see above.



- Startup now polls the public `https://.../health` endpoint and reports success only after the tunnel actually serves traffic.



- Tunnel errors now print the last lines of `tunnel.log` directly in the console.



- `write_file` and `edit_file` write atomically (temp file + replace) to survive crashes and OneDrive sync races.



- Host header check restored in `SecurityMiddleware`: only `localhost`, `127.0.0.1` and `*.serveousercontent.com` are accepted (in stable mode — only the reserved hostname); other hosts get HTTP 403.



- `SHOW_CONNECTION.bat` masks the Bearer token by default; pass `--full` to reveal it.



- Added tests: symlink escape from workspace, Cyrillic paths, atomic write, Host check, `BatchMode`, public health-check, token masking.







## 1.2.2 — 2026-07-17







- Fixed reserved-hostname startup when Serveo keeps SSH output silent.



- Stable mode now derives its known public URL instead of waiting for an announcement.



- Added regression tests for silent and failed stable SSH processes.







## 1.2.1 — 2026-07-17







- Added a standalone full Serveo setup guide for temporary and stable URLs.



- Added step-by-step SSH key, reserved hostname, Notion connection and troubleshooting instructions.



- Added a ready-to-use prompt for AI-assisted installation.







## 1.2.0 — 2026-07-17







- Added optional stable Serveo mode with a reserved hostname and dedicated SSH key.



- Setup wizard now supports both zero-config temporary URLs and persistent URLs.



- Stable reconnects reuse the same URL and no longer require editing Notion.



- Added live verification and unit tests for the reserved-hostname SSH command.







## 1.1.0 — 2026-07-17







- File-only mode is now the default for new installations.



- Renamed command access to trusted developer mode and documented that it is not a sandbox.



- Added bounded streaming capture for command output; the process tree is stopped at the limit.



- Added authenticated `/health` checks and wrong-token tests.



- Launcher now rejects an occupied port before creating a tunnel.



- Runtime process IDs are checked against expected command lines before stopping them.



- Append operations now enforce the final file-size limit.



- Directory moves are no longer supported by `move_file`.



- Regex search mode is disabled to avoid pathological expressions.



- Reconnect output now explicitly tells the user to update the URL in Notion.



- Added launcher, process-limit, authentication and occupied-port tests.







## 1.0.0 — 2026-07-16







- Initial one-click Windows fork with FastMCP, Serveo launcher, workspace file tools and optional developer commands.



