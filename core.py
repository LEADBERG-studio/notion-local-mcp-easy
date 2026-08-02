from __future__ import annotations

import hmac
import os
import shutil
from pathlib import Path


def _consteq(a: str, b: str) -> bool:
    """Constant-time string comparison that fails closed on hostile input."""
    return hmac.compare_digest(a.encode("utf-8", "ignore"), b.encode("utf-8", "ignore"))

DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}

DEFAULT_ALLOWED_COMMANDS = {
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
}


def safe_path(base_dir: Path, value: str | os.PathLike[str]) -> Path:
    """Return a path inside base_dir while blocking symlink/parent escapes.

    On Windows CI, tempfile may expose 8.3 short paths while Path.resolve()
    expands them to long paths. Keep the caller-facing spelling stable, but use
    resolved paths for the security check.
    """
    base_display = Path(base_dir)
    base_resolved = base_display.resolve()
    raw = Path(value).expanduser()
    candidate_display = raw if raw.is_absolute() else (base_display / raw)
    candidate_resolved = candidate_display.resolve()
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"Access denied: path is outside {base_resolved}") from exc
    return candidate_display


def normalized_program_name(program: str) -> str:
    name = Path(program).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def resolve_program(base_dir: Path, program: str, allowed: set[str]) -> str:
    """Resolve an executable without enabling a command shell."""
    if not program or "\x00" in program:
        raise ValueError("Program is required")

    name = normalized_program_name(program)
    if name not in allowed:
        raise ValueError(
            f"Program '{name}' is not allowed. Allowed: {', '.join(sorted(allowed))}"
        )

    if any(sep in program for sep in ("/", "\\")) or Path(program).is_absolute():
        candidate = safe_path(base_dir, program)
        if not candidate.is_file():
            raise ValueError(f"Program does not exist: {candidate}")
        return str(candidate)

    resolved = shutil.which(program)
    if not resolved:
        raise ValueError(f"Program is not installed or not on PATH: {program}")
    return resolved


def should_skip(path: Path, include_hidden: bool, excludes: set[str]) -> bool:
    name = path.name
    return name in excludes or (not include_hidden and name.startswith("."))
