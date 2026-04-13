"""Tests for clou.tokens — token usage tracking."""

from __future__ import annotations

from clou.tokens import (
    BLOCK_THRESHOLD,
    COMPACT_THRESHOLD,
    WARN_THRESHOLD,
    ContextPressure,
    TokenTracker,
    _estimate_tokens,
)


class TestTrack:
    """TokenTracker.track accumulates counts correctly."""

    def test_supervisor_defaults(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 100, "output_tokens": 50})

        assert t.total == {"input": 100, "output": 50}
        assert t.supervisor == {"input": 100, "output": 50}

    def test_coordinator_with_milestone(self) -> None:
        t = TokenTracker()
        t.track(
            {"input_tokens": 200, "output_tokens": 80},
            tier="coordinator",
            milestone="plan",
        )

        assert t.total == {"input": 200, "output": 80}
        # Supervisor should be untouched.
        assert t.supervisor == {"input": 0, "output": 0}
        assert t.coordinator("plan") == {"input": 200, "output": 80}

    def test_coordinator_without_milestone_is_ignored(self) -> None:
        """Coordinator usage without a milestone is added to total only."""
        t = TokenTracker()
        t.track(
            {"input_tokens": 10, "output_tokens": 5},
            tier="coordinator",
        )

        assert t.total == {"input": 10, "output": 5}
        assert t.supervisor == {"input": 0, "output": 0}

    def test_multiple_tracks_accumulate(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 100, "output_tokens": 40})
        t.track({"input_tokens": 200, "output_tokens": 60})

        assert t.total == {"input": 300, "output": 100}
        assert t.supervisor == {"input": 300, "output": 100}

    def test_multiple_milestones(self) -> None:
        t = TokenTracker()
        t.track(
            {"input_tokens": 50, "output_tokens": 10},
            tier="coordinator",
            milestone="plan",
        )
        t.track(
            {"input_tokens": 70, "output_tokens": 20},
            tier="coordinator",
            milestone="implement",
        )

        assert t.total == {"input": 120, "output": 30}
        assert t.coordinator("plan") == {"input": 50, "output": 10}
        assert t.coordinator("implement") == {"input": 70, "output": 20}

    def test_same_milestone_accumulates(self) -> None:
        t = TokenTracker()
        t.track(
            {"input_tokens": 30, "output_tokens": 10},
            tier="coordinator",
            milestone="plan",
        )
        t.track(
            {"input_tokens": 20, "output_tokens": 5},
            tier="coordinator",
            milestone="plan",
        )

        assert t.coordinator("plan") == {"input": 50, "output": 15}

    def test_missing_keys_default_to_zero(self) -> None:
        t = TokenTracker()
        t.track({})

        assert t.total == {"input": 0, "output": 0}
        assert t.supervisor == {"input": 0, "output": 0}

    def test_extra_keys_are_ignored(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 10, "output_tokens": 5, "cache_tokens": 3})

        assert t.total == {"input": 10, "output": 5}

    def test_unknown_milestone_returns_zeros(self) -> None:
        t = TokenTracker()
        assert t.coordinator("nonexistent") == {"input": 0, "output": 0}


class TestContextExhausted:
    """TokenTracker.is_context_exhausted checks against 1M window."""

    def test_below_threshold(self) -> None:
        t = TokenTracker()
        assert t.is_context_exhausted({"input_tokens": 500_000}) is False

    def test_above_threshold(self) -> None:
        t = TokenTracker()
        assert t.is_context_exhausted({"input_tokens": 800_000}) is True

    def test_exact_threshold_is_not_exhausted(self) -> None:
        t = TokenTracker()
        # 1_000_000 * 0.75 == 750_000; equal is NOT greater-than.
        assert t.is_context_exhausted({"input_tokens": 750_000}) is False

    def test_custom_threshold(self) -> None:
        t = TokenTracker()
        # 1_000_000 * 0.5 == 500_000
        assert t.is_context_exhausted({"input_tokens": 500_001}, threshold=0.5) is True
        assert t.is_context_exhausted({"input_tokens": 500_000}, threshold=0.5) is False

    def test_missing_input_tokens(self) -> None:
        t = TokenTracker()
        assert t.is_context_exhausted({}) is False


class TestSupervisorAnchorDelta:
    """Anchor-plus-delta estimation for supervisor context pressure."""

    def test_initial_estimate_is_zero(self) -> None:
        t = TokenTracker()
        assert t.supervisor_context_estimate() == 0

    def test_anchor_set_on_supervisor_track(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 2000})
        assert t.supervisor_context_estimate() == 50_000

    def test_anchor_replaces_previous(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 2000})
        t.track({"input_tokens": 80_000, "output_tokens": 3000})
        assert t.supervisor_context_estimate() == 80_000

    def test_delta_accumulates(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 2000})
        t.add_supervisor_delta("x" * 400)  # ~133 tokens
        assert t.supervisor_context_estimate() > 50_000

    def test_anchor_resets_delta(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 2000})
        t.add_supervisor_delta("x" * 40_000)  # large delta
        big_estimate = t.supervisor_context_estimate()
        assert big_estimate > 50_000

        # New anchor clears the delta.
        t.track({"input_tokens": 60_000, "output_tokens": 2500})
        assert t.supervisor_context_estimate() == 60_000

    def test_coordinator_track_does_not_affect_anchor(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 2000})
        t.track(
            {"input_tokens": 100_000, "output_tokens": 5000},
            tier="coordinator",
            milestone="test",
        )
        assert t.supervisor_context_estimate() == 50_000

    def test_zero_input_tokens_does_not_reset_anchor(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 2000})
        t.add_supervisor_delta("x" * 400)
        # A message with zero input_tokens shouldn't clobber the anchor.
        t.track({"input_tokens": 0, "output_tokens": 100})
        assert t.supervisor_context_estimate() > 50_000


class TestContextPressure:
    """Graduated threshold levels for supervisor context."""

    def test_none_below_warn(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": WARN_THRESHOLD - 1, "output_tokens": 100})
        assert t.supervisor_pressure() == ContextPressure.NONE

    def test_warn_at_threshold(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": WARN_THRESHOLD, "output_tokens": 100})
        assert t.supervisor_pressure() == ContextPressure.WARN

    def test_warn_between_thresholds(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": WARN_THRESHOLD + 1000, "output_tokens": 100})
        assert t.supervisor_pressure() == ContextPressure.WARN

    def test_compact_at_threshold(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": COMPACT_THRESHOLD, "output_tokens": 100})
        assert t.supervisor_pressure() == ContextPressure.COMPACT

    def test_block_at_threshold(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": BLOCK_THRESHOLD, "output_tokens": 100})
        assert t.supervisor_pressure() == ContextPressure.BLOCK

    def test_delta_can_push_into_warn(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": WARN_THRESHOLD - 500, "output_tokens": 100})
        assert t.supervisor_pressure() == ContextPressure.NONE
        # Add enough delta to cross the threshold.
        t.add_supervisor_delta("x" * 4000)  # ~1333 tokens > 500 gap
        assert t.supervisor_pressure() == ContextPressure.WARN

    def test_threshold_ordering(self) -> None:
        assert WARN_THRESHOLD < COMPACT_THRESHOLD < BLOCK_THRESHOLD


class TestEstimateTokens:
    """Rough token estimation function."""

    def test_empty_string(self) -> None:
        assert _estimate_tokens("") == 0

    def test_short_string(self) -> None:
        result = _estimate_tokens("hello world")
        assert result > 0
        # ~11 chars / 4 * 4/3 ≈ 4 tokens
        assert result < 10

    def test_proportional_to_length(self) -> None:
        short = _estimate_tokens("x" * 100)
        long = _estimate_tokens("x" * 1000)
        assert long > short


class TestCyclePeakInput:
    """Per-cycle peak input_tokens tracking."""

    def test_initial_peak_is_zero(self) -> None:
        t = TokenTracker()
        assert t.cycle_peak_input == 0

    def test_peak_tracks_coordinator_input(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 50_000, "output_tokens": 1000},
                tier="coordinator", milestone="m1")
        t.track({"input_tokens": 80_000, "output_tokens": 2000},
                tier="coordinator", milestone="m1")
        t.track({"input_tokens": 60_000, "output_tokens": 1500},
                tier="coordinator", milestone="m1")
        assert t.cycle_peak_input == 80_000

    def test_reset_clears_peak(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 80_000, "output_tokens": 2000},
                tier="coordinator", milestone="m1")
        assert t.cycle_peak_input == 80_000
        t.reset_cycle_peak()
        assert t.cycle_peak_input == 0

    def test_supervisor_track_does_not_affect_peak(self) -> None:
        t = TokenTracker()
        t.track({"input_tokens": 100_000, "output_tokens": 5000})
        assert t.cycle_peak_input == 0
