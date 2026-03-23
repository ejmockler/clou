"""WakeIndicator — the system's first sign of life.

Two-part widget: scrollable activity text above, animated wave below.
Activity lines are mounted as Static widgets that wrap naturally.
The wave uses render_line for per-cell animated teal light.

Shown during initialization while the supervisor orients itself.
Replaced by the gold › prompt when the greeting arrives.
"""

from __future__ import annotations

import math

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from clou.ui.theme import OklchColor

# ── OKLCH teal — saturated, for wave ─────────────────────────────
_TEAL_HUE: float = 180.0
_TEAL_CHROMA: float = 0.10
_BG_L: float = 0.13          # surface-deep
_PEAK_L: float = 0.75        # wave crest

# ── Block characters by height fraction ───────────────────────────
_BLOCKS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

# ── Animation ─────────────────────────────────────────────────────
_FPS: int = 24
_SPEED: float = 0.18         # wave traversals per second
_BREATH_PERIOD: float = 4.5  # same as the breath widget

# ── Wave shimmer ─────────────────────────────────────────────────
_SHIMMER_AMP: float = 0.03
_SHIMMER_WAVELENGTH: float = 20.0
_SHIMMER_SPEED: float = 0.8

# ── Layout ───────────────────────────────────────────────────────
_MAX_LINES: int = 50          # generous — scrollable now

# ── exp(sin) normalization ────────────────────────────────────────
_EXP_NEG1: float = math.exp(-1)
_EXP_1: float = math.exp(1)
_EXP_RANGE: float = _EXP_1 - _EXP_NEG1

# ── Wave LUT (256 lightness levels → RGB) ─────────────────────────
_TEAL_LUT: list[tuple[int, int, int]] | None = None

# ── Text color — computed once from OKLCH ────────────────────────
_TEXT_HEX: str | None = None


def _ensure_lut() -> list[tuple[int, int, int]]:
    global _TEAL_LUT
    if _TEAL_LUT is None:
        _TEAL_LUT = []
        for i in range(256):
            col = OklchColor(i / 255.0, _TEAL_CHROMA, _TEAL_HUE)
            h = col.to_hex()
            _TEAL_LUT.append((int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)))
    return _TEAL_LUT


def _text_hex() -> str:
    """Teal-tinted dim text — computed once."""
    global _TEXT_HEX
    if _TEXT_HEX is None:
        _TEXT_HEX = OklchColor(0.52, 0.025, _TEAL_HUE).to_hex()
    return _TEXT_HEX


def _norm(raw: float) -> float:
    """Normalize an exp(sin(x)) value to [0, 1]."""
    return (raw - _EXP_NEG1) / _EXP_RANGE


class WakeWave(Widget):
    """Traveling teal wave — per-cell render_line animation."""

    DEFAULT_CSS = """
    WakeWave {
        height: 1;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._phase: float = 0.0
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0 / _FPS, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._phase += 1.0 / _FPS
        self.refresh()

    def stop(self) -> None:
        """Stop animation."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if w < 1 or y != 0:
            return Strip.blank(w)

        lut = _ensure_lut()
        t = self._phase
        TWO_PI = 2.0 * math.pi

        # Temporal breath envelope.
        breath = _norm(math.exp(math.sin(TWO_PI * t / _BREATH_PERIOD)))
        breath = 0.35 + 0.65 * breath

        segments: list[Segment] = []
        for i in range(w):
            x = i / max(w - 1, 1)

            # Spatial wave: exp(sin(2π(x - vt))) — the breathing curve as shape.
            wave = _norm(math.exp(math.sin(TWO_PI * (x - t * _SPEED))))

            # Shimmer: sub-threshold traveling sine.
            shimmer = _SHIMMER_AMP * math.sin(
                TWO_PI * (i / _SHIMMER_WAVELENGTH - t * _SHIMMER_SPEED)
            )

            intensity = max(0.0, min(1.0, wave * breath + shimmer))

            if intensity < 0.06:
                segments.append(Segment(" "))
                continue

            block_idx = max(1, min(8, round(intensity * 8)))
            lightness = _BG_L + (_PEAK_L - _BG_L) * intensity
            lut_idx = max(0, min(255, round(lightness * 255)))
            r, g, b = lut[lut_idx]

            segments.append(
                Segment(_BLOCKS[block_idx], Style(color=f"#{r:02x}{g:02x}{b:02x}"))
            )

        return Strip(segments, w)


class WakeIndicator(Widget):
    """Startup activity text + animated wave.

    Activity mounts as wrapping Static widgets in a scrollable area.
    Wave renders per-cell at the bottom.
    """

    DEFAULT_CSS = """
    WakeIndicator {
        layout: vertical;
        height: 1;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._lines: list[tuple[str, float]] = []  # (text, _) — kept for history promotion

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wake-activity"):
            yield Static("", id="wake-spacer")
        yield WakeWave(id="wake-wave")

    @property
    def _timer(self) -> Timer | None:
        """Wave timer — exposed for test compatibility."""
        try:
            return self.query_one(WakeWave)._timer
        except LookupError:
            return None

    def stop(self) -> None:
        """Stop animation and hide."""
        try:
            self.query_one(WakeWave).stop()
        except LookupError:
            pass
        self.display = False

    def add_line(self, text: str) -> None:
        """Mount an activity line as a wrapping Static widget."""
        self._lines.append((text, 0.0))
        if len(self._lines) > _MAX_LINES:
            self._lines = self._lines[-_MAX_LINES:]
        try:
            activity = self.query_one("#wake-activity", VerticalScroll)
            color = _text_hex()
            activity.mount(Static(Text(text, style=color), classes="wake-line"))
            activity.scroll_end(animate=False)
        except LookupError:
            pass
