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

    def test_all_rows_match_width(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        for y in range(len(w._row_map)):
            strip = w.render_line(y)
            assert strip.cell_length == 80


# ---------------------------------------------------------------------------
# test_pending_icon_color
# ---------------------------------------------------------------------------


class TestPendingRendering:
    """Pending tasks render as dim text with no icon."""

    def test_pending_has_no_icon(self) -> None:
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        # All tasks start as pending -- no status icon should appear.
        for y in range(len(w._row_map)):
            row_type, _data = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                text = _strip_text(strip)
                # No circle, no checkmark, no cross.
                assert "\u25cb" not in text  # ○
                assert "\u25c9" not in text  # ◉
                assert "\u2713" not in text  # ✓
                assert "\u2717" not in text  # ✗
                break

    def test_pending_uses_dim_luminance(self) -> None:
        """Pending task text uses _TEXT_PENDING_L luminance."""
        from clou.ui.widgets.task_graph import _TEXT_PENDING_L

        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        # Find a pending task row.
        for y in range(len(w._row_map)):
            row_type, _data = w._row_map[y]
            if row_type == "task":
                strip = w.render_line(y)
                # Name region char at index 3 (after 3-space indent).
                seg = strip._segments[3]
                expected_rgb = luminance_to_rgb(_TEXT_PENDING_L)
                er, eg, eb = expected_rgb
                expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
                actual_style = str(seg.style).lower()
                assert expected_hex in actual_style or (
                    f"rgb({er},{eg},{eb})"
                    in actual_style.replace(" ", "")
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
# test_layer_indentation
# ---------------------------------------------------------------------------


class TestLayerIndentation:
    """Layers distinguished by indentation depth."""

    def test_three_layer_model_no_spacers(self) -> None:
        """Three-layer chain has no spacers between layers."""
        model = _make_model()
        assert len(model.layers) == 3

        w = _make_sized_widget(80)
        w.update_model(model)

        row_types = [rt for rt, _ in w._row_map]
        assert row_types.count("spacer") == 0
        assert row_types.count("task") == 3

    def test_single_layer_model(self) -> None:
        """All independent tasks in one layer -- no spacers."""
        model = TaskGraphModel(
            tasks=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
            deps={"a": [], "b": [], "c": []},
        )
        assert len(model.layers) == 1

        w = _make_sized_widget(80)
        w.update_model(model)

        row_types = [rt for rt, _ in w._row_map]
        assert row_types.count("spacer") == 0
        assert row_types.count("task") == 3

    def test_layer_depth_increases_indent(self) -> None:
        """Deeper layers produce more leading whitespace."""
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)

        texts: dict[str, str] = {}
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task":
                strip = w.render_line(y)
                texts[str(data)] = _strip_text(strip)

        indent_0 = len(texts["build_model"]) - len(texts["build_model"].lstrip())
        indent_1 = len(texts["build_widget"]) - len(texts["build_widget"].lstrip())
        indent_2 = len(texts["integrate"]) - len(texts["integrate"].lstrip())
        assert indent_1 > indent_0
        assert indent_2 > indent_1

    def test_parallel_tasks_same_indent(self) -> None:
        """Tasks in the same layer share the same indentation."""
        model = TaskGraphModel(
            tasks=[{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}],
            deps={"a": [], "b": [], "c": ["a", "b"], "d": ["a", "b"]},
        )
        w = _make_sized_widget(80)
        w.update_model(model)

        texts: dict[str, str] = {}
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task":
                strip = w.render_line(y)
                texts[str(data)] = _strip_text(strip)

        indent_a = len(texts["a"]) - len(texts["a"].lstrip())
        indent_b = len(texts["b"]) - len(texts["b"].lstrip())
        indent_c = len(texts["c"]) - len(texts["c"].lstrip())
        indent_d = len(texts["d"]) - len(texts["d"].lstrip())
        assert indent_a == indent_b  # same layer
        assert indent_c == indent_d  # same layer
        assert indent_c > indent_a   # deeper layer

    def test_tasks_preserve_layer_order(self) -> None:
        """Tasks appear in dependency-layer order."""
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)

        task_order = [
            str(data) for rt, data in w._row_map if rt == "task"
        ]
        assert task_order == ["build_model", "build_widget", "integrate"]


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

        Pending tasks use _TEXT_PENDING_L.  Active tasks use _TEXT_DIM_L.
        """
        from clou.ui.widgets.task_graph import _TEXT_PENDING_L

        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)

        # Find the build_model task row (pending).
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                segs = strip._segments

                # Name region character (index 3, first name char after indent).
                name_seg = segs[3]
                expected_rgb = luminance_to_rgb(_TEXT_PENDING_L)
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


class TestHashStripping:
    """Agent hash suffixes stripped from display names."""

    def test_strips_hex_hash(self) -> None:
        assert TaskGraphWidget._clean_display_name(
            "Brutalist quality gate:a120b4a37b227f381"
        ) == "Brutalist quality gate"

    def test_strips_short_hash(self) -> None:
        assert TaskGraphWidget._clean_display_name(
            "Classify findings:a77fecd37b2"
        ) == "Classify findings"

    def test_preserves_non_hash(self) -> None:
        assert TaskGraphWidget._clean_display_name(
            "create_metrics_module"
        ) == "create_metrics_module"

    def test_preserves_colon_without_hex(self) -> None:
        assert TaskGraphWidget._clean_display_name(
            "task:setup_database"
        ) == "task:setup_database"


class TestWidgetEdgeCases:
    """Edge cases for widget rendering."""

    def test_zero_width_returns_empty_strip(self) -> None:
        w = _make_sized_widget(0)
        strip = w.render_line(0)
        assert strip.cell_length == 0

    def test_tool_count_not_in_default_row(self) -> None:
        """Tool count is hidden in the default task row (shown on expansion)."""
        w = _make_sized_widget(80)
        model = _make_model()
        model.update_progress("build_model", 7, "Read")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "7 tools" not in text
                return
        raise AssertionError("No build_model task row found")

    def test_last_tool_not_in_default_row(self) -> None:
        """Last tool name is hidden in the default task row."""
        w = _make_sized_widget(80)
        model = _make_model()
        model.update_progress("build_model", 3, "Grep")
        w.update_model(model)
        for y in range(len(w._row_map)):
            row_type, data = w._row_map[y]
            if row_type == "task" and data == "build_model":
                strip = w.render_line(y)
                text = _strip_text(strip)
                assert "Grep" not in text
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


# ---------------------------------------------------------------------------
# test_phase_aware_layout
# ---------------------------------------------------------------------------


class TestPhaseAwareLayout:
    """Phase label and ordering change with cycle_type."""

    def test_no_phase_label_without_cycle_type(self) -> None:
        """No phase_label row when cycle_type is empty."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        assert not any(rt == "phase_label" for rt, _ in w._row_map)

    def test_phase_label_present_with_cycle_type(self) -> None:
        """Phase label row appears when cycle_type is set."""
        w = _make_sized_widget()
        model = _make_model()
        w.update_model(model)
        w.cycle_type = "EXECUTE"
        assert w._row_map[0] == ("phase_label", "EXECUTE")

    def test_phase_label_renders_cycle_name(self) -> None:
        """Phase label row contains the cycle type text."""
        w = _make_sized_widget(80)
        model = _make_model()
        w.update_model(model)
        w.cycle_type = "ASSESS"
        strip = w.render_line(0)
        text = _strip_text(strip)
        assert "ASSESS" in text

    def test_execute_dag_first(self) -> None:
        """During EXECUTE, DAG tasks come before unmapped agents."""
        w = _make_sized_widget()
        model = _make_model()
        from clou.ui.task_graph import TaskState

        model.unmapped_agents["assessment:x"] = TaskState(status="active")
        w.update_model(model)
        w.cycle_type = "EXECUTE"
        task_entries = [(rt, d) for rt, d in w._row_map if rt == "task"]
        assert task_entries[0] == ("task", "build_model")

    def test_assess_unmapped_first(self) -> None:
        """During ASSESS, unmapped agents come before DAG tasks."""
        w = _make_sized_widget()
        model = _make_model()
        from clou.ui.task_graph import TaskState

        model.unmapped_agents["Classify:x"] = TaskState(status="active")
        w.update_model(model)
        w.cycle_type = "ASSESS"
        task_entries = [(rt, d) for rt, d in w._row_map if rt == "task"]
        assert task_entries[0] == ("task", "Classify:x")

    def test_dag_dim_during_assess(self) -> None:
        """DAG tasks use pending luminance when in ASSESS phase."""
        from clou.ui.widgets.task_graph import _TEXT_PENDING_L

        w = _make_sized_widget(80)
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.complete_task("build_model", "complete", "done")
        from clou.ui.task_graph import TaskState

        model.unmapped_agents["gate:x"] = TaskState(status="active")
        w.update_model(model)
        w.cycle_type = "ASSESS"

        # Find build_model (a DAG task, now secondary).
        for y in range(len(w._row_map)):
            rt, data = w._row_map[y]
            if rt == "task" and data == "build_model":
                strip = w.render_line(y)
                # Name char after icon column should use _TEXT_PENDING_L.
                segs = strip._segments
                # Find first non-space content char.
                for seg in segs[3:10]:
                    if seg.text.strip():
                        expected_rgb = luminance_to_rgb(_TEXT_PENDING_L)
                        er, eg, eb = expected_rgb
                        expected_hex = f"#{er:02x}{eg:02x}{eb:02x}"
                        actual = str(seg.style).lower()
                        assert expected_hex in actual, (
                            f"Expected {expected_hex} in {actual}"
                        )
                        return
        raise AssertionError("No build_model task row found")
