"""WakeIndicator — the system's first sign of life.

Two-part widget: scrollable activity text above, animated wave below.
Activity lines are mounted as Static widgets that wrap naturally.
The wave uses render_line for multi-row per-cell animated teal light
with dual-wave interference, emergence from stillness, depth-graded
chromatic color, surface glow, and block-character vertical resolution.

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

from clou.ui.theme import OklchColor, PALETTE

# ── Constants ────────────────────────────────────────────────────
TWO_PI: float = 2.0 * math.pi

# ── OKLCH wave palette ──────────────────────────────────────────
_CREST_HUE: float = 176.0       # warm cyan-green at surface
_BASE_HUE: float = 186.0        # cool blue-teal at depth
_CREST_CHROMA: float = 0.12     # saturated at surface
_BASE_CHROMA: float = 0.055     # stays recognizably teal in the deep
_CREST_L: float = 0.72          # matches accent-teal luminance
_BASE_L: float = 0.20           # visible above background
_BG_L: float = 0.13             # surface-deep
_BG_HEX: str = PALETTE["surface-deep"].to_hex()

# ── Depth attenuation (Beer-Lambert) ───────────────────────────
_DEPTH_COEFF: float = 1.8       # exponential falloff coefficient

# ── Dual-wave parameters ────────────────────────────────────────
_PRIMARY_SPEED: float = 0.11     # rightward traversals per second
_SECONDARY_FREQ: float = 1.618   # golden ratio — never-repeating beat
_SECONDARY_SPEED: float = 0.068  # slower, leftward
_PRIMARY_WEIGHT: float = 0.62
_SECONDARY_WEIGHT: float = 0.38

# ── Emergence from stillness ────────────────────────────────────
_EMERGENCE_TAU: float = 1.6      # exponential rise time constant (s)
_EMERGENCE_SHAPING: float = 2.5  # power curve for perceptual linearity

# ── Breathing envelope ──────────────────────────────────────────
_BREATH_PERIOD: float = 4.5      # shared with breath widget
_BREATH_FLOOR: float = 0.50      # minimum envelope at exhale
_BREATH_CEIL: float = 1.0        # maximum at inhale

# ── Shimmer ─────────────────────────────────────────────────────
_SHIMMER_AMP: float = 0.018
_SHIMMER_WAVELENGTH: float = 13.0
_SHIMMER_SPEED: float = 1.2

# ── Surface glow (achromatic — matches background hue) ─────────
_GLOW_REACH: float = 3.0        # rows of glow above surface
_GLOW_PEAK_L: float = 0.045     # max luminance offset above BG_L

# ── Deep floor ──────────────────────────────────────────────────
_FLOOR_HEIGHT: float = 0.025     # ever-present faint base glow

# ── Animation ───────────────────────────────────────────────────
_FPS: int = 24

# ── Block characters (by fill fraction, bottom-up) ─────────────
_BLOCKS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

# ── exp(sin) normalization ──────────────────────────────────────
_EXP_NEG1: float = math.exp(-1.0)
_EXP_1: float = math.exp(1.0)
_EXP_RANGE: float = _EXP_1 - _EXP_NEG1

# ── Layout ──────────────────────────────────────────────────────
_MAX_LINES: int = 50
_TEAL_HUE: float = 180.0

# ── Pre-computed LUTs ───────────────────────────────────────────
_BODY_LUT: list[tuple[int, int, int]] | None = None
_GLOW_LUT: list[str] | None = None
_GLOW_STEPS: int = 16
_TEXT_HEX: str | None = None


def _norm(raw: float) -> float:
    """Normalize an exp(sin(x)) value to [0, 1]."""
    return (raw - _EXP_NEG1) / _EXP_RANGE


def _ensure_body_lut() -> list[tuple[int, int, int]]:
    """256-entry depth LUT: index 0 = crest (bright), 255 = deep (dark)."""
    global _BODY_LUT
    if _BODY_LUT is None:
        _BODY_LUT = []
        for i in range(256):
            frac = i / 255.0
            L = _CREST_L + (_BASE_L - _CREST_L) * frac
            C = _CREST_CHROMA + (_BASE_CHROMA - _CREST_CHROMA) * frac
            H = _CREST_HUE + (_BASE_HUE - _CREST_HUE) * frac
            col = OklchColor(L, C, H)
            h = col.to_hex()
            _BODY_LUT.append((int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)))
    return _BODY_LUT


def _ensure_glow_lut() -> list[str]:
    """Glow LUT: achromatic brightness on background. No hue shift."""
    global _GLOW_LUT
    if _GLOW_LUT is None:
        bg = PALETTE["surface-deep"]
        _GLOW_LUT = []
        for i in range(_GLOW_STEPS):
            proximity = i / max(_GLOW_STEPS - 1, 1)
            intensity = proximity * proximity  # quadratic falloff
            L = bg.l + _GLOW_PEAK_L * intensity
            _GLOW_LUT.append(OklchColor(L, bg.c, bg.h).to_hex())
    return _GLOW_LUT


def _text_hex() -> str:
    """Teal-tinted dim text — computed once."""
    global _TEXT_HEX
    if _TEXT_HEX is None:
        _TEXT_HEX = OklchColor(0.52, 0.025, _TEAL_HUE).to_hex()
    return _TEXT_HEX


class WakeWave(Widget):
    """Multi-row traveling teal wave with interference and emergence.

    Dual exp(sin) waves at golden-ratio frequencies produce non-repeating
    interference.  Block characters give sub-row vertical resolution.
    Depth-graded chromatic color runs from bright cyan-green crests to
    deep blue-teal base, with a luminous glow above the surface.
    """

    DEFAULT_CSS = """
    WakeWave {
        height: 7;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._phase: float = 0.0
        self._timer: Timer | None = None
        self._heights: list[float] = []
        self._crest_boost: list[float] = []
        self._cached_width: int = 0

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0 / _FPS, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._phase += 1.0 / _FPS
        self._compute_frame()
        self.refresh()

    def stop(self) -> None:
        """Stop animation."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _compute_frame(self) -> None:
        """Pre-compute wave heights and crest boost for all columns."""
        w = self.size.width
        if w < 1:
            self._heights = []
            self._crest_boost = []
            self._cached_width = 0
            return

        t = self._phase

        # Emergence: exponential rise from stillness.
        emergence_raw = 1.0 - math.exp(-t / _EMERGENCE_TAU)
        emergence = emergence_raw ** _EMERGENCE_SHAPING

        # Breathing envelope.
        breath_raw = _norm(math.exp(math.sin(TWO_PI * t / _BREATH_PERIOD)))
        breath = _BREATH_FLOOR + (_BREATH_CEIL - _BREATH_FLOOR) * breath_raw

        envelope = emergence * breath

        heights: list[float] = []
        inv_w = 1.0 / max(w - 1, 1)

        for i in range(w):
            x = i * inv_w

            # Primary: rightward exp(sin) sweep.
            w1 = _norm(math.exp(math.sin(TWO_PI * (x - t * _PRIMARY_SPEED))))

            # Secondary: golden-ratio frequency, counter-propagating.
            w2 = _norm(math.exp(math.sin(
                TWO_PI * (x * _SECONDARY_FREQ + t * _SECONDARY_SPEED)
            )))

            # Interference.
            h = _PRIMARY_WEIGHT * w1 + _SECONDARY_WEIGHT * w2

            # Shimmer: high-frequency spatial detail.
            shimmer = _SHIMMER_AMP * math.sin(
                TWO_PI * (i / _SHIMMER_WAVELENGTH - t * _SHIMMER_SPEED)
            )

            h = h * envelope + shimmer
            # Deep floor: ever-present faint base, first sign of life.
            h = max(_FLOOR_HEIGHT * emergence_raw, min(1.0, h))
            heights.append(h)

        # Crest detection: local peaks get a brightness boost.
        crest_boost: list[float] = [0.0] * w
        for i in range(1, w - 1):
            if heights[i] > heights[i - 1] and heights[i] > heights[i + 1]:
                prominence = heights[i] - 0.5 * (heights[i - 1] + heights[i + 1])
                crest_boost[i] = min(1.0, prominence * 12.0)
                # Spread softer boost to neighbors.
                spread = crest_boost[i] * 0.3
                crest_boost[i - 1] = max(crest_boost[i - 1], spread)
                if i + 1 < w:
                    crest_boost[i + 1] = max(crest_boost[i + 1], spread)

        self._heights = heights
        self._crest_boost = crest_boost
        self._cached_width = w

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        H = self.size.height
        if w < 1 or H < 1:
            return Strip.blank(w)

        if w != self._cached_width or not self._heights:
            self._compute_frame()

        heights = self._heights
        crest_boost = self._crest_boost
        if not heights:
            return Strip.blank(w)

        body_lut = _ensure_body_lut()
        glow_lut = _ensure_glow_lut()

        # Absolute vertical position for this row.
        # bottom_idx: 0 at widget bottom, H-1 at top.
        bottom_idx = H - 1 - y
        vert = bottom_idx / max(H - 1, 1)  # 0.0=bottom, 1.0=top

        # Beer-Lambert attenuation: light decays exponentially with depth.
        # vert=1 (top) → atten=1.0; vert=0 (bottom) → atten≈0.17
        row_atten = math.exp(-_DEPTH_COEFF * (1.0 - vert))

        segments: list[Segment] = []
        bg_style = Style(bgcolor=_BG_HEX)

        for i in range(w):
            h = heights[i]

            # How many rows filled from the bottom.
            filled_rows = h * H

            if filled_rows >= bottom_idx + 1:
                # ── Fully inside wave body ──
                # Brightness: absolute row attenuation * wave energy.
                energy = 0.78 + 0.22 * h
                boost = crest_boost[i] if i < len(crest_boost) else 0.0
                brightness = row_atten * energy + boost * 0.10
                brightness = max(0.0, min(1.0, brightness))

                lut_idx = max(0, min(255, round((1.0 - brightness) * 255)))
                r, g, b = body_lut[lut_idx]
                segments.append(Segment(
                    "\u2588",
                    Style(color=f"#{r:02x}{g:02x}{b:02x}", bgcolor=_BG_HEX),
                ))

            elif filled_rows > bottom_idx:
                # ── Surface transition row ──
                fill = filled_rows - bottom_idx
                fill = max(0.0, min(1.0, fill))

                block_idx = max(0, min(8, round(fill * 8)))
                if block_idx == 0:
                    segments.append(Segment(" ", bg_style))
                    continue

                # Same absolute-position model, with a surface emphasis.
                energy = 0.78 + 0.22 * h
                boost = crest_boost[i] if i < len(crest_boost) else 0.0
                brightness = row_atten * energy + 0.06 + boost * 0.08
                brightness = max(0.0, min(1.0, brightness))

                lut_idx = max(0, min(255, round((1.0 - brightness) * 255)))
                r, g, b = body_lut[lut_idx]

                # Background: glow tint for the empty fraction above.
                if fill < 0.75:
                    glow_idx = max(0, min(
                        _GLOW_STEPS - 1,
                        round(fill * (_GLOW_STEPS - 1)),
                    ))
                    bg_hex = glow_lut[glow_idx]
                else:
                    bg_hex = _BG_HEX

                segments.append(Segment(
                    _BLOCKS[block_idx],
                    Style(color=f"#{r:02x}{g:02x}{b:02x}", bgcolor=bg_hex),
                ))

            else:
                # ── Above the wave — check for glow ──
                dist_above = bottom_idx - filled_rows
                if dist_above < _GLOW_REACH:
                    proximity = 1.0 - dist_above / _GLOW_REACH
                    glow_idx = max(0, min(
                        _GLOW_STEPS - 1,
                        round(proximity * (_GLOW_STEPS - 1)),
                    ))
                    segments.append(Segment(" ", Style(bgcolor=glow_lut[glow_idx])))
                else:
                    segments.append(Segment(" ", bg_style))

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
