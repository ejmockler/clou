"""Task graph widget -- renders compose.py tasks with per-character OKLCH coloring.

Displays task rows grouped by dependency layer, with breathing status icons
and active-row shimmer.  Reads from ``TaskGraphModel`` (the Phase 1 data
layer).  Supports keyboard navigation and drill-down expansion (Phase 3).

Public API:
    TaskGraphWidget  -- Widget subclass for rendering task state
"""

from __future__ import annotations

import time
from typing import ClassVar

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual.events import Key
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from clou.ui.task_graph import TaskGraphModel
from clou.ui.theme import PALETTE, OklchColor, breath_modulate
from clou.ui.widgets.breath import (
    _ARRIVAL_DURATION,
    _LINGER_DURATION,
    _SETTLE_DURATION,
    _STAGE_LUMINANCE,
    compute_shimmer,
    luminance_to_rgb,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Status icons mapped to task status strings.
STATUS_ICONS: dict[str, str] = {
    "pending": "\u25cb",   # ○
    "active": "\u25c9",    # ◉
    "complete": "\u2713",  # ✓
    "failed": "\u2717",    # ✗
}

#: PALETTE token for each status.
_STATUS_PALETTE: dict[str, str] = {
    "pending": "text-muted",
    "active": "accent-gold",
    "complete": "accent-teal",
    "failed": "accent-rose",
}

#: Column widths for task row layout.
ICON_COL_WIDTH: int = 3        # icon + space + space
TASK_NAME_MAX: int = 40        # left-aligned, truncated
TOOL_COUNT_WIDTH: int = 11     # " [nn tools]" or blank
LAST_TOOL_MIN: int = 1         # at least 1 char for last_tool

#: Neutral text hue/chroma (from palette: text-dim).
_TEXT_HUE: float = 250.0
_TEXT_CHROMA: float = 0.008

#: Luminance for non-active task names.
_TEXT_DIM_L: float = 0.60

#: Luminance for muted elements (tool count, last tool, headers).
_TEXT_MUTED_L: float = 0.45

#: Luminance boost for focused row.
_FOCUS_BOOST: float = 0.15

# ---------------------------------------------------------------------------
# Row map types
# ---------------------------------------------------------------------------

# Each entry in _row_map is (row_type, data):
#   ("header", layer_index)            -- phase group header
#   ("task", task_name)                -- a task row
#   ("spacer", None)                   -- blank line between groups
#   ("tool_call", (task_name, index))  -- drill-down tool call line
#   ("summary", task_name)             -- drill-down summary line
_RowEntry = tuple[str, object]

# ---------------------------------------------------------------------------
# Pre-computed status icon RGB cache
# ---------------------------------------------------------------------------

_STATUS_RGB_CACHE: dict[str, tuple[int, int, int]] = {}


def _status_icon_rgb(status: str) -> tuple[int, int, int]:
    """Return cached (R, G, B) for a static status icon color."""
    if status not in _STATUS_RGB_CACHE:
        token = _STATUS_PALETTE.get(status, "text-muted")
        col = PALETTE[token]
        hex_str = col.to_hex()
        r = int(hex_str[1:3], 16)
        g = int(hex_str[3:5], 16)
        b = int(hex_str[5:7], 16)
        _STATUS_RGB_CACHE[status] = (r, g, b)
    return _STATUS_RGB_CACHE[status]


def _oklch_to_rgb(col: OklchColor) -> tuple[int, int, int]:
    """Convert an OklchColor to (R, G, B)."""
    hex_str = col.to_hex()
    r = int(hex_str[1:3], 16)
    g = int(hex_str[3:5], 16)
    b = int(hex_str[5:7], 16)
    return (r, g, b)


# ---------------------------------------------------------------------------
# Expansion animation helpers
# ---------------------------------------------------------------------------


def _expansion_luminance(expanded_at: float, now: float) -> float:
    """Compute luminance for an expansion drill-down line.

    Follows the same lifecycle as BreathEventItem:
      arrival  (0..100ms):  0.88
      linger   (100ms..2s): 0.60
      settle   (2s..4s):    0.45
      resting  (4s+):       0.45
    """
    age = now - expanded_at
    if age < _ARRIVAL_DURATION:
        return _STAGE_LUMINANCE["arrival"]
    if age < _LINGER_DURATION:
        return _STAGE_LUMINANCE["linger"]
    if age < _SETTLE_DURATION:
        return _STAGE_LUMINANCE["settle"]
    return _STAGE_LUMINANCE["resting"]


# ---------------------------------------------------------------------------
# TaskGraphWidget
# ---------------------------------------------------------------------------


class TaskGraphWidget(Widget):
    """Renders task rows with per-character OKLCH coloring and breathing icons.

    The ``breath_phase`` reactive is driven by the app's animation timer
    (typically 24 FPS).  The ``shimmer_active`` reactive enables the
    traveling luminance wave on active task rows.

    Supports keyboard navigation (Up/Down/Enter/Escape) and drill-down
    expansion of focused tasks to show tool calls and summary.
    """

    #: Allow Textual to route key events to this widget.
    can_focus = True

    #: Current breath animation value [0, 1], set by the app timer.
    breath_phase: reactive[float] = reactive(0.0)

    #: Whether shimmer is active (any task is in "active" status).
    shimmer_active: reactive[bool] = reactive(False)

    #: Palette tokens used by this widget (for test introspection).
    PALETTE_TOKENS_USED: ClassVar[frozenset[str]] = frozenset(
        _STATUS_PALETTE.values()
    ) | frozenset({"text-dim"})

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._model: TaskGraphModel | None = None
        self._row_map: list[_RowEntry] = []
        self._frame_time: float = 0.0

        #: Which task row has focus (-1 = none).
        self._focused_index: int = -1

        #: Task names currently expanded for drill-down.
        self._expanded: set[str] = set()

        #: Expansion timestamps: task_name -> monotonic time when expanded.
        self._expansion_states: dict[str, float] = {}

    # -- public API ----------------------------------------------------------

    def update_model(self, model: TaskGraphModel) -> None:
        """Set or replace the data model and rebuild the row map."""
        self._model = model
        self._rebuild_row_map()
        # Update shimmer based on whether any task is active.
        self.shimmer_active = any(
            ts.status == "active" for ts in model.task_states.values()
        )
        self.refresh()

    # -- reactive watchers ---------------------------------------------------

    def watch_breath_phase(self, value: float) -> None:
        """Trigger a repaint whenever the breath phase changes."""
        self._frame_time = time.monotonic()
        self.refresh()

    # -- focus helpers -------------------------------------------------------

    def _task_names_ordered(self) -> list[str]:
        """Return task names in display order (from row map)."""
        return [
            str(data) for row_type, data in self._row_map if row_type == "task"
        ]

    def _task_count(self) -> int:
        """Return the number of task rows in the current row map."""
        return sum(1 for rt, _ in self._row_map if rt == "task")

    def _focused_task_name(self) -> str | None:
        """Return the task name at _focused_index, or None."""
        if self._focused_index < 0:
            return None
        task_names = self._task_names_ordered()
        if self._focused_index < len(task_names):
            return task_names[self._focused_index]
        return None

    def _row_index_for_task(self, task_name: str) -> int | None:
        """Find the row_map index for a given task name."""
        for i, (rt, data) in enumerate(self._row_map):
            if rt == "task" and data == task_name:
                return i
        return None

    # -- key handling --------------------------------------------------------

    def on_key(self, event: Key) -> None:
        """Handle keyboard navigation for task focus and drill-down."""
        if event.key == "down":
            self._move_focus(1)
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            self._move_focus(-1)
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self._toggle_expand()
            event.prevent_default()
            event.stop()
        elif event.key == "escape":
            self._defocus()
            event.prevent_default()
            event.stop()

    def on_focus(self, _event: object) -> None:
        """When widget gains focus, set _focused_index to 0 if tasks exist."""
        if self._focused_index < 0 and self._task_count() > 0:
            self._focused_index = 0
            self.refresh()

    def _move_focus(self, delta: int) -> None:
        """Move focus by delta, clamping to valid range."""
        count = self._task_count()
        if count == 0:
            return
        if self._focused_index < 0:
            self._focused_index = 0
        else:
            self._focused_index = max(
                0, min(count - 1, self._focused_index + delta)
            )
        self.refresh()

    def _toggle_expand(self) -> None:
        """Toggle expansion of the focused task."""
        task_name = self._focused_task_name()
        if task_name is None:
            return
        if task_name in self._expanded:
            self._expanded.discard(task_name)
            self._expansion_states.pop(task_name, None)
        else:
            self._expanded.add(task_name)
            self._expansion_states[task_name] = time.monotonic()
        self._rebuild_row_map()
        self.refresh()

    def _defocus(self) -> None:
        """Remove focus from this widget."""
        self._focused_index = -1
        self.refresh()

    # -- row map construction ------------------------------------------------

    def _rebuild_row_map(self) -> None:
        """Compute the flat list of row entries from the model's layers.

        Includes drill-down rows (tool_call, summary) for expanded tasks.
        """
        rows: list[_RowEntry] = []
        if self._model is None:
            self._row_map = rows
            return

        for layer_idx, layer in enumerate(self._model.layers):
            if not layer:
                continue  # Skip empty layers.
            if layer_idx > 0 and rows:
                rows.append(("spacer", None))
            rows.append(("header", layer_idx))
            for task_name in layer:
                rows.append(("task", task_name))
                # If expanded, add drill-down rows.
                if task_name in self._expanded and self._model is not None:
                    state = self._model.task_states.get(task_name)
                    if state is not None:
                        for tc_idx in range(len(state.tool_calls)):
                            rows.append(("tool_call", (task_name, tc_idx)))
                        if state.summary:
                            rows.append(("summary", task_name))

        self._row_map = rows

    # -- rendering -----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render a single line with per-character OKLCH coloring."""
        width = self.size.width
        if width <= 0:
            return Strip([Segment("", Style())])

        # Out-of-bounds or no row map entry -> blank line.
        if y < 0 or y >= len(self._row_map):
            return Strip([Segment(" " * width, Style())], width)

        row_type, data = self._row_map[y]

        if row_type == "spacer":
            return Strip([Segment(" " * width, Style())], width)

        if row_type == "header":
            return self._render_header(width, data)  # type: ignore[arg-type]

        if row_type == "task":
            task_name = str(data)
            is_focused = self._is_task_focused(task_name)
            return self._render_task(width, task_name, focus_boost=is_focused)

        if row_type == "tool_call":
            task_name, tc_idx = data  # type: ignore[misc]
            return self._render_tool_call(
                width, str(task_name), int(tc_idx)
            )

        if row_type == "summary":
            return self._render_summary(width, str(data))

        # Fallback: blank.
        return Strip([Segment(" " * width, Style())], width)

    def _is_task_focused(self, task_name: str) -> bool:
        """Check if the given task is currently focused."""
        if self._focused_index < 0:
            return False
        focused_name = self._focused_task_name()
        return focused_name == task_name

    def _render_header(self, width: int, layer_index: int) -> Strip:
        """Render a phase group header line."""
        # Format: "-- Phase N --" centered, text-muted luminance.
        label = f"\u2500\u2500 Phase {layer_index + 1} \u2500\u2500"
        if len(label) < width:
            label = label + " " * (width - len(label))
        elif len(label) > width:
            label = label[:width]

        r, g, b = luminance_to_rgb(_TEXT_MUTED_L)
        style = Style(color=Color.from_rgb(r, g, b))
        segments = [Segment(ch, style) for ch in label]
        return Strip(segments, width)

    def _render_task(
        self, width: int, task_name: str, *, focus_boost: bool = False
    ) -> Strip:
        """Render a single task row with icon, name, tool count, last tool."""
        if self._model is None:
            return Strip([Segment(" " * width, Style())], width)

        state = self._model.task_states.get(task_name)
        if state is None:
            return Strip([Segment(" " * width, Style())], width)

        status = state.status
        icon = STATUS_ICONS.get(status, "\u25cb")
        is_active = status == "active"

        # Build the text columns.
        # Icon column: "  {icon}" (2-space indent + icon = 3 chars).
        icon_text = f"  {icon}"

        # Task name: space + left-aligned, truncated at TASK_NAME_MAX.
        display_name = task_name[:TASK_NAME_MAX].ljust(TASK_NAME_MAX)
        name_text = f" {display_name}"

        # Tool count: " [nn tools]" or blank.
        if state.tool_count > 0:
            tc_str = f" [{state.tool_count:>2} tools]"
        else:
            tc_str = " " * TOOL_COUNT_WIDTH

        # Last tool: remaining space, truncated, dim.
        last_tool_text = ""
        if state.last_tool:
            last_tool_text = f"  {state.last_tool}"

        # Compose full line.
        line = icon_text + name_text + tc_str + last_tool_text
        if len(line) < width:
            line = line + " " * (width - len(line))
        elif len(line) > width:
            line = line[:width]

        # Luminance boost for focus.
        boost = _FOCUS_BOOST if focus_boost else 0.0

        # Compute icon RGB.
        if is_active:
            # Active icon: breathe via breath_modulate.
            base_col = PALETTE["accent-gold"]
            modulated_l = breath_modulate(base_col.l, self.breath_phase)
            boosted_l = min(1.0, modulated_l + boost)
            breathing_col = OklchColor(boosted_l, base_col.c, base_col.h)
            icon_rgb = _oklch_to_rgb(breathing_col)
        elif focus_boost:
            # Non-active but focused: boost the icon color luminance.
            token = _STATUS_PALETTE.get(status, "text-muted")
            base_col = PALETTE[token]
            boosted_l = min(1.0, base_col.l + boost)
            boosted_col = OklchColor(boosted_l, base_col.c, base_col.h)
            icon_rgb = _oklch_to_rgb(boosted_col)
        else:
            icon_rgb = _status_icon_rgb(status)

        t = self._frame_time
        segments: list[Segment] = []

        # Column boundaries.
        icon_end = len(icon_text)              # end of icon column
        name_end = icon_end + len(name_text)   # end of name column

        for x in range(width):
            char = line[x]

            if x < icon_end:
                # Icon column: status color.
                r, g, b = icon_rgb
            elif x < name_end:
                # Task name: text-dim luminance, with shimmer if active.
                l_val = _TEXT_DIM_L + boost
                if is_active and self.shimmer_active:
                    l_val += compute_shimmer(x, t)
                l_val = max(0.0, min(1.0, l_val))
                r, g, b = luminance_to_rgb(l_val)
            else:
                # Tool count + last tool: text-muted luminance.
                l_val = _TEXT_MUTED_L + boost
                if is_active and self.shimmer_active:
                    l_val += compute_shimmer(x, t)
                l_val = max(0.0, min(1.0, l_val))
                r, g, b = luminance_to_rgb(l_val)

            style = Style(color=Color.from_rgb(r, g, b))
            segments.append(Segment(char, style))

        return Strip(segments, width)

    def _render_tool_call(
        self, width: int, task_name: str, tc_idx: int
    ) -> Strip:
        """Render a drill-down tool call line."""
        if self._model is None:
            return Strip([Segment(" " * width, Style())], width)

        state = self._model.task_states.get(task_name)
        if state is None or tc_idx >= len(state.tool_calls):
            return Strip([Segment(" " * width, Style())], width)

        tool_name, brief = state.tool_calls[tc_idx]
        text = f"    \u2192 {tool_name}: {brief}"
        if len(text) < width:
            text = text + " " * (width - len(text))
        elif len(text) > width:
            text = text[:width]

        # Compute luminance from expansion animation.
        now = time.monotonic()
        expanded_at = self._expansion_states.get(task_name, now)
        l_val = _expansion_luminance(expanded_at, now)

        r, g, b = luminance_to_rgb(l_val)
        style = Style(color=Color.from_rgb(r, g, b))
        segments = [Segment(ch, style) for ch in text]
        return Strip(segments, width)

    def _render_summary(self, width: int, task_name: str) -> Strip:
        """Render a drill-down summary line."""
        if self._model is None:
            return Strip([Segment(" " * width, Style())], width)

        state = self._model.task_states.get(task_name)
        if state is None or not state.summary:
            return Strip([Segment(" " * width, Style())], width)

        text = f"    \u21b3 {state.summary}"
        if len(text) < width:
            text = text + " " * (width - len(text))
        elif len(text) > width:
            text = text[:width]

        # Compute luminance from expansion animation.
        now = time.monotonic()
        expanded_at = self._expansion_states.get(task_name, now)
        l_val = _expansion_luminance(expanded_at, now)

        r, g, b = luminance_to_rgb(l_val)
        style = Style(color=Color.from_rgb(r, g, b))
        segments = [Segment(ch, style) for ch in text]
        return Strip(segments, width)
