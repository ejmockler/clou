"""Turn-management logic — pure state + decisions, no Textual dependencies.

Extracted from ConversationWidget to separate turn lifecycle concerns
(stream buffering, narration detection, working state, turn assembly)
from UI rendering (Textual timers, widget mounting, CSS classes).

TurnController is a composition object: ConversationWidget creates it
and delegates turn decisions.  Methods return dataclass results that
tell the widget what to render, keeping this module testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Action results — tell the widget what UI work to do
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupervisorTextResult:
    """What the widget should do after processing supervisor text.

    ``flushed_texts`` — pending texts that were flushed (render each as
    markdown, clear tail, stop working timer).

    ``action`` — one of:
      * ``"buffer"`` — text buffered as candidate narration; optionally
        start working if ``need_start_working`` is True.
      * ``"render"`` — text is a real response; stop working, render as
        markdown, clear tail.
      * ``"render_and_restart"`` — same as ``"render"`` then restart the
        working indicator (more work may follow).
    """

    flushed_texts: tuple[str, ...]
    action: str
    text: str
    need_start_working: bool


@dataclass(frozen=True, slots=True)
class StreamChunkResult:
    """What the widget should do after processing a stream chunk."""

    flushed_text: str | None
    new_stream: bool


@dataclass(frozen=True, slots=True)
class TurnCompleteResult:
    """What the widget should do to complete a turn."""

    flushed_text: str | None
    completed_content: str
    stream_content: str
    had_stream: bool


# ---------------------------------------------------------------------------
# TurnController
# ---------------------------------------------------------------------------


class TurnController:
    """Turn lifecycle state machine — pure logic, no Textual dependencies.

    Owns: stream buffer, stream UUID, dirty flag, pending text buffer,
    turn text accumulator, working state, working animation phase.

    Does NOT own: Textual timers, widget rendering, CSS classes, disclosure
    management, or any UI concerns.
    """

    __slots__ = (
        "stream_buffer",
        "stream_uuid",
        "stream_dirty",
        "pending_text",
        "turn_text",
        "working",
        "working_phase",
    )

    def __init__(self) -> None:
        self.stream_buffer: str = ""
        self.stream_uuid: str = ""
        self.stream_dirty: bool = False
        self.pending_text: str | None = None
        self.turn_text: str = ""
        self.working: bool = False
        self.working_phase: float = 0.0

    # ------------------------------------------------------------------
    # Pending text buffer
    # ------------------------------------------------------------------

    def flush_pending(self) -> str | None:
        """Clear pending text buffer, returning text if any.

        Side effects: sets ``working=False``, accumulates flushed text
        into ``turn_text``.  Caller handles UI (render markdown, clear
        tail, stop timer).
        """
        if self.pending_text is None:
            return None
        text = self.pending_text
        self.pending_text = None
        self.working = False
        self.turn_text += ("\n\n" + text) if self.turn_text else text
        return text

    def consume_narration(self) -> None:
        """Tool call confirms pending text was narration — discard it."""
        if self.pending_text is not None:
            self.pending_text = None

    # ------------------------------------------------------------------
    # Supervisor text processing
    # ------------------------------------------------------------------

    def process_supervisor_text(self, text: str) -> SupervisorTextResult:
        """Decide how to handle incoming supervisor text.

        Returns a result telling the widget what to render.
        """
        was_working = self.working
        flushed: list[str] = []

        # Any previously buffered text is a response — flush it.
        t = self.flush_pending()
        if t is not None:
            flushed.append(t)

        # Short text while working -> candidate narration.
        if was_working and len(text) < 200:
            first_line = text.strip().splitlines()[0] if text.strip() else ""
            if first_line:
                self.pending_text = text
                need_start = not self.working
                return SupervisorTextResult(
                    flushed_texts=tuple(flushed),
                    action="buffer",
                    text=text,
                    need_start_working=need_start,
                )

        # Not narration — real response.
        self.working = False
        self.turn_text += ("\n\n" + text) if self.turn_text else text

        action = "render_and_restart" if was_working else "render"
        return SupervisorTextResult(
            flushed_texts=tuple(flushed),
            action=action,
            text=text,
            need_start_working=False,
        )

    # ------------------------------------------------------------------
    # Stream chunk processing
    # ------------------------------------------------------------------

    def process_stream_chunk(
        self, text: str, uuid: str, max_buffer: int,
    ) -> StreamChunkResult:
        """Accept a streaming token chunk.

        Returns whether pending text was flushed and whether a new stream
        started (UUID changed).  Caller handles UI (timer, tail class).
        """
        flushed = self.flush_pending()

        new_stream = uuid != self.stream_uuid
        if new_stream:
            self.stream_buffer = ""
            self.stream_uuid = uuid
            self.working = False

        self.stream_buffer += text
        if len(self.stream_buffer) > max_buffer:
            self.stream_buffer = self.stream_buffer[-max_buffer:]
        self.stream_dirty = True

        return StreamChunkResult(flushed_text=flushed, new_stream=new_stream)

    # ------------------------------------------------------------------
    # Turn completion
    # ------------------------------------------------------------------

    def process_turn_complete(self) -> TurnCompleteResult:
        """Finalize the current turn and assemble content.

        Returns assembled ``completed_content`` for persistence, any
        ``stream_content`` to mount as markdown, and whether pending text
        was flushed.  Clears all turn state.
        """
        flushed = self.flush_pending()
        self.working = False

        # Compute completed content.
        if self.turn_text and self.stream_buffer:
            completed = self.turn_text + "\n\n" + self.stream_buffer
        else:
            completed = self.stream_buffer or self.turn_text

        had_stream = bool(self.stream_buffer)
        stream_content = self.stream_buffer

        # Clear turn state.
        self.stream_buffer = ""
        self.stream_uuid = ""
        self.stream_dirty = False
        self.turn_text = ""

        return TurnCompleteResult(
            flushed_text=flushed,
            completed_content=completed,
            stream_content=stream_content,
            had_stream=had_stream,
        )

    # ------------------------------------------------------------------
    # Working indicator state
    # ------------------------------------------------------------------

    def start_working(self) -> None:
        """Mark working state active and reset animation phase."""
        self.working = True
        self.working_phase = 0.0

    def stop_working(self) -> None:
        """Mark working state inactive."""
        self.working = False

    # ------------------------------------------------------------------
    # Stream tick
    # ------------------------------------------------------------------

    def tick_stream(self) -> bool:
        """Check and clear the dirty flag.  Returns True if was dirty."""
        if self.stream_dirty:
            self.stream_dirty = False
            return True
        return False

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset turn state except animation phase.

        Clears working flag, pending text, turn text, and stream state.
        Does NOT reset ``working_phase`` — that is managed by
        ``start_working()`` which resets it to 0.0.
        """
        self.working = False
        self.pending_text = None
        self.turn_text = ""
        self.stream_buffer = ""
        self.stream_uuid = ""
        self.stream_dirty = False
