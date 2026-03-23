"""Context screen — push-screen for the golden context tree."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Static

from clou.ui.theme import PALETTE
from clou.ui.widgets.context_tree import ContextTreeWidget

_GOLD_HEX = PALETTE["accent-gold"].to_hex()


class ContextScreen(Screen[None]):
    """On-demand panel showing the .clou/ directory structure."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close", show=False),
    ]

    DEFAULT_CSS = f"""
    ContextScreen {{
        background: {PALETTE["surface"].to_hex()};
        padding: 1 2;
    }}

    #context-header {{
        height: auto;
        color: {_GOLD_HEX};
        text-style: bold;
        padding-bottom: 1;
    }}
    """

    def __init__(
        self,
        project_dir: Path,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold {_GOLD_HEX}]Golden Context \u2014 .clou/[/]",
            id="context-header",
        )
        yield ContextTreeWidget(
            self._project_dir / ".clou",
            id="context-tree",
        )

