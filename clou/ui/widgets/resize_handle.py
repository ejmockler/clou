"""Resize handle — drag to adjust the conversation/task-graph split.

A one-row horizontal bar between the conversation widget and the task graph.
Visible only in BREATH and DECISION modes. On mouse-drag, it mutates the
conversation widget's ``styles.height`` / ``styles.max_height`` directly,
mirroring the pattern used by ``PromptInput._auto_resize`` in
``clou/ui/widgets/prompt_input.py``.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.events import MouseDown, MouseMove, MouseUp
from textual.widget import Widget

from clou.ui.theme import PALETTE

# ── Color constants (resolved from the PALETTE, matches clou.tcss vars) ──
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()
_SURFACE_OVERLAY_HEX = PALETTE["surface-overlay"].to_hex()
_TEXT_MUTED_HEX = PALETTE["text-muted"].to_hex()
_TEXT_DIM_HEX = PALETTE["text-dim"].to_hex()
_ACCENT_GOLD_HEX = PALETTE["accent-gold"].to_hex()

#: Minimum rows to keep for the conversation (prompt + a line of context).
_MIN_CONV_HEIGHT: int = 4
#: Minimum rows to reserve for task-graph + breath-widget + status-bar.
_MIN_LOWER_HEIGHT: int = 6


class ResizeHandle(Widget):
    """A 1-row horizontal bar; drag to resize the conversation/task-graph split."""

    DEFAULT_CSS = f"""
    ResizeHandle {{
        display: none;
        height: 1;
        background: {_SURFACE_RAISED_HEX};
        color: {_TEXT_MUTED_HEX};
    }}
    ResizeHandle:hover {{
        color: {_TEXT_DIM_HEX};
        background: {_SURFACE_OVERLAY_HEX};
    }}
    ResizeHandle.-dragging {{
        color: {_ACCENT_GOLD_HEX};
        background: {_SURFACE_OVERLAY_HEX};
    }}
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._dragging: bool = False
        self._drag_start_screen_y: int = 0
        self._drag_start_conv_height: int = 0

    def render(self) -> RenderResult:
        """Render as a row of dashes that spans the widget width."""
        width = max(0, self.size.width)
        return Text("\u254c" * width, no_wrap=True, overflow="crop")

    # -- drag lifecycle -------------------------------------------------

    def on_mouse_down(self, event: MouseDown) -> None:
        """Begin a resize drag — capture the mouse and snapshot current height."""
        try:
            conv = self.app.query_one("#conversation")
        except LookupError:
            return
        self._dragging = True
        self._drag_start_screen_y = event.screen_y
        self._drag_start_conv_height = conv.size.height
        self.capture_mouse(True)
        self.add_class("-dragging")
        event.stop()

    def on_mouse_move(self, event: MouseMove) -> None:
        """Continuously apply the new height while dragging."""
        if not self._dragging:
            return
        try:
            conv = self.app.query_one("#conversation")
        except LookupError:
            return
        delta = event.screen_y - self._drag_start_screen_y
        screen_h = self.app.size.height
        max_conv = max(_MIN_CONV_HEIGHT, screen_h - _MIN_LOWER_HEIGHT)
        new_h = max(
            _MIN_CONV_HEIGHT,
            min(max_conv, self._drag_start_conv_height + delta),
        )
        # Persist on app so the value survives BREATH→DECISION flips.
        self.app._conversation_override_height = new_h
        conv.styles.height = new_h
        conv.styles.max_height = new_h

    def on_mouse_up(self, event: MouseUp) -> None:
        """Release the mouse and clear drag state."""
        if self._dragging:
            self._dragging = False
            self.capture_mouse(False)
            self.remove_class("-dragging")
            event.stop()
