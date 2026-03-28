"""CommandPalette — navigable command list with submenu support.

Appears when the user types ``/`` in the input field.  Supports
keyboard navigation (↑↓), submenu drill-down (↵), and provides
clear visual affordances for available actions.

Display-only in the sense that the input widget retains focus —
the app routes navigation keys here via :meth:`navigate`,
:meth:`select`, and :meth:`back`.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from clou.ui.theme import PALETTE

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_MUTED_HEX = PALETTE["text-muted"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()


class CommandPalette(Static):
    """Navigable slash-command list with submenu support."""

    DEFAULT_CSS = f"""
    CommandPalette {{
        height: auto;
        max-height: 16;
        background: {_SURFACE_RAISED_HEX};
        padding: 0 2;
        display: none;
    }}
    CommandPalette.visible {{
        display: block;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._commands: list = []
        self._selected: int = 0
        self._in_submenu: bool = False
        self._sub_parent: object | None = None
        self._sub_selected: int = 0
        # Resolved items for dynamic sub-items (items_factory).
        # Used instead of _sub_parent.items when non-empty.
        self._resolved_items: tuple = ()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        """Whether the palette is currently shown."""
        return self.has_class("visible")

    @property
    def _sub_items(self) -> tuple:
        """Active submenu items — resolved (dynamic) or static."""
        return self._resolved_items or (self._sub_parent.items if self._sub_parent else ())

    # ------------------------------------------------------------------
    # Navigation API — called by the app's key router
    # ------------------------------------------------------------------

    def update_filter(self, prefix: str) -> None:
        """Filter commands by *prefix*. Resets navigation state."""
        from clou.ui.commands import all_commands

        # Typing always collapses back to top level.
        self._in_submenu = False
        self._sub_parent = None
        self._sub_selected = 0
        self._resolved_items = ()

        commands = all_commands()
        if prefix:
            commands = [c for c in commands if c.name.startswith(prefix)]

        if not commands:
            self._commands = []
            self.remove_class("visible")
            return

        self._commands = commands
        self._selected = min(self._selected, len(commands) - 1)
        self._refresh_display()
        self.add_class("visible")

    def navigate(self, delta: int) -> None:
        """Move selection by *delta* (+1 down, −1 up)."""
        if self._in_submenu:
            items = self._sub_items
            if items:
                self._sub_selected = max(
                    0, min(len(items) - 1, self._sub_selected + delta)
                )
        elif self._commands:
            self._selected = max(
                0, min(len(self._commands) - 1, self._selected + delta)
            )
        self._refresh_display()

    def select(self) -> str | None:
        """Activate current selection.

        Returns the command text to dispatch (e.g. ``"/model sonnet"``),
        or ``None`` if a submenu was opened instead.
        """
        if self._in_submenu:
            parent = self._sub_parent
            items = self._sub_items
            if items and 0 <= self._sub_selected < len(items):
                sub = items[self._sub_selected]
                return f"/{parent.name} {sub.args}".rstrip()
            return f"/{parent.name}"

        if not self._commands or self._selected >= len(self._commands):
            return None

        cmd = self._commands[self._selected]

        # Resolve dynamic items if the command has a factory.
        items = cmd.items
        if not items and cmd.items_factory:
            items = cmd.items_factory(self.app)
            self._resolved_items = items

        if items:
            # Drill into submenu.
            self._in_submenu = True
            self._sub_parent = cmd
            self._sub_selected = 0
            self._refresh_display()
            return None

        return f"/{cmd.name}"

    def back(self) -> bool:
        """Go back from submenu.  Returns ``False`` if already at top level."""
        if self._in_submenu:
            self._in_submenu = False
            self._sub_parent = None
            self._sub_selected = 0
            self._refresh_display()
            return True
        return False

    def hide(self) -> None:
        """Hide palette and reset all state."""
        self._commands = []
        self._selected = 0
        self._in_submenu = False
        self._sub_parent = None
        self._sub_selected = 0
        self._resolved_items = ()
        self.remove_class("visible")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _refresh_display(self) -> None:
        """Rebuild the palette text."""
        result = Text()

        if self._in_submenu:
            self._render_submenu(result)
        else:
            self._render_top_level(result)

        # Navigation hints — muted, at the bottom.
        result.append("\n")
        if self._in_submenu:
            result.append("  ↑↓", style=_MUTED_HEX)
            result.append(" navigate  ", style=_DIM_HEX)
            result.append("↵", style=_MUTED_HEX)
            result.append(" select  ", style=_DIM_HEX)
            result.append("esc", style=_MUTED_HEX)
            result.append(" back", style=_DIM_HEX)
        else:
            result.append("  ↑↓", style=_MUTED_HEX)
            result.append(" navigate  ", style=_DIM_HEX)
            result.append("↵", style=_MUTED_HEX)
            result.append(" select  ", style=_DIM_HEX)
            result.append("esc", style=_MUTED_HEX)
            result.append(" close", style=_DIM_HEX)

        self.update(result)

    def _render_top_level(self, result: Text) -> None:
        """Render the top-level command list with selection highlight."""
        for i, cmd in enumerate(self._commands):
            if i > 0:
                result.append("\n")

            selected = i == self._selected
            marker = "› " if selected else "  "
            name_style = f"bold {_TEAL_HEX}" if selected else f"bold {_GOLD_HEX}"
            marker_style = _TEAL_HEX if selected else ""

            result.append(marker, style=marker_style)
            result.append(f"/{cmd.name:<12}", style=name_style)
            result.append(cmd.description, style=_DIM_HEX)

            if cmd.items or cmd.items_factory:
                result.append(" ▸", style=_TEAL_HEX if selected else _MUTED_HEX)
            if cmd.shortcut:
                result.append(f"  {cmd.shortcut}", style=_MUTED_HEX)

    def _render_submenu(self, result: Text) -> None:
        """Render a command's submenu with selection highlight."""
        parent = self._sub_parent

        # Header — parent command name.
        result.append(f"  /{parent.name}", style=f"bold {_GOLD_HEX}")
        result.append(" ▸", style=_MUTED_HEX)

        for i, item in enumerate(self._sub_items):
            result.append("\n")

            selected = i == self._sub_selected
            marker = "› " if selected else "  "
            label_style = f"bold {_TEAL_HEX}" if selected else _GOLD_HEX
            marker_style = _TEAL_HEX if selected else ""

            result.append(f"  {marker}", style=marker_style)
            result.append(f"{item.label:<14}", style=label_style)
            result.append(item.description, style=_DIM_HEX)
