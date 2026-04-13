"""Agent detail screen -- push-screen for viewing an agent's tool call stream."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import RichLog, Static

from rich.markup import escape as _escape_markup

from clou.ui.messages import ClouAgentComplete, ClouToolCallRecorded
from clou.ui.task_graph import ToolInvocation
from clou.ui.theme import PALETTE

# -- Accent hex values for tool categories and status indicators -----------

_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_VIOLET_HEX = PALETTE["accent-violet"].to_hex()
_BLUE_HEX = PALETTE["accent-blue"].to_hex()
_MUTED_HEX = PALETTE["text-muted"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()

_CATEGORY_COLORS: dict[str, str] = {
    "reads": _TEAL_HEX,
    "writes": _GOLD_HEX,
    "shell": _VIOLET_HEX,
    "searches": _BLUE_HEX,
    "other": _MUTED_HEX,
}

_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "active": ("\u25cf running", _GOLD_HEX),
    "complete": ("\u2713 complete", _TEAL_HEX),
    "failed": ("\u2717 failed", _ROSE_HEX),
    "aborted": ("\u2717 aborted", _ROSE_HEX),
    "pending": ("\u2026 pending", _MUTED_HEX),
}


def _format_time(timestamp: float, base: float) -> str:
    """Format a monotonic timestamp as mm:ss relative to a base time."""
    elapsed = max(0, timestamp - base)
    minutes, seconds = divmod(int(elapsed), 60)
    return f"{minutes:2d}:{seconds:02d}"


def _render_invocation_line(
    inv: ToolInvocation,
    base_time: float,
    index: int,
    *,
    focused: bool = False,
) -> str:
    """Render a single tool invocation as a Rich markup line."""
    prefix = "\u25b8 " if focused else "  "
    time_str = _format_time(inv.timestamp, base_time)
    color = _CATEGORY_COLORS.get(inv.category, _MUTED_HEX)
    name_padded = f"{_escape_markup(inv.name):<8}"
    summary = _escape_markup(inv.input_summary) if inv.input_summary else ""
    return (
        f"[{_DIM_HEX}]{prefix}{time_str}[/]  "
        f"[{color}]{name_padded}[/]  "
        f"[{_MUTED_HEX}]{summary}[/]"
    )


def _render_expanded_detail(inv: ToolInvocation) -> str:
    """Render the expanded input/output detail lines."""
    input_text = _escape_markup(inv.input_summary) if inv.input_summary else "(no input data available)"
    output_text = _escape_markup(inv.output_summary) if inv.output_summary else "(no output data available)"
    indent = "           "
    return (
        f"[{_MUTED_HEX}]{indent}input:  {input_text}[/]\n"
        f"[{_MUTED_HEX}]{indent}output: {output_text}[/]"
    )


class AgentDetailScreen(Screen[None]):
    """Dedicated screen showing an agent's complete tool call stream."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("enter", "toggle_selected", "Expand/Collapse", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    DEFAULT_CSS = f"""
    AgentDetailScreen {{
        background: {PALETTE["surface"].to_hex()};
        padding: 1 2;
    }}

    #agent-detail-header {{
        height: auto;
        text-style: bold;
        padding-bottom: 1;
    }}

    #agent-detail-stream {{
        height: 1fr;
    }}
    """

    def __init__(
        self,
        task_name: str,
        task_state: object | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._task_name = task_name
        self._task_state = task_state  # TaskState (untyped to avoid tight coupling)
        self._expanded_entries: set[int] = set()
        self._invocation_count: int = 0  # tracks total invocations rendered
        self._base_time: float | None = None  # monotonic reference for time display
        self._cursor_index: int = 0  # Currently focused tool call index

    def compose(self) -> ComposeResult:
        header_markup = self._build_header()
        yield Static(header_markup, id="agent-detail-header")
        yield RichLog(id="agent-detail-stream", markup=True)

    def on_mount(self) -> None:
        """Render existing tool invocations into the stream."""
        self._render_existing_history()

    # -- Header ------------------------------------------------------------

    def _build_header(self) -> str:
        """Build the header markup with task name and status."""
        title = f"Agent Detail \u2014 {_escape_markup(self._task_name)}"
        status = self._get_status()
        indicator, color = _STATUS_DISPLAY.get(status, ("\u2026 unknown", _MUTED_HEX))
        return f"[bold {color}]{title}[/]  [{color}]{indicator}[/]"

    def _get_status(self) -> str:
        """Extract status from task_state, with safe fallback."""
        if self._task_state is None:
            return "pending"
        return getattr(self._task_state, "status", "pending")

    def _update_header(self) -> None:
        """Refresh the header to reflect current status."""
        try:
            header = self.query_one("#agent-detail-header", Static)
            header.update(self._build_header())
        except Exception:
            pass

    # -- Cursor navigation -------------------------------------------------

    def action_toggle_selected(self) -> None:
        """Toggle expansion of the currently focused entry."""
        self.toggle_entry(self._cursor_index)

    def action_cursor_up(self) -> None:
        """Move cursor up one entry."""
        if self._cursor_index > 0:
            self._cursor_index -= 1
            self._rerender_stream()

    def action_cursor_down(self) -> None:
        """Move cursor down one entry."""
        invocations = self._get_invocations()
        if self._cursor_index < len(invocations) - 1:
            self._cursor_index += 1
            self._rerender_stream()

    # -- History rendering -------------------------------------------------

    def _render_existing_history(self) -> None:
        """Render all existing tool invocations into the RichLog."""
        invocations = self._get_invocations()
        log = self.query_one("#agent-detail-stream", RichLog)

        if not invocations:
            status = self._get_status()
            if status in ("complete", "failed", "aborted"):
                log.write(f"[{_MUTED_HEX}]Agent completed with no tool calls[/]")
            else:
                log.write(f"[{_MUTED_HEX}]Waiting for agent activity...[/]")
            return

        self._base_time = invocations[0].timestamp
        self._cursor_index = min(self._cursor_index, len(invocations) - 1)
        for i, inv in enumerate(invocations):
            line = _render_invocation_line(
                inv, self._base_time, i, focused=(i == self._cursor_index),
            )
            log.write(line)
        self._invocation_count = len(invocations)

    def _get_invocations(self) -> list[ToolInvocation]:
        """Safely extract tool_invocations from task_state."""
        if self._task_state is None:
            return []
        return list(getattr(self._task_state, "tool_invocations", []))

    # -- Live streaming ----------------------------------------------------

    def _append_invocation(self, inv: ToolInvocation) -> None:
        """Append a single new tool invocation to the stream."""
        if self._base_time is None:
            self._base_time = inv.timestamp
            # Clear the "waiting" placeholder on first real tool call.
            try:
                log = self.query_one("#agent-detail-stream", RichLog)
                log.clear()
            except Exception:
                pass

        index = self._invocation_count
        line = _render_invocation_line(
            inv, self._base_time, index, focused=(index == self._cursor_index),
        )
        try:
            log = self.query_one("#agent-detail-stream", RichLog)
            log.write(line)
        except Exception:
            pass
        self._invocation_count += 1

    def on_clou_tool_call_recorded(self, msg: ClouToolCallRecorded) -> None:
        """Append new tool call to the stream if it is for our task."""
        if msg.task_name != self._task_name:
            return
        self._append_invocation(msg.invocation)

    def on_clou_agent_complete(self, msg: ClouAgentComplete) -> None:
        """Refresh header when an agent completes while the screen is open."""
        # The app handler already updates task_state.status before this fires.
        # Unconditionally refresh -- _task_state is already scoped to this task.
        self._update_header()

    # -- Expandable entries ------------------------------------------------

    def toggle_entry(self, index: int) -> None:
        """Toggle expansion of a tool call entry by index."""
        invocations = self._get_invocations()
        if index < 0 or index >= len(invocations):
            return

        if index in self._expanded_entries:
            self._expanded_entries.discard(index)
        else:
            self._expanded_entries.add(index)

        # Re-render the full log to reflect expansion state.
        self._rerender_stream()

    def _rerender_stream(self) -> None:
        """Clear and re-render the entire tool stream with expansion state."""
        invocations = self._get_invocations()
        try:
            log = self.query_one("#agent-detail-stream", RichLog)
            log.clear()
        except Exception:
            return

        if not invocations:
            return

        if self._base_time is None:
            self._base_time = invocations[0].timestamp

        self._cursor_index = min(self._cursor_index, len(invocations) - 1)
        for i, inv in enumerate(invocations):
            line = _render_invocation_line(
                inv, self._base_time, i, focused=(i == self._cursor_index),
            )
            log.write(line)
            if i in self._expanded_entries:
                detail = _render_expanded_detail(inv)
                log.write(detail)
