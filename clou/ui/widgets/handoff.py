"""Handoff renderer — semantic display of milestone handoff documents.

Parses a coordinator's ``handoff.md`` into typed sections and renders
each with appropriate colour coding from the Clou palette.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto

from rich.text import Text
from textual.widgets import Static

from clou.ui.theme import PALETTE

# Semantic hex colors from the palette for inline Rich markup.
_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_GREEN_DIM_HEX = PALETTE["accent-green"].dim().to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_TEXT_HEX = PALETTE["text"].to_hex()
_TEXT_DIM_HEX = PALETTE["text-dim"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()

# ---------------------------------------------------------------------------
# Section model
# ---------------------------------------------------------------------------

#: URL pattern for detection in handoff text.
_URL_RE = re.compile(r"https?://\S+")

#: Pass/fail indicator patterns.
_PASS_RE = re.compile(r"[\u2705\u2714]")  # ✅ or ✔
_FAIL_RE = re.compile(r"[\u274c\u2718]")  # ❌ or ✘


class SectionType(Enum):
    """Known handoff section types."""

    SUMMARY = auto()
    RUNNING_SERVICES = auto()
    WALKTHROUGH = auto()
    VERIFICATION = auto()
    LIMITATIONS = auto()
    FILES_CHANGED = auto()
    UNKNOWN = auto()


#: Map lowered header text to section type.
_HEADER_MAP: dict[str, SectionType] = {
    "summary": SectionType.SUMMARY,
    "running services": SectionType.RUNNING_SERVICES,
    "walk-through": SectionType.WALKTHROUGH,
    "walkthrough": SectionType.WALKTHROUGH,
    "verification results": SectionType.VERIFICATION,
    "verification": SectionType.VERIFICATION,
    "known limitations": SectionType.LIMITATIONS,
    "limitations": SectionType.LIMITATIONS,
    "files changed": SectionType.FILES_CHANGED,
}


@dataclass
class HandoffSection:
    """A single parsed section of a handoff document."""

    section_type: SectionType
    title: str
    body: str
    lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_handoff(text: str) -> list[HandoffSection]:
    """Parse a handoff markdown string into typed sections.

    Recognises ``## <Title>`` as section boundaries. The top-level
    ``# Handoff: ...`` heading is treated as a summary preamble.

    Returns a list of :class:`HandoffSection` in document order.
    """
    sections: list[HandoffSection] = []
    current_title = ""
    current_type = SectionType.SUMMARY
    current_lines: list[str] = []

    def _flush() -> None:
        body = "\n".join(current_lines).strip()
        if body or current_title:
            sections.append(
                HandoffSection(
                    section_type=current_type,
                    title=current_title,
                    body=body,
                    lines=[ln for ln in current_lines if ln.strip()],
                )
            )

    for line in text.splitlines():
        stripped = line.strip()

        # Top-level heading — treat as title, skip to sections.
        if stripped.startswith("# ") and not stripped.startswith("## "):
            current_title = stripped[2:].strip() or "Untitled"
            current_type = SectionType.SUMMARY
            current_lines = []
            continue

        # Section heading.
        if stripped.startswith("## "):
            _flush()
            heading = stripped[3:].strip() or "Untitled"
            current_title = heading
            current_type = _HEADER_MAP.get(heading.lower(), SectionType.UNKNOWN)
            current_lines = []
            continue

        current_lines.append(line)

    _flush()
    return sections


# ---------------------------------------------------------------------------
# Rich rendering
# ---------------------------------------------------------------------------


def _render_line(line: str) -> Text:
    """Render a single body line with semantic highlighting."""
    text = Text()

    # Check for pass/fail indicators.
    if _PASS_RE.search(line):
        text.append(line, style=f"{_GREEN_DIM_HEX}")
        return text
    if _FAIL_RE.search(line):
        text.append(line, style=f"{_ROSE_HEX}")
        return text

    # Check for URLs — render URL portions in teal.
    last = 0
    for match in _URL_RE.finditer(line):
        start, end = match.span()
        if start > last:
            text.append(line[last:start], style=f"{_TEXT_HEX}")
        text.append(match.group(), style=f"underline {_TEAL_HEX}")
        last = end
    if last < len(line):
        text.append(line[last:], style=f"{_TEXT_HEX}")

    return text


def render_handoff(sections: list[HandoffSection]) -> Text:
    """Render all handoff sections into a single Rich Text object."""
    result = Text()

    for i, section in enumerate(sections):
        if i > 0:
            result.append("\n")

        # Section header.
        if section.title:
            result.append(f"  {section.title}\n", style=f"bold {_GOLD_HEX}")

        # Body lines.
        for line in section.body.splitlines():
            stripped = line.strip()
            if not stripped:
                result.append("\n")
                continue
            result.append("  ")
            result.append_text(_render_line(stripped))
            result.append("\n")

    return result


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class HandoffWidget(Static):
    """Renders a milestone handoff document with semantic colour coding."""

    def __init__(
        self,
        content: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._content = content
        self._sections: list[HandoffSection] = []
        if content:
            self._sections = parse_handoff(content)

    def on_mount(self) -> None:
        """Render initial content if provided."""
        if self._sections:
            self.update(render_handoff(self._sections))

    def update_content(self, text: str) -> None:
        """Parse new handoff markdown and re-render."""
        self._content = text
        self._sections = parse_handoff(text)
        self.update(render_handoff(self._sections))
