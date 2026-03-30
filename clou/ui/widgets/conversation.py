"""Conversation surface — scrollable message list with animated tail.

Turn state lives in ``TurnController``; this widget owns rendering and timers.
"""

from __future__ import annotations

import time

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from clou.ui.bridge import _strip_ansi
from clou.ui.mixins.drag_scroll import DragScrollMixin
from clou.ui.diff import build_diff_body, build_edit_summary
from clou.ui.messages import (
    ClouProcessingStarted,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
    ClouTurnContentReady,
)
from clou.ui.rendering.breathing import BREATH_FPS, breathing_text
from clou.ui.rendering.tool_summary import (
    format_ask_user_question,
    tool_glyph,
    tool_summary,
)
from clou.ui.theme import PALETTE
from clou.ui.turn_controller import TurnController
from clou.ui.widgets.agent_disclosure import AgentDisclosure
from clou.ui.widgets.edit_disclosure import (
    DISCLOSURE_PRUNE,
    EditDisclosure,
)
from clou.ui.widgets.message_widgets import MarkdownMessage, UserMessage
from clou.ui.widgets.prompt_input import PromptInput
from clou.ui.widgets.wake import WakeIndicator

# ── Color constants (only those used in this module) ──────────────
_TEAL_DIM_HEX = PALETTE["accent-teal"].dim().to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_SURFACE_HEX = PALETTE["surface"].to_hex()

# ── Stream constants ──────────────────────────────────────────────
_STREAM_FLUSH_INTERVAL: float = 0.1
_MAX_STREAM_BUFFER: int = 500_000


class ConversationWidget(DragScrollMixin, Widget):
    """Scrollable conversation with in-place animated tail."""

    DEFAULT_CSS = f"""
    ConversationWidget {{
        layout: vertical;
        height: 1fr;
    }}
    ConversationWidget #history {{
        height: 1fr;
    }}
    ConversationWidget #tail {{
        height: auto;
        text-align: center;
    }}
    ConversationWidget #tail.streaming {{
        border-left: tall {_TEAL_DIM_HEX};
        background: {_SURFACE_HEX};
        padding: 1 1 1 2;
        text-align: left;
    }}
    ConversationWidget #queue-indicator {{
        height: auto;
        width: auto;
        background: transparent;
    }}
    ConversationWidget .thinking,
    ConversationWidget .tool-activity {{
        padding-left: 4;
        margin: 0;
    }}
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
        self._tc = TurnController()
        self._stream_timer: Timer | None = None
        self._working_timer: Timer | None = None
        self._initializing: bool = True
        self._init_drag_scroll()
        self._disclosure_timer: Timer | None = None
        self._pending_agents: dict[str, AgentDisclosure] = {}

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

    # -- Internal helpers -----------------------------------------------

    def _clear_tail(self) -> None:
        """Reset #tail to idle — clear content and streaming class."""
        tail = self.query_one("#tail", Static)
        tail.remove_class("streaming")
        tail.update("")

    def _mount_msg(self, widget: Widget) -> None:
        """Mount *widget* before #tail and scroll to bottom."""
        history = self.query_one("#history", VerticalScroll)
        history.mount(widget, before=self.query_one("#tail", Static))
        history.scroll_end(animate=False)

    def _append(self, renderable: Text | Markdown | str, extra_classes: str = "") -> None:
        """Mount a message widget before #tail and scroll to bottom."""
        self._mount_msg(Static(renderable, classes=f"msg {extra_classes}".strip()))

    def _append_markdown(self, source: str, extra_classes: str = "") -> None:
        """Mount a Markdown message that re-renders on resize."""
        self._mount_msg(MarkdownMessage(source, classes=f"msg {extra_classes}".strip()))

    def _append_edit(self, name: str, tool_input: dict[str, object], styled: Text) -> None:
        """Mount an EditDisclosure widget for edit tool calls."""
        diff_body = build_diff_body(name, tool_input)
        self._mount_msg(EditDisclosure(styled, diff_body, classes="msg tool-activity"))
        self._ensure_disclosure_timer()

    # -- Disclosure lifecycle --------------------------------------------

    def _ensure_disclosure_timer(self) -> None:
        """Start the disclosure collapse timer if not already running."""
        if self._disclosure_timer is None:
            self._disclosure_timer = self.set_interval(0.5, self._disclosure_tick)

    def _disclosure_tick(self) -> None:
        """Check disclosure widgets for lifecycle transitions and prune stale ones."""
        now = time.monotonic()
        any_active = False
        to_remove: list[EditDisclosure] = []
        for widget in self.query(EditDisclosure):
            if not widget._pinned and widget._expanded:
                any_active = True
            if widget.update_lifecycle(now):
                widget.refresh()
            if (
                not widget._pinned
                and not widget._expanded
                and widget._collapsed_at > 0
                and (now - widget._collapsed_at) >= DISCLOSURE_PRUNE
            ):
                to_remove.append(widget)
        for widget in to_remove:
            widget.remove()
        has_pending = any(
            not w._pinned and not w._expanded and w._collapsed_at > 0
            for w in self.query(EditDisclosure)
        )
        if not any_active and not has_pending and self._disclosure_timer is not None:
            self._disclosure_timer.stop()
            self._disclosure_timer = None

    # -- Public helpers --------------------------------------------------

    def add_user_message(self, text: str, *, queued: bool = False) -> None:
        """Append a user message and start the working indicator."""
        self._flush_pending_text()
        self._tc.turn_text = ""
        self._mount_msg(UserMessage(text, queued=queued, classes="msg"))
        self._start_working()

    def recall_last_queued(self) -> None:
        """Remove the last queued UserMessage from the conversation."""
        queued = [um for um in self.query(UserMessage) if um._queued]
        if queued:
            queued[-1].remove()

    def reset_turn_state(self) -> None:
        """Atomically clear all turn state — single reset point."""
        self._stop_working()
        self._tc.reset()
        self._stop_timer()
        self._pending_agents.clear()
        self._clear_tail()
        for um in self.query(UserMessage):
            um.mark_active()

    def add_error_message(self, text: str) -> None:
        """Append an error message to the conversation history."""
        self._append(Text(f"\n{text}\n", style=f"bold {_ROSE_HEX}"))

    def add_command_output(self, renderable: Text | str) -> None:
        """Append slash command output."""
        self._append(Text(""))
        if isinstance(renderable, str):
            renderable = Text(renderable, style=_DIM_HEX)
        self._append(renderable)

    def add_command_error(self, text: str) -> None:
        """Append a brief dim error for bad slash commands."""
        self._append(Text(f"  {text}", style=_DIM_HEX))

    # -- Working indicator -----------------------------------------------

    def _start_working(self) -> None:
        """Begin breathing indicator in #tail."""
        self._tc.start_working()
        tail = self.query_one("#tail", Static)
        tail.update(breathing_text(0.0))
        if self._working_timer is None:
            self._working_timer = self.set_interval(1.0 / BREATH_FPS, self._working_tick)
        self.query_one("#history", VerticalScroll).scroll_end(animate=False)

    def _stop_working(self) -> None:
        """Stop the breathing indicator."""
        self._tc.stop_working()
        if self._working_timer is not None:
            self._working_timer.stop()
            self._working_timer = None

    def _working_tick(self) -> None:
        """Advance the breathing animation one frame."""
        if not self._tc.working:
            return
        self._tc.working_phase += 1.0 / BREATH_FPS
        tail = self.query_one("#tail", Static)
        tail.update(breathing_text(self._tc.working_phase))

    # -- Stream debounce -------------------------------------------------

    def _ensure_timer(self) -> None:
        """Start the stream flush timer if not already running."""
        if self._stream_timer is None:
            self._stream_timer = self.set_interval(_STREAM_FLUSH_INTERVAL, self._tick)

    def _stop_timer(self) -> None:
        """Cancel the stream flush timer if running."""
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None

    def _tick(self) -> None:
        """Flush accumulated stream content to #tail."""
        if self._tc.tick_stream():
            tail = self.query_one("#tail", Static)
            tail.update(Markdown(self._tc.stream_buffer))
            self.query_one("#history", VerticalScroll).scroll_end(animate=False)

    def on_unmount(self) -> None:
        """Clean up timers on widget removal."""
        self._stop_timer()
        self._tc.working = False
        if self._working_timer is not None:
            self._working_timer.stop()
            self._working_timer = None
        if self._disclosure_timer is not None:
            self._disclosure_timer.stop()
            self._disclosure_timer = None

    # -- Startup lifecycle -----------------------------------------------

    def _end_initializing(self) -> None:
        """The system is present — wave stops, gold prompt appears."""
        if not self._initializing:
            return
        self._initializing = False
        self.remove_class("initializing")
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

    # -- Pending text buffer ---------------------------------------------

    def _flush_pending_text(self) -> None:
        """Flush buffered supervisor text as a normal response."""
        text = self._tc.flush_pending()
        if text is None:
            return
        self._stop_working()
        self._append_markdown(text)
        self._clear_tail()

    # -- Message handlers ------------------------------------------------

    def on_clou_supervisor_text(self, msg: ClouSupervisorText) -> None:
        """Append assistant text to history, or buffer as candidate narration."""
        self._end_initializing()
        result = self._tc.process_supervisor_text(msg.text)
        for text in result.flushed_texts:
            self._stop_working()
            self._append_markdown(text)
            self._clear_tail()
        if result.action == "buffer":
            if result.need_start_working:
                self._start_working()
        else:
            self._stop_working()
            self._append_markdown(result.text)
            self._clear_tail()
            if result.action == "render_and_restart":
                self._start_working()

    def on_clou_stream_chunk(self, msg: ClouStreamChunk) -> None:
        """Accumulate streaming tokens and mark dirty for next flush."""
        result = self._tc.process_stream_chunk(msg.text, msg.uuid, _MAX_STREAM_BUFFER)
        if result.flushed_text is not None:
            self._stop_working()
            self._append_markdown(result.flushed_text)
            self._clear_tail()
        if result.new_stream:
            self._stop_working()
            self.query_one("#tail", Static).add_class("streaming")
        self._ensure_timer()

    def on_clou_turn_complete(self, msg: ClouTurnComplete) -> None:
        """Move accumulated stream buffer to history and clear tail."""
        self._end_initializing()
        result = self._tc.process_turn_complete()
        if result.flushed_text is not None:
            self._stop_working()
            self._append_markdown(result.flushed_text)
            self._clear_tail()
        self._stop_working()
        self._stop_timer()
        if result.had_stream:
            self._append_markdown(result.stream_content)
        self._clear_tail()
        if result.completed_content:
            self.post_message(ClouTurnContentReady(result.completed_content))

    def on_clou_thinking(self, msg: ClouThinking) -> None:
        """Append dimmed thinking block — flows into wake during startup."""
        if self._initializing:
            try:
                self.query_one(WakeIndicator).add_line(msg.text.splitlines()[0])
            except (LookupError, IndexError):
                pass
            return
        for line in msg.text.splitlines():
            self._append(Text(line, style=f"italic {_DIM_HEX}"), "thinking")

    def on_clou_tool_use(self, msg: ClouToolUse) -> None:
        """Append compact tool-use indicator — shimmers in wake during startup."""
        glyph = tool_glyph(msg.name)
        if self._initializing:
            try:
                self.query_one(WakeIndicator).add_line(
                    f"{glyph} {tool_summary(msg.name, msg.tool_input)}",
                )
            except LookupError:
                pass
            return
        self._tc.consume_narration()
        summary = tool_summary(msg.name, msg.tool_input)
        if not self._tc.working:
            self._start_working()
        if msg.name == "AskUserQuestion":
            self._stop_working()
            md = format_ask_user_question(msg.tool_input)
            if md:
                self._append_markdown(md)
                self._clear_tail()
            return
        if msg.name in ("ask_user", "mcp__clou__ask_user"):
            self._stop_working()
            self._clear_tail()
            return
        if msg.name == "Agent":
            desc = str(msg.tool_input.get("description", "agent"))
            disclosure = AgentDisclosure(
                msg.tool_use_id, desc, classes="msg agent-disclosure",
            )
            self._mount_msg(disclosure)
            if msg.tool_use_id:
                self._pending_agents[msg.tool_use_id] = disclosure
            return
        if msg.name in ("Edit", "MultiEdit", "Write"):
            styled = build_edit_summary(msg.name, msg.tool_input)
            self._append_edit(msg.name, msg.tool_input, styled)
        else:
            self._append(Text(f"{glyph} {summary}", style=_DIM_HEX), "tool-activity")

    def on_clou_tool_result(self, msg: ClouToolResult) -> None:
        """Route tool results — agent results fill disclosures, errors whisper."""
        if self._initializing:
            return
        disclosure = self._pending_agents.pop(msg.tool_use_id, None)
        if disclosure is not None:
            disclosure.complete(msg.content, msg.is_error)
            return
        if msg.is_error:
            content = _strip_ansi(msg.content)
            brief = content.splitlines()[0][:60] if content else ""
            self._append(Text(f"\u2717 {brief}", style=_DIM_HEX), "tool-activity")

    def on_clou_processing_started(self, msg: ClouProcessingStarted) -> None:
        """User message picked up by model — transition queued to active."""
        for um in self.query(UserMessage):
            if um._queued:
                um.mark_active()
                break
        if not self._tc.working:
            self._start_working()

    # -- Queue indicator -------------------------------------------------

    def update_queue_count(self, count: int) -> None:
        """Update the queue indicator with the number of pending messages.

        Only shown when 2+ messages are pending — for a single in-flight
        message the user can already see it in the conversation.
        """
        try:
            indicator = self.query_one("#queue-indicator", Static)
            if count > 1:
                indicator.update(Text(f"  \u00b7 {count} queued", style=f"italic {_DIM_HEX}"))
            else:
                indicator.update("")
        except LookupError:
            pass
