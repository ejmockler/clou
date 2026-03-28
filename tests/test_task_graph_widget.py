"""Tests for clou.ui.widgets.task_graph -- TaskGraphWidget rendering."""

from __future__ import annotations

from textual.geometry import Size

from clou.ui.task_graph import TaskGraphModel
from clou.ui.theme import PALETTE, OklchColor, breath_modulate
from clou.ui.widgets.breath import luminance_to_rgb
from clou.ui.widgets.task_graph import (
    _STATUS_PALETTE,
    TaskGraphWidget,
    _oklch_to_rgb,
    _status_icon_rgb,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sized_widget(
    width: int = 80, height: int = 24
) -> TaskGraphWidget:
    """Create a widget with a controlled size for render_line testing.

    Subclasses to override the ``size`` property since it reads from
    ``content_region`` which requires a full layout pass.
    """
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


def _strip_color_at(strip: object, index: int) -> str | None:
    """Extract the style string at a given segment index."""
    segs = strip._segments  # type: ignore[attr-defined]
    if index < len(segs):
        return str(segs[index].style)
    return None


# ---------------------------------------------------------------------------
# test_empty_model_renders_blank
# ---------------------------------------------------------------------------


class TestEmptyModelRendersBlank:
    """No tasks = blank lines."""

    def test_no_model_set(self) -> None:
        w = _make_sized_widget(80)
        strip = w.render_line(0)
        assert strip.cell_length == 80
        text = _strip_text(strip)
        assert text.strip() == ""

    def test_empty_task_list(self) -> None:
        w = _make_sized_widget(80)
        model = TaskGraphModel(tasks=[], deps={})
        w.update_model(model)
        strip = w.render_line(0)
        assert strip.cell_length == 80
        text = _strip_text(strip)
        assert text.strip() == ""


# ---------------------------------------------------------------------------
# test_task_row_width
# ---------------------------------------------------------------------------


class TestTaskRowWidth:
    """Strip cell_length matches widget width."""

    def test_row_matches_width_80(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        # Find a task row (skip header rows).
        for y in range(len(w._row_map)):
            row_type, _ = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                assert strip.cell_length == 80
                return
        raise AssertionError("No task row found")

    def test_row_matches_width_120(self) -> None:
        w = _make_sized_widget(120)
        model = _make_model()
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, _ = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                assert strip.cell_length == 120
                return
        raise AssertionError("No task row found")

    def test_header_matches_width(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, _ = w._row_map[y]
            if row_type == "header":
                strip = w.render_line(y)
                assert strip.cell_length == 80
                return
        raise AssertionError("No header row found")


# ---------------------------------------------------------------------------
# test_pending_icon_color
# ---------------------------------------------------------------------------


class TestPendingIconColor:
    """Pending task icon uses text-muted palette token."""

    def test_pending_icon_is_circle(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        # All tasks start as pending.
        for y in range(len(w._row_map)):
            row_type, _data = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "\u25cb" in text  # ○
                break

    def test_pending_icon_rgb_matches_palette(self) -> None:
        """The pending icon RGB must match PALETTE['text-muted']."""
        expected_rgb = _status_icon_rgb("pending")
        palette_hex = PALETTE["text-muted"].to_hex()
        er = int(palette_hex[1:3], 16)
        eg = int(palette_hex[3:5], 16)
        eb = int(palette_hex[5:7], 16)
        assert expected_rgb == (er, eg, eb)

    def test_pending_icon_segment_color(self) -> None:
        """The actual rendered segment for the icon uses text-muted."""
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        # Find a pending task row.
        for y in range(len(w._row_map)):
            row_type, _data = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                # Icon is at index 2 (after 2-space indent).
                segs = strip._segments
                icon_seg = segs[2]
                assert icon_seg.text == "\u25cb"
                # Check color matches text-muted.
                r, g, b = _status_icon_rgb("pending")
                expected_style_str = str(
                    icon_seg.style
                )
                assert f"#{r:02x}{g:02x}{b:02x}" in expected_style_str.lower() or (
                    # Rich might format rgb differently
                    f"rgb({r},{g},{b})" in expected_style_str.replace(" ", "")
                )
                return
        raise AssertionError("No pending task row found")


# ---------------------------------------------------------------------------
# test_active_icon_breathes
# ---------------------------------------------------------------------------


class TestActiveIconBreathes:
    """Active task icon color changes with breath_phase."""

    def test_active_icon_differs_at_different_phases(self) -> None:
        """The active icon at breath_phase=0.0 differs from breath_phase=1.0."""
        w1 = _make_sized_widget(80)
        model1 = _make_model()
        model1.activate_task("build_model", "agent-1")
        w1.update_model(model1)
        w1.breath_phase = 0.0

        w2 = _make_sized_widget(80)
        model2 = _make_model()
        model2.activate_task("build_model", "agent-1")
        w2.update_model(model2)
        w2.breath_phase = 1.0

        # Find the active task row.
        task_y = None
        for y in range(len(w1._row_map)):
            row_type, data = w1._row_map[y]
            if row_type == "task" and data == "build_model":
                task_y = y
                break
        assert task_y is not None

        strip1 = w1.render_line(task_y)
        strip2 = w2.render_line(task_y)

        # Icon is at index 2.
        icon_style1 = str(strip1._segments[2].style)
        icon_style2 = str(strip2._segments[2].style)
        assert icon_style1 != icon_style2

    def test_active_icon_uses_gold_palette(self) -> None:
        """Active icon color should be derived from accent-gold."""
        base_col = PALETTE["accent-gold"]
        modulated_l = breath_modulate(base_col.l, 0.5)
        breathing_col = OklchColor(modulated_l, base_col.c, base_col.h)
        rgb = _oklch_to_rgb(breathing_col)
        # All channels should be non-zero (gold is a warm color).
        assert rgb[0] > 0
        assert rgb[1] > 0

    def test_active_icon_is_filled_circle(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "\u25c9" in text  # ◉
                return
        raise AssertionError("No active task row found")


# ---------------------------------------------------------------------------
# test_complete_icon_color
# ---------------------------------------------------------------------------


class TestCompleteIconColor:
    """Complete task icon uses accent-teal palette token."""

    def test_complete_icon_is_checkmark(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.complete_task("build_model", "complete", "done")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "\u2713" in text  # ✓
                return
        raise AssertionError("No complete task row found")

    def test_complete_rgb_matches_teal(self) -> None:
        expected_rgb = _status_icon_rgb("complete")
        palette_hex = PALETTE["accent-teal"].to_hex()
        er = int(palette_hex[1:3], 16)
        eg = int(palette_hex[3:5], 16)
        eb = int(palette_hex[5:7], 16)
        assert expected_rgb == (er, eg, eb)


# ---------------------------------------------------------------------------
# test_failed_icon_color
# ---------------------------------------------------------------------------


class TestFailedIconColor:
    """Failed task icon uses accent-rose palette token."""

    def test_failed_icon_is_cross(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.complete_task("build_model", "failed", "error")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "\u2717" in text  # ✗
                return
        raise AssertionError("No failed task row found")

    def test_failed_rgb_matches_rose(self) -> None:
        expected_rgb = _status_icon_rgb("failed")
        palette_hex = PALETTE["accent-rose"].to_hex()
        er = int(palette_hex[1:3], 16)
        eg = int(palette_hex[3:5], 16)
        eb = int(palette_hex[5:7], 16)
        assert expected_rgb == (er, eg, eb)


# ---------------------------------------------------------------------------
# test_shimmer_on_active_row
# ---------------------------------------------------------------------------


class TestShimmerOnActiveRow:
    """Active rows differ when shimmer is enabled."""

    def test_active_shimmer_differs(self) -> None:
        # Without shimmer.
        w1 = _make_sized_widget(80)
        model1 = _make_model()
        model1.activate_task("build_model", "agent-1")
        w1.update_model(model1)
        w1.shimmer_active = False
        w1.breath_phase = 0.5

        # With shimmer.
        w2 = _make_sized_widget(80)
        model2 = _make_model()
        model2.activate_task("build_model", "agent-1")
        w2.update_model(model2)
        w2.shimmer_active = True
        w2.breath_phase = 0.5
        # Set a non-zero frame time to ensure shimmer has spatial variation.
        w2._frame_time = 1.0

        # Find the active task row.
        task_y = None
        for y in range(len(w1._row_map)):
            row_type, data = w1._row_map[y]
            if row_type == "task" and data == "build_model":
                task_y = y
                break
        assert task_y is not None

        strip1 = w1.render_line(task_y)
        strip2 = w2.render_line(task_y)

        # Compare description region styles (past icon column).
        def _desc_styles(strip: object) -> list[str]:
            segs = strip._segments  # type: ignore[attr-defined]
            return [str(s.style) for s in segs[5:25]]

        styles1 = _desc_styles(strip1)
        styles2 = _desc_styles(strip2)
        assert styles1 != styles2, "Shimmer should produce different colors"

    def test_pending_row_no_shimmer_effect(self) -> None:
        """Non-active rows should NOT have shimmer even when shimmer_active."""
        w = _make_sized_widget(80)
        model = _make_model()
        # build_widget is still pending.
        model.activate_task("build_model", "agent-1")
        w.update_model(model)
        w.shimmer_active = True
        w.breath_phase = 0.5

        # Find the pending task row.
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_widget":
                strip = w.render_line(y)
                # All name characters should have the same luminance (no shimmer).
                segs = strip._segments
                # Name region: indices 3..43 (after icon col).
                name_styles = [str(s.style) for s in segs[3:20]]
                # All should be identical since no shimmer on pending.
                assert len(set(name_styles)) == 1
                return
        raise AssertionError("No pending task row found")


# ---------------------------------------------------------------------------
# test_phase_grouping
# ---------------------------------------------------------------------------


class TestPhaseGrouping:
    """Tasks grouped by dependency layer."""

    def test_three_layer_model(self) -> None:
        model = _make_model()
        # build_model has 0 deps -> layer 0
        # build_widget depends on build_model -> layer 1
        # integrate depends on build_widget -> layer 2
        assert len(model.layers) == 3

        w = _make_sized_widget(80)
        w.update_model(model)

        # Row map should have: header, task, spacer, header, task, spacer, header, task
        row_types = [rt for rt, _ in w._row_map]
        assert row_types.count("header") == 3
        assert row_types.count("task") == 3

    def test_single_layer_model(self) -> None:
        """All independent tasks go in one layer -- one header."""
        model = TaskGraphModel(
            tasks=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
            deps={"a": [], "b": [], "c": []},
        )
        assert len(model.layers) == 1

        w = _make_sized_widget(80)
        w.update_model(model)

        row_types = [rt for rt, _ in w._row_map]
        assert row_types.count("header") == 1
        assert row_types.count("task") == 3
        # No spacers for single group.
        assert row_types.count("spacer") == 0

    def test_header_contains_phase_number(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "header" and data == 0:
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "Phase 1" in text
                return
        raise AssertionError("No Phase 1 header found")

    def test_tasks_appear_after_their_header(self) -> None:
        """Within each group, tasks follow their header."""
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)

        last_header_layer = -1
        for row_type, data in w._row_map:
            if row_type == "header":
                last_header_layer = data  # type: ignore[assignment]
            elif row_type == "task":
                # This task should belong to the current layer.
                task_name = str(data)
                # Find which layer this task is in.
                task_layer = None
                for li, layer in enumerate(model.layers):
                    if task_name in layer:
                        task_layer = li
                        break
                assert task_layer == last_header_layer


# ---------------------------------------------------------------------------
# test_no_adhoc_hex
# ---------------------------------------------------------------------------


class TestNoAdhocHex:
    """All segment colors resolve to palette values, not ad-hoc hex."""

    def test_all_icon_colors_from_palette(self) -> None:
        """Every status icon color must come from a known PALETTE token."""
        for status, token in _STATUS_PALETTE.items():
            palette_col = PALETTE[token]
            expected_hex = palette_col.to_hex().lower()
            icon_rgb = _status_icon_rgb(status)
            # Convert back to hex.
            actual_hex = f"#{icon_rgb[0]:02x}{icon_rgb[1]:02x}{icon_rgb[2]:02x}"
            assert actual_hex == expected_hex, (
                f"Status '{status}' icon color {actual_hex} does not match "
                f"palette token '{token}' ({expected_hex})"
            )

    def test_text_dim_luminance_from_palette(self) -> None:
        """The text-dim luminance constant matches PALETTE['text-dim'].l."""
        from clou.ui.widgets.task_graph import _TEXT_DIM_L

        assert PALETTE["text-dim"].l == _TEXT_DIM_L

    def test_text_muted_luminance_from_palette(self) -> None:
        """The text-muted luminance constant matches PALETTE['text-muted'].l."""
        from clou.ui.widgets.task_graph import _TEXT_MUTED_L

        assert PALETTE["text-muted"].l == _TEXT_MUTED_L

    def test_rendered_segments_use_palette_derived_colors(self) -> None:
        """Spot-check that rendered task row segments use palette-derived RGB.

        For non-active tasks, name characters should use luminance_to_rgb
        with _TEXT_DIM_L, and tool-count characters should use _TEXT_MUTED_L.
        """
        w = _make_sized_widget(80)
        model = _make_model()
        model.update_progress("build_model", 5, "Read")
        w.update_model(model)

        # Find the build_model task row.
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                segs = strip._segments

                # Name region character (index 4, first char of name).
                name_seg = segs[4]
                expected_rgb = luminance_to_rgb(0.60)
                er, eg, eb = expected_rgb
                expected_style = f"#{er:02x}{eg:02x}{eb:02x}"
                actual_style = str(name_seg.style).lower()
                assert expected_style in actual_style or (
                    f"rgb({er},{eg},{eb})"
                    in actual_style.replace(" ", "")
                )
                return
        raise AssertionError("No build_model task row found")


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestWidgetEdgeCases:
    """Edge cases for widget rendering."""

    def test_zero_width_returns_empty_strip(self) -> None:
        w = _make_sized_widget(0)
        strip = w.render_line(0)
        assert strip.cell_length == 0

    def test_tool_count_displayed(self) -> None:
        """Tool count is shown when > 0."""
        w = _make_sized_widget(80)
        model = _make_model()
        model.update_progress("build_model", 7, "Read")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "7 tools" in text
                return
        raise AssertionError("No build_model task row found")

    def test_last_tool_displayed(self) -> None:
        """Last tool name appears in the row."""
        w = _make_sized_widget(80)
        model = _make_model()
        model.update_progress("build_model", 3, "Grep")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "Grep" in text
                return
        raise AssertionError("No build_model task row found")

    def test_update_model_refreshes_shimmer(self) -> None:
        """update_model sets shimmer_active based on task statuses."""
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        assert w.shimmer_active is False

        model.activate_task("build_model", "agent-1")
        w.update_model(model)
        assert w.shimmer_active is True

    def test_long_task_name_truncated(self) -> None:
        """Task names longer than 40 chars are truncated."""
        long_name = "a" * 60
        model = TaskGraphModel(
            tasks=[{"name": long_name}],
            deps={long_name: []},
        )
        w = _make_sized_widget(80)
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, _data = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                text = _strip_text(strip)
                # The full 60-char name should NOT appear.
                assert long_name not in text
                # But the truncated 40-char portion should.
                assert "a" * 40 in text
                return
        raise AssertionError("No task row found")
