"""PostgreSQL plugin. Targets PostgreSQL 15 and newer, works back to 9.6.

Design rules:

* **Read-only is enforced by the server, not guessed from the SQL text.** Every
  read tool connects with ``default_transaction_read_only=on``, so a write is
  refused by PostgreSQL even if it hides inside a function or a CTE.
* **Every query is bounded.** ``statement_timeout`` is set inside the database
  and result sets are capped before they reach the transport, because one broad
  ``SELECT`` is the easiest way to take a tunnel down.
* **Passwords never appear in a command line.** They travel through the
  environment as ``PGPASSWORD``, referenced by variable name in config.
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
    run_postgres_sql,
    server_version,
    shell_program_exists,
    sql_literal,
)

SYSTEM_SCHEMAS = "('pg_catalog', 'information_schema', 'pg_toast')"


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
    payload["targetVersion"] = "PostgreSQL 15+"
    return payload


def _split_table_name(value: str) -> tuple[str, str]:
    table = str(value or "").strip().strip('"')
    if not table:
        raise ValueError("table is required")
    if "." in table:
        schema, name = table.split(".", 1)
        return schema.strip().strip('"') or "public", name.strip().strip('"')
    return "public", table


def _qualified(schema: str, table: str) -> str:
    """Quote an identifier so mixed case and reserved words survive."""
    return '"{}"."{}"'.format(schema.replace('"', '""'), table.replace('"', '""'))


def _ensure_no_params(arguments: dict[str, Any]) -> None:
    if parse_params_json(arguments):
        raise ValueError(
            "PostgreSQL plugin does not support params_json yet; interpolate values "
            "safely before calling"
        )


def _read(record: dict[str, Any], sql: str, *, timeout: int | None = None) -> list[dict[str, str]]:
    return parse_tsv(run_postgres_sql(record, sql, read_only=True, timeout=timeout))


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)

    if tool_name == "postgres_list_connections":
        return {
            "connections": [
                {
                    "name": item["name"],
                    "database": item["database"],
                    "host": item.get("host", "(local)"),
                }
                for item in normalized["connections"]
            ]
        }

    alias = str(arguments.get("connection", "")).strip()
    if not alias:
        raise ValueError("connection is required")
    record = find_connection(normalized, alias, plugin_label="PostgreSQL")

    if tool_name == "postgres_server_info":
        info = server_version(record, provider="postgres")
        if info.get("reachable"):
            extras = _read(
                record,
                "SELECT current_database() AS database, current_user AS user, "
                "pg_size_pretty(pg_database_size(current_database())) AS size",
                timeout=20,
            )
            if extras:
                info.update(extras[0])
        return {"connection": alias, **info}

    if tool_name == "postgres_list_schemas":
        rows = _read(
            record,
            "SELECT nspname AS schema, pg_catalog.pg_get_userbyid(nspowner) AS owner "
            f"FROM pg_catalog.pg_namespace WHERE nspname NOT IN {SYSTEM_SCHEMAS} "
            "AND nspname NOT LIKE 'pg_temp%' AND nspname NOT LIKE 'pg_toast%' "
            "ORDER BY 1",
        )
        return {"connection": alias, **cap_rows(rows, resolve_row_limit(arguments))}

    if tool_name == "postgres_list_tables":
        schema = str(arguments.get("schema", "")).strip()
        where = (
            f"WHERE table_schema = {sql_literal(schema)}"
            if schema
            else f"WHERE table_schema NOT IN {SYSTEM_SCHEMAS}"
        )
        rows = _read(
            record,
            "SELECT table_schema || '.' || table_name AS name, table_type AS type, "
            "pg_size_pretty(pg_total_relation_size("
            "quote_ident(table_schema) || '.' || quote_ident(table_name))) AS size "
            f"FROM information_schema.tables {where} ORDER BY 1",
        )
        return {"connection": alias, **cap_rows(rows, resolve_row_limit(arguments))}

    if tool_name == "postgres_describe_table":
        schema, table = _split_table_name(str(arguments.get("table", "")))
        columns = _read(
            record,
            "SELECT column_name, data_type, is_nullable, column_default, "
            "character_maximum_length AS max_length "
            "FROM information_schema.columns "
            f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
            "ORDER BY ordinal_position",
        )
        if not columns:
            raise ValueError(f"Table not found: {schema}.{table}")
        indexes = _read(
            record,
            "SELECT indexname AS name, indexdef AS definition FROM pg_indexes "
            f"WHERE schemaname = {sql_literal(schema)} AND tablename = {sql_literal(table)} "
            "ORDER BY 1",
        )
        constraints = _read(
            record,
            "SELECT conname AS name, pg_get_constraintdef(oid) AS definition "
            "FROM pg_constraint "
            f"WHERE conrelid = {sql_literal(_qualified(schema, table))}::regclass "
            "ORDER BY 1",
        )
        return {
            "connection": alias,
            "table": f"{schema}.{table}",
            "columns": columns,
            "indexes": indexes,
            "constraints": constraints,
        }

    if tool_name == "postgres_count_rows":
        schema, table = _split_table_name(str(arguments.get("table", "")))
        where = str(arguments.get("where", "")).strip()
        clause = f" WHERE {where}" if where else ""
        rows = _read(
            record, f"SELECT count(*) AS rows FROM {_qualified(schema, table)}{clause}"
        )
        return {
            "connection": alias,
            "table": f"{schema}.{table}",
            "rows": rows[0]["rows"] if rows else "0",
        }

    if tool_name == "postgres_sample_rows":
        schema, table = _split_table_name(str(arguments.get("table", "")))
        limit = resolve_row_limit(arguments)
        rows = _read(
            record, f"SELECT * FROM {_qualified(schema, table)} LIMIT {int(limit)}"
        )
        return {"connection": alias, "table": f"{schema}.{table}", **cap_rows(rows, limit)}

    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    _ensure_no_params(arguments)

    if tool_name == "postgres_query":
        limit = resolve_row_limit(arguments)
        rows = _read(record, sql)
        return {"connection": alias, **cap_rows(rows, limit)}

    if tool_name == "postgres_explain":
        analyze = str(arguments.get("analyze", "")).strip().lower() in {"1", "true", "yes"}
        if analyze and context.get("effectiveMode") != "full_access":
            raise ValueError(
                "EXPLAIN ANALYZE actually runs the query, so it requires full_access. "
                "Plain EXPLAIN works in read-only mode."
            )
        prefix = "EXPLAIN (ANALYZE, BUFFERS, VERBOSE)" if analyze else "EXPLAIN (VERBOSE)"
        output = run_postgres_sql(record, f"{prefix} {sql}", read_only=not analyze)
        return {"connection": alias, "plan": output.splitlines()}

    if tool_name == "postgres_execute":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("postgres_execute requires full_access under a trusted profile")
        output = run_postgres_sql(record, sql)
        return {"connection": alias, "status": output or "OK"}

    raise ValueError(f"Unknown PostgreSQL tool: {tool_name}")
