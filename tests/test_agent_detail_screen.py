"""Tests for clou.ui.screens.agent_detail -- AgentDetailScreen."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from textual.widgets import RichLog, Static

from clou.ui.messages import ClouAgentComplete, ClouToolCallRecorded
from clou.ui.screens.agent_detail import (
    AgentDetailScreen,
    _CATEGORY_COLORS,
    _DIM_HEX,
    _MUTED_HEX,
    _STATUS_DISPLAY,
    _format_time,
    _render_expanded_detail,
    _render_invocation_line,
)
from clou.ui.task_graph import TaskState, ToolInvocation, categorize_tool
from clou.ui.theme import PALETTE


# ---------------------------------------------------------------------------
# _format_time
# ---------------------------------------------------------------------------


class TestFormatTime:
    """Tests for _format_time helper."""

    def test_zero_elapsed(self) -> None:
        assert _format_time(100.0, 100.0) == " 0:00"

    def test_seconds_only(self) -> None:
        assert _format_time(105.0, 100.0) == " 0:05"

    def test_minutes_and_seconds(self) -> None:
        assert _format_time(225.0, 100.0) == " 2:05"

    def test_negative_elapsed_clamps_to_zero(self) -> None:
        result = _format_time(90.0, 100.0)
        assert result == " 0:00"

    def test_large_elapsed(self) -> None:
        # 10 minutes + 30 seconds = 630 seconds
        result = _format_time(730.0, 100.0)
        assert result == "10:30"


# ---------------------------------------------------------------------------
# _render_invocation_line
# ---------------------------------------------------------------------------


class TestRenderInvocationLine:
    """Tests for _render_invocation_line helper."""

    def test_basic_rendering(self) -> None:
        inv = ToolInvocation(
            name="Read",
            timestamp=100.0,
            category="reads",
            input_summary="clou/ui/app.py",
        )
        line = _render_invocation_line(inv, 100.0, 0)
        assert "Read" in line
        assert "clou/ui/app.py" in line
        assert " 0:00" in line

    def test_empty_input_summary(self) -> None:
        inv = ToolInvocation(name="Bash", timestamp=105.0, category="shell")
        line = _render_invocation_line(inv, 100.0, 0)
        assert "Bash" in line
        assert " 0:05" in line

    def test_category_color_reads(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _CATEGORY_COLORS["reads"] in line

    def test_category_color_writes(self) -> None:
        inv = ToolInvocation(name="Edit", timestamp=100.0, category="writes")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _CATEGORY_COLORS["writes"] in line

    def test_category_color_shell(self) -> None:
        inv = ToolInvocation(name="Bash", timestamp=100.0, category="shell")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _CATEGORY_COLORS["shell"] in line

    def test_category_color_searches(self) -> None:
        inv = ToolInvocation(name="Grep", timestamp=100.0, category="searches")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _CATEGORY_COLORS["searches"] in line

    def test_category_color_other(self) -> None:
        inv = ToolInvocation(name="Unknown", timestamp=100.0, category="other")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _CATEGORY_COLORS["other"] in line

    def test_markup_escaping_in_summary(self) -> None:
        """Rich markup characters in input_summary are escaped."""
        inv = ToolInvocation(
            name="Edit",
            timestamp=100.0,
            category="writes",
            input_summary="old='[bold]foo[/]'",
        )
        line = _render_invocation_line(inv, 100.0, 0)
        # Escaped brackets should appear, not raw Rich markup
        assert "\\[bold]" in line or "\\[" in line


# ---------------------------------------------------------------------------
# _render_expanded_detail
# ---------------------------------------------------------------------------


class TestRenderExpandedDetail:
    """Tests for _render_expanded_detail helper."""

    def test_with_data(self) -> None:
        inv = ToolInvocation(
            name="Edit",
            timestamp=100.0,
            category="writes",
            input_summary="old_string='foo'",
            output_summary="applied successfully",
        )
        detail = _render_expanded_detail(inv)
        assert "old_string='foo'" in detail
        assert "applied successfully" in detail
        assert "input:" in detail
        assert "output:" in detail

    def test_empty_input(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        detail = _render_expanded_detail(inv)
        assert "(no input data available)" in detail

    def test_empty_output(self) -> None:
        inv = ToolInvocation(
            name="Read",
            timestamp=100.0,
            category="reads",
            input_summary="some file",
        )
        detail = _render_expanded_detail(inv)
        assert "(no output data available)" in detail


# ---------------------------------------------------------------------------
# AgentDetailScreen construction
# ---------------------------------------------------------------------------


class TestAgentDetailScreenConstruction:
    """Tests for AgentDetailScreen instantiation and compose."""

    def test_construction_stores_task_name(self) -> None:
        screen = AgentDetailScreen(task_name="data_layer")
        assert screen._task_name == "data_layer"

    def test_construction_stores_task_state(self) -> None:
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="test", task_state=state)
        assert screen._task_state is state

    def test_construction_none_task_state(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        assert screen._task_state is None

    def test_compose_yields_header_and_richlog(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        widgets = list(screen.compose())
        assert len(widgets) == 2
        assert isinstance(widgets[0], Static)
        assert isinstance(widgets[1], RichLog)

    def test_richlog_has_markup_enabled(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        widgets = list(screen.compose())
        rich_logs = [w for w in widgets if isinstance(w, RichLog)]
        assert len(rich_logs) == 1
        assert rich_logs[0].markup is True

    def test_header_id(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        widgets = list(screen.compose())
        header = widgets[0]
        assert header.id == "agent-detail-header"

    def test_stream_id(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        widgets = list(screen.compose())
        stream = widgets[1]
        assert stream.id == "agent-detail-stream"


# ---------------------------------------------------------------------------
# Header building
# ---------------------------------------------------------------------------


class TestBuildHeader:
    """Tests for _build_header and status display."""

    def test_active_status(self) -> None:
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="my_task", task_state=state)
        header = screen._build_header()
        assert "my_task" in header
        assert "running" in header

    def test_complete_status(self) -> None:
        state = TaskState(status="complete")
        screen = AgentDetailScreen(task_name="done_task", task_state=state)
        header = screen._build_header()
        assert "complete" in header

    def test_failed_status(self) -> None:
        state = TaskState(status="failed")
        screen = AgentDetailScreen(task_name="bad_task", task_state=state)
        header = screen._build_header()
        assert "failed" in header

    def test_pending_status_with_none_state(self) -> None:
        screen = AgentDetailScreen(task_name="unknown_task")
        header = screen._build_header()
        assert "pending" in header

    def test_header_uses_correct_accent_for_active(self) -> None:
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="t", task_state=state)
        header = screen._build_header()
        _, gold_hex = _STATUS_DISPLAY["active"]
        assert gold_hex in header

    def test_header_uses_correct_accent_for_complete(self) -> None:
        state = TaskState(status="complete")
        screen = AgentDetailScreen(task_name="t", task_state=state)
        header = screen._build_header()
        _, teal_hex = _STATUS_DISPLAY["complete"]
        assert teal_hex in header


# ---------------------------------------------------------------------------
# Invocation access
# ---------------------------------------------------------------------------


class TestGetInvocations:
    """Tests for _get_invocations safety."""

    def test_none_state_returns_empty(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        assert screen._get_invocations() == []

    def test_state_with_invocations(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        result = screen._get_invocations()
        assert len(result) == 1
        assert result[0].name == "Read"

    def test_returns_copy_not_reference(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        result = screen._get_invocations()
        result.clear()
        # Original should be unchanged
        assert len(state.tool_invocations) == 1


# ---------------------------------------------------------------------------
# ClouToolCallRecorded filtering
# ---------------------------------------------------------------------------


class TestToolCallRecordedFiltering:
    """Tests that on_clou_tool_call_recorded filters by task_name."""

    def test_matching_task_appends(self) -> None:
        """Verify _append_invocation is called for matching task."""
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="my_task", task_state=state)
        # Track calls to _append_invocation
        appended: list[ToolInvocation] = []
        original = screen._append_invocation
        screen._append_invocation = lambda inv: appended.append(inv)

        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        msg = ClouToolCallRecorded(task_name="my_task", invocation=inv)
        screen.on_clou_tool_call_recorded(msg)
        assert len(appended) == 1

    def test_non_matching_task_ignored(self) -> None:
        """Verify _append_invocation is NOT called for non-matching task."""
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="my_task", task_state=state)
        appended: list[ToolInvocation] = []
        screen._append_invocation = lambda inv: appended.append(inv)

        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        msg = ClouToolCallRecorded(task_name="other_task", invocation=inv)
        screen.on_clou_tool_call_recorded(msg)
        assert len(appended) == 0


# ---------------------------------------------------------------------------
# Toggle entry expansion
# ---------------------------------------------------------------------------


class TestToggleEntry:
    """Tests for toggle_entry expansion tracking."""

    def test_expand_adds_to_set(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        # Patch _rerender_stream to avoid needing a live app
        screen._rerender_stream = lambda: None
        screen.toggle_entry(0)
        assert 0 in screen._expanded_entries

    def test_collapse_removes_from_set(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        screen._rerender_stream = lambda: None
        screen.toggle_entry(0)
        assert 0 in screen._expanded_entries
        screen.toggle_entry(0)
        assert 0 not in screen._expanded_entries

    def test_out_of_range_index_ignored(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        screen._rerender_stream = lambda: None
        screen.toggle_entry(5)
        assert len(screen._expanded_entries) == 0

    def test_negative_index_ignored(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        screen._rerender_stream = lambda: None
        screen.toggle_entry(-1)
        assert len(screen._expanded_entries) == 0


# ---------------------------------------------------------------------------
# Status display mapping
# ---------------------------------------------------------------------------


class TestStatusDisplay:
    """Tests for _STATUS_DISPLAY coverage."""

    def test_all_task_statuses_covered(self) -> None:
        """All expected task statuses have a display mapping."""
        for status in ("active", "complete", "failed", "aborted", "pending"):
            assert status in _STATUS_DISPLAY

    def test_display_tuples_are_two_element(self) -> None:
        for status, (indicator, color) in _STATUS_DISPLAY.items():
            assert isinstance(indicator, str)
            assert isinstance(color, str)
            assert color.startswith("#")


# ---------------------------------------------------------------------------
# Category colors mapping
# ---------------------------------------------------------------------------


class TestCategoryColors:
    """Tests for _CATEGORY_COLORS coverage."""

    def test_all_categories_have_colors(self) -> None:
        for cat in ("reads", "writes", "shell", "searches", "other"):
            assert cat in _CATEGORY_COLORS

    def test_colors_are_hex(self) -> None:
        for color in _CATEGORY_COLORS.values():
            assert color.startswith("#")

    def test_colors_match_palette(self) -> None:
        assert _CATEGORY_COLORS["reads"] == PALETTE["accent-teal"].to_hex()
        assert _CATEGORY_COLORS["writes"] == PALETTE["accent-gold"].to_hex()
        assert _CATEGORY_COLORS["shell"] == PALETTE["accent-violet"].to_hex()
        assert _CATEGORY_COLORS["searches"] == PALETTE["accent-blue"].to_hex()
        assert _CATEGORY_COLORS["other"] == PALETTE["text-muted"].to_hex()


# ---------------------------------------------------------------------------
# Screen pattern conformance
# ---------------------------------------------------------------------------


class TestScreenPattern:
    """Tests that AgentDetailScreen follows existing push-screen patterns."""

    def test_bindings_include_escape(self) -> None:
        has_escape = any(
            b.key == "escape" for b in AgentDetailScreen.BINDINGS
        )
        assert has_escape

    def test_screen_type_is_none(self) -> None:
        """Screen returns None (matches Screen[None] pattern)."""
        # Verify via class hierarchy -- Screen[None] is the base.
        import inspect
        bases = inspect.getmro(AgentDetailScreen)
        assert any("Screen" in cls.__name__ for cls in bases)

    def test_default_css_includes_surface_background(self) -> None:
        surface_hex = PALETTE["surface"].to_hex()
        assert surface_hex in AgentDetailScreen.DEFAULT_CSS

    def test_default_css_includes_stream_height(self) -> None:
        assert "1fr" in AgentDetailScreen.DEFAULT_CSS


# ---------------------------------------------------------------------------
# Screens __init__ export
# ---------------------------------------------------------------------------


class TestScreensExport:
    """Tests that AgentDetailScreen is exported from clou.ui.screens."""

    def test_importable_from_screens(self) -> None:
        from clou.ui.screens import AgentDetailScreen as Imported
        assert Imported is AgentDetailScreen

    def test_in_all(self) -> None:
        from clou.ui import screens
        assert "AgentDetailScreen" in screens.__all__


# ---------------------------------------------------------------------------
# _append_invocation behavior
# ---------------------------------------------------------------------------


class TestAppendInvocation:
    """Tests for _append_invocation streaming logic."""

    def test_sets_base_time_on_first_call(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        assert screen._base_time is None
        inv = ToolInvocation(name="Read", timestamp=200.0, category="reads")
        # Patch query_one to avoid needing live app
        screen.query_one = lambda *a, **kw: _FakeRichLog()
        screen._append_invocation(inv)
        assert screen._base_time == 200.0

    def test_increments_invocation_count(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        screen._base_time = 100.0
        screen.query_one = lambda *a, **kw: _FakeRichLog()
        assert screen._invocation_count == 0
        inv = ToolInvocation(name="Edit", timestamp=105.0, category="writes")
        screen._append_invocation(inv)
        assert screen._invocation_count == 1
        inv2 = ToolInvocation(name="Bash", timestamp=110.0, category="shell")
        screen._append_invocation(inv2)
        assert screen._invocation_count == 2

    def test_preserves_base_time_on_subsequent_calls(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        screen._base_time = 100.0
        screen.query_one = lambda *a, **kw: _FakeRichLog()
        inv = ToolInvocation(name="Read", timestamp=200.0, category="reads")
        screen._append_invocation(inv)
        assert screen._base_time == 100.0  # unchanged


class _FakeRichLog:
    """Minimal stub for RichLog to avoid needing a live app."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def clear(self) -> None:
        self.lines.clear()


# ---------------------------------------------------------------------------
# Agent completion header refresh
# ---------------------------------------------------------------------------


class TestAgentCompleteHeaderRefresh:
    """Tests that on_clou_agent_complete refreshes the header."""

    def test_update_header_called_on_agent_complete(self) -> None:
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="my_task", task_state=state)
        updated = []
        screen._update_header = lambda: updated.append(True)
        msg = ClouAgentComplete(task_id="agent-1", status="complete", summary="done")
        screen.on_clou_agent_complete(msg)
        assert len(updated) == 1

    def test_header_reflects_status_change(self) -> None:
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="my_task", task_state=state)
        header_active = screen._build_header()
        assert "running" in header_active
        # Simulate app updating state before message reaches screen
        state.status = "complete"
        header_complete = screen._build_header()
        assert "complete" in header_complete
        assert "running" not in header_complete

    def test_header_reflects_failed_status(self) -> None:
        state = TaskState(status="active")
        screen = AgentDetailScreen(task_name="my_task", task_state=state)
        state.status = "failed"
        header = screen._build_header()
        assert "failed" in header


# ---------------------------------------------------------------------------
# Aborted status
# ---------------------------------------------------------------------------


class TestAbortedStatus:
    """Tests for aborted status display."""

    def test_aborted_header(self) -> None:
        state = TaskState(status="aborted")
        screen = AgentDetailScreen(task_name="t", task_state=state)
        header = screen._build_header()
        assert "aborted" in header

    def test_aborted_uses_rose_accent(self) -> None:
        state = TaskState(status="aborted")
        screen = AgentDetailScreen(task_name="t", task_state=state)
        header = screen._build_header()
        _, rose_hex = _STATUS_DISPLAY["aborted"]
        assert rose_hex in header


# ---------------------------------------------------------------------------
# Empty state messages
# ---------------------------------------------------------------------------


class TestEmptyStateMessages:
    """Tests for empty state messaging in _render_existing_history."""

    def test_active_no_invocations_shows_waiting(self) -> None:
        state = TaskState(status="active", tool_invocations=[])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._render_existing_history()
        assert any("Waiting for agent activity" in line for line in fake_log.lines)

    def test_complete_no_invocations_shows_no_tool_calls(self) -> None:
        state = TaskState(status="complete", tool_invocations=[])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._render_existing_history()
        assert any("no tool calls" in line for line in fake_log.lines)

    def test_failed_no_invocations_shows_no_tool_calls(self) -> None:
        state = TaskState(status="failed", tool_invocations=[])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._render_existing_history()
        assert any("no tool calls" in line for line in fake_log.lines)

    def test_none_state_shows_waiting(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._render_existing_history()
        assert any("Waiting for agent activity" in line for line in fake_log.lines)


# ---------------------------------------------------------------------------
# History rendering
# ---------------------------------------------------------------------------


class TestRenderExistingHistory:
    """Tests for _render_existing_history with invocations."""

    def test_renders_all_invocations(self) -> None:
        invs = [
            ToolInvocation(name="Read", timestamp=100.0, category="reads"),
            ToolInvocation(name="Edit", timestamp=105.0, category="writes"),
            ToolInvocation(name="Bash", timestamp=110.0, category="shell"),
        ]
        state = TaskState(status="active", tool_invocations=invs)
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._render_existing_history()
        assert len(fake_log.lines) == 3
        assert screen._invocation_count == 3
        assert screen._base_time == 100.0

    def test_base_time_set_from_first_invocation(self) -> None:
        inv = ToolInvocation(name="Grep", timestamp=42.0, category="searches")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._render_existing_history()
        assert screen._base_time == 42.0


# ---------------------------------------------------------------------------
# Rerender with expansion
# ---------------------------------------------------------------------------


class TestRerenderStream:
    """Tests for _rerender_stream with expanded entries."""

    def test_expanded_entry_shows_detail(self) -> None:
        inv = ToolInvocation(
            name="Edit",
            timestamp=100.0,
            category="writes",
            input_summary="old='a'",
            output_summary="applied",
        )
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        screen._expanded_entries = {0}
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._rerender_stream()
        # Should have the invocation line + the expanded detail
        assert len(fake_log.lines) == 2
        assert "input:" in fake_log.lines[1]
        assert "output:" in fake_log.lines[1]

    def test_collapsed_entry_no_detail(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        screen._expanded_entries = set()  # nothing expanded
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._rerender_stream()
        assert len(fake_log.lines) == 1

    def test_empty_invocations_does_nothing(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._rerender_stream()
        assert len(fake_log.lines) == 0


# ---------------------------------------------------------------------------
# App integration -- RequestAgentDetail handler
# ---------------------------------------------------------------------------


class TestAppRequestAgentDetail:
    """Tests for the on_request_agent_detail handler in app.py."""

    @staticmethod
    def _make_mock_app(
        *,
        has_screen: bool = False,
        model: object | None = None,
    ) -> MagicMock:
        """Build a MagicMock with the attributes on_request_agent_detail reads."""
        app = MagicMock()
        app._has_screen = MagicMock(return_value=has_screen)
        app._task_graph_model = model
        app.push_screen = MagicMock()
        return app

    def test_handler_pushes_detail_screen(self) -> None:
        """on_request_agent_detail pushes AgentDetailScreen for known task."""
        from clou.ui.app import ClouApp
        from clou.ui.messages import RequestAgentDetail
        from clou.ui.task_graph import TaskGraphModel

        model = TaskGraphModel(
            tasks=[{"name": "build_widget"}], deps={},
        )
        model.task_states["build_widget"].status = "active"
        app = self._make_mock_app(model=model)

        msg = RequestAgentDetail(task_name="build_widget")
        ClouApp.on_request_agent_detail(app, msg)

        app.push_screen.assert_called_once()
        pushed = app.push_screen.call_args[0][0]
        assert isinstance(pushed, AgentDetailScreen)
        assert pushed._task_name == "build_widget"

    def test_handler_guards_duplicate_screen(self) -> None:
        """on_request_agent_detail no-ops when screen already open."""
        from clou.ui.app import ClouApp
        from clou.ui.messages import RequestAgentDetail
        from clou.ui.task_graph import TaskGraphModel

        model = TaskGraphModel(
            tasks=[{"name": "build_widget"}], deps={},
        )
        app = self._make_mock_app(has_screen=True, model=model)

        msg = RequestAgentDetail(task_name="build_widget")
        ClouApp.on_request_agent_detail(app, msg)

        app.push_screen.assert_not_called()

    def test_handler_noops_without_model(self) -> None:
        """on_request_agent_detail no-ops when model is None."""
        from clou.ui.app import ClouApp
        from clou.ui.messages import RequestAgentDetail

        app = self._make_mock_app(model=None)

        msg = RequestAgentDetail(task_name="anything")
        ClouApp.on_request_agent_detail(app, msg)

        app.push_screen.assert_not_called()


# ---------------------------------------------------------------------------
# Invocation line rendering edge cases
# ---------------------------------------------------------------------------


class TestRenderInvocationLineEdgeCases:
    """Additional edge cases for _render_invocation_line."""

    def test_unknown_category_uses_muted_color(self) -> None:
        inv = ToolInvocation(name="CustomTool", timestamp=100.0, category="magic")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _MUTED_HEX in line

    def test_long_tool_name_not_truncated(self) -> None:
        inv = ToolInvocation(name="VeryLongToolName", timestamp=100.0, category="other")
        line = _render_invocation_line(inv, 100.0, 0)
        assert "VeryLongToolName" in line

    def test_time_formatting_in_line(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=165.0, category="reads")
        line = _render_invocation_line(inv, 100.0, 0)
        assert " 1:05" in line

    def test_dim_hex_used_for_timestamp(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        line = _render_invocation_line(inv, 100.0, 0)
        assert _DIM_HEX in line


# ---------------------------------------------------------------------------
# Cursor navigation and toggle_selected
# ---------------------------------------------------------------------------


class TestCursorNavigation:
    """Tests for cursor tracking and keyboard-driven navigation."""

    def test_cursor_index_defaults_to_zero(self) -> None:
        screen = AgentDetailScreen(task_name="test")
        assert screen._cursor_index == 0

    def test_action_toggle_selected_calls_toggle_entry(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        screen._rerender_stream = lambda: None
        screen._cursor_index = 0
        screen.action_toggle_selected()
        assert 0 in screen._expanded_entries

    def test_action_toggle_selected_uses_cursor_index(self) -> None:
        invs = [
            ToolInvocation(name="Read", timestamp=100.0, category="reads"),
            ToolInvocation(name="Edit", timestamp=105.0, category="writes"),
        ]
        state = TaskState(status="active", tool_invocations=invs)
        screen = AgentDetailScreen(task_name="test", task_state=state)
        screen._rerender_stream = lambda: None
        screen._cursor_index = 1
        screen.action_toggle_selected()
        assert 1 in screen._expanded_entries
        assert 0 not in screen._expanded_entries

    def test_action_cursor_down_increments(self) -> None:
        invs = [
            ToolInvocation(name="Read", timestamp=100.0, category="reads"),
            ToolInvocation(name="Edit", timestamp=105.0, category="writes"),
        ]
        state = TaskState(status="active", tool_invocations=invs)
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        assert screen._cursor_index == 0
        screen.action_cursor_down()
        assert screen._cursor_index == 1

    def test_action_cursor_down_does_not_exceed_bounds(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._cursor_index = 0
        screen.action_cursor_down()
        assert screen._cursor_index == 0  # only 1 item, can't go past index 0

    def test_action_cursor_up_decrements(self) -> None:
        invs = [
            ToolInvocation(name="Read", timestamp=100.0, category="reads"),
            ToolInvocation(name="Edit", timestamp=105.0, category="writes"),
        ]
        state = TaskState(status="active", tool_invocations=invs)
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._cursor_index = 1
        screen.action_cursor_up()
        assert screen._cursor_index == 0

    def test_action_cursor_up_does_not_go_negative(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._cursor_index = 0
        screen.action_cursor_up()
        assert screen._cursor_index == 0

    def test_cursor_clamped_on_rerender(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._cursor_index = 5  # artificially high
        screen._rerender_stream()
        assert screen._cursor_index == 0  # clamped to len-1 = 0

    def test_cursor_clamped_on_render_existing_history(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        state = TaskState(status="active", tool_invocations=[inv])
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._cursor_index = 10  # artificially high
        screen._render_existing_history()
        assert screen._cursor_index == 0  # clamped


# ---------------------------------------------------------------------------
# Focused line rendering indicator
# ---------------------------------------------------------------------------


class TestFocusedIndicator:
    """Tests that focused lines render with a visual indicator."""

    def test_focused_line_has_triangle(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        line = _render_invocation_line(inv, 100.0, 0, focused=True)
        assert "\u25b8" in line  # right-pointing small triangle

    def test_unfocused_line_has_space_prefix(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        line = _render_invocation_line(inv, 100.0, 0, focused=False)
        assert "\u25b8" not in line

    def test_default_focused_is_false(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=100.0, category="reads")
        line = _render_invocation_line(inv, 100.0, 0)
        assert "\u25b8" not in line

    def test_rerender_marks_focused_line(self) -> None:
        invs = [
            ToolInvocation(name="Read", timestamp=100.0, category="reads"),
            ToolInvocation(name="Edit", timestamp=105.0, category="writes"),
        ]
        state = TaskState(status="active", tool_invocations=invs)
        screen = AgentDetailScreen(task_name="test", task_state=state)
        fake_log = _FakeRichLog()
        screen.query_one = lambda *a, **kw: fake_log
        screen._cursor_index = 1
        screen._rerender_stream()
        # Second line (index 1) should have the triangle
        assert "\u25b8" in fake_log.lines[1]
        # First line (index 0) should not
        assert "\u25b8" not in fake_log.lines[0]


# ---------------------------------------------------------------------------
# Bindings include new keys
# ---------------------------------------------------------------------------


class TestNewBindings:
    """Tests that BINDINGS list includes enter, up, and down."""

    def test_enter_binding_present(self) -> None:
        keys = [b.key for b in AgentDetailScreen.BINDINGS]
        assert "enter" in keys

    def test_up_binding_present(self) -> None:
        keys = [b.key for b in AgentDetailScreen.BINDINGS]
        assert "up" in keys

    def test_down_binding_present(self) -> None:
        keys = [b.key for b in AgentDetailScreen.BINDINGS]
        assert "down" in keys

    def test_escape_still_present(self) -> None:
        keys = [b.key for b in AgentDetailScreen.BINDINGS]
        assert "escape" in keys


# ---------------------------------------------------------------------------
# F7 comment fix
# ---------------------------------------------------------------------------


class TestF7CommentFix:
    """Verify the on_clou_agent_complete comment is accurate."""

    def test_comment_matches_unconditional_behavior(self) -> None:
        """The comment should reflect that refresh is unconditional."""
        import inspect
        source = inspect.getsource(AgentDetailScreen.on_clou_agent_complete)
        assert "Unconditionally refresh" in source
        # Old inaccurate comment should NOT be present
        assert "We only need to refresh if" not in source
