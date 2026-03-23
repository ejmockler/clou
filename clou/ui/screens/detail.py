"""Detail screen — generic push-screen for viewing markdown/text content."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import RichLog, Static

from rich.markup import escape as _escape_markup

from clou.ui.theme import PALETTE

_GOLD_HEX = PALETTE["accent-gold"].to_hex()


class DetailScreen(Screen[None]):
    """Generic detail viewer for decisions.md, cost breakdown, etc."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close", show=False),
    ]

    DEFAULT_CSS = f"""
    DetailScreen {{
        background: {PALETTE["surface"].to_hex()};
        padding: 1 2;
    }}

    #detail-header {{
        height: auto;
        color: {_GOLD_HEX};
        text-style: bold;
        padding-bottom: 1;
    }}

    #detail-content {{
        height: 1fr;
    }}
    """

    def __init__(
        self,
        title: str = "",
        content: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold {_GOLD_HEX}]{_escape_markup(self._title)}[/]",
            id="detail-header",
        )
        yield RichLog(id="detail-content", markup=False)

    def on_mount(self) -> None:
        """Write content to the RichLog."""
        if self._content:
            log = self.query_one("#detail-content", RichLog)
            log.write(self._content)
