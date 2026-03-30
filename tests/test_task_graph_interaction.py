"""Tests for TaskGraphWidget keyboard navigation and drill-down expansion."""

from __future__ import annotations

import time

from textual.events import Key
from textual.geometry import Size

from clou.ui.task_graph import TaskGraphModel
from clou.ui.widgets.breath import (
    _ARRIVAL_DURATION,
    _LINGER_DURATION,
    _SETTLE_DURATION,
    _STAGE_LUMINANCE,
    luminance_to_rgb,
)
from clou.ui.widgets.task_graph import (
    _FOCUS_BOOST,
    _TEXT_DIM_L,
    TaskGraphWidget,
    _expansion_luminance,
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
    # Key events in Textual take (key, character)
    char = key if len(key) == 1 else None
    return Key(key, char)


# ---------------------------------------------------------------------------
# test_focus_moves_down
# ---------------------------------------------------------------------------


class TestFocusMovesDown:
    """Down arrow increments _focused_index."""

    def test_down_from_none_to_zero(self) -> None:
        """First down arrow moves focus to first task."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        assert w._focused_index == -1

        w.on_key(_make_key_event("down"))
        assert w._focused_index == 0

    def test_down_increments(self) -> None:
        """Successive down arrows increment focus."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("down"))
        assert w._focused_index == 1

        w.on_key(_make_key_event("down"))
        assert w._focused_index == 2

    def test_down_with_no_tasks(self) -> None:
        """Down arrow with no tasks does nothing."""
        w = _make_sized_widget()
        model = TaskGraphModel(tasks=[], deps={})
        w.update_model(model)
        w.on_key(_make_key_event("down"))
        assert w._focused_index == -1


# ---------------------------------------------------------------------------
# test_focus_moves_up
# ---------------------------------------------------------------------------


class TestFocusMovesUp:
    """Up arrow decrements _focused_index."""

    def test_up_from_none_to_zero(self) -> None:
        """First up arrow moves focus to first task (from unfocused)."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = -1

        w.on_key(_make_key_event("up"))
        assert w._focused_index == 0

    def test_up_decrements(self) -> None:
        """Up arrow decrements focus from non-zero."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 2

        w.on_key(_make_key_event("up"))
        assert w._focused_index == 1


# ---------------------------------------------------------------------------
# test_focus_wraps_or_clamps
# ---------------------------------------------------------------------------


class TestFocusWrapsOrClamps:
    """Boundary behavior at top/bottom (clamps, does not wrap)."""

    def test_clamps_at_bottom(self) -> None:
        """Down arrow at last task stays at last."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        # 3 tasks: indices 0, 1, 2
        w._focused_index = 2

        w.on_key(_make_key_event("down"))
        assert w._focused_index == 2

    def test_clamps_at_top(self) -> None:
        """Up arrow at first task stays at first."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("up"))
        assert w._focused_index == 0

    def test_single_task_clamps(self) -> None:
        """With one task, both up and down stay at 0."""
        model = TaskGraphModel(
            tasks=[{"name": "only_task"}],
            deps={"only_task": []},
        )
        w = _make_sized_widget()
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("down"))
        assert w._focused_index == 0

        w.on_key(_make_key_event("up"))
        assert w._focused_index == 0


# ---------------------------------------------------------------------------
# test_enter_toggles_expand
# ---------------------------------------------------------------------------


class TestEnterTogglesExpand:
    """Enter adds/removes from _expanded."""

    def test_expand_on_enter(self) -> None:
        """Enter on a focused task expands it."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0  # build_model

        w.on_key(_make_key_event("enter"))
        assert "build_model" in w._expanded

    def test_collapse_on_second_enter(self) -> None:
        """Second Enter on the same task collapses it."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("enter"))
        assert "build_model" in w._expanded

        w.on_key(_make_key_event("enter"))
        assert "build_model" not in w._expanded

    def test_enter_with_no_focus_does_nothing(self) -> None:
        """Enter with _focused_index = -1 does nothing."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = -1

        w.on_key(_make_key_event("enter"))
        assert len(w._expanded) == 0

    def test_expand_records_timestamp(self) -> None:
        """Expanding a task records a monotonic timestamp."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0

        before = time.monotonic()
        w.on_key(_make_key_event("enter"))
        after = time.monotonic()

        assert "build_model" in w._expansion_states
        ts = w._expansion_states["build_model"]
        assert before <= ts <= after

    def test_collapse_removes_timestamp(self) -> None:
        """Collapsing removes the expansion state entry."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "file.py")
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("enter"))
        w.on_key(_make_key_event("enter"))
        assert "build_model" not in w._expansion_states


# ---------------------------------------------------------------------------
# test_focused_row_luminance_boost
# ---------------------------------------------------------------------------


class TestFocusedRowLuminanceBoost:
    """Focused row is brighter by _FOCUS_BOOST."""

    def test_focused_row_brighter_than_unfocused(self) -> None:
        """The focused task row segments have higher luminance."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)

        # Render without focus.
        w._focused_index = -1
        # Find build_model task row.
        task_y = None
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task" and data == "build_model":
                task_y = y
                break
        assert task_y is not None

        strip_unfocused = w.render_line(task_y)

        # Now set focus to first task (build_model).
        w._focused_index = 0
        strip_focused = w.render_line(task_y)

        # Compare name region styles (past icon column).
        unfocused_styles = [
            str(s.style) for s in strip_unfocused._segments[4:10]
        ]
        focused_styles = [
            str(s.style) for s in strip_focused._segments[4:10]
        ]
        assert unfocused_styles != focused_styles, (
            "Focused row should have different (brighter) styles"
        )

    def test_focus_boost_uses_correct_luminance(self) -> None:
        """Focused row name chars use _TEXT_DIM_L + _FOCUS_BOOST."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 0

        task_y = None
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task" and data == "build_model":
                task_y = y
                break
        assert task_y is not None

        strip = w.render_line(task_y)
        # Name region char at index 4.
        seg = strip._segments[4]
        expected_rgb = luminance_to_rgb(_TEXT_DIM_L + _FOCUS_BOOST)
        er, eg, eb = expected_rgb
        expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
        actual_style = str(seg.style).lower()
        assert expected_hex in actual_style, (
            f"Expected {expected_hex} in {actual_style}"
        )

    def test_unfocused_task_no_boost(self) -> None:
        """Non-focused tasks do not get the luminance boost."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        # Focus first task, check second task has no boost.
        w._focused_index = 0

        # Find build_widget task row (second task, unfocused).
        task_y = None
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task" and data == "build_widget":
                task_y = y
                break
        assert task_y is not None

        strip = w.render_line(task_y)
        seg = strip._segments[4]
        expected_rgb = luminance_to_rgb(_TEXT_DIM_L)  # no boost
        er, eg, eb = expected_rgb
        expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
        actual_style = str(seg.style).lower()
        assert expected_hex in actual_style

    def test_icon_also_gets_boost(self) -> None:
        """The status icon on a focused row also gets the luminance boost."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)

        # Find the task row index for build_model.
        task_y = None
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task" and data == "build_model":
                task_y = y
                break
        assert task_y is not None

        # Unfocused icon.
        w._focused_index = -1
        strip_unfocused = w.render_line(task_y)
        icon_style_unfocused = str(strip_unfocused._segments[2].style)

        # Focused icon.
        w._focused_index = 0
        strip_focused = w.render_line(task_y)
        icon_style_focused = str(strip_focused._segments[2].style)

        assert icon_style_focused != icon_style_unfocused, (
            "Focused icon should differ from unfocused"
        )


# ---------------------------------------------------------------------------
# test_drill_down_shows_tool_calls
# ---------------------------------------------------------------------------


class TestDrillDownShowsToolCalls:
    """Expanded task has tool call lines."""

    def test_tool_groups_row_appears(self) -> None:
        """Expanding a task adds a tool_groups row to the row map."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        model.add_tool_call("build_model", "Grep", "pattern")
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("enter"))

        # Check that a tool_groups row is in the row map.
        tg_rows = [
            (rt, data) for rt, data in w._row_map if rt == "tool_groups"
        ]
        assert len(tg_rows) == 1

    def test_tool_groups_text_content(self) -> None:
        """Tool groups row renders grouped tool counts."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        model.add_tool_call("build_model", "Read", "other.py")
        model.add_tool_call("build_model", "Grep", "pattern")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        # Find the tool_groups row.
        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "Read" in text
                assert "Grep" in text
                assert "2\u00d7" in text  # 2× Read
                return
        raise AssertionError("No tool_groups row found")

    def test_summary_row_appears(self) -> None:
        """Expanding a task with a summary shows the summary row."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        model.complete_task("build_model", "complete", "All done")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        # Find the summary row.
        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "summary":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "\u21b3" in text  # ↳
                assert "All done" in text
                return
        raise AssertionError("No summary row found")

    def test_no_summary_when_absent(self) -> None:
        """No summary row when task has no summary set."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        summary_rows = [
            (rt, data) for rt, data in w._row_map if rt == "summary"
        ]
        assert len(summary_rows) == 0

    def test_collapse_removes_drill_down(self) -> None:
        """Collapsing removes tool_groups and summary rows."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        model.complete_task("build_model", "complete", "All done")
        w.update_model(model)
        w._focused_index = 0

        w.on_key(_make_key_event("enter"))
        assert any(rt == "tool_groups" for rt, _ in w._row_map)

        w.on_key(_make_key_event("enter"))
        assert not any(rt == "tool_groups" for rt, _ in w._row_map)
        assert not any(rt == "summary" for rt, _ in w._row_map)

    def test_drill_down_indentation(self) -> None:
        """Drill-down lines are indented 4 spaces."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                text = _strip_text(strip)
                # Starts with 4-space indent.
                assert text.startswith("    ")
                return
        raise AssertionError("No tool_groups row found")


# ---------------------------------------------------------------------------
# test_drill_down_animation
# ---------------------------------------------------------------------------


class TestDrillDownAnimation:
    """Expansion lines follow arrival -> linger -> settle lifecycle."""

    def test_arrival_luminance(self) -> None:
        """Just-expanded drill-down lines use arrival luminance (0.88)."""
        now = time.monotonic()
        l_val = _expansion_luminance(now, now)
        assert l_val == _STAGE_LUMINANCE["arrival"]

    def test_linger_luminance(self) -> None:
        """After arrival period, lines use linger luminance (0.60)."""
        now = time.monotonic()
        expanded_at = now - (_ARRIVAL_DURATION + 0.01)
        l_val = _expansion_luminance(expanded_at, now)
        assert l_val == _STAGE_LUMINANCE["linger"]

    def test_settle_luminance(self) -> None:
        """After linger period, lines use settle luminance (0.45)."""
        now = time.monotonic()
        expanded_at = now - (_LINGER_DURATION + 0.01)
        l_val = _expansion_luminance(expanded_at, now)
        assert l_val == _STAGE_LUMINANCE["settle"]

    def test_resting_luminance(self) -> None:
        """After settle period, lines use resting luminance (0.45)."""
        now = time.monotonic()
        expanded_at = now - (_SETTLE_DURATION + 1.0)
        l_val = _expansion_luminance(expanded_at, now)
        assert l_val == _STAGE_LUMINANCE["resting"]

    def test_drill_down_line_uses_expansion_luminance(self) -> None:
        """Rendered tool_groups line luminance matches expansion lifecycle."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        w.update_model(model)
        w._focused_index = 0

        # Expand -- sets expansion timestamp to ~now.
        w.on_key(_make_key_event("enter"))

        # Since we just expanded, the luminance should be arrival (0.88).
        # Find the tool_groups row and check its luminance.
        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                seg = strip._segments[5]  # a content char
                # Arrival luminance -> RGB.
                expected_rgb = luminance_to_rgb(
                    _STAGE_LUMINANCE["arrival"]
                )
                er, eg, eb = expected_rgb
                expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
                actual_style = str(seg.style).lower()
                assert expected_hex in actual_style, (
                    f"Expected arrival luminance {expected_hex} "
                    f"in {actual_style}"
                )
                return
        raise AssertionError("No tool_groups row found")

    def test_aged_expansion_uses_lower_luminance(self) -> None:
        """Backdating the expansion timestamp produces lower luminance."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        w.update_model(model)
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        # Backdate expansion to well past settle.
        w._expansion_states["build_model"] = time.monotonic() - 10.0

        for y in range(len(w._row_map)):
            rt, _data = w._row_map[y]
            if rt == "tool_groups":
                strip = w.render_line(y)
                seg = strip._segments[5]
                expected_rgb = luminance_to_rgb(
                    _STAGE_LUMINANCE["resting"]
                )
                er, eg, eb = expected_rgb
                expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
                actual_style = str(seg.style).lower()
                assert expected_hex in actual_style, (
                    f"Expected resting luminance {expected_hex} "
                    f"in {actual_style}"
                )
                return
        raise AssertionError("No tool_groups row found")


# ---------------------------------------------------------------------------
# test_escape_defocuses
# ---------------------------------------------------------------------------


class TestEscapeDefocuses:
    """Escape sets _focused_index to -1."""

    def test_escape_from_focused(self) -> None:
        """Escape from a focused state returns to -1."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 1

        w.on_key(_make_key_event("escape"))
        assert w._focused_index == -1

    def test_escape_from_unfocused(self) -> None:
        """Escape when already unfocused stays at -1."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = -1

        w.on_key(_make_key_event("escape"))
        assert w._focused_index == -1


# ---------------------------------------------------------------------------
# Additional integration-style tests
# ---------------------------------------------------------------------------


class TestCanFocus:
    """Widget can_focus is set correctly."""

    def test_can_focus_is_true(self) -> None:
        w = _make_sized_widget()
        assert w.can_focus is True


class TestRowMapWithExpansion:
    """Row map correctly includes drill-down rows."""

    def test_expanded_task_adds_rows(self) -> None:
        """Expanding a task with tool calls + summary adds 2 rows."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        model.add_tool_call("build_model", "Write", "output.py")
        model.complete_task("build_model", "complete", "Done")
        w.update_model(model)

        before_count = len(w._row_map)

        w._focused_index = 0
        w.on_key(_make_key_event("enter"))

        after_count = len(w._row_map)
        # 1 tool_groups + 1 summary = 2 extra rows.
        assert after_count == before_count + 2

    def test_multiple_tasks_expanded(self) -> None:
        """Multiple tasks can be expanded simultaneously."""
        w = _make_sized_widget()
        model = _make_model()
        model.add_tool_call("build_model", "Read", "main.py")
        model.add_tool_call("build_widget", "Write", "widget.py")
        w.update_model(model)

        # Expand first task.
        w._focused_index = 0
        w.on_key(_make_key_event("enter"))
        assert "build_model" in w._expanded

        # Move to second task and expand.
        w.on_key(_make_key_event("down"))
        w.on_key(_make_key_event("enter"))
        assert "build_widget" in w._expanded
        assert "build_model" in w._expanded

        # Both should have tool_groups rows.
        tg_rows = [
            (rt, data) for rt, data in w._row_map if rt == "tool_groups"
        ]
        assert len(tg_rows) == 2


class TestFocusOnWidgetFocus:
    """When widget gains Textual focus, _focused_index auto-sets."""

    def test_auto_focus_on_widget_focus(self) -> None:
        """on_focus sets _focused_index to 0 when tasks exist."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        assert w._focused_index == -1

        # Simulate widget gaining focus.
        w.on_focus(object())
        assert w._focused_index == 0

    def test_no_auto_focus_with_no_tasks(self) -> None:
        """on_focus does not set focus when no tasks."""
        w = _make_sized_widget()
        model = TaskGraphModel(tasks=[], deps={})
        w.update_model(model)
        w.on_focus(object())
        assert w._focused_index == -1

    def test_no_reset_if_already_focused(self) -> None:
        """on_focus does not reset if already focused."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w._focused_index = 2

        w.on_focus(object())
        assert w._focused_index == 2
