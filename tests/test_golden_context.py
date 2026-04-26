"""Tests for clou.golden_context — protocol artifact serializers.

The key invariant: every render_* function produces markdown that passes
the corresponding validator with zero errors.  Format correctness is
guaranteed by construction.
"""

from __future__ import annotations

import pytest

from clou.golden_context import (
    assemble_execution,
    render_checkpoint,
    render_execution_summary,
    render_execution_task,
    render_status,
    sanitize_phase,
)
from clou.validation import (
    Severity,
    validate_checkpoint,
    validate_status_checkpoint,
)


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


class TestRenderCheckpoint:

    def test_minimal(self) -> None:
        content = render_checkpoint(cycle=1, step="PLAN", next_step="EXECUTE")
        assert "cycle: 1\n" in content
        assert "step: PLAN\n" in content
        assert "next_step: EXECUTE\n" in content
        assert "current_phase: \n" in content
        assert "phases_completed: 0\n" in content
        assert "phases_total: 0\n" in content

    def test_full(self) -> None:
        content = render_checkpoint(
            cycle=3, step="ASSESS", next_step="VERIFY",
            current_phase="api", phases_completed=2, phases_total=4,
        )
        assert "cycle: 3\n" in content
        assert "current_phase: api\n" in content
        assert "phases_completed: 2\n" in content
        assert "phases_total: 4\n" in content

    def test_passes_validation(self) -> None:
        content = render_checkpoint(
            cycle=2, step="EXECUTE", next_step="ASSESS",
            current_phase="impl", phases_completed=1, phases_total=3,
        )
        findings = validate_checkpoint(content)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []

    def test_minimal_passes_validation(self) -> None:
        content = render_checkpoint(cycle=0, step="PLAN", next_step="PLAN")
        findings = validate_checkpoint(content)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []

    def test_all_valid_next_steps(self) -> None:
        # M50 I1 cycle-3 rework (F4/F15): structured
        # ``EXECUTE_REWORK`` / ``EXECUTE_VERIFY`` tokens replace the
        # punctuated legacy forms in the ``VALID_NEXT_STEPS``
        # vocabulary, and ``'none'`` is no longer a valid write-time
        # value — it is parse-only legacy tolerance, tested separately
        # in :meth:`test_none_next_step_parse_only_tolerance`.
        for ns in ("PLAN", "EXECUTE", "EXECUTE_REWORK", "EXECUTE_VERIFY",
                    "ASSESS", "VERIFY", "EXIT", "COMPLETE"):
            content = render_checkpoint(cycle=1, step="PLAN", next_step=ns)
            findings = validate_checkpoint(content)
            errors = [f for f in findings if f.severity == Severity.ERROR]
            assert errors == [], f"next_step={ns!r} produced errors: {errors}"

    def test_rejects_negative_cycle(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            render_checkpoint(cycle=-1, step="PLAN", next_step="EXECUTE")

    def test_rejects_invalid_step(self) -> None:
        with pytest.raises(ValueError, match="invalid step"):
            render_checkpoint(cycle=1, step="BOGUS", next_step="EXECUTE")

    def test_rejects_invalid_next_step(self) -> None:
        with pytest.raises(ValueError, match="invalid next_step"):
            render_checkpoint(cycle=1, step="PLAN", next_step="BOGUS")

    def test_rejects_completed_exceeds_total(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            render_checkpoint(
                cycle=1, step="PLAN", next_step="EXECUTE",
                phases_completed=5, phases_total=3,
            )

    def test_zero_warnings_when_fully_specified(self) -> None:
        content = render_checkpoint(
            cycle=1, step="PLAN", next_step="EXECUTE",
            current_phase="impl", phases_completed=0, phases_total=2,
        )
        findings = validate_checkpoint(content)
        assert findings == []


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestRenderStatus:

    def test_minimal(self) -> None:
        # M50 I1 cycle-4 rework (F20): render_status requires an
        # explicit valid next_step.  The former empty-default
        # silent-tolerance pattern was removed; callers must name
        # "no dispatch scheduled" via an actual vocabulary token.
        content = render_status(
            milestone="auth", phase="impl", cycle=1, next_step="PLAN",
        )
        assert "# Status: auth" in content
        assert "## Current State" in content
        assert "phase: impl" in content
        assert "cycle: 1" in content
        assert "## Phase Progress" in content

    def test_with_phase_progress(self) -> None:
        content = render_status(
            milestone="auth", phase="api", cycle=2,
            next_step="ASSESS",
            phase_progress={"impl": "completed", "api": "in_progress"},
        )
        assert "| impl | completed |" in content
        assert "| api | in_progress |" in content

    def test_passes_validation(self) -> None:
        content = render_status(
            milestone="auth", phase="impl", cycle=1,
            next_step="EXECUTE",
            phase_progress={"impl": "in_progress", "api": "pending"},
        )
        findings = validate_status_checkpoint(content)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []

    def test_minimal_passes_validation(self) -> None:
        # M50 I1 cycle-4 rework (F20): explicit next_step required.
        content = render_status(
            milestone="auth", phase="impl", cycle=1, next_step="PLAN",
        )
        findings = validate_status_checkpoint(content)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []

    def test_with_notes(self) -> None:
        # M50 I1 cycle-4 rework (F20): explicit next_step required.
        content = render_status(
            milestone="auth", phase="impl", cycle=1, next_step="PLAN",
            notes="Rework needed for F3.",
        )
        assert "## Notes" in content
        assert "Rework needed" in content


    def test_render_status_rejects_empty_next_step(self) -> None:
        """M50 I1 cycle-4 rework (F20): empty next_step is NOT a silent sentinel.

        The prior ``if next_step and next_step not in VALID_NEXT_STEPS``
        short-circuit let ``render_status(next_step="")`` slip through
        validation — preserving the same silent-tolerance pattern
        that cycle-3's ``"none"`` narrowing was trying to eliminate.
        The "No silent coerce" requirement extends to render-path,
        not just parse-path.

        Now: empty-string fails the same as any other invalid value.
        Callers must supply an explicit vocabulary token; "no
        dispatch scheduled" is named, not implicit.
        """
        with pytest.raises(ValueError, match="invalid next_step"):
            render_status(
                milestone="auth", phase="impl", cycle=1, next_step="",
            )


    def test_render_status_rejects_default_next_step(self) -> None:
        """M50 I1 cycle-4 rework (F20): the signature default is an error.

        The function signature still has ``next_step: str = ""`` for
        backward-compatible keyword invocation.  The default itself
        is no longer a silent pass-through — callers that omit the
        keyword trigger the same ValueError as
        ``next_step="FOOBAR"`` would.  Pins the contract: empty
        string is NOT allowed via default OR explicit argument.
        """
        with pytest.raises(ValueError, match="invalid next_step"):
            render_status(milestone="auth", phase="impl", cycle=1)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestRenderExecutionSummary:

    def test_defaults(self) -> None:
        content = render_execution_summary()
        assert "## Summary" in content
        assert "status: in_progress" in content
        assert "failures: none" in content

    def test_completed(self) -> None:
        content = render_execution_summary(
            status="completed", tasks_total=3, tasks_completed=3,
        )
        assert "status: completed" in content
        assert "3 total, 3 completed" in content


class TestRenderExecutionTask:

    def test_minimal(self) -> None:
        content = render_execution_task(task_id=1, name="Build auth")
        assert "### T1: Build auth" in content
        assert "**Status:** pending" in content

    def test_full(self) -> None:
        content = render_execution_task(
            task_id=2, name="Write tests", status="completed",
            files_changed=["tests/test_auth.py", "clou/auth.py"],
            tests="12 passing",
            notes="Covers all edge cases.",
        )
        assert "### T2: Write tests" in content
        assert "**Status:** completed" in content
        assert "  - tests/test_auth.py" in content
        assert "**Tests:** 12 passing" in content
        assert "**Notes:** Covers all edge cases." in content


class TestAssembleExecution:

    def test_structure(self) -> None:
        summary = render_execution_summary(
            status="completed", tasks_total=2, tasks_completed=2,
        )
        tasks = [
            render_execution_task(1, "Build shard", status="completed"),
            render_execution_task(2, "Write tests", status="completed"),
        ]
        content = assemble_execution(summary, tasks)

        assert "## Summary" in content
        assert "## Tasks" in content
        assert "### T1:" in content
        assert "### T2:" in content

    def test_passes_execution_validation(self) -> None:
        """Assembled execution.md passes _validate_execution checks."""
        from pathlib import Path
        import tempfile
        from clou.validation import _validate_execution

        summary = render_execution_summary(
            status="completed", tasks_total=1, tasks_completed=1,
        )
        tasks = [
            render_execution_task(1, "Do work", status="completed"),
        ]
        content = assemble_execution(summary, tasks)

        with tempfile.TemporaryDirectory() as tmp:
            # Mimic the .clou path structure so _rel() works.
            p = Path(tmp) / ".clou" / "milestones" / "m1" / "phases" / "impl" / "execution.md"
            p.parent.mkdir(parents=True)
            p.write_text(content)
            findings = _validate_execution(p)
            errors = [f for f in findings if f.severity == Severity.ERROR]
            assert errors == [], f"Assembled execution.md has errors: {errors}"


# ---------------------------------------------------------------------------
# Serializer → Parser round-trip (critical path)
# ---------------------------------------------------------------------------


class TestCheckpointParserRoundTrip:
    """render_checkpoint → parse_checkpoint preserves all values."""

    def test_full_round_trip(self) -> None:
        from clou.recovery import parse_checkpoint

        content = render_checkpoint(
            cycle=3, step="ASSESS", next_step="VERIFY",
            current_phase="api", phases_completed=2, phases_total=4,
        )
        cp = parse_checkpoint(content)
        assert cp.cycle == 3
        assert cp.step == "ASSESS"
        assert cp.next_step == "VERIFY"
        assert cp.current_phase == "api"
        assert cp.phases_completed == 2
        assert cp.phases_total == 4

    def test_minimal_round_trip(self) -> None:
        from clou.recovery import parse_checkpoint

        content = render_checkpoint(cycle=0, step="PLAN", next_step="PLAN")
        cp = parse_checkpoint(content)
        assert cp.cycle == 0
        assert cp.next_step == "PLAN"

    def test_none_next_step_parse_only_tolerance(self) -> None:
        """M50 I1 cycle-3 rework (F4/F15): 'none' is parse-only legacy tolerance.

        ``render_checkpoint(next_step='none')`` now raises ``ValueError``;
        ``parse_checkpoint`` coerces legacy ``next_step: none`` inputs
        (e.g., from pre-M50 on-disk checkpoints) to ``COMPLETE`` so
        trajectories resume, but new writes MUST use ``COMPLETE``
        directly.  This test proves both halves of the one-way
        tolerance: (a) render rejects, (b) parse coerces.
        """
        from clou.recovery import parse_checkpoint

        # Render rejects 'none' at write time.
        with pytest.raises(ValueError, match="invalid next_step 'none'"):
            render_checkpoint(cycle=5, step="EXIT", next_step="none")

        # Parse still coerces legacy 'none' inputs to COMPLETE so
        # existing on-disk checkpoints (pre-M50) resume trajectory.
        legacy_content = (
            "cycle: 5\nstep: EXIT\nnext_step: none\ncurrent_phase: \n"
        )
        cp = parse_checkpoint(legacy_content)
        assert cp.next_step == "COMPLETE"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:

    def test_status_rejects_invalid_phase_status(self) -> None:
        with pytest.raises(ValueError, match="invalid phase status"):
            render_status(
                milestone="m1", phase="impl", cycle=1,
                next_step="PLAN",
                phase_progress={"impl": "YOLO"},
            )

    def test_status_rejects_negative_cycle(self) -> None:
        # cycle-validation short-circuits before next_step-validation,
        # so no explicit next_step needed here.
        with pytest.raises(ValueError, match="non-negative"):
            render_status(milestone="m1", phase="impl", cycle=-1)

    def test_execution_summary_rejects_invalid_status(self) -> None:
        with pytest.raises(ValueError, match="invalid execution status"):
            render_execution_summary(status="YOLO")

    def test_execution_task_rejects_invalid_status(self) -> None:
        with pytest.raises(ValueError, match="invalid task status"):
            render_execution_task(task_id=1, name="x", status="YOLO")


class TestSanitizePhase:

    def test_valid_phases(self) -> None:
        assert sanitize_phase("impl") == "impl"
        assert sanitize_phase("shard-infrastructure") == "shard-infrastructure"
        assert sanitize_phase("phase_1") == "phase_1"

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError):
            sanitize_phase("../../escape")

    def test_rejects_slashes(self) -> None:
        with pytest.raises(ValueError):
            sanitize_phase("some/path")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            sanitize_phase("")
