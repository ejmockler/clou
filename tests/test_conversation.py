"""Tests for clou.ui.widgets.conversation — scrollable message widgets."""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from clou.ui.messages import (
    ClouProcessingStarted,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
)
from clou.ui.widgets.conversation import ConversationWidget, _tool_summary
import clou.ui.widgets.conversation as _conv_mod


class ConversationApp(App[None]):
    """Minimal app for testing ConversationWidget."""

    def compose(self) -> ComposeResult:
        yield ConversationWidget()


def _msg_count(widget: ConversationWidget) -> int:
    """Count message widgets in the history (excludes #tail)."""
    return len(widget.query(".msg"))


def _all_text(widget: ConversationWidget) -> str:
    """Extract plain text from all message widgets."""
    parts: list[str] = []
    for child in widget.query(".msg"):
        r = child.render()
        if isinstance(r, Text):
            parts.append(r.plain)
        else:
            parts.append(str(r))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------


class TestMounting:
    @pytest.mark.asyncio
    async def test_mounts_with_empty_state(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            assert widget._stream_buffer == ""
            assert widget._stream_uuid == ""

    @pytest.mark.asyncio
    async def test_has_history_and_tail(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            assert widget.query_one("#history", VerticalScroll) is not None
            assert widget.query_one("#tail", Static) is not None


# ---------------------------------------------------------------------------
# User messages
# ---------------------------------------------------------------------------


class TestUserMessage:
    @pytest.mark.asyncio
    async def test_add_user_message(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.add_user_message("hello")
            await pilot.pause()
            assert _msg_count(widget) >= 1


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------


class TestErrorMessage:
    @pytest.mark.asyncio
    async def test_add_error_message(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.add_error_message("Something went wrong")
            await pilot.pause()
            assert _msg_count(widget) >= 1


# ---------------------------------------------------------------------------
# Supervisor text
# ---------------------------------------------------------------------------


class TestSupervisorText:
    @pytest.mark.asyncio
    async def test_appends_to_history(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.post_message(ClouSupervisorText(text="response", model="opus"))
            await pilot.pause()
            assert _msg_count(widget) >= 1


# ---------------------------------------------------------------------------
# Stream chunks
# ---------------------------------------------------------------------------


class TestStreamChunk:
    @pytest.mark.asyncio
    async def test_accumulates_in_buffer(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.post_message(ClouStreamChunk(text="hel", uuid="u1"))
            await pilot.pause()
            widget.post_message(ClouStreamChunk(text="lo", uuid="u1"))
            await pilot.pause()
            assert widget._stream_buffer == "hello"
            assert widget._stream_uuid == "u1"

    @pytest.mark.asyncio
    async def test_new_uuid_resets_buffer(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.post_message(ClouStreamChunk(text="old", uuid="u1"))
            await pilot.pause()
            widget.post_message(ClouStreamChunk(text="new", uuid="u2"))
            await pilot.pause()
            assert widget._stream_buffer == "new"
            assert widget._stream_uuid == "u2"

    @pytest.mark.asyncio
    async def test_stream_buffer_truncated_at_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = 100
        monkeypatch.setattr(_conv_mod, "_MAX_STREAM_BUFFER", cap)
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            # Send chunks that together exceed the cap.
            chunk_size = 30
            total_chunks = 5  # 150 chars total, exceeds cap of 100
            for i in range(total_chunks):
                char = str(i)
                widget.post_message(
                    ClouStreamChunk(text=char * chunk_size, uuid="u1")
                )
                await pilot.pause()
            assert len(widget._stream_buffer) == cap
            # Buffer should keep the tail (most recent content), not the head.
            last_char = str(total_chunks - 1)
            assert widget._stream_buffer.endswith(last_char * chunk_size)


# ---------------------------------------------------------------------------
# Turn complete
# ---------------------------------------------------------------------------


class TestTurnComplete:
    @pytest.mark.asyncio
    async def test_moves_buffer_to_history_and_clears(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            # Simulate a streaming turn
            widget.post_message(ClouStreamChunk(text="streamed", uuid="u1"))
            await pilot.pause()
            assert widget._stream_buffer == "streamed"

            widget.post_message(
                ClouTurnComplete(
                    input_tokens=10, output_tokens=5, cost_usd=0.01, duration_ms=100
                )
            )
            await pilot.pause()

            # Buffer should be cleared
            assert widget._stream_buffer == ""
            assert widget._stream_uuid == ""
            # History should have the streamed content
            assert _msg_count(widget) >= 1

    @pytest.mark.asyncio
    async def test_empty_buffer_does_not_add_to_history(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            initial_count = _msg_count(widget)
            widget.post_message(
                ClouTurnComplete(
                    input_tokens=0, output_tokens=0, cost_usd=None, duration_ms=0
                )
            )
            await pilot.pause()
            assert _msg_count(widget) == initial_count


# ---------------------------------------------------------------------------
# Multiple turns accumulate
# ---------------------------------------------------------------------------


class TestMultipleTurns:
    @pytest.mark.asyncio
    async def test_multiple_turns_accumulate(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)

            # Turn 1: user + assistant
            widget.add_user_message("question 1")
            widget.post_message(ClouSupervisorText(text="answer 1", model="opus"))
            await pilot.pause()

            # Turn 2: user + streamed assistant
            widget.add_user_message("question 2")
            widget.post_message(ClouStreamChunk(text="answer ", uuid="u1"))
            await pilot.pause()
            widget.post_message(ClouStreamChunk(text="2", uuid="u1"))
            await pilot.pause()
            widget.post_message(
                ClouTurnComplete(
                    input_tokens=10, output_tokens=5, cost_usd=0.01, duration_ms=100
                )
            )
            await pilot.pause()

            # Should have: user1, assistant1, user2, streamed-answer (+ horizons, spacers)
            assert _msg_count(widget) >= 4


# ---------------------------------------------------------------------------
# Thinking and tool use
# ---------------------------------------------------------------------------


class TestThinking:
    @pytest.mark.asyncio
    async def test_thinking_appends_to_history(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._initializing = False  # past startup
            widget.post_message(ClouThinking(text="reasoning about it"))
            await pilot.pause()
            assert _msg_count(widget) >= 1


class TestToolUse:
    @pytest.mark.asyncio
    async def test_tool_use_appends_to_history(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._initializing = False  # past startup
            widget.post_message(
                ClouToolUse(name="Write", tool_input={"file_path": "/tmp/x"})
            )
            await pilot.pause()
            assert _msg_count(widget) >= 1

    @pytest.mark.asyncio
    async def test_tool_use_shows_compact_summary(self) -> None:
        """Tool use shows name + key argument, not raw tool_input."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._initializing = False  # past startup
            widget.post_message(
                ClouToolUse(
                    name="Read",
                    tool_input={"file_path": "/Users/noot/clou/src/main.py"},
                )
            )
            await pilot.pause()
            text = _all_text(widget)
            assert "main.py" in text
            # Should NOT contain the full path or raw dict.
            assert "/Users/noot" not in text


class TestToolSummary:
    def test_read_shows_filename(self) -> None:
        assert _tool_summary("Read", {"file_path": "/a/b/main.py"}) == "Read main.py"

    def test_write_shows_filename(self) -> None:
        assert _tool_summary("Write", {"file_path": "/tmp/out.txt"}) == "Write out.txt"

    def test_bash_shows_command(self) -> None:
        result = _tool_summary("Bash", {"command": "npm test | head -20"})
        assert result == "Bash npm test"

    def test_grep_shows_pattern(self) -> None:
        assert _tool_summary("Grep", {"pattern": "def main"}) == "Grep /def main/"

    def test_glob_shows_pattern(self) -> None:
        assert _tool_summary("Glob", {"pattern": "**/*.py"}) == "Glob **/*.py"

    def test_unknown_tool(self) -> None:
        assert _tool_summary("CustomTool", {}) == "CustomTool"

    def test_empty_file_path(self) -> None:
        assert _tool_summary("Read", {"file_path": ""}) == "Read"

    def test_agent_shows_description(self) -> None:
        result = _tool_summary("Agent", {"description": "explore the codebase"})
        assert result == "Agent explore the codebase"


# ---------------------------------------------------------------------------
# Unmount cleanup
# ---------------------------------------------------------------------------


class TestUnmount:
    @pytest.mark.asyncio
    async def test_on_unmount_stops_stream_timer(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            # Start a stream to create the timer.
            widget.post_message(ClouStreamChunk(text="hi", uuid="u1"))
            await pilot.pause()
            assert widget._stream_timer is not None
            # Simulate unmount.
            widget.on_unmount()
            assert widget._stream_timer is None


# ---------------------------------------------------------------------------
# Flush stream debounce
# ---------------------------------------------------------------------------


class TestFlushStream:
    @pytest.mark.asyncio
    async def test_noop_when_not_dirty(self) -> None:
        """_tick does not update tail when _stream_dirty is False."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._stream_dirty = False
            widget._stream_buffer = "should not matter"
            tail = widget.query_one("#tail", Static)
            tail.update("")
            widget._tick()
            await pilot.pause()
            # _stream_dirty should remain False
            assert widget._stream_dirty is False

    @pytest.mark.asyncio
    async def test_resets_dirty_flag(self) -> None:
        """_tick sets _stream_dirty to False when it was True."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._stream_dirty = True
            widget._stream_buffer = "hello"
            widget._tick()
            await pilot.pause()
            assert widget._stream_dirty is False

    @pytest.mark.asyncio
    async def test_updates_tail_with_buffer(self) -> None:
        """_tick updates #tail with Markdown of the buffer."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._stream_dirty = True
            widget._stream_buffer = "# heading\nparagraph"
            widget._tick()
            await pilot.pause()
            tail = widget.query_one("#tail", Static)
            # After tick, tail should have been updated (non-empty).
            # The content is a Markdown object — verify _stream_dirty was cleared
            # and that the update was applied.
            assert widget._stream_dirty is False
            # The tail's update() was called with Markdown — check it's not empty.
            visual = tail.render()
            assert visual is not None


# ---------------------------------------------------------------------------
# Tool result
# ---------------------------------------------------------------------------


class TestToolResult:
    @pytest.mark.asyncio
    async def test_success_result_suppressed(self) -> None:
        """Successful tool results are not shown — ambient noise."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            initial_count = _msg_count(widget)
            widget.post_message(
                ClouToolResult(tool_use_id="t1", content="result text", is_error=False)
            )
            await pilot.pause()
            assert _msg_count(widget) == initial_count

    @pytest.mark.asyncio
    async def test_error_result_appends_to_history(self) -> None:
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._initializing = False  # past startup
            widget.post_message(
                ClouToolResult(tool_use_id="t1", content="something failed", is_error=True)
            )
            await pilot.pause()
            assert _msg_count(widget) >= 1

    @pytest.mark.asyncio
    async def test_error_content_truncated(self) -> None:
        """Error content longer than 120 chars is truncated."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._initializing = False  # past startup
            long_content = "x" * 300
            widget.post_message(
                ClouToolResult(tool_use_id="t1", content=long_content, is_error=True)
            )
            await pilot.pause()
            assert _msg_count(widget) >= 1
            assert "x" * 300 not in _all_text(widget)

    @pytest.mark.asyncio
    async def test_ansi_stripped_from_error(self) -> None:
        """ANSI escape sequences are stripped from error content."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget._initializing = False  # past startup
            ansi_content = "\x1b[31mred error\x1b[0m output"
            widget.post_message(
                ClouToolResult(tool_use_id="t1", content=ansi_content, is_error=True)
            )
            await pilot.pause()
            text = _all_text(widget)
            assert "\x1b" not in text
            assert "red error" in text


# ---------------------------------------------------------------------------
# Message bubbling — posting to widget reaches widget handlers
# ---------------------------------------------------------------------------


class TestMessageBubbling:
    """Messages must be posted to ConversationWidget (not the app) so
    widget handlers fire.  Messages then bubble UP to app handlers."""

    @pytest.mark.asyncio
    async def test_supervisor_text_via_widget_reaches_history(self) -> None:
        """ClouSupervisorText posted to widget fires on_clou_supervisor_text."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.post_message(ClouSupervisorText(text="hello", model="opus"))
            await pilot.pause()
            assert _msg_count(widget) >= 1

    @pytest.mark.asyncio
    async def test_supervisor_text_via_app_does_not_reach_widget(self) -> None:
        """ClouSupervisorText posted to app does NOT fire widget handler.

        This is the Textual message model: messages bubble UP, not DOWN.
        This test documents why the orchestrator must post to the widget.
        """
        async with ConversationApp().run_test() as pilot:
            # Post to app, not widget
            pilot.app.post_message(ClouSupervisorText(text="hello", model="opus"))
            await pilot.pause()
            widget = pilot.app.query_one(ConversationWidget)
            # Text does NOT appear — this is the bug scenario
            assert _msg_count(widget) == 0


# ---------------------------------------------------------------------------
# Processing started (queue-aware message display)
# ---------------------------------------------------------------------------


class TestProcessingStarted:
    @pytest.mark.asyncio
    async def test_processing_started_shows_user_message(self) -> None:
        """ClouProcessingStarted should display the user message in history."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.post_message(ClouProcessingStarted(text="hello world"))
            await pilot.pause()
            assert _msg_count(widget) >= 1
            assert "hello world" in _all_text(widget)

    @pytest.mark.asyncio
    async def test_processing_started_starts_working(self) -> None:
        """ClouProcessingStarted should start the working indicator."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.post_message(ClouProcessingStarted(text="query"))
            await pilot.pause()
            assert widget._working is True


# ---------------------------------------------------------------------------
# Queue indicator
# ---------------------------------------------------------------------------


class TestQueueIndicator:
    @pytest.mark.asyncio
    async def test_queue_count_shows_indicator(self) -> None:
        """Queue count > 0 should show indicator text."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.update_queue_count(2)
            await pilot.pause()
            indicator = widget.query_one("#queue-indicator", Static)
            rendered = str(indicator.render())
            assert "2" in rendered

    @pytest.mark.asyncio
    async def test_queue_count_zero_clears_indicator(self) -> None:
        """Queue count 0 should clear indicator."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            widget.update_queue_count(3)
            await pilot.pause()
            widget.update_queue_count(0)
            await pilot.pause()
            indicator = widget.query_one("#queue-indicator", Static)
            rendered = str(indicator.render())
            assert "queued" not in rendered


# ---------------------------------------------------------------------------
# Startup lifecycle — tool noise suppressed, greeting ends initialization
# ---------------------------------------------------------------------------


class TestStartupLifecycle:
    @pytest.mark.asyncio
    async def test_starts_initializing(self) -> None:
        """Widget starts in initializing state with CSS class."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            assert widget._initializing is True
            assert widget.has_class("initializing")

    @pytest.mark.asyncio
    async def test_wake_indicator_visible_on_mount(self) -> None:
        """WakeIndicator is visible during initialization."""
        from clou.ui.widgets.wake import WakeIndicator

        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            wake = widget.query_one(WakeIndicator)
            assert wake._timer is not None

    @pytest.mark.asyncio
    async def test_tool_use_routed_to_wake_during_startup(self) -> None:
        """Tool activity during initialization routes to WakeIndicator."""
        from clou.ui.widgets.wake import WakeIndicator

        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            initial_count = _msg_count(widget)
            wake = widget.query_one(WakeIndicator)
            widget.post_message(
                ClouToolUse(name="Read", tool_input={"file_path": "/tmp/x"})
            )
            await pilot.pause()
            # No messages in conversation — activity went to wake.
            assert _msg_count(widget) == initial_count
            assert len(wake._lines) == 1
            assert "Read" in wake._lines[0][0]

    @pytest.mark.asyncio
    async def test_thinking_routed_to_wake_during_startup(self) -> None:
        """Thinking during initialization routes to WakeIndicator."""
        from clou.ui.widgets.wake import WakeIndicator

        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            initial_count = _msg_count(widget)
            wake = widget.query_one(WakeIndicator)
            widget.post_message(ClouThinking(text="reading context"))
            await pilot.pause()
            # No messages in conversation — activity went to wake.
            assert _msg_count(widget) == initial_count
            assert len(wake._lines) == 1
            assert "reading context" in wake._lines[0][0]

    @pytest.mark.asyncio
    async def test_tool_result_suppressed_during_startup(self) -> None:
        """Even error tool results are suppressed during initialization."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            initial_count = _msg_count(widget)
            widget.post_message(
                ClouToolResult(tool_use_id="t1", content="oops", is_error=True)
            )
            await pilot.pause()
            assert _msg_count(widget) == initial_count

    @pytest.mark.asyncio
    async def test_supervisor_text_ends_initialization(self) -> None:
        """ClouSupervisorText (non-streaming greeting) ends startup state."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            assert widget._initializing is True
            widget.post_message(ClouSupervisorText(text="Hello!", model="opus"))
            await pilot.pause()
            assert widget._initializing is False
            assert not widget.has_class("initializing")

    @pytest.mark.asyncio
    async def test_turn_complete_ends_initialization(self) -> None:
        """ClouTurnComplete (streaming greeting done) ends startup state."""
        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            assert widget._initializing is True
            widget.post_message(
                ClouTurnComplete(input_tokens=0, output_tokens=0, cost_usd=0, duration_ms=0)
            )
            await pilot.pause()
            assert widget._initializing is False
            assert not widget.has_class("initializing")

    @pytest.mark.asyncio
    async def test_prompt_input_ready_after_greeting(self) -> None:
        """PromptInput gets placeholder when greeting arrives."""
        from clou.ui.widgets.prompt_input import PromptInput
        from textual.widgets import Input

        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            prompt = widget.query_one(PromptInput)
            # Starts with no placeholder.
            assert prompt.query_one(Input).placeholder == ""
            # Greeting arrives.
            widget.post_message(ClouSupervisorText(text="Hi!", model="opus"))
            await pilot.pause()
            # Now ready — full invitation.
            assert prompt.query_one(Input).placeholder == "Talk to clou..."

    @pytest.mark.asyncio
    async def test_wake_indicator_stopped_after_greeting(self) -> None:
        """WakeIndicator stops and hides when greeting arrives."""
        from clou.ui.widgets.wake import WakeIndicator

        async with ConversationApp().run_test() as pilot:
            widget = pilot.app.query_one(ConversationWidget)
            wake = widget.query_one(WakeIndicator)
            assert wake._timer is not None
            widget.post_message(ClouSupervisorText(text="Hi!", model="opus"))
            await pilot.pause()
            assert wake._timer is None
