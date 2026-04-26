"""Regression test for the COMPLETE exclusion in run_coordinator's
session-start ORIENT interposition.

Before this fix, a coordinator dispatched on a milestone whose
checkpoint already said ``next_step=COMPLETE`` would have its first
iteration rewrite the checkpoint to ``next_step=ORIENT`` (with
COMPLETE stashed in ``pre_orient_next_step``), waste a full ORIENT
cycle of API tokens, then restore COMPLETE on iteration 2 and
finally exit.

After the fix, ``COMPLETE`` joins ``ORIENT`` and ``HALTED`` in the
exclusion list — a milestone whose checkpoint says it is shipped
exits immediately on next dispatch with no LLM call.

The test mirrors the inline first-iteration condition in
``coordinator.py``.  If a future refactor moves the condition or
adds a new exclusion, this test should be updated alongside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.golden_context import render_checkpoint
from clou.recovery_checkpoint import parse_checkpoint


def _condition_says_skip_orient(next_step: str) -> bool:
    """Mirror the first-iteration interposition condition.

    The interposition runs when ``next_step`` is NOT in this skip
    set.  Keeping the predicate here keeps the test honest: any
    drift in the production condition (in coordinator.py around the
    ``# M52 follow-up`` comment) shows up as a test diff."""
    return next_step in ("ORIENT", "HALTED", "COMPLETE")


class TestOrientInterpositionSkipSet:
    @pytest.mark.parametrize(
        "next_step,should_skip",
        [
            ("PLAN", False),
            ("EXECUTE", False),
            ("ASSESS", False),
            ("VERIFY", False),
            ("EXIT", False),
            ("EXECUTE_REWORK", False),
            ("ORIENT", True),
            ("HALTED", True),
            ("COMPLETE", True),
        ],
    )
    def test_predicate(self, next_step: str, should_skip: bool) -> None:
        assert _condition_says_skip_orient(next_step) is should_skip

    def test_complete_checkpoint_round_trip_safe(
        self, tmp_path: Path,
    ) -> None:
        """A COMPLETE checkpoint round-trips through render → parse
        without losing the next_step.  This is the data-layer side
        of the fix: the renderer accepts COMPLETE, the parser
        recognises it, and any downstream consumer reading the
        checkpoint sees ``next_step=COMPLETE`` rather than coercing
        it to PLAN or ORIENT."""
        cp_path = tmp_path / "coordinator.md"
        cp_path.write_text(
            render_checkpoint(
                cycle=1,
                step="EXIT",
                next_step="COMPLETE",
                current_phase="",
                phases_completed=0,
                phases_total=0,
            ),
            encoding="utf-8",
        )
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "COMPLETE"


class TestProductionCondition:
    """Inspect the production source so the predicate above does
    not silently drift away from coordinator.py's actual logic.

    A simple substring check is enough — if the production code
    changes the exclusion set, the test fails and forces an update
    here.  Strict text match would be too brittle (whitespace,
    comment changes) but a token-level check on the membership
    branch is stable."""

    def test_production_condition_includes_complete(self) -> None:
        coord_path = (
            Path(__file__).resolve().parent.parent
            / "clou" / "coordinator.py"
        )
        source = coord_path.read_text(encoding="utf-8")
        # The first-iteration ORIENT block lives inside run_coordinator;
        # it tests next_step against a 3-element exclusion.  Locate
        # the marker comment we added and verify COMPLETE appears in
        # the conjunction immediately after.
        anchor = "M52 follow-up: also skip COMPLETE"
        assert anchor in source, (
            f"Marker comment {anchor!r} missing from coordinator.py — "
            f"the COMPLETE exclusion may have been removed"
        )
        idx = source.index(anchor)
        # Look at the next 1200 bytes for the membership branch
        # (the comment block precedes the condition; needs enough
        # window to clear both).
        window = source[idx:idx + 1200]
        assert 'next_step != "COMPLETE"' in window, (
            "Production condition near the M52 comment no longer "
            "excludes COMPLETE — see TestOrientInterpositionSkipSet "
            "for the spec the predicate should match"
        )
