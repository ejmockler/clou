"""Conversation surface — scrollable message widgets with animated tail.

Messages are mounted as individual ``Static`` widgets inside a
``VerticalScroll`` container.  The *tail* widget stays at the bottom
and updates in place — showing either the animated working indicator
or the streaming Markdown preview.  When a turn completes the stream
content is promoted to a permanent widget and the tail clears.

Rendering is debounced: stream chunks accumulate in ``_stream_buffer``
and the tail is only re-rendered at most every 100 ms.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Rule, Static

from pathlib import PurePosixPath

from clou.ui.bridge import _strip_ansi
from clou.ui.theme import PALETTE
from clou.ui.widgets.prompt_input import PromptInput
from clou.ui.widgets.wake import WakeIndicator
from clou.ui.messages import (
    ClouProcessingStarted,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
)

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()
_BORDER_HEX = PALETTE["border"].to_hex()
_STREAM_FLUSH_INTERVAL: float = 0.1  # seconds
_MAX_STREAM_BUFFER: int = 500_000  # ~500KB — cap to prevent OOM on very long streaming turns
_WORKING_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_WORKING_TICKS_PER_FRAME = 2  # ticks between frame advances (tick = 100ms → 200ms/frame)
_DRAG_SCROLL_EDGE = 3  # rows from container edge to trigger auto-scroll
_DRAG_SCROLL_INTERVAL = 0.05  # seconds between scroll steps during drag
_DRAG_SCROLL_SPEED = 2  # lines per scroll step


def _tool_summary(name: str, tool_input: dict[str, object]) -> str:
    """One-line ambient summary of a tool call — name + key context."""
    if name in ("Read", "Write", "Edit"):
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        return f"{name} {fname}" if fname else name
    if name == "Bash":
        cmd = str(tool_input.get("command", ""))
        # Show first meaningful segment, strip long pipes.
        short = cmd.split("|")[0].strip()[:50]
        return f"{name} {short}" if short else name
    if name == "Grep":
        pattern = str(tool_input.get("pattern", ""))
        return f"{name} /{pattern[:30]}/" if pattern else name
    if name == "Glob":
        pattern = str(tool_input.get("pattern", ""))
        return f"{name} {pattern[:40]}" if pattern else name
    if name == "Agent":
        desc = str(tool_input.get("description", ""))
        return f"{name} {desc[:40]}" if desc else name
    return name


class ConversationWidget(Widget):
    """Scrollable conversation with in-place animated tail."""

    DEFAULT_CSS = """
    ConversationWidget {
        layout: vertical;
        height: 1fr;
    }
    ConversationWidget #history {
        height: 1fr;
    }
    ConversationWidget #tail {
        height: auto;
        text-align: center;
    }
    ConversationWidget #queue-indicator {
        height: auto;
        width: auto;
        background: transparent;
    }
    ConversationWidget .thinking {
        padding-left: 4;
    }
    ConversationWidget .tool-activity {
        padding-left: 4;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._stream_buffer: str = ""
        self._stream_uuid: str = ""
        self._stream_dirty: bool = False
        self._stream_timer: Timer | None = None
        self._last_completed_content: str = ""
        self._working: bool = False
        self._working_frame: int = 0
        self._working_tick: int = 0
        # Startup state — tool/thinking noise is suppressed until the
        # supervisor's greeting arrives, creating a clean experiential arc
        # from "system orienting" to "system present."
        self._initializing: bool = True
        # Drag-to-scroll: auto-scroll history when dragging near edges.
        self._drag_scroll_timer: Timer | None = None
        self._drag_scroll_direction: int = 0  # -1 up, +1 down

    def compose(self) -> ComposeResult:
        from clou.ui.widgets.command_palette import CommandPalette

        with VerticalScroll(id="history"):
            yield Static("", id="tail")
        yield Static("", id="queue-indicator")
        yield CommandPalette(id="command-palette")
        yield WakeIndicator(id="wake-indicator")
        yield PromptInput(id="user-input")

    def on_mount(self) -> None:
        """Start in the initializing state — wave provides the pulse."""
        self.add_class("initializing")

    # ------------------------------------------------------------------
    # Drag-to-scroll — auto-scroll history when selecting near edges
    # ------------------------------------------------------------------

    def on_mouse_move(self, event) -> None:  # noqa: ANN001 (MouseMove)
        """Auto-scroll history when dragging near top/bottom edge."""
        if not event.button:
            self._stop_drag_scroll()
            return
        try:
            history = self.query_one("#history", VerticalScroll)
        except LookupError:
            return
        region = history.region
        y = event.screen_y
        if y <= region.y + _DRAG_SCROLL_EDGE:
            self._start_drag_scroll(-1)
        elif y >= region.y + region.height - _DRAG_SCROLL_EDGE:
            self._start_drag_scroll(1)
        else:
            self._stop_drag_scroll()

    def on_mouse_up(self, event) -> None:  # noqa: ANN001 (MouseUp)
        """End drag — stop any auto-scrolling."""
        self._stop_drag_scroll()

    def _start_drag_scroll(self, direction: int) -> None:
        """Begin or update continuous edge-scroll in the given direction."""
        self._drag_scroll_direction = direction
        if self._drag_scroll_timer is None:
            self._drag_scroll_timer = self.set_interval(
                _DRAG_SCROLL_INTERVAL, self._drag_scroll_tick
            )
            self._drag_scroll_tick()  # immediate first step

    def _drag_scroll_tick(self) -> None:
        """One step of edge-scroll."""
        try:
            history = self.query_one("#history", VerticalScroll)
        except LookupError:
            self._stop_drag_scroll()
            return
        history.scroll_relative(
            y=self._drag_scroll_direction * _DRAG_SCROLL_SPEED, animate=False
        )

    def _stop_drag_scroll(self) -> None:
        """Cancel the edge-scroll timer."""
        if self._drag_scroll_timer is not None:
            self._drag_scroll_timer.stop()
            self._drag_scroll_timer = None
        self._drag_scroll_direction = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append(self, renderable: Text | Markdown | str, extra_classes: str = "") -> None:
        """Mount a message widget before #tail and scroll to bottom."""
        history = self.query_one("#history", VerticalScroll)
        tail = self.query_one("#tail", Static)
        css = f"msg {extra_classes}".strip()
        history.mount(Static(renderable, classes=css), before=tail)
        history.scroll_end(animate=False)

    def _width(self) -> int:
        """Return usable content width."""
        history = self.query_one("#history", VerticalScroll)
        return history.content_size.width or history.size.width or 80

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def add_user_message(self, text: str) -> None:
        """Append a user message and start the working indicator."""
        width = self._width()
        label = Text(f"  › {text}", style=f"bold {_GOLD_HEX}")
        label.pad_right(width)
        bg = f"on {_SURFACE_RAISED_HEX}"
        self._append(Text(""))
        self._append(Text("".ljust(width), style=bg))
        self._append(Text.assemble(label, style=bg))
        self._append(Text("".ljust(width), style=bg))
        self._start_working()

    def add_error_message(self, text: str) -> None:
        """Append an error message to the conversation history."""
        self._append(Text(f"\n{text}\n", style=f"bold {_ROSE_HEX}"))

    def add_command_output(self, renderable: Text | str) -> None:
        """Append slash command output — lighter than supervisor text."""
        self._append(Text(""))  # breathing room
        if isinstance(renderable, str):
            renderable = Text(renderable, style=_DIM_HEX)
        self._append(renderable)

    def add_command_error(self, text: str) -> None:
        """Append a brief dim error for bad slash commands."""
        self._append(Text(f"  {text}", style=_DIM_HEX))

    # ------------------------------------------------------------------
    # Working indicator (animated in-place via #tail)
    # ------------------------------------------------------------------

    def _start_working(self) -> None:
        """Begin animated working indicator in the tail widget."""
        self._working = True
        self._working_frame = 0
        self._working_tick = 0
        tail = self.query_one("#tail", Static)
        tail.update(Text(_WORKING_FRAMES[0], style=_TEAL_HEX, justify="center"))
        self._ensure_timer()
        self.query_one("#history", VerticalScroll).scroll_end(animate=False)

    def _stop_working(self) -> None:
        """Stop the working indicator."""
        self._working = False

    # ------------------------------------------------------------------
    # Stream debounce + animation tick
    # ------------------------------------------------------------------

    def _ensure_timer(self) -> None:
        """Start the flush/animation timer if not already running."""
        if self._stream_timer is None:
            self._stream_timer = self.set_interval(
                _STREAM_FLUSH_INTERVAL, self._tick
            )

    def _stop_timer(self) -> None:
        """Cancel the periodic timer if running."""
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None

    def _tick(self) -> None:
        """Unified tick: animate working indicator and flush stream content."""
        # Animate working indicator — update #tail in place.
        if self._working and not self._stream_dirty:
            self._working_tick += 1
            if self._working_tick >= _WORKING_TICKS_PER_FRAME:
                self._working_tick = 0
                self._working_frame = (self._working_frame + 1) % len(_WORKING_FRAMES)
                tail = self.query_one("#tail", Static)
                tail.update(Text(
                    _WORKING_FRAMES[self._working_frame],
                    style=_TEAL_HEX,
                    justify="center",
                ))
                self.query_one("#history", VerticalScroll).scroll_end(animate=False)

        # Flush stream content to #tail.
        if self._stream_dirty:
            self._stream_dirty = False
            tail = self.query_one("#tail", Static)
            tail.update(Markdown(self._stream_buffer))
            self.query_one("#history", VerticalScroll).scroll_end(animate=False)

    def on_unmount(self) -> None:
        """Clean up timers on widget removal."""
        self._stop_timer()
        self._stop_drag_scroll()

    # ------------------------------------------------------------------
    # Startup lifecycle
    # ------------------------------------------------------------------

    def _end_initializing(self) -> None:
        """The system is present — wave stops, gold prompt appears."""
        if not self._initializing:
            return
        self._initializing = False
        self.remove_class("initializing")
        # Promote startup activity into scrollable history before hiding wake.
        try:
            wake = self.query_one(WakeIndicator)
            for text, _birth in wake._lines:
                self._append(Text(f"  {text}", style=_DIM_HEX), "tool-activity")
            wake.stop()
        except LookupError:
            pass
        try:
            self.query_one("#user-input", PromptInput).set_ready()
        except LookupError:
            pass

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _write_horizon(self) -> None:
        """Write a horizon line that adapts to container width."""
        history = self.query_one("#history", VerticalScroll)
        tail = self.query_one("#tail", Static)
        history.mount(Rule(classes="msg horizon"), before=tail)
        history.scroll_end(animate=False)

    def on_clou_supervisor_text(self, msg: ClouSupervisorText) -> None:
        """Append completed assistant text to history."""
        self._end_initializing()
        self._stop_working()
        self._write_horizon()
        self._append(Markdown(msg.text))
        self.query_one("#tail", Static).update("")

    def on_clou_stream_chunk(self, msg: ClouStreamChunk) -> None:
        """Accumulate streaming tokens and mark dirty for next flush."""
        if msg.uuid != self._stream_uuid:
            self._stream_buffer = ""
            self._stream_uuid = msg.uuid
        self._stream_buffer += msg.text
        if len(self._stream_buffer) > _MAX_STREAM_BUFFER:
            self._stream_buffer = self._stream_buffer[-_MAX_STREAM_BUFFER:]
        self._stream_dirty = True
        self._ensure_timer()

    def on_clou_turn_complete(self, msg: ClouTurnComplete) -> None:
        """Move accumulated stream buffer to history and clear tail."""
        self._end_initializing()
        self._stop_working()
        self._stop_timer()
        # Preserve content before clearing — app handler reads this.
        self._last_completed_content = self._stream_buffer
        if self._stream_buffer:
            self._write_horizon()
            self._append(Markdown(self._stream_buffer))
        self._stream_buffer = ""
        self._stream_uuid = ""
        self._stream_dirty = False
        self.query_one("#tail", Static).update("")

    def on_clou_thinking(self, msg: ClouThinking) -> None:
        """Append dimmed thinking block — flows into wake during startup."""
        if self._initializing:
            try:
                first_line = msg.text.splitlines()[0]
                self.query_one(WakeIndicator).add_line(first_line)
            except (LookupError, IndexError):
                pass
            return
        self._stop_working()
        for line in msg.text.splitlines():
            self._append(Text(line, style=f"italic {_DIM_HEX}"), "thinking")
        self._start_working()

    def on_clou_tool_use(self, msg: ClouToolUse) -> None:
        """Append compact tool-use indicator — shimmers in wake during startup."""
        if self._initializing:
            summary = _tool_summary(msg.name, msg.tool_input)
            try:
                self.query_one(WakeIndicator).add_line(f"\u25b8 {summary}")
            except LookupError:
                pass
            return
        self._stop_working()
        summary = _tool_summary(msg.name, msg.tool_input)
        self._append(Text(f"\u25b8 {summary}", style=_DIM_HEX), "tool-activity")
        self._start_working()

    def on_clou_tool_result(self, msg: ClouToolResult) -> None:
        """Only surface errors — suppressed entirely during startup."""
        if self._initializing:
            return
        if msg.is_error:
            content = _strip_ansi(msg.content)
            truncated = content[:120]
            self._append(
                Text(f"\u2717 {truncated}", style=f"bold {_ROSE_HEX}"),
                "tool-activity",
            )

    def on_clou_processing_started(self, msg: ClouProcessingStarted) -> None:
        """User message picked up by model — show it in conversation now."""
        self.add_user_message(msg.text)

    # ------------------------------------------------------------------
    # Queue indicator
    # ------------------------------------------------------------------

    def update_queue_count(self, count: int) -> None:
        """Update the queue indicator with the number of pending messages."""
        try:
            indicator = self.query_one("#queue-indicator", Static)
            if count > 0:
                label = "queued" if count > 1 else "queued"
                indicator.update(
                    Text(f"  · {count} {label}", style=f"italic {_DIM_HEX}")
                )
            else:
                indicator.update("")
        except LookupError:
            pass
