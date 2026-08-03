"""MySQL / MariaDB plugin.

Same shape as the PostgreSQL plugin: connection aliases come from local plugin
config, passwords are read from environment variables and never appear on a
command line, and read-only tools are separated from mutating ones by the
plugin mode.

Results are capped before they reach the transport. A database is the easiest
way to push megabytes through a tunnel by accident, so every result set is
trimmed and the caller is told exactly what was withheld.
"""

from __future__ import annotations

from typing import Any

from plugins.db_shared import (
    cap_rows,
    find_connection,
    health_payload,
    normalize_connections,
    parse_params_json,
    parse_tsv,
    resolve_row_limit,
    run_mysql_sql,
    shell_program_exists,
    sql_literal,
)

# Statements that must never run through the read-only query tool, even when the
# plugin is loaded in full_access mode. The execute tool exists for those.
WRITE_PREFIXES = (
    "insert",
    "update",
    "delete",
    "replace",
    "truncate",
    "drop",
    "alter",
    "create",
    "grant",
    "revoke",
    "rename",
    "load",
    "call",
    "set",
    "lock",
    "unlock",
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_connections(
        config,
        required_fields=("name", "database"),
        optional_fields=("host", "port", "user", "password_env", "ssl_mode"),
        plugin_label="MySQL",
    )


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    payload = health_payload(
        provider="mysql",
        context=context,
        config=config,
        env_secret_fields=("password_env",),
    )
    payload["mysqlAvailable"] = shell_program_exists("mysql")
    return payload


def _reject_writes(sql: str) -> None:
    head = sql.strip().lstrip("(").strip().lower()
    for prefix in WRITE_PREFIXES:
        if head.startswith(prefix):
            raise ValueError(
                f"mysql_query is read-only and refuses '{prefix.upper()}'. "
                "Use mysql_execute for statements that change data."
            )
    if ";" in sql.strip().rstrip(";"):
        raise ValueError(
            "mysql_query runs a single statement. Remove the extra ';' or use mysql_execute."
        )


def _ensure_no_params(arguments: dict[str, Any]) -> None:
    if parse_params_json(arguments):
        raise ValueError(
            "MySQL plugin does not support params_json yet; interpolate values safely before calling"
        )


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)
    if tool_name == "mysql_list_connections":
        return {"connections": [item["name"] for item in normalized["connections"]]}

    alias = str(arguments.get("connection", "")).strip()
    if not alias:
        raise ValueError("connection is required")
    record = find_connection(normalized, alias, plugin_label="MySQL")
    limit = resolve_row_limit(arguments)

    if tool_name == "mysql_list_tables":
        sql = (
            "SELECT table_name AS name, table_type AS type, table_rows AS approx_rows "
            "FROM information_schema.tables "
            f"WHERE table_schema = {sql_literal(str(record['database']))} "
            "ORDER BY table_name"
        )
        rows = parse_tsv(run_mysql_sql(record, sql))
        return {"connection": alias, "database": record["database"], **cap_rows(rows, limit)}

    if tool_name == "mysql_describe_table":
        table = str(arguments.get("table", "")).strip()
        if not table:
            raise ValueError("table is required")
        schema = record["database"]
        if "." in table:
            schema, table = (part.strip() for part in table.split(".", 1))
        sql = (
            "SELECT column_name, column_type, is_nullable, column_key, column_default, extra "
            "FROM information_schema.columns "
            f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
            "ORDER BY ordinal_position"
        )
        rows = parse_tsv(run_mysql_sql(record, sql))
        if not rows:
            raise ValueError(f"Table not found: {schema}.{table}")
        return {
            "connection": alias,
            "table": f"{schema}.{table}",
            "columns": rows,
        }

    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    _ensure_no_params(arguments)

    if tool_name == "mysql_query":
        _reject_writes(sql)
        rows = parse_tsv(run_mysql_sql(record, sql))
        return {"connection": alias, **cap_rows(rows, limit)}

    if tool_name == "mysql_execute":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("mysql_execute requires full_access under a trusted profile")
        output = run_mysql_sql(record, sql)
        return {"connection": alias, "status": output or "OK"}

    raise ValueError(f"Unknown MySQL tool: {tool_name}")
