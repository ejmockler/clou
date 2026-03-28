"""DAG screen — push-screen for the task dependency viewer."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Static

from rich.markup import escape as _escape_markup

from clou.ui.messages import ClouDagUpdate
from clou.ui.theme import PALETTE
from clou.ui.widgets.dag import DagWidget

_TEAL_HEX = PALETTE["accent-teal"].to_hex()


class DagScreen(Screen[None]):
    """On-demand panel showing the task dependency graph."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close", show=False),
    ]

    DEFAULT_CSS = f"""
    DagScreen {{
        background: {PALETTE["surface"].to_hex()};
        padding: 1 2;
    }}

    #dag-header {{
        height: auto;
        color: {_TEAL_HEX};
        text-style: bold;
        padding-bottom: 1;
    }}
    """

    def __init__(
        self,
        milestone: str = "",
        tasks: list[dict[str, str]] | None = None,
        deps: dict[str, list[str]] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._milestone = milestone
        self._tasks = tasks or []
        self._deps = deps or {}

    def compose(self) -> ComposeResult:
        if self._milestone:
            title = f"Task Dependencies \u2014 {_escape_markup(self._milestone)}"
        else:
            title = "Task Dependencies"
        yield Static(
            f"[bold {_TEAL_HEX}]{title}[/]",
            id="dag-header",
        )
        yield DagWidget(
            tasks=self._tasks,
            deps=self._deps,
            id="dag-widget",
        )

    def on_clou_dag_update(self, msg: ClouDagUpdate) -> None:
        """Live-update the DAG when new data arrives."""
        try:
            self.query_one("#dag-widget", DagWidget).update_dag(msg.tasks, msg.deps)
        except Exception:
            pass
