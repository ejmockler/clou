"""ClouStatusBar — always-visible reactive metrics bar.

Docked to the bottom of the screen. Shows identity, milestone context,
cycle activity, token counts, and cost. Reactive attributes auto-trigger
re-render on change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import RenderResult

from clou.ui.theme import PALETTE, cycle_color

# Semantic hex colors from the palette for inline Rich styles.
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_MUTED_HEX = PALETTE["text-muted"].to_hex()
_ORANGE_HEX = PALETTE["accent-orange"].to_hex()


def format_cost(usd: float) -> str:
    """Format a USD cost value for display.

    Examples:
        >>> format_cost(3.42)
        '$3.42'
        >>> format_cost(0.0)
        '$0.00'
    """
    return f"${usd:.2f}"


def format_tokens(n: int) -> str:
    """Format a token count with thousands separators.

    Examples:
        >>> format_tokens(142338)
        '142,338'
        >>> format_tokens(0)
        '0'
    """
    return f"{n:,}"


def render_status_bar(
    *,
    milestone: str = "",
    cycle_type: str = "",
    cycle_num: int = 0,
    phase: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    rate_limited: bool = False,
) -> Text:
    """Build the Rich Text for the status bar.

    Extracted from the widget so it can be tested without Textual's
    reactive machinery.
    """
    token_str = (
        f"{format_tokens(input_tokens)}\u2193 {format_tokens(output_tokens)}\u2191"
    )

    if rate_limited:
        return Text.assemble(
            ("clou", "bold"),
            ("  ", ""),
            ("\u26a0 rate limited", f"bold {_ORANGE_HEX}"),
            ("  tokens: ", f"{_DIM_HEX}"),
            (token_str, ""),
        )

    if not milestone:
        return Text.assemble(
            ("clou", "bold"),
            ("  tokens: ", f"{_DIM_HEX}"),
            (token_str, ""),
        )

    # Active milestone — full layout.
    if cycle_type:
        try:
            cycle_hex = cycle_color(cycle_type)
        except ValueError:
            cycle_hex = _DIM_HEX
    else:
        cycle_hex = _DIM_HEX
    return Text.assemble(
        ("clou", "bold"),
        ("  ", ""),
        (milestone, f"bold {_GOLD_HEX}"),
        ("  ", ""),
        (cycle_type, cycle_hex),
        (f" #{cycle_num}", f"{_DIM_HEX}"),
        ("  ", ""),
        (phase or "", ""),
        ("  tokens: ", f"{_DIM_HEX}"),
        (token_str, ""),
        ("  ", ""),
        (format_cost(cost_usd), f"{_MUTED_HEX}"),
    )


class ClouStatusBar(Static):
    """Always-visible metrics bar docked to the bottom."""

    milestone: reactive[str] = reactive("")
    cycle_type: reactive[str] = reactive("")
    cycle_num: reactive[int] = reactive(0)
    phase: reactive[str] = reactive("")
    input_tokens: reactive[int] = reactive(0)
    output_tokens: reactive[int] = reactive(0)
    cost_usd: reactive[float] = reactive(0.0)
    rate_limited: reactive[bool] = reactive(False)

    def render(self) -> RenderResult:
        """Render the status bar content using Rich Text.assemble()."""
        return render_status_bar(
            milestone=self.milestone,
            cycle_type=self.cycle_type,
            cycle_num=self.cycle_num,
            phase=self.phase,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            rate_limited=self.rate_limited,
        )
