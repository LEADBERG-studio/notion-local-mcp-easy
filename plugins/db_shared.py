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


def postgres_env(record: dict[str, Any]) -> dict[str, str]:
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
    return env


def postgres_cli_args(record: dict[str, Any], sql: str) -> list[str]:
    args = [
        require_program("psql", label="PostgreSQL"),
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-F",
        "\t",
        "-P",
        "footer=off",
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
    args.extend(["-d", str(record["database"]), "-c", sql])
    return args


def run_postgres_sql(record: dict[str, Any], sql: str) -> str:
    proc = subprocess.run(
        postgres_cli_args(record, sql),
        capture_output=True,
        text=True,
        env=postgres_env(record),
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "psql command failed").strip()
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
