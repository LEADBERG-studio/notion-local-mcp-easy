from __future__ import annotations

from typing import Any

from plugins.db_shared import (
    find_connection,
    health_payload,
    normalize_connections,
    parse_params_json,
    parse_tsv,
    run_postgres_sql,
    shell_program_exists,
    sql_literal,
)


def validate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return normalize_connections(
        config,
        required_fields=("name", "database"),
        optional_fields=("host", "port", "user", "password_env", "sslmode"),
        plugin_label="PostgreSQL",
    )


def healthcheck(context: dict[str, Any]) -> dict[str, Any]:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    payload = health_payload(
        provider="postgres",
        context=context,
        config=config,
        env_secret_fields=("password_env",),
    )
    payload["psqlAvailable"] = shell_program_exists("psql")
    return payload


def _split_table_name(value: str) -> tuple[str, str]:
    table = value.strip()
    if not table:
        raise ValueError("table is required")
    if "." in table:
        schema, name = table.split(".", 1)
        return schema.strip() or "public", name.strip()
    return "public", table


def _ensure_no_params(arguments: dict[str, Any]) -> None:
    params = parse_params_json(arguments)
    if params:
        raise ValueError(
            "PostgreSQL plugin does not support params_json yet; interpolate values safely before calling"
        )


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)
    if tool_name == "postgres_list_connections":
        return {"connections": [item["name"] for item in normalized["connections"]]}

    alias = str(arguments.get("connection", "")).strip()
    if not alias:
        raise ValueError("connection is required")
    record = find_connection(normalized, alias, plugin_label="PostgreSQL")

    if tool_name == "postgres_list_tables":
        sql = (
            "SELECT table_schema || '.' || table_name AS name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY 1"
        )
        return {"connection": alias, "tables": parse_tsv(run_postgres_sql(record, sql))}

    if tool_name == "postgres_describe_table":
        raw_table = str(arguments.get("table", ""))
        schema, table = _split_table_name(raw_table)
        sql = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
            "ORDER BY ordinal_position"
        )
        return {
            "connection": alias,
            "table": f"{schema}.{table}",
            "columns": parse_tsv(run_postgres_sql(record, sql)),
        }

    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    _ensure_no_params(arguments)
    if tool_name == "postgres_query":
        return {"connection": alias, "rows": parse_tsv(run_postgres_sql(record, sql))}
    if tool_name == "postgres_execute":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("postgres_execute requires full_access under a trusted profile")
        output = run_postgres_sql(record, sql)
        return {"connection": alias, "status": output or "OK"}
    raise ValueError(f"Unknown PostgreSQL tool: {tool_name}")
