"""Tests for clou.ui.theme — OKLCH palette and color utilities."""

from __future__ import annotations

import re

import pytest

from clou.ui.theme import (
    PALETTE,
    OklchColor,
    breath_modulate,
    build_css_variables,
    cycle_color,
)

HEX_PATTERN = re.compile(r"^#[0-9a-f]{6}$")

# All 20 primitive token keys expected in the palette.
EXPECTED_PALETTE_KEYS = [
    "surface-deep",
    "surface",
    "surface-raised",
    "surface-overlay",
    "border-subtle",
    "border",
    "border-bright",
    "surface-bright",
    "text-muted",
    "text-dim",
    "text",
    "text-bright",
    "accent-gold",
    "accent-blue",
    "accent-teal",
    "accent-amber",
    "accent-violet",
    "accent-green",
    "accent-orange",
    "accent-rose",
]


class TestOklchColorToHex:
    """Tests for OKLCH -> hex conversion."""

    def test_produces_valid_hex(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        result = color.to_hex()
        assert HEX_PATTERN.match(result), f"Expected valid hex, got {result}"

    def test_pure_black(self) -> None:
        black = OklchColor(0.0, 0.0, 0.0)
        assert black.to_hex() == "#000000"

    def test_pure_white(self) -> None:
        white = OklchColor(1.0, 0.0, 0.0)
        result = white.to_hex()
        # Allow small rounding tolerance: each channel should be >= 0xfd
        r, g, b = int(result[1:3], 16), int(result[3:5], 16), int(result[5:7], 16)
        assert r >= 0xFD, f"Red channel too low: {r:#x}"
        assert g >= 0xFD, f"Green channel too low: {g:#x}"
        assert b >= 0xFD, f"Blue channel too low: {b:#x}"

    def test_all_palette_entries_produce_valid_hex(self) -> None:
        for name, color in PALETTE.items():
            result = color.to_hex()
            assert HEX_PATTERN.match(result), f"Palette entry {name!r} gave {result}"


class TestOklchColorModifiers:
    """Tests for dim, bright, with_l, with_c."""

    def test_dim_preserves_hue(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        dimmed = color.dim()
        assert dimmed.h == color.h

    def test_dim_shifts_l_and_c(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        dimmed = color.dim()
        assert dimmed.l == 0.55
        assert dimmed.c == pytest.approx(0.12 * 0.6)

    def test_bright_preserves_hue(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        brightened = color.bright()
        assert brightened.h == color.h

    def test_bright_shifts_l_and_c(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        brightened = color.bright()
        assert brightened.l == 0.85
        assert brightened.c == pytest.approx(0.12 * 1.2)

    def test_bright_caps_chroma(self) -> None:
        # A color with high chroma that would exceed 0.37 after 1.2x
        color = OklchColor(0.72, 0.35, 180)
        brightened = color.bright()
        assert brightened.c == 0.37

    def test_with_l(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        result = color.with_l(0.5)
        assert result.l == 0.5
        assert result.c == color.c
        assert result.h == color.h

    def test_with_c(self) -> None:
        color = OklchColor(0.72, 0.12, 75)
        result = color.with_c(0.08)
        assert result.c == 0.08
        assert result.l == color.l
        assert result.h == color.h


class TestPalette:
    """Tests for the PALETTE dict."""

    def test_has_all_20_keys(self) -> None:
        assert sorted(PALETTE.keys()) == sorted(EXPECTED_PALETTE_KEYS)

    def test_exactly_20_entries(self) -> None:
        assert len(PALETTE) == 20

    def test_all_values_are_oklch(self) -> None:
        for name, color in PALETTE.items():
            assert isinstance(color, OklchColor), f"{name} is not OklchColor"


class TestBuildCssVariables:
    """Tests for build_css_variables()."""

    def test_contains_all_base_tokens(self) -> None:
        css = build_css_variables()
        for name in EXPECTED_PALETTE_KEYS:
            assert f"${name}:" in css, f"Missing token ${name}"

    def test_contains_accent_dim_variants(self) -> None:
        css = build_css_variables()
        accent_names = [k for k in EXPECTED_PALETTE_KEYS if k.startswith("accent-")]
        for name in accent_names:
            assert f"${name}-dim:" in css, f"Missing dim variant for {name}"

    def test_contains_accent_bright_variants(self) -> None:
        css = build_css_variables()
        accent_names = [k for k in EXPECTED_PALETTE_KEYS if k.startswith("accent-")]
        for name in accent_names:
            assert f"${name}-bright:" in css, f"Missing bright variant for {name}"

    def test_no_dim_bright_for_non_accents(self) -> None:
        css = build_css_variables()
        assert "$surface-deep-dim:" not in css
        assert "$text-bright-dim:" not in css

    def test_all_values_are_valid_hex(self) -> None:
        css = build_css_variables()
        hex_values = re.findall(r"(#[0-9a-f]{6})", css)
        assert len(hex_values) > 0
        for hv in hex_values:
            assert HEX_PATTERN.match(hv), f"Invalid hex in CSS: {hv}"


class TestBreathModulate:
    """Tests for breath_modulate()."""

    def test_no_modulation_at_zero(self) -> None:
        assert breath_modulate(0.5, 0.0) == 0.5

    def test_positive_modulation(self) -> None:
        result = breath_modulate(0.5, 1.0, amplitude=0.15)
        assert result == pytest.approx(0.65)

    def test_clamps_high(self) -> None:
        result = breath_modulate(0.95, 1.0, amplitude=0.15)
        assert result == 1.0

    def test_clamps_low(self) -> None:
        result = breath_modulate(0.05, -1.0, amplitude=0.15)
        assert result == 0.0

    def test_negative_breath_value(self) -> None:
        result = breath_modulate(0.5, -0.5, amplitude=0.15)
        assert result == pytest.approx(0.425)


class TestCycleColor:
    """Tests for cycle_color()."""

    def test_plan_returns_blue(self) -> None:
        assert cycle_color("PLAN") == PALETTE["accent-blue"].to_hex()

    def test_execute_returns_teal(self) -> None:
        assert cycle_color("EXECUTE") == PALETTE["accent-teal"].to_hex()

    def test_assess_returns_amber(self) -> None:
        assert cycle_color("ASSESS") == PALETTE["accent-amber"].to_hex()

    def test_verify_returns_violet(self) -> None:
        assert cycle_color("VERIFY") == PALETTE["accent-violet"].to_hex()

    def test_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown cycle type"):
            cycle_color("UNKNOWN")
