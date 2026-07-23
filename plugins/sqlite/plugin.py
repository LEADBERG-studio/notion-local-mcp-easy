from __future__ import annotations

import sqlite3
from typing import Any

from plugins.db_shared import (
    find_connection,
    health_payload,
    normalize_connections,
    parse_params_json,
    workspace_scoped_path,
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_connections(
        config,
        required_fields=("name", "path"),
        plugin_label="SQLite",
    )


def _resolve_connection(config: dict[str, Any], context: dict[str, Any], alias: str):
    record = find_connection(config, alias, plugin_label="SQLite")
    return workspace_scoped_path(context, str(record["path"]))


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    return health_payload(provider="sqlite", context=context, config=config)


def _query_rows(db_path, sql: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(sql, params)
        rows = cursor.fetchmany(max(1, min(limit, 500)))
        return [dict(row) for row in rows]


def _execute(db_path, sql: str, params: list[Any]) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(sql, params)
        connection.commit()
        return {"rowcount": cursor.rowcount, "lastrowid": cursor.lastrowid}


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)
    if tool_name == "sqlite_list_connections":
        return {"connections": [item["name"] for item in normalized["connections"]]}

    alias = str(arguments.get("connection", "")).strip()
    if not alias:
        raise ValueError("connection is required")
    db_path = _resolve_connection(normalized, context, alias)
    if tool_name == "sqlite_list_tables":
        rows = _query_rows(
            db_path,
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name",
            [],
            500,
        )
        return {"connection": alias, "tables": rows}
    if tool_name == "sqlite_describe_table":
        table = str(arguments.get("table", "")).strip()
        if not table:
            raise ValueError("table is required")
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(f"PRAGMA table_info({table!r})").fetchall()
            return {"connection": alias, "table": table, "columns": [dict(row) for row in rows]}
    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    params = parse_params_json(arguments)
    if tool_name == "sqlite_query":
        limit = int(arguments.get("limit", 200) or 200)
        return {"connection": alias, "rows": _query_rows(db_path, sql, params, limit)}
    if tool_name == "sqlite_execute":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("sqlite_execute requires full_access under a trusted profile")
        return {"connection": alias, **_execute(db_path, sql, params)}
    raise ValueError(f"Unknown SQLite tool: {tool_name}")
