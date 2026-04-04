"""Token usage tracking for the Clou orchestrator.

In-memory accumulator for input/output token counts.  The orchestrator
snapshots TokenTracker state around each coordinator cycle and writes
the deltas into the telemetry span log (see ``clou.telemetry``).
``write_milestone_summary`` in telemetry.py aggregates those deltas
into the golden context ``metrics.md``.

Supervisor context awareness uses anchor-plus-delta estimation: the last
API response's ``input_tokens`` serves as anchor, with rough estimates
for messages added since.  Graduated thresholds (warn → compact → block)
drive proactive context management instead of reacting at 75% exhaustion.

Public API:
    TokenTracker — tracks input/output token counts per tier and milestone.
    tracker / cumulative_cost_usd — module-level singletons shared by
        supervisor and coordinator.
    track() / context_exhausted() — convenience functions wrapping the
        singleton tracker.
    ContextPressure — graduated threshold state for the supervisor session.
"""

from __future__ import annotations

from enum import Enum

# Model and context window configuration — single source of truth.
MODEL = "opus"
CONTEXT_WINDOW = 200_000  # Opus context window in tokens

# ---------------------------------------------------------------------------
# Graduated context thresholds (derived from Claude Code's proven math)
# ---------------------------------------------------------------------------
# Reserve for model output during normal operation.
_OUTPUT_BUDGET = 20_000
# Effective context available for input (prompt + messages).
EFFECTIVE_WINDOW = CONTEXT_WINDOW - _OUTPUT_BUDGET  # 180k

# Buffers subtracted from EFFECTIVE_WINDOW to produce thresholds.
WARN_BUFFER = 20_000      # Show ambient indicator
COMPACT_BUFFER = 13_000   # Auto-trigger compaction
BLOCK_BUFFER = 3_000      # Hard stop — force checkpoint

WARN_THRESHOLD = EFFECTIVE_WINDOW - WARN_BUFFER       # 140k
COMPACT_THRESHOLD = EFFECTIVE_WINDOW - COMPACT_BUFFER  # 167k
BLOCK_THRESHOLD = EFFECTIVE_WINDOW - BLOCK_BUFFER      # 177k


class ContextPressure(Enum):
    """Graduated context pressure levels for the supervisor session."""
    NONE = "none"
    WARN = "warn"
    COMPACT = "compact"
    BLOCK = "block"


# Rough token estimation: ~4 chars per token, padded by 4/3 for safety.
def _estimate_tokens(text: str) -> int:
    return int(len(text) / 4 * 4 / 3)


class TokenTracker:
    """Tracks token usage per session tier and per milestone.

    Stores input/output token counts (DB-06: track in tokens, not USD).
    Provides anchor-plus-delta context estimation for the supervisor.
    """

    def __init__(self) -> None:
        self._total: dict[str, int] = {"input": 0, "output": 0}
        self._supervisor: dict[str, int] = {"input": 0, "output": 0}
        self._coordinators: dict[str, dict[str, int]] = {}

        # Anchor-plus-delta state for supervisor context estimation.
        # Anchor: last known input_tokens from a supervisor ResultMessage.
        # Delta: rough estimate of tokens added since the anchor.
        self._sv_anchor: int = 0
        self._sv_delta: int = 0

        # Per-cycle peak tracking for coordinator cycles.
        # Records the highest input_tokens seen in any single API response
        # within the current cycle. Reset between cycles via reset_cycle_peak().
        self._cycle_peak_input: int = 0

    # -- Mutation ----------------------------------------------------------

    def track(
        self,
        usage: dict[str, int],
        tier: str = "supervisor",
        milestone: str | None = None,
    ) -> None:
        """Record token usage from a single SDK message.

        Args:
            usage: The ``usage`` dict from an SDK ``ResultMessage``
                   (must contain ``input_tokens`` and ``output_tokens``).
            tier: ``"supervisor"`` or ``"coordinator"``.
            milestone: Required when *tier* is ``"coordinator"``.
        """
        input_t: int = usage.get("input_tokens", 0)
        output_t: int = usage.get("output_tokens", 0)

        self._total["input"] += input_t
        self._total["output"] += output_t

        if tier == "supervisor":
            self._supervisor["input"] += input_t
            self._supervisor["output"] += output_t
            # Reset anchor to the API's authoritative count; clear delta.
            if input_t > 0:
                self._sv_anchor = input_t
                self._sv_delta = 0
        elif milestone is not None:
            bucket = self._coordinators.setdefault(milestone, {"input": 0, "output": 0})
            bucket["input"] += input_t
            bucket["output"] += output_t
            # Track peak input_tokens within this cycle.
            if input_t > self._cycle_peak_input:
                self._cycle_peak_input = input_t

    def reset_cycle_peak(self) -> None:
        """Reset per-cycle peak tracker. Call at cycle start."""
        self._cycle_peak_input = 0

    @property
    def cycle_peak_input(self) -> int:
        """Highest input_tokens seen in any coordinator API response this cycle."""
        return self._cycle_peak_input

    def add_supervisor_delta(self, text: str) -> None:
        """Accumulate estimated tokens for content added since last anchor.

        Call this when queuing user input or system messages to the
        supervisor session between API round-trips.
        """
        self._sv_delta += _estimate_tokens(text)

    # -- Queries -----------------------------------------------------------

    def supervisor_context_estimate(self) -> int:
        """Estimated supervisor context usage (anchor + delta).

        The anchor is the last ``input_tokens`` from a supervisor
        ``ResultMessage``.  The delta is the rough estimate for messages
        queued since that response (user input, compact prompts, etc.).
        """
        return self._sv_anchor + self._sv_delta

    def supervisor_pressure(self) -> ContextPressure:
        """Graduated context pressure level for the supervisor session."""
        estimate = self.supervisor_context_estimate()
        if estimate >= BLOCK_THRESHOLD:
            return ContextPressure.BLOCK
        if estimate >= COMPACT_THRESHOLD:
            return ContextPressure.COMPACT
        if estimate >= WARN_THRESHOLD:
            return ContextPressure.WARN
        return ContextPressure.NONE

    def is_context_exhausted(
        self,
        usage: dict[str, int],
        threshold: float = 0.75,
    ) -> bool:
        """Check if a single cycle is approaching the context limit.

        This is an edge-case handler, not the primary compaction mechanism.
        The primary mechanism is session-per-cycle — each cycle starts fresh.
        Mid-cycle exhaustion signals that a phase is too large and should be
        decomposed, but we handle it gracefully by checkpointing and
        restarting.

        Args:
            usage: The ``usage`` dict from the latest SDK message.
            threshold: Fraction of the context window.

        Returns:
            ``True`` when ``input_tokens`` exceeds ``CONTEXT_WINDOW * threshold``.
        """
        input_tokens: int = usage.get("input_tokens", 0)
        return input_tokens > CONTEXT_WINDOW * threshold

    @property
    def total(self) -> dict[str, int]:
        """Cumulative input/output token counts across all tiers."""
        return dict(self._total)

    @property
    def supervisor(self) -> dict[str, int]:
        """Input/output token counts for the supervisor tier."""
        return dict(self._supervisor)

    def coordinator(self, milestone: str) -> dict[str, int]:
        """Input/output token counts for a single coordinator milestone."""
        bucket = self._coordinators.get(milestone)
        return dict(bucket) if bucket else {"input": 0, "output": 0}


# ---------------------------------------------------------------------------
# Module-level singleton — shared by supervisor and coordinator
# ---------------------------------------------------------------------------

tracker = TokenTracker()
cumulative_cost_usd: dict[str, float] = {}  # milestone → cumulative USD


def track(msg: object, tier: str = "supervisor", milestone: str | None = None) -> None:
    """Extract and track token usage and cost from an SDK message."""
    # Lazy import to avoid circular dependency at module load time.
    from claude_agent_sdk import ResultMessage

    if isinstance(msg, ResultMessage) and msg.usage:
        tracker.track(msg.usage, tier=tier, milestone=milestone)
    if isinstance(msg, ResultMessage) and milestone:
        cost = getattr(msg, "total_cost_usd", None)
        if cost is not None:
            cumulative_cost_usd[milestone] = (
                cumulative_cost_usd.get(milestone, 0.0) + cost
            )


def context_exhausted(msg: object) -> bool:
    """Check if a message indicates context exhaustion."""
    from claude_agent_sdk import ResultMessage

    if isinstance(msg, ResultMessage) and msg.usage:
        return tracker.is_context_exhausted(msg.usage)
    return False


def supervisor_pressure() -> ContextPressure:
    """Current context pressure level for the supervisor session."""
    return tracker.supervisor_pressure()
