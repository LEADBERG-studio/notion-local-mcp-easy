# External AI and Subagent Plugin Model

## Status
Primary implementation artifact for TASK-011.

## Implemented provider family
Implemented plugin:
- `plugins/openai_compat/plugin.json`
- `plugins/openai_compat/plugin.py`
- shared helper layer: `plugins/ai_shared.py`

This plugin is intentionally provider-family oriented rather than vendor-hardcoded. It targets OpenAI-compatible chat-completions endpoints through per-provider config.

## Provider role vs subagent role
### Provider role
Read-only tool family:
- `openai_compat_list_models`
- `openai_compat_describe_provider`
- `openai_compat_generate_text`

Purpose:
- route model selection through the active profile config;
- allow global/current provider attachment;
- expose diagnostics without leaking secrets.

### Subagent-compatible role
Trusted/full-access tool family:
- `openai_compat_run_subagent`

Purpose:
- run delegated prompts only when the active profile/runtime allows `full_access`;
- carry explicit profile/effective-mode context into the delegated system prompt;
- prevent subagent invocation from bypassing profile rules.

## Secret handling model
- Provider config stores only secret references, currently `api_key_env`.
- The actual bearer token is loaded from the host environment at runtime.
- Diagnostics and health output expose only:
  - the env variable name;
  - whether a value is present.
- Secret values are never persisted into tracked repo files and are not returned by diagnostics.

## Attach scope behavior
- Global attach stores provider config in `globalPlugins[pluginId]`.
- Current-area attach stores provider config in `profiles[profileId].plugins[pluginId]`.
- Current-area provider config overrides global config key-by-key when both exist.
- Global-only provider attach does not promote the area from `DEFAULT` to `CUSTOM`.
- Area-specific provider config promotes the area to `CUSTOM`.

## Safety behavior
- `openai_compat_generate_text` is `read_only` and remains available under file-only profiles.
- `openai_compat_run_subagent` is `full_access` and is only registered when:
  - server trusted mode is enabled;
  - active profile access mode is `trusted`;
  - requested mode remains `full_access` after loader gating.
- Runtime subagent invocation also checks `effectiveMode` defensively before sending the request.

## Routing model
Config example shape:
```json
{
  "providers": [
    {
      "name": "main",
      "base_url": "https://example.test/v1",
      "api_key_env": "OPENAI_API_KEY",
      "default_model": "demo-model",
      "subagent_model": "demo-subagent"
    }
  ],
  "default_provider": "main",
  "default_model": "demo-model",
  "subagent_defaults": {
    "temperature": 0.2,
    "max_output_tokens": 800
  }
}
```

## Verification evidence
- `tests/test_ai_plugin_foundation.py`

Covered proofs:
- provider tools register under file-only mode while subagent tools do not;
- subagent tool registration appears only in trusted/full-access mode;
- bearer token is read from env and not leaked into diagnostics;
- global vs current provider scope merge is explicit and test-backed.

## Known current limits
- The provider path assumes an OpenAI-compatible `/chat/completions` endpoint.
- Model discovery is config-driven today; no live remote models endpoint is required.
- Subagent execution is prompt-based and intentionally constrained by the same profile/effective-mode boundaries as the loader.
