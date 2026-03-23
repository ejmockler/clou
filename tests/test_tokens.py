"""Tests for clou.tokens — token usage tracking."""

from __future__ import annotations

from clou.tokens import TokenTracker


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
    """TokenTracker.is_context_exhausted checks against 200K window."""

    def test_below_threshold(self) -> None:
        t = TokenTracker()
        assert t.is_context_exhausted({"input_tokens": 100_000}) is False

    def test_above_threshold(self) -> None:
        t = TokenTracker()
        assert t.is_context_exhausted({"input_tokens": 160_000}) is True

    def test_exact_threshold_is_not_exhausted(self) -> None:
        t = TokenTracker()
        # 200_000 * 0.75 == 150_000; equal is NOT greater-than.
        assert t.is_context_exhausted({"input_tokens": 150_000}) is False

    def test_custom_threshold(self) -> None:
        t = TokenTracker()
        # 200_000 * 0.5 == 100_000
        assert t.is_context_exhausted({"input_tokens": 100_001}, threshold=0.5) is True
        assert t.is_context_exhausted({"input_tokens": 100_000}, threshold=0.5) is False

    def test_missing_input_tokens(self) -> None:
        t = TokenTracker()
        assert t.is_context_exhausted({}) is False
