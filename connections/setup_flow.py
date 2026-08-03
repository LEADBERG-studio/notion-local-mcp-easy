"""Interactive helpers shared by the profile setup script and the launcher.

Every prompt is owned by a circuit through ``Circuit.questions()``. This module
only renders them, so adding a new circuit never means editing a setup menu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .base import (
    Circuit,
    ConnectionConfigError,
    ConnectionSetupAborted,
    Question,
    normalize_label,
)

PromptFn = Callable[[str], str]


def default_prompt(text: str) -> str:
    try:
        return input(text)
    except (EOFError, StopIteration) as exc:
        raise ConnectionSetupAborted(
            "Interactive setup needs a console. Run SETUP.bat or PROFILES.bat directly."
        ) from exc


def ask_yes_no(question: str, default: bool, prompt: PromptFn = default_prompt) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        answer = prompt(f"{question} [{marker}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "д", "да"}:
            return True
        if answer in {"n", "no", "н", "нет"}:
            return False
        print("Please answer yes or no.")


def _coerce(question: Question, raw: str, current: Any) -> Any:
    value = raw.strip().strip('"')
    if not value:
        return current
    if question.kind == "hostname":
        return normalize_label(value)
    if question.kind == "port":
        if not value.isdigit():
            raise ConnectionConfigError("Use a numeric TCP port.")
        return value
    if question.kind == "path":
        return str(Path(value).expanduser())
    if question.kind == "url":
        return value.rstrip("/")
    if question.kind == "bool":
        return value.lower() in {"y", "yes", "true", "1", "д", "да"}
    return value


def _display(question: Question, value: Any) -> str:
    text = str(value or "")
    if question.kind == "secret" and len(text) > 10:
        return f"{text[:4]}...{text[-4:]}"
    return text


def ask_question(
    question: Question,
    current: Any,
    prompt: PromptFn = default_prompt,
) -> Any:
    if question.help:
        print(f"   {question.help}")
    shown = _display(question, current)
    label = f"{question.prompt} [{shown}]" if shown else question.prompt
    if not question.required and not shown:
        label += " (optional, Enter to skip)"
    while True:
        raw = prompt(f"{label}: ")
        try:
            value = _coerce(question, raw, current)
        except ConnectionConfigError as exc:
            print(f"   {exc}")
            continue
        if question.required and not str(value or "").strip():
            print("   This value is required.")
            continue
        return value


def configure_circuit(
    circuit: Circuit,
    current_settings: dict[str, Any] | None = None,
    prompt: PromptFn = default_prompt,
) -> dict[str, Any]:
    """Ask a circuit's own questions and return validated settings.

    Values the operator is never asked about come straight from the immutable
    blueprint, which is why a fresh profile is already complete.
    """
    settings = circuit.merge(current_settings)
    blueprint = circuit.blueprint
    print(f"\n=== {circuit.title} ===")
    print(blueprint.get("summary", circuit.summary))
    prepares = blueprint.get("operatorPrepares") or []
    if prepares:
        print("\nWhat you prepare yourself:")
        for item in prepares:
            print(f"  - {item}")
    questions = circuit.questions()
    if not questions:
        print("\nThis circuit needs no input. Defaults come from the shipped blueprint.")
        return circuit.validate(settings)
    print("")
    while True:
        for question in questions:
            settings[question.key] = ask_question(question, settings.get(question.key), prompt)
        try:
            return circuit.validate(settings)
        except ConnectionConfigError as exc:
            print(f"\nThat profile is not valid yet: {exc}\nLet's go through it again.\n")


def verify_circuit(circuit: Circuit, settings: dict[str, Any]) -> tuple[bool, str]:
    try:
        return circuit.verify(settings)
    except ConnectionConfigError as exc:
        return False, str(exc)


def print_summary(circuit: Circuit, settings: dict[str, Any] | None) -> None:
    lines = circuit.summary_lines(settings)
    if not lines:
        return
    for line in lines:
        print(f"   {line}")
