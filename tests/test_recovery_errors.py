"""Tests for error classification in clou.recovery_errors.

Covers the three-kind taxonomy (HOLD / TRANSIENT / TERMINAL), precedence
rules, and the HOLD-specific invariants:

- HOLD is never converted to TERMINAL by retry count.
- HOLD patterns (rate-limit, 429, throttl, quota) classify before
  TRANSIENT, so rate-limit never gets the 30s-cooldown-then-escalate
  treatment.
- TERMINAL still wins over HOLD when both match (a rate-limit string
  inside a traceback with ImportError is still a code bug).
"""

from __future__ import annotations

import pytest

from clou.recovery_errors import (
    DEFAULT_HOLD_COOLDOWN,
    DEFAULT_MAX_RETRIES,
    ErrorKind,
    classify_error,
    compute_hold_wait,
)


class TestClassificationHold:
    """HOLD classification for rate-limit / quota / throttle errors."""

    @pytest.mark.parametrize(
        "message",
        [
            "rate limit exceeded",
            "rate_limit hit",
            "Rate-Limited by upstream",
            "HTTP 429 Too Many Requests",
            "error: 429",
            "quota exceeded for this project",
            "throttled: please retry later",
        ],
    )
    def test_rate_limit_patterns_classify_as_hold(self, message: str) -> None:
        kind, reason = classify_error(message, "task-x")
        assert kind == ErrorKind.HOLD, f"{message!r} -> {kind!r}: {reason}"

    def test_hold_never_becomes_terminal_on_max_retries(self) -> None:
        """Retry count is irrelevant for HOLD.

        Rate-limit escalating after N retries would violate the zero-
        escalations principle: the system would surface scheduled
        capacity as a user-facing failure.
        """
        kind, _ = classify_error(
            "429 rate limit",
            "task-x",
            retry_count=99,
            max_retries=DEFAULT_MAX_RETRIES,
        )
        assert kind == ErrorKind.HOLD

    def test_terminal_wins_over_hold(self) -> None:
        """A structural failure that mentions rate-limit in its trace
        stays terminal.  Don't HOLD on a code bug.
        """
        kind, reason = classify_error(
            "ImportError: module rate_limit not found",
            "task-x",
        )
        assert kind == ErrorKind.TERMINAL
        assert "terminal pattern" in reason


class TestClassificationTransient:
    """TRANSIENT classification still works and rate-limit is no longer
    part of it.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "timeout waiting for response",
            "connection reset by peer",
            "network unreachable",
            "503 service unavailable",
            "ECONNRESET",
            "sleep interrupted",
            "retry later",
        ],
    )
    def test_transient_patterns_classify_as_transient(self, message: str) -> None:
        kind, _ = classify_error(message, "task-x")
        assert kind == ErrorKind.TRANSIENT

    def test_transient_escalates_after_max_retries(self) -> None:
        kind, reason = classify_error(
            "connection timeout",
            "task-x",
            retry_count=DEFAULT_MAX_RETRIES,
            max_retries=DEFAULT_MAX_RETRIES,
        )
        assert kind == ErrorKind.TERMINAL
        assert "retry count" in reason

    def test_rate_limit_not_in_transient_patterns(self) -> None:
        """Regression guard: rate-limit messages must never classify as
        TRANSIENT, because that path applies 30s cooldowns and escalates
        after two retries.  Rate-limit is HOLD.
        """
        for message in (
            "rate limit",
            "429",
            "quota exceeded",
            "throttled",
        ):
            kind, _ = classify_error(message, "task-x")
            assert kind == ErrorKind.HOLD, (
                f"{message!r} must be HOLD, not {kind.value}"
            )


class TestClassificationTerminal:
    """TERMINAL classification covers structural bugs."""

    @pytest.mark.parametrize(
        "message",
        [
            "SyntaxError: invalid syntax",
            "ImportError: no module named foo",
            "FileNotFoundError: [Errno 2] No such file",
            "TypeError: unsupported operand",
            "schema validation failed",
        ],
    )
    def test_terminal_patterns(self, message: str) -> None:
        kind, _ = classify_error(message, "task-x")
        assert kind == ErrorKind.TERMINAL

    def test_unknown_defaults_to_terminal(self) -> None:
        kind, reason = classify_error("something weird happened", "task-x")
        assert kind == ErrorKind.TERMINAL
        assert "defaulting to terminal" in reason


class TestComputeHoldWait:
    """compute_hold_wait: derive sleep-until-reset from a unix timestamp."""

    def test_future_resets_at_returns_delta(self) -> None:
        now = 1_700_000_000.0
        resets_at = now + 42.0
        assert compute_hold_wait(resets_at=resets_at, now=now) == pytest.approx(42.0)

    def test_past_resets_at_returns_floor(self) -> None:
        """A reset time in the past means the reset already happened;
        wait briefly, don't negative-sleep or hang."""
        now = 1_700_000_000.0
        resets_at = now - 5.0
        assert compute_hold_wait(resets_at=resets_at, now=now) == 1.0

    def test_missing_resets_at_returns_default(self) -> None:
        assert (
            compute_hold_wait(resets_at=None, now=1_700_000_000.0)
            == DEFAULT_HOLD_COOLDOWN
        )

    def test_malformed_resets_at_returns_default(self) -> None:
        assert (
            compute_hold_wait(resets_at="not-a-number", now=1_700_000_000.0)  # type: ignore[arg-type]
            == DEFAULT_HOLD_COOLDOWN
        )

    def test_clamped_to_ceiling(self) -> None:
        """Far-future reset times are almost certainly clock skew or
        stale headers; cap at one hour.
        """
        now = 1_700_000_000.0
        resets_at = now + 99_999.0
        assert compute_hold_wait(resets_at=resets_at, now=now) == 3600.0

    def test_custom_ceiling(self) -> None:
        now = 0.0
        assert compute_hold_wait(resets_at=500.0, now=now, ceiling=100.0) == 100.0


class TestErrorKindEnum:
    """Enum shape + string values.  Stable surface for telemetry."""

    def test_enum_values(self) -> None:
        assert ErrorKind.HOLD.value == "hold"
        assert ErrorKind.TRANSIENT.value == "transient"
        assert ErrorKind.TERMINAL.value == "terminal"

    def test_enum_is_str_subclass(self) -> None:
        """StrEnum behavior -- telemetry can serialize directly."""
        assert ErrorKind.HOLD == "hold"


class TestHoldCoalescing:
    """The coalescing decision the coordinator makes.

    Mirrors the logic in clou/coordinator.py: if _hold_active_until is
    in the future, a new HOLD failure coalesces (no new sleep); if it's
    None or in the past, a fresh hold starts.  The coordinator's inner
    message loop tracks this variable across iterations so simultaneous
    rate-limit failures from N siblings produce one sleep, not N.
    """

    @staticmethod
    def should_coalesce(hold_active_until: float | None, now: float) -> bool:
        """The coalescing predicate (pure; mirrors coordinator logic)."""
        return hold_active_until is not None and now < hold_active_until

    def test_no_active_hold_starts_new(self) -> None:
        assert self.should_coalesce(None, now=1_700_000_000.0) is False

    def test_active_hold_coalesces(self) -> None:
        now = 1_700_000_000.0
        active_until = now + 30.0
        assert self.should_coalesce(active_until, now) is True

    def test_expired_hold_starts_new(self) -> None:
        """If the previous hold already ended, a new failure starts
        a fresh hold; it does not coalesce against the stale window.
        """
        now = 1_700_000_000.0
        active_until = now - 5.0
        assert self.should_coalesce(active_until, now) is False

    def test_exact_boundary_is_fresh_hold(self) -> None:
        """now == active_until is the reset moment: start fresh."""
        now = 1_700_000_000.0
        assert self.should_coalesce(now, now) is False
