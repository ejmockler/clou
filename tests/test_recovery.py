"""Tests for checkpoint parsing, cycle determination, and escalation writing.

Exercises the public API of clou.recovery: parse_checkpoint,
determine_next_cycle, read_cycle_count, read_cycle_outcome, and the
escalation writer coroutines.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clou.recovery import (
    Checkpoint,
    ConvergenceState,
    _safe_int,
    assess_convergence,
    attempt_self_heal,
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    log_self_heal_attempt,
    parse_checkpoint,
    read_cycle_count,
    read_cycle_outcome,
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_validation_escalation,
)
from clou.validation import Severity, ValidationFinding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    """Write content to path, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


VALID_CHECKPOINT = """\
## Cycle
cycle: 3
step: ASSESS
next_step: EXECUTE (rework)

## Phase Status
current_phase: implementation
phases_completed: 1
phases_total: 3
"""


# ---------------------------------------------------------------------------
# parse_checkpoint
# ---------------------------------------------------------------------------


def test_parse_checkpoint_full() -> None:
    """All fields present are parsed correctly."""
    cp = parse_checkpoint(VALID_CHECKPOINT)
    assert cp.cycle == 3
    assert cp.step == "ASSESS"
    assert cp.next_step == "EXECUTE (rework)"
    assert cp.current_phase == "implementation"
    assert cp.phases_completed == 1
    assert cp.phases_total == 3


def test_parse_checkpoint_empty_string() -> None:
    """Empty content yields all defaults."""
    cp = parse_checkpoint("")
    assert cp == Checkpoint()


def test_parse_checkpoint_partial() -> None:
    """Only some fields present — rest default."""
    cp = parse_checkpoint("cycle: 5\nstep: VERIFY\n")
    assert cp.cycle == 5
    assert cp.step == "VERIFY"
    assert cp.next_step == "PLAN"
    assert cp.current_phase == ""
    assert cp.phases_completed == 0
    assert cp.phases_total == 0


def test_parse_checkpoint_ignores_non_matching_lines() -> None:
    """Lines that are not key: value are ignored."""
    content = "# Title\nSome prose.\ncycle: 2\n\n---\n"
    cp = parse_checkpoint(content)
    assert cp.cycle == 2
    assert cp.step == "PLAN"


def test_parse_checkpoint_frozen() -> None:
    """Checkpoint is immutable."""
    cp = parse_checkpoint("cycle: 1\n")
    with pytest.raises(AttributeError):
        cp.cycle = 99  # type: ignore[misc]


def test_parse_checkpoint_whitespace_in_value() -> None:
    """Values with spaces (like 'EXECUTE (rework)') are preserved."""
    cp = parse_checkpoint("next_step: EXECUTE (rework)\n")
    assert cp.next_step == "EXECUTE (rework)"


# ---------------------------------------------------------------------------
# determine_next_cycle
# ---------------------------------------------------------------------------


def test_determine_next_cycle_no_file(tmp_path: Path) -> None:
    """Missing checkpoint returns PLAN with initial read set."""
    cycle_type, read_set = determine_next_cycle(tmp_path / "nonexistent.md", "m1")
    assert cycle_type == "PLAN"
    assert read_set == ["milestone.md", "requirements.md", "project.md"]


def test_determine_next_cycle_plan(tmp_path: Path) -> None:
    """next_step=PLAN returns PLAN cycle."""
    cp_path = tmp_path / "coordinator.md"
    _write(cp_path, "cycle: 1\nstep: PLAN\nnext_step: PLAN\n")
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "PLAN"
    assert "milestone.md" in read_set


def test_determine_next_cycle_execute(tmp_path: Path) -> None:
    """next_step=EXECUTE returns EXECUTE cycle with phase-specific files."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: design\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "EXECUTE"
    assert "phases/design/phase.md" in read_set
    assert "active/coordinator.md" in read_set


def test_determine_next_cycle_execute_rework(tmp_path: Path) -> None:
    """next_step='EXECUTE (rework)' also maps to EXECUTE."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 3\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        "current_phase: implementation\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "EXECUTE"
    assert "phases/implementation/phase.md" in read_set


def test_determine_next_cycle_assess(tmp_path: Path) -> None:
    """next_step=ASSESS returns ASSESS cycle."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: testing\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "ASSESS"
    assert "phases/testing/execution.md" in read_set
    assert "decisions.md" in read_set
    assert "assessment.md" in read_set


def test_determine_next_cycle_verify(tmp_path: Path) -> None:
    """next_step=VERIFY returns VERIFY cycle."""
    cp_path = tmp_path / "coordinator.md"
    _write(cp_path, "cycle: 5\nstep: ASSESS\nnext_step: VERIFY\n")
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "VERIFY"
    assert "requirements.md" in read_set
    assert "compose.py" in read_set


def test_determine_next_cycle_exit(tmp_path: Path) -> None:
    """next_step=EXIT returns EXIT cycle."""
    cp_path = tmp_path / "coordinator.md"
    _write(cp_path, "cycle: 6\nstep: VERIFY\nnext_step: EXIT\n")
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "EXIT"
    assert "handoff.md" in read_set
    assert "decisions.md" in read_set


def test_determine_next_cycle_complete(tmp_path: Path) -> None:
    """next_step=COMPLETE returns COMPLETE with empty read set."""
    cp_path = tmp_path / "coordinator.md"
    _write(cp_path, "cycle: 7\nstep: EXIT\nnext_step: COMPLETE\n")
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "COMPLETE"
    assert read_set == []


def test_determine_next_cycle_unknown_falls_back(tmp_path: Path) -> None:
    """An unrecognized next_step falls back to PLAN."""
    cp_path = tmp_path / "coordinator.md"
    _write(cp_path, "cycle: 1\nstep: PLAN\nnext_step: UNKNOWN_STEP\n")
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "PLAN"
    assert "milestone.md" in read_set


# ---------------------------------------------------------------------------
# read_cycle_count
# ---------------------------------------------------------------------------


def test_read_cycle_count_no_file(tmp_path: Path) -> None:
    """Returns 0 when checkpoint file doesn't exist."""
    assert read_cycle_count(tmp_path / "nonexistent.md") == 0


def test_read_cycle_count_from_file(tmp_path: Path) -> None:
    """Returns the cycle number from the checkpoint."""
    cp_path = tmp_path / "coordinator.md"
    _write(cp_path, "cycle: 12\nstep: ASSESS\n")
    assert read_cycle_count(cp_path) == 12


# ---------------------------------------------------------------------------
# read_cycle_outcome
# ---------------------------------------------------------------------------


def test_read_cycle_outcome_no_file(tmp_path: Path) -> None:
    """Returns 'PLAN' when no checkpoint exists."""
    assert read_cycle_outcome(tmp_path) == "PLAN"


def test_read_cycle_outcome_from_checkpoint(tmp_path: Path) -> None:
    """Returns next_step from existing checkpoint."""
    _write(
        tmp_path / ".clou" / "active" / "coordinator.md",
        "cycle: 3\nstep: ASSESS\nnext_step: VERIFY\n",
    )
    assert read_cycle_outcome(tmp_path) == "VERIFY"


# ---------------------------------------------------------------------------
# Escalation writers
# ---------------------------------------------------------------------------


def test_write_cycle_limit_escalation(tmp_path: Path) -> None:
    """Creates escalation file with correct structure."""
    asyncio.run(write_cycle_limit_escalation(tmp_path, "m1", 20))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("-cycle-limit.md")

    content = files[0].read_text()
    assert "# Escalation: Cycle Limit Reached" in content
    assert "**Classification:** blocking" in content
    assert "**Filed:**" in content
    assert "## Context" in content
    assert "## Issue" in content
    assert "20" in content
    assert "## Evidence" in content
    assert "## Options" in content
    assert "## Recommendation" in content
    assert "## Disposition" in content
    assert "status: open" in content


def test_write_agent_crash_escalation(tmp_path: Path) -> None:
    """Creates agent crash escalation file."""
    asyncio.run(write_agent_crash_escalation(tmp_path, "m1"))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("-agent-crash.md")

    content = files[0].read_text()
    assert "# Escalation: Agent Crash" in content
    assert "**Classification:** blocking" in content


def test_write_validation_escalation(tmp_path: Path) -> None:
    """Creates validation escalation file with error details."""
    errors = ["missing '## Cycle'", "task 1 missing '**Status:**'"]
    asyncio.run(write_validation_escalation(tmp_path, "m1", errors))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("-validation-failure.md")

    content = files[0].read_text()
    assert "# Escalation: Repeated Validation Failures" in content
    assert "**Classification:** blocking" in content
    assert "missing '## Cycle'" in content
    assert "task 1 missing '**Status:**'" in content


def test_escalation_file_naming_format(tmp_path: Path) -> None:
    """Escalation file name follows {timestamp}-{slug}.md pattern."""
    asyncio.run(write_agent_crash_escalation(tmp_path, "m1"))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    name = files[0].name
    # Should be like 20260319-041500-agent-crash.md
    assert name.endswith("-agent-crash.md")
    # Timestamp part: 8 digit date - 6 digit time
    parts = name.split("-")
    assert len(parts[0]) == 8
    assert parts[0].isdigit()
    assert len(parts[1]) == 6
    assert parts[1].isdigit()


def test_escalation_sections_complete(tmp_path: Path) -> None:
    """All required escalation sections are present."""
    asyncio.run(write_cycle_limit_escalation(tmp_path, "m1", 20))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    content = next(esc_dir.iterdir()).read_text()
    for section in [
        "# Escalation:",
        "**Classification:**",
        "**Filed:**",
        "## Context",
        "## Issue",
        "## Evidence",
        "## Options",
        "## Recommendation",
        "## Disposition",
    ]:
        assert section in content, f"Missing section: {section}"


# ---------------------------------------------------------------------------
# git_revert_golden_context — signature only
# ---------------------------------------------------------------------------


def test_git_revert_golden_context_exists() -> None:
    """git_revert_golden_context is an async function with the right signature."""
    import inspect

    assert inspect.iscoroutinefunction(git_revert_golden_context)
    sig = inspect.signature(git_revert_golden_context)
    params = list(sig.parameters.keys())
    assert params == ["project_dir", "milestone"]


# ---------------------------------------------------------------------------
# assess_convergence
# ---------------------------------------------------------------------------


# Sample decisions.md content — newest-first per DB-08
_DECISIONS_CONVERGED = """\
## Cycle 5 — Brutalist Assessment

### Overridden: "Missing rate limiting"
**Brutalist said:** "No rate limiting on any endpoint"
**Action:** Override — no changes
**Reasoning:** Out of scope for this milestone.

## Cycle 4 — Brutalist Assessment

### Overridden: "Architecture should use microservices"
**Brutalist said:** "Monolith won't scale"
**Action:** Override — no changes
**Reasoning:** Intentional design per project.md.

## Cycle 3 — Brutalist Assessment

### Accepted: SQL injection risk in search handler
**Brutalist said:** "User input concatenated into SQL"
**Action:** Created rework task
**Reasoning:** Valid security finding.
"""

_DECISIONS_NOT_CONVERGED = """\
## Cycle 3 — Brutalist Assessment

### Accepted: Missing input validation
**Brutalist said:** "Orders endpoint accepts negative quantities"
**Action:** Created rework task
**Reasoning:** Valid finding.

## Cycle 2 — Brutalist Assessment

### Overridden: "Should add caching"
**Brutalist said:** "No caching layer"
**Action:** Override
**Reasoning:** Out of scope.
"""

_DECISIONS_ALL_ACCEPTED = """\
## Cycle 2 — Brutalist Assessment

### Accepted: XSS in template rendering
**Brutalist said:** "User input rendered unsanitized"
**Action:** Fix templates
**Reasoning:** Valid.

## Cycle 1 — Brutalist Assessment

### Accepted: SQL injection
**Brutalist said:** "Raw SQL"
**Action:** Parameterize
**Reasoning:** Valid.
"""

_DECISIONS_MIXED_TYPES = """\
## Cycle 4 — Brutalist Assessment

### Overridden: "No error handling"
**Action:** Override
**Reasoning:** Internal functions.

## Cycle 3 — Coordinator Judgment

### Tradeoff: Chose bcrypt over argon2
**Decision:** bcrypt
**Reasoning:** Ecosystem support.

## Cycle 2 — Brutalist Assessment

### Overridden: "Monolith architecture"
**Action:** Override
**Reasoning:** Intentional.
"""


def test_convergence_two_consecutive_zero_accepts() -> None:
    """Two consecutive ASSESS cycles with zero accepts = converged."""
    state = assess_convergence(_DECISIONS_CONVERGED)
    assert state.consecutive_zero_accepts == 2
    assert state.total_assess_cycles == 3
    assert state.converged is True


def test_convergence_most_recent_has_accepted() -> None:
    """Most recent ASSESS has accepted findings = not converged."""
    state = assess_convergence(_DECISIONS_NOT_CONVERGED)
    assert state.consecutive_zero_accepts == 0
    assert state.total_assess_cycles == 2
    assert state.converged is False


def test_convergence_all_accepted() -> None:
    """Every ASSESS cycle has accepted findings = not converged."""
    state = assess_convergence(_DECISIONS_ALL_ACCEPTED)
    assert state.consecutive_zero_accepts == 0
    assert state.total_assess_cycles == 2
    assert state.converged is False


def test_convergence_skips_non_assess_sections() -> None:
    """Coordinator Judgment sections are ignored — only ASSESS counted."""
    state = assess_convergence(_DECISIONS_MIXED_TYPES)
    assert state.consecutive_zero_accepts == 2
    assert state.total_assess_cycles == 2  # Only ASSESS, not Coordinator Judgment
    assert state.converged is True


def test_convergence_empty_decisions() -> None:
    """Empty decisions content = zero cycles, not converged."""
    state = assess_convergence("")
    assert state.consecutive_zero_accepts == 0
    assert state.total_assess_cycles == 0
    assert state.converged is False


def test_convergence_single_zero_accept_below_threshold() -> None:
    """One zero-accept ASSESS is below default threshold of 2."""
    content = """\
## Cycle 2 — Brutalist Assessment

### Overridden: "No tests"
**Action:** Override
**Reasoning:** Tests exist.

## Cycle 1 — Brutalist Assessment

### Accepted: Missing validation
**Action:** Fix
**Reasoning:** Valid.
"""
    state = assess_convergence(content)
    assert state.consecutive_zero_accepts == 1
    assert state.converged is False


def test_convergence_custom_threshold() -> None:
    """Custom threshold changes the convergence point."""
    state = assess_convergence(_DECISIONS_CONVERGED, threshold=3)
    assert state.consecutive_zero_accepts == 2
    assert state.converged is False  # Need 3, only have 2

    state = assess_convergence(_DECISIONS_CONVERGED, threshold=1)
    assert state.converged is True  # Need 1, have 2


def test_convergence_em_dash_and_en_dash() -> None:
    """Both em-dash (—) and en-dash (–) in cycle headers are recognized."""
    content = """\
## Cycle 2 – Brutalist Assessment

### Overridden: "Style issue"
**Action:** Override

## Cycle 1 — Brutalist Assessment

### Overridden: "Other issue"
**Action:** Override
"""
    state = assess_convergence(content)
    assert state.total_assess_cycles == 2
    assert state.consecutive_zero_accepts == 2
    assert state.converged is True


def test_convergence_state_is_frozen() -> None:
    """ConvergenceState is immutable."""
    state = assess_convergence("")
    with pytest.raises(AttributeError):
        state.converged = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# determine_next_cycle — convergence override
# ---------------------------------------------------------------------------


def test_rework_overridden_when_converged(tmp_path: Path) -> None:
    """EXECUTE (rework) becomes VERIFY when decisions.md shows convergence."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 5\nstep: ASSESS\nnext_step: EXECUTE (rework)\ncurrent_phase: impl\n",
    )
    decisions_path = tmp_path / "decisions.md"
    _write(decisions_path, _DECISIONS_CONVERGED)

    cycle_type, read_set = determine_next_cycle(
        cp_path,
        "m1",
        decisions_path=decisions_path,
    )
    assert cycle_type == "VERIFY"
    assert "requirements.md" in read_set
    assert "compose.py" in read_set


def test_rework_proceeds_when_not_converged(tmp_path: Path) -> None:
    """EXECUTE (rework) stays EXECUTE when decisions.md has accepted findings."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 3\nstep: ASSESS\nnext_step: EXECUTE (rework)\ncurrent_phase: impl\n",
    )
    decisions_path = tmp_path / "decisions.md"
    _write(decisions_path, _DECISIONS_NOT_CONVERGED)

    cycle_type, read_set = determine_next_cycle(
        cp_path,
        "m1",
        decisions_path=decisions_path,
    )
    assert cycle_type == "EXECUTE"
    assert "phases/impl/phase.md" in read_set


def test_rework_proceeds_when_no_decisions_path(tmp_path: Path) -> None:
    """EXECUTE (rework) stays EXECUTE when decisions_path is not provided."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 5\nstep: ASSESS\nnext_step: EXECUTE (rework)\ncurrent_phase: impl\n",
    )
    cycle_type, _ = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "EXECUTE"


def test_normal_execute_unaffected_by_convergence(tmp_path: Path) -> None:
    """Regular EXECUTE (after PLAN, not rework) is never overridden."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: design\n",
    )
    decisions_path = tmp_path / "decisions.md"
    _write(decisions_path, _DECISIONS_CONVERGED)

    cycle_type, read_set = determine_next_cycle(
        cp_path,
        "m1",
        decisions_path=decisions_path,
    )
    assert cycle_type == "EXECUTE"
    assert "phases/design/phase.md" in read_set


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------


def test_safe_int_valid() -> None:
    """Valid integer strings are converted correctly."""
    assert _safe_int("42") == 42
    assert _safe_int("0") == 0
    assert _safe_int("1") == 1


def test_safe_int_invalid() -> None:
    """Non-integer strings return the default."""
    assert _safe_int("boom") == 0
    assert _safe_int("boom", 5) == 5
    assert _safe_int("") == 0
    assert _safe_int("3.14") == 0


def test_safe_int_negative() -> None:
    """Negative values are clamped to 0."""
    assert _safe_int("-1") == 0
    assert _safe_int("-999") == 0


def test_safe_int_none_like() -> None:
    """TypeError inputs (e.g. None cast) return default."""
    assert _safe_int(None, 7) == 7  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_checkpoint — malformed int fields
# ---------------------------------------------------------------------------


def test_parse_checkpoint_malformed_cycle() -> None:
    """Malformed cycle value falls back to 0 via _safe_int."""
    cp = parse_checkpoint("cycle: boom\nstep: PLAN\n")
    assert cp.cycle == 0
    assert cp.step == "PLAN"


def test_parse_checkpoint_malformed_phases() -> None:
    """Malformed phases_completed/phases_total fall back to 0."""
    cp = parse_checkpoint("cycle: 3\nphases_completed: oops\nphases_total: nope\n")
    assert cp.cycle == 3
    assert cp.phases_completed == 0
    assert cp.phases_total == 0


def test_parse_checkpoint_negative_cycle() -> None:
    """Negative cycle is clamped to 0."""
    cp = parse_checkpoint("cycle: -5\n")
    assert cp.cycle == 0


# ---------------------------------------------------------------------------
# parse_checkpoint — unknown next_step falls back to PLAN
# ---------------------------------------------------------------------------


def test_parse_checkpoint_unknown_next_step_defaults_to_plan() -> None:
    """An unrecognized next_step in checkpoint content defaults to PLAN."""
    cp = parse_checkpoint("cycle: 1\nnext_step: GARBAGE_VALUE\n")
    assert cp.next_step == "PLAN"


def test_parse_checkpoint_valid_next_steps_preserved() -> None:
    """All valid next_step values are preserved as-is."""
    for step in (
        "PLAN",
        "EXECUTE",
        "EXECUTE (rework)",
        "EXECUTE (additional verification)",
        "ASSESS",
        "VERIFY",
        "EXIT",
        "COMPLETE",
    ):
        cp = parse_checkpoint(f"next_step: {step}\n")
        assert cp.next_step == step, f"Expected {step!r}, got {cp.next_step!r}"


# ---------------------------------------------------------------------------
# determine_next_cycle — path traversal in current_phase
# ---------------------------------------------------------------------------


def test_determine_next_cycle_path_traversal_execute(tmp_path: Path) -> None:
    """Path traversal in current_phase during EXECUTE defaults to PLAN."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: ../../etc/passwd\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "PLAN"
    assert read_set == ["milestone.md", "requirements.md", "project.md"]


def test_determine_next_cycle_path_traversal_assess(tmp_path: Path) -> None:
    """Path traversal in current_phase during ASSESS defaults to PLAN."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: foo/../../bar\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "PLAN"
    assert read_set == ["milestone.md", "requirements.md", "project.md"]


def test_determine_next_cycle_slash_in_phase(tmp_path: Path) -> None:
    """A slash in current_phase (no ..) is still rejected."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: nested/phase\n",
    )
    cycle_type, _ = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "PLAN"


def test_stale_complete_checkpoint_detected(tmp_path: Path) -> None:
    """A checkpoint with next_step=COMPLETE from a prior milestone is detectable."""
    cp_path = tmp_path / "coordinator.md"
    cp_path.write_text("cycle: 5\nnext_step: COMPLETE\ncurrent_phase: final\n")
    # determine_next_cycle correctly returns COMPLETE — the caller (run_coordinator)
    # is responsible for clearing stale checkpoints via milestone marker.
    cycle_type, _ = determine_next_cycle(cp_path, "new-milestone")
    assert cycle_type == "COMPLETE"


def test_determine_next_cycle_clean_phase_passes(tmp_path: Path) -> None:
    """A clean current_phase value works normally."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: implementation\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "EXECUTE"
    assert "phases/implementation/phase.md" in read_set


# ---------------------------------------------------------------------------
# Milestone validation — defense-in-depth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_name", ["../../etc", "UPPER", "has space", ".dot", ""])
def test_write_cycle_limit_escalation_rejects_bad_milestone(
    tmp_path: Path, bad_name: str
) -> None:
    with pytest.raises(ValueError, match="Invalid milestone name"):
        asyncio.run(write_cycle_limit_escalation(tmp_path, bad_name, 20))


@pytest.mark.parametrize("bad_name", ["../../etc", "UPPER", "has space", ".dot", ""])
def test_write_agent_crash_escalation_rejects_bad_milestone(
    tmp_path: Path, bad_name: str
) -> None:
    with pytest.raises(ValueError, match="Invalid milestone name"):
        asyncio.run(write_agent_crash_escalation(tmp_path, bad_name))


@pytest.mark.parametrize("bad_name", ["../../etc", "UPPER", "has space", ".dot", ""])
def test_write_validation_escalation_rejects_bad_milestone(
    tmp_path: Path, bad_name: str
) -> None:
    with pytest.raises(ValueError, match="Invalid milestone name"):
        asyncio.run(write_validation_escalation(tmp_path, bad_name, ["err"]))


@pytest.mark.parametrize("bad_name", ["../../etc", "UPPER", "has space", ".dot", ""])
def test_git_revert_rejects_bad_milestone(tmp_path: Path, bad_name: str) -> None:
    with pytest.raises(ValueError, match="Invalid milestone name"):
        asyncio.run(git_revert_golden_context(tmp_path, bad_name))


# ---------------------------------------------------------------------------
# git_revert_golden_context — timeout handling
# ---------------------------------------------------------------------------


def test_git_revert_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung git process is killed after timeout and raises RuntimeError."""
    killed = False

    async def _hanging_subprocess(*args: object, **kwargs: object) -> object:
        """Return a mock process whose communicate() hangs then returns after kill."""

        class _HangingProc:
            def __init__(self) -> None:
                self._hung = True

            async def communicate(self) -> tuple[bytes, bytes]:
                if self._hung:
                    await asyncio.sleep(3600)
                return b"", b""

            def kill(self) -> None:
                nonlocal killed
                killed = True
                self._hung = False

        return _HangingProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _hanging_subprocess)
    # Use a very short internal timeout so the test doesn't wait 30s.
    _original_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro: object, *, timeout: float | None = None) -> object:
        # Replace the 30s timeout with 0.05s for this test
        if timeout == 30:
            timeout = 0.05
        return await _original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)

    with pytest.raises(RuntimeError, match="git revert timed out"):
        asyncio.run(git_revert_golden_context(tmp_path, "m1"))

    assert killed


# ---------------------------------------------------------------------------
# git_commit_phase
# ---------------------------------------------------------------------------


def test_git_commit_phase_exists() -> None:
    """git_commit_phase is an async function with the right signature."""
    import inspect

    assert inspect.iscoroutinefunction(git_commit_phase)
    sig = inspect.signature(git_commit_phase)
    params = list(sig.parameters.keys())
    assert params == ["project_dir", "milestone", "phase"]


@pytest.mark.parametrize("bad_name", ["../../etc", "UPPER", "has space", ".dot", ""])
def test_git_commit_phase_rejects_bad_milestone(
    tmp_path: Path, bad_name: str
) -> None:
    with pytest.raises(ValueError, match="Invalid milestone name"):
        asyncio.run(git_commit_phase(tmp_path, bad_name, "impl"))


def test_git_commit_phase_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung git process is killed after timeout and raises RuntimeError."""
    killed = False

    async def _hanging_subprocess(*args: object, **kwargs: object) -> object:
        class _HangingProc:
            def __init__(self) -> None:
                self._hung = True

            async def communicate(self) -> tuple[bytes, bytes]:
                if self._hung:
                    await asyncio.sleep(3600)
                return b"", b""

            def kill(self) -> None:
                nonlocal killed
                killed = True
                self._hung = False

        return _HangingProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _hanging_subprocess)
    _original_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro: object, *, timeout: float | None = None) -> object:
        if timeout == 30:
            timeout = 0.05
        return await _original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)

    with pytest.raises(RuntimeError, match="git add timed out"):
        asyncio.run(git_commit_phase(tmp_path, "m1", "impl"))

    assert killed


def test_git_commit_phase_calls_git_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git_commit_phase calls git add -A, git diff --cached --quiet, git commit."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            # git diff --cached --quiet returns 1 = there are changes
            returncode = 1 if "diff" in cmd else 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)

    asyncio.run(git_commit_phase(tmp_path, "m1", "design"))

    assert len(commands) == 3
    assert "add" in commands[0]
    assert "-A" in commands[0]
    assert "diff" in commands[1]
    assert "--cached" in commands[1]
    assert "commit" in commands[2]
    assert "-m" in commands[2]


def test_git_commit_phase_skips_when_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When git diff --cached --quiet returns 0, no commit is made."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0  # all succeed; diff quiet = no changes

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)

    asyncio.run(git_commit_phase(tmp_path, "m1", "impl"))

    # Only add + diff, no commit
    assert len(commands) == 2
    assert "add" in commands[0]
    assert "diff" in commands[1]


def test_git_commit_phase_message_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit message follows feat(milestone): complete phase 'phase' format."""
    commit_msg = None

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        nonlocal commit_msg
        cmd = [str(a) for a in args]

        class _Proc:
            returncode = 1 if "diff" in cmd else 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        if "commit" in cmd:
            m_idx = cmd.index("-m")
            commit_msg = cmd[m_idx + 1]

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)

    asyncio.run(git_commit_phase(tmp_path, "auth-v2", "implementation"))

    assert commit_msg == "feat(auth-v2): complete phase 'implementation'"


# ---------------------------------------------------------------------------
# parse_checkpoint — "none" maps to COMPLETE
# ---------------------------------------------------------------------------


def test_parse_checkpoint_none_maps_to_complete() -> None:
    """next_step: none (from coordinator-exit.md) maps to COMPLETE."""
    cp = parse_checkpoint("cycle: 7\nstep: EXIT\nnext_step: none\n")
    assert cp.next_step == "COMPLETE"


def test_parse_checkpoint_none_case_insensitive() -> None:
    """next_step normalization is case-insensitive."""
    for variant in ("none", "None", "NONE"):
        cp = parse_checkpoint(f"next_step: {variant}\n")
        assert cp.next_step == "COMPLETE", f"Failed for {variant!r}"


# ---------------------------------------------------------------------------
# determine_next_cycle — EXECUTE (additional verification)
# ---------------------------------------------------------------------------


def test_determine_next_cycle_additional_verification(tmp_path: Path) -> None:
    """EXECUTE (additional verification) routes to EXECUTE cycle."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 6\nstep: VERIFY\nnext_step: EXECUTE (additional verification)\n"
        "current_phase: verification\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "EXECUTE"
    assert "phases/verification/phase.md" in read_set


def test_additional_verification_not_affected_by_convergence(tmp_path: Path) -> None:
    """EXECUTE (additional verification) is NOT overridden by convergence."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 6\nstep: VERIFY\nnext_step: EXECUTE (additional verification)\n"
        "current_phase: verification\n",
    )
    decisions_path = tmp_path / "decisions.md"
    _write(decisions_path, _DECISIONS_CONVERGED)

    cycle_type, _ = determine_next_cycle(
        cp_path, "m1", decisions_path=decisions_path,
    )
    assert cycle_type == "EXECUTE"  # NOT overridden to VERIFY


# ---------------------------------------------------------------------------
# Self-heal tests — validator-resilience
# ---------------------------------------------------------------------------


def test_self_heal_normalises_status_value(tmp_path: Path) -> None:
    """Self-heal normalises 'in progress' to 'in_progress' in status.md table."""
    status_content = """\
# Status

## Current State
phase: implementation
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
| implementation | in progress |
"""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "status.md",
        status_content,
    )
    errors = [
        ValidationFinding(
            severity=Severity.WARNING,
            message="invalid phase status 'in progress'",
            path="milestones/m1/status.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    assert len(fixes) >= 1
    assert any("normalised" in f for f in fixes)

    # Verify the file was actually fixed.
    content = (tmp_path / ".clou" / "milestones" / "m1" / "status.md").read_text()
    assert "in_progress" in content
    assert "in progress" not in content.split("Phase Progress")[1]


def test_self_heal_adds_missing_field(tmp_path: Path) -> None:
    """Self-heal adds missing phase: field to Current State."""
    status_content = """\
# Status

## Current State
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
"""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "status.md",
        status_content,
    )
    errors = [
        ValidationFinding(
            severity=Severity.ERROR,
            message="'Current State' missing 'phase:' field",
            path="milestones/m1/status.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    assert len(fixes) >= 1
    assert any("phase:" in f for f in fixes)

    # Verify the field was added.
    content = (tmp_path / ".clou" / "milestones" / "m1" / "status.md").read_text()
    assert "phase:" in content


def test_self_heal_skips_non_owned_files(tmp_path: Path) -> None:
    """Self-heal does not touch files outside coordinator write permissions."""
    # execution.md under phases/ is worker-owned, not coordinator-owned.
    exec_content = """\
## Summary
status: completed

## Tasks
### T1: Do something
**Status:** done
"""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "phases" / "impl" / "execution.md",
        exec_content,
    )
    errors = [
        ValidationFinding(
            severity=Severity.WARNING,
            message="task 1 has invalid status 'done'",
            path="milestones/m1/phases/impl/execution.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    assert fixes == []  # nothing attempted

    # Verify file was not modified.
    content = (
        tmp_path / ".clou" / "milestones" / "m1" / "phases" / "impl" / "execution.md"
    ).read_text()
    assert "done" in content


def test_self_heal_idempotent(tmp_path: Path) -> None:
    """Running self-heal twice produces identical file content."""
    status_content = """\
# Status

## Current State
phase: implementation
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
| implementation | in progress |
"""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "status.md",
        status_content,
    )
    errors = [
        ValidationFinding(
            severity=Severity.WARNING,
            message="invalid phase status 'in progress'",
            path="milestones/m1/status.md",
        ),
    ]

    # First application.
    fixes1 = attempt_self_heal(tmp_path, "m1", errors)
    content_after_first = (
        tmp_path / ".clou" / "milestones" / "m1" / "status.md"
    ).read_text()

    # Second application — should be a no-op (no new fixes).
    fixes2 = attempt_self_heal(tmp_path, "m1", errors)
    content_after_second = (
        tmp_path / ".clou" / "milestones" / "m1" / "status.md"
    ).read_text()

    assert content_after_first == content_after_second
    assert len(fixes1) >= 1
    assert fixes2 == []  # second run finds nothing to fix


def test_self_heal_failure_falls_through(tmp_path: Path) -> None:
    """Unfixable error in coordinator-writable file returns empty list."""
    # Missing ## Current State entirely — self-heal can add fields but
    # cannot create the section from scratch.
    status_content = """\
# Status

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
"""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "status.md",
        status_content,
    )
    errors = [
        ValidationFinding(
            severity=Severity.ERROR,
            message="missing 'Current State' section",
            path="milestones/m1/status.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    # No fixes possible — the section header is absent.
    assert fixes == []


def test_self_heal_logs_attempt(tmp_path: Path) -> None:
    """log_self_heal_attempt writes to decisions.md."""
    decisions_path = tmp_path / ".clou" / "milestones" / "m1" / "decisions.md"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text("# Decisions\n\n## Cycle 1\n### Accepted: X\nOK.\n")

    fixes = ["normalised status 'in progress' -> 'in_progress'"]
    remaining = [
        ValidationFinding(Severity.ERROR, "missing section", "status.md"),
    ]
    log_self_heal_attempt(tmp_path, "m1", fixes, remaining)

    content = decisions_path.read_text()
    assert "Self-Heal" in content
    assert "normalised status" in content
    assert "missing section" in content


# ---------------------------------------------------------------------------
# Escalation tests with structured findings — validator-resilience
# ---------------------------------------------------------------------------


def test_escalation_includes_severity_breakdown(tmp_path: Path) -> None:
    """Validation escalation evidence section shows errors vs warnings."""
    findings = [
        ValidationFinding(
            Severity.ERROR, "missing ## Summary", "execution.md",
        ),
        ValidationFinding(
            Severity.WARNING,
            "task 1 missing **Status:**",
            "execution.md",
        ),
    ]
    asyncio.run(
        write_validation_escalation(tmp_path, "m1", findings),
    )
    esc_dir = (
        tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    )
    files = list(esc_dir.iterdir())
    assert len(files) == 1

    content = files[0].read_text()
    assert "Errors (blocking)" in content
    assert "missing ## Summary" in content
    assert "Warnings (non-blocking)" in content
    assert "task 1 missing **Status:**" in content


def test_escalation_classification_blocking_on_errors(tmp_path: Path) -> None:
    """Escalation classification is 'blocking' when errors are present."""
    findings = [
        ValidationFinding(Severity.ERROR, "missing key", "coordinator.md"),
        ValidationFinding(Severity.WARNING, "bad format", "roadmap.md"),
    ]
    asyncio.run(write_validation_escalation(tmp_path, "m1", findings))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    content = next(esc_dir.iterdir()).read_text()
    assert "**Classification:** blocking" in content


def test_escalation_classification_informational_warnings_only(
    tmp_path: Path,
) -> None:
    """Escalation classification is 'informational' when only warnings present."""
    findings = [
        ValidationFinding(Severity.WARNING, "bad format", "roadmap.md"),
    ]
    asyncio.run(write_validation_escalation(tmp_path, "m1", findings))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    content = next(esc_dir.iterdir()).read_text()
    assert "**Classification:** informational" in content


def test_escalation_with_legacy_strings(tmp_path: Path) -> None:
    """Validation escalation still works with plain string errors (backward compat)."""
    errors = ["missing '## Cycle'", "task 1 missing '**Status:**'"]
    asyncio.run(write_validation_escalation(tmp_path, "m1", errors))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    content = next(esc_dir.iterdir()).read_text()
    assert "**Classification:** blocking" in content
    assert "missing '## Cycle'" in content
