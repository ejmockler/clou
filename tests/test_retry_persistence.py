"""Tests for retry counter persistence and _active_app accessor pattern.

Covers:
- Intent 14: Retry counters round-trip through checkpoint serialize/parse.
- Intent 13: get_active_app / set_active_app accessor encapsulation.
- Backward compatibility: old-format checkpoints default retry fields to 0.
"""

from __future__ import annotations

import pytest

pytest.importorskip("claude_agent_sdk")

from clou.golden_context import render_checkpoint
from clou.recovery import Checkpoint, parse_checkpoint


# ---------------------------------------------------------------------------
# Intent 14: Retry counter persistence round-trip
# ---------------------------------------------------------------------------


class TestRetryCounterRoundTrip:
    """Retry counters survive render_checkpoint -> parse_checkpoint."""

    def test_all_counters_round_trip(self) -> None:
        """All four retry counters survive serialization and parsing.

        M50 I1: ``EXECUTE_REWORK`` replaces the punctuated legacy
        ``EXECUTE (rework)`` token in the round-trip vocabulary.
        """
        content = render_checkpoint(
            cycle=3,
            step="ASSESS",
            next_step="EXECUTE_REWORK",
            current_phase="implementation",
            phases_completed=1,
            phases_total=3,
            validation_retries=2,
            readiness_retries=1,
            crash_retries=3,
            staleness_count=2,
        )
        cp = parse_checkpoint(content)

        assert cp.validation_retries == 2
        assert cp.readiness_retries == 1
        assert cp.crash_retries == 3
        assert cp.staleness_count == 2
        # Core fields unaffected.
        assert cp.cycle == 3
        assert cp.step == "ASSESS"
        assert cp.next_step == "EXECUTE_REWORK"
        assert cp.current_phase == "implementation"
        assert cp.phases_completed == 1
        assert cp.phases_total == 3

    def test_zero_counters_round_trip(self) -> None:
        """All-zero retry counters also serialize and parse correctly."""
        content = render_checkpoint(
            cycle=1,
            step="PLAN",
            next_step="EXECUTE",
            validation_retries=0,
            readiness_retries=0,
            crash_retries=0,
            staleness_count=0,
        )
        cp = parse_checkpoint(content)

        assert cp.validation_retries == 0
        assert cp.readiness_retries == 0
        assert cp.crash_retries == 0
        assert cp.staleness_count == 0

    def test_individual_counter_isolation(self) -> None:
        """Each counter is independent -- setting one does not affect others."""
        content = render_checkpoint(
            cycle=5,
            step="EXECUTE",
            next_step="ASSESS",
            validation_retries=3,
            readiness_retries=0,
            crash_retries=0,
            staleness_count=0,
        )
        cp = parse_checkpoint(content)

        assert cp.validation_retries == 3
        assert cp.readiness_retries == 0
        assert cp.crash_retries == 0
        assert cp.staleness_count == 0

    def test_default_counters_omitted(self) -> None:
        """render_checkpoint with default counters still includes them."""
        content = render_checkpoint(
            cycle=1,
            step="PLAN",
            next_step="EXECUTE",
        )
        # All four counter fields should be explicitly present.
        assert "validation_retries: 0" in content
        assert "readiness_retries: 0" in content
        assert "crash_retries: 0" in content
        assert "staleness_count: 0" in content


class TestOldFormatBackwardCompat:
    """Old-format checkpoints (without retry fields) default to 0."""

    def test_old_checkpoint_defaults_to_zero(self) -> None:
        """A checkpoint without retry fields parses with all counters at 0."""
        old_content = (
            "cycle: 5\n"
            "step: ASSESS\n"
            "next_step: VERIFY\n"
            "current_phase: testing\n"
            "phases_completed: 2\n"
            "phases_total: 3\n"
        )
        cp = parse_checkpoint(old_content)

        assert cp.validation_retries == 0
        assert cp.readiness_retries == 0
        assert cp.crash_retries == 0
        assert cp.staleness_count == 0
        # Core fields still correct.
        assert cp.cycle == 5
        assert cp.step == "ASSESS"
        assert cp.next_step == "VERIFY"

    def test_empty_checkpoint_defaults_all(self) -> None:
        """Empty checkpoint content defaults everything to 0."""
        cp = parse_checkpoint("")
        assert cp.validation_retries == 0
        assert cp.readiness_retries == 0
        assert cp.crash_retries == 0
        assert cp.staleness_count == 0

    def test_partial_retry_fields(self) -> None:
        """Checkpoint with only some retry fields defaults the rest."""
        content = (
            "cycle: 2\n"
            "step: PLAN\n"
            "next_step: EXECUTE\n"
            "validation_retries: 1\n"
            "crash_retries: 2\n"
        )
        cp = parse_checkpoint(content)

        assert cp.validation_retries == 1
        assert cp.readiness_retries == 0  # missing -> default
        assert cp.crash_retries == 2
        assert cp.staleness_count == 0  # missing -> default


class TestCheckpointDataclassRetryFields:
    """Checkpoint dataclass accepts and stores retry counter fields."""

    def test_dataclass_defaults(self) -> None:
        """Checkpoint() with no args has all retry counters at 0."""
        cp = Checkpoint()
        assert cp.validation_retries == 0
        assert cp.readiness_retries == 0
        assert cp.crash_retries == 0
        assert cp.staleness_count == 0

    def test_dataclass_explicit_values(self) -> None:
        """Checkpoint can be constructed with explicit retry values."""
        cp = Checkpoint(
            cycle=1,
            validation_retries=2,
            readiness_retries=3,
            crash_retries=1,
            staleness_count=4,
        )
        assert cp.validation_retries == 2
        assert cp.readiness_retries == 3
        assert cp.crash_retries == 1
        assert cp.staleness_count == 4

    def test_dataclass_frozen(self) -> None:
        """Retry counter fields are frozen (immutable)."""
        cp = Checkpoint(validation_retries=1)
        with pytest.raises(AttributeError):
            cp.validation_retries = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Intent 13: Accessor pattern
# ---------------------------------------------------------------------------


class TestActiveAppAccessor:
    """get_active_app / set_active_app encapsulate module-level state."""

    def test_initial_state_is_none(self) -> None:
        """get_active_app returns None before any set call."""
        from clou.coordinator import get_active_app, set_active_app

        # Save and restore to avoid test pollution.
        original = get_active_app()
        try:
            set_active_app(None)
            assert get_active_app() is None
        finally:
            set_active_app(original)

    def test_set_and_get(self) -> None:
        """set_active_app stores a value retrievable by get_active_app."""
        from clou.coordinator import get_active_app, set_active_app

        original = get_active_app()
        sentinel = object()
        try:
            set_active_app(sentinel)  # type: ignore[arg-type]
            assert get_active_app() is sentinel
        finally:
            set_active_app(original)

    def test_clear_to_none(self) -> None:
        """set_active_app(None) clears the stored reference."""
        from clou.coordinator import get_active_app, set_active_app

        original = get_active_app()
        sentinel = object()
        try:
            set_active_app(sentinel)  # type: ignore[arg-type]
            assert get_active_app() is sentinel
            set_active_app(None)
            assert get_active_app() is None
        finally:
            set_active_app(original)

    def test_no_module_level_global_keyword(self) -> None:
        """Production code no longer uses 'global _active_app'."""
        import inspect

        import clou.coordinator as coord

        source = inspect.getsource(coord.run_coordinator)
        assert "global _active_app" not in source

    def test_accessor_not_bare_attribute(self) -> None:
        """_active_app is not a simple module-level variable anymore."""
        import clou.coordinator as coord

        # The module should not have _active_app as a direct attribute
        # in its __dict__ (it's encapsulated in _state).
        assert "_active_app" not in coord.__dict__
