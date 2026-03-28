"""Message display widgets — assistant and user message rendering.

``MarkdownMessage`` renders assistant text with a teal left edge.
``UserMessage`` renders user input with a gold left edge.
The alternating edge colors create a scannable dialogue rhythm.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from clou.ui.rendering.markdown_cache import md_to_text
from clou.ui.theme import PALETTE

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_GOLD_DIM_HEX = PALETTE["accent-gold"].dim().to_hex()
_TEAL_DIM_HEX = PALETTE["accent-teal"].dim().to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_SURFACE_HEX = PALETTE["surface"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()


class MarkdownMessage(Static):
    """Assistant message — the system's voice.

    Left teal edge identifies the speaker in peripheral vision.
    Re-renders Markdown on resize so text wrapping stays correct.
    Background lifts slightly off surface-deep to create figure/ground
    separation from tool activity substrate.
    """

    DEFAULT_CSS = f"""
    MarkdownMessage {{
        border-left: tall {_TEAL_DIM_HEX};
        background: {_SURFACE_HEX};
        padding: 1 1 1 2;
        margin: 1 0 0 0;
    }}
    """

    def __init__(self, source: str, *, classes: str = "") -> None:
        super().__init__("", classes=classes)
        self._source = source

    def render(self) -> Text:
        parent = self.parent
        if parent is not None:
            w = parent.content_size.width or parent.size.width
            w = max(40, (w or 80) - 4)
        else:
            w = 76
        return md_to_text(self._source, w)


class UserMessage(Static):
    """User message — the human's voice.

    Gold left edge mirrors the assistant's teal edge, creating a
    scannable dialogue rhythm: gold-teal-gold-teal in peripheral vision.
    Raised background distinguishes intent from response.
    """

    DEFAULT_CSS = f"""
    UserMessage {{
        width: 100%;
        border-left: tall {_GOLD_DIM_HEX};
        background: {_SURFACE_RAISED_HEX};
        padding: 1 1 1 2;
        margin: 1 0 0 0;
    }}
    """

    def __init__(self, text: str, *, queued: bool = False, classes: str = "") -> None:
        super().__init__("", classes=classes)
        self._text = text
        self._queued = queued

    def mark_active(self) -> None:
        """Transition from queued to active — model picked up the message."""
        if self._queued:
            self._queued = False
            self.refresh()

    def render(self) -> Text:
        if self._queued:
            result = Text(f"\u203a {self._text}", style=f"bold {_GOLD_DIM_HEX}")
            result.append("  queued", style=f"italic {_DIM_HEX}")
            return result
        return Text(f"\u203a {self._text}", style=f"bold {_GOLD_HEX}")
