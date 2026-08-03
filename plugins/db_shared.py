from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core import safe_path


def normalize_connections(
    config: dict[str, Any],
    *,
    required_fields: tuple[str, ...],
    optional_fields: tuple[str, ...] = (),
    plugin_label: str,
) -> dict[str, Any]:
    connections = config.get("connections") if isinstance(config.get("connections"), list) else []
    normalized: list[dict[str, Any]] = []
    seen = set()
    for item in connections:
        if not isinstance(item, dict):
            continue
        record: dict[str, Any] = {}
        for field in required_fields:
            value = str(item.get(field, "")).strip()
            if not value:
                raise ValueError(
                    f"{plugin_label} plugin requires config.connections entries with fields: {', '.join(required_fields)}"
                )
            record[field] = value
        for field in optional_fields:
            value = item.get(field)
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            record[field] = value
        name = record["name"]
        if name in seen:
            raise ValueError(f"Duplicate {plugin_label} connection alias: {name}")
        seen.add(name)
        normalized.append(record)
    if not normalized:
        raise ValueError(f"{plugin_label} plugin requires config.connections with at least one record")
    return {"connections": normalized}


def connections_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    connections = config.get("connections") if isinstance(config.get("connections"), list) else []
    return [item for item in connections if isinstance(item, dict)]


def find_connection(config: dict[str, Any], alias: str, *, plugin_label: str) -> dict[str, Any]:
    for item in connections_from_config(config):
        if str(item.get("name", "")).strip() == alias:
            return item
    raise ValueError(f"Unknown {plugin_label} connection alias: {alias}")


def workspace_scoped_path(context: dict[str, Any], raw_path: str) -> Path:
    workspace = Path(str(context.get("workspacePath", ""))).resolve()
    return safe_path(workspace, raw_path)


def parse_params_json(arguments: dict[str, Any]) -> list[Any]:
    params_json = str(arguments.get("params_json", "[]") or "[]")
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"params_json is not valid JSON: {exc}") from exc
    if not isinstance(params, list):
        raise ValueError("params_json must decode to a JSON array")
    return params


def sanitize_connections(
    connections: list[dict[str, Any]],
    *,
    secret_fields: tuple[str, ...] = (),
    env_secret_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in connections:
        cleaned: dict[str, Any] = {}
        for key, value in item.items():
            if key in secret_fields:
                cleaned[key] = "<redacted>"
            elif key in env_secret_fields:
                cleaned[key] = value
                cleaned[f"{key}Present"] = bool(os.environ.get(str(value), ""))
            else:
                cleaned[key] = value
        sanitized.append(cleaned)
    return sanitized


def health_payload(
    *,
    provider: str,
    context: dict[str, Any],
    config: dict[str, Any],
    secret_fields: tuple[str, ...] = (),
    env_secret_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "provider": provider,
        "workspace": str(context.get("workspacePath", "")),
        "connections": sanitize_connections(
            connections_from_config(config),
            secret_fields=secret_fields,
            env_secret_fields=env_secret_fields,
        ),
    }


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def shell_program_exists(program: str) -> bool:
    return bool(shutil.which(program))


def require_program(program: str, *, label: str) -> str:
    resolved = shutil.which(program)
    if not resolved:
        raise ValueError(f"{label} CLI is not installed or not on PATH: {program}")
    return resolved


def postgres_env(
    record: dict[str, Any],
    *,
    read_only: bool = False,
    statement_timeout_ms: int | None = None,
) -> dict[str, str]:
    """Environment for psql, including session-level safety options.

    Read-only is enforced through ``default_transaction_read_only`` rather than
    a BEGIN/COMMIT wrapper. Multiple ``-c`` arguments are separate requests, so
    a wrapper is fragile; a session option applies to everything the connection
    does, including writes hidden inside a function or a CTE. Supported by every
    PostgreSQL version this plugin targets.
    """
    env = os.environ.copy()
    password_env = str(record.get("password_env", "")).strip()
    if password_env:
        secret = os.environ.get(password_env, "")
        if not secret:
            raise ValueError(f"Environment variable not set: {password_env}")
        env["PGPASSWORD"] = secret
    sslmode = str(record.get("sslmode", "")).strip()
    if sslmode:
        env["PGSSLMODE"] = sslmode
    # Force UTF-8 data and untranslated messages. On a localised Windows the
    # client otherwise emits OEM-codepage text that arrives as mojibake, which
    # makes a real error unreadable. Verified against PostgreSQL 17.
    env["PGCLIENTENCODING"] = "UTF8"
    env["LC_MESSAGES"] = "C"
    env.setdefault("LANG", "C")
    options: list[str] = []
    if read_only:
        options.append("-c default_transaction_read_only=on")
    if statement_timeout_ms:
        options.append(f"-c statement_timeout={int(statement_timeout_ms)}")
    if options:
        existing = env.get("PGOPTIONS", "").strip()
        env["PGOPTIONS"] = (existing + " " + " ".join(options)).strip()
    return env


DEFAULT_SQL_TIMEOUT = max(5, int(os.environ.get("MCP_DB_TIMEOUT_SECONDS", "") or 60))


def postgres_cli_args(record: dict[str, Any], statements: list[str]) -> list[str]:
    args = [
        require_program("psql", label="PostgreSQL"),
        "-X",
        # Never prompt for a password. Without this, psql blocks on stdin
        # forever when a password is missing, which looks like a hung tool call
        # instead of a configuration error. Found by live-testing against a real
        # PostgreSQL 17 server.
        "-w",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-F",
        "\t",
        "-P",
        "footer=off",
        "--no-psqlrc",
    ]
    host = str(record.get("host", "")).strip()
    if host:
        args.extend(["-h", host])
    port = str(record.get("port", "")).strip()
    if port:
        args.extend(["-p", port])
    user = str(record.get("user", "")).strip()
    if user:
        args.extend(["-U", user])
    args.extend(["-d", str(record["database"])])
    for statement in statements:
        args.extend(["-c", statement])
    return args


def run_postgres_sql(
    record: dict[str, Any],
    sql: str,
    *,
    read_only: bool = False,
    timeout: int | None = None,
    statement_timeout_ms: int | None = None,
) -> str:
    """Run one statement through psql.

    With ``read_only=True`` the *server* refuses writes, so this is a guarantee
    rather than a guess about what the SQL text does. ``statement_timeout_ms``
    is enforced inside PostgreSQL, so a runaway query is cancelled there instead
    of merely being abandoned locally.
    """
    resolved_timeout = timeout or DEFAULT_SQL_TIMEOUT
    if read_only and statement_timeout_ms is None:
        statement_timeout_ms = resolved_timeout * 1000
    try:
        proc = subprocess.run(
            postgres_cli_args(record, [sql]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=postgres_env(
                record, read_only=read_only, statement_timeout_ms=statement_timeout_ms
            ),
            check=False,
            timeout=resolved_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"PostgreSQL query timed out after {resolved_timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "psql command failed").strip()
        lowered = detail.lower()
        if "no password supplied" in lowered or "password authentication failed" in lowered:
            detail += (
                " | Set password_env on this connection to the name of an environment "
                "variable holding the password."
            )
        if "read-only transaction" in lowered:
            detail += (
                " | This tool runs on a read-only connection. "
                "Use postgres_execute for statements that change data."
            )
        if "statement timeout" in lowered or "canceling statement" in lowered:
            detail += " | The database cancelled the query. Narrow it or add a LIMIT."
        raise ValueError(detail)
    return proc.stdout.strip()


def parse_tsv(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = lines[0].split("\t")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        values = line.split("\t")
        rows.append({header: values[index] if index < len(values) else "" for index, header in enumerate(headers)})
    return rows

# --- Result size guards ---------------------------------------------------
# A database plugin is the easiest way to take a tunnel down: one broad SELECT
# can return megabytes. Rows are capped by default and the caller is told what
# was withheld, so paging is an obvious next step instead of a blind retry.
DEFAULT_ROW_LIMIT = max(1, int(os.environ.get("MCP_DB_ROW_LIMIT", "") or 200))
MAX_ROW_LIMIT = max(DEFAULT_ROW_LIMIT, int(os.environ.get("MCP_DB_MAX_ROW_LIMIT", "") or 2000))
MAX_CELL_CHARS = max(80, int(os.environ.get("MCP_DB_MAX_CELL_CHARS", "") or 500))


def resolve_row_limit(arguments: dict[str, Any]) -> int:
    raw = str(arguments.get("row_limit", "") or "").strip()
    if not raw:
        return DEFAULT_ROW_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("row_limit must be an integer") from exc
    if value <= 0:
        raise ValueError("row_limit must be positive")
    return min(value, MAX_ROW_LIMIT)


def cap_rows(rows: list[dict[str, str]], limit: int) -> dict[str, Any]:
    """Trim a result set for transport and report honestly what was cut."""
    trimmed = []
    for row in rows[:limit]:
        cleaned = {}
        for key, value in row.items():
            text = "" if value is None else str(value)
            if len(text) > MAX_CELL_CHARS:
                text = text[:MAX_CELL_CHARS] + f"...[+{len(text) - MAX_CELL_CHARS} chars]"
            cleaned[key] = text
        trimmed.append(cleaned)
    payload: dict[str, Any] = {"rows": trimmed, "rowCount": len(trimmed)}
    if len(rows) > limit:
        payload["truncated"] = True
        payload["totalRowsSeen"] = len(rows)
        payload["hint"] = (
            f"Only the first {limit} rows are shown. Add LIMIT/OFFSET to the query "
            "or raise row_limit if you really need more."
        )
    return payload


def mysql_defaults_file(record: dict[str, Any]) -> tuple[list[str], object]:
    """Pass the password via a temp defaults file, never on the command line.

    Command lines are visible to every process on the machine, so a password in
    argv is a credential leak. MySQL reads --defaults-extra-file first, which
    keeps the secret in a file only this user can read.
    """
    import tempfile

    password_env = str(record.get("password_env", "")).strip()
    if not password_env:
        return [], None
    secret = os.environ.get(password_env, "")
    if not secret:
        raise ValueError(f"Environment variable not set: {password_env}")
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".cnf", delete=False, encoding="utf-8", newline="\n"
    )
    handle.write("[client]\npassword=" + secret + "\n")
    handle.close()
    try:
        os.chmod(handle.name, 0o600)
    except OSError:
        pass
    return [f"--defaults-extra-file={handle.name}"], Path(handle.name)


def mysql_cli_args(
    record: dict[str, Any],
    sql: str,
    defaults_args: list[str],
    *,
    read_only: bool = False,
    timeout_seconds: int | None = None,
) -> list[str]:
    args = [
        require_program("mysql", label="MySQL"),
        *defaults_args,
        "--batch",
        "--raw",
        # MySQL 8 defaults to utf8mb4 server-side; state it on the client too so
        # emoji and 4-byte characters survive the round trip.
        "--default-character-set=utf8mb4",
    ]
    if read_only:
        # A session-level setting beats inspecting the SQL text: it also blocks
        # writes hidden inside a routine. MySQL 5.6+ and all of MySQL 8.
        args.append("--init-command=SET SESSION TRANSACTION READ ONLY")
    if timeout_seconds:
        args.append(f"--connect-timeout={max(2, min(int(timeout_seconds), 60))}")
    host = str(record.get("host", "")).strip()
    if host:
        args.append(f"--host={host}")
    port = str(record.get("port", "")).strip()
    if port:
        args.append(f"--port={port}")
    user = str(record.get("user", "")).strip()
    if user:
        args.append(f"--user={user}")
    ssl_mode = str(record.get("ssl_mode", "")).strip()
    if ssl_mode:
        args.append(f"--ssl-mode={ssl_mode}")
    args.extend(["--database=" + str(record["database"]), "--execute=" + sql])
    return args


def run_mysql_sql(
    record: dict[str, Any],
    sql: str,
    *,
    read_only: bool = False,
    timeout: int | None = None,
) -> str:
    """Run one statement through the mysql client.

    ``read_only=True`` wraps the statement in ``START TRANSACTION READ ONLY``,
    which makes the server reject writes. That is stronger than inspecting the
    SQL text, and it also covers writes hidden inside routines.
    """
    resolved_timeout = timeout or DEFAULT_SQL_TIMEOUT
    defaults_args, temp_path = mysql_defaults_file(record)
    try:
        proc = subprocess.run(
            mysql_cli_args(
                record, sql, defaults_args, read_only=read_only, timeout_seconds=resolved_timeout
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=resolved_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"MySQL query timed out after {resolved_timeout}s") from exc
    finally:
        if temp_path is not None:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "mysql command failed").strip()
        lowered = detail.lower()
        if "read only" in lowered or "read-only" in lowered:
            detail += (
                " | This tool runs inside a read-only transaction. "
                "Use mysql_execute for statements that change data."
            )
        if "caching_sha2_password" in lowered:
            detail += (
                " | MySQL 8 defaults to caching_sha2_password, which needs either a "
                "TLS connection or a client that supports it. Set ssl_mode=REQUIRED "
                "on this connection."
            )
        raise ValueError(detail)
    return proc.stdout.strip()


def server_version(record: dict[str, Any], *, provider: str) -> dict[str, Any]:
    """Read the server version so compatibility can be verified, not assumed."""
    try:
        if provider == "postgres":
            raw = run_postgres_sql(record, "SHOW server_version", read_only=True, timeout=15)
        else:
            raw = run_mysql_sql(record, "SELECT VERSION()", read_only=True, timeout=15)
    except ValueError as exc:
        return {"reachable": False, "detail": str(exc)[:300]}
    lines = [line for line in raw.splitlines() if line.strip()]
    version = lines[-1].strip() if lines else ""
    major = 0
    for part in version.replace("-", ".").split("."):
        if part.isdigit():
            major = int(part)
            break
    minimum = 15 if provider == "postgres" else 8
    return {
        "reachable": True,
        "version": version,
        "majorVersion": major,
        "meetsTarget": major >= minimum if major else None,
        "targetMinimum": minimum,
    }
