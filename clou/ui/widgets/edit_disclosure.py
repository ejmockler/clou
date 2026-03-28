"""Edit disclosure — collapsible diff display with auto-collapse lifecycle.

Green left edge identifies edits in peripheral vision (parallel to
teal for assistant, gold for user, blue for agents).
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import Static

from clou.ui.theme import PALETTE

_GREEN_DIM_HEX = PALETTE["accent-green"].dim().to_hex()

# ── Lifecycle thresholds ────────────────────────────────────────────
DISCLOSURE_SETTLE: float = 4.0    # seconds before collapse
DISCLOSURE_PRUNE: float = 30.0    # seconds after collapse before DOM removal


class EditDisclosure(Static):
    """Edit diff disclosure — compact summary + expandable diff body."""

    DEFAULT_CSS = f"""
    EditDisclosure {{
        border-left: tall {_GREEN_DIM_HEX};
        padding: 0 0 0 2;
        margin: 0;
    }}
    """

    def __init__(
        self,
        summary: Text,
        diff_body: Text | None = None,
        *,
        classes: str = "",
    ) -> None:
        super().__init__("", classes=classes)
        self._summary = summary
        self._diff_body = diff_body
        self._expanded: bool = True
        self._pinned: bool = False
        self._birth: float = time.monotonic()
        self._collapsed_at: float = 0.0

    def render(self) -> Text:
        result = Text()
        result.append_text(self._summary)
        if self._expanded and self._diff_body and self._diff_body.plain:
            result.append("\n")
            result.append_text(self._diff_body)
        return result

    def update_lifecycle(self, now: float) -> bool:
        """Check age and collapse if past settle threshold.

        Returns True if visual state changed (needs refresh).
        """
        if self._pinned:
            return False
        age = now - self._birth
        if age >= DISCLOSURE_SETTLE and self._expanded:
            self._expanded = False
            self._collapsed_at = now
            return True
        return False

    def on_click(self) -> None:
        """Toggle expansion on click; pin to prevent auto-collapse."""
        self._expanded = not self._expanded
        self._pinned = True
        self.refresh()
