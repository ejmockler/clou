"""Tests for inline_preview phase: categorized activity, breathing dot, 'd' key, live refresh."""

from __future__ import annotations

import time

from textual.events import Key
from textual.geometry import Size

from clou.ui.messages import ClouToolCallRecorded, RequestAgentDetail
from clou.ui.task_graph import TaskGraphModel, ToolInvocation
from clou.ui.theme import PALETTE, OklchColor, breath_modulate
from clou.ui.widgets.breath import _STAGE_LUMINANCE, luminance_to_rgb
from clou.ui.widgets.task_graph import (
    _CAT_ORDER,
    TaskGraphWidget,
    _oklch_to_rgb,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sized_widget(
    width: int = 80, height: int = 24
) -> TaskGraphWidget:
    """Create a widget with a controlled size for render_line testing."""
    fake_size = Size(width, height)

    class _Sized(TaskGraphWidget):
        @property
        def size(self) -> Size:  # type: ignore[override]
            return fake_size

    w = _Sized()
    w.breath_phase = 0.5
    w.shimmer_active = False
    return w


def _make_model(
    tasks: list[dict[str, str]] | None = None,
    deps: dict[str, list[str]] | None = None,
) -> TaskGraphModel:
    """Build a TaskGraphModel from simple specs."""
    if tasks is None:
        tasks = [
            {"name": "build_model"},
            {"name": "build_widget"},
            {"name": "integrate"},
        ]
    if deps is None:
        deps = {
            "build_model": [],
            "build_widget": ["build_model"],
            "integrate": ["build_widget"],
        }
    return TaskGraphModel(tasks=tasks, deps=deps)


def _strip_text(strip: object) -> str:
    """Extract the full text content from a Strip."""
    return "".join(seg.text for seg in strip._segments)  # type: ignore[attr-defined]


def _make_key_event(key: str) -> Key:
    """Create a Textual Key event for testing."""
    char = key if len(key) == 1 else None
    return Key(key, char)


# ---------------------------------------------------------------------------
# TestCategorizedActivity -- I1: structured activity summary
# ---------------------------------------------------------------------------


class TestCategorizedActivity:
    """Expanded tool_groups row shows categorized activity instead of raw tool names."""

    def test_reads_category(self) -> None:
        """Read and Glob tools are grouped under 'reads'."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        model.add_tool_call("build_model", "Glob", "*.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "2 reads" in text
                return
        raise AssertionError("No tool_groups row found")

    def test_writes_category(self) -> None:
        """Edit and Write tools are grouped under 'writes'."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Edit", "fix")
        model.add_tool_call("build_model", "Write", "new.py")
        model.add_tool_call("build_model", "MultiEdit", "batch")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "3 writes" in text
                return
        raise AssertionError("No tool_groups row found")

    def test_shell_category(self) -> None:
        """Bash tool is grouped under 'shell'."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Bash", "ls")
        model.add_tool_call("build_model", "Bash", "pwd")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "2 shell" in text
                return
        raise AssertionError("No tool_groups row found")

    def test_searches_category(self) -> None:
        """Grep, WebSearch, WebFetch are grouped under 'searches'."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Grep", "pattern")
        model.add_tool_call("build_model", "WebSearch", "query")
        model.add_tool_call("build_model", "WebFetch", "url")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "3 searches" in text
                return
        raise AssertionError("No tool_groups row found")

    def test_other_category(self) -> None:
        """Unknown tools fall into 'other' category."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "CustomTool", "arg")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "1 other" in text
                return
        raise AssertionError("No tool_groups row found")

    def test_mixed_categories_with_separator(self) -> None:
        """Multiple categories are separated by middle dot."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "a.py")
        model.add_tool_call("build_model", "Edit", "b.py")
        model.add_tool_call("build_model", "Bash", "ls")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "1 reads" in text
                assert "1 writes" in text
                assert "1 shell" in text
                # Middle dot separates categories.
                assert "\u00b7" in text
                return
        raise AssertionError("No tool_groups row found")

    def test_category_order_matches_cat_order(self) -> None:
        """Categories appear in _CAT_ORDER: reads, writes, shell, searches, other."""
        w = _make_sized_widget()
        model = _make_model()
        # Add in reverse order to verify display order is not insertion order.
        model.add_tool_call("build_model", "CustomTool", "x")
        model.add_tool_call("build_model", "Grep", "y")
        model.add_tool_call("build_model", "Bash", "z")
        model.add_tool_call("build_model", "Edit", "w")
        model.add_tool_call("build_model", "Read", "v")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                reads_pos = text.index("reads")
                writes_pos = text.index("writes")
                shell_pos = text.index("shell")
                searches_pos = text.index("searches")
                other_pos = text.index("other")
                assert reads_pos < writes_pos < shell_pos < searches_pos < other_pos
                return
        raise AssertionError("No tool_groups row found")

    def test_empty_invocations_no_tool_groups_row(self) -> None:
        """No tool_groups row when tool_invocations is empty."""
        w = _make_sized_widget()
        model = _make_model()
        # No tool calls added.
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        tg_rows = [(rt, d) for rt, d in w._row_map if rt == "tool_groups"]
        assert len(tg_rows) == 0

    def test_zero_count_categories_omitted(self) -> None:
        """Categories with 0 tools are not shown."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "reads" in text
                assert "writes" not in text
                assert "shell" not in text
                assert "searches" not in text
                assert "other" not in text
                return
        raise AssertionError("No tool_groups row found")


# ---------------------------------------------------------------------------
# TestBreathingDot -- N1: breathing dot uses shared exp(sin(t)) LUT
# ---------------------------------------------------------------------------


class TestBreathingDot:
    """Active tasks show a breathing dot in the tool_groups row."""

    def test_active_task_has_dot(self) -> None:
        """Active task's tool_groups row contains the filled circle dot."""
        w = _make_sized_widget()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "\u25cf" in text  # filled circle
                return
        raise AssertionError("No tool_groups row found")

    def test_pending_task_no_dot(self) -> None:
        """Pending task's tool_groups row has blank instead of dot."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "\u25cf" not in text
                return
        raise AssertionError("No tool_groups row found")

    def test_complete_task_no_dot(self) -> None:
        """Complete task's tool_groups row has no breathing dot."""
        w = _make_sized_widget()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "file.py")
        model.complete_task("build_model", "complete", "done")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "\u25cf" not in text
                return
        raise AssertionError("No tool_groups row found")

    def test_breathing_dot_color_uses_accent_gold(self) -> None:
        """The breathing dot at position 4 uses accent-gold with breath_modulate."""
        w = _make_sized_widget()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                segs = strip._segments
                # Dot is at index 4.
                dot_seg = segs[4]
                assert dot_seg.text == "\u25cf"

                # Expected color from accent-gold + breath_modulate.
                base_col = PALETTE["accent-gold"]
                modulated_l = breath_modulate(base_col.l, 0.5)
                r, g, b = _oklch_to_rgb(
                    OklchColor(modulated_l, base_col.c, base_col.h)
                )
                expected_hex = f"#{r:02x}{g:02x}{b:02x}"
                actual_style = str(dot_seg.style).lower()
                assert expected_hex in actual_style, (
                    f"Expected {expected_hex} in {actual_style}"
                )
                return
        raise AssertionError("No tool_groups row found")

    def test_breathing_dot_changes_with_phase(self) -> None:
        """The breathing dot color differs between phase 0.0 and phase 1.0."""
        w1 = _make_sized_widget()
        model1 = _make_model()
        model1.activate_task("build_model", "agent-1")
        model1.add_tool_call("build_model", "Read", "file.py")
        w1.update_model(model1)
        w1._focused_index = 0
        w1.breath_phase = 0.0
        w1.on_key(_make_key_event("enter"))

        w2 = _make_sized_widget()
        model2 = _make_model()
        model2.activate_task("build_model", "agent-1")
        model2.add_tool_call("build_model", "Read", "file.py")
        w2.update_model(model2)
        w2._focused_index = 0
        w2.breath_phase = 1.0
        w2.on_key(_make_key_event("enter"))

        def _dot_style(widget: TaskGraphWidget) -> str:
            for y in range(len(widget._row_map)):
                rt, _data = widget._row_map[y]
                if rt == "tool_groups":
                    strip = widget.render_line(y)
                    return str(strip._segments[4].style)
            raise AssertionError("No tool_groups row found")

        style1 = _dot_style(w1)
        style2 = _dot_style(w2)
        assert style1 != style2, "Breathing dot should differ at different phases"

    def test_non_dot_chars_use_expansion_luminance(self) -> None:
        """Non-dot characters in tool_groups use expansion luminance, not gold."""
        w = _make_sized_widget()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                # Check a content character (e.g., index 7 which should be in the category text).
                text = _strip_text(strip)
                # Find first non-space character after the dot.
                content_start = 6  # After "    . " (4 spaces + dot + space)
                while content_start < len(text) and text[content_start] == " ":
                    content_start += 1
                if content_start < len(text):
                    seg = strip._segments[content_start]
                    # Should use expansion luminance (arrival since just expanded).
                    expected_rgb = luminance_to_rgb(_STAGE_LUMINANCE["arrival"])
                    er, eg, eb = expected_rgb
                    expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
                    actual_style = str(seg.style).lower()
                    assert expected_hex in actual_style, (
                        f"Expected arrival luminance {expected_hex} in {actual_style}"
                    )
                return
        raise AssertionError("No tool_groups row found")


# ---------------------------------------------------------------------------
# TestDKeyPostsRequestAgentDetail -- 'd' keybinding
# ---------------------------------------------------------------------------


class TestDKeyPostsRequestAgentDetail:
    """Pressing 'd' on a focused task posts RequestAgentDetail."""

    def test_d_key_recognized(self) -> None:
        """Pressing 'd' does not move focus or toggle expand."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 0
        initial_expanded = set(w._expanded)

        posted: list[object] = []
        w.post_message = lambda msg: posted.append(msg)  # type: ignore[assignment]

        w.on_key(_make_key_event("d"))
        # Focus unchanged, expanded unchanged.
        assert w._focused_index == 0
        assert w._expanded == initial_expanded

    def test_d_key_posts_request(self) -> None:
        """Pressing 'd' with a focused task posts RequestAgentDetail."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 0
        expected_name = w._focused_task_name()

        posted: list[object] = []
        w.post_message = lambda msg: posted.append(msg)  # type: ignore[assignment]

        w.on_key(_make_key_event("d"))
        detail_msgs = [m for m in posted if isinstance(m, RequestAgentDetail)]
        assert len(detail_msgs) == 1
        assert detail_msgs[0].task_name == expected_name

    def test_d_key_with_no_focus_does_nothing(self) -> None:
        """'d' with no focused task does not post RequestAgentDetail."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = -1

        posted: list[object] = []
        w.post_message = lambda msg: posted.append(msg)  # type: ignore[assignment]

        w.on_key(_make_key_event("d"))
        assert w._focused_index == -1
        detail_msgs = [m for m in posted if isinstance(m, RequestAgentDetail)]
        assert len(detail_msgs) == 0


# ---------------------------------------------------------------------------
# TestLiveRefresh -- I4: ClouToolCallRecorded triggers rebuild
# ---------------------------------------------------------------------------


class TestLiveRefresh:
    """ClouToolCallRecorded triggers row map rebuild for expanded tasks."""

    def test_rebuild_on_expanded_task(self) -> None:
        """Receiving ClouToolCallRecorded for an expanded task rebuilds row map."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "a.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        # Record the row map state.
        old_row_map = list(w._row_map)

        # Add another tool call.
        inv = model.add_tool_call("build_model", "Edit", "b.py")
        assert inv is not None

        # Simulate message.
        msg = ClouToolCallRecorded(task_name="build_model", invocation=inv)
        w.on_clou_tool_call_recorded(msg)

        # Row map should be rebuilt (still valid).
        assert any(rt == "tool_groups" for rt, _ in w._row_map)

    def test_no_rebuild_for_non_expanded_task(self) -> None:
        """ClouToolCallRecorded for a non-expanded task does not rebuild."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "a.py")
        w.update_model(model)
        # build_model is NOT expanded.

        old_row_map = list(w._row_map)

        inv = model.add_tool_call("build_model", "Edit", "b.py")
        assert inv is not None
        msg = ClouToolCallRecorded(task_name="build_model", invocation=inv)
        w.on_clou_tool_call_recorded(msg)

        # Row map should be unchanged since task is not expanded.
        assert w._row_map == old_row_map

    def test_no_rebuild_without_model(self) -> None:
        """ClouToolCallRecorded with no model set does not crash."""
        w = _make_sized_widget()
        inv = ToolInvocation(name="Read", timestamp=time.monotonic(), category="reads")
        msg = ClouToolCallRecorded(task_name="build_model", invocation=inv)
        # Should not raise.
        w.on_clou_tool_call_recorded(msg)

    def test_updated_content_after_live_refresh(self) -> None:
        """After live refresh, the tool_groups row shows updated category counts."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "a.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        # Verify initial state.
        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "1 reads" in text
                break

        # Add more tools and trigger refresh.
        inv = model.add_tool_call("build_model", "Edit", "b.py")
        assert inv is not None
        msg = ClouToolCallRecorded(task_name="build_model", invocation=inv)
        w.on_clou_tool_call_recorded(msg)

        # Verify updated state.
        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                text = _strip_text(w.render_line(y))
                assert "1 reads" in text
                assert "1 writes" in text
                return
        raise AssertionError("No tool_groups row found after refresh")


# ---------------------------------------------------------------------------
# TestRebuildRowMapUsesToolInvocations -- tool_invocations not tool_calls
# ---------------------------------------------------------------------------


class TestRebuildRowMapUsesToolInvocations:
    """_rebuild_row_map uses tool_invocations for expansion decisions."""

    def test_tool_invocations_triggers_tool_groups(self) -> None:
        """Task with tool_invocations (via add_tool_call) gets tool_groups row."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        tg_rows = [(rt, d) for rt, d in w._row_map if rt == "tool_groups"]
        assert len(tg_rows) == 1

    def test_multiple_expanded_tasks_independent(self) -> None:
        """Multiple expanded tasks each get their own category rendering."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "a.py")
        model.add_tool_call("build_widget", "Edit", "b.py")
        w.update_model(model)

        # Expand both.
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))
        w.on_key(_make_key_event("down"))
        w.on_key(_make_key_event("enter"))

        tg_rows = [(rt, d) for rt, d in w._row_map if rt == "tool_groups"]
        assert len(tg_rows) == 2

        # Verify each shows different content.
        texts: list[str] = []
        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                texts.append(_strip_text(w.render_line(y)))

        assert "reads" in texts[0]
        assert "writes" in texts[1]


# ---------------------------------------------------------------------------
# TestToolGroupsTruncation -- edge case: long text
# ---------------------------------------------------------------------------


class TestToolGroupsTruncation:
    """Very long category text is truncated at width boundary."""

    def test_text_does_not_exceed_width(self) -> None:
        """Tool groups text is truncated to widget width."""
        w = _make_sized_widget(width=30)
        model = _make_model()
        # Add many different category tools.
        model.add_tool_call("build_model", "Read", "a.py")
        model.add_tool_call("build_model", "Edit", "b.py")
        model.add_tool_call("build_model", "Bash", "ls")
        model.add_tool_call("build_model", "Grep", "pattern")
        model.add_tool_call("build_model", "CustomTool", "arg")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                # Strip width must match widget width.
                text = _strip_text(strip)
                assert len(text) == 30
                return
        raise AssertionError("No tool_groups row found")


# ---------------------------------------------------------------------------
# TestCatOrderConstant -- verify _CAT_ORDER is accessible
# ---------------------------------------------------------------------------


class TestCatOrderConstant:
    """The _CAT_ORDER constant is correctly defined."""

    def test_cat_order_has_five_entries(self) -> None:
        assert len(_CAT_ORDER) == 5

    def test_cat_order_contents(self) -> None:
        assert _CAT_ORDER == ["reads", "writes", "shell", "searches", "other"]
