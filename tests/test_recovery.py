"""Tests for checkpoint parsing, cycle determination, and escalation writing.

Exercises the public API of clou.recovery: parse_checkpoint,
determine_next_cycle, read_cycle_count, read_cycle_outcome, and the
escalation writer coroutines.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from clou.recovery import (
    Checkpoint,
    ConvergenceState,
    MemoryPattern,
    _consolidated_milestones,
    _milestone_sort_key,
    _parse_memory,
    _render_memory,
    _safe_int,
    _analyze_compose,
    _count_metrics_section_rows,
    _parse_metrics_header,
    _reinforce_or_create,
    _apply_decay,
    assess_convergence,
    attempt_self_heal,
    consolidate_milestone,
    consolidate_pending,
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    log_self_heal_attempt,
    parse_checkpoint,
    read_cycle_count,
    read_cycle_outcome,
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_staleness_escalation,
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
    assert read_set == ["milestone.md", "intents.md", "requirements.md", "project.md"]


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
    assert "active/coordinator.md" not in read_set  # DB-15: removed from read sets


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
    assert "intents.md" in read_set
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
# determine_next_cycle — shard-aware ASSESS read set (M17)
# ---------------------------------------------------------------------------


def test_assess_read_set_with_shards(tmp_path: Path) -> None:
    """ASSESS includes execution-*.md shard files when they exist on disk."""
    # Build a directory structure that matches the checkpoint path layout:
    # checkpoint lives at {ms}/active/coordinator.md
    # shards live at {ms}/phases/{phase}/execution-{task}.md
    ms_dir = tmp_path / "milestones" / "m1"
    cp_path = ms_dir / "active" / "coordinator.md"
    phase_dir = ms_dir / "phases" / "building"
    phase_dir.mkdir(parents=True)
    cp_path.parent.mkdir(parents=True)
    cp_path.write_text(
        "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: building\n"
    )
    # Create the merged execution.md and two shard files.
    (phase_dir / "execution.md").write_text("## Summary\nstatus: completed\n")
    (phase_dir / "execution-alpha.md").write_text("## Summary\nstatus: completed\n")
    (phase_dir / "execution-beta.md").write_text("## Summary\nstatus: completed\n")

    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "ASSESS"
    # Standard entries always present.
    assert "phases/building/execution.md" in read_set
    assert "requirements.md" in read_set
    assert "decisions.md" in read_set
    assert "assessment.md" in read_set
    # Shard files appended.
    assert "phases/building/execution-alpha.md" in read_set
    assert "phases/building/execution-beta.md" in read_set


def test_assess_read_set_without_shards(tmp_path: Path) -> None:
    """No shard files present -- standard execution.md only, no extras."""
    ms_dir = tmp_path / "milestones" / "m1"
    cp_path = ms_dir / "active" / "coordinator.md"
    phase_dir = ms_dir / "phases" / "testing"
    phase_dir.mkdir(parents=True)
    cp_path.parent.mkdir(parents=True)
    cp_path.write_text(
        "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: testing\n"
    )
    (phase_dir / "execution.md").write_text("## Summary\nstatus: completed\n")

    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "ASSESS"
    assert "phases/testing/execution.md" in read_set
    # No shard entries.
    shard_entries = [f for f in read_set if "execution-" in f]
    assert shard_entries == []


def test_assess_read_set_no_phase_dir(tmp_path: Path) -> None:
    """Phase dir does not exist yet -- graceful, no shards added."""
    ms_dir = tmp_path / "milestones" / "m1"
    cp_path = ms_dir / "active" / "coordinator.md"
    cp_path.parent.mkdir(parents=True)
    cp_path.write_text(
        "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: missing\n"
    )

    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "ASSESS"
    assert "phases/missing/execution.md" in read_set
    # No crash, no shard entries.
    shard_entries = [f for f in read_set if "execution-" in f]
    assert shard_entries == []


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


def test_write_staleness_escalation(tmp_path: Path) -> None:
    """Creates staleness escalation file with expected content."""
    asyncio.run(
        write_staleness_escalation(
            tmp_path, "m1",
            cycle_type="EXECUTE",
            consecutive_count=3,
            phases_completed=1,
            next_step="EXECUTE",
        )
    )
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("-staleness.md")

    content = files[0].read_text()
    assert "# Escalation: Staleness Detected" in content
    assert "**Classification:** blocking" in content
    assert "cycle_type: EXECUTE" in content
    assert "consecutive_count: 3" in content
    assert "phases_completed: 1" in content
    assert "next_step: EXECUTE" in content
    for section in [
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
    assert params == ["project_dir", "milestone", "current_phase"]


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
    assert "intents.md" in read_set
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
    assert read_set == ["milestone.md", "intents.md", "requirements.md", "project.md"]


def test_determine_next_cycle_path_traversal_assess(tmp_path: Path) -> None:
    """Path traversal in current_phase during ASSESS defaults to PLAN."""
    cp_path = tmp_path / "coordinator.md"
    _write(
        cp_path,
        "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: foo/../../bar\n",
    )
    cycle_type, read_set = determine_next_cycle(cp_path, "m1")
    assert cycle_type == "PLAN"
    assert read_set == ["milestone.md", "intents.md", "requirements.md", "project.md"]


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


@pytest.mark.parametrize("bad_phase", ["../../../etc", "foo/bar", "a/../b", "sub/dir"])
def test_git_revert_rejects_traversal_in_current_phase(
    tmp_path: Path, bad_phase: str
) -> None:
    """F4: current_phase with path traversal sequences is rejected."""
    with pytest.raises(ValueError, match="Invalid current_phase"):
        asyncio.run(
            git_revert_golden_context(tmp_path, "m1", current_phase=bad_phase)
        )


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

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(git_commit_phase(tmp_path, "m1", "impl"))

    assert killed


def test_git_commit_phase_calls_git_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git_commit_phase uses selective staging: git diff, git ls-files, git add, git commit."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            # git diff --cached --quiet returns 1 = there are staged changes
            returncode = 1 if ("diff" in cmd and "--cached" in cmd) else 0

            async def communicate(self) -> tuple[bytes, bytes]:
                if "diff" in cmd and "--name-only" in cmd:
                    return b".clou/milestones/m1/status.md\n", b""
                if "ls-files" in cmd:
                    return b".clou/milestones/m1/phases/design/phase.md\n", b""
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)

    asyncio.run(git_commit_phase(tmp_path, "m1", "design"))

    # Should have: diff --name-only, ls-files, add (selective),
    # diff --cached --quiet, commit
    assert len(commands) == 5
    assert "diff" in commands[0] and "--name-only" in commands[0]
    assert "ls-files" in commands[1]
    assert "add" in commands[2]
    assert "-A" not in commands[2]  # NOT git add -A
    assert ".clou/milestones/m1/status.md" in commands[2] or \
        ".clou/milestones/m1/phases/design/phase.md" in commands[2]
    assert "diff" in commands[3] and "--cached" in commands[3]
    assert "commit" in commands[4]
    assert "-m" in commands[4]


def test_git_commit_phase_skips_when_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no files changed, no commit is made."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""  # No changed files

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)

    asyncio.run(git_commit_phase(tmp_path, "m1", "impl"))

    # Only diff + ls-files, no add or commit
    assert len(commands) == 2
    assert "diff" in commands[0]
    assert "ls-files" in commands[1]


def test_git_commit_phase_message_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit message follows feat(milestone): complete phase 'phase' format."""
    commit_msg = None

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        nonlocal commit_msg
        cmd = [str(a) for a in args]

        class _Proc:
            returncode = 1 if ("diff" in cmd and "--cached" in cmd) else 0

            async def communicate(self) -> tuple[bytes, bytes]:
                if "diff" in cmd and "--name-only" in cmd:
                    return b".clou/milestones/auth-v2/status.md\n", b""
                return b"", b""

        if "commit" in cmd:
            m_idx = cmd.index("-m")
            commit_msg = cmd[m_idx + 1]

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)

    asyncio.run(git_commit_phase(tmp_path, "auth-v2", "implementation"))

    assert commit_msg == "feat(auth-v2): complete phase 'implementation'"


# ---------------------------------------------------------------------------
# V4: git_commit_phase — milestone-scoped staging only
# ---------------------------------------------------------------------------


def test_git_commit_phase_excludes_non_milestone_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V4: Only files under .clou/milestones/{ms}/ are staged; user files excluded."""
    staged_files: list[str] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]

        class _Proc:
            returncode = 1 if ("diff" in cmd and "--cached" in cmd) else 0

            async def communicate(self) -> tuple[bytes, bytes]:
                if "diff" in cmd and "--name-only" in cmd:
                    # Simulate user-changed file + milestone file
                    return (
                        b"src/app.py\n"
                        b".clou/milestones/m1/status.md\n",
                        b"",
                    )
                if "ls-files" in cmd:
                    return (
                        b"README.md\n"
                        b".clou/milestones/m1/phases/impl/execution.md\n",
                        b"",
                    )
                return b"", b""

        if "add" in cmd:
            # Capture what was staged
            dash_idx = cmd.index("--")
            staged_files.extend(cmd[dash_idx + 1 :])

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(git_commit_phase(tmp_path, "m1", "impl"))

    # Only milestone-scoped files should be staged
    assert ".clou/milestones/m1/status.md" in staged_files
    assert ".clou/milestones/m1/phases/impl/execution.md" in staged_files
    # User files must NOT be staged
    assert "src/app.py" not in staged_files
    assert "README.md" not in staged_files


def test_git_commit_phase_excludes_other_milestone_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V4: Files from other milestones are not staged."""
    staged_files: list[str] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]

        class _Proc:
            returncode = 1 if ("diff" in cmd and "--cached" in cmd) else 0

            async def communicate(self) -> tuple[bytes, bytes]:
                if "diff" in cmd and "--name-only" in cmd:
                    return (
                        b".clou/milestones/m1/status.md\n"
                        b".clou/milestones/m2/status.md\n",
                        b"",
                    )
                return b"", b""

        if "add" in cmd:
            dash_idx = cmd.index("--")
            staged_files.extend(cmd[dash_idx + 1 :])

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(git_commit_phase(tmp_path, "m1", "impl"))

    assert ".clou/milestones/m1/status.md" in staged_files
    assert ".clou/milestones/m2/status.md" not in staged_files


# ---------------------------------------------------------------------------
# V6: git_revert_golden_context — preserve completed phases' execution.md
# ---------------------------------------------------------------------------


def test_git_revert_scopes_to_current_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V6: When current_phase is given, only that phase dir is reverted."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(
        git_revert_golden_context(tmp_path, "m1", current_phase="impl")
    )

    # First command is git checkout
    checkout_cmd = commands[0]
    assert "checkout" in checkout_cmd
    # Should include phases/impl/ but NOT phases/ (all phases)
    paths_after_dash = checkout_cmd[checkout_cmd.index("--") + 1 :]
    phase_paths = [p for p in paths_after_dash if "phases/" in p]
    assert len(phase_paths) == 1
    assert phase_paths[0] == ".clou/milestones/m1/phases/impl/"


def test_git_revert_without_current_phase_reverts_all_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V6: Without current_phase, all phases are reverted (backward compat)."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(git_revert_golden_context(tmp_path, "m1"))

    checkout_cmd = commands[0]
    paths_after_dash = checkout_cmd[checkout_cmd.index("--") + 1 :]
    phase_paths = [p for p in paths_after_dash if "phases/" in p]
    assert len(phase_paths) == 1
    assert phase_paths[0] == ".clou/milestones/m1/phases/"


# ---------------------------------------------------------------------------
# V10: git_revert_golden_context — clean untracked files
# ---------------------------------------------------------------------------


def test_git_revert_runs_git_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V10: After checkout revert, git clean -fd is run on directory paths."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(
        git_revert_golden_context(tmp_path, "m1", current_phase="impl")
    )

    # Should have two commands: checkout + clean
    assert len(commands) == 2
    clean_cmd = commands[1]
    assert "clean" in clean_cmd
    assert "-fd" in clean_cmd
    # Clean paths should only be directories (ending with /)
    clean_paths = clean_cmd[clean_cmd.index("--") + 1 :]
    for p in clean_paths:
        assert p.endswith("/"), f"Clean path {p!r} should end with /"
    # Must include the current phase directory
    assert ".clou/milestones/m1/phases/impl/" in clean_paths
    # Must include active/ and escalations/
    assert ".clou/milestones/m1/active/" in clean_paths
    assert ".clou/milestones/m1/escalations/" in clean_paths


def test_git_revert_clean_scoped_to_milestone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V10: git clean is scoped to milestone dirs only, never user files."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(git_revert_golden_context(tmp_path, "m1"))

    clean_cmd = commands[1]
    clean_paths = clean_cmd[clean_cmd.index("--") + 1 :]
    for p in clean_paths:
        assert p.startswith(".clou/milestones/m1/"), (
            f"Clean path {p!r} is outside milestone directory"
        )


# ---------------------------------------------------------------------------
# T3: git_revert_golden_context — excludes supervisor files
# ---------------------------------------------------------------------------


def test_git_revert_excludes_supervisor_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3: Revert path list excludes supervisor files (milestone.md, intents.md, requirements.md)."""
    commands: list[list[str]] = []

    async def _mock_subprocess(*args: object, **kwargs: object) -> object:
        cmd = [str(a) for a in args]
        commands.append(cmd)

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_subprocess)
    asyncio.run(git_revert_golden_context(tmp_path, "m1"))

    checkout_cmd = commands[0]
    paths_after_dash = checkout_cmd[checkout_cmd.index("--") + 1 :]

    # Supervisor files must NOT be in the revert path list
    supervisor_files = ["milestone.md", "intents.md", "requirements.md"]
    for sf in supervisor_files:
        full_path = f".clou/milestones/m1/{sf}"
        assert full_path not in paths_after_dash, (
            f"Supervisor file {sf!r} should not be reverted"
        )

    # Coordinator-owned paths should be present
    assert ".clou/milestones/m1/active/" in paths_after_dash
    assert ".clou/milestones/m1/status.md" in paths_after_dash
    assert ".clou/milestones/m1/compose.py" in paths_after_dash
    assert ".clou/milestones/m1/decisions.md" in paths_after_dash
    assert ".clou/milestones/m1/assessment.md" in paths_after_dash
    assert ".clou/milestones/m1/escalations/" in paths_after_dash


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


# ---------------------------------------------------------------------------
# Checkpoint normalisation (self-heal)
# ---------------------------------------------------------------------------


def test_self_heal_normalises_checkpoint_alias(tmp_path: Path) -> None:
    """Self-heal re-renders checkpoint, resolving 'phase' alias to 'current_phase'."""
    checkpoint = "cycle: 3\nnext_step: VERIFY\nphase: impl\n"
    cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
    _write(cp_path, checkpoint)
    errors = [
        ValidationFinding(
            severity=Severity.WARNING,
            message="optional key 'current_phase' missing",
            path="milestones/m1/active/coordinator.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    assert len(fixes) >= 1

    content = cp_path.read_text()
    assert "current_phase: impl" in content
    # Re-rendered via serializer: all fields present, canonical format.
    assert "cycle: 3" in content
    assert "next_step: VERIFY" in content


def test_self_heal_adds_missing_checkpoint_fields(tmp_path: Path) -> None:
    """Self-heal injects missing optional fields with defaults."""
    checkpoint = "cycle: 2\nnext_step: ASSESS\n"
    cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
    _write(cp_path, checkpoint)
    errors = [
        ValidationFinding(
            severity=Severity.WARNING,
            message="optional key 'step' missing",
            path="milestones/m1/active/coordinator.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    assert len(fixes) >= 1

    content = cp_path.read_text()
    assert "step: PLAN" in content
    assert "current_phase:" in content
    assert "phases_completed: 0" in content
    assert "phases_total: 0" in content


def test_self_heal_checkpoint_idempotent(tmp_path: Path) -> None:
    """Running checkpoint self-heal twice produces no additional changes."""
    checkpoint = "cycle: 2\nnext_step: ASSESS\nphase: impl\n"
    cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
    _write(cp_path, checkpoint)
    errors = [
        ValidationFinding(
            severity=Severity.WARNING,
            message="optional key 'step' missing",
            path="milestones/m1/active/coordinator.md",
        ),
    ]
    fixes1 = attempt_self_heal(tmp_path, "m1", errors)
    content_after_first = cp_path.read_text()

    fixes2 = attempt_self_heal(tmp_path, "m1", errors)
    content_after_second = cp_path.read_text()

    assert len(fixes1) >= 1
    assert len(fixes2) == 0
    assert content_after_first == content_after_second


def test_self_heal_checkpoint_skips_without_required_keys(tmp_path: Path) -> None:
    """Self-heal does nothing if required keys (cycle, next_step) are missing."""
    checkpoint = "# Coordinator\nJust some text.\n"
    cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
    _write(cp_path, checkpoint)
    errors = [
        ValidationFinding(
            severity=Severity.ERROR,
            message="missing required key 'cycle'",
            path="milestones/m1/active/coordinator.md",
        ),
    ]
    fixes = attempt_self_heal(tmp_path, "m1", errors)
    assert fixes == []
    assert cp_path.read_text() == checkpoint


# ---------------------------------------------------------------------------
# DB-15: Decisions compaction
# ---------------------------------------------------------------------------


def test_compact_decisions_below_threshold(tmp_path: Path) -> None:
    """No compaction when decisions.md is below token threshold."""
    path = tmp_path / "decisions.md"
    path.write_text("## Cycle 1 — Assessment\nSmall content\n")
    from clou.recovery import compact_decisions

    assert compact_decisions(path) is False


def test_compact_decisions_keeps_recent(tmp_path: Path) -> None:
    """Recent 3 cycle groups kept in full, older compacted."""
    from clou.recovery import compact_decisions

    groups = []
    for i in range(6):
        groups.append(
            f"## Cycle {6 - i} — Quality Gate Assessment\n"
            f"### Accepted: finding-{i}a\n**Finding:** \"detail\"\n"
            f"### Overridden: finding-{i}b\n**Finding:** \"detail\"\n"
        )
    path = tmp_path / "decisions.md"
    path.write_text("# Decisions\n\n" + "\n".join(groups))

    result = compact_decisions(path, token_threshold=0)  # Force compaction
    assert result is True

    content = path.read_text()
    # Recent 3 (Cycles 6, 5, 4) should have full detail
    assert "### Accepted: finding-0a" in content  # Cycle 6
    assert "### Accepted: finding-1a" in content  # Cycle 5
    assert "### Accepted: finding-2a" in content  # Cycle 4
    # Older (Cycles 3, 2, 1) should be compacted
    assert "(compacted)" in content
    assert "### Accepted: finding-3a" not in content  # Cycle 3 compacted


def test_compact_decisions_preserves_counts(tmp_path: Path) -> None:
    """Compacted groups show accepted/overridden counts."""
    from clou.recovery import compact_decisions

    groups = []
    for i in range(5):
        groups.append(
            f"## Cycle {5 - i} — Assessment\n"
            f"### Accepted: f1\n**Finding:** \"d\"\n"
            f"### Accepted: f2\n**Finding:** \"d\"\n"
            f"### Overridden: f3\n**Finding:** \"d\"\n"
        )
    path = tmp_path / "decisions.md"
    path.write_text("\n".join(groups))

    compact_decisions(path, token_threshold=0)
    content = path.read_text()
    assert "Accepted: 2" in content
    assert "Overridden: 1" in content


# ---------------------------------------------------------------------------
# DB-15: Selective staging excludes patterns
# ---------------------------------------------------------------------------


def test_staging_exclude_patterns() -> None:
    """_STAGING_EXCLUDE_PATTERNS filters telemetry, sessions, and common artifacts."""
    import fnmatch

    from clou.recovery import _STAGING_EXCLUDE_PATTERNS

    should_exclude = [
        ".clou/telemetry/span.jsonl",
        ".clou/sessions/abc.jsonl",
        "node_modules/express/index.js",
        "__pycache__/foo.cpython-313.pyc",
        "app.pyc",
        ".env",
        ".env.local",
    ]
    should_include = [
        "src/main.py",
        ".clou/milestones/m1/status.md",
        "tests/test_app.py",
    ]
    for f in should_exclude:
        assert any(
            fnmatch.fnmatch(f, pat) for pat in _STAGING_EXCLUDE_PATTERNS
        ), f"Should exclude {f}"
    for f in should_include:
        assert not any(
            fnmatch.fnmatch(f, pat) for pat in _STAGING_EXCLUDE_PATTERNS
        ), f"Should include {f}"


def test_staging_scopes_clou_to_active_milestone() -> None:
    """V4: git_commit_phase only stages files within .clou/milestones/{ms}/."""
    import fnmatch  # noqa: F811

    from clou.recovery import _STAGING_EXCLUDE_PATTERNS  # noqa: F811

    milestone = "my-ms"
    milestone_prefix = f".clou/milestones/{milestone}/"
    changed = [
        ".clou/milestones/my-ms/compose.py",       # should include
        ".clou/milestones/my-ms/status.md",         # should include
        ".clou/milestones/other-ms/compose.py",     # different milestone -- exclude
        ".clou/memory.md",                          # shared .clou/ metadata -- exclude
        "src/main.py",                              # workspace file -- exclude (V4)
    ]
    to_stage = [
        f for f in changed
        if f.startswith(milestone_prefix)
        and not any(fnmatch.fnmatch(f, pat) for pat in _STAGING_EXCLUDE_PATTERNS)
    ]
    assert ".clou/milestones/my-ms/compose.py" in to_stage
    assert ".clou/milestones/my-ms/status.md" in to_stage
    assert "src/main.py" not in to_stage  # V4: user files excluded
    assert ".clou/milestones/other-ms/compose.py" not in to_stage
    assert ".clou/memory.md" not in to_stage


# ---------------------------------------------------------------------------
# DB-18: Memory consolidation
# ---------------------------------------------------------------------------

_SAMPLE_MEMORY = """\
# Operational Memory

## Patterns

### cycle-count-distribution
type: cost-calibration
observed: 12-reasoning-loop, 13-convergence
reinforced: 2
last_active: 13-convergence

2 cycles per milestone on average.

### decomposition-topology
type: decomposition
observed: 12-reasoning-loop
reinforced: 1
last_active: 12-reasoning-loop
status: fading

1 phase, sequential execution.

## Archived

### old-pattern
type: debt
observed: 5-legacy
reinforced: 1
last_active: 5-legacy
status: archived

Legacy pattern no longer relevant.
"""

_SAMPLE_METRICS = """\
# Metrics: 14-test

outcome: completed
cycles: 5
duration: 35m 10s
tokens_in: 500
tokens_out: 50000
agents_spawned: 6
agents_completed: 6
agents_failed: 0
crash_retries: 0
validation_failures: 2
context_exhaustions: 0

## Cycles

| # | Type | Duration | Tokens In | Tokens Out | Outcome |
|---|------|----------|-----------|------------|---------|
| 1 | PLAN | 5m | 100 | 10000 | EXECUTE |
| 2 | EXECUTE | 10m | 200 | 20000 | ASSESS |
| 3 | ASSESS | 8m | 100 | 8000 | VERIFY |
| 4 | VERIFY | 10m | 50 | 10000 | EXIT |
| 5 | EXIT | 2m | 50 | 2000 | COMPLETE |

## Rework

| Cycle | From | To | Phase |
|-------|------|----|-------|
| 3 | ASSESS | EXECUTE | impl |

## Incidents

- Cycle 1: validation_failure (attempt 1, 1 errors)
- Cycle 3: validation_failure (attempt 1, 1 errors)
"""


class TestParseMemory:
    """Tests for _parse_memory round-trip."""

    def test_parse_basic(self) -> None:
        patterns = _parse_memory(_SAMPLE_MEMORY)
        assert len(patterns) == 3
        assert patterns[0].name == "cycle-count-distribution"
        assert patterns[0].type == "cost-calibration"
        assert patterns[0].reinforced == 2
        assert patterns[0].observed == ["12-reasoning-loop", "13-convergence"]
        assert patterns[0].status == "active"

    def test_parse_fading(self) -> None:
        patterns = _parse_memory(_SAMPLE_MEMORY)
        assert patterns[1].status == "fading"

    def test_parse_archived(self) -> None:
        patterns = _parse_memory(_SAMPLE_MEMORY)
        assert patterns[2].status == "archived"
        assert patterns[2].name == "old-pattern"

    def test_round_trip_preserves_data(self) -> None:
        patterns = _parse_memory(_SAMPLE_MEMORY)
        rendered = _render_memory(patterns)
        reparsed = _parse_memory(rendered)
        assert len(reparsed) == len(patterns)
        for orig, new in zip(patterns, reparsed):
            assert orig.name == new.name
            assert orig.type == new.type
            assert orig.observed == new.observed
            assert orig.reinforced == new.reinforced
            assert orig.status == new.status
            assert orig.description == new.description

    def test_description_with_colon_not_parsed_as_field(self) -> None:
        """Known-fields whitelist prevents description corruption."""
        content = """\
# Operational Memory

## Patterns

### test-pattern
type: debt
observed: m1
reinforced: 1
last_active: m1

Sequential: 3 phases in a row.
"""
        patterns = _parse_memory(content)
        assert len(patterns) == 1
        assert "Sequential" in patterns[0].description
        assert patterns[0].type == "debt"

    def test_empty_memory(self) -> None:
        patterns = _parse_memory("")
        assert patterns == []

    def test_empty_memory_header_only(self) -> None:
        patterns = _parse_memory("# Operational Memory\n\n## Patterns\n")
        assert patterns == []


class TestReinforceOrCreate:

    def test_create_new(self) -> None:
        patterns: list[MemoryPattern] = []
        _reinforce_or_create(patterns, "test", "debt", "m1", "desc")
        assert len(patterns) == 1
        assert patterns[0].reinforced == 1
        assert patterns[0].observed == ["m1"]

    def test_reinforce_existing(self) -> None:
        patterns = [MemoryPattern(
            name="test", type="debt", observed=["m1"],
            reinforced=1, last_active="m1",
        )]
        _reinforce_or_create(patterns, "test", "debt", "m2", "desc2")
        assert patterns[0].reinforced == 2
        assert patterns[0].observed == ["m1", "m2"]

    def test_idempotent_same_milestone(self) -> None:
        patterns = [MemoryPattern(
            name="test", type="debt", observed=["m1"],
            reinforced=1, last_active="m1",
        )]
        _reinforce_or_create(patterns, "test", "debt", "m1", "desc2")
        # Should NOT increment reinforced for same milestone.
        assert patterns[0].reinforced == 1
        assert patterns[0].observed == ["m1"]


class TestApplyDecay:

    def test_active_within_threshold(self) -> None:
        p = MemoryPattern(
            name="recent", type="debt", observed=["m8"],
            reinforced=1, last_active="8-test",
        )
        milestones = [f"{i}-ms" for i in range(1, 11)]
        _apply_decay([p], "10-ms", milestones, fading_threshold=5, archive_threshold=10)
        assert p.status == "active"

    def test_fading_at_threshold(self) -> None:
        p = MemoryPattern(
            name="old", type="debt", observed=["m3"],
            reinforced=1, last_active="3-ms",
        )
        milestones = [f"{i}-ms" for i in range(1, 11)]
        _apply_decay([p], "9-ms", milestones, fading_threshold=5, archive_threshold=10)
        assert p.status == "fading"

    def test_archived_at_threshold(self) -> None:
        p = MemoryPattern(
            name="ancient", type="debt", observed=["m1"],
            reinforced=1, last_active="1-ms",
        )
        milestones = [f"{i}-ms" for i in range(1, 15)]
        _apply_decay([p], "14-ms", milestones, fading_threshold=5, archive_threshold=10)
        assert p.status == "archived"

    def test_reinforced_3_skips_fading(self) -> None:
        """reinforced=3 at distance 7 stays active (fading requires < 3)."""
        p = MemoryPattern(
            name="medium", type="debt", observed=["m1"],
            reinforced=3, last_active="3-ms",
        )
        milestones = [f"{i}-ms" for i in range(1, 11)]
        _apply_decay([p], "10-ms", milestones, fading_threshold=5, archive_threshold=10)
        assert p.status == "active"

    def test_high_reinforced_exempt(self) -> None:
        """Patterns with reinforced >= 5 don't decay."""
        p = MemoryPattern(
            name="durable", type="debt", observed=["m1"],
            reinforced=5, last_active="1-ms",
        )
        milestones = [f"{i}-ms" for i in range(1, 20)]
        _apply_decay([p], "19-ms", milestones, fading_threshold=5, archive_threshold=10)
        assert p.status == "active"


class TestMilestoneSortKey:

    def test_numeric_prefix(self) -> None:
        names = ["10-baz", "2-bar", "1-foo", "3-qux"]
        result = sorted(names, key=_milestone_sort_key)
        assert result == ["1-foo", "2-bar", "3-qux", "10-baz"]

    def test_no_numeric_prefix(self) -> None:
        names = ["beta", "alpha"]
        result = sorted(names, key=_milestone_sort_key)
        assert result == ["alpha", "beta"]


class TestParseMetricsHeader:

    def test_basic(self) -> None:
        header = _parse_metrics_header(_SAMPLE_METRICS)
        assert header["outcome"] == "completed"
        assert header["cycles"] == "5"
        assert header["tokens_out"] == "50000"
        assert header["validation_failures"] == "2"


class TestCountMetricsSectionRows:

    def test_rework_rows(self) -> None:
        assert _count_metrics_section_rows(_SAMPLE_METRICS, "## Rework") == 1

    def test_missing_section(self) -> None:
        assert _count_metrics_section_rows(_SAMPLE_METRICS, "## Quality Gate") == 0


class TestAnalyzeCompose:
    """Tests for _analyze_compose topology extraction."""

    def test_sequential(self, tmp_path: Path) -> None:
        compose = tmp_path / "compose.py"
        compose.write_text('''\
async def phase_a() -> A:
    """Phase A."""
async def phase_b(a: A) -> B:
    """Phase B."""
async def verify():
    """Verify."""
async def execute():
    a = await phase_a()
    b = await phase_b(a)
    await verify()
''')
        count, has_gather = _analyze_compose(compose)
        assert count == 2  # verify excluded
        assert has_gather is False

    def test_parallel_gather(self, tmp_path: Path) -> None:
        compose = tmp_path / "compose.py"
        compose.write_text('''\
async def phase_a() -> A:
    """Phase A."""
async def phase_b() -> B:
    """Phase B."""
async def verify():
    """Verify."""
async def execute():
    a, b = await gather(phase_a(), phase_b())
    await verify()
''')
        count, has_gather = _analyze_compose(compose)
        assert count == 2
        assert has_gather is True

    def test_missing_file(self, tmp_path: Path) -> None:
        count, has_gather = _analyze_compose(tmp_path / "missing.py")
        assert count == 0
        assert has_gather is False

    def test_syntax_error(self, tmp_path: Path) -> None:
        compose = tmp_path / "compose.py"
        compose.write_text("def broken(:\n  pass\n")
        count, has_gather = _analyze_compose(compose)
        assert count == 0
        assert has_gather is False



    """Integration test for consolidate_milestone."""

    def _setup_milestone(self, tmp_path: Path, name: str = "14-test") -> Path:
        """Create a minimal milestone directory with metrics.md and compose.py."""
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True)
        (ms_dir / "metrics.md").write_text(_SAMPLE_METRICS)
        (ms_dir / "compose.py").write_text('''\
async def implement_feature() -> Feature:
    """Implement the feature.
    Criteria: feature works."""

async def verify():
    """Verify the feature.
    Criteria: tests pass."""

async def execute():
    feature = await implement_feature()
    await verify()
''')
        return ms_dir

    def test_creates_memory_md(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path)
        result = consolidate_milestone(tmp_path, "14-test")
        assert result is True
        memory_path = tmp_path / ".clou" / "memory.md"
        assert memory_path.exists()
        content = memory_path.read_text()
        assert "# Operational Memory" in content
        assert "cycle-count-distribution" in content

    def test_extracts_cost_calibration(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path)
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        cost = [p for p in patterns if p.type == "cost-calibration"]
        assert len(cost) == 1
        assert "5 cycles" in cost[0].description
        assert "50,000" in cost[0].description

    def test_extracts_decomposition(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path)
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        decomp = [p for p in patterns if p.type == "decomposition"]
        assert len(decomp) == 1
        assert "1 phases" in decomp[0].description  # verify excluded

    def test_extracts_rework(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path)
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        rework = [p for p in patterns if p.name == "rework-frequency"]
        assert len(rework) == 1
        assert "1 rework" in rework[0].description

    def test_extracts_validation_debt(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path)
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        debt = [p for p in patterns if p.type == "debt"]
        assert len(debt) == 1
        assert "2 validation failures" in debt[0].description

    def test_reinforces_existing_patterns(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path, "14-test")
        self._setup_milestone(tmp_path, "15-test")
        consolidate_milestone(tmp_path, "14-test")
        consolidate_milestone(tmp_path, "15-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        cost = [p for p in patterns if p.type == "cost-calibration"]
        assert len(cost) == 1
        assert cost[0].reinforced == 2
        assert "14-test" in cost[0].observed
        assert "15-test" in cost[0].observed

    def test_idempotent_consolidation(self, tmp_path: Path) -> None:
        self._setup_milestone(tmp_path)
        consolidate_milestone(tmp_path, "14-test")
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        cost = [p for p in patterns if p.type == "cost-calibration"]
        assert cost[0].reinforced == 1  # not 2

    def test_extracts_quality_gate_unavailability(self, tmp_path: Path) -> None:
        ms_dir = self._setup_milestone(tmp_path)
        # Add a Quality Gate section with unavailable tools.
        metrics = ms_dir / "metrics.md"
        content = metrics.read_text()
        content += """\

## Quality Gate

| Cycle | Tools Invoked | Tools Unavailable | Tool Count |
|-------|---------------|-------------------|------------|
| 3 | roast_codebase | roast_architecture | 5 |
"""
        metrics.write_text(content)
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        qg = [p for p in patterns if p.type == "quality-gate"]
        assert len(qg) == 1
        assert "unavailable" in qg[0].description

    def test_extracts_escalation_frequency(self, tmp_path: Path) -> None:
        ms_dir = self._setup_milestone(tmp_path)
        # Add an Escalations section.
        metrics = ms_dir / "metrics.md"
        content = metrics.read_text()
        content += """\

## Escalations

| Cycle | Classification | Severity |
|-------|----------------|----------|
| 5 | validation_failure | blocking |
"""
        metrics.write_text(content)
        consolidate_milestone(tmp_path, "14-test")
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        esc = [p for p in patterns if p.name == "escalation-frequency"]
        assert len(esc) == 1
        assert "1 escalation" in esc[0].description

    def test_no_metrics_returns_false(self, tmp_path: Path) -> None:
        ms_dir = tmp_path / ".clou" / "milestones" / "14-test"
        ms_dir.mkdir(parents=True)
        assert consolidate_milestone(tmp_path, "14-test") is False


class TestDetermineNextCycleMemory:
    """DB-18: memory.md in PLAN read set."""

    def test_plan_includes_memory_when_exists(self, tmp_path: Path) -> None:
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text("# Operational Memory\n")
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert "memory.md" in read_set

    def test_plan_excludes_memory_when_absent(self, tmp_path: Path) -> None:
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert "memory.md" not in read_set


class TestConsolidatePending:
    """DB-18: self-healing pending-consolidation sweep."""

    def _make_milestone(self, tmp_path: Path, name: str) -> None:
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True)
        (ms_dir / "metrics.md").write_text(_SAMPLE_METRICS)
        (ms_dir / "compose.py").write_text('''\
async def do_work() -> Done:
    """Work."""
async def execute():
    await do_work()
''')

    def test_bootstraps_when_no_memory(self, tmp_path: Path) -> None:
        """First run: no memory.md, 3 milestones → consolidates all 3."""
        self._make_milestone(tmp_path, "12-alpha")
        self._make_milestone(tmp_path, "13-beta")
        self._make_milestone(tmp_path, "14-gamma")
        count = consolidate_pending(tmp_path)
        assert count == 3
        memory = tmp_path / ".clou" / "memory.md"
        assert memory.exists()
        patterns = _parse_memory(memory.read_text())
        cost = [p for p in patterns if p.type == "cost-calibration"]
        assert cost[0].reinforced == 3
        assert "12-alpha" in cost[0].observed
        assert "14-gamma" in cost[0].observed

    def test_skips_already_consolidated(self, tmp_path: Path) -> None:
        """Second run: milestones already in memory.md → no-op."""
        self._make_milestone(tmp_path, "12-alpha")
        consolidate_pending(tmp_path)
        count = consolidate_pending(tmp_path)
        assert count == 0

    def test_catches_new_milestone(self, tmp_path: Path) -> None:
        """Milestone added after initial consolidation → picks it up."""
        self._make_milestone(tmp_path, "12-alpha")
        consolidate_pending(tmp_path)
        self._make_milestone(tmp_path, "13-beta")
        count = consolidate_pending(tmp_path)
        assert count == 1

    def test_no_milestones_dir(self, tmp_path: Path) -> None:
        assert consolidate_pending(tmp_path) == 0

    def test_milestones_without_metrics(self, tmp_path: Path) -> None:
        """Milestones with no metrics.md (never ran) are skipped."""
        ms_dir = tmp_path / ".clou" / "milestones" / "12-empty"
        ms_dir.mkdir(parents=True)
        assert consolidate_pending(tmp_path) == 0

    def test_chronological_order(self, tmp_path: Path) -> None:
        """Milestones consolidated in numeric order (decay correctness)."""
        self._make_milestone(tmp_path, "2-second")
        self._make_milestone(tmp_path, "1-first")
        self._make_milestone(tmp_path, "10-tenth")
        consolidate_pending(tmp_path)
        patterns = _parse_memory(
            (tmp_path / ".clou" / "memory.md").read_text()
        )
        cost = [p for p in patterns if p.type == "cost-calibration"]
        # last_active should be the last milestone consolidated (10-tenth)
        assert cost[0].last_active == "10-tenth"
        # observed should list all three in order they were consolidated
        assert cost[0].observed == ["1-first", "2-second", "10-tenth"]
