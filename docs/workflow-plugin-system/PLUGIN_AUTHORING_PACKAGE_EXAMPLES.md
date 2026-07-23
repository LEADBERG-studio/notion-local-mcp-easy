# Plugin Authoring Package Examples

## Status
Example package shapes for TASK-012.

## Example 1 — DB plugin family package
```text
plugins/
  mysql/
    plugin.json
    plugin.py
tests/
  test_mysql_plugin_foundation.py
docs/workflow-plugin-system/
  MYSQL_PLUGIN_MODEL.md
```

### Expected manifest traits
- `id: "mysql"`
- `install_scope_support: "both"`
- DB tool family:
  - `mysql_list_connections`
  - `mysql_list_tables`
  - `mysql_describe_table`
  - `mysql_query`
  - `mysql_execute`

### Expected config shape
```json
{
  "connections": [
    {
      "name": "primary",
      "host": "db.internal",
      "port": "3306",
      "database": "app",
      "user": "app_user",
      "password_env": "MYSQL_APP_PASSWORD"
    }
  ]
}
```

### Global attach example
```json
{
  "globalPlugins": {
    "mysql": {
      "scope": "global",
      "requestedMode": "read_only",
      "config": {
        "connections": [
          {
            "name": "primary",
            "host": "db.internal",
            "port": "3306",
            "database": "app",
            "user": "app_user",
            "password_env": "MYSQL_APP_PASSWORD"
          }
        ]
      }
    }
  }
}
```

### Current-area attach example
```json
{
  "profiles": {
    "profile-1": {
      "plugins": {
        "mysql": {
          "scope": "current",
          "requestedMode": "full_access",
          "config": {
            "connections": [
              {
                "name": "primary",
                "host": "db.internal",
                "port": "3306",
                "database": "workspace_override",
                "user": "workspace_user",
                "password_env": "MYSQL_WORKSPACE_PASSWORD"
              }
            ]
          }
        }
      }
    }
  }
}
```

## Example 2 — AI/subagent plugin family package
```text
plugins/
  anthropic_compat/
    plugin.json
    plugin.py
  ai_shared.py
tests/
  test_anthropic_compat_plugin.py
docs/workflow-plugin-system/
  ANTHROPIC_COMPAT_PLUGIN_MODEL.md
```

### Expected manifest traits
- `id: "anthropic_compat"`
- `install_scope_support: "both"`
- AI/subagent tool family:
  - `anthropic_compat_list_models`
  - `anthropic_compat_describe_provider`
  - `anthropic_compat_generate_text`
  - `anthropic_compat_run_subagent`

### Expected config shape
```json
{
  "providers": [
    {
      "name": "main",
      "base_url": "https://api.vendor.test/v1",
      "api_key_env": "ANTHROPIC_API_KEY",
      "default_model": "claude-demo",
      "subagent_model": "claude-subagent"
    }
  ],
  "default_provider": "main",
  "default_model": "claude-demo",
  "subagent_defaults": {
    "temperature": 0.2,
    "max_output_tokens": 800
  }
}
```

### Global attach example
```json
{
  "globalPlugins": {
    "anthropic_compat": {
      "scope": "global",
      "requestedMode": "read_only",
      "config": {
        "providers": [
          {
            "name": "main",
            "base_url": "https://api.vendor.test/v1",
            "api_key_env": "ANTHROPIC_API_KEY",
            "default_model": "claude-demo"
          }
        ],
        "default_provider": "main",
        "default_model": "claude-demo"
      }
    }
  }
}
```

### Current-area attach example
```json
{
  "profiles": {
    "profile-1": {
      "plugins": {
        "anthropic_compat": {
          "scope": "current",
          "requestedMode": "full_access",
          "config": {
            "providers": [
              {
                "name": "main",
                "base_url": "https://api.vendor.test/v1",
                "api_key_env": "WORKSPACE_ANTHROPIC_API_KEY",
                "default_model": "claude-workspace",
                "subagent_model": "claude-workspace-subagent"
              }
            ],
            "default_provider": "main",
            "default_model": "claude-workspace"
          }
        }
      }
    }
  }
}
```

## Compatibility rule
The package is only compatible if its manifest, runtime functions, tests, and docs line up with the existing registry contract and current loader behavior.
