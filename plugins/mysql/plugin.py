"""MySQL / MariaDB plugin. Targets MySQL 8 and newer, works back to 5.7.

Design rules:

* **Read-only is enforced by the server.** Read tools connect with
  ``SET SESSION TRANSACTION READ ONLY``, so MySQL refuses a write even if it
  hides inside a routine. A text prefix check runs as well, purely so the caller
  gets a clear message instead of a driver error.
* **Every query is bounded.** Result sets are capped before they reach the
  transport, because one broad ``SELECT`` is the easiest way to take a tunnel
  down, and long cells are trimmed.
* **Passwords never appear in a command line.** ``argv`` is readable by every
  process on the machine, so the secret goes into a private defaults file that
  is deleted immediately after the call.
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
    server_version,
    shell_program_exists,
    sql_literal,
)

# Refused by the read-only tool so the caller gets a readable explanation before
# the server would reject it anyway.
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
    payload["targetVersion"] = "MySQL 8+"
    return payload


def _quoted(identifier: str) -> str:
    """Backtick-quote an identifier so reserved words and case survive."""
    return "`" + str(identifier).replace("`", "``") + "`"


def _split_table_name(value: str, default_schema: str) -> tuple[str, str]:
    table = str(value or "").strip().strip("`")
    if not table:
        raise ValueError("table is required")
    if "." in table:
        schema, name = table.split(".", 1)
        return schema.strip().strip("`") or default_schema, name.strip().strip("`")
    return default_schema, table


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
            "MySQL plugin does not support params_json yet; interpolate values safely "
            "before calling"
        )


def _read(record: dict[str, Any], sql: str, *, timeout: int | None = None) -> list[dict[str, str]]:
    return parse_tsv(run_mysql_sql(record, sql, read_only=True, timeout=timeout))


def invoke(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    config = context.get("pluginConfig") if isinstance(context.get("pluginConfig"), dict) else {}
    normalized = validate_config(config, context)

    if tool_name == "mysql_list_connections":
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
    record = find_connection(normalized, alias, plugin_label="MySQL")
    schema_default = str(record["database"])

    if tool_name == "mysql_server_info":
        info = server_version(record, provider="mysql")
        if info.get("reachable"):
            extras = _read(
                record,
                "SELECT DATABASE() AS `database`, CURRENT_USER() AS `user`, "
                "@@character_set_database AS charset, @@collation_database AS collation, "
                "@@transaction_isolation AS isolation",
                timeout=20,
            )
            if extras:
                info.update(extras[0])
        return {"connection": alias, **info}

    if tool_name == "mysql_list_schemas":
        rows = _read(
            record,
            "SELECT schema_name AS `schema`, default_character_set_name AS charset "
            "FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('mysql', 'information_schema', "
            "'performance_schema', 'sys') ORDER BY 1",
        )
        return {"connection": alias, **cap_rows(rows, resolve_row_limit(arguments))}

    if tool_name == "mysql_list_tables":
        schema = str(arguments.get("schema", "")).strip() or schema_default
        rows = _read(
            record,
            "SELECT table_name AS name, table_type AS type, engine, "
            "table_rows AS approx_rows, "
            "ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb "
            "FROM information_schema.tables "
            f"WHERE table_schema = {sql_literal(schema)} ORDER BY table_name",
        )
        return {
            "connection": alias,
            "schema": schema,
            "note": "approx_rows is an InnoDB estimate; use mysql_count_rows for an exact count",
            **cap_rows(rows, resolve_row_limit(arguments)),
        }

    if tool_name == "mysql_describe_table":
        schema, table = _split_table_name(str(arguments.get("table", "")), schema_default)
        columns = _read(
            record,
            "SELECT column_name, column_type, is_nullable, column_key, column_default, extra "
            "FROM information_schema.columns "
            f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
            "ORDER BY ordinal_position",
        )
        if not columns:
            raise ValueError(f"Table not found: {schema}.{table}")
        indexes = _read(
            record,
            "SELECT index_name AS name, column_name, non_unique, index_type "
            "FROM information_schema.statistics "
            f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
            "ORDER BY index_name, seq_in_index",
        )
        foreign_keys = _read(
            record,
            "SELECT constraint_name AS name, column_name, "
            "referenced_table_name AS references_table, "
            "referenced_column_name AS references_column "
            "FROM information_schema.key_column_usage "
            f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
            "AND referenced_table_name IS NOT NULL ORDER BY 1",
        )
        return {
            "connection": alias,
            "table": f"{schema}.{table}",
            "columns": columns,
            "indexes": indexes,
            "foreignKeys": foreign_keys,
        }

    if tool_name == "mysql_count_rows":
        schema, table = _split_table_name(str(arguments.get("table", "")), schema_default)
        where = str(arguments.get("where", "")).strip()
        clause = f" WHERE {where}" if where else ""
        rows = _read(
            record,
            f"SELECT COUNT(*) AS `rows` FROM {_quoted(schema)}.{_quoted(table)}{clause}",
        )
        return {
            "connection": alias,
            "table": f"{schema}.{table}",
            "rows": rows[0]["rows"] if rows else "0",
        }

    if tool_name == "mysql_sample_rows":
        schema, table = _split_table_name(str(arguments.get("table", "")), schema_default)
        limit = resolve_row_limit(arguments)
        rows = _read(
            record,
            f"SELECT * FROM {_quoted(schema)}.{_quoted(table)} LIMIT {int(limit)}",
        )
        return {"connection": alias, "table": f"{schema}.{table}", **cap_rows(rows, limit)}

    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    _ensure_no_params(arguments)

    if tool_name == "mysql_query":
        _reject_writes(sql)
        limit = resolve_row_limit(arguments)
        rows = _read(record, sql)
        return {"connection": alias, **cap_rows(rows, limit)}

    if tool_name == "mysql_explain":
        _reject_writes(sql)
        rows = _read(record, f"EXPLAIN FORMAT=JSON {sql}")
        raw = run_mysql_sql(record, f"EXPLAIN {sql}", read_only=True)
        return {
            "connection": alias,
            "plan": parse_tsv(raw),
            "json": [row for row in rows] if rows else [],
        }

    if tool_name == "mysql_execute":
        if context.get("effectiveMode") != "full_access":
            raise ValueError("mysql_execute requires full_access under a trusted profile")
        output = run_mysql_sql(record, sql)
        return {"connection": alias, "status": output or "OK"}

    raise ValueError(f"Unknown MySQL tool: {tool_name}")
