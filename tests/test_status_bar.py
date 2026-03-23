"""Tests for clou.ui.widgets.status_bar — ClouStatusBar widget."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from clou.ui.theme import PALETTE, cycle_color
from clou.ui.widgets.status_bar import (
    ClouStatusBar,
    format_cost,
    format_tokens,
    render_status_bar,
)

_CONSOLE = Console()


class TestFormatCost:
    """Tests for format_cost()."""

    def test_typical_value(self) -> None:
        assert format_cost(3.42) == "$3.42"

    def test_zero(self) -> None:
        assert format_cost(0.0) == "$0.00"

    def test_rounds_to_cents(self) -> None:
        assert format_cost(1.999) == "$2.00"

    def test_large_value(self) -> None:
        assert format_cost(123.4) == "$123.40"


class TestFormatTokens:
    """Tests for format_tokens()."""

    def test_typical_value(self) -> None:
        assert format_tokens(142338) == "142,338"

    def test_zero(self) -> None:
        assert format_tokens(0) == "0"

    def test_small_value(self) -> None:
        assert format_tokens(42) == "42"

    def test_millions(self) -> None:
        assert format_tokens(1_000_000) == "1,000,000"


class TestRenderStatusBar:
    """Tests for render_status_bar() — the pure rendering function."""

    def test_idle_shows_clou(self) -> None:
        result = render_status_bar()
        assert isinstance(result, Text)
        plain = result.plain
        assert "clou" in plain
        assert "0\u2193" in plain
        assert "0\u2191" in plain

    def test_idle_shows_token_counts(self) -> None:
        result = render_status_bar(input_tokens=100, output_tokens=200)
        assert isinstance(result, Text)
        plain = result.plain
        assert "100\u2193" in plain
        assert "200\u2191" in plain

    def test_active_milestone_full_layout(self) -> None:
        result = render_status_bar(
            milestone="auth-system",
            cycle_type="EXECUTE",
            cycle_num=4,
            phase="foundation",
            input_tokens=142338,
            output_tokens=28102,
            cost_usd=3.42,
        )
        assert isinstance(result, Text)
        plain = result.plain
        assert "clou" in plain
        assert "auth-system" in plain
        assert "EXECUTE" in plain
        assert "#4" in plain
        assert "foundation" in plain
        assert "142,338\u2193" in plain
        assert "28,102\u2191" in plain
        assert "$3.42" in plain

    def test_cycle_color_applied_for_plan(self) -> None:
        result = render_status_bar(milestone="test", cycle_type="PLAN")
        expected_hex = cycle_color("PLAN")
        start = result.plain.index("PLAN")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == expected_hex

    def test_cycle_color_applied_for_execute(self) -> None:
        result = render_status_bar(milestone="test", cycle_type="EXECUTE")
        expected_hex = cycle_color("EXECUTE")
        start = result.plain.index("EXECUTE")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == expected_hex

    def test_cycle_color_applied_for_assess(self) -> None:
        result = render_status_bar(milestone="test", cycle_type="ASSESS")
        expected_hex = cycle_color("ASSESS")
        start = result.plain.index("ASSESS")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == expected_hex

    def test_cycle_color_applied_for_verify(self) -> None:
        result = render_status_bar(milestone="test", cycle_type="VERIFY")
        expected_hex = cycle_color("VERIFY")
        start = result.plain.index("VERIFY")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == expected_hex

    def test_rate_limited_render(self) -> None:
        result = render_status_bar(rate_limited=True, input_tokens=50)
        plain = result.plain
        assert "rate limited" in plain
        assert "clou" in plain
        assert "50\u2193" in plain

    def test_rate_limited_uses_orange_color(self) -> None:
        result = render_status_bar(rate_limited=True)
        orange_hex = PALETTE["accent-orange"].to_hex()
        start = result.plain.index("rate limited")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == orange_hex

    def test_unknown_cycle_type_falls_back_to_dim(self) -> None:
        """An unrecognized cycle_type should not crash; it falls back to dim."""
        dim_hex = PALETTE["text-dim"].to_hex()
        result = render_status_bar(milestone="test", cycle_type="UNKNOWN_THING")
        assert isinstance(result, Text)
        plain = result.plain
        assert "UNKNOWN_THING" in plain
        start = plain.index("UNKNOWN_THING")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == dim_hex

    def test_milestone_uses_gold_color(self) -> None:
        result = render_status_bar(milestone="auth-system")
        gold_hex = PALETTE["accent-gold"].to_hex()
        start = result.plain.index("auth-system")
        style_at = result.get_style_at_offset(_CONSOLE, start)
        assert style_at.color is not None
        assert style_at.color.get_truecolor().hex == gold_hex


class TestClouStatusBarClass:
    """Tests for the ClouStatusBar class structure."""

    def test_is_static_subclass(self) -> None:
        from textual.widgets import Static

        assert issubclass(ClouStatusBar, Static)

    def test_has_reactive_attributes(self) -> None:
        expected = {
            "milestone",
            "cycle_type",
            "cycle_num",
            "phase",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "rate_limited",
        }
        # Reactive descriptors are on the class
        for attr in expected:
            assert hasattr(ClouStatusBar, attr), f"Missing reactive: {attr}"
