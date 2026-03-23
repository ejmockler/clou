"""Tests for clou.ui.mode — mode transitions and breathing state machine."""

from __future__ import annotations

import math
from typing import ClassVar

import pytest

from clou.ui.messages import Mode
from clou.ui.mode import (
    TIMING,
    TRANSITIONS,
    BreathState,
    BreathStateMachine,
    TransitionMeta,
    get_transition,
)

# ---------------------------------------------------------------------------
# Legal mode transitions
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    """All 12 legal mode transitions return correct TransitionMeta."""

    @pytest.mark.parametrize(
        ("from_mode", "to_mode", "duration", "easing"),
        [
            (Mode.DIALOGUE, Mode.BREATH, 1500, "out_cubic"),
            (Mode.BREATH, Mode.DIALOGUE, 500, "out_cubic"),
            (Mode.BREATH, Mode.DECISION, 800, "in_cubic"),
            (Mode.DECISION, Mode.BREATH, 1500, "out_cubic"),
            (Mode.DIALOGUE, Mode.DECISION, 300, "ease-out"),
            (Mode.DECISION, Mode.DIALOGUE, 300, "ease-out"),
            (Mode.DECISION, Mode.HANDOFF, 500, "ease-out"),
            (Mode.BREATH, Mode.HANDOFF, 3000, "out_cubic"),
            (Mode.HANDOFF, Mode.DIALOGUE, 500, "out_cubic"),
            (Mode.HANDOFF, Mode.BREATH, 1500, "out_cubic"),
            (Mode.HANDOFF, Mode.DECISION, 300, "ease-out"),
            (Mode.DIALOGUE, Mode.HANDOFF, 500, "out_cubic"),
        ],
    )
    def test_legal_transition(
        self,
        from_mode: Mode,
        to_mode: Mode,
        duration: int,
        easing: str,
    ) -> None:
        meta = get_transition(from_mode, to_mode)
        assert meta is not None
        assert isinstance(meta, TransitionMeta)
        assert meta.duration_ms == duration
        assert meta.easing == easing

    def test_transitions_count(self) -> None:
        assert len(TRANSITIONS) == 12


# ---------------------------------------------------------------------------
# Illegal mode transitions
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    """Transitions not in the state diagram must return None."""

    @pytest.mark.parametrize(
        ("from_mode", "to_mode"),
        [
            (Mode.HANDOFF, Mode.HANDOFF),
            (Mode.DIALOGUE, Mode.DIALOGUE),
            (Mode.BREATH, Mode.BREATH),
            (Mode.DECISION, Mode.DECISION),
        ],
    )
    def test_illegal_transition(self, from_mode: Mode, to_mode: Mode) -> None:
        assert get_transition(from_mode, to_mode) is None


# ---------------------------------------------------------------------------
# BreathStateMachine — state management
# ---------------------------------------------------------------------------


class TestBreathStateMachine:
    """Breathing state machine behaviour."""

    def test_starts_idle(self) -> None:
        bsm = BreathStateMachine()
        assert bsm.state is BreathState.IDLE

    def test_idle_to_breathing_legal(self) -> None:
        bsm = BreathStateMachine()
        assert bsm.transition(BreathState.BREATHING) is True
        assert bsm.state is BreathState.BREATHING

    def test_idle_to_holding_illegal(self) -> None:
        bsm = BreathStateMachine()
        assert bsm.transition(BreathState.HOLDING) is False
        assert bsm.state is BreathState.IDLE

    def test_breathing_to_settling_legal(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        assert bsm.transition(BreathState.SETTLING) is True
        assert bsm.state is BreathState.SETTLING

    def test_settling_to_breathing_illegal(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        bsm.transition(BreathState.SETTLING)
        assert bsm.transition(BreathState.BREATHING) is False
        assert bsm.state is BreathState.SETTLING

    def test_holding_to_idle_interrupt(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        bsm.transition(BreathState.HOLDING)
        assert bsm.transition(BreathState.IDLE) is True
        assert bsm.state is BreathState.IDLE

    # --- RELEASING transitions ---

    def test_breathing_to_releasing_legal(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        assert bsm.transition(BreathState.RELEASING) is True
        assert bsm.state is BreathState.RELEASING

    def test_holding_to_releasing_legal(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        bsm.transition(BreathState.HOLDING)
        assert bsm.transition(BreathState.RELEASING) is True
        assert bsm.state is BreathState.RELEASING

    def test_releasing_to_settling_legal(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        bsm.transition(BreathState.RELEASING)
        assert bsm.transition(BreathState.SETTLING) is True
        assert bsm.state is BreathState.SETTLING

    def test_releasing_to_idle_interrupt(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        bsm.transition(BreathState.RELEASING)
        assert bsm.transition(BreathState.IDLE) is True
        assert bsm.state is BreathState.IDLE

    def test_idle_to_releasing_illegal(self) -> None:
        bsm = BreathStateMachine()
        assert bsm.transition(BreathState.RELEASING) is False
        assert bsm.state is BreathState.IDLE

    def test_settling_to_releasing_illegal(self) -> None:
        bsm = BreathStateMachine()
        bsm.transition(BreathState.BREATHING)
        bsm.transition(BreathState.SETTLING)
        assert bsm.transition(BreathState.RELEASING) is False
        assert bsm.state is BreathState.SETTLING

    def test_reset_from_any_state(self) -> None:
        for target in (
            BreathState.BREATHING,
            BreathState.HOLDING,
            BreathState.SETTLING,
        ):
            bsm = BreathStateMachine()
            bsm.transition(BreathState.BREATHING)
            if target is BreathState.HOLDING:
                bsm.transition(BreathState.HOLDING)
            elif target is BreathState.SETTLING:
                bsm.transition(BreathState.SETTLING)
            bsm.reset()
            assert bsm.state is BreathState.IDLE


# ---------------------------------------------------------------------------
# compute_breath
# ---------------------------------------------------------------------------


class TestComputeBreath:
    """The exp(sin(t)) normalised breathing formula."""

    @pytest.mark.parametrize("t", [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 100.0])
    def test_range_zero_to_one(self, t: float) -> None:
        value = BreathStateMachine.compute_breath(t)
        assert 0.0 <= value <= 1.0, f"compute_breath({t}) = {value}"

    def test_peak_at_quarter_period(self) -> None:
        period = 4.5
        t_peak = period / 4.0  # sin peaks at pi/2
        value = BreathStateMachine.compute_breath(t_peak, period)
        assert value == pytest.approx(1.0, abs=1e-9)

    def test_trough_at_three_quarter_period(self) -> None:
        period = 4.5
        t_trough = 3.0 * period / 4.0  # sin troughs at 3pi/2
        value = BreathStateMachine.compute_breath(t_trough, period)
        assert value == pytest.approx(0.0, abs=1e-9)

    def test_midpoint_value(self) -> None:
        """At t=0, sin(0)=0 so raw=e^0=1; normalised = (1 - e^-1)/(e - e^-1)."""
        value = BreathStateMachine.compute_breath(0.0)
        expected = (1.0 - 1.0 / math.e) / (math.e - 1.0 / math.e)
        assert value == pytest.approx(expected, abs=1e-12)


# ---------------------------------------------------------------------------
# TIMING constants
# ---------------------------------------------------------------------------


class TestTimingConstants:
    """All expected keys are present in TIMING."""

    EXPECTED_KEYS: ClassVar[set[str]] = {
        "instant",
        "snap",
        "transition",
        "atmosphere_in",
        "atmosphere_out",
        "gather",
        "release",
        "releasing",
        "settle",
        "breath_period",
        "shimmer_speed",
        "event_linger",
        "cycle_announce",
    }

    def test_all_keys_present(self) -> None:
        assert set(TIMING.keys()) == self.EXPECTED_KEYS

    def test_all_values_are_int(self) -> None:
        for key, value in TIMING.items():
            assert isinstance(value, int), f"TIMING[{key!r}] is {type(value)}, not int"
