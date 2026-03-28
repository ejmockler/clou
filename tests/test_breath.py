"""Tests for clou.ui.widgets.breath — breathing animation + shimmer."""

from __future__ import annotations

import time

import pytest
from textual.geometry import Size

from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouCoordinatorSpawned,
    ClouCycleComplete,
)
from clou.ui.mode import BreathStateMachine
from clou.ui.theme import PALETTE, cycle_color
from clou.ui.widgets.breath import (
    _CYCLE_RGB_CACHE,
    LABEL_WIDTH,
    MAX_EVENTS,
    SHIMMER_AMPLITUDE,
    BreathEventItem,
    BreathWidget,
    _cycle_type_rgb,
    compute_shimmer,
    luminance_to_rgb,
)

# ---------------------------------------------------------------------------
# BreathEventItem
# ---------------------------------------------------------------------------


class TestBreathEventItem:
    """Event data model and lifecycle."""

    def test_defaults(self) -> None:
        ev = BreathEventItem(text="hello", cycle_type="PLAN")
        assert ev.luminance_state == "arrival"
        assert ev.phase is None

    def test_lifecycle_arrival(self) -> None:
        now = time.monotonic()
        ev = BreathEventItem(text="x", cycle_type="PLAN", timestamp=now)
        ev.update_state(now + 0.05)
        assert ev.luminance_state == "arrival"

    def test_lifecycle_linger(self) -> None:
        now = time.monotonic()
        ev = BreathEventItem(text="x", cycle_type="PLAN", timestamp=now)
        ev.update_state(now + 0.5)
        assert ev.luminance_state == "linger"

    def test_lifecycle_settle(self) -> None:
        now = time.monotonic()
        ev = BreathEventItem(text="x", cycle_type="PLAN", timestamp=now)
        ev.update_state(now + 3.0)
        assert ev.luminance_state == "settle"

    def test_lifecycle_resting(self) -> None:
        now = time.monotonic()
        ev = BreathEventItem(text="x", cycle_type="PLAN", timestamp=now)
        ev.update_state(now + 5.0)
        assert ev.luminance_state == "resting"


# ---------------------------------------------------------------------------
# compute_breath (from mode.py, used by widget)
# ---------------------------------------------------------------------------


class TestComputeBreath:
    """Verify the breathing formula returns [0, 1]."""

    @pytest.mark.parametrize("t", [0.0, 1.0, 2.0, 3.0, 4.5, 10.0])
    def test_range(self, t: float) -> None:
        v = BreathStateMachine.compute_breath(t)
        assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# compute_shimmer
# ---------------------------------------------------------------------------


class TestComputeShimmer:
    """Shimmer sine wave stays within expected amplitude."""

    @pytest.mark.parametrize("x", [0, 5, 10, 20, 40, 79])
    @pytest.mark.parametrize("t", [0.0, 1.0, 2.5, 5.0])
    def test_amplitude_bounds(self, x: int, t: float) -> None:
        v = compute_shimmer(x, t)
        assert -SHIMMER_AMPLITUDE - 1e-12 <= v <= SHIMMER_AMPLITUDE + 1e-12

    def test_spatial_variation(self) -> None:
        """Different columns produce different values at same time."""
        vals = [compute_shimmer(x, 1.0) for x in range(20)]
        assert len(set(vals)) > 1


# ---------------------------------------------------------------------------
# luminance_to_rgb
# ---------------------------------------------------------------------------


class TestLuminanceToRgb:
    """Pre-computed LUT maps lightness to valid RGB."""

    def test_black(self) -> None:
        r, g, b = luminance_to_rgb(0.0)
        assert r == 0 and g == 0 and b == 0

    def test_high_luminance(self) -> None:
        r, g, b = luminance_to_rgb(1.0)
        assert r > 200 and g > 200 and b > 200

    def test_clamp_above(self) -> None:
        r, g, b = luminance_to_rgb(1.5)
        r2, g2, b2 = luminance_to_rgb(1.0)
        assert (r, g, b) == (r2, g2, b2)

    def test_clamp_below(self) -> None:
        r, g, b = luminance_to_rgb(-0.5)
        r2, g2, b2 = luminance_to_rgb(0.0)
        assert (r, g, b) == (r2, g2, b2)


# ---------------------------------------------------------------------------
# BreathWidget event handling (unit-level, no app mount)
# ---------------------------------------------------------------------------


class TestBreathWidgetEvents:
    """Message handlers store events correctly."""

    def _make_widget(self) -> BreathWidget:
        return BreathWidget()

    def test_breath_event_stored(self) -> None:
        w = self._make_widget()
        w._add_event(text="compose.py updated", cycle_type="PLAN", phase="foundation")
        assert len(w._events) == 1
        assert w._events[0].text == "compose.py updated"
        assert w._events[0].cycle_type == "PLAN"
        assert w._events[0].phase == "foundation"

    def test_agent_spawned_event(self) -> None:
        w = self._make_widget()
        w._add_event(
            text="agent:abc  dispatched  setup database",
            cycle_type="EXECUTE",
        )
        assert len(w._events) == 1
        assert "dispatched" in w._events[0].text

    def test_agent_complete_event(self) -> None:
        w = self._make_widget()
        w._add_event(
            text="agent:abc  completed  database ready",
            cycle_type="EXECUTE",
        )
        assert len(w._events) == 1
        assert "completed" in w._events[0].text

    def test_cycle_complete_event(self) -> None:
        w = self._make_widget()
        w._add_event(
            text="cycle #1  PLAN complete  → EXECUTE",
            cycle_type="PLAN",
        )
        assert len(w._events) == 1
        assert "PLAN complete" in w._events[0].text

    def test_event_cap(self) -> None:
        w = self._make_widget()
        for i in range(MAX_EVENTS + 10):
            w._add_event(text=f"event {i}", cycle_type="PLAN")
        assert len(w._events) == MAX_EVENTS
        # Oldest events are dropped; newest kept.
        assert w._events[-1].text == f"event {MAX_EVENTS + 9}"
        assert w._events[0].text == "event 10"


# ---------------------------------------------------------------------------
# Cycle-type colouring matches theme
# ---------------------------------------------------------------------------


class TestCycleTypeRgbFallback:
    """_cycle_type_rgb falls back to text-dim for unknown cycle types."""

    def test_nonexistent_cycle_type_returns_text_dim_rgb(self) -> None:
        _CYCLE_RGB_CACHE.pop("nonexistent_cycle_type", None)
        r, g, b = _cycle_type_rgb("nonexistent_cycle_type")
        assert isinstance(r, int) and isinstance(g, int) and isinstance(b, int)
        expected_hex = PALETTE["text-dim"].to_hex()
        er = int(expected_hex[1:3], 16)
        eg = int(expected_hex[3:5], 16)
        eb = int(expected_hex[5:7], 16)
        assert (r, g, b) == (er, eg, eb)


class TestCycleTypeColoring:
    """Cycle type labels use the correct theme colour."""

    @pytest.mark.parametrize("ct", ["PLAN", "EXECUTE", "ASSESS", "VERIFY"])
    def test_known_cycle_type(self, ct: str) -> None:
        hex_str = cycle_color(ct)
        assert hex_str.startswith("#")
        assert len(hex_str) == 7

    def test_unknown_cycle_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown cycle type"):
            cycle_color("UNKNOWN")


# ---------------------------------------------------------------------------
# render_line produces Strip with correct width
# ---------------------------------------------------------------------------


class TestRenderLine:
    """render_line output shape and content."""

    @staticmethod
    def _make_sized_widget(width: int = 80, height: int = 24) -> BreathWidget:
        """Create a widget with a controlled size for render_line testing.

        We subclass to override the ``size`` property since it reads from
        ``content_region`` which requires a full layout pass.
        """
        fake_size = Size(width, height)

        class _Sized(BreathWidget):
            @property
            def size(self) -> Size:  # type: ignore[override]
                return fake_size

        w = _Sized()
        w.breath_phase = 0.5
        w.shimmer_active = False
        return w

    def test_empty_line_is_blank(self) -> None:
        w = self._make_sized_widget(80)
        strip = w.render_line(0)
        assert strip.cell_length == 80

    def test_event_line_width(self) -> None:
        w = self._make_sized_widget(80)
        w._add_event(text="compose.py updated", cycle_type="PLAN")
        strip = w.render_line(0)  # y=0 maps to most recent event
        assert strip.cell_length == 80

    def test_event_line_has_segments(self) -> None:
        w = self._make_sized_widget(40)
        w._add_event(text="test", cycle_type="EXECUTE")
        strip = w.render_line(0)
        # Each character should be a separate segment for per-char colour.
        total_chars = sum(len(seg.text) for seg in strip._segments)
        assert total_chars == 40

    def test_label_column_content(self) -> None:
        w = self._make_sized_widget(40)
        w._add_event(text="desc", cycle_type="PLAN")
        strip = w.render_line(0)
        label_text = "".join(seg.text for seg in strip._segments[:LABEL_WIDTH])
        assert label_text.strip() == "PLAN"

    def test_zero_width_returns_blank(self) -> None:
        w = self._make_sized_widget(0)
        strip = w.render_line(0)
        assert strip.cell_length == 0

    def test_shimmer_changes_output(self) -> None:
        """With shimmer active, description segments differ from no-shimmer."""
        w = self._make_sized_widget(40)
        w._add_event(text="something happening", cycle_type="EXECUTE")

        w.shimmer_active = False
        strip_no = w.render_line(0)

        w.shimmer_active = True
        strip_yes = w.render_line(0)

        # Extract colours from description region (past label).
        def _desc_colors(strip: object) -> list[str | None]:
            segs = strip._segments  # type: ignore[attr-defined]
            return [str(s.style) for s in segs[LABEL_WIDTH : LABEL_WIDTH + 10]]

        # At least one segment should differ.
        no_cols = _desc_colors(strip_no)
        yes_cols = _desc_colors(strip_yes)
        assert no_cols != yes_cols


# ---------------------------------------------------------------------------
# Shimmer reset on agent completion
# ---------------------------------------------------------------------------


class TestShimmerReset:
    """shimmer_active resets to False when all agents complete."""

    def test_shimmer_resets_after_all_agents_complete(self) -> None:
        w = BreathWidget()

        # Spawn two agents.
        w.on_clou_agent_spawned(ClouAgentSpawned(task_id="a1", description="task one"))
        w.on_clou_agent_spawned(ClouAgentSpawned(task_id="a2", description="task two"))
        assert w.shimmer_active is True
        assert w._active_agent_count == 2

        # Complete first — shimmer should remain active.
        w.on_clou_agent_complete(
            ClouAgentComplete(task_id="a1", status="completed", summary="done")
        )
        assert w.shimmer_active is True
        assert w._active_agent_count == 1

        # Complete second — shimmer should turn off.
        w.on_clou_agent_complete(
            ClouAgentComplete(task_id="a2", status="completed", summary="done")
        )
        assert w.shimmer_active is False
        assert w._active_agent_count == 0

    def test_agent_count_does_not_go_negative(self) -> None:
        w = BreathWidget()

        # Complete without a prior spawn.
        w.on_clou_agent_complete(
            ClouAgentComplete(task_id="x", status="completed", summary="ok")
        )
        assert w._active_agent_count == 0
        assert w.shimmer_active is False

    def test_coordinator_spawned_resets_agent_tracking(self) -> None:
        w = BreathWidget()

        # Simulate some active agents.
        w._active_agent_count = 3
        w.shimmer_active = True

        # New coordinator session should reset everything.
        w.on_clou_coordinator_spawned(ClouCoordinatorSpawned(milestone="test"))
        assert w._active_agent_count == 0
        assert w.shimmer_active is False

    def test_agent_progress_is_ambient_only(self) -> None:
        """Agent progress is ambient (shimmer), not a visible event line."""
        w = BreathWidget()

        w.on_clou_agent_progress(
            ClouAgentProgress(
                task_id="a1",
                last_tool="Read",
                total_tokens=5000,
                tool_uses=3,
            )
        )
        assert len(w._events) == 0  # no visible line added

    def test_agent_progress_does_not_change_agent_count(self) -> None:
        w = BreathWidget()

        # Spawn an agent first.
        w.on_clou_agent_spawned(ClouAgentSpawned(task_id="a1", description="task"))
        assert w._active_agent_count == 1
        assert w.shimmer_active is True

        # Progress should not change count or shimmer.
        w.on_clou_agent_progress(
            ClouAgentProgress(
                task_id="a1",
                last_tool="Grep",
                total_tokens=1000,
                tool_uses=1,
            )
        )
        assert w._active_agent_count == 1
        assert w.shimmer_active is True

    def test_cycle_complete_adds_event(self) -> None:
        w = BreathWidget()

        w.on_clou_cycle_complete(
            ClouCycleComplete(
                cycle_num=1,
                cycle_type="PLAN",
                next_step="EXECUTE",
                phase_status={},
            )
        )
        assert len(w._events) == 1
        assert "cycle #1" in w._events[0].text
        assert "PLAN complete" in w._events[0].text
        assert "\u2192 EXECUTE" in w._events[0].text
