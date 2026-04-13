"""Tool result disclosure — collapsible MCP / external tool result display.

Violet left edge identifies external tool results in peripheral vision
(parallel to teal for assistant, gold for user, green for edits, blue
for agents).
"""

from __future__ import annotations

import math

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from clou.ui.rendering.breathing import (
    BREATH_PERIOD,
    EXP_NEG1,
    EXP_RANGE,
    ensure_breath_lut,
)
from clou.ui.rendering.markdown_cache import md_to_text
from clou.ui.theme import PALETTE

_VIOLET_DIM_HEX = PALETTE["accent-violet"].dim().to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()

_GLYPH_TOOL = "\u25b8"     # ▸
_GLYPH_SUCCESS = "\u2713"  # ✓
_GLYPH_ERROR = "\u2717"    # ✗
_GLYPH_RUNNING = "\u25cf"  # ●


class ToolResultDisclosure(Static):
    """Collapsible tool result — violet left edge, starts expanded.

    Breathing dot while the tool is running; result content renders as
    markdown when expanded. Auto-collapses after settle threshold.
    """

    DEFAULT_CSS = f"""
    ToolResultDisclosure {{
        border-left: tall {_VIOLET_DIM_HEX};
        padding: 0 0 0 2;
        margin: 0;
    }}
    """

    _BREATH_FPS: int = 12

    def __init__(
        self,
        tool_use_id: str | None,
        description: str,
        *,
        classes: str = "",
    ) -> None:
        super().__init__("", classes=classes)
        self._tool_use_id = tool_use_id
        self._description = description
        self._result: str | None = None
        self._expanded: bool = False
        self._status: str = "running"
        self._phase: float = 0.0
        self._breath_timer: Timer | None = None

    def on_mount(self) -> None:
        if self._status == "running":
            self._breath_timer = self.set_interval(
                1.0 / self._BREATH_FPS, self._breath_tick,
            )

    def _breath_tick(self) -> None:
        self._phase += 1.0 / self._BREATH_FPS
        self.refresh()

    def _stop_breath(self) -> None:
        if self._breath_timer is not None:
            self._breath_timer.stop()
            self._breath_timer = None

    def complete(self, content: str, is_error: bool) -> None:
        """Fill in the tool result and transition from running."""
        self._stop_breath()
        self._result = content
        self._status = "error" if is_error else "success"
        # Auto-expand on completion if there's content
        if content and content.strip():
            self._expanded = True
        self.refresh()

    def on_click(self) -> None:
        if self._result:
            self._expanded = not self._expanded
            self.refresh()

    def render(self) -> Text:
        if self._status == "running":
            lut = ensure_breath_lut()
            raw = math.exp(math.sin(2.0 * math.pi * self._phase / BREATH_PERIOD))
            breath = (raw - EXP_NEG1) / EXP_RANGE
            idx = max(0, min(63, round(breath * 63)))
            glyph = Text(_GLYPH_RUNNING, style=lut[idx])
        elif self._status == "error":
            glyph = Text(_GLYPH_ERROR, style=_ROSE_HEX)
        else:
            glyph = Text(_GLYPH_SUCCESS, style=_DIM_HEX)
        result = Text()
        result.append(f"{_GLYPH_TOOL} {self._description}  ", style=_DIM_HEX)
        result.append_text(glyph)
        if self._expanded and self._result:
            width = self.container_size.width - 4 if self.container_size.width > 8 else 60
            rendered = md_to_text(self._result, width)
            result.append("\n")
            result.append_text(rendered)
        return result
