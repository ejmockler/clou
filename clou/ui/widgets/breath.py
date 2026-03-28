"""Breath widget — ambient activity display with temporal pacing.

The heart of clou's breathing conversation. Renders curated status lines
from coordinator activity with per-character color modulation via
``render_line``, breathing luminance animation, and shimmer.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouBreathEvent,
    ClouCoordinatorSpawned,
    ClouCycleComplete,
)
from clou.ui.theme import PALETTE, OklchColor, breath_modulate, cycle_color

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of events kept in the display buffer.
MAX_EVENTS: int = 20

#: Width of the left-hand cycle-type label column (characters).
LABEL_WIDTH: int = 10

#: Shimmer parameters (from visual-language §4).
SHIMMER_AMPLITUDE: float = 0.03
SHIMMER_WAVELENGTH: float = 20.0
SHIMMER_SPEED: float = 0.8

#: Event lifecycle thresholds (seconds).
_ARRIVAL_DURATION: float = 0.1  # 100 ms
_LINGER_DURATION: float = 2.0  # 2 s total from arrival
_SETTLE_DURATION: float = 4.0  # 4 s total from arrival

#: Base luminance for each lifecycle stage.
_STAGE_LUMINANCE: dict[str, float] = {
    "arrival": 0.88,  # text luminance — full brightness spark
    "linger": 0.60,  # text-dim — readable if you look
    "settle": 0.45,  # text-muted — joins ambient background
    "resting": 0.45,  # text-muted — part of history
}

#: Neutral text hue/chroma for breath-mode lines (from palette: text-dim).
_TEXT_HUE: float = 250.0
_TEXT_CHROMA: float = 0.008

# ---------------------------------------------------------------------------
# Pre-computed luminance → RGB lookup (neutral text hue)
# ---------------------------------------------------------------------------

_L_LUT: list[tuple[int, int, int]] = []


def _build_luminance_lut() -> list[tuple[int, int, int]]:
    """Build a 256-entry lookup: index → (R, G, B) for the neutral text hue."""
    lut: list[tuple[int, int, int]] = []
    for i in range(256):
        lightness = i / 255.0
        col = OklchColor(lightness, _TEXT_CHROMA, _TEXT_HUE)
        hex_str = col.to_hex()  # '#rrggbb'
        r = int(hex_str[1:3], 16)
        g = int(hex_str[3:5], 16)
        b = int(hex_str[5:7], 16)
        lut.append((r, g, b))
    return lut


def _ensure_lut() -> list[tuple[int, int, int]]:
    global _L_LUT
    if not _L_LUT:
        _L_LUT = _build_luminance_lut()
    return _L_LUT


def luminance_to_rgb(lightness: float) -> tuple[int, int, int]:
    """Map a lightness value [0, 1] to (R, G, B) via the pre-computed LUT."""
    lut = _ensure_lut()
    idx = max(0, min(255, round(lightness * 255)))
    return lut[idx]


# ---------------------------------------------------------------------------
# Cycle-type label → RGB (pre-resolved from theme)
# ---------------------------------------------------------------------------

_CYCLE_RGB_CACHE: dict[str, tuple[int, int, int]] = {}


def _cycle_type_rgb(cycle_type: str) -> tuple[int, int, int]:
    """Return (R, G, B) for a cycle-type label colour, with caching."""
    if cycle_type not in _CYCLE_RGB_CACHE:
        try:
            hex_str = cycle_color(cycle_type)
        except ValueError:
            hex_str = PALETTE["text-dim"].to_hex()
        r = int(hex_str[1:3], 16)
        g = int(hex_str[3:5], 16)
        b = int(hex_str[5:7], 16)
        _CYCLE_RGB_CACHE[cycle_type] = (r, g, b)
    return _CYCLE_RGB_CACHE[cycle_type]


# ---------------------------------------------------------------------------
# BreathEventItem
# ---------------------------------------------------------------------------


@dataclass
class BreathEventItem:
    """A single curated status line in the breath widget."""

    text: str
    cycle_type: str
    phase: str | None = None
    timestamp: float = field(default_factory=time.monotonic)
    luminance_state: str = "arrival"

    def update_state(self, now: float) -> None:
        """Progress through the arrival → linger → settle → resting lifecycle."""
        age = now - self.timestamp
        if age < _ARRIVAL_DURATION:
            self.luminance_state = "arrival"
        elif age < _LINGER_DURATION:
            self.luminance_state = "linger"
        elif age < _SETTLE_DURATION:
            self.luminance_state = "settle"
        else:
            self.luminance_state = "resting"


# ---------------------------------------------------------------------------
# Shimmer calculation
# ---------------------------------------------------------------------------


def compute_shimmer(x: int, t: float) -> float:
    """Return the shimmer luminance offset at column *x* and time *t*.

    The shimmer is a sub-threshold traveling sine wave.

    Returns a value in [-SHIMMER_AMPLITUDE, +SHIMMER_AMPLITUDE].
    """
    return SHIMMER_AMPLITUDE * math.sin(x / SHIMMER_WAVELENGTH - t * SHIMMER_SPEED)


# ---------------------------------------------------------------------------
# BreathWidget
# ---------------------------------------------------------------------------


class BreathWidget(Widget):
    """Ambient activity display with breathing animation and shimmer.

    Renders curated status lines using per-character colour modulation
    via ``render_line``.  The ``breath_phase`` reactive is driven by the
    app's animation timer (typically at 24 FPS).
    """

    #: Current breath animation value [0, 1], set by the app timer.
    breath_phase: reactive[float] = reactive(0.0)

    #: Whether shimmer is active (agents working).
    shimmer_active: reactive[bool] = reactive(False)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._events: list[BreathEventItem] = []
        self._frame_time: float = 0.0
        self._active_agent_count: int = 0

    # -- reactive watchers ---------------------------------------------------

    def watch_breath_phase(self, value: float) -> None:
        """Trigger a repaint whenever the breath phase changes."""
        self._frame_time = time.monotonic()
        self._update_event_states()
        self.refresh()

    # -- message handlers ----------------------------------------------------

    def on_clou_breath_event(self, message: ClouBreathEvent) -> None:
        """Handle a curated status line from coordinator activity."""
        self._add_event(
            text=message.text,
            cycle_type=message.cycle_type,
            phase=message.phase,
        )

    def on_clou_coordinator_spawned(self, message: ClouCoordinatorSpawned) -> None:
        """Reset agent tracking for new coordinator session."""
        self._active_agent_count = 0
        self.shimmer_active = False

    def on_clou_agent_spawned(self, message: ClouAgentSpawned) -> None:
        """Handle an agent dispatch — one curated line per spawn."""
        self._active_agent_count += 1
        self.shimmer_active = True
        desc = message.description.strip()[:60] or "agent"
        self._add_event(text=f"dispatching  {desc}", cycle_type="EXECUTE")

    def on_clou_agent_progress(self, message: ClouAgentProgress) -> None:
        """Agent mid-flight progress — ambient only, no visible line.

        The shimmer is the progress indicator. Per-tool-call lines are a
        log stream, not a breath (interface.md §4).
        """

    def on_clou_agent_complete(self, message: ClouAgentComplete) -> None:
        """Handle an agent completion — one curated line."""
        self._active_agent_count = max(0, self._active_agent_count - 1)
        if self._active_agent_count == 0:
            self.shimmer_active = False
        summary = message.summary.strip()[:60] if message.summary else message.status
        self._add_event(text=f"{summary}  {message.status}", cycle_type="EXECUTE")

    def on_clou_cycle_complete(self, message: ClouCycleComplete) -> None:
        """Handle a cycle completion event."""
        self._add_event(
            text=(
                f"cycle #{message.cycle_num}  "
                f"{message.cycle_type} complete  "
                f"\u2192 {message.next_step}"
            ),
            cycle_type=message.cycle_type,
        )

    # -- internal ------------------------------------------------------------

    def _add_event(
        self,
        text: str,
        cycle_type: str,
        phase: str | None = None,
    ) -> None:
        """Append an event, capping the buffer at MAX_EVENTS."""
        self._events.append(
            BreathEventItem(text=text, cycle_type=cycle_type, phase=phase)
        )
        if len(self._events) > MAX_EVENTS:
            self._events = self._events[-MAX_EVENTS:]
        self.refresh()

    def _update_event_states(self) -> None:
        """Progress all event lifecycle states based on current time."""
        now = time.monotonic()
        for event in self._events:
            event.update_state(now)

    # -- rendering -----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render a single line with per-character breathing + shimmer."""
        width = self.size.width
        if width <= 0:
            return Strip([Segment("", Style())])

        # Map y to an event (newest at bottom, reversed display).
        event_index = len(self._events) - 1 - y
        if event_index < 0 or event_index >= len(self._events):
            # Empty line — explicit Style() required for Textual opacity.
            return Strip([Segment(" " * width, Style())], width)

        event = self._events[event_index]

        # Compose the full text line: label + description.
        label = event.cycle_type.upper().ljust(LABEL_WIDTH)[:LABEL_WIDTH]
        desc = event.text
        line = label + desc
        # Pad or truncate to width.
        if len(line) < width:
            line = line + " " * (width - len(line))
        elif len(line) > width:
            line = line[:width]

        # Get base luminance from event lifecycle stage.
        base_l = _STAGE_LUMINANCE.get(event.luminance_state, 0.45)

        # Breathing modulation (15% amplitude).
        breath_l = breath_modulate(base_l, self.breath_phase)

        # Cycle-type label RGB.
        label_rgb = _cycle_type_rgb(event.cycle_type)

        t = self._frame_time
        segments: list[Segment] = []

        for x in range(width):
            char = line[x]

            if x < LABEL_WIDTH:
                # Cycle label: use cycle colour, modulated by breath.
                r, g, b = label_rgb
                # Scale brightness by breath ratio.
                scale = breath_l / max(base_l, 0.01)
                r = max(0, min(255, round(r * scale)))
                g = max(0, min(255, round(g * scale)))
                b = max(0, min(255, round(b * scale)))
            else:
                # Description text: neutral hue, breathing + shimmer.
                l_val = breath_l
                if self.shimmer_active:
                    l_val += compute_shimmer(x, t)
                l_val = max(0.0, min(1.0, l_val))
                r, g, b = luminance_to_rgb(l_val)

            style = Style(color=Color.from_rgb(r, g, b))
            segments.append(Segment(char, style))

        return Strip(segments)
