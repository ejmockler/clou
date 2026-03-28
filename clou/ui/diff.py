"""Diff rendering — unified diff to styled Rich Text.

Also provides ``build_edit_summary`` and ``build_diff_body`` for
rendering edit tool calls as styled summaries and inline diffs.
"""

from __future__ import annotations

import difflib
from pathlib import PurePosixPath

from rich.text import Text

from clou.ui.theme import PALETTE

_GREEN_HEX = PALETTE["accent-green"].to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_MUTED_HEX = PALETTE["text-muted"].to_hex()

# Cap line count for SequenceMatcher to avoid O(n²) blowup on huge edits.
_MAX_DIFF_LINES: int = 1000


def compute_edit_stats(old_string: str, new_string: str) -> tuple[int, int]:
    """Count (additions, removals) between old and new strings."""
    old_lines = old_string.splitlines(keepends=True)
    new_lines = new_string.splitlines(keepends=True)
    if len(old_lines) > _MAX_DIFF_LINES or len(new_lines) > _MAX_DIFF_LINES:
        return len(new_lines), len(old_lines)  # coarse estimate
    additions = 0
    removals = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if tag == "replace":
            removals += i2 - i1
            additions += j2 - j1
        elif tag == "delete":
            removals += i2 - i1
        elif tag == "insert":
            additions += j2 - j1
    return additions, removals


def compute_multi_edit_stats(edits: list[dict[str, str]]) -> tuple[int, int]:
    """Sum stats across a MultiEdit's edits list."""
    total_add = 0
    total_rem = 0
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        old = edit.get("old_string", "")
        new = edit.get("new_string", "")
        a, r = compute_edit_stats(old, new)
        total_add += a
        total_rem += r
    return total_add, total_rem


def render_inline_diff(old_string: str, new_string: str) -> Text:
    """Render old/new strings as rose removals + green additions.

    Works with raw strings directly (not unified diff format).
    """
    old_lines = old_string.splitlines(keepends=True)
    new_lines = new_string.splitlines(keepends=True)
    if len(old_lines) > _MAX_DIFF_LINES or len(new_lines) > _MAX_DIFF_LINES:
        result = Text()
        result.append(f"(diff too large: {len(old_lines)} → {len(new_lines)} lines)", style=_DIM_HEX)
        return result
    result = Text()
    first = True
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            for line in old_lines[i1:i2]:
                if not first:
                    result.append("\n")
                first = False
                result.append(f"- {line.rstrip()}", style=_ROSE_HEX)
        if tag in ("replace", "insert"):
            for line in new_lines[j1:j2]:
                if not first:
                    result.append("\n")
                first = False
                result.append(f"+ {line.rstrip()}", style=_GREEN_HEX)
    return result


def build_edit_summary(name: str, tool_input: dict[str, object]) -> Text:
    """Build styled Text for edit tool summaries with colored diff stats."""
    text = Text()
    text.append("\u25b8 ", style=_DIM_HEX)
    fp = str(tool_input.get("file_path", ""))
    fname = PurePosixPath(fp).name if fp else name
    adds, rems = 0, 0
    if name == "Edit":
        adds, rems = compute_edit_stats(
            str(tool_input.get("old_string", "")),
            str(tool_input.get("new_string", "")),
        )
    elif name == "MultiEdit":
        edits = tool_input.get("edits", [])
        if isinstance(edits, list):
            adds, rems = compute_multi_edit_stats(edits)
    text.append(f"{name} {fname}", style=_DIM_HEX)
    if name == "Write":
        text.append("  (new)", style=_GREEN_HEX)
    elif adds or rems:
        text.append("  ", style="")
        text.append(f"+{adds}", style=_GREEN_HEX)
        text.append(" ", style="")
        text.append(f"\u2212{rems}", style=_ROSE_HEX)
    return text


def build_diff_body(name: str, tool_input: dict[str, object]) -> Text | None:
    """Build inline diff body for an edit tool disclosure."""
    if name == "Edit":
        old = str(tool_input.get("old_string", ""))
        new = str(tool_input.get("new_string", ""))
        return render_inline_diff(old, new) if old or new else None
    if name == "MultiEdit":
        edits = tool_input.get("edits", [])
        if not isinstance(edits, list):
            return None
        combined = Text()
        for i, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            old = str(edit.get("old_string", ""))
            new = str(edit.get("new_string", ""))
            if old or new:
                if i > 0 and combined.plain:
                    combined.append("\n")
                combined.append_text(render_inline_diff(old, new))
        return combined if combined.plain else None
    return None


def render_diff(diff_text: str) -> Text:
    """Parse a unified diff string into styled Rich Text.

    Returns an empty ``Text`` if *diff_text* is empty.
    """
    result = Text()
    for i, line in enumerate(diff_text.splitlines()):
        if i > 0:
            result.append("\n")
        if line.startswith("diff ") or line.startswith("index "):
            result.append(line, style=_MUTED_HEX)
        elif line.startswith("---") or line.startswith("+++"):
            result.append(line, style=f"bold {_GOLD_HEX}")
        elif line.startswith("@@"):
            result.append(line, style=_MUTED_HEX)
        elif line.startswith("+"):
            result.append(line, style=_GREEN_HEX)
        elif line.startswith("-"):
            result.append(line, style=_ROSE_HEX)
        else:
            result.append(line, style=_DIM_HEX)
    return result
