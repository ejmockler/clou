"""Semantic conversation history — shared by /export and /compact."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ConversationEntry:
    """One turn in the conversation."""

    role: str  # "user", "assistant", "tool", "system"
    content: str
    timestamp: float = field(default_factory=time.time)

    def iso_timestamp(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()


def export_markdown(
    entries: list[ConversationEntry],
    *,
    include_tools: bool = False,
) -> str:
    """Serialize conversation entries to Markdown.

    Args:
        entries: The conversation history.
        include_tools: If True, include tool-use entries.
    """
    lines: list[str] = ["# Conversation Export", ""]
    for entry in entries:
        if entry.role == "tool" and not include_tools:
            continue
        heading = {
            "user": "## You",
            "assistant": "## Clou",
            "tool": "## Tool",
            "system": "## System",
        }.get(entry.role, f"## {entry.role}")
        lines.append(f"{heading}")
        lines.append(f"*{entry.iso_timestamp()}*")
        lines.append("")
        lines.append(entry.content)
        lines.append("")
    return "\n".join(lines)
