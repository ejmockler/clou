"""Drag-to-scroll mixin — auto-scroll a VerticalScroll when dragging near edges."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget

_DRAG_SCROLL_EDGE = 3  # rows from container edge to trigger auto-scroll
_DRAG_SCROLL_INTERVAL = 0.05  # seconds between scroll steps during drag
_DRAG_SCROLL_SPEED = 2  # lines per scroll step


class DragScrollMixin(Widget):
    """Mixin that auto-scrolls a ``#history`` VerticalScroll when dragging near edges."""

    _drag_scroll_timer: Timer | None
    _drag_scroll_direction: int

    def _init_drag_scroll(self) -> None:
        """Initialize drag-scroll state — call from __init__."""
        self._drag_scroll_timer = None
        self._drag_scroll_direction = 0

    def _get_scroll_target(self) -> VerticalScroll:
        """Return the scrollable container. Override to customize."""
        return self.query_one("#history", VerticalScroll)

    def on_mouse_move(self, event) -> None:  # noqa: ANN001 (MouseMove)
        """Auto-scroll history when dragging near top/bottom edge."""
        if not event.button:
            self._stop_drag_scroll()
            return
        try:
            history = self._get_scroll_target()
        except LookupError:
            return
        region = history.region
        y = event.screen_y
        if y <= region.y + _DRAG_SCROLL_EDGE:
            self._start_drag_scroll(-1)
        elif y >= region.y + region.height - _DRAG_SCROLL_EDGE:
            self._start_drag_scroll(1)
        else:
            self._stop_drag_scroll()

    def on_mouse_up(self, event) -> None:  # noqa: ANN001 (MouseUp)
        """End drag — stop any auto-scrolling."""
        self._stop_drag_scroll()

    def _start_drag_scroll(self, direction: int) -> None:
        """Begin or update continuous edge-scroll in the given direction."""
        self._drag_scroll_direction = direction
        if self._drag_scroll_timer is None:
            self._drag_scroll_timer = self.set_interval(
                _DRAG_SCROLL_INTERVAL, self._drag_scroll_tick
            )
            self._drag_scroll_tick()  # immediate first step

    def _drag_scroll_tick(self) -> None:
        """One step of edge-scroll."""
        try:
            history = self._get_scroll_target()
        except LookupError:
            self._stop_drag_scroll()
            return
        history.scroll_relative(
            y=self._drag_scroll_direction * _DRAG_SCROLL_SPEED, animate=False
        )

    def _stop_drag_scroll(self) -> None:
        """Cancel the edge-scroll timer."""
        if self._drag_scroll_timer is not None:
            self._drag_scroll_timer.stop()
            self._drag_scroll_timer = None
        self._drag_scroll_direction = 0
