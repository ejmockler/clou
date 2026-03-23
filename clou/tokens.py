"""Token usage tracking for the Clou orchestrator.

Public API:
    TokenTracker — tracks input/output token counts per tier and milestone.
"""

from __future__ import annotations


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
            threshold: Fraction of the 200K-token Opus context window.

        Returns:
            ``True`` when ``input_tokens`` exceeds ``200_000 * threshold``.
        """
        input_tokens: int = usage.get("input_tokens", 0)
        return input_tokens > 200_000 * threshold

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
