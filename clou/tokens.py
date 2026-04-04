"""Token usage tracking for the Clou orchestrator.

In-memory accumulator for input/output token counts.  The orchestrator
snapshots TokenTracker state around each coordinator cycle and writes
the deltas into the telemetry span log (see ``clou.telemetry``).
``write_milestone_summary`` in telemetry.py aggregates those deltas
into the golden context ``metrics.md``.

Public API:
    TokenTracker — tracks input/output token counts per tier and milestone.
    tracker / cumulative_cost_usd — module-level singletons shared by
        supervisor and coordinator.
    track() / context_exhausted() — convenience functions wrapping the
        singleton tracker.
"""

from __future__ import annotations

# Model and context window configuration — single source of truth.
MODEL = "opus"
CONTEXT_WINDOW = 200_000  # Opus context window in tokens


class TokenTracker:
    """Tracks token usage per session tier and per milestone.

    Stores input/output token counts (DB-06: track in tokens, not USD).
    """

    def __init__(self) -> None:
        self._total: dict[str, int] = {"input": 0, "output": 0}
        self._supervisor: dict[str, int] = {"input": 0, "output": 0}
        self._coordinators: dict[str, dict[str, int]] = {}

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
        elif milestone is not None:
            bucket = self._coordinators.setdefault(milestone, {"input": 0, "output": 0})
            bucket["input"] += input_t
            bucket["output"] += output_t

    # -- Queries -----------------------------------------------------------

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
