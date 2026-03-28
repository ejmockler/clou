"""Breathing animation primitives — shared exp(sin(t)) indicator.

Renders the three-dot teal breathing pulse used by the working
indicator (#tail) and agent disclosure dots.  Uses a 64-step
pre-computed LUT for smooth animation at any FPS.
"""

from __future__ import annotations

import math

from rich.text import Text

from clou.ui.theme import OklchColor

# ── Constants ──────────────────────────────────────────────────────

BREATH_FPS: int = 24
BREATH_PERIOD: float = 3.0       # seconds per full breath cycle
BREATH_HUE: float = 180.0        # teal
BREATH_CHROMA: float = 0.08
BREATH_DIM_L: float = 0.18       # near surface-deep
BREATH_BRIGHT_L: float = 0.50    # clearly visible teal
EXP_NEG1: float = math.exp(-1)
EXP_RANGE: float = math.exp(1) - EXP_NEG1

# Pre-compute 64-step LUT for smooth breathing.
_BREATH_LUT: list[str] = []


def ensure_breath_lut() -> list[str]:
    """Return the 64-step hex color LUT, building it on first call."""
    if not _BREATH_LUT:
        for i in range(64):
            t = i / 63
            lightness = BREATH_DIM_L + (BREATH_BRIGHT_L - BREATH_DIM_L) * t
            _BREATH_LUT.append(OklchColor(lightness, BREATH_CHROMA, BREATH_HUE).to_hex())
    return _BREATH_LUT


def breathing_text(phase: float) -> Text:
    """Build a centered breathing indicator — three teal dots pulsing."""
    lut = ensure_breath_lut()
    TWO_PI = 2.0 * math.pi
    text = Text(justify="center")
    for i in range(3):
        p = phase + i * 0.3
        raw = math.exp(math.sin(TWO_PI * p / BREATH_PERIOD))
        breath = (raw - EXP_NEG1) / EXP_RANGE
        idx = max(0, min(63, round(breath * 63)))
        if i > 0:
            text.append(" ", style="")
        text.append("●", style=lut[idx])
    return text
