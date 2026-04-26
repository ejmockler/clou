"""Tests for clou.ui.bridge — SDK message to Clou message routing."""

from __future__ import annotations

from typing import Any

from clou.ui.bridge import (
    _extract_transition_summary,
    _strip_ansi,
    extract_coordinator_status,
    extract_stream_text,
    route_coordinator_message,
    route_supervisor_message,
)
from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouBreathEvent,
    ClouMetrics,
    ClouRateLimit,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight mock objects with duck-typed attributes
# ---------------------------------------------------------------------------


class _ToolUseBlock:
    """Mimics SDK ToolUseBlock."""

    def __init__(self, name: str, tool_input: dict[str, Any] | None = None) -> None:
        self.name = name
        self.input = tool_input or {}


class _TextBlock:
    """Mimics SDK TextBlock."""

    def __init__(self, text: str) -> None:
        self.text = text


class _ThinkingBlock:
    """Mimics SDK ThinkingBlock."""

    def __init__(self, thinking: str) -> None:
        self.thinking = thinking


class _ToolResultBlock:
    """Mimics SDK ToolResultBlock."""

    def __init__(
        self,
        tool_use_id: str,
        content: str | list[dict[str, Any]] | None = None,
        is_error: bool | None = None,
    ) -> None:
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class _Msg:
    """Generic message mock — set arbitrary attrs via kwargs."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# extract_coordinator_status
# ---------------------------------------------------------------------------


class TestExtractCoordinatorStatus:
    def test_compose_py_write(self) -> None:
        blocks = [_ToolUseBlock("Write", {"file_path": "/proj/.clou/compose.py"})]
        assert extract_coordinator_status(blocks, "EXECUTE") == "compose.py updated"

    def test_compose_py_edit(self) -> None:
        blocks = [_ToolUseBlock("Edit", {"file_path": "/proj/.clou/compose.py"})]
        assert extract_coordinator_status(blocks, "EXECUTE") == "compose.py updated"

    def test_phase_md_write(self) -> None:
        blocks = [
            _ToolUseBlock(
                "Write",
                {"file_path": "/proj/.clou/milestones/m1/plan/phase.md"},
            )
        ]
        assert extract_coordinator_status(blocks, "PLAN") == "phase:plan spec written"

    def test_decisions_md_write(self) -> None:
        blocks = [_ToolUseBlock("Write", {"file_path": "/proj/.clou/decisions.md"})]
        assert extract_coordinator_status(blocks, "PLAN") == "decision logged"

    def test_status_md_write_returns_none(self) -> None:
        blocks = [_ToolUseBlock("Write", {"file_path": "/proj/.clou/status.md"})]
        assert extract_coordinator_status(blocks, "EXECUTE") is None

    def test_compose_py_edit_with_stats(self) -> None:
        blocks = [_ToolUseBlock("Edit", {
            "file_path": "/proj/.clou/compose.py",
            "old_string": "old\n",
            "new_string": "new\nextra\n",
        })]
        result = extract_coordinator_status(blocks, "EXECUTE")
        assert result == "compose.py updated  +2 −1"

    def test_execution_md_write_returns_none(self) -> None:
        blocks = [_ToolUseBlock("Write", {"file_path": "/proj/.clou/execution.md"})]
        assert extract_coordinator_status(blocks, "EXECUTE") is None

    def test_agent_tool(self) -> None:
        blocks = [_ToolUseBlock("Agent", {"description": "implement auth module"})]
        result = extract_coordinator_status(blocks, "EXECUTE")
        assert result == "dispatching implement auth module"

    def test_agent_tool_truncates_long_description(self) -> None:
        desc = "x" * 100
        blocks = [_ToolUseBlock("Agent", {"description": desc})]
        result = extract_coordinator_status(blocks, "EXECUTE")
        assert result is not None
        assert len(result) <= len("dispatching ") + 50

    def test_brutalist_tool(self) -> None:
        blocks = [_ToolUseBlock("mcp__brutalist__roast", {})]
        assert extract_coordinator_status(blocks, "ASSESS") == "brutalist roast"

    def test_non_dict_input_returns_none(self) -> None:
        """When block.input is not a dict, fallback to {} means file_path="" → None."""
        import types

        block = types.SimpleNamespace(name="Write", input="not_a_dict")
        assert extract_coordinator_status([block], "EXECUTE") is None

    def test_unknown_tool_returns_none(self) -> None:
        blocks = [_ToolUseBlock("Read", {"file_path": "/proj/foo.py"})]
        assert extract_coordinator_status(blocks, "EXECUTE") is None

    def test_plain_text_without_transition_returns_none(self) -> None:
        blocks = [_TextBlock("I'm analyzing the codebase structure")]
        assert extract_coordinator_status(blocks, "PLAN") is None

    def test_text_with_phase_complete(self) -> None:
        blocks = [_TextBlock("The planning phase complete, moving forward.")]
        result = extract_coordinator_status(blocks, "PLAN")
        assert result is not None
        assert "phase complete" in result.lower()

    def test_text_with_moving_to(self) -> None:
        blocks = [_TextBlock("Now moving to the execution stage.")]
        result = extract_coordinator_status(blocks, "PLAN")
        assert result is not None
        assert "moving to" in result.lower()

    def test_empty_content(self) -> None:
        assert extract_coordinator_status([], "PLAN") is None


# ---------------------------------------------------------------------------
# extract_stream_text
# ---------------------------------------------------------------------------


class TestExtractStreamText:
    def test_valid_text_delta(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hello"},
        }
        assert extract_stream_text(event) == "hello"

    def test_non_text_event(self) -> None:
        event = {"type": "content_block_start", "content_block": {}}
        assert extract_stream_text(event) is None

    def test_delta_without_text(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": "{}"},
        }
        assert extract_stream_text(event) is None

    def test_non_dict_delta(self) -> None:
        event = {"type": "content_block_delta", "delta": "not a dict"}
        assert extract_stream_text(event) is None

    def test_empty_event(self) -> None:
        assert extract_stream_text({}) is None


# ---------------------------------------------------------------------------
# route_supervisor_message
# ---------------------------------------------------------------------------


class TestRouteSupervisorMessage:
    def test_text_block_posts_supervisor_text(self) -> None:
        msg = _Msg(content=[_TextBlock("hello world")], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouSupervisorText)
        assert posted[0].text == "hello world"
        assert posted[0].model == "opus"

    def test_thinking_block_posts_thinking(self) -> None:
        msg = _Msg(content=[_ThinkingBlock("reasoning about X")])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouThinking)
        assert posted[0].text == "reasoning about X"

    def test_tool_use_block_posts_tool_use(self) -> None:
        msg = _Msg(content=[_ToolUseBlock("Write", {"file_path": "/tmp/x"})])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolUse)
        assert posted[0].name == "Write"

    def test_stream_event_posts_chunk(self) -> None:
        event = {"type": "content_block_delta", "delta": {"text": "tok"}}
        msg = _Msg(event=event, uuid="u-123")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouStreamChunk)
        assert posted[0].text == "tok"
        assert posted[0].uuid == "u-123"

    def test_stream_event_strips_ansi_from_uuid(self) -> None:
        event = {"type": "content_block_delta", "delta": {"text": "tok"}}
        msg = _Msg(event=event, uuid="\x1b[31mu-123\x1b[0m")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert posted[0].uuid == "u-123"

    def test_result_message_posts_turn_complete(self) -> None:
        msg = _Msg(
            usage={"input_tokens": 100, "output_tokens": 50},
            total_cost_usd=0.01,
            duration_ms=1200,
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouTurnComplete)
        assert posted[0].input_tokens == 100

    def test_rate_limit_posts_rate_limit(self) -> None:
        rli = _Msg(status="throttled", resets_at=1700000000)
        msg = _Msg(rate_limit_info=rli)
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouRateLimit)
        assert posted[0].status == "throttled"

    def test_unknown_message_posts_nothing(self) -> None:
        msg = _Msg(something_else="foo")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 0

    def test_mixed_content_blocks(self) -> None:
        msg = _Msg(
            content=[
                _ThinkingBlock("hmm"),
                _TextBlock("answer"),
                _ToolUseBlock("Read", {"file_path": "/x"}),
            ],
            model="opus",
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 3
        assert isinstance(posted[0], ClouThinking)
        assert isinstance(posted[1], ClouSupervisorText)
        assert isinstance(posted[2], ClouToolUse)


# ---------------------------------------------------------------------------
# route_coordinator_message
# ---------------------------------------------------------------------------


class TestRouteCoordinatorMessage:
    def test_tool_use_posts_breath_event(self) -> None:
        msg = _Msg(content=[_ToolUseBlock("Write", {"file_path": "/proj/compose.py"})])
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouBreathEvent)
        assert posted[0].text == "compose.py updated"
        assert posted[0].cycle_type == "EXECUTE"

    def test_non_surfaceable_content_posts_nothing(self) -> None:
        msg = _Msg(content=[_ToolUseBlock("Write", {"file_path": "/proj/status.md"})])
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 0

    def test_task_started_posts_agent_spawned(self) -> None:
        msg = _Msg(task_id="t-1", description="implement auth")
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentSpawned)
        assert posted[0].task_id == "t-1"
        assert posted[0].description == "implement auth"

    def test_task_progress_posts_agent_progress(self) -> None:
        msg = _Msg(
            task_id="t-1",
            last_tool_name="Edit",
            usage={"total_tokens": 500, "tool_uses": 3},
        )
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentProgress)
        assert posted[0].last_tool == "Edit"
        assert posted[0].total_tokens == 500

    def test_task_notification_posts_agent_complete(self) -> None:
        msg = _Msg(task_id="t-1", status="completed", summary="all done")
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentComplete)
        assert posted[0].status == "completed"
        assert posted[0].summary == "all done"

    def test_result_message_posts_metrics(self) -> None:
        msg = _Msg(
            usage={"input_tokens": 200, "output_tokens": 80},
            total_cost_usd=0.03,
        )
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "PLAN", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouMetrics)
        assert posted[0].tier == "coordinator"
        assert posted[0].milestone == "m1"

    def test_task_progress_with_none_usage(self) -> None:
        """usage=None should fallback to {} giving total_tokens=0, tool_uses=0."""
        msg = _Msg(task_id="t-nil", last_tool_name="Bash", usage=None)
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentProgress)
        assert posted[0].total_tokens == 0
        assert posted[0].tool_uses == 0

    def test_unknown_message_posts_nothing(self) -> None:
        msg = _Msg(unknown_field=True)
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "PLAN", posted.append)
        assert len(posted) == 0


# ---------------------------------------------------------------------------
# ANSI escape stripping
# ---------------------------------------------------------------------------


class TestStripAnsi:
    def test_strips_color_escape(self) -> None:
        assert _strip_ansi("\x1b[31mred text\x1b[0m") == "red text"

    def test_strips_osc_sequence(self) -> None:
        assert _strip_ansi("\x1b]0;evil title\x07normal") == "normal"

    def test_passthrough_clean_text(self) -> None:
        assert _strip_ansi("hello world") == "hello world"

    def test_none_returns_empty_string(self) -> None:
        assert _strip_ansi(None) == ""  # type: ignore[arg-type]

    def test_strip_osc_st_terminator(self) -> None:
        """OSC terminated by ESC\\ is stripped."""
        assert _strip_ansi("\x1b]0;evil-title\x1b\\clean") == "clean"

    def test_strip_osc_0x9c_terminator(self) -> None:
        """OSC terminated by 0x9C is stripped."""
        assert _strip_ansi("\x1b]52;c;data\x9cclean") == "clean"

    def test_strip_osc8_hyperlink(self) -> None:
        """OSC 8 hyperlinks are stripped."""
        assert _strip_ansi("\x1b]8;;https://evil.com\x1b\\click\x1b]8;;\x1b\\") == "click"

    def test_strip_dcs_payload(self) -> None:
        """DCS with full payload is stripped."""
        assert _strip_ansi("\x1bPpayload\x1b\\clean") == "clean"

    def test_strip_apc_payload(self) -> None:
        """APC sequence is fully stripped."""
        assert _strip_ansi("\x1b_apc-data\x1b\\clean") == "clean"

    def test_strip_csi_non_alpha_final(self) -> None:
        """CSI with non-alpha final byte (e.g. @, ~, `) is stripped."""
        assert _strip_ansi("\x1b[2~clean") == "clean"
        assert _strip_ansi("\x1b[1@clean") == "clean"

    def test_strip_8bit_c1(self) -> None:
        """8-bit C1 control codes are stripped."""
        assert _strip_ansi("\x9bclean") == "clean"  # 8-bit CSI
        assert _strip_ansi("\x9dclean") == "clean"  # 8-bit OSC

    def test_strip_charset_designation(self) -> None:
        """Charset designation sequences are stripped."""
        assert _strip_ansi("\x1b(0clean") == "clean"
        assert _strip_ansi("\x1b)Bclean") == "clean"

    def test_normal_text_roundtrip(self) -> None:
        """Normal ASCII and UTF-8 text passes through unchanged."""
        normal = "Hello, world! 日本語 émojis 🎉 tabs\there newlines\nhere"
        assert _strip_ansi(normal) == normal

    def test_printable_not_stripped(self) -> None:
        """Parentheses, brackets, and backslashes in normal text survive."""
        text = "array[0] = func(x) \\ path"
        assert _strip_ansi(text) == text

    def test_strip_trailing_lone_esc(self) -> None:
        """A lone trailing ESC byte (incomplete sequence) is stripped."""
        assert _strip_ansi("clean\x1b") == "clean"

    def test_strip_lone_esc_only(self) -> None:
        """A string that is just ESC is stripped to empty."""
        assert _strip_ansi("\x1b") == ""

    def test_consecutive_esc_before_csi(self) -> None:
        """ESC ESC [31m — first ESC survives regex but is caught by belt-and-suspenders."""
        assert _strip_ansi("\x1b\x1b[31mred") == "red"

    def test_consecutive_esc_pair(self) -> None:
        """Two ESC bytes mid-string — second forms Fe sequence with 'b', first caught by cleanup."""
        assert _strip_ansi("a\x1b\x1bb") == "a"

    def test_triple_esc(self) -> None:
        """Three consecutive ESC bytes — all stripped."""
        assert _strip_ansi("\x1b\x1b\x1b") == ""


# ---------------------------------------------------------------------------
# _extract_transition_summary
# ---------------------------------------------------------------------------


class TestExtractTransitionSummary:
    def test_multiline_match_on_later_line(self) -> None:
        text = "Some preamble\nAnother line\nThe planning phase complete now.\n"
        result = _extract_transition_summary(text)
        assert "phase complete" in result.lower()
        assert result == "The planning phase complete now."

    def test_truncation_at_80_chars(self) -> None:
        long_line = "This phase complete " + "x" * 100
        result = _extract_transition_summary(long_line)
        assert len(result) <= 80

    def test_no_match_falls_through(self) -> None:
        text = "Nothing special here, just some text about the project."
        result = _extract_transition_summary(text)
        assert result == text.strip()[:80]


class TestAnsiStrippingInRouting:
    def test_supervisor_text_strips_ansi(self) -> None:
        msg = _Msg(
            content=[_TextBlock("\x1b[31mmalicious\x1b[0m text")],
            model="opus",
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouSupervisorText)
        assert "\x1b" not in posted[0].text
        assert posted[0].text == "malicious text"

    def test_stream_chunk_strips_ansi(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {"text": "\x1b[31mred\x1b[0m"},
        }
        msg = _Msg(event=event, uuid="u-1")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouStreamChunk)
        assert "\x1b" not in posted[0].text
        assert posted[0].text == "red"

    def test_thinking_text_strips_ansi(self) -> None:
        msg = _Msg(content=[_ThinkingBlock("\x1b[36mthinking\x1b[0m deeply")])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouThinking)
        assert "\x1b" not in posted[0].text
        assert posted[0].text == "thinking deeply"

    def test_tool_name_strips_ansi(self) -> None:
        msg = _Msg(
            content=[_ToolUseBlock("\x1b[1mWrite\x1b[0m", {"file_path": "/tmp/x"})],
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolUse)
        assert "\x1b" not in posted[0].name
        assert posted[0].name == "Write"

    def test_agent_description_strips_ansi(self) -> None:
        msg = _Msg(task_id="t-2", description="\x1b[32mimplement auth\x1b[0m")
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentSpawned)
        assert "\x1b" not in posted[0].description
        assert posted[0].description == "implement auth"

    def test_agent_summary_strips_ansi(self) -> None:
        msg = _Msg(
            task_id="t-3",
            status="completed",
            summary="\x1b[33mall done\x1b[0m",
        )
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentComplete)
        assert "\x1b" not in posted[0].summary
        assert posted[0].summary == "all done"

    def test_none_thinking_returns_empty(self) -> None:
        """SDK thinking block with thinking=None should not crash."""
        msg = _Msg(content=[_ThinkingBlock(None)])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouThinking)
        assert posted[0].text == ""

    def test_none_description_returns_empty(self) -> None:
        """SDK task started with description=None should not crash."""
        msg = _Msg(task_id="t-99", description=None)
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouAgentSpawned)
        assert posted[0].description == ""

    def test_none_model_defaults_to_empty(self) -> None:
        """SDK message with model=None should default to empty string."""
        msg = _Msg(content=[_TextBlock("hello")], model=None)
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouSupervisorText)
        assert posted[0].model == ""

    def test_breath_event_text_strips_ansi(self) -> None:
        block = _ToolUseBlock("Write", {"file_path": "/proj/compose.py"})
        msg = _Msg(content=[block])
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouBreathEvent)
        # compose.py status is clean already; test with a transition text
        # that would contain ANSI if it came through
        assert "\x1b" not in posted[0].text


# ---------------------------------------------------------------------------
# AssistantMessage.error handling
# ---------------------------------------------------------------------------


class TestAssistantMessageError:
    def test_supervisor_error_posts_supervisor_text(self) -> None:
        """An AssistantMessage with error set should post the error as text."""
        msg = _Msg(
            content=[_TextBlock("partial output")],
            model="opus",
            error="billing_error",
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        # First message is the error, second is the text block content
        assert len(posted) == 2
        assert isinstance(posted[0], ClouSupervisorText)
        assert posted[0].text == "billing_error"
        assert posted[0].model == "opus"
        assert isinstance(posted[1], ClouSupervisorText)
        assert posted[1].text == "partial output"

    def test_supervisor_error_none_posts_no_extra(self) -> None:
        """An AssistantMessage with error=None should not post an extra message."""
        msg = _Msg(content=[_TextBlock("hello")], model="opus", error=None)
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouSupervisorText)
        assert posted[0].text == "hello"

    def test_supervisor_error_no_attr_posts_no_extra(self) -> None:
        """An AssistantMessage without error attr should not post an extra message."""
        msg = _Msg(content=[_TextBlock("hello")], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1

    def test_supervisor_error_empty_content(self) -> None:
        """Error with empty content list should post just the error."""
        msg = _Msg(content=[], model="opus", error="authentication_failed")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouSupervisorText)
        assert posted[0].text == "authentication_failed"

    def test_supervisor_error_strips_ansi(self) -> None:
        """Error text should be ANSI-stripped."""
        msg = _Msg(
            content=[],
            model="opus",
            error="\x1b[31mserver_error\x1b[0m",
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert "\x1b" not in posted[0].text
        assert posted[0].text == "server_error"

    def test_coordinator_error_posts_breath_event(self) -> None:
        """Coordinator AssistantMessage with error should post a breath event."""
        msg = _Msg(
            content=[],
            error="rate_limit",
        )
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouBreathEvent)
        assert posted[0].text == "rate_limit"
        assert posted[0].cycle_type == "EXECUTE"

    def test_coordinator_error_none_posts_nothing_extra(self) -> None:
        """Coordinator with error=None and non-surfaceable content posts nothing."""
        msg = _Msg(
            content=[_ToolUseBlock("Write", {"file_path": "/proj/status.md"})],
            error=None,
        )
        posted: list[Any] = []
        route_coordinator_message(msg, "m1", "EXECUTE", posted.append)
        assert len(posted) == 0


# ---------------------------------------------------------------------------
# ToolResultBlock routing
# ---------------------------------------------------------------------------


class TestToolResultBlockRouting:
    def test_tool_result_block_posts_clou_tool_result(self) -> None:
        """ToolResultBlock in content should be routed to ClouToolResult."""
        block = _ToolResultBlock(
            tool_use_id="tu-123", content="file written", is_error=False,
        )
        msg = _Msg(content=[block], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolResult)
        assert posted[0].tool_use_id == "tu-123"
        assert posted[0].content == "file written"
        assert posted[0].is_error is False

    def test_tool_result_block_error(self) -> None:
        """ToolResultBlock with is_error=True should propagate."""
        block = _ToolResultBlock(
            tool_use_id="tu-456", content="command failed", is_error=True,
        )
        msg = _Msg(content=[block], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolResult)
        assert posted[0].is_error is True

    def test_tool_result_block_none_content(self) -> None:
        """ToolResultBlock with content=None should post empty content."""
        block = _ToolResultBlock(tool_use_id="tu-789", content=None, is_error=False)
        msg = _Msg(content=[block], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolResult)
        assert posted[0].content == ""

    def test_tool_result_block_list_content(self) -> None:
        """ToolResultBlock with list content should stringify it."""
        block = _ToolResultBlock(
            tool_use_id="tu-list",
            content=[{"type": "text", "text": "hello"}],
            is_error=False,
        )
        msg = _Msg(content=[block], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolResult)
        assert "hello" in posted[0].content

    def test_tool_result_block_strips_ansi(self) -> None:
        """ToolResultBlock content and tool_use_id should be ANSI-stripped."""
        block = _ToolResultBlock(
            tool_use_id="\x1b[31mtu-ansi\x1b[0m",
            content="\x1b[32mresult\x1b[0m",
            is_error=False,
        )
        msg = _Msg(content=[block], model="opus")
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert posted[0].tool_use_id == "tu-ansi"
        assert posted[0].content == "result"

    def test_mixed_blocks_with_tool_result(self) -> None:
        """Text + ToolUse + ToolResult should all route correctly."""
        msg = _Msg(
            content=[
                _TextBlock("response"),
                _ToolUseBlock("Read", {"file_path": "/x"}),
                _ToolResultBlock("tu-mix", "file contents", False),
            ],
            model="opus",
        )
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 3
        assert isinstance(posted[0], ClouSupervisorText)
        assert isinstance(posted[1], ClouToolUse)
        assert isinstance(posted[2], ClouToolResult)


# ---------------------------------------------------------------------------
# ANSI stripping in extract_coordinator_status file_path
# ---------------------------------------------------------------------------


class TestAnsiInCoordinatorFilePath:
    def test_ansi_in_phase_md_path(self) -> None:
        """ANSI in file_path should be stripped before Path decomposition."""
        ansi_path = "\x1b[32m/proj/.clou/milestones/m1/plan/phase.md\x1b[0m"
        blocks = [_ToolUseBlock("Write", {"file_path": ansi_path})]
        result = extract_coordinator_status(blocks, "PLAN")
        assert result == "phase:plan spec written"

    def test_ansi_in_compose_py_path(self) -> None:
        """ANSI in compose.py path should still match."""
        ansi_path = "\x1b[1m/proj/.clou/compose.py\x1b[0m"
        blocks = [_ToolUseBlock("Write", {"file_path": ansi_path})]
        assert extract_coordinator_status(blocks, "EXECUTE") == "compose.py updated"

    def test_ansi_in_decisions_md_path(self) -> None:
        """ANSI in decisions.md path should still match."""
        ansi_path = "\x1b[36m/proj/.clou/decisions.md\x1b[0m"
        blocks = [_ToolUseBlock("Edit", {"file_path": ansi_path})]
        assert extract_coordinator_status(blocks, "PLAN") == "decision logged"


# ---------------------------------------------------------------------------
# tool_use_id threading
# ---------------------------------------------------------------------------


class TestToolUseIdThreading:
    """ClouToolUse carries tool_use_id from the SDK ToolUseBlock."""

    def test_tool_use_id_passed_through(self) -> None:
        block = _ToolUseBlock("Read", {"file_path": "/tmp/x"})
        block.id = "toolu_abc123"  # type: ignore[attr-defined]
        msg = _Msg(content=[block])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert len(posted) == 1
        assert isinstance(posted[0], ClouToolUse)
        assert posted[0].tool_use_id == "toolu_abc123"

    def test_tool_use_id_defaults_empty_when_missing(self) -> None:
        """Blocks without id attribute get empty string."""
        block = _ToolUseBlock("Read", {"file_path": "/tmp/x"})
        msg = _Msg(content=[block])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert posted[0].tool_use_id == ""

    def test_tool_use_id_ansi_stripped(self) -> None:
        block = _ToolUseBlock("Read", {"file_path": "/tmp/x"})
        block.id = "\x1b[1mtoolu_abc\x1b[0m"  # type: ignore[attr-defined]
        msg = _Msg(content=[block])
        posted: list[Any] = []
        route_supervisor_message(msg, posted.append)
        assert posted[0].tool_use_id == "toolu_abc"
