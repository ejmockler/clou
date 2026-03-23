"""Clou color palette and design tokens.

All colors specified in OKLCH (Lightness, Chroma, Hue) and converted to
hex for Textual consumption. The OKLCH specification is the source of truth;
hex values are derived.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class OklchColor:
    """A color in OKLCH space."""

    l: float  # Lightness [0, 1]  # noqa: E741
    c: float  # Chroma [0, ~0.37]
    h: float  # Hue [0, 360]

    def to_hex(self) -> str:
        """Convert OKLCH -> sRGB hex. Uses Oklab as intermediate."""
        # OKLCH -> Oklab
        a = self.c * math.cos(math.radians(self.h))
        b = self.c * math.sin(math.radians(self.h))
        L = self.l

        # Oklab -> linear sRGB (via LMS)
        l_ = L + 0.3963377774 * a + 0.2158037573 * b
        m_ = L - 0.1055613458 * a - 0.0638541728 * b
        s_ = L - 0.0894841775 * a - 1.2914855480 * b

        l_cubed = l_**3
        m_cubed = m_**3
        s_cubed = s_**3

        lc, mc, sc = l_cubed, m_cubed, s_cubed
        r_lin = +4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc
        g_lin = -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc
        b_lin = -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc

        def linear_to_srgb(v: float) -> int:
            v = max(0.0, min(1.0, v))
            v = 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055
            return max(0, min(255, round(v * 255)))

        r = linear_to_srgb(r_lin)
        g = linear_to_srgb(g_lin)
        b_out = linear_to_srgb(b_lin)
        return f"#{r:02x}{g:02x}{b_out:02x}"

    def with_l(self, lightness: float) -> OklchColor:
        """Return a new color with the given lightness."""
        return OklchColor(lightness, self.c, self.h)

    def with_c(self, c: float) -> OklchColor:
        """Return a new color with the given chroma."""
        return OklchColor(self.l, c, self.h)

    def dim(self) -> OklchColor:
        """Dim variant: L->0.55, C*0.6."""
        return OklchColor(0.55, self.c * 0.6, self.h)

    def bright(self) -> OklchColor:
        """Bright variant: L->0.85, C*1.2 (capped at 0.37)."""
        return OklchColor(0.85, min(self.c * 1.2, 0.37), self.h)


# === Primitive Palette ===
# Source of truth. All hex values derived from these.

PALETTE: dict[str, OklchColor] = {
    # Surfaces (H:260, near-achromatic)
    "surface-deep": OklchColor(0.13, 0.015, 260),
    "surface": OklchColor(0.17, 0.015, 260),
    "surface-raised": OklchColor(0.21, 0.015, 260),
    "surface-overlay": OklchColor(0.25, 0.012, 260),
    # Borders
    "border-subtle": OklchColor(0.30, 0.010, 260),
    "border": OklchColor(0.35, 0.010, 260),
    "border-bright": OklchColor(0.40, 0.012, 260),
    "surface-bright": OklchColor(0.45, 0.010, 260),
    # Text
    "text-muted": OklchColor(0.45, 0.008, 250),
    "text-dim": OklchColor(0.60, 0.008, 250),
    "text": OklchColor(0.88, 0.010, 80),
    "text-bright": OklchColor(0.95, 0.005, 80),
    # Accents (all L:0.72 -- equal perceived brightness)
    "accent-gold": OklchColor(0.72, 0.12, 75),
    "accent-blue": OklchColor(0.72, 0.10, 255),
    "accent-teal": OklchColor(0.72, 0.10, 180),
    "accent-amber": OklchColor(0.72, 0.12, 55),
    "accent-violet": OklchColor(0.72, 0.10, 295),
    "accent-green": OklchColor(0.72, 0.12, 145),
    "accent-orange": OklchColor(0.72, 0.14, 50),
    "accent-rose": OklchColor(0.72, 0.14, 15),
}


def build_css_variables() -> str:
    """Generate CSS variable block for clou.tcss."""
    lines: list[str] = []
    for name, color in PALETTE.items():
        lines.append(f"${name}: {color.to_hex()};")
        # Generate dim/bright variants for accents
        if name.startswith("accent-"):
            lines.append(f"${name}-dim: {color.dim().to_hex()};")
            lines.append(f"${name}-bright: {color.bright().to_hex()};")
    return "\n".join(lines)


def breath_modulate(
    base_l: float, breath_value: float, amplitude: float = 0.15
) -> float:
    """Modulate lightness by a breath value, clamped to [0, 1].

    Args:
        base_l: Base lightness value.
        breath_value: Current breath cycle value (typically 0-1).
        amplitude: Modulation amplitude (default 0.15 = 15% range).

    Returns:
        Modulated lightness clamped to [0, 1].
    """
    return max(0.0, min(1.0, base_l + breath_value * amplitude))


_CYCLE_COLOR_MAP: dict[str, str] = {
    "PLAN": "accent-blue",
    "EXECUTE": "accent-teal",
    "ASSESS": "accent-amber",
    "VERIFY": "accent-violet",
}


def cycle_color(cycle_type: str) -> str:
    """Return the hex color for a cycle type.

    Args:
        cycle_type: One of PLAN, EXECUTE, ASSESS, VERIFY.

    Returns:
        Hex color string (e.g. '#7d9ed5').

    Raises:
        ValueError: If cycle_type is not recognized.
    """
    token = _CYCLE_COLOR_MAP.get(cycle_type)
    if token is None:
        raise ValueError(
            f"Unknown cycle type: {cycle_type!r}. "
            f"Expected one of: {', '.join(_CYCLE_COLOR_MAP)}"
        )
    return PALETTE[token].to_hex()
