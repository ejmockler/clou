"""Tests for clou.ui.turn_controller — pure turn lifecycle logic."""

from __future__ import annotations

import pytest

from clou.ui.turn_controller import TurnController


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_defaults(self) -> None:
        tc = TurnController()
        assert tc.stream_buffer == ""
        assert tc.stream_uuid == ""
        assert tc.stream_dirty is False
        assert tc.pending_text is None
        assert tc.turn_text == ""
        assert tc.working is False
        assert tc.working_phase == 0.0


# ---------------------------------------------------------------------------
# flush_pending
# ---------------------------------------------------------------------------


class TestFlushPending:
    def test_returns_none_when_nothing_pending(self) -> None:
        tc = TurnController()
        assert tc.flush_pending() is None

    def test_returns_text_and_clears(self) -> None:
        tc = TurnController()
        tc.pending_text = "hello"
        tc.working = True
        result = tc.flush_pending()
        assert result == "hello"
        assert tc.pending_text is None
        assert tc.working is False

    def test_accumulates_into_turn_text(self) -> None:
        tc = TurnController()
        tc.pending_text = "first"
        tc.flush_pending()
        assert tc.turn_text == "first"

    def test_appends_to_existing_turn_text(self) -> None:
        tc = TurnController()
        tc.turn_text = "existing"
        tc.pending_text = "more"
        tc.flush_pending()
        assert tc.turn_text == "existing\n\nmore"


# ---------------------------------------------------------------------------
# consume_narration
# ---------------------------------------------------------------------------


class TestConsumeNarration:
    def test_clears_pending_text(self) -> None:
        tc = TurnController()
        tc.pending_text = "narration"
        tc.consume_narration()
        assert tc.pending_text is None

    def test_noop_when_nothing_pending(self) -> None:
        tc = TurnController()
        tc.consume_narration()
        assert tc.pending_text is None


# ---------------------------------------------------------------------------
# process_supervisor_text
# ---------------------------------------------------------------------------


class TestProcessSupervisorText:
    def test_short_text_while_working_buffers_as_narration(self) -> None:
        tc = TurnController()
        tc.working = True
        result = tc.process_supervisor_text("Let me check")
        assert result.action == "buffer"
        assert tc.pending_text == "Let me check"

    def test_long_text_while_working_renders_and_restarts(self) -> None:
        tc = TurnController()
        tc.working = True
        long_text = "A" * 201
        result = tc.process_supervisor_text(long_text)
        assert result.action == "render_and_restart"
        assert result.text == long_text
        assert tc.working is False
        assert tc.turn_text == long_text

    def test_text_when_not_working_renders(self) -> None:
        tc = TurnController()
        result = tc.process_supervisor_text("response")
        assert result.action == "render"
        assert result.text == "response"
        assert tc.working is False
        assert tc.turn_text == "response"

    def test_flushes_pending_text_before_processing(self) -> None:
        tc = TurnController()
        tc.pending_text = "buffered"
        tc.working = True
        result = tc.process_supervisor_text("A" * 201)
        assert result.flushed_texts == ("buffered",)
        assert tc.pending_text is None

    def test_empty_text_while_working_renders(self) -> None:
        """Empty/whitespace-only text is not narration even if short."""
        tc = TurnController()
        tc.working = True
        result = tc.process_supervisor_text("   ")
        assert result.action == "render_and_restart"

    def test_narration_sets_need_start_working_when_stopped(self) -> None:
        """After flush stops working, narration path signals need_start_working."""
        tc = TurnController()
        tc.working = True
        tc.pending_text = "old"
        result = tc.process_supervisor_text("new")
        # The flush stopped working, narration needs restart.
        assert result.action == "buffer"
        assert result.need_start_working is True

    def test_narration_no_start_when_already_working(self) -> None:
        """When working hasn't been stopped, no restart needed."""
        tc = TurnController()
        tc.working = True
        result = tc.process_supervisor_text("short")
        assert result.action == "buffer"
        assert result.need_start_working is False


# ---------------------------------------------------------------------------
# process_stream_chunk
# ---------------------------------------------------------------------------


class TestProcessStreamChunk:
    def test_accumulates_in_buffer(self) -> None:
        tc = TurnController()
        tc.process_stream_chunk("hel", "u1", 500_000)
        tc.process_stream_chunk("lo", "u1", 500_000)
        assert tc.stream_buffer == "hello"
        assert tc.stream_uuid == "u1"

    def test_new_uuid_resets_buffer(self) -> None:
        tc = TurnController()
        tc.process_stream_chunk("old", "u1", 500_000)
        result = tc.process_stream_chunk("new", "u2", 500_000)
        assert result.new_stream is True
        assert tc.stream_buffer == "new"
        assert tc.stream_uuid == "u2"

    def test_same_uuid_no_new_stream(self) -> None:
        tc = TurnController()
        tc.process_stream_chunk("a", "u1", 500_000)
        result = tc.process_stream_chunk("b", "u1", 500_000)
        assert result.new_stream is False

    def test_caps_buffer_at_max(self) -> None:
        tc = TurnController()
        tc.process_stream_chunk("x" * 60, "u1", 100)
        tc.process_stream_chunk("y" * 60, "u1", 100)
        assert len(tc.stream_buffer) == 100
        assert tc.stream_buffer.endswith("y" * 60)

    def test_marks_dirty(self) -> None:
        tc = TurnController()
        tc.process_stream_chunk("data", "u1", 500_000)
        assert tc.stream_dirty is True

    def test_flushes_pending_on_chunk(self) -> None:
        tc = TurnController()
        tc.pending_text = "narration"
        result = tc.process_stream_chunk("data", "u1", 500_000)
        assert result.flushed_text == "narration"
        assert tc.pending_text is None

    def test_new_stream_stops_working(self) -> None:
        tc = TurnController()
        tc.working = True
        tc.process_stream_chunk("data", "u1", 500_000)
        assert tc.working is False


# ---------------------------------------------------------------------------
# process_turn_complete
# ---------------------------------------------------------------------------


class TestProcessTurnComplete:
    def test_stream_only(self) -> None:
        tc = TurnController()
        tc.stream_buffer = "streamed"
        tc.stream_uuid = "u1"
        result = tc.process_turn_complete()
        assert result.completed_content == "streamed"
        assert result.had_stream is True
        assert result.stream_content == "streamed"
        assert tc.stream_buffer == ""
        assert tc.stream_uuid == ""

    def test_text_only(self) -> None:
        tc = TurnController()
        tc.turn_text = "direct"
        result = tc.process_turn_complete()
        assert result.completed_content == "direct"
        assert result.had_stream is False

    def test_both_text_and_stream(self) -> None:
        tc = TurnController()
        tc.turn_text = "text"
        tc.stream_buffer = "stream"
        result = tc.process_turn_complete()
        assert result.completed_content == "text\n\nstream"
        assert result.had_stream is True

    def test_empty_turn(self) -> None:
        tc = TurnController()
        result = tc.process_turn_complete()
        assert result.completed_content == ""
        assert result.had_stream is False

    def test_clears_all_state(self) -> None:
        tc = TurnController()
        tc.stream_buffer = "buf"
        tc.stream_uuid = "uid"
        tc.stream_dirty = True
        tc.turn_text = "txt"
        tc.working = True
        tc.process_turn_complete()
        assert tc.stream_buffer == ""
        assert tc.stream_uuid == ""
        assert tc.stream_dirty is False
        assert tc.turn_text == ""
        assert tc.working is False

    def test_flushes_pending_before_assembly(self) -> None:
        tc = TurnController()
        tc.pending_text = "pending"
        tc.stream_buffer = "stream"
        result = tc.process_turn_complete()
        assert result.flushed_text == "pending"
        # Flushed text went to turn_text, then combined with stream.
        assert result.completed_content == "pending\n\nstream"


# ---------------------------------------------------------------------------
# Working state
# ---------------------------------------------------------------------------


class TestWorkingState:
    def test_start_working(self) -> None:
        tc = TurnController()
        tc.start_working()
        assert tc.working is True
        assert tc.working_phase == 0.0

    def test_stop_working(self) -> None:
        tc = TurnController()
        tc.working = True
        tc.working_phase = 5.0
        tc.stop_working()
        assert tc.working is False

    def test_start_resets_phase(self) -> None:
        tc = TurnController()
        tc.working_phase = 3.14
        tc.start_working()
        assert tc.working_phase == 0.0


# ---------------------------------------------------------------------------
# tick_stream
# ---------------------------------------------------------------------------


class TestTickStream:
    def test_returns_true_when_dirty(self) -> None:
        tc = TurnController()
        tc.stream_dirty = True
        assert tc.tick_stream() is True
        assert tc.stream_dirty is False

    def test_returns_false_when_clean(self) -> None:
        tc = TurnController()
        assert tc.tick_stream() is False

    def test_clears_dirty_flag(self) -> None:
        tc = TurnController()
        tc.stream_dirty = True
        tc.tick_stream()
        assert tc.stream_dirty is False


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_clears_all_state(self) -> None:
        tc = TurnController()
        tc.stream_buffer = "buf"
        tc.stream_uuid = "uid"
        tc.stream_dirty = True
        tc.pending_text = "pending"
        tc.turn_text = "text"
        tc.working = True
        tc.working_phase = 5.0
        tc.reset()
        assert tc.stream_buffer == ""
        assert tc.stream_uuid == ""
        assert tc.stream_dirty is False
        assert tc.pending_text is None
        assert tc.turn_text == ""
        assert tc.working is False
        # working_phase is not reset by reset() — only by start_working()


# ---------------------------------------------------------------------------
# Integration: multi-step turn sequences
# ---------------------------------------------------------------------------


class TestTurnSequences:
    def test_narration_then_tool_consumes_narration(self) -> None:
        """Short text → tool call should consume narration, not flush."""
        tc = TurnController()
        tc.working = True
        tc.process_supervisor_text("Looking at files")
        assert tc.pending_text == "Looking at files"
        tc.consume_narration()
        assert tc.pending_text is None
        # turn_text should be empty — narration was consumed, not flushed
        assert tc.turn_text == ""

    def test_narration_then_turn_complete_flushes(self) -> None:
        """Short text → turn complete should flush narration as response."""
        tc = TurnController()
        tc.working = True
        tc.process_supervisor_text("The answer is 42")
        assert tc.pending_text == "The answer is 42"
        result = tc.process_turn_complete()
        assert result.flushed_text == "The answer is 42"
        assert result.completed_content == "The answer is 42"

    def test_supervisor_text_then_stream_flushes(self) -> None:
        """Supervisor text buffered, then stream chunk flushes it."""
        tc = TurnController()
        tc.working = True
        tc.process_supervisor_text("checking")
        assert tc.pending_text == "checking"
        result = tc.process_stream_chunk("token", "u1", 500_000)
        assert result.flushed_text == "checking"
        assert tc.pending_text is None

    def test_full_non_streamed_turn(self) -> None:
        """Non-streamed turn: supervisor text → turn complete."""
        tc = TurnController()
        result1 = tc.process_supervisor_text("Hello world!")
        assert result1.action == "render"
        assert tc.turn_text == "Hello world!"
        result2 = tc.process_turn_complete()
        assert result2.completed_content == "Hello world!"
        assert tc.turn_text == ""

    def test_full_streamed_turn(self) -> None:
        """Streamed turn: stream chunks → turn complete."""
        tc = TurnController()
        tc.process_stream_chunk("Hel", "u1", 500_000)
        tc.process_stream_chunk("lo", "u1", 500_000)
        result = tc.process_turn_complete()
        assert result.completed_content == "Hello"
        assert result.had_stream is True
        assert result.stream_content == "Hello"
