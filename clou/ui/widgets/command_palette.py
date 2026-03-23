"""CommandPalette — filtered command list anchored above the input.

Appears when the user types ``/`` in the input field. Filters as they
continue typing. Display-only — the input widget retains focus.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from clou.ui.theme import PALETTE

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()


class CommandPalette(Static):
    """Filtered slash-command list, docked above the prompt."""

    DEFAULT_CSS = f"""
    CommandPalette {{
        height: auto;
        max-height: 12;
        background: {_SURFACE_RAISED_HEX};
        padding: 0 2;
        display: none;
    }}
    CommandPalette.visible {{
        display: block;
    }}
    """

    def update_filter(self, prefix: str) -> None:
        """Filter commands by *prefix* and show/hide accordingly.

        Args:
            prefix: The text after ``/`` (may be empty to show all).
        """
        from clou.ui.commands import all_commands

        commands = all_commands()
        if prefix:
            commands = [c for c in commands if c.name.startswith(prefix)]

        if not commands:
            self.remove_class("visible")
            return

        result = Text()
        for i, cmd in enumerate(commands):
            if i > 0:
                result.append("\n")
            result.append(f"  /{cmd.name:<12}", style=f"bold {_GOLD_HEX}")
            result.append(cmd.description, style=_DIM_HEX)

        self.update(result)
        self.add_class("visible")

    def hide(self) -> None:
        """Hide the palette."""
        self.remove_class("visible")
