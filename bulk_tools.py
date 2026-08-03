"""Batch and patch helpers for the MCP file tools.

These exist for two reasons that matter to a tunnelled server:

**Fewer round trips.** Reading ten files used to be ten requests. Over a tunnel
every request is a chance to hit a keep-alive race or a relay hiccup, so a burst
of small calls is exactly the traffic shape that breaks transports. One batched
call is one request.

**Fewer tokens.** Rewriting a whole file to change three lines costs the entire
file twice, once to read and once to write. A unified diff costs the changed
lines. On large files that is the difference between a cheap edit and an
expensive one.

Everything here is pure logic over text and paths, so it is testable without a
server and without a workspace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """A unified diff could not be applied."""


@dataclass
class Hunk:
    old_start: int
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)


def parse_unified_diff(diff: str) -> list[Hunk]:
    """Parse the hunks of a unified diff for a single file.

    File headers are tolerated and ignored: the caller already knows which file
    is being patched, and trusting a path from inside the diff would be a way to
    escape the workspace.
    """
    hunks: list[Hunk] = []
    current: Hunk | None = None
    for raw in diff.splitlines():
        if raw.startswith(("--- ", "+++ ", "diff ", "index ", "new file", "deleted file")):
            continue
        match = HUNK_HEADER.match(raw)
        if match:
            current = Hunk(old_start=int(match.group(1)))
            hunks.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        marker, _, text = raw[:1], raw[1:2], raw[1:]
        if marker == " ":
            current.old_lines.append(text)
            current.new_lines.append(text)
        elif marker == "-":
            current.old_lines.append(text)
        elif marker == "+":
            current.new_lines.append(text)
        elif raw == "":
            # A bare empty line inside a hunk is a context line whose trailing
            # space was stripped by an editor or a transport.
            current.old_lines.append("")
            current.new_lines.append("")
    if not hunks:
        raise PatchError(
            "No unified diff hunks were found. Each change must start with a "
            "'@@ -start,count +start,count @@' header."
        )
    return hunks


def _match_at(lines: list[str], index: int, expected: list[str]) -> bool:
    if index < 0 or index + len(expected) > len(lines):
        return False
    return lines[index : index + len(expected)] == expected


def _locate(lines: list[str], hunk: Hunk, *, search_radius: int = 200) -> int:
    """Find where a hunk applies.

    The line numbers in a diff drift as soon as an earlier hunk changes the line
    count, and models often produce slightly stale numbers. So the stated
    position is a hint: the content has to match, and it is searched for nearby.
    """
    if not hunk.old_lines:
        return max(0, min(hunk.old_start - 1, len(lines)))
    preferred = hunk.old_start - 1
    if _match_at(lines, preferred, hunk.old_lines):
        return preferred
    for distance in range(1, search_radius + 1):
        for candidate in (preferred - distance, preferred + distance):
            if _match_at(lines, candidate, hunk.old_lines):
                return candidate
    raise PatchError(
        "A hunk did not match the file. The context lines differ from what is on "
        f"disk near line {hunk.old_start}. Re-read the file and rebuild the diff."
    )


def apply_unified_diff(content: str, diff: str) -> tuple[str, int]:
    """Apply a unified diff to text. Returns the result and the hunk count.

    All hunks apply or none do: a half-applied patch is worse than a rejected
    one, because the file is then in a state nobody described.
    """
    hunks = parse_unified_diff(diff)
    newline = "\r\n" if "\r\n" in content else "\n"
    trailing_newline = content.endswith(("\n", "\r"))
    lines = content.splitlines()
    # Apply from the bottom up so earlier offsets stay valid.
    located = sorted(((_locate(lines, hunk), hunk) for hunk in hunks), reverse=True)
    for index, hunk in located:
        lines[index : index + len(hunk.old_lines)] = hunk.new_lines
    result = newline.join(lines)
    if trailing_newline and result:
        result += newline
    return result, len(hunks)


@dataclass
class ReplaceHit:
    path: str
    count: int
    preview: str = ""


def replace_in_text(
    content: str,
    search: str,
    replacement: str,
    *,
    regex: bool = False,
    ignore_case: bool = False,
) -> tuple[str, int]:
    if not search:
        raise ValueError("search must not be empty")
    if regex:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            pattern = re.compile(search, flags)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc
        updated, count = pattern.subn(replacement, content)
        return updated, count
    if ignore_case:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        updated, count = pattern.subn(replacement.replace("\\", "\\\\"), content)
        return updated, count
    count = content.count(search)
    return (content.replace(search, replacement) if count else content), count


def first_change_preview(before: str, after: str, *, width: int = 160) -> str:
    """One readable line showing the first difference.

    A count alone does not let anyone judge whether a project-wide replacement
    is safe. A sample does.
    """
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    for index, (old, new) in enumerate(zip(before_lines, after_lines), start=1):
        if old != new:
            return f"line {index}: {old.strip()[:width]}  ->  {new.strip()[:width]}"
    if len(before_lines) != len(after_lines):
        return f"line count {len(before_lines)} -> {len(after_lines)}"
    return ""


def tail_lines(content: str, limit: int) -> tuple[list[str], int]:
    lines = content.splitlines()
    limit = max(1, limit)
    return lines[-limit:], len(lines)


def render_batch(
    entries: Iterable[tuple[str, str]],
    *,
    per_file_chars: int,
    total_chars: int,
) -> str:
    """Render several files as one answer under a shared budget.

    The budget is shared on purpose: the point of batching is to make one
    predictable response, not to multiply the per-file limit by the file count
    and hand the transport something enormous.
    """
    chunks: list[str] = []
    used = 0
    skipped: list[str] = []
    for label, body in entries:
        if used >= total_chars:
            skipped.append(label)
            continue
        allowance = min(per_file_chars, total_chars - used)
        text = body
        note = ""
        if len(text) > allowance:
            text = text[:allowance]
            note = f"\n... [truncated: {len(body):,} chars total]"
        block = f"===== {label} =====\n{text}{note}"
        chunks.append(block)
        used += len(block)
    if skipped:
        chunks.append(
            "===== not shown =====\n"
            + "\n".join(skipped)
            + "\n[the shared character budget ran out; request these separately]"
        )
    return "\n\n".join(chunks) if chunks else "(no files matched)"


__all__ = [
    "Hunk",
    "PatchError",
    "ReplaceHit",
    "apply_unified_diff",
    "first_change_preview",
    "parse_unified_diff",
    "render_batch",
    "replace_in_text",
    "tail_lines",
]
