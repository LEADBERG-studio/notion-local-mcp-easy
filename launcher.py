from __future__ import annotations



import argparse

import contextlib

import json
import logging

import os

import queue

import re

import secrets

import shutil

import socket

import subprocess

import sys

import threading

import time

import urllib.error

import urllib.request

from datetime import datetime
from logging.handlers import RotatingFileHandler

from pathlib import Path

from typing import TextIO
from urllib.parse import urlsplit



from core import DEFAULT_ALLOWED_COMMANDS


def configure_stdio_for_unicode() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


configure_stdio_for_unicode()
from profiles import (

    access_mode_from_allow_commands,

    apply_profile_to_legacy_config,

    load_profiles,

    mark_active_profile,

    save_profiles,

    sync_profiles_with_slots,

)



APP_NAME = "NotionMcpEasy"

VERSION = "2.2.0"

SCRIPT_DIR = Path(__file__).resolve().parent

CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME

CONFIG_FILE = CONFIG_DIR / "config.json"

RUNTIME_FILE = CONFIG_DIR / "runtime.json"

CONNECTION_FILE = CONFIG_DIR / "connection.txt"

CONNECTIONS_FILE = SCRIPT_DIR / "connections.cfg"

SERVER_LOG = CONFIG_DIR / "server.log"

TUNNEL_LOG = CONFIG_DIR / "tunnel.log"

URL_PATTERN = re.compile(r"https://[a-zA-Z0-9.-]+\.serveousercontent\.com")

PATH_SLOT_PATTERN = re.compile(r"PATH\[(\d+)\]$", re.IGNORECASE)

DEFAULT_CONNECTION_SLOTS = 9

AUTH_MODES = {"legacy", "oauth", "dual"}

AUTH_MODE_OPTIONS = ("legacy", "oauth", "dual")

AUTH_MODE_DESCRIPTIONS = {

    "legacy": "static Bearer token only (Notion Custom MCP and similar clients)",

    "oauth": "OAuth 2.1 only (Hyperagent and other OAuth MCP clients)",

    "dual": "Bearer token and OAuth on the same /mcp endpoint",

}

TUNNEL_BACKENDS = {"serveo", "tunnellio", "custom_proxy", "sish"}

DEFAULT_TUNNELLIO_CONNECTION_MODE = "cloud_proxy"

DEFAULT_TUNNELLIO_OAUTH_CLIENT_POLICY = "shared"
DEFAULT_SISH_SSH_PORT = 2222





def normalize_auth_mode(value: object) -> str:

    mode = str(value or "legacy").strip().lower()

    return mode if mode in AUTH_MODES else "legacy"


def config_auth_mode(config: dict) -> str:
    return normalize_auth_mode(config.get("auth_mode", "legacy"))





def normalize_tunnel_backend(value: object) -> str:

    backend = str(value or "serveo").strip().lower()

    return backend if backend in TUNNEL_BACKENDS else "serveo"






def default_tunnel_backend(existing: dict | None = None) -> str:
    existing = existing or {}
    raw = existing.get("tunnel_backend")
    if raw:
        return normalize_tunnel_backend(raw)
    if str(existing.get("tunnel_host", "")).strip() or str(existing.get("tunnel_domain", "")).strip():
        return "sish"
    if str(existing.get("public_url", "")).strip():
        return "custom_proxy"
    if existing.get("serveo_hostname") or existing.get("ssh_key"):
        return "serveo"
    return "tunnellio" if (SCRIPT_DIR / "tunnellio.exe").is_file() else "serveo"


def tunnel_backend(config: dict) -> str:
    return normalize_tunnel_backend(config.get("tunnel_backend", "serveo"))


def config_tunnel_backend(config: dict) -> str:
    raw = str(config.get("tunnel_backend", "serveo")).strip().lower()
    if raw == "custom-ssh":
        return "custom-ssh"
    return tunnel_backend(config)


def config_uses_serveo(config: dict) -> bool:
    backend = config_tunnel_backend(config)
    if backend in {"custom-ssh", "custom_proxy"}:
        return False
    return not custom_public_url(config)


def tunnel_process_match(config: dict) -> str:
    backend = config_tunnel_backend(config)
    if backend == "tunnellio":
        return "tunnellio.exe"
    if backend == "sish":
        return sish_tunnel_match(config)
    if backend in {"custom-ssh", "custom_proxy"}:
        return "custom_proxy"
    return "serveo.net"


def custom_public_url(config: dict) -> str:
    return str(config.get("public_url", "")).strip().rstrip("/")


def reverse_proxy_enabled(config: dict) -> bool:
    raw = str(config.get("tunnel_backend", "")).strip()
    if raw:
        return normalize_tunnel_backend(raw) == "custom_proxy"
    return bool(custom_public_url(config))


def validate_public_base_url(value: str) -> str:
    candidate = str(value).strip().rstrip("/")
    if not candidate:
        raise ValueError("Public URL is required for reverse proxy mode.")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Use a full http:// or https:// public URL.")
    if not parsed.netloc:
        raise ValueError("Public URL must include a hostname.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Use only the base origin without path, query, or fragment.")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def prompt_public_url(existing: dict) -> str:
    current = custom_public_url(existing)
    while True:
        prompt = (
            f"Public base URL (for example https://mcp.example.com) [{current}]"
            if current
            else "Public base URL (for example https://mcp.example.com)"
        )
        raw = prompt_input(f"{prompt}: ").strip()
        try:
            return validate_public_base_url(raw or current)
        except ValueError as exc:
            print(exc)



def slugify_runtime_name(value: str) -> str:

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")

    return slug[:48] or "notion-local-mcp"





def tunnellio_executable_path(config: dict) -> Path:

    raw = str(config.get("tunnellio_path", "")).strip()

    candidate = Path(raw).expanduser() if raw else (SCRIPT_DIR / "tunnellio.exe")

    return candidate.resolve()





def tunnellio_state_dir(config: dict) -> Path:

    raw = str(config.get("tunnellio_state_dir", "")).strip()

    candidate = Path(raw).expanduser() if raw else (CONFIG_DIR / "tunnellio-state")

    return candidate.resolve()





def tunnellio_runtime_name(config: dict) -> str:

    raw = str(config.get("tunnellio_runtime_name", "")).strip()

    if raw:

        return slugify_runtime_name(raw)

    workspace_name = Path(str(config.get("workspace") or "notion-local-mcp")).name

    return slugify_runtime_name(f"{workspace_name}-mcp")






def prompt_input(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, StopIteration):
        return ""


def default_tunnel_mode(existing: dict | None = None) -> str:
    existing = existing or {}
    preferred = str(existing.get("tunnel_mode_preference", "")).strip().lower()
    if preferred in {"tunnellio", "serveo_temporary", "serveo_stable", "reverse_proxy", "sish"}:
        return preferred
    if reverse_proxy_enabled(existing):
        return "reverse_proxy"
    backend = default_tunnel_backend(existing)
    if backend == "tunnellio":
        return "tunnellio"
    if backend == "sish":
        return "sish"
    return "serveo_stable" if existing.get("serveo_hostname") else "serveo_temporary"


def prompt_tunnel_mode(existing: dict) -> str:
    tunnellio_available = tunnellio_executable_path(existing).is_file()
    default_mode = default_tunnel_mode(existing)
    default_choice = {
        "tunnellio": "1",
        "serveo_temporary": "2",
        "serveo_stable": "3",
        "reverse_proxy": "4",
        "sish": "5",
    }[default_mode]
    aliases = {
        "1": "tunnellio",
        "t": "tunnellio",
        "tunnellio": "tunnellio",
        "2": "serveo_temporary",
        "temp": "serveo_temporary",
        "temporary": "serveo_temporary",
        "serveo": "serveo_temporary",
        "3": "serveo_stable",
        "stable": "serveo_stable",
        "reserved": "serveo_stable",
        "4": "reverse_proxy",
        "reverse": "reverse_proxy",
        "proxy": "reverse_proxy",
        "reverse_proxy": "reverse_proxy",
        "custom": "reverse_proxy",
        "5": "sish",
        "s": "sish",
        "sish": "sish",
    }
    print("\nTunnel mode:")
    if tunnellio_available:
        print(" 1. Tunnellio managed runtime (recommended)")
    else:
        print(" 1. Tunnellio managed runtime (unavailable: tunnellio.exe not found)")
    print(" 2. Serveo temporary domain")
    print(" 3. Serveo stable domain (reserved hostname + SSH key)")
    print(" 4. Custom public URL / reverse proxy (no built-in tunnel)")
    print(" 5. Self-hosted sish relay (SSH reverse tunnel)")
    while True:
        raw = prompt_input(f"Choose tunnel mode [{default_choice}]: ").strip().lower()
        selected = default_mode if not raw else aliases.get(raw)
        if selected is None:
            print("Enter 1, 2, 3, 4, or 5.")
            continue
        if selected == "tunnellio" and not tunnellio_available:
            print("Tunnellio is not available yet. Put tunnellio.exe next to launcher.py or choose a Serveo mode.")
            continue
        return selected


def config_public_url(config: dict) -> str:
    custom = custom_public_url(config)
    if custom:
        return custom
    hostname = str(config.get("serveo_hostname", "")).strip().lower()
    backend = tunnel_backend(config)
    if backend == "sish":
        domain = str(config.get("tunnel_domain", "")).strip().lower().strip(".")
        if hostname and domain:
            return f"https://{hostname}.{domain}"
        return ""
    if backend == "custom_proxy":
        return ""
    if hostname:
        return f"https://{hostname}.serveousercontent.com"
    return ""


def sish_tunnel_match(config: dict) -> str:
    return str(config.get("tunnel_host", "")).strip() or "ssh"


def load_json(path: Path) -> dict:

    try:

        return json.loads(path.read_text(encoding="utf-8"))

    except (FileNotFoundError, json.JSONDecodeError):

        return {}





def save_json(path: Path, value: dict) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    temp.replace(path)





def generate_legacy_token() -> str:
    return "bridge-secret-token-" + secrets.token_urlsafe(18)

def _workspace_from_connections_cfg() -> str:
    try:
        connections = load_connections_cfg()
        paths = dict(connections.get("paths") or {})
        for _, saved_path in sorted(paths.items()):
            candidate = normalize_workspace_path(saved_path)
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass
    return str(SCRIPT_DIR)

def heal_legacy_config(config: dict, *, persist: bool = True) -> dict:
    if not isinstance(config, dict):
        config = {}
    changed = False
    if not str(config.get("workspace", "")).strip():
        config["workspace"] = _workspace_from_connections_cfg()
        changed = True
    if not str(config.get("token", "")).strip():
        config["token"] = generate_legacy_token()
        changed = True
    auth_mode = normalize_auth_mode(config.get("auth_mode", "legacy"))
    if config.get("auth_mode") != auth_mode:
        config["auth_mode"] = auth_mode
        changed = True
    if not str(config.get("tunnel_backend", "")).strip():
        config["tunnel_backend"] = default_tunnel_backend(config)
        changed = True
    if changed and persist:
        save_json(CONFIG_FILE, config)
        print(f"Config recovered missing legacy fields in: {CONFIG_FILE}")
    return config

def tunnel_log_suggests_remote_port_busy() -> bool:
    tail = tunnel_log_tail().lower()
    return ("remote port forwarding failed" in tail or "forwarding failed" in tail or "listen port 80" in tail)

def stop_previous_tunnel_runtime(config: dict) -> None:
    runtime = load_json(RUNTIME_FILE)
    pid = int(runtime.get("tunnel_pid", 0) or 0)
    match = str(runtime.get("tunnel_match", tunnel_process_match(config)))
    if pid and pid_matches(pid, match):
        with contextlib.suppress(Exception):
            stop_pid(pid, match)
    if tunnel_backend(config) == "tunnellio":
        runtime_name = str(runtime.get("tunnellio_runtime_name") or tunnellio_runtime_name(config)).strip()
        if runtime_name:
            with contextlib.suppress(Exception):
                request_tunnellio_stop(config, runtime_name, force=True)

def start_and_resolve_tunnel(config: dict, *, attempts: int = 4) -> tuple[subprocess.Popen, queue.Queue[str], str]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            delay = min(30, 3 * attempt)
            print(f"Tunnel start retry {attempt}/{attempts} in {delay}s...")
            time.sleep(delay)
        stop_previous_tunnel_runtime(config)
        tunnel, lines = start_tunnel(config)
        try:
            url = resolve_tunnel_url(config, tunnel, lines)
            return tunnel, lines, url
        except Exception as exc:
            last_error = exc
            with contextlib.suppress(Exception):
                stop_pid(tunnel.pid, tunnel_process_match(config))
            if tunnel_log_suggests_remote_port_busy():
                print("Tunnel remote port is still busy on the relay; cleaning up and retrying.")
                continue
            if attempt >= attempts:
                raise
    assert last_error is not None
    raise last_error


def yes_no(prompt: str, default: bool) -> bool:

    marker = "Y/n" if default else "y/N"

    while True:

        answer = input(f"{prompt} [{marker}]: ").strip().lower()

        if not answer:

            return default

        if answer in {"y", "yes", "д", "да"}:

            return True

        if answer in {"n", "no", "н", "нет"}:

            return False

        print("Please answer yes or no.")





def normalize_workspace_path(value: str | Path) -> Path:

    return Path(value).expanduser().resolve()





def connections_cfg_template(menu_on: bool, paths: dict[int, str]) -> str:

    slots = sorted(set(range(1, DEFAULT_CONNECTION_SLOTS + 1)) | set(paths))

    lines = [

        "# connections.cfg — сохранённые рабочие области для Notion Local MCP Easy",

        "#",

        "# MENU = on  -> при запуске показывать меню выбора рабочей области.",

        f"# MENU = off -> запускать сервер сразу с областью из {CONFIG_FILE}.",

        "#",

        "# PATH[n] — сохранённые варианты пути к рабочей области.",

        "# Этот файл можно редактировать вручную в любом текстовом редакторе.",

        "# Примеры:",

        r"# PATH[1] = C:\Users\you\Documents\project-one",

        r"# PATH[2] = D:\Work\project-two",

        "#",

        f"MENU = {'on' if menu_on else 'off'}",

        "",

    ]

    for slot in slots:

        lines.append(f"PATH[{slot}] = {paths.get(slot, '')}")

    return "\n".join(lines) + "\n"





def ensure_connections_cfg_exists() -> None:

    if CONNECTIONS_FILE.exists():

        return

    CONNECTIONS_FILE.write_text(connections_cfg_template(True, {}), encoding="utf-8")





def load_connections_cfg() -> dict[str, object]:

    ensure_connections_cfg_exists()

    menu_on = True

    paths: dict[int, str] = {}

    text = CONNECTIONS_FILE.read_text(encoding="utf-8", errors="replace")

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:

            continue

        key, value = (part.strip() for part in line.split("=", 1))

        if key.upper() == "MENU":

            menu_on = value.lower() != "off"

            continue

        match = PATH_SLOT_PATTERN.fullmatch(key)

        if match and value:

            paths[int(match.group(1))] = value

    return {"menu_on": menu_on, "paths": paths}





def save_connections_cfg(menu_on: bool, paths: dict[int, str]) -> None:

    ensure_connections_cfg_exists()

    CONNECTIONS_FILE.write_text(

        connections_cfg_template(menu_on, paths), encoding="utf-8"

    )





def first_free_connection_slot(paths: dict[int, str]) -> int | None:

    for slot in range(1, DEFAULT_CONNECTION_SLOTS + 1):

        if slot not in paths or not str(paths[slot]).strip():

            return slot

    return None





def find_connection_slot(paths: dict[int, str], workspace: Path) -> int | None:

    target = os.path.normcase(str(workspace))

    for slot, saved in sorted(paths.items()):

        try:

            candidate = os.path.normcase(str(normalize_workspace_path(saved)))

        except OSError:

            continue

        if candidate == target:

            return slot

    return None





def prompt_workspace_folder(prompt: str, default_workspace: Path | None = None) -> Path:

    while True:

        suffix = f" [{default_workspace}]" if default_workspace is not None else ""

        raw = input(f"{prompt}{suffix}: ").strip().strip('"')

        workspace = (

            normalize_workspace_path(raw)

            if raw

            else default_workspace.resolve() if default_workspace is not None else None

        )

        if workspace is None:

            print("Введите путь к существующей папке.")

            continue

        if workspace.is_dir():

            return workspace

        print(f"Folder does not exist: {workspace}")





def remember_workspace_path(

    workspace: Path, *, preferred_slot: int | None = None

) -> tuple[int, bool]:

    connections = load_connections_cfg()

    paths = dict(connections["paths"])

    existing_slot = find_connection_slot(paths, workspace)

    if existing_slot is not None:

        return existing_slot, False

    slot = (

        preferred_slot

        if preferred_slot is not None and preferred_slot not in paths

        else first_free_connection_slot(paths)

    )

    if slot is None:

        slot = max(paths, default=0) + 1

    paths[slot] = str(workspace)

    save_connections_cfg(bool(connections["menu_on"]), paths)

    return slot, True





def bootstrap_workspace_in_connections(config: dict) -> tuple[int | None, bool]:

    workspace_raw = str(config.get("workspace", "")).strip()

    if not workspace_raw:

        return None, False

    try:

        workspace = normalize_workspace_path(workspace_raw)

    except OSError:

        return None, False

    return remember_workspace_path(workspace, preferred_slot=1)





def prompt_access_mode(default_access_mode: str) -> str:

    trusted_default = default_access_mode == "trusted"

    allow_commands = yes_no(

        "Enable trusted developer mode for this workspace?",

        trusted_default,

    )

    return access_mode_from_allow_commands(allow_commands)





def workflow_profiles_file() -> Path:

    return CONFIG_FILE.with_name("workflow-profiles.json")





def sync_workflow_profiles(

    config: dict, *, created_from: str = "sync"

) -> tuple[dict, dict | None]:

    connections = load_connections_cfg()

    storage = load_profiles(workflow_profiles_file())

    updated, active_profile, changed = sync_profiles_with_slots(

        storage,

        dict(connections["paths"]),

        legacy_allow_commands=bool(config.get("allow_commands", False)),

        active_workspace=str(config.get("workspace", "")),

        created_from=created_from,

    )

    if changed:

        save_profiles(updated, workflow_profiles_file())

    return updated, active_profile





def profile_for_slot(storage: dict, slot: int) -> dict | None:

    profiles = storage.get("profiles") if isinstance(storage.get("profiles"), dict) else {}

    for profile in profiles.values():

        if int(profile.get("pathSlot", 0) or 0) == int(slot):

            return profile

    return None





def activate_profile_config(storage: dict, profile: dict, config: dict) -> tuple[dict, dict]:

    updated_storage = mark_active_profile(storage, str(profile["profileId"]))

    save_profiles(updated_storage, workflow_profiles_file())

    updated_config = apply_profile_to_legacy_config(config, profile)

    save_json(CONFIG_FILE, updated_config)

    return updated_storage, updated_config





def choose_workspace_from_connections(config: dict) -> dict:

    slot, added = bootstrap_workspace_in_connections(config)

    if added and slot is not None:

        print(

            f"Текущая рабочая область добавлена в {CONNECTIONS_FILE} (слот {slot})."

        )

    connections = load_connections_cfg()

    paths = dict(connections["paths"])

    storage, active_profile = sync_workflow_profiles(config, created_from="menu_sync")

    if not bool(connections["menu_on"]):

        if active_profile is not None:

            _, config = activate_profile_config(storage, active_profile, config)

        # Ensure ide_gateway_api_key exists in global config
        config = ensure_ide_gateway_key(config)

        return config



    config = heal_legacy_config(config)
    current_workspace = normalize_workspace_path(config["workspace"])

    print("\n=== Меню рабочих областей Notion Local MCP Easy ===")

    print(f"Список путей хранится в: {CONNECTIONS_FILE}")

    print(f"Текущая рабочая область из конфига: {current_workspace}")

    occupied = sorted(paths.items())

    if occupied:

        print("\nСохранённые рабочие области:")

        for slot_number, saved_path in occupied:

            slot_profile = profile_for_slot(storage, slot_number)

            marker = (

                " (текущая)"

                if slot_profile is not None

                and str(slot_profile.get("workspacePath", ""))

                == str(current_workspace)

                else ""

            )

            suffix = "" if Path(saved_path).expanduser().exists() else " [папка не найдена]"

            if slot_profile is None:

                profile_hint = ""

            else:

                profile_hint = (

                    f" [mode={slot_profile.get('accessMode', 'file_only')} | "

                    f"env={slot_profile.get('environmentMode', 'DEFAULT')}]"

                )

            print(f" {slot_number}. {saved_path}{profile_hint}{marker}{suffix}")

    else:

        print("\nСохранённых рабочих областей пока нет.")

    print(" 0. Задать новую рабочую область")

    print(

        f" q. Выключить меню и запускать сервер сразу с последней областью ({current_workspace})"

    )

    print(f"Подсказка: текущая активная область сохраняется в {CONFIG_FILE}")



    while True:

        choice = input("\nВыберите пункт [Enter = оставить текущую область]: ").strip().lower()

        if not choice:

            if current_workspace.is_dir():

                if active_profile is not None:

                    _, config = activate_profile_config(storage, active_profile, config)

                print(

                    f"Оставляем текущую рабочую область без изменений: {current_workspace}.\n"

                    f"При необходимости отредактируйте {CONNECTIONS_FILE} вручную."

                )

                return config

            print("Текущая рабочая область недоступна. Выберите сохранённый слот или задайте новую папку.")

            continue

        if choice == "q":

            save_connections_cfg(False, paths)

            if active_profile is not None:

                _, config = activate_profile_config(storage, active_profile, config)

            print(

                f"Меню отключено в {CONNECTIONS_FILE}.\n"

                f"По умолчанию остаётся рабочая область из {CONFIG_FILE}: {current_workspace}"

            )

            return config

        if not choice.isdigit():

            print("Введите номер сохранённой области, 0 для новой области или q для отключения меню.")

            continue

        selected = int(choice)

        if selected == 0:

            default_workspace = current_workspace if current_workspace.is_dir() else SCRIPT_DIR.parent.parent.resolve()

            workspace = prompt_workspace_folder("Новая рабочая область", default_workspace)

            existing_slot = find_connection_slot(paths, workspace)

            if existing_slot is not None:

                print(

                    f"Эта рабочая область уже сохранена в {CONNECTIONS_FILE} (слот {existing_slot})."

                )

                selected = existing_slot

            else:

                slot_number = first_free_connection_slot(paths)

                replaced = False

                if slot_number is None:

                    while True:

                        raw_slot = input(

                            "Свободных базовых слотов больше нет. Укажите номер для сохранения (можно 10 и выше): "

                        ).strip()

                        if raw_slot.isdigit() and int(raw_slot) > 0:

                            slot_number = int(raw_slot)

                            replaced = slot_number in paths

                            break

                        print("Введите положительный номер слота, например 9 или 10.")

                paths[slot_number] = str(workspace)

                save_connections_cfg(bool(connections["menu_on"]), paths)

                action = "обновлён" if replaced else "сохранён"

                print(

                    f"Новый путь {action} в {CONNECTIONS_FILE} (слот {slot_number})."

                )

                config["workspace"] = str(workspace)

                temp_storage, _ = sync_workflow_profiles(config, created_from="menu_add")

                profile = profile_for_slot(temp_storage, slot_number)

                if profile is None:

                    print("Не удалось создать профиль для новой области.")

                    continue

                default_access_mode = str(

                    (active_profile or {}).get(

                        "accessMode",

                        access_mode_from_allow_commands(bool(config.get("allow_commands", False))),

                    )

                )

                chosen_access_mode = prompt_access_mode(default_access_mode)

                profile["accessMode"] = chosen_access_mode

                profile["updatedAt"] = profile.get("updatedAt") or ""

                temp_storage, config = activate_profile_config(temp_storage, profile, config)

                save_profiles(temp_storage, workflow_profiles_file())

                print(

                    f"Текущая рабочая область обновлена в {CONFIG_FILE}. Сервер продолжит запуск с: {workspace}"

                )

                return config

        if selected not in paths:

            print(f"Слот {selected} пуст. Откройте {CONNECTIONS_FILE} или выберите другой пункт.")

            continue

        workspace = normalize_workspace_path(paths[selected])

        if not workspace.is_dir():

            print(

                f"Сохранённая папка из слота {selected} недоступна: {workspace}.\n"

                f"Исправьте путь в {CONNECTIONS_FILE} или задайте новую область через пункт 0."

            )

            continue

        config["workspace"] = str(workspace)

        storage, _ = sync_workflow_profiles(config, created_from="menu_switch")

        profile = profile_for_slot(storage, selected)

        if profile is None:

            print(f"Не удалось найти профиль для слота {selected}.")

            continue

        storage, config = activate_profile_config(storage, profile, config)

        print(

            f"Выбрана рабочая область из {CONNECTIONS_FILE} (слот {selected}).\n"

            f"Текущий config обновлён: {CONFIG_FILE}"

        )

        return config






def setup(force: bool = False) -> dict:
    ensure_connections_cfg_exists()
    raw_existing = load_json(CONFIG_FILE)
    existing = heal_legacy_config(raw_existing, persist=True) if raw_existing else {}
    if existing and not force:
        return choose_workspace_from_connections(existing)

    print(f"\n=== Notion Local MCP Easy {VERSION}: first-time setup ===\n")
    default_workspace = (
        normalize_workspace_path(existing["workspace"])
        if existing.get("workspace")
        else SCRIPT_DIR.parent.parent.resolve()
    )
    workspace = prompt_workspace_folder("Workspace folder", default_workspace)

    print("\nFile-only mode keeps MCP file operations inside the selected workspace.")
    print("Trusted developer mode adds Python/Git/Node commands with your Windows user rights.")
    print("Those programs can access files and the network outside the workspace.")
    allow_commands = yes_no(
        "Enable trusted developer mode?", bool(existing.get("allow_commands", False))
    )

    selected_tunnel_mode = prompt_tunnel_mode(existing)
    if selected_tunnel_mode == "tunnellio":
        selected_tunnel_backend = "tunnellio"
    elif selected_tunnel_mode == "reverse_proxy":
        selected_tunnel_backend = "custom_proxy"
    elif selected_tunnel_mode == "sish":
        selected_tunnel_backend = "sish"
    else:
        selected_tunnel_backend = "serveo"
    serveo_hostname = ""
    ssh_key = ""
    public_url = ""
    tunnel_host = str(existing.get("tunnel_host", "")).strip()
    tunnel_ssh_port = str(existing.get("tunnel_ssh_port", DEFAULT_SISH_SSH_PORT)).strip() or str(DEFAULT_SISH_SSH_PORT)
    tunnel_domain = str(existing.get("tunnel_domain", "")).strip().lower().strip(".")
    if selected_tunnel_mode == "serveo_stable":
        print("\nA reserved Serveo hostname keeps the same Custom MCP URL after restarts.")
        serveo_hostname = str(existing.get("serveo_hostname", "")).strip().lower()
        ssh_key = str(existing.get("ssh_key", "")).strip()
        while not serveo_hostname:
            current_hostname = serveo_hostname or ""
            prompt = (
                f"Reserved hostname (without domain) [{current_hostname}]"
                if current_hostname
                else "Reserved hostname (without domain)"
            )
            raw_hostname = prompt_input(f"{prompt}: ").strip().lower()
            serveo_hostname = (raw_hostname or serveo_hostname).removesuffix(
                ".serveousercontent.com"
            )
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", serveo_hostname):
                print("Use 3-63 lowercase letters, digits or hyphens.")
                serveo_hostname = ""
        default_key = Path(ssh_key).expanduser().resolve() if ssh_key else (Path.home() / ".ssh" / "serveo_notion_mcp").resolve()
        key_path = default_key
        while True:
            raw_key = prompt_input(f"Serveo private SSH key [{default_key}]: ").strip().strip('"')
            key_path = normalize_workspace_path(raw_key) if raw_key else default_key.resolve()
            if key_path.is_file():
                break
            print(f"Private key not found: {key_path}")
        ssh_key = str(key_path)
    elif selected_tunnel_mode == "serveo_temporary":
        print("\nServeo temporary mode keeps a random public domain. The URL may change after reconnects.")
    elif selected_tunnel_mode == "reverse_proxy":
        print("\nReverse proxy mode keeps the MCP server on 127.0.0.1 and expects your own proxy/domain in front of it.")
        public_url = prompt_public_url(existing)
    elif selected_tunnel_mode == "sish":
        print("\nSelf-hosted sish mode opens an SSH reverse tunnel to your own relay.")
        tunnel_host = str(prompt_input(
            f"sish SSH endpoint host [{tunnel_host}]" if tunnel_host else "sish SSH endpoint host"
        )).strip() or tunnel_host
        if not tunnel_host:
            raise RuntimeError("sish SSH endpoint host is required.")
        while True:
            raw_port = prompt_input(f"sish SSH port [{tunnel_ssh_port}]: ").strip()
            chosen_port = raw_port or tunnel_ssh_port
            if str(chosen_port).isdigit():
                tunnel_ssh_port = str(chosen_port)
                break
            print("Use a numeric TCP port.")
        tunnel_domain = str(prompt_input(
            f"Public wildcard base domain [{tunnel_domain}]" if tunnel_domain else "Public wildcard base domain"
        )).strip().lower().strip(".") or tunnel_domain
        if not tunnel_domain:
            raise RuntimeError("Public wildcard base domain is required for sish mode.")
        while True:
            current_hostname = serveo_hostname or ""
            prompt = (
                f"Reserved subdomain label [{current_hostname}]"
                if current_hostname
                else "Reserved subdomain label"
            )
            raw_hostname = prompt_input(f"{prompt}: ").strip().lower()
            serveo_hostname = (raw_hostname or serveo_hostname).removesuffix("." + tunnel_domain)
            if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", serveo_hostname):
                break
            print("Use 3-63 lowercase letters, digits or hyphens.")
            serveo_hostname = ""
        default_key = Path(ssh_key).expanduser().resolve() if ssh_key else (Path.home() / ".ssh" / "sish_local_mcp").resolve()
        key_path = default_key
        while True:
            raw_key = prompt_input(f"sish private SSH key [{default_key}]: ").strip().strip('"')
            key_path = normalize_workspace_path(raw_key) if raw_key else default_key.resolve()
            if key_path.is_file():
                break
            print(f"Private key not found: {key_path}")
        ssh_key = str(key_path)
    else:
        print("\nTunnellio mode uses the managed runtime and restores public connection details from the runtime snapshot.")

    token = str(existing.get("token", "")).strip() or secrets.token_urlsafe(32)
    config = {
        "version": VERSION,
        "token": token,
        "workspace": str(workspace),
        "port": int(existing.get("port", 8765) or 8765),
        "allow_commands": allow_commands,
        "auth_mode": normalize_auth_mode(existing.get("auth_mode", "legacy")),
        "oauth_owner_code": str(existing.get("oauth_owner_code", "")).strip() or secrets.token_urlsafe(9),
        "public_url": public_url,
        "tunnel_backend": selected_tunnel_backend,
        "tunnel_mode_preference": selected_tunnel_mode,
        "serveo_hostname": serveo_hostname,
        "ssh_key": ssh_key,
        "tunnel_host": tunnel_host,
        "tunnel_ssh_port": tunnel_ssh_port,
        "tunnel_domain": tunnel_domain,
        "tunnellio_path": str(Path(str(existing.get("tunnellio_path") or (SCRIPT_DIR / "tunnellio.exe"))).expanduser()),
        "tunnellio_state_dir": str(Path(str(existing.get("tunnellio_state_dir") or (CONFIG_DIR / "tunnellio-state"))).expanduser()),
        "tunnellio_runtime_name": str(existing.get("tunnellio_runtime_name", "")).strip(),
        "tunnellio_base_url": str(existing.get("tunnellio_base_url", "")).strip(),
        "tunnellio_token": str(existing.get("tunnellio_token", "")).strip(),
        "tunnellio_domain": str(existing.get("tunnellio_domain", "")).strip(),
        "tunnellio_key": str(existing.get("tunnellio_key", "")).strip(),
        "tunnellio_connection_mode": str(existing.get("tunnellio_connection_mode", DEFAULT_TUNNELLIO_CONNECTION_MODE)).strip() or DEFAULT_TUNNELLIO_CONNECTION_MODE,
        "tunnellio_oauth_client_policy": str(existing.get("tunnellio_oauth_client_policy", DEFAULT_TUNNELLIO_OAUTH_CLIENT_POLICY)).strip() or DEFAULT_TUNNELLIO_OAUTH_CLIENT_POLICY,
        "tunnellio_use_discovery": bool(existing.get("tunnellio_use_discovery", True)),
        "tunnellio_enable_pkce": bool(existing.get("tunnellio_enable_pkce", True)),
        "allowed_commands": [
            "git",
            "make",
            "node",
            "npm",
            "npx",
            "pip",
            "py",
            "pytest",
            "python",
            "ruff",
            "uv",
        ],
    }
    save_json(CONFIG_FILE, config)
    saved_slot, added_to_connections = remember_workspace_path(workspace, preferred_slot=1)
    storage, active_profile = sync_workflow_profiles(config, created_from="setup")
    if active_profile is not None:
        _, config = activate_profile_config(storage, active_profile, config)
    print(f"\nConfiguration saved in: {CONFIG_FILE}")
    if added_to_connections:
        print(f"Рабочая область сохранена в {CONNECTIONS_FILE} (слот {saved_slot}).")
    else:
        print(f"Рабочая область уже есть в {CONNECTIONS_FILE} (слот {saved_slot}).")
    auth_mode = normalize_auth_mode(config.get("auth_mode", "legacy"))
    if auth_mode in ("oauth", "dual"):
        print(f"Auth mode: {auth_mode} ({AUTH_MODE_DESCRIPTIONS[auth_mode]})")
        print("OAuth owner code (use it to approve new OAuth clients):")
        print(f"    {config['oauth_owner_code']}")
    print("Access token is stored in the config and reused on later launches.\n")
    # Generate or reuse ide_gateway API key
    gw_key = str(existing.get("ide_gateway_api_key", "")).strip()
    if not gw_key:
        import secrets as _secrets
        gw_key = "ideg_" + _secrets.token_urlsafe(32)
        config["ide_gateway_api_key"] = gw_key
        save_json(CONFIG_FILE, config)
        print(f"IDE Gateway API key generated: {gw_key[:20]}... (stored in config)")
    else:
        config["ide_gateway_api_key"] = gw_key
        print(f"IDE Gateway API key reused from config: {gw_key[:20]}...")
    return config



def pid_exists(pid: int) -> bool:

    if pid <= 0:

        return False

    if os.name == "nt":

        import ctypes



        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)

        if not handle:

            return False

        ctypes.windll.kernel32.CloseHandle(handle)

        return True

    try:

        os.kill(pid, 0)

        return True

    except OSError:

        return False





def process_command_line(pid: int) -> str:

    if not pid_exists(pid):

        return ""

    if os.name == "nt":

        command = (

            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId="

            + str(pid)

            + "\" -ErrorAction SilentlyContinue; if($p){$p.CommandLine}"

        )

        result = subprocess.run(

            ["powershell", "-NoProfile", "-Command", command],

            capture_output=True,

            text=True,

            encoding="utf-8",

            errors="replace",

            timeout=5,

            check=False,

        )

        return result.stdout.strip()

    proc_cmdline = Path(f"/proc/{pid}/cmdline")

    with contextlib.suppress(OSError):

        return proc_cmdline.read_bytes().replace(b"\0", b" ").decode(errors="replace")

    return ""





def pid_matches(pid: int, expected: str) -> bool:

    command_line = process_command_line(pid)

    return bool(command_line) and expected.lower() in command_line.lower()





def stop_pid(pid: int, expected: str) -> bool:

    if not pid_exists(pid):

        return True

    if not pid_matches(pid, expected):

        print(f"Refusing to stop PID {pid}: process identity does not match {expected!r}.")

        return False

    if os.name == "nt":

        subprocess.run(

            ["taskkill", "/T", "/F", "/PID", str(pid)],

            stdout=subprocess.DEVNULL,

            stderr=subprocess.DEVNULL,

            check=False,

        )

    else:

        with contextlib.suppress(OSError):

            os.kill(pid, 15)

    return True





def _tunnellio_command_prefix(config: dict, *, include_remote_auth: bool) -> list[str]:

    executable = tunnellio_executable_path(config)

    if not executable.is_file():

        raise RuntimeError(f"Tunnellio executable not found: {executable}")

    state_dir = tunnellio_state_dir(config)

    state_dir.mkdir(parents=True, exist_ok=True)

    command = [str(executable), "--state-dir", str(state_dir)]

    if include_remote_auth:

        base_url = str(config.get("tunnellio_base_url", "")).strip()

        token = str(config.get("tunnellio_token", "")).strip()

        if base_url:

            command.extend(["--base-url", base_url])

        if token:

            command.extend(["--token", token])

    return command





def run_tunnellio_local_command(

    config: dict, args: list[str], *, timeout: int = 30, include_remote_auth: bool = False

) -> subprocess.CompletedProcess[str]:

    command = _tunnellio_command_prefix(

        config, include_remote_auth=include_remote_auth

    ) + args

    return subprocess.run(

        command,

        capture_output=True,

        text=True,

        encoding="utf-8",

        errors="replace",

        timeout=timeout,

        check=False,

    )





def request_tunnellio_stop(config: dict, runtime_name: str, *, force: bool = True) -> None:

    args = ["stop", "--name", runtime_name, "--grace-seconds", "3"]

    if force:

        args.append("--force")

    result = run_tunnellio_local_command(

        config, args, timeout=30, include_remote_auth=False

    )

    if result.returncode != 0:

        details = (result.stderr or result.stdout or "").strip()

        raise RuntimeError(details or f"tunnellio stop failed with code {result.returncode}")





def stop_all() -> None:

    runtime = load_json(RUNTIME_FILE)

    backend = normalize_tunnel_backend(runtime.get("tunnel_backend", "serveo"))

    if backend == "tunnellio":

        runtime_name = str(runtime.get("tunnellio_runtime_name", "")).strip()

        if runtime_name:

            stop_config = {

                "tunnellio_path": runtime.get("tunnellio_path", ""),

                "tunnellio_state_dir": runtime.get("tunnellio_state_dir", ""),

            }

            with contextlib.suppress(Exception):

                request_tunnellio_stop(stop_config, runtime_name, force=True)

    pairs = (

        (runtime.get("tunnel_pid", 0), runtime.get("tunnel_match", "serveo.net")),

        (runtime.get("server_pid", 0), runtime.get("server_match", "server.py")),

    )

    all_stopped = True

    for raw_pid, expected in pairs:

        with contextlib.suppress(TypeError, ValueError):

            all_stopped = stop_pid(int(raw_pid), str(expected)) and all_stopped

    if all_stopped:

        RUNTIME_FILE.unlink(missing_ok=True)

        print("Notion Local MCP Easy is stopped.")

    else:

        print("One or more PIDs were not stopped because their identity did not match.")





def port_is_open(port: int) -> bool:

    try:

        with socket.create_connection(("127.0.0.1", port), timeout=0.3):

            return True

    except OSError:

        return False





def health_ok(port: int, token: str) -> bool:

    request = urllib.request.Request(

        f"http://127.0.0.1:{port}/health",

        headers={"Authorization": f"Bearer {token}"},

    )

    try:

        with urllib.request.urlopen(request, timeout=1) as response:

            payload = json.loads(response.read())

            return response.status == 200 and payload == {"status": "ok"}

    except (OSError, ValueError, urllib.error.URLError):

        return False





def tunnel_log_tail(limit: int = 15) -> str:

    try:

        lines = TUNNEL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()

    except OSError:

        return ""

    return "\n".join(lines[-limit:])





def tunnel_error(message: str) -> RuntimeError:

    tail = tunnel_log_tail()

    details = f"\n--- last tunnel output ({TUNNEL_LOG}) ---\n{tail}" if tail else f"; see {TUNNEL_LOG}"

    return RuntimeError(message + details)





def public_health_ok(

    url: str,

    token: str,

    attempts: int = 10,

    delay: float = 2.0,

    process: subprocess.Popen | None = None,

) -> bool:

    """Poll the public /health endpoint until the tunnel actually serves traffic."""

    for attempt in range(attempts):

        if process is not None and process.poll() is not None:

            return False

        request = urllib.request.Request(

            url.rstrip("/") + "/health",

            headers={"Authorization": f"Bearer {token}"},

        )

        try:

            with urllib.request.urlopen(request, timeout=5) as response:

                payload = json.loads(response.read())

                if response.status == 200 and payload == {"status": "ok"}:

                    return True

        except (OSError, ValueError, urllib.error.URLError):

            pass

        if attempt < attempts - 1:

            time.sleep(delay)

    return False





def wait_for_server(

    port: int, token: str, process: subprocess.Popen, timeout: float = 25.0

) -> None:

    deadline = time.time() + timeout

    while time.time() < deadline:

        if process.poll() is not None:

            raise RuntimeError(

                f"MCP server exited with code {process.returncode}; see {SERVER_LOG}"

            )

        if health_ok(port, token):

            return

        time.sleep(0.25)

    raise RuntimeError(f"MCP server health check failed on port {port}; see {SERVER_LOG}")





def make_log_writer(path: Path, max_bytes: int = 1_000_000, backups: int = 3) -> logging.Logger:
    """Return a rotating file logger dedicated to a launcher log file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"local_mcp_easy.log.{path.resolve()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def start_server(config: dict) -> tuple[subprocess.Popen, TextIO]:

    port = int(config.get("port", 8765))

    if port_is_open(port):

        raise RuntimeError(

            f"Port {port} is already in use. Stop the other service or change the port in {CONFIG_FILE}."

        )

    profile_storage_path = ""

    profile_id = ""

    profile_access_mode = access_mode_from_allow_commands(bool(config.get("allow_commands", False)))

    storage, active_profile = sync_workflow_profiles(config, created_from="launcher_start")

    if active_profile is not None:

        profile_storage_path = str(workflow_profiles_file())

        profile_id = str(active_profile["profileId"])

        profile_access_mode = str(active_profile.get("accessMode", profile_access_mode))

        config = apply_profile_to_legacy_config(config, active_profile)

        save_json(CONFIG_FILE, config)

    env = os.environ.copy()

    env.update(

        {

            "MCP_TOKEN": config["token"],

            "MCP_BASE_DIR": config["workspace"],

            "MCP_PORT": str(port),

            "MCP_ALLOW_COMMANDS": "1" if config.get("allow_commands", False) else "0",

            "MCP_ALLOWED_COMMANDS": ",".join(config.get("allowed_commands", [])),

            "MCP_AUTH_MODE": normalize_auth_mode(config.get("auth_mode", "legacy")),

            "MCP_OAUTH_OWNER_CODE": str(config.get("oauth_owner_code", "")).strip(),

            "MCP_PUBLIC_URL": config_public_url(config),

            "MCP_SERVEO_HOSTNAME": str(config.get("serveo_hostname", "")).strip().lower(),

            "MCP_PROFILE_STORAGE": profile_storage_path,

            "MCP_PROFILE_ID": profile_id,

            "MCP_PROFILE_ACCESS_MODE": profile_access_mode,

            "PYTHONUNBUFFERED": "1",

        }

    )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    log = SERVER_LOG.open("w", encoding="utf-8")

    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

    process = subprocess.Popen(

        [sys.executable, str(SCRIPT_DIR / "server.py")],

        cwd=str(SCRIPT_DIR),

        env=env,

        stdout=log,

        stderr=subprocess.STDOUT,

        creationflags=flags,

    )

    try:

        wait_for_server(port, config["token"], process)

    except Exception:

        log.close()

        with contextlib.suppress(Exception):

            stop_pid(process.pid, "server.py")

        raise

    return process, log





def build_serveo_tunnel_command(config: dict) -> list[str]:

    if not shutil.which("ssh"):

        raise RuntimeError(

            "OpenSSH client was not found. Install Windows Optional Feature: OpenSSH Client."

        )

    port = int(config.get("port", 8765))

    hostname = str(config.get("serveo_hostname", "")).strip().lower()

    command = [

        "ssh",

        "-T",

        "-o",

        "StrictHostKeyChecking=accept-new",

        "-o",

        "ServerAliveInterval=30",

        "-o",

        "ServerAliveCountMax=3",

        "-o",

        "ExitOnForwardFailure=yes",

    ]

    if hostname:

        key_path = Path(str(config.get("ssh_key", ""))).expanduser().resolve()

        if not key_path.is_file():

            raise RuntimeError(f"Serveo private SSH key not found: {key_path}")

        # No BatchMode here: Serveo finishes auth via keyboard-interactive with

        # an empty challenge even for registered keys (the key only authorizes

        # the reserved hostname). BatchMode disables keyboard-interactive and

        # breaks auth entirely — verified live on 2026-07-17.

        command.extend(["-i", str(key_path), "-o", "IdentitiesOnly=yes"])

        remote = f"{hostname}:80:127.0.0.1:{port}"

    else:

        remote = f"80:127.0.0.1:{port}"

    command.extend(["-R", remote, "serveo.net"])

    return command



def build_sish_tunnel_command(config: dict) -> list[str]:

    if not shutil.which("ssh"):

        raise RuntimeError(

            "OpenSSH client was not found. Install Windows Optional Feature: OpenSSH Client."

        )

    tunnel_host = str(config.get("tunnel_host", "")).strip()

    if not tunnel_host:

        raise RuntimeError("sish tunnel backend requires 'tunnel_host' in the config.")

    key_path = Path(str(config.get("ssh_key", ""))).expanduser().resolve()

    if not key_path.is_file():

        raise RuntimeError(f"sish private SSH key not found: {key_path}")

    port = int(config.get("port", 8765))

    hostname = str(config.get("serveo_hostname", "")).strip().lower()

    command = [

        "ssh",

        "-T",

        "-o",

        "StrictHostKeyChecking=accept-new",

        "-o",

        "ServerAliveInterval=30",

        "-o",

        "ServerAliveCountMax=3",

        "-o",

        "ExitOnForwardFailure=yes",

        "-i",

        str(key_path),

        "-o",

        "IdentitiesOnly=yes",

    ]

    ssh_port = str(config.get("tunnel_ssh_port", "")).strip()

    if ssh_port:

        command.extend(["-p", ssh_port])

    remote = f"{hostname}:80:127.0.0.1:{port}" if hostname else f"80:127.0.0.1:{port}"

    command.extend(["-R", remote, tunnel_host])

    return command


def build_tunnellio_tunnel_command(config: dict) -> list[str]:

    port = int(config.get("port", 8765))

    runtime_name = tunnellio_runtime_name(config)

    command = _tunnellio_command_prefix(config, include_remote_auth=True)

    command.extend(

        [

            "connect",

            "--local-host",

            "127.0.0.1",

            "--local-port",

            str(port),

            "--run",

            "--no-watch",

            "--name",

            runtime_name,

            "--runtime-name",

            runtime_name,

            "--requested-auth-mode",

            normalize_auth_mode(config.get("auth_mode", "legacy")),

            "--connection-mode",

            str(config.get("tunnellio_connection_mode", DEFAULT_TUNNELLIO_CONNECTION_MODE)).strip() or DEFAULT_TUNNELLIO_CONNECTION_MODE,

            "--oauth-client-policy",

            str(config.get("tunnellio_oauth_client_policy", DEFAULT_TUNNELLIO_OAUTH_CLIENT_POLICY)).strip() or DEFAULT_TUNNELLIO_OAUTH_CLIENT_POLICY,

        ]

    )

    command.append("--use-discovery" if bool(config.get("tunnellio_use_discovery", True)) else "--no-use-discovery")

    command.append("--enable-pkce" if bool(config.get("tunnellio_enable_pkce", True)) else "--no-enable-pkce")

    domain = str(config.get("tunnellio_domain", "")).strip()

    key = str(config.get("tunnellio_key", "")).strip()

    if domain:

        command.extend(["--domain", domain])

    if key:

        command.extend(["--key", key])

    return command






def build_tunnel_command(config: dict) -> list[str]:
    if reverse_proxy_enabled(config):
        raise RuntimeError("Reverse proxy mode does not use a built-in tunnel process.")
    backend = tunnel_backend(config)
    if backend == "tunnellio":
        return build_tunnellio_tunnel_command(config)
    if backend == "sish":
        return build_sish_tunnel_command(config)
    return build_serveo_tunnel_command(config)



def start_tunnel(config: dict) -> tuple[subprocess.Popen, queue.Queue[str]]:

    command = build_tunnel_command(config)

    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

    process = subprocess.Popen(

        command,

        cwd=str(SCRIPT_DIR),

        stdout=subprocess.PIPE,

        stderr=subprocess.STDOUT,

        text=True,

        encoding="utf-8",

        errors="replace",

        creationflags=flags,

    )

    lines: queue.Queue[str] = queue.Queue()



    def pump() -> None:

        with TUNNEL_LOG.open("w", encoding="utf-8") as log:

            assert process.stdout is not None

            for line in process.stdout:

                log.write(line)

                log.flush()

                lines.put(line)



    threading.Thread(target=pump, daemon=True).start()

    return process, lines





def wait_for_url(

    process: subprocess.Popen, lines: queue.Queue[str], timeout: float = 45.0

) -> str:

    deadline = time.time() + timeout

    while time.time() < deadline:

        if process.poll() is not None:

            raise tunnel_error(f"SSH tunnel exited with code {process.returncode}")

        try:

            line = lines.get(timeout=0.5)

        except queue.Empty:

            continue

        match = URL_PATTERN.search(line)

        if match:

            return match.group(0)

    raise tunnel_error("Tunnel URL was not received")





def load_tunnellio_runtime_snapshot(config: dict, runtime_name: str) -> dict | None:

    result = run_tunnellio_local_command(

        config,

        ["show-config", "--output", "json", "--name", runtime_name],

        timeout=15,

        include_remote_auth=False,

    )

    if result.returncode != 0:

        return None

    try:

        return json.loads(result.stdout)

    except json.JSONDecodeError:

        return None





def extract_tunnellio_public_url(snapshot: dict) -> str:

    if not isinstance(snapshot, dict):

        return ""

    transport = snapshot.get("transport")

    if isinstance(transport, dict):

        candidate = str(transport.get("publicUrl", "")).strip()

        if candidate:

            return candidate

    connection = snapshot.get("connection")

    if isinstance(connection, dict):

        profile = connection.get("connectionProfile")

        if isinstance(profile, dict):

            candidate = str(profile.get("publicUrl", "")).strip()

            if candidate:

                return candidate

        candidate = str(connection.get("publicUrl", "")).strip()

        if candidate:

            return candidate

    return str(snapshot.get("publicUrl", "")).strip()





def resolve_tunnellio_url(

    config: dict, process: subprocess.Popen, timeout: float = 45.0

) -> str:

    runtime_name = tunnellio_runtime_name(config)

    deadline = time.time() + timeout

    while time.time() < deadline:

        if process.poll() is not None:

            raise tunnel_error(

                f"Tunnellio client exited with code {process.returncode}"

            )

        snapshot = load_tunnellio_runtime_snapshot(config, runtime_name)

        public_url = extract_tunnellio_public_url(snapshot or {})

        if public_url:

            return public_url

        time.sleep(0.5)

    raise tunnel_error(

        f"Tunnellio runtime snapshot was not received for {runtime_name!r}"

    )





def resolve_tunnel_url(

    config: dict,

    process: subprocess.Popen,

    lines: queue.Queue[str],

    startup_grace: float = 2.0,

) -> str:

    backend = tunnel_backend(config)

    if backend == "tunnellio":

        return resolve_tunnellio_url(process=process, config=config)

    if backend == "sish":

        deadline = time.time() + max(0.0, startup_grace)

        while time.time() < deadline:

            if process.poll() is not None:

                raise tunnel_error(f"SSH tunnel exited with code {process.returncode}")

            time.sleep(0.1)

        if process.poll() is not None:

            raise tunnel_error(f"SSH tunnel exited with code {process.returncode}")

        public_url = config_public_url(config)

        if not public_url:

            raise tunnel_error("sish backend needs a stable public URL: set 'serveo_hostname' and 'tunnel_domain'.")

        return public_url



    hostname = str(config.get("serveo_hostname", "")).strip().lower()

    if not hostname:

        return wait_for_url(process, lines)



    deadline = time.time() + max(0.0, startup_grace)

    while time.time() < deadline:

        if process.poll() is not None:

            raise tunnel_error(f"SSH tunnel exited with code {process.returncode}")

        time.sleep(0.1)



    if process.poll() is not None:

        raise tunnel_error(f"SSH tunnel exited with code {process.returncode}")

    return f"https://{hostname}.serveousercontent.com"
def publish_connection(config: dict, url: str, server_pid: int, tunnel_pid: int) -> None:
    endpoint = url.rstrip("/") + "/mcp"
    backend = "custom_proxy" if reverse_proxy_enabled(config) else tunnel_backend(config)
    runtime = {
        "version": VERSION,
        "server_pid": server_pid,
        "server_match": "server.py",
        "tunnel_pid": tunnel_pid,
        "tunnel_match": (
            "custom_proxy"
            if backend == "custom_proxy"
            else "tunnellio.exe" if backend == "tunnellio" else sish_tunnel_match(config) if backend == "sish" else "serveo.net"
        ),
        "tunnel_backend": backend,
        "tunnellio_runtime_name": tunnellio_runtime_name(config)
        if backend == "tunnellio"
        else "",
        "tunnellio_path": str(tunnellio_executable_path(config)) if backend == "tunnellio" else "",
        "tunnellio_state_dir": str(tunnellio_state_dir(config)) if backend == "tunnellio" else "",
        "url": endpoint,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_json(RUNTIME_FILE, runtime)
    mode = "trusted developer" if config.get("allow_commands", False) else "file-only"
    auth_mode = normalize_auth_mode(config.get("auth_mode", "legacy"))
    public_url = config_public_url(config)
    hostname = str(config.get("serveo_hostname", "")).strip()
    if backend == "custom_proxy":
        tunnel_mode = "custom reverse proxy"
    elif backend == "tunnellio":
        tunnel_mode = f"tunnellio ({tunnellio_runtime_name(config)})"
    elif backend == "sish":
        tunnel_mode = f"sish ({sish_tunnel_match(config)})"
    else:
        tunnel_mode = f"stable ({hostname})" if hostname else "temporary"
    oauth_lines = ""
    oauth_prints: list[str] = []
    if auth_mode in ("oauth", "dual"):
        owner_code = str(config.get("oauth_owner_code", "")).strip()
        oauth_lines = (
            f"OAuth discovery: {url.rstrip('/')}/.well-known/oauth-protected-resource/mcp\n"
            f"OAuth owner code: {owner_code}\n"
        )
        oauth_prints = [
            f" OAuth discovery: {url.rstrip('/')}/.well-known/oauth-protected-resource/mcp",
            f" OAuth owner code: {owner_code}",
        ]
    CONNECTION_FILE.write_text(
        f"Notion Local MCP Easy {VERSION}\n"
        f"URL: {endpoint}\n"
        f"Authorization=Bearer {config['token']} (Bearer token)\n"
        f"Workspace: {config['workspace']}\n"
        f"Mode: {mode}\n"
        f"Auth: {auth_mode}\n"
        f"{oauth_lines}"
        + (f"Public URL: {public_url}\n" if public_url else "")
        + f"Tunnel: {tunnel_mode}\n",
        encoding="utf-8",
    )
    print("\n=======================================================")
    print(f" Notion Local MCP Easy {VERSION} is running")
    print("=======================================================")
    print(f" URL: {endpoint}")
    print(f" Authorization=Bearer {config['token']} (Bearer token)")
    print(f" Workspace: {config['workspace']}")
    print(f" Mode: {mode}")
    print(f" Auth: {auth_mode}")
    for line in oauth_prints:
        print(line)
    if public_url:
        print(f" Public URL: {public_url}")
    print(f" Connection info: {CONNECTION_FILE}")
    # IDE Gateway info — read from endpoint state if available
    gw_key = str(config.get("ide_gateway_api_key", "")).strip()
    gw_mode = str(config.get("ide_gateway_mode", "sandbox")).strip()
    gw_port = int(config.get("ide_gateway_port", 8787) or 8787)
    gw_host = str(config.get("ide_gateway_host", "127.0.0.1")).strip()
    # Try to read public_url from endpoint state
    gw_url = ""
    try:
        import json as _json
        ep_state_path = Path(config["workspace"]) / "temp" / "ide_gateway_runtime" / "endpoints" / "default.json"
        if ep_state_path.is_file():
            ep_state = _json.loads(ep_state_path.read_text(encoding="utf-8"))
            gw_url = str(ep_state.get("tunnellio_public_url", "")).strip()
            if not gw_key:
                gw_key = str(ep_state.get("token", "")).strip()
    except Exception:
        pass
    if gw_key:
        print("---")
        print(f" IDE Gateway: {gw_mode} mode")
        print(f"   Local:  http://{gw_host}:{gw_port}/v1")
        if gw_url:
            print(f"   Public: {gw_url}")
        print(f"   API key: {gw_key}")
        print(f"   Model:   ide-gateway")
        print(f"   (call ide_gateway_bridge_prompt in chat to start the bridge)")
    print("=======================================================")
    print("Keep this window open. Press Ctrl+C to stop.\n")


def ensure_ide_gateway_key(config: dict) -> dict:
    """Ensure ide_gateway_api_key and mode exist in the global config.json."""
    existing = load_json(CONFIG_FILE)
    changed = False

    gw_key = str(existing.get("ide_gateway_api_key", "")).strip()
    if not gw_key:
        import secrets as _secrets
        gw_key = "ideg_" + _secrets.token_urlsafe(32)
        existing["ide_gateway_api_key"] = gw_key
        changed = True
        print(f"IDE Gateway API key generated: {gw_key[:20]}... (stored in global config)")
    config["ide_gateway_api_key"] = gw_key

    # Also store mode/host/port in global config if missing
    if not existing.get("ide_gateway_mode"):
        existing["ide_gateway_mode"] = "sandbox"
        changed = True
    if not existing.get("ide_gateway_host"):
        existing["ide_gateway_host"] = "127.0.0.1"
        changed = True
    if not existing.get("ide_gateway_port"):
        existing["ide_gateway_port"] = 8787
        changed = True

    config["ide_gateway_mode"] = existing["ide_gateway_mode"]
    config["ide_gateway_host"] = existing["ide_gateway_host"]
    config["ide_gateway_port"] = existing["ide_gateway_port"]

    if changed:
        save_json(CONFIG_FILE, existing)
    return config


def run() -> int:
    config = setup()
    # Ensure ide_gateway_api_key exists in global config (survives upgrades)
    config = ensure_ide_gateway_key(config)
    runtime = load_json(RUNTIME_FILE)
    old_server = int(runtime.get("server_pid", 0) or 0)
    if pid_matches(old_server, str(runtime.get("server_match", "server.py"))):
        print("The server is already running.")
        if CONNECTION_FILE.exists():
            print(CONNECTION_FILE.read_text(encoding="utf-8"))
        return 0
    RUNTIME_FILE.unlink(missing_ok=True)

    server: subprocess.Popen | None = None
    server_log: TextIO | None = None
    tunnel: subprocess.Popen | None = None
    current_url = ""
    try:
        server, server_log = start_server(config)
        if reverse_proxy_enabled(config):
            current_url = config_public_url(config)
            healthy = public_health_ok(current_url, config["token"], attempts=1, delay=0)
            publish_connection(config, current_url, server.pid, 0)
            if not healthy:
                print(
                    "WARNING: the configured public URL is not answering yet. "
                    "Make sure your reverse proxy forwards it to the local MCP port."
                )
        else:
            tunnel, lines, current_url = start_and_resolve_tunnel(config)
            if not public_health_ok(current_url, config["token"], process=tunnel):
                raise tunnel_error(
                    f"Public health check failed: {current_url}/health did not answer"
                )
            publish_connection(config, current_url, server.pid, tunnel.pid)

        while True:
            if server.poll() is not None:
                raise RuntimeError(
                    f"MCP server stopped with code {server.returncode}; see {SERVER_LOG}"
                )
            if reverse_proxy_enabled(config):
                time.sleep(1)
                continue
            if tunnel.poll() is not None:
                print("Tunnel disconnected; reconnecting in 3 seconds...")
                time.sleep(3)
                previous_url = current_url
                tunnel, lines, current_url = start_and_resolve_tunnel(config)
                healthy = public_health_ok(current_url, config["token"], process=tunnel)
                publish_connection(config, current_url, server.pid, tunnel.pid)
                if not healthy:
                    print(
                        f"WARNING: {current_url}/health is not answering yet; keeping the tunnel up and retrying on next disconnect."
                    )
                elif current_url == previous_url:
                    print("Tunnel restored with the same URL.")
                else:
                    print(
                        "IMPORTANT: the tunnel URL changed. Update the Custom MCP URL in Notion."
                    )
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if tunnel is not None and tunnel.poll() is None:
            if tunnel_backend(config) == "tunnellio":
                with contextlib.suppress(Exception):
                    request_tunnellio_stop(config, tunnellio_runtime_name(config), force=True)
            stop_pid(
                tunnel.pid,
                "tunnellio.exe"
                if tunnel_backend(config) == "tunnellio"
                else sish_tunnel_match(config)
                if tunnel_backend(config) == "sish"
                else "serveo.net",
            )
        if server is not None and server.poll() is None:
            stop_pid(server.pid, "server.py")
        if server_log is not None:
            server_log.close()
        RUNTIME_FILE.unlink(missing_ok=True)



def mask_token(token: str) -> str:

    if len(token) <= 10:

        return "*" * len(token)

    return f"{token[:4]}...{token[-4:]}"





def show_connection(full: bool) -> int:

    if not CONNECTION_FILE.exists():

        print("No connection information yet. Run START.bat first.")

        return 1

    text = CONNECTION_FILE.read_text(encoding="utf-8")

    if not full:

        config = load_json(CONFIG_FILE)

        for secret in (str(config.get("token", "")), str(config.get("oauth_owner_code", ""))):

            if secret:

                text = text.replace(secret, mask_token(secret))

        print(text)

        print("Secrets are masked. Use SHOW_CONNECTION.bat --full to reveal them.")

        return 0

    print(text)

    return 0





def tunnel_setup() -> int:
    config = load_json(CONFIG_FILE)
    if not config:
        print("Run setup first: the base configuration does not exist yet.")
        return 1
    print("\nTunnel backend:")
    print(" 1. Serveo")
    print(" 2. Self-hosted sish relay")
    print(" 3. Custom public URL / reverse proxy")
    choice = prompt_input("Choose tunnel backend [1]: ").strip().lower() or "1"
    if choice in {"2", "sish"}:
        config["tunnel_backend"] = "sish"
        config["tunnel_host"] = prompt_input("sish SSH host: ").strip()
        ssh_port = prompt_input(f"sish SSH port [{DEFAULT_SISH_SSH_PORT}]: ").strip()
        if ssh_port:
            config["tunnel_ssh_port"] = ssh_port
        config["tunnel_domain"] = prompt_input("public wildcard domain: ").strip().lower().strip(".")
        config["serveo_hostname"] = prompt_input("reserved subdomain: ").strip().lower()
        config["ssh_key"] = prompt_input("private key path: ").strip()
    elif choice in {"3", "custom", "reverse", "proxy"}:
        config["tunnel_backend"] = "custom_proxy"
        config["public_url"] = prompt_public_url(config)
    else:
        config["tunnel_backend"] = "serveo"
    save_json(CONFIG_FILE, config)
    print(f"Tunnel settings saved: {CONFIG_FILE}")
    return 0


def oauth_setup() -> int:

    config = load_json(CONFIG_FILE)

    if not config:

        print("Run SETUP.bat first: the base configuration does not exist yet.")

        return 1

    current = normalize_auth_mode(config.get("auth_mode", "legacy"))

    print(f"\n=== Notion Local MCP Easy {VERSION}: OAuth setup ===\n")

    print(f"Current auth mode: {current}")

    for index, mode in enumerate(AUTH_MODE_OPTIONS, start=1):

        print(f"  {index}. {mode} — {AUTH_MODE_DESCRIPTIONS[mode]}")

    choice = input(f"Select auth mode [1-{len(AUTH_MODE_OPTIONS)}, Enter keeps '{current}']: ").strip()

    mode = current

    if choice:

        if not choice.isdigit() or not 1 <= int(choice) <= len(AUTH_MODE_OPTIONS):

            print("Invalid selection; nothing changed.")

            return 1

        mode = AUTH_MODE_OPTIONS[int(choice) - 1]

    config["auth_mode"] = mode



    if mode in ("oauth", "dual"):

        owner_code = str(config.get("oauth_owner_code", "")).strip()

        if owner_code and yes_no("Generate a new OAuth owner code?", False):

            owner_code = ""

        if not owner_code:

            owner_code = secrets.token_urlsafe(9)

            print("\nNew OAuth owner code (needed to approve clients on /consent):")

            print(f"  {owner_code}")

        config["oauth_owner_code"] = owner_code

        if not reverse_proxy_enabled(config) and tunnel_backend(config) != "tunnellio" and not str(config.get("serveo_hostname", "")).strip():

            print("\nWARNING: OAuth works best with a stable public URL. Configure a reserved Serveo hostname in SETUP.bat if you want stable OAuth metadata.")

        print(

            "\nConnection summary:\n"

            "  - For Notion Custom MCP use the same Bearer-token flow in legacy/dual.\n"

            "  - For OAuth clients use Streamable HTTP + OAuth against the /mcp URL.\n"

            "  - Approve new OAuth clients on the /consent page with the owner code.\n"

            "  - Use REGISTER_OAUTH_CLIENT.bat/.sh for Bring Your Own OAuth App flows."

        )

    save_json(CONFIG_FILE, config)

    print(f"\nAuth mode saved: {mode} ({CONFIG_FILE})")

    return 0





def register_oauth_client() -> int:

    from auth import ALL_SCOPES, LocalOAuthProvider, OAuthStore

    from mcp.shared.auth import OAuthClientInformationFull



    config = load_json(CONFIG_FILE)

    if not config:

        print("Run SETUP.bat first: the base configuration does not exist yet.")

        return 1

    print(f"\n=== Notion Local MCP Easy {VERSION}: register OAuth client ===\n")

    redirect_raw = input("Redirect URI(s): ").strip()

    redirect_uris = [item.strip() for item in redirect_raw.split(",") if item.strip()]

    if not redirect_uris:

        print("At least one redirect URI is required.")

        return 1



    public_client = yes_no("Public client with PKCE and no client secret?", True)

    scopes_raw = input(f"Scopes [{' '.join(ALL_SCOPES)}]: ").strip()

    scopes = scopes_raw.split() if scopes_raw else list(ALL_SCOPES)

    unknown = [scope for scope in scopes if scope not in ALL_SCOPES]

    if unknown:

        print(f"Unknown scopes: {', '.join(unknown)}")

        return 1



    client_id = "byo-" + secrets.token_urlsafe(8)

    client_secret = None if public_client else secrets.token_hex(32)

    client = OAuthClientInformationFull(

        client_id=client_id,

        client_secret=client_secret,

        redirect_uris=redirect_uris,

        token_endpoint_auth_method="none" if public_client else "client_secret_post",

        grant_types=["authorization_code", "refresh_token"],

        response_types=["code"],

        scope=" ".join(scopes),

        client_name="Manually registered client (BYO)",

    )

    try:

        LocalOAuthProvider.validate_redirect_uris(client)

    except Exception as exc:

        detail = getattr(exc, "error_description", None) or str(exc)

        print(f"Invalid redirect URI: {detail}")

        return 1



    store = OAuthStore(CONFIG_DIR / "oauth_state.json")

    store.clients[client.client_id] = client.model_dump(mode="json")

    store.save()



    print("\nClient registered. Enter these values in the MCP client:")

    print(f"  client_id: {client_id}")

    if client_secret:

        print(f"  client_secret: {client_secret}")

    else:

        print("  client_secret: (none — public client with PKCE)")

    print(f"  scopes: {' '.join(scopes)}")

    print(f"Stored in: {store.state_file}")

    return 0





def main() -> int:

    parser = argparse.ArgumentParser(

        description=f"One-click launcher for Notion Local MCP Easy {VERSION}"

    )

    parser.add_argument("--setup", action="store_true", help="run the setup wizard again")

    parser.add_argument("--stop", action="store_true", help="stop background processes")

    parser.add_argument("--show", action="store_true", help="show current connection details")

    parser.add_argument(

        "--full", action="store_true", help="with --show: reveal the full Bearer token and OAuth owner code"

    )

    parser.add_argument("--oauth", action="store_true", help="configure auth mode (legacy/oauth/dual)")

    parser.add_argument("--register-oauth-client", action="store_true", help="pre-register an OAuth client for BYO OAuth app flows")

    args = parser.parse_args()



    if args.stop:

        stop_all()

        return 0

    if args.setup:

        stop_all()

        setup(force=True)

        return 0

    if args.show:

        return show_connection(args.full)

    if args.oauth:

        return oauth_setup()

    if args.register_oauth_client:

        return register_oauth_client()

    return run()





if __name__ == "__main__":

    raise SystemExit(main())

