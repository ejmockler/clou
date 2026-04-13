"""Tool call summarization — pure functions for one-line tool descriptions.

Formats tool calls into human-readable one-liners for activity lines
and breath events. No widget state dependencies.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from clou.ui.bridge import _shorten_url
from clou.ui.diff import compute_edit_stats, compute_multi_edit_stats

# ── Glyphs ──────────────────────────────────────────────────────────
# ▸ for local workspace tools, ↗ for web tools (reaching outward).

GLYPH_LOCAL = "\u25b8"  # ▸
GLYPH_WEB = "\u2197"    # ↗

_WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})


def tool_glyph(name: str) -> str:
    """Return the activity-line glyph for a tool — ↗ for web, ▸ for local."""
    return GLYPH_WEB if name in _WEB_TOOLS else GLYPH_LOCAL


def tool_summary(name: str, tool_input: dict[str, object]) -> str:
    """One-line ambient summary of a tool call — name + key context."""
    if name == "Edit":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        old = str(tool_input.get("old_string", ""))
        new = str(tool_input.get("new_string", ""))
        adds, rems = compute_edit_stats(old, new)
        stats = f"  +{adds} −{rems}" if adds or rems else ""  # noqa: RUF001
        return f"{name} {fname}{stats}" if fname else name
    if name == "MultiEdit":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        edits = tool_input.get("edits", [])
        if isinstance(edits, list):
            adds, rems = compute_multi_edit_stats(edits)
        else:
            adds, rems = 0, 0
        stats = f"  +{adds} −{rems}" if adds or rems else ""  # noqa: RUF001
        return f"{name} {fname}{stats}" if fname else name
    if name == "Write":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        return f"{name} {fname}  (new)" if fname else name
    if name == "Read":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        return f"{name} {fname}" if fname else name
    if name == "Bash":
        cmd = str(tool_input.get("command", ""))
        # Show first meaningful segment, strip long pipes.
        short = cmd.split("|")[0].strip()[:50]
        return f"{name} {short}" if short else name
    if name == "Grep":
        pattern = str(tool_input.get("pattern", ""))
        return f"{name} /{pattern[:30]}/" if pattern else name
    if name == "Glob":
        pattern = str(tool_input.get("pattern", ""))
        return f"{name} {pattern[:40]}" if pattern else name
    if name == "Agent":
        desc = str(tool_input.get("description", ""))
        return f"{name} {desc[:40]}" if desc else name
    if name == "WebFetch":
        url = str(tool_input.get("url", ""))
        return f"fetch {_shorten_url(url)}" if url else name
    if name == "WebSearch":
        query = str(tool_input.get("query", ""))
        return f'search "{query[:40]}"' if query else name
    if name.startswith("mcp__"):
        # mcp__brutalist__roast → "brutalist roast"
        parts = name.split("__")
        short = " ".join(parts[1:]) if len(parts) > 1 else name
        return short
    return name


