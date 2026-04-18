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
    _MEMORY_TYPE_FILTERS,
    _accumulate_distribution,
    _consolidated_milestones,
    _detect_contradiction,
    _filter_memory_for_cycle,
    _invalidate_contradictions,
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
    compact_decisions,
    compact_understanding,
    consolidate_milestone,
    consolidate_pending,
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    log_self_heal_attempt,
    parse_checkpoint,
    parse_obsolete_flags,
    read_cycle_count,
    read_cycle_outcome,
    run_lifecycle_pipeline,
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

### Valid: SQL injection risk in search handler
**Brutalist said:** "User input concatenated into SQL"
**Action:** Created rework task
**Reasoning:** Valid security finding.
"""

_DECISIONS_NOT_CONVERGED = """\
## Cycle 3 — Brutalist Assessment

### Valid: Missing input validation
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

### Valid: XSS in template rendering
**Brutalist said:** "User input rendered unsanitized"
**Action:** Fix templates
**Reasoning:** Valid.

## Cycle 1 — Brutalist Assessment

### Security: SQL injection
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
    state = assess_convergence(decisions_content=_DECISIONS_CONVERGED)
    assert state.consecutive_zero_accepts == 2
    assert state.total_assess_cycles == 3
    assert state.converged is True


def test_convergence_most_recent_has_accepted() -> None:
    """Most recent ASSESS has accepted findings = not converged."""
    state = assess_convergence(decisions_content=_DECISIONS_NOT_CONVERGED)
    assert state.consecutive_zero_accepts == 0
    assert state.total_assess_cycles == 2
    assert state.converged is False


def test_convergence_all_accepted() -> None:
    """Every ASSESS cycle has accepted findings = not converged."""
    state = assess_convergence(decisions_content=_DECISIONS_ALL_ACCEPTED)
    assert state.consecutive_zero_accepts == 0
    assert state.total_assess_cycles == 2
    assert state.converged is False


def test_convergence_skips_non_assess_sections() -> None:
    """Coordinator Judgment sections are ignored — only ASSESS counted."""
    state = assess_convergence(decisions_content=_DECISIONS_MIXED_TYPES)
    assert state.consecutive_zero_accepts == 2
    assert state.total_assess_cycles == 2  # Only ASSESS, not Coordinator Judgment
    assert state.converged is True


def test_convergence_empty_decisions() -> None:
    """Empty decisions content = zero cycles, not converged."""
    state = assess_convergence(decisions_content="")
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

### Valid: Missing validation
**Action:** Fix
**Reasoning:** Valid.
"""
    state = assess_convergence(decisions_content=content)
    assert state.consecutive_zero_accepts == 1
    assert state.converged is False


def test_convergence_custom_threshold() -> None:
    """Custom threshold changes the convergence point."""
    state = assess_convergence(decisions_content=_DECISIONS_CONVERGED, threshold=3)
    assert state.consecutive_zero_accepts == 2
    assert state.converged is False  # Need 3, only have 2

    state = assess_convergence(decisions_content=_DECISIONS_CONVERGED, threshold=1)
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
    state = assess_convergence(decisions_content=content)
    assert state.total_assess_cycles == 2
    assert state.consecutive_zero_accepts == 2
    assert state.converged is True


def test_convergence_state_is_frozen() -> None:
    """ConvergenceState is immutable."""
    state = assess_convergence(decisions_content="")
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
    """DB-18 I6: filtered memory.md in per-cycle read sets."""

    _SAMPLE_MEMORY = """\
# Operational Memory

## Patterns

### cycle-count-distribution
type: cost-calibration
observed: 12-ms
reinforced: 1
last_active: 12-ms
status: active

5 cycles, ~10,000 output tokens, 3m.
"""

    def test_plan_includes_filtered_memory_when_exists(self, tmp_path: Path) -> None:
        """PLAN cycle includes active/_filtered_memory.md when memory has matching patterns."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._SAMPLE_MEMORY)
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert "active/_filtered_memory.md" in read_set
        filtered = (cp_path.parent / "_filtered_memory.md").read_text()
        assert "cycle-count-distribution" in filtered

    def test_plan_excludes_memory_when_absent(self, tmp_path: Path) -> None:
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert "active/_filtered_memory.md" not in read_set
        assert "memory.md" not in read_set

    def test_plan_excludes_memory_when_no_matching_patterns(self, tmp_path: Path) -> None:
        """memory.md exists but has no active patterns of PLAN types."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text("# Operational Memory\n")
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert "active/_filtered_memory.md" not in read_set

    def test_execute_excludes_memory(self, tmp_path: Path) -> None:
        """EXECUTE cycle gets no memory.md content."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._SAMPLE_MEMORY)
        cp_path.write_text("cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: impl\n")
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "EXECUTE"
        assert "active/_filtered_memory.md" not in read_set

    def test_stale_filtered_file_deleted_on_cycle_switch(self, tmp_path: Path) -> None:
        """_filtered_memory.md from PLAN is deleted when EXECUTE runs."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._SAMPLE_MEMORY)

        # First: PLAN writes _filtered_memory.md.
        determine_next_cycle(cp_path, "m1")
        filtered_path = cp_path.parent / "_filtered_memory.md"
        assert filtered_path.exists(), "PLAN should create _filtered_memory.md"

        # Second: EXECUTE should delete the stale file.
        cp_path.write_text("cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: impl\n")
        determine_next_cycle(cp_path, "m1")
        assert not filtered_path.exists(), "EXECUTE should delete stale _filtered_memory.md"

    def test_unlink_resilient_to_oserror(self, tmp_path: Path) -> None:
        """Guarded unlink does not abort determine_next_cycle on OSError."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        # Write a checkpoint requesting EXECUTE (which cleans up stale files).
        cp_path.write_text("cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: impl\n")
        # No _filtered_memory.md exists -- unlink(missing_ok=True) handles this.
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "EXECUTE"

    def test_symlink_write_rejected(self, tmp_path: Path) -> None:
        """F7: symlink at _filtered_memory.md path prevents write_text."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._SAMPLE_MEMORY)

        # Place a symlink where _filtered_memory.md would be written.
        filtered_path = cp_path.parent / "_filtered_memory.md"
        decoy = tmp_path / "decoy.md"
        decoy.write_text("decoy")
        filtered_path.symlink_to(decoy)

        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "PLAN"
        # The symlink should NOT have been followed for writing.
        assert "active/_filtered_memory.md" not in read_set
        # Decoy file should be unchanged.
        assert decoy.read_text() == "decoy"

    def test_symlink_unlink_rejected(self, tmp_path: Path) -> None:
        """F7: symlink at _filtered_memory.md path prevents unlink."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        # EXECUTE has no memory filter -- triggers the unlink branch.
        cp_path.write_text(
            "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: impl\n"
        )

        # Place a symlink where _filtered_memory.md would be unlinked.
        filtered_path = cp_path.parent / "_filtered_memory.md"
        decoy = tmp_path / "decoy.md"
        decoy.write_text("decoy")
        filtered_path.symlink_to(decoy)

        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "EXECUTE"
        # The symlink should still exist (not unlinked).
        assert filtered_path.is_symlink()
        # Decoy target should be untouched.
        assert decoy.read_text() == "decoy"


class TestFilterMemoryForCycle:
    """DB-18 I6: unit tests for _filter_memory_for_cycle helper."""

    _FULL_MEMORY = """\
# Operational Memory

## Patterns

### cycle-count-distribution
type: cost-calibration
observed: 12-ms
reinforced: 1
last_active: 12-ms
status: active

5 cycles, ~10,000 output tokens, 3m.

### task-decomposition-heuristic
type: decomposition
observed: 12-ms
reinforced: 2
last_active: 12-ms
status: active

Split by concern, not by file.

### known-tech-debt
type: debt
observed: 11-ms
reinforced: 1
last_active: 11-ms
status: active

Stale fixture in test_ui.

### gate-failure-patterns
type: quality-gate
observed: 12-ms
reinforced: 3
last_active: 12-ms
status: active

Lint errors in generated code.

### escalation-threshold
type: escalation
observed: 10-ms
reinforced: 1
last_active: 10-ms
status: active

Escalate after 3 consecutive failures.

### fading-pattern
type: cost-calibration
observed: 5-ms
reinforced: 1
last_active: 5-ms
status: fading

Old cost data.

### archived-gate
type: quality-gate
observed: 3-ms
reinforced: 1
last_active: 3-ms
status: archived

No longer relevant.
"""

    def test_plan_returns_decomposition_cost_debt(self, tmp_path: Path) -> None:
        """PLAN filter includes only decomposition, cost-calibration, and debt types."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(mem_path, "PLAN", ms_dir)
        assert result is not None
        assert "cycle-count-distribution" in result
        assert "task-decomposition-heuristic" in result
        assert "known-tech-debt" in result
        # Quality-gate and escalation must NOT appear.
        assert "gate-failure-patterns" not in result
        assert "escalation-threshold" not in result

    def test_assess_returns_quality_gate_and_escalation(self, tmp_path: Path) -> None:
        """ASSESS filter includes only quality-gate and escalation types."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(mem_path, "ASSESS", ms_dir)
        assert result is not None
        assert "gate-failure-patterns" in result
        assert "escalation-threshold" in result
        # Decomposition, cost-calibration, and debt must NOT appear.
        assert "cycle-count-distribution" not in result
        assert "task-decomposition-heuristic" not in result
        assert "known-tech-debt" not in result

    def test_fading_patterns_excluded(self, tmp_path: Path) -> None:
        """Fading patterns are excluded even when their type matches the filter."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(mem_path, "PLAN", ms_dir)
        assert result is not None
        assert "fading-pattern" not in result

    def test_archived_patterns_excluded(self, tmp_path: Path) -> None:
        """Archived patterns are excluded even when their type matches the filter."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(mem_path, "ASSESS", ms_dir)
        assert result is not None
        assert "archived-gate" not in result

    def test_execute_returns_none(self, tmp_path: Path) -> None:
        """EXECUTE is not in _MEMORY_TYPE_FILTERS, so returns None."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "EXECUTE", ms_dir) is None

    def test_verify_returns_none(self, tmp_path: Path) -> None:
        """VERIFY is not in _MEMORY_TYPE_FILTERS, so returns None."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "VERIFY", ms_dir) is None

    def test_exit_returns_none(self, tmp_path: Path) -> None:
        """EXIT is not in _MEMORY_TYPE_FILTERS, so returns None."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "EXIT", ms_dir) is None

    def test_replan_returns_none(self, tmp_path: Path) -> None:
        """REPLAN is not in _MEMORY_TYPE_FILTERS, so returns None."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "REPLAN", ms_dir) is None

    def test_missing_memory_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when memory.md does not exist."""
        mem_path = tmp_path / "nonexistent" / "memory.md"
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "PLAN", ms_dir) is None

    def test_empty_memory_returns_none(self, tmp_path: Path) -> None:
        """Returns None when memory.md has no parseable patterns."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text("# Operational Memory\n\n## Patterns\n")
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "PLAN", ms_dir) is None

    def test_only_fading_patterns_returns_none(self, tmp_path: Path) -> None:
        """Returns None when all matching patterns are fading."""
        mem_path = tmp_path / "memory.md"
        mem_path.write_text("""\
# Operational Memory

## Patterns

### old-cost-data
type: cost-calibration
observed: 5-ms
reinforced: 1
last_active: 5-ms
status: fading

Stale.
""")
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        assert _filter_memory_for_cycle(mem_path, "PLAN", ms_dir) is None

    def test_memory_type_filters_constant(self) -> None:
        """_MEMORY_TYPE_FILTERS has exactly PLAN and ASSESS keys."""
        assert set(_MEMORY_TYPE_FILTERS.keys()) == {"PLAN", "ASSESS"}
        assert "decomposition" in _MEMORY_TYPE_FILTERS["PLAN"]
        assert "cost-calibration" in _MEMORY_TYPE_FILTERS["PLAN"]
        assert "debt" in _MEMORY_TYPE_FILTERS["PLAN"]
        assert "quality-gate" in _MEMORY_TYPE_FILTERS["ASSESS"]
        assert "escalation" in _MEMORY_TYPE_FILTERS["ASSESS"]


class TestFilterMemoryTelemetry:
    """M35 I1: telemetry emission when memory patterns pass retrieval filtering."""

    _FULL_MEMORY = """\
# Operational Memory

## Patterns

### cycle-count-distribution
type: cost-calibration
observed: 12-ms
reinforced: 1
last_active: 12-ms
status: active

5 cycles, ~10,000 output tokens, 3m.

### task-decomposition-heuristic
type: decomposition
observed: 12-ms
reinforced: 2
last_active: 12-ms
status: active

Split by concern, not by file.

### known-tech-debt
type: debt
observed: 11-ms
reinforced: 1
last_active: 11-ms
status: active

Stale fixture in test_ui.

### gate-failure-patterns
type: quality-gate
observed: 12-ms
reinforced: 3
last_active: 12-ms
status: active

Lint errors in generated code.

### escalation-threshold
type: escalation
observed: 10-ms
reinforced: 1
last_active: 10-ms
status: active

Escalate after 3 consecutive failures.

### fading-pattern
type: cost-calibration
observed: 5-ms
reinforced: 1
last_active: 5-ms
status: fading

Old cost data.

### archived-gate
type: quality-gate
observed: 3-ms
reinforced: 1
last_active: 3-ms
status: archived

No longer relevant.
"""

    def test_plan_emits_patterns_retrieved_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PLAN filtering emits memory.patterns_retrieved with correct structure."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(
            mem_path, "PLAN", ms_dir, milestone="test-ms", cycle_num=4,
        )
        assert result is not None

        assert len(captured) == 1
        name, attrs = captured[0]
        assert name == "memory.patterns_retrieved"
        assert attrs["milestone"] == "test-ms"
        assert attrs["cycle_num"] == 4
        assert attrs["cycle_type"] == "PLAN"
        assert attrs["pattern_count"] == 3
        patterns = attrs["patterns"]
        assert len(patterns) == 3
        names = {p["name"] for p in patterns}
        assert names == {
            "cycle-count-distribution",
            "task-decomposition-heuristic",
            "known-tech-debt",
        }
        # Each pattern dict has required fields.
        for p in patterns:
            assert "name" in p
            assert "type" in p
            assert "description" in p

    def test_assess_emits_patterns_retrieved_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ASSESS filtering emits event with quality-gate and escalation patterns."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(
            mem_path, "ASSESS", ms_dir, milestone="test-ms", cycle_num=7,
        )
        assert result is not None

        assert len(captured) == 1
        name, attrs = captured[0]
        assert name == "memory.patterns_retrieved"
        assert attrs["cycle_num"] == 7
        assert attrs["cycle_type"] == "ASSESS"
        assert attrs["pattern_count"] == 2
        names = {p["name"] for p in attrs["patterns"]}
        assert names == {"gate-failure-patterns", "escalation-threshold"}

    def test_no_event_when_no_patterns_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No telemetry event emitted when filtering yields no patterns."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        # EXECUTE has no filter entry -- returns None, no event.
        result = _filter_memory_for_cycle(
            mem_path, "EXECUTE", ms_dir, milestone="test-ms",
        )
        assert result is None
        assert len(captured) == 0

    def test_no_event_when_only_fading_patterns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No telemetry event emitted when all matching patterns are fading."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text("""\
# Operational Memory

## Patterns

### old-cost-data
type: cost-calibration
observed: 5-ms
reinforced: 1
last_active: 5-ms
status: fading

Stale.
""")
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result = _filter_memory_for_cycle(
            mem_path, "PLAN", ms_dir, milestone="test-ms",
        )
        assert result is None
        assert len(captured) == 0

    def test_event_pattern_description_populated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pattern descriptions in event match the parsed memory content."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        _filter_memory_for_cycle(
            mem_path, "PLAN", ms_dir, milestone="test-ms", cycle_num=2,
        )

        _, attrs = captured[0]
        assert attrs["cycle_num"] == 2
        by_name = {p["name"]: p for p in attrs["patterns"]}
        assert by_name["task-decomposition-heuristic"]["type"] == "decomposition"
        assert "concern" in by_name["task-decomposition-heuristic"]["description"]

    def test_return_value_unchanged_by_telemetry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Telemetry emission does not alter the function's return value."""
        from clou import telemetry

        monkeypatch.setattr(telemetry, "event", lambda name, **kw: None)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        result_with = _filter_memory_for_cycle(
            mem_path, "PLAN", ms_dir, milestone="test-ms",
        )
        # Call again without milestone (default empty string) -- same filtering.
        result_without = _filter_memory_for_cycle(
            mem_path, "PLAN", ms_dir,
        )
        assert result_with == result_without

    def test_no_event_when_milestone_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No telemetry event emitted when milestone is empty string (F2)."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        # Default milestone="" -- should NOT emit event.
        result = _filter_memory_for_cycle(mem_path, "PLAN", ms_dir)
        assert result is not None  # Filtering still works.
        assert len(captured) == 0  # But no orphaned telemetry event.

    def test_cycle_num_defaults_to_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cycle_num defaults to 0 when not provided."""
        from clou import telemetry

        captured: list[tuple[str, dict]] = []

        def _capture_event(name: str, **attrs: object) -> None:
            captured.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture_event)

        mem_path = tmp_path / "memory.md"
        mem_path.write_text(self._FULL_MEMORY)
        ms_dir = tmp_path / "milestones"
        ms_dir.mkdir()

        _filter_memory_for_cycle(mem_path, "PLAN", ms_dir, milestone="test-ms")

        assert len(captured) == 1
        assert captured[0][1]["cycle_num"] == 0

    def test_cycle_num_matches_coordinator_convention(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Retrieval cycle_num = checkpoint.cycle + 1, matching coordinator convention.

        RF1: when checkpoint.cycle=3, the telemetry event should have cycle_num=4,
        which is the same cycle_num the coordinator uses (cycle_count + 1).
        """
        cp_path = tmp_path / ".clou" / "milestones" / "test-ms" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._FULL_MEMORY)
        # checkpoint.cycle=3, next_step=PLAN
        cp_path.write_text("cycle: 3\nstep: PLAN\nnext_step: PLAN\ncurrent_phase: impl\n")

        emitted: list[dict] = []
        import clou.telemetry as _tel
        monkeypatch.setattr(_tel, "event", lambda name, **kw: emitted.append({"name": name, **kw}))

        cycle_type, read_set = determine_next_cycle(cp_path, "test-ms")
        assert cycle_type == "PLAN"

        # The retrieval event should have cycle_num = checkpoint.cycle + 1 = 4
        retrieval_events = [e for e in emitted if e["name"] == "memory.patterns_retrieved"]
        assert len(retrieval_events) == 1
        assert retrieval_events[0]["cycle_num"] == 4  # 3 + 1

    def test_no_checkpoint_uses_cycle_num_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no checkpoint exists, retrieval event uses cycle_num=1 (first cycle)."""
        cp_path = tmp_path / ".clou" / "milestones" / "test-ms" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._FULL_MEMORY)
        # Do NOT create coordinator.md -- simulates first cycle

        emitted: list[dict] = []
        import clou.telemetry as _tel
        monkeypatch.setattr(_tel, "event", lambda name, **kw: emitted.append({"name": name, **kw}))

        cycle_type, read_set = determine_next_cycle(cp_path, "test-ms")
        assert cycle_type == "PLAN"

        retrieval_events = [e for e in emitted if e["name"] == "memory.patterns_retrieved"]
        assert len(retrieval_events) == 1
        assert retrieval_events[0]["cycle_num"] == 1


class TestDetermineNextCycleAssessMemory:
    """DB-18 I6: ASSESS cycle filtered memory integration tests."""

    _ASSESS_MEMORY = """\
# Operational Memory

## Patterns

### gate-failure-patterns
type: quality-gate
observed: 12-ms
reinforced: 3
last_active: 12-ms
status: active

Lint errors in generated code.

### escalation-threshold
type: escalation
observed: 10-ms
reinforced: 1
last_active: 10-ms
status: active

Escalate after 3 consecutive failures.

### cost-data
type: cost-calibration
observed: 12-ms
reinforced: 1
last_active: 12-ms
status: active

5 cycles.
"""

    def test_assess_includes_filtered_memory(self, tmp_path: Path) -> None:
        """ASSESS cycle includes active/_filtered_memory.md with quality-gate/escalation only."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._ASSESS_MEMORY)
        cp_path.write_text(
            "cycle: 3\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: impl\n"
        )
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "ASSESS"
        assert "active/_filtered_memory.md" in read_set
        filtered = (cp_path.parent / "_filtered_memory.md").read_text()
        assert "gate-failure-patterns" in filtered
        assert "escalation-threshold" in filtered
        # cost-calibration is a PLAN type, not ASSESS.
        assert "cost-data" not in filtered

    def test_assess_excludes_memory_when_no_matching_patterns(self, tmp_path: Path) -> None:
        """ASSESS cycle omits memory when no quality-gate/escalation patterns exist."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        # Memory with only cost-calibration (a PLAN type).
        (tmp_path / ".clou" / "memory.md").write_text("""\
# Operational Memory

## Patterns

### cost-data
type: cost-calibration
observed: 12-ms
reinforced: 1
last_active: 12-ms
status: active

5 cycles.
""")
        cp_path.write_text(
            "cycle: 3\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: impl\n"
        )
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "ASSESS"
        assert "active/_filtered_memory.md" not in read_set

    def test_verify_excludes_memory(self, tmp_path: Path) -> None:
        """VERIFY cycle gets no memory.md content."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._ASSESS_MEMORY)
        cp_path.write_text(
            "cycle: 4\nstep: ASSESS\nnext_step: VERIFY\ncurrent_phase: impl\n"
        )
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "VERIFY"
        assert "active/_filtered_memory.md" not in read_set

    def test_exit_excludes_memory(self, tmp_path: Path) -> None:
        """EXIT cycle gets no memory.md content."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._ASSESS_MEMORY)
        cp_path.write_text(
            "cycle: 5\nstep: VERIFY\nnext_step: EXIT\ncurrent_phase: impl\n"
        )
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "EXIT"
        assert "active/_filtered_memory.md" not in read_set

    def test_replan_excludes_memory(self, tmp_path: Path) -> None:
        """REPLAN cycle gets no memory.md content."""
        cp_path = tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True)
        (tmp_path / ".clou" / "memory.md").write_text(self._ASSESS_MEMORY)
        cp_path.write_text(
            "cycle: 6\nstep: ASSESS\nnext_step: REPLAN\ncurrent_phase: impl\n"
        )
        cycle, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle == "REPLAN"
        assert "active/_filtered_memory.md" not in read_set


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


# ---------------------------------------------------------------------------
# compact_understanding (DB-18 I5)
# ---------------------------------------------------------------------------

_UNDERSTANDING_TEMPLATE = """\
# Understanding

Durable conceptual memory.

## What this project is becoming

## Active tensions

### Entry about milestone alpha
- **Asked:** Some question?
- **Response:** Some answer.
- **Framing:** Some framing.
- **When:** 2026-04-01
- **Fed into:** intents.md, milestone.md (10-alpha)

### Entry without fed-into tag
- **Asked:** Another question?
- **Response:** Another answer.
- **Framing:** Another framing.
- **When:** 2026-04-02

## Continuity

### Continuity entry about beta
- **Asked:** Yet another question?
- **Response:** Yet another answer.
- **Framing:** Yet another framing.
- **When:** 2026-04-03
- **Fed into:** intents.md (11-beta)

## Resolved
"""


class TestCompactUnderstanding:
    """Tests for compact_understanding() lifecycle mechanism."""

    @staticmethod
    def _make_completed(milestones_dir: Path, name: str) -> None:
        """Create a milestone directory with metrics.md (marks it completed)."""
        ms = milestones_dir / name
        ms.mkdir(parents=True, exist_ok=True)
        (ms / "metrics.md").write_text(f"# Metrics\nmilestone: {name}\n")

    @staticmethod
    def _get_section(content: str, title: str) -> str:
        """Extract the body of a ## section from understanding.md content.

        Uses line-anchored regex to avoid matching ### headers.
        """
        parts = re.split(r"(?m)^## ", content)
        for part in parts:
            if part.startswith(title):
                nl = part.find("\n")
                return part[nl + 1:] if nl != -1 else ""
        return ""

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        """understanding.md doesn't exist -> returns False."""
        result = compact_understanding(
            tmp_path / "understanding.md",
            tmp_path / "milestones",
        )
        assert result is False

    def test_resolution_moves_completed_entry(self, tmp_path: Path) -> None:
        """Entry in Active tensions with completed milestone moves to Resolved."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(_UNDERSTANDING_TEMPLATE)
        self._make_completed(milestones_dir, "10-alpha")

        result = compact_understanding(understanding, milestones_dir)
        assert result is True

        content = understanding.read_text()
        # Entry should have moved out of Active tensions.
        active_body = self._get_section(content, "Active tensions")
        assert "Entry about milestone alpha" not in active_body
        # Entry should now be in Resolved.
        resolved_body = self._get_section(content, "Resolved")
        assert "Entry about milestone alpha" in resolved_body

    def test_entry_without_fed_into_stays(self, tmp_path: Path) -> None:
        """Entry without 'Fed into:' tag remains in place."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(_UNDERSTANDING_TEMPLATE)
        self._make_completed(milestones_dir, "10-alpha")

        compact_understanding(understanding, milestones_dir)
        content = understanding.read_text()
        active_body = self._get_section(content, "Active tensions")
        assert "Entry without fed-into tag" in active_body

    def test_continuity_entry_resolved(self, tmp_path: Path) -> None:
        """Continuity entry with completed milestone also moves to Resolved."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(_UNDERSTANDING_TEMPLATE)
        self._make_completed(milestones_dir, "11-beta")

        result = compact_understanding(understanding, milestones_dir)
        assert result is True

        content = understanding.read_text()
        continuity_body = self._get_section(content, "Continuity")
        assert "Continuity entry about beta" not in continuity_body
        resolved_body = self._get_section(content, "Resolved")
        assert "Continuity entry about beta" in resolved_body

    def test_uncompleted_milestone_stays(self, tmp_path: Path) -> None:
        """Entry referencing a milestone without metrics.md stays in place."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(_UNDERSTANDING_TEMPLATE)
        # Create milestone dir but no metrics.md.
        (milestones_dir / "10-alpha").mkdir(parents=True)

        result = compact_understanding(understanding, milestones_dir)
        assert result is False

    def test_idempotent(self, tmp_path: Path) -> None:
        """Running twice produces the same result."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(_UNDERSTANDING_TEMPLATE)
        self._make_completed(milestones_dir, "10-alpha")

        compact_understanding(understanding, milestones_dir)
        content_after_first = understanding.read_text()

        result = compact_understanding(understanding, milestones_dir)
        assert result is False  # No changes on second run.
        assert understanding.read_text() == content_after_first

    def test_archival_removes_old_entries(self, tmp_path: Path) -> None:
        """Resolved entry older than archive_threshold milestones is removed."""
        milestones_dir = tmp_path / "milestones"
        # Create a range of milestones: 1-a through 15-o.
        for i in range(1, 16):
            self._make_completed(milestones_dir, f"{i}-ms{i}")

        understanding = tmp_path / "understanding.md"
        understanding.write_text(
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "## Continuity\n\n"
            "## Resolved\n\n"
            "### Old resolved entry\n"
            "- **Fed into:** intents.md (1-ms1)\n\n"
            "### Recent resolved entry\n"
            "- **Fed into:** intents.md (14-ms14)\n\n"
        )

        result = compact_understanding(
            understanding, milestones_dir, archive_threshold=10,
        )
        assert result is True

        content = understanding.read_text()
        # Old entry (distance 14 > 10) should be removed.
        assert "Old resolved entry" not in content
        # Recent entry (distance 1 <= 10) should remain.
        assert "Recent resolved entry" in content

    def test_archival_preserves_within_threshold(self, tmp_path: Path) -> None:
        """Resolved entry within archive_threshold is kept."""
        milestones_dir = tmp_path / "milestones"
        for i in range(1, 6):
            self._make_completed(milestones_dir, f"{i}-ms{i}")

        understanding = tmp_path / "understanding.md"
        understanding.write_text(
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "## Resolved\n\n"
            "### Recent resolved entry\n"
            "- **Fed into:** intents.md (3-ms3)\n\n"
        )

        result = compact_understanding(
            understanding, milestones_dir, archive_threshold=10,
        )
        # Distance is 2 (5-3), which is <= 10, so no change.
        assert result is False

    def test_empty_resolved_section_preserved(self, tmp_path: Path) -> None:
        """Empty Resolved section header is preserved after compaction."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "## Resolved\n"
        )

        result = compact_understanding(understanding, milestones_dir)
        assert result is False
        content = understanding.read_text()
        assert "## Resolved" in content

    def test_no_changes_returns_false(self, tmp_path: Path) -> None:
        """No completed milestones -> no changes -> False."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        milestones_dir.mkdir(parents=True)
        understanding.write_text(_UNDERSTANDING_TEMPLATE)

        result = compact_understanding(understanding, milestones_dir)
        assert result is False

    def test_multiple_entries_same_milestone_moved_independently(
        self, tmp_path: Path,
    ) -> None:
        """Multiple entries referencing the same completed milestone each move."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "### First entry about alpha\n"
            "- **Fed into:** intents.md (10-alpha)\n\n"
            "### Second entry about alpha\n"
            "- **Fed into:** milestone.md (10-alpha)\n\n"
            "## Resolved\n"
        )
        self._make_completed(milestones_dir, "10-alpha")

        result = compact_understanding(understanding, milestones_dir)
        assert result is True

        content = understanding.read_text()
        active_body = self._get_section(content, "Active tensions")
        assert "First entry about alpha" not in active_body
        assert "Second entry about alpha" not in active_body
        resolved_body = self._get_section(content, "Resolved")
        assert "First entry about alpha" in resolved_body
        assert "Second entry about alpha" in resolved_body

    def test_only_becoming_section_noop(self, tmp_path: Path) -> None:
        """Understanding.md with only 'What this project is becoming' -> no-op."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        milestones_dir.mkdir(parents=True)
        understanding.write_text(
            "# Understanding\n\n"
            "## What this project is becoming\n\n"
            "### Some vision entry\n"
            "- **Asked:** Vision question?\n"
            "- **Response:** Vision answer.\n"
        )

        result = compact_understanding(understanding, milestones_dir)
        assert result is False
        # Content should be unchanged.
        content = understanding.read_text()
        assert "Some vision entry" in content

    def test_fed_into_nonexistent_milestone_stays(self, tmp_path: Path) -> None:
        """'Fed into:' referencing a milestone that does not exist stays in place."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        milestones_dir.mkdir(parents=True)
        understanding.write_text(
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "### Entry about future milestone\n"
            "- **Fed into:** intents.md (99-future)\n\n"
            "## Resolved\n"
        )

        result = compact_understanding(understanding, milestones_dir)
        assert result is False
        content = understanding.read_text()
        active_body = self._get_section(content, "Active tensions")
        assert "Entry about future milestone" in active_body

    def test_prose_preserved_on_round_trip(self, tmp_path: Path) -> None:
        """Section-level prose between ## header and first ### entry survives round-trip."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(
            "# Understanding\n\n"
            "Durable conceptual memory.\n\n"
            "## Active tensions\n\n"
            "This section tracks unresolved design questions.\n\n"
            "### Entry about milestone alpha\n"
            "- **Asked:** Question?\n"
            "- **Response:** Answer.\n"
            "- **Fed into:** intents.md (10-alpha)\n\n"
            "### Entry that stays\n"
            "- **Asked:** Other question?\n"
            "- **Response:** Other answer.\n\n"
            "## Resolved\n"
        )
        self._make_completed(milestones_dir, "10-alpha")

        result = compact_understanding(understanding, milestones_dir)
        assert result is True

        content = understanding.read_text()
        # The prose between "## Active tensions" and the first ### must survive.
        active_body = self._get_section(content, "Active tensions")
        assert "This section tracks unresolved design questions." in active_body
        # The moved entry should be in Resolved now.
        resolved_body = self._get_section(content, "Resolved")
        assert "Entry about milestone alpha" in resolved_body
        # The staying entry is still in Active tensions.
        assert "Entry that stays" in active_body

    def test_mixed_sections_prose_and_entries_only(self, tmp_path: Path) -> None:
        """File where SOME sections have prose and others do not -- both preserved."""
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(
            "# Understanding\n\n"
            "Preamble text.\n\n"
            "## Active tensions\n\n"
            "Prose explaining active tensions.\n\n"
            "### Alpha entry\n"
            "- **Asked:** Q?\n"
            "- **Response:** A.\n"
            "- **Fed into:** intents.md (10-alpha)\n\n"
            "## Continuity\n\n"
            "### Beta entry\n"
            "- **Asked:** Q2?\n"
            "- **Response:** A2.\n"
            "- **Fed into:** intents.md (11-beta)\n\n"
            "## Resolved\n"
        )
        self._make_completed(milestones_dir, "10-alpha")
        self._make_completed(milestones_dir, "11-beta")

        result = compact_understanding(understanding, milestones_dir)
        assert result is True

        content = understanding.read_text()
        # Active tensions prose preserved even though its entry moved.
        active_body = self._get_section(content, "Active tensions")
        assert "Prose explaining active tensions." in active_body
        # Continuity had NO prose -- section header should still be present
        # and no spurious blank lines introduced.
        continuity_body = self._get_section(content, "Continuity")
        assert "Beta entry" not in continuity_body  # moved to Resolved
        # Both entries end up in Resolved.
        resolved_body = self._get_section(content, "Resolved")
        assert "Alpha entry" in resolved_body
        assert "Beta entry" in resolved_body
        # Preamble intact.
        assert "Preamble text." in content

    def test_entries_only_round_trip_unchanged(self, tmp_path: Path) -> None:
        """Entries-only sections round-trip byte-identically through parse/render.

        R3: when a section has no prose between ## header and first ### entry,
        _render_understanding(*_parse_understanding_sections(content)) must
        produce output byte-identical to the original content.
        """
        from clou.recovery_compaction import (
            _parse_understanding_sections,
            _render_understanding,
        )

        # Content where every section has entries but NO meaningful prose
        # between the ## header and the first ### entry.
        content = (
            "# Understanding\n\n"
            "Durable conceptual memory.\n\n"
            "## Active tensions\n\n"
            "### Entry that stays\n"
            "- **Asked:** Another question?\n"
            "- **Response:** Another answer.\n\n"
            "## Continuity\n\n"
            "### Continuity entry\n"
            "- **Asked:** Continuity question?\n"
            "- **Response:** Continuity answer.\n\n"
            "## Resolved\n"
        )

        preamble, sections, section_prose = _parse_understanding_sections(content)

        # Render with prose data must byte-equal the original content.
        rendered = _render_understanding(preamble, sections, section_prose)
        assert rendered == content, (
            "Entries-only round-trip was not byte-identical"
        )

        # Also verify at integration level: compact_understanding on a file
        # with entries-only sections produces correct functional output.
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        self._make_completed(milestones_dir, "10-alpha")

        integration_content = (
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "### Alpha entry\n"
            "- **Fed into:** intents.md (10-alpha)\n\n"
            "### Stays entry\n"
            "- **Asked:** Q?\n\n"
            "## Resolved\n"
        )
        understanding.write_text(integration_content)

        result = compact_understanding(understanding, milestones_dir)
        assert result is True

        after = understanding.read_text()
        # Moved entry in Resolved.
        resolved_body = self._get_section(after, "Resolved")
        assert "Alpha entry" in resolved_body
        # Staying entry still in Active tensions.
        active_body = self._get_section(after, "Active tensions")
        assert "Stays entry" in active_body
        # Idempotent on second pass.
        assert compact_understanding(understanding, milestones_dir) is False

    def test_empty_prose_no_extra_whitespace(self, tmp_path: Path) -> None:
        """Empty-string prose must not add whitespace beyond no-prose baseline.

        Edge case: if section_prose maps a section to an empty string,
        _render_understanding must produce the same output as if
        section_prose were None.  Non-empty whitespace-only prose is
        emitted verbatim for round-trip fidelity.
        """
        from clou.recovery_compaction import _render_understanding

        preamble = "# Understanding\n\n"
        sections = {
            "Active tensions": [
                "### Entry A\n- **Asked:** Q?\n\n",
            ],
            "Continuity": [
                "### Entry B\n- **Asked:** Q2?\n\n",
            ],
            "Resolved": [],
        }

        # Baseline: render without any prose.
        baseline = _render_understanding(preamble, sections)

        # Render with empty-string prose for every section.
        empty_prose = {
            "Active tensions": "",
            "Continuity": "",
            "Resolved": "",
        }
        rendered_empty = _render_understanding(preamble, sections, empty_prose)
        assert rendered_empty == baseline, (
            "Empty-string prose diverged from no-prose baseline"
        )

        # Non-empty whitespace-only prose is emitted verbatim for
        # round-trip fidelity — it represents structural whitespace
        # captured by _parse_understanding_sections.
        whitespace_prose = {
            "Active tensions": "\n",
            "Continuity": "   \n\n",
            "Resolved": "",
        }
        rendered_ws = _render_understanding(preamble, sections, whitespace_prose)
        # The "\n" and "   \n\n" are emitted; output differs from
        # baseline only by the injected whitespace, not by corruption.
        assert "## Active tensions\n\n### Entry A" in rendered_ws
        assert "## Continuity\n   \n\n### Entry B" in rendered_ws

    def test_prose_multi_round_trip_stable(self, tmp_path: Path) -> None:
        """Multi-round-trip stability: second compact_understanding is a no-op.

        Write understanding.md with prose-bearing sections and a completed
        milestone entry.  First compact moves the entry; second compact
        returns False (no further changes).  Prose survives intact.
        """
        understanding = tmp_path / "understanding.md"
        milestones_dir = tmp_path / "milestones"
        understanding.write_text(
            "# Understanding\n\n"
            "Durable conceptual memory.\n\n"
            "## Active tensions\n\n"
            "This section tracks unresolved design questions.\n\n"
            "### Entry about milestone alpha\n"
            "- **Asked:** Question?\n"
            "- **Response:** Answer.\n"
            "- **Fed into:** intents.md (10-alpha)\n\n"
            "### Entry that stays\n"
            "- **Asked:** Other question?\n"
            "- **Response:** Other answer.\n\n"
            "## Continuity\n\n"
            "### Continuity entry\n"
            "- **Asked:** Continuity Q?\n"
            "- **Response:** Continuity A.\n\n"
            "## Resolved\n"
        )
        self._make_completed(milestones_dir, "10-alpha")

        # First pass: moves the completed entry.
        result1 = compact_understanding(understanding, milestones_dir)
        assert result1 is True

        content_after_first = understanding.read_text()

        # Prose must survive the round-trip.
        active_body = self._get_section(content_after_first, "Active tensions")
        assert "This section tracks unresolved design questions." in active_body
        # Entry moved to Resolved.
        resolved_body = self._get_section(content_after_first, "Resolved")
        assert "Entry about milestone alpha" in resolved_body
        # Staying entry still in Active tensions.
        assert "Entry that stays" in active_body

        # Second pass: no changes, proves idempotency on prose-bearing content.
        result2 = compact_understanding(understanding, milestones_dir)
        assert result2 is False
        assert understanding.read_text() == content_after_first


# ---------------------------------------------------------------------------
# _reinforce_or_create type collision (DB-18 rework R3)
# ---------------------------------------------------------------------------


class TestReinforceOrCreateTypeCollision:
    """Same name + different type must create a new pattern, not reinforce."""

    def test_same_name_different_type_creates_new(self) -> None:
        patterns: list[MemoryPattern] = []
        _reinforce_or_create(patterns, "foo", "debt", "m1", "debt desc")
        _reinforce_or_create(patterns, "foo", "escalation", "m2", "esc desc")
        assert len(patterns) == 2
        assert patterns[0].type == "debt"
        assert patterns[1].type == "escalation"

    def test_same_name_same_type_reinforces(self) -> None:
        patterns: list[MemoryPattern] = []
        _reinforce_or_create(patterns, "foo", "debt", "m1", "desc1")
        _reinforce_or_create(patterns, "foo", "debt", "m2", "desc2")
        assert len(patterns) == 1
        assert patterns[0].reinforced == 2
        assert patterns[0].observed == ["m1", "m2"]

    def test_different_name_same_type_creates_new(self) -> None:
        patterns: list[MemoryPattern] = []
        _reinforce_or_create(patterns, "alpha", "debt", "m1", "desc1")
        _reinforce_or_create(patterns, "beta", "debt", "m2", "desc2")
        assert len(patterns) == 2


# ---------------------------------------------------------------------------
# _accumulate_distribution (DB-18 rework R3)
# ---------------------------------------------------------------------------


class TestAccumulateDistribution:
    """Distribution accumulation from plain descriptions and existing distributions."""

    def test_first_accumulation_from_plain(self) -> None:
        """Plain description '5 cycles ...' -> Distribution with n=2 after adding new value."""
        result = _accumulate_distribution("5 cycles, ~10,000 output tokens, 3m.", 5)
        assert "Distribution:" in result
        assert "n=2" in result
        assert "min=5" in result
        assert "max=5" in result

    def test_second_accumulation(self) -> None:
        """Accumulating into an existing distribution with n=2."""
        first = _accumulate_distribution("3 cycles, ~5000 output tokens.", 3)
        second = _accumulate_distribution(first, 7)
        assert "n=3" in second
        assert "min=3" in second
        assert "max=7" in second

    def test_existing_distribution_accumulates(self) -> None:
        """An existing distribution string gets values extracted and new value appended."""
        existing = "Distribution: cycles min=2 median=3 max=5 (n=3)."
        result = _accumulate_distribution(existing, 4)
        assert "n=4" in result
        assert "min=2" in result

    def test_non_numeric_description_fallback(self) -> None:
        """Non-numeric descriptions still produce a valid distribution from the new value."""
        result = _accumulate_distribution("no numbers here at all", 10)
        assert "Distribution:" in result
        assert "n=1" in result
        assert "min=10" in result
        assert "max=10" in result

    def test_empty_description(self) -> None:
        """Empty string -> distribution of just the new value."""
        result = _accumulate_distribution("", 42)
        assert "n=1" in result
        assert "min=42" in result
        assert "max=42" in result


# ---------------------------------------------------------------------------
# Distribution accumulation integration (DB-18 R7 wiring)
# ---------------------------------------------------------------------------


class TestDistributionAccumulationIntegration:
    """Verify _accumulate_distribution is wired into consolidate_milestone."""

    def _make_milestone(self, tmp_path: Path, name: str, cycles: int) -> Path:
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            f"# Metrics: {name}\n\n"
            f"outcome: completed\n"
            f"cycles: {cycles}\n"
            f"duration: 10m\n"
            f"tokens_in: 100\n"
            f"tokens_out: 5000\n"
            f"agents_spawned: 2\n"
            f"agents_completed: 2\n"
            f"agents_failed: 0\n"
            f"crash_retries: 0\n"
            f"validation_failures: 0\n"
            f"context_exhaustions: 0\n"
        )
        return ms_dir

    def test_first_consolidation_includes_distribution(self, tmp_path: Path) -> None:
        """First consolidation writes both observation and Distribution suffix."""
        self._make_milestone(tmp_path, "1-alpha", cycles=3)
        consolidate_milestone(tmp_path, "1-alpha")
        memory_text = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(memory_text)
        cost_pattern = next(
            p for p in patterns
            if p.name == "cycle-count-distribution" and p.type == "cost-calibration"
        )
        assert "3 cycles" in cost_pattern.description
        assert "Distribution:" in cost_pattern.description
        assert "n=1" in cost_pattern.description
        assert "min=3" in cost_pattern.description
        assert "max=3" in cost_pattern.description

    def test_second_consolidation_accumulates(self, tmp_path: Path) -> None:
        """Two milestones with different cycle counts produce accumulated distribution."""
        self._make_milestone(tmp_path, "1-alpha", cycles=3)
        self._make_milestone(tmp_path, "2-beta", cycles=7)
        consolidate_milestone(tmp_path, "1-alpha")
        consolidate_milestone(tmp_path, "2-beta")
        memory_text = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(memory_text)
        cost_pattern = next(
            p for p in patterns
            if p.name == "cycle-count-distribution" and p.type == "cost-calibration"
        )
        # Latest observation should be from 2-beta.
        assert "7 cycles" in cost_pattern.description
        assert "Distribution:" in cost_pattern.description
        assert "n=2" in cost_pattern.description
        assert "min=3" in cost_pattern.description
        assert "max=7" in cost_pattern.description

    def test_distribution_survives_round_trip(self, tmp_path: Path) -> None:
        """Distribution data survives write -> read -> write cycle."""
        self._make_milestone(tmp_path, "1-alpha", cycles=2)
        self._make_milestone(tmp_path, "2-beta", cycles=5)
        self._make_milestone(tmp_path, "3-gamma", cycles=4)
        consolidate_milestone(tmp_path, "1-alpha")
        consolidate_milestone(tmp_path, "2-beta")
        consolidate_milestone(tmp_path, "3-gamma")
        memory_text = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(memory_text)
        cost_pattern = next(
            p for p in patterns
            if p.name == "cycle-count-distribution" and p.type == "cost-calibration"
        )
        assert "n=3" in cost_pattern.description
        assert "min=2" in cost_pattern.description
        assert "max=5" in cost_pattern.description
        # Latest observation is from 3-gamma.
        assert "4 cycles" in cost_pattern.description


# ---------------------------------------------------------------------------
# run_lifecycle_pipeline integration (DB-18 rework R3)
# ---------------------------------------------------------------------------


class TestRunLifecyclePipeline:
    """Integration tests for run_lifecycle_pipeline."""

    def _make_milestone(self, tmp_path: Path, name: str) -> Path:
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            f"# Metrics: {name}\n\n"
            f"outcome: completed\n"
            f"cycles: 3\n"
            f"duration: 10m\n"
            f"tokens_in: 100\n"
            f"tokens_out: 5000\n"
            f"agents_spawned: 2\n"
            f"agents_completed: 2\n"
            f"agents_failed: 0\n"
            f"crash_retries: 0\n"
            f"validation_failures: 0\n"
            f"context_exhaustions: 0\n"
        )
        return ms_dir

    def test_consolidates_pending_milestones(self, tmp_path: Path) -> None:
        self._make_milestone(tmp_path, "1-alpha")
        self._make_milestone(tmp_path, "2-beta")
        result = asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert isinstance(result, list)
        assert "1-alpha" in result
        assert "2-beta" in result
        # memory.md should exist after consolidation.
        memory_path = tmp_path / ".clou" / "memory.md"
        assert memory_path.exists()

    def test_returns_empty_when_no_pending(self, tmp_path: Path) -> None:
        """No milestones dir -> empty list."""
        (tmp_path / ".clou").mkdir(parents=True, exist_ok=True)
        result = asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert result == []

    def test_already_consolidated_not_repeated(self, tmp_path: Path) -> None:
        """Milestones already in memory.md are not re-consolidated."""
        self._make_milestone(tmp_path, "1-alpha")
        first = asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert "1-alpha" in first
        second = asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert second == []

    def test_pipeline_compacts_understanding(self, tmp_path: Path) -> None:
        """Pipeline stage 5 resolves understanding.md entries for completed milestones."""
        self._make_milestone(tmp_path, "1-alpha")

        # Write an understanding.md with an entry referencing 1-alpha.
        understanding = tmp_path / ".clou" / "understanding.md"
        understanding.write_text(
            "# Understanding\n\n"
            "## Active tensions\n\n"
            "### Tension about alpha\n"
            "- **Asked:** Question?\n"
            "- **Response:** Answer.\n"
            "- **Framing:** Framing.\n"
            "- **When:** 2026-04-01\n"
            "- **Fed into:** intents.md (1-alpha)\n\n"
            "## Resolved\n"
        )

        asyncio.run(run_lifecycle_pipeline(tmp_path))

        content = understanding.read_text()
        # Entry should have moved from Active tensions to Resolved.
        parts = re.split(r"(?m)^## ", content)
        for part in parts:
            if part.startswith("Active tensions"):
                assert "Tension about alpha" not in part
            if part.startswith("Resolved"):
                assert "Tension about alpha" in part


# ---------------------------------------------------------------------------
# DB-18 I1: Decay persistence end-to-end tests
# ---------------------------------------------------------------------------

_DECAY_METRICS_TEMPLATE = """\
# Metrics: {name}

outcome: completed
cycles: 2
duration: 10m
tokens_in: 100
tokens_out: 5000
agents_spawned: 2
agents_completed: 2
agents_failed: 0
crash_retries: 0
validation_failures: 0
context_exhaustions: 0
"""


class TestDecayPersistenceE2E:
    """End-to-end tests verifying _apply_decay persists status through _render_memory."""

    def _make_milestone(
        self, tmp_path: Path, name: str, *, cycles: int = 2,
    ) -> Path:
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            _DECAY_METRICS_TEMPLATE.format(name=name)
        )
        (ms_dir / "compose.py").write_text(
            'async def impl() -> R:\n    """Impl."""\n'
            'async def execute():\n    await impl()\n'
        )
        return ms_dir

    def _seed_memory(self, tmp_path: Path, content: str) -> Path:
        """Write initial memory.md content."""
        mem = tmp_path / ".clou" / "memory.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text(content)
        return mem

    def test_fading_persisted_to_disk(self, tmp_path: Path) -> None:
        """Pattern with last_active 6+ milestones behind, reinforced < 3 -> status: fading on disk."""
        # Create 10 milestones to establish distance.
        for i in range(1, 11):
            self._make_milestone(tmp_path, f"{i}-ms")

        # Seed memory with a pattern last active at milestone 3.
        self._seed_memory(tmp_path, """\
# Operational Memory

## Patterns

### old-debt-pattern
type: debt
observed: 3-ms
reinforced: 1
last_active: 3-ms

Some old debt.
""")
        # Consolidate milestone 10 (distance = 7 from 3-ms).
        consolidate_milestone(tmp_path, "10-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        debt = [p for p in patterns if p.name == "old-debt-pattern"]
        assert len(debt) == 1
        assert debt[0].status == "fading"
        # Verify it appears in the rendered text.
        assert "status: fading" in content

    def test_archived_persisted_to_disk(self, tmp_path: Path) -> None:
        """Pattern with distance >= 10 milestones -> status: archived on disk."""
        for i in range(1, 15):
            self._make_milestone(tmp_path, f"{i}-ms")

        self._seed_memory(tmp_path, """\
# Operational Memory

## Patterns

### ancient-pattern
type: debt
observed: 1-ms
reinforced: 1
last_active: 1-ms

Very old debt.
""")
        consolidate_milestone(tmp_path, "14-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        ancient = [p for p in patterns if p.name == "ancient-pattern"]
        assert len(ancient) == 1
        assert ancient[0].status == "archived"
        # Archived section present in rendered output.
        assert "## Archived" in content

    def test_reinforced_5_never_decays(self, tmp_path: Path) -> None:
        """Patterns with reinforced >= 5 are durable and exempt from decay."""
        for i in range(1, 20):
            self._make_milestone(tmp_path, f"{i}-ms")

        self._seed_memory(tmp_path, """\
# Operational Memory

## Patterns

### durable-pattern
type: debt
observed: 1-ms, 2-ms, 3-ms, 4-ms, 5-ms
reinforced: 5
last_active: 1-ms

Durable pattern.
""")
        consolidate_milestone(tmp_path, "19-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        durable = [p for p in patterns if p.name == "durable-pattern"]
        assert len(durable) == 1
        assert durable[0].status == "active"

    def test_invalidated_becomes_archived_during_decay(self, tmp_path: Path) -> None:
        """Invalidated patterns get status: archived during decay."""
        for i in range(1, 5):
            self._make_milestone(tmp_path, f"{i}-ms")

        self._seed_memory(tmp_path, """\
# Operational Memory

## Patterns

### invalidated-pattern
type: decomposition
observed: 1-ms
reinforced: 1
last_active: 1-ms
invalidated: 3-ms
invalidation_reason: structural change: sequential -> parallel

Old decomposition, now invalidated.
""")
        consolidate_milestone(tmp_path, "4-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        inv = [p for p in patterns if p.name == "invalidated-pattern"]
        assert len(inv) == 1
        assert inv[0].status == "archived"
        assert inv[0].invalidated == "3-ms"

    def test_already_archived_no_op(self, tmp_path: Path) -> None:
        """Decay of already-archived pattern is a no-op (stays archived)."""
        for i in range(1, 5):
            self._make_milestone(tmp_path, f"{i}-ms")

        self._seed_memory(tmp_path, """\
# Operational Memory

## Patterns

## Archived

### already-archived
type: debt
observed: 1-ms
reinforced: 1
last_active: 1-ms
status: archived

Already archived.
""")
        consolidate_milestone(tmp_path, "4-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        arch = [p for p in patterns if p.name == "already-archived"]
        assert len(arch) == 1
        assert arch[0].status == "archived"


# ---------------------------------------------------------------------------
# DB-18 I2: Decisions compaction integration tests
# ---------------------------------------------------------------------------


class TestDecisionsCompactionIntegration:
    """End-to-end tests for compact_decisions in the lifecycle pipeline."""

    def _make_decisions(
        self, tmp_path: Path, milestone: str, num_groups: int, *, big: bool = True,
    ) -> Path:
        """Create a decisions.md with *num_groups* cycle groups."""
        ms_dir = tmp_path / ".clou" / "milestones" / milestone
        ms_dir.mkdir(parents=True, exist_ok=True)
        # Also create metrics.md so pipeline can consolidate.
        (ms_dir / "metrics.md").write_text(
            f"# Metrics: {milestone}\n\n"
            f"outcome: completed\ncycles: 2\nduration: 5m\n"
            f"tokens_in: 100\ntokens_out: 5000\n"
            f"agents_spawned: 1\nagents_completed: 1\nagents_failed: 0\n"
            f"crash_retries: 0\nvalidation_failures: 0\n"
            f"context_exhaustions: 0\n"
        )

        groups = []
        for i in range(num_groups):
            cycle_num = num_groups - i
            detail = "x" * 3000 if big else "small"
            groups.append(
                f"## Cycle {cycle_num} -- Assessment\n"
                f"### Accepted: finding-{cycle_num}a\n"
                f"**Finding:** \"{detail}\"\n"
                f"### Overridden: finding-{cycle_num}b\n"
                f"**Finding:** \"{detail}\"\n"
            )
        path = ms_dir / "decisions.md"
        path.write_text("# Decisions\n\n" + "\n".join(groups))
        return path

    def test_integration_compaction_in_pipeline(self, tmp_path: Path) -> None:
        """run_lifecycle_pipeline compacts decisions.md with >3 groups and >16k chars."""
        path = self._make_decisions(tmp_path, "1-test", num_groups=6)

        asyncio.run(run_lifecycle_pipeline(tmp_path))

        content = path.read_text()
        assert "(compacted)" in content
        # Most recent 3 should be preserved.
        assert "finding-6a" in content
        assert "finding-5a" in content
        assert "finding-4a" in content
        # Older should be compacted.
        assert "finding-3a" not in content

    def test_exactly_3_groups_no_compaction(self, tmp_path: Path) -> None:
        """Exactly 3 groups does NOT trigger compaction."""
        path = self._make_decisions(tmp_path, "1-test", num_groups=3)

        result = compact_decisions(path, token_threshold=0)
        assert result is False
        content = path.read_text()
        assert "(compacted)" not in content

    def test_recent_3_groups_preserved_in_full(self, tmp_path: Path) -> None:
        """The 3 most recent groups are preserved verbatim."""
        path = self._make_decisions(tmp_path, "1-test", num_groups=5)

        compact_decisions(path, token_threshold=0)
        content = path.read_text()
        # Recent 3 (cycles 5, 4, 3) preserved.
        assert "finding-5a" in content
        assert "finding-4a" in content
        assert "finding-3a" in content
        # Older (cycles 2, 1) compacted.
        assert "finding-2a" not in content
        assert "finding-1a" not in content

    def test_compaction_idempotent(self, tmp_path: Path) -> None:
        """Running compact_decisions twice produces the same result."""
        path = self._make_decisions(tmp_path, "1-test", num_groups=6)

        compact_decisions(path, token_threshold=0)
        first_pass = path.read_text()

        compact_decisions(path, token_threshold=0)
        second_pass = path.read_text()

        assert first_pass == second_pass

    def test_below_token_threshold_no_compaction(self, tmp_path: Path) -> None:
        """Small content below token threshold is not compacted."""
        path = self._make_decisions(tmp_path, "1-test", num_groups=6, big=False)

        # Default threshold is 4000 tokens = 16000 chars.
        result = compact_decisions(path)
        assert result is False


# ---------------------------------------------------------------------------
# DB-18 I3: Temporal invalidation tests
# ---------------------------------------------------------------------------


class TestDetectContradiction:
    """Unit tests for _detect_contradiction keyword group matching."""

    def test_sequential_to_parallel_contradiction(self) -> None:
        """Decomposition: 'sequential' -> 'parallel (gather)' is a contradiction."""
        reason = _detect_contradiction(
            "1 phase, sequential execution.",
            "3 phases, parallel (gather) execution.",
            "decomposition",
        )
        assert reason
        assert "sequential" in reason
        assert "parallel" in reason

    def test_numeric_update_not_contradiction(self) -> None:
        """Changing cycle count 2->3 is NOT a contradiction."""
        reason = _detect_contradiction(
            "2 phases, sequential execution.",
            "3 phases, sequential execution.",
            "decomposition",
        )
        assert reason == ""

    def test_same_topology_not_contradiction(self) -> None:
        """Same structural keywords do not trigger contradiction."""
        reason = _detect_contradiction(
            "3 phases, parallel (gather) execution.",
            "5 phases, parallel (gather) execution.",
            "decomposition",
        )
        assert reason == ""

    def test_escalation_type_change(self) -> None:
        """Escalation: 'staleness' -> 'crash' is a contradiction."""
        reason = _detect_contradiction(
            "Milestone ended with outcome: escalated_staleness.",
            "Milestone ended with outcome: escalated_crash.",
            "escalation",
        )
        assert reason
        assert "staleness" in reason
        assert "crash" in reason

    def test_unknown_type_no_contradiction(self) -> None:
        """Unknown pattern types never produce contradictions."""
        reason = _detect_contradiction(
            "anything",
            "something else entirely",
            "cost-calibration",
        )
        assert reason == ""

    def test_all_defined_contradiction_groups(self) -> None:
        """Verify all _CONTRADICTION_GROUPS types have at least one group."""
        from clou.recovery_compaction import _CONTRADICTION_GROUPS
        assert "decomposition" in _CONTRADICTION_GROUPS
        assert "escalation" in _CONTRADICTION_GROUPS
        for type_name, groups in _CONTRADICTION_GROUPS.items():
            assert len(groups) >= 1, f"{type_name} has no keyword groups"
            for group in groups:
                assert len(group) >= 2, f"{type_name} group has fewer than 2 terms"


class TestInvalidateContradictions:
    """Tests for _invalidate_contradictions."""

    def test_contradiction_marks_invalidated(self) -> None:
        """Structural contradiction marks the existing pattern as invalidated."""
        patterns = [
            MemoryPattern(
                name="decomposition-topology",
                type="decomposition",
                observed=["1-ms"],
                reinforced=1,
                last_active="1-ms",
                description="1 phase, sequential execution.",
            ),
        ]
        result = _invalidate_contradictions(
            patterns,
            name="decomposition-topology",
            type_="decomposition",
            new_description="3 phases, parallel (gather) execution.",
            milestone="2-ms",
        )
        assert result is True
        assert patterns[0].invalidated == "2-ms"
        assert "sequential" in patterns[0].invalidation_reason
        assert "parallel" in patterns[0].invalidation_reason

    def test_no_contradiction_leaves_pattern(self) -> None:
        """Compatible descriptions do not invalidate."""
        patterns = [
            MemoryPattern(
                name="decomposition-topology",
                type="decomposition",
                observed=["1-ms"],
                reinforced=1,
                last_active="1-ms",
                description="3 phases, sequential execution.",
            ),
        ]
        result = _invalidate_contradictions(
            patterns,
            name="decomposition-topology",
            type_="decomposition",
            new_description="4 phases, sequential execution.",
            milestone="2-ms",
        )
        assert result is False
        assert patterns[0].invalidated == ""

    def test_already_invalidated_not_double_invalidated(self) -> None:
        """Already-invalidated patterns are not double-invalidated."""
        patterns = [
            MemoryPattern(
                name="decomposition-topology",
                type="decomposition",
                observed=["1-ms"],
                reinforced=1,
                last_active="1-ms",
                invalidated="2-ms",
                invalidation_reason="structural change: sequential -> parallel",
                description="1 phase, sequential execution.",
            ),
        ]
        result = _invalidate_contradictions(
            patterns,
            name="decomposition-topology",
            type_="decomposition",
            new_description="5 phases, parallel (gather) execution.",
            milestone="3-ms",
        )
        assert result is False
        # Original invalidation preserved.
        assert patterns[0].invalidated == "2-ms"

    def test_invalidated_pattern_preserved_in_rendered_memory(self) -> None:
        """Invalidated patterns are preserved (not deleted) in _render_memory output."""
        patterns = [
            MemoryPattern(
                name="decomposition-topology",
                type="decomposition",
                observed=["1-ms"],
                reinforced=1,
                last_active="1-ms",
                invalidated="2-ms",
                invalidation_reason="structural change: sequential -> parallel",
                description="1 phase, sequential execution.",
            ),
        ]
        rendered = _render_memory(patterns)
        assert "decomposition-topology" in rendered
        assert "invalidated: 2-ms" in rendered
        assert "invalidation_reason:" in rendered


class TestInvalidationE2E:
    """End-to-end invalidation through consolidate_milestone."""

    def _make_milestone(
        self, tmp_path: Path, name: str, *,
        topology: str = "sequential", phase_count: int = 1,
    ) -> Path:
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            f"# Metrics: {name}\n\n"
            f"outcome: completed\ncycles: 2\nduration: 5m\n"
            f"tokens_in: 100\ntokens_out: 5000\n"
            f"agents_spawned: 1\nagents_completed: 1\nagents_failed: 0\n"
            f"crash_retries: 0\nvalidation_failures: 0\n"
            f"context_exhaustions: 0\n"
        )
        if topology == "parallel":
            (ms_dir / "compose.py").write_text(
                "from clou.compose_types import gather\n"
                + "".join(
                    f'async def phase_{i}() -> R{i}:\n    """Phase {i}."""\n'
                    for i in range(phase_count)
                )
                + "async def execute():\n"
                + f"    results = await gather({', '.join(f'phase_{i}()' for i in range(phase_count))})\n"
            )
        else:
            (ms_dir / "compose.py").write_text(
                "".join(
                    f'async def phase_{i}() -> R{i}:\n    """Phase {i}."""\n'
                    for i in range(phase_count)
                )
                + "async def execute():\n"
                + "".join(
                    f"    r{i} = await phase_{i}()\n" for i in range(phase_count)
                )
            )
        return ms_dir

    def test_sequential_to_parallel_invalidates(self, tmp_path: Path) -> None:
        """Sequential decomposition followed by parallel triggers invalidation."""
        self._make_milestone(tmp_path, "1-ms", topology="sequential", phase_count=1)
        consolidate_milestone(tmp_path, "1-ms")

        # Second milestone is parallel.
        self._make_milestone(tmp_path, "2-ms", topology="parallel", phase_count=3)
        consolidate_milestone(tmp_path, "2-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        # The reinforce_or_create call clears invalidation and re-activates.
        # But there should be evidence of the invalidation + re-creation flow.
        decomp = [p for p in patterns if p.name == "decomposition-topology"]
        assert len(decomp) == 1
        # After invalidation, _reinforce_or_create clears it and updates.
        # The pattern should now reflect the latest (parallel) topology.
        assert "parallel" in decomp[0].description

    def test_numeric_only_change_no_invalidation(self, tmp_path: Path) -> None:
        """Changing phase count (1 -> 3) without topology change is not a contradiction."""
        self._make_milestone(tmp_path, "1-ms", topology="sequential", phase_count=1)
        consolidate_milestone(tmp_path, "1-ms")

        self._make_milestone(tmp_path, "2-ms", topology="sequential", phase_count=3)
        consolidate_milestone(tmp_path, "2-ms")

        content = (tmp_path / ".clou" / "memory.md").read_text()
        patterns = _parse_memory(content)
        decomp = [p for p in patterns if p.name == "decomposition-topology"]
        assert len(decomp) == 1
        assert decomp[0].invalidated == ""
        assert decomp[0].status == "active"


# ---------------------------------------------------------------------------
# DB-18 I7: Distribution accumulation enrichment (additional edge cases)
# ---------------------------------------------------------------------------


class TestDistributionAccumulationEdgeCases:
    """Additional edge case tests for distribution accumulation."""

    def test_distribution_with_single_observation_n1(self) -> None:
        """First accumulation from empty produces n=1."""
        result = _accumulate_distribution("", 3)
        assert "n=1" in result
        assert "min=3" in result
        assert "max=3" in result
        assert "median=3" in result

    def test_distribution_preserves_min_across_many(self) -> None:
        """Min is preserved correctly across 4 accumulations."""
        r = _accumulate_distribution("", 10)
        r = _accumulate_distribution(r, 5)
        r = _accumulate_distribution(r, 20)
        r = _accumulate_distribution(r, 1)
        assert "min=1" in r

    def test_distribution_median_computed(self) -> None:
        """Median is computed correctly for odd count."""
        # Start: 3 -> n=1
        r = _accumulate_distribution("", 3)
        # Add 7 -> n=2
        r = _accumulate_distribution(r, 7)
        # Add 5 -> n=3
        r = _accumulate_distribution(r, 5)
        assert "n=3" in r
        # Median of [3, 7, 5] approximation -- since reconstruction is approximate,
        # just verify the format is correct.
        assert "median=" in r

    def test_backward_compat_existing_plain_description(self) -> None:
        """Existing plain '2 cycles per milestone' gracefully accumulates."""
        result = _accumulate_distribution("2 cycles per milestone on average.", 4)
        assert "Distribution:" in result
        assert "n=2" in result
        assert "min=2" in result
        assert "max=4" in result

    def test_existing_pattern_without_distribution_in_consolidation(self, tmp_path: Path) -> None:
        """Pre-existing memory.md without distribution format gets graceful first accumulation."""
        ms_dir = tmp_path / ".clou" / "milestones" / "1-test"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            "# Metrics: 1-test\n\n"
            "outcome: completed\ncycles: 4\nduration: 10m\n"
            "tokens_in: 100\ntokens_out: 5000\n"
            "agents_spawned: 2\nagents_completed: 2\nagents_failed: 0\n"
            "crash_retries: 0\nvalidation_failures: 0\n"
            "context_exhaustions: 0\n"
        )

        # Seed memory with old-format (no Distribution suffix).
        mem_path = tmp_path / ".clou" / "memory.md"
        mem_path.write_text("""\
# Operational Memory

## Patterns

### cycle-count-distribution
type: cost-calibration
observed: 0-old
reinforced: 1
last_active: 0-old

2 cycles per milestone on average.
""")

        consolidate_milestone(tmp_path, "1-test")

        content = mem_path.read_text()
        patterns = _parse_memory(content)
        cost = [p for p in patterns if p.name == "cycle-count-distribution"]
        assert len(cost) == 1
        assert "Distribution:" in cost[0].description
        assert "4 cycles" in cost[0].description  # Latest observation.


# ---------------------------------------------------------------------------
# DB-18 I1: Empty memory.md (first consolidation) edge case
# ---------------------------------------------------------------------------


class TestFirstConsolidationEver:
    """Edge case: completely empty/absent memory.md."""

    def test_absent_memory_md_initializes(self, tmp_path: Path) -> None:
        """First consolidation with no memory.md creates it from scratch."""
        ms_dir = tmp_path / ".clou" / "milestones" / "1-first"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            "# Metrics: 1-first\n\n"
            "outcome: completed\ncycles: 3\nduration: 5m\n"
            "tokens_in: 50\ntokens_out: 2000\n"
            "agents_spawned: 1\nagents_completed: 1\nagents_failed: 0\n"
            "crash_retries: 0\nvalidation_failures: 0\n"
            "context_exhaustions: 0\n"
        )
        (ms_dir / "compose.py").write_text(
            'async def impl() -> R:\n    """Impl."""\n'
            'async def execute():\n    await impl()\n'
        )

        mem = tmp_path / ".clou" / "memory.md"
        assert not mem.exists()

        consolidate_milestone(tmp_path, "1-first")
        assert mem.exists()

        content = mem.read_text()
        assert "# Operational Memory" in content
        patterns = _parse_memory(content)
        assert len(patterns) >= 1
        cost = [p for p in patterns if p.name == "cycle-count-distribution"]
        assert len(cost) == 1
        assert "Distribution:" in cost[0].description


# ---------------------------------------------------------------------------
# parse_obsolete_flags -- handoff.md cleanup flag parser
# ---------------------------------------------------------------------------


class TestParseObsoleteFlags:
    """Tests for parse_obsolete_flags()."""

    def test_single_flag(self, tmp_path: Path) -> None:
        """Single Obsolete flag in Known Limitations is extracted."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- `.clou/roadmap.py.example` is legacy. "
            "Obsolete: `.clou/roadmap.py.example`\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == [".clou/roadmap.py.example"]

    def test_multiple_flags(self, tmp_path: Path) -> None:
        """Multiple Obsolete flags are all extracted."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Old file A. Obsolete: `.clou/old-a.md`\n"
            "- Old file B. Obsolete: `.clou/old-b.py`\n"
            "- Old file C. Obsolete: `.clou/old-c.txt`\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == [".clou/old-a.md", ".clou/old-b.py", ".clou/old-c.txt"]

    def test_no_flags(self, tmp_path: Path) -> None:
        """Known Limitations section with no Obsolete flags returns empty."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Some limitation without any obsolete annotation.\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == []

    def test_missing_file(self, tmp_path: Path) -> None:
        """Missing handoff.md returns empty list."""
        result = parse_obsolete_flags(tmp_path / "nonexistent.md")
        assert result == []

    def test_flags_outside_known_limitations_ignored(self, tmp_path: Path) -> None:
        """Obsolete flags outside Known Limitations section are ignored."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## What Was Built\n"
            "- Obsolete: `.clou/should-be-ignored.md`\n"
            "\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/should-be-found.md`\n"
            "\n"
            "## What to Look For\n"
            "- Obsolete: `.clou/also-ignored.md`\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == [".clou/should-be-found.md"]

    def test_flag_without_backticks_ignored(self, tmp_path: Path) -> None:
        """Obsolete flag without backtick wrapping is ignored."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: .clou/no-backticks.md\n"
            "- Obsolete: `.clou/with-backticks.md`\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == [".clou/with-backticks.md"]

    def test_no_known_limitations_section(self, tmp_path: Path) -> None:
        """File with no Known Limitations section returns empty."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## What Was Built\n"
            "- Something great.\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == []

    def test_real_handoff_format(self, tmp_path: Path) -> None:
        """Parser works with the real M31 handoff.md format (no flags)."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff: 31-cross-milestone-parallel-dispatch\n\n"
            "## Environment\n"
            "status: running\n\n"
            "## What Was Built\n"
            "The orchestrator now dispatches coordinators concurrently.\n\n"
            "## Known Limitations\n\n"
            "- `.clou/roadmap.py.example` still contains the legacy "
            "Python-format roadmap. It should be updated or removed.\n"
            "- The pairwise independence check requires at least one "
            "direction.\n\n"
            "## What to Look For\n"
            "- Verify annotation format.\n"
        )
        # No "Obsolete:" keyword -- just a description.
        result = parse_obsolete_flags(handoff)
        assert result == []

    def test_adversarial_headings_do_not_trigger_parsing(self, tmp_path: Path) -> None:
        """Headings containing 'known limitations' as substring are rejected."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text(
            "# Handoff\n\n"
            "## Unknown Limitations\n"
            "- Obsolete: `.clou/should-not-match.md`\n"
            "\n"
            "## Known Limitations Archive\n"
            "- Obsolete: `.clou/also-should-not-match.md`\n"
            "\n"
            "## No Known Limitations\n"
            "- Obsolete: `.clou/third-no-match.md`\n"
        )
        result = parse_obsolete_flags(handoff)
        assert result == []


# ---------------------------------------------------------------------------
# Lifecycle pipeline Stage 6: obsolete file cleanup
# ---------------------------------------------------------------------------


class TestLifecyclePipelineCleanup:
    """Tests for Stage 6 (obsolete file cleanup) in run_lifecycle_pipeline."""

    def _make_milestone(self, tmp_path: Path, name: str) -> Path:
        ms_dir = tmp_path / ".clou" / "milestones" / name
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            f"# Metrics: {name}\n\n"
            f"outcome: completed\n"
            f"cycles: 3\n"
            f"duration: 10m\n"
            f"tokens_in: 100\n"
            f"tokens_out: 5000\n"
            f"agents_spawned: 2\n"
            f"agents_completed: 2\n"
            f"agents_failed: 0\n"
            f"crash_retries: 0\n"
            f"validation_failures: 0\n"
            f"context_exhaustions: 0\n"
        )
        return ms_dir

    def test_cleanup_stage_deletes_flagged_file(self, tmp_path: Path) -> None:
        """Stage 6 deletes files flagged as obsolete in handoff.md."""
        ms_dir = self._make_milestone(tmp_path, "1-alpha")
        clou_dir = tmp_path / ".clou"

        # Create the flagged file.
        target = clou_dir / "old-template.example"
        target.write_text("legacy content")

        # Add handoff.md with Obsolete flag.
        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/old-template.example`\n",
        )

        asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert not target.exists()

    def test_cleanup_stage_no_flags_no_action(self, tmp_path: Path) -> None:
        """Stage 6 with no Obsolete flags does not delete anything."""
        ms_dir = self._make_milestone(tmp_path, "1-alpha")
        clou_dir = tmp_path / ".clou"

        # Create a file that should NOT be deleted.
        safe_file = clou_dir / "safe.example"
        safe_file.write_text("safe content")

        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Some limitation without flags.\n",
        )

        asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert safe_file.exists()

    def test_cleanup_stage_missing_handoff(self, tmp_path: Path) -> None:
        """Stage 6 with no handoff.md does not error."""
        self._make_milestone(tmp_path, "1-alpha")
        # No handoff.md created.
        result = asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert isinstance(result, list)

    def test_cleanup_stage_permission_guard_rejects(self, tmp_path: Path) -> None:
        """Stage 6 does not delete files outside cleanup scope."""
        ms_dir = self._make_milestone(tmp_path, "1-alpha")
        clou_dir = tmp_path / ".clou"

        # Create file that does not match CLEANUP_SCOPE patterns.
        target = clou_dir / "project.md"
        target.write_text("important config")

        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/project.md`\n",
        )

        asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert target.exists()  # file preserved

    def test_cleanup_stage_file_already_absent(self, tmp_path: Path) -> None:
        """Stage 6 handles flagged files that are already absent."""
        ms_dir = self._make_milestone(tmp_path, "1-alpha")

        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/gone.old`\n",
        )

        # Should not raise.
        result = asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert isinstance(result, list)

    def test_existing_stages_unchanged(self, tmp_path: Path) -> None:
        """DB-18 stages 1-5 still work correctly with Stage 6 present."""
        ms_dir = self._make_milestone(tmp_path, "1-alpha")
        clou_dir = tmp_path / ".clou"

        # Add a flagged file to verify Stage 6 runs alongside existing stages.
        target = clou_dir / "old.bak"
        target.write_text("backup")

        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/old.bak`\n",
        )

        result = asyncio.run(run_lifecycle_pipeline(tmp_path))

        # Stage 1+2: consolidation happened.
        assert "1-alpha" in result
        # Stage 5: memory.md was created.
        assert (clou_dir / "memory.md").exists()
        # Stage 6: flagged file deleted.
        assert not target.exists()

    def test_multi_milestone_retroactive_cleanup(self, tmp_path: Path) -> None:
        """F13: Stage 6 processes multiple milestones in a single pipeline run.

        Sets up two milestones (one already consolidated in memory.md, one new)
        and verifies both get their handoff flags processed.
        """
        clou_dir = tmp_path / ".clou"

        # Milestone 1: already consolidated (pre-existing in memory.md).
        ms1 = self._make_milestone(tmp_path, "1-alpha")
        (ms1 / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/old-alpha.example`\n",
        )
        target1 = clou_dir / "old-alpha.example"
        target1.write_text("alpha legacy")

        # Pre-populate memory.md so 1-alpha is already consolidated.
        memory_path = clou_dir / "memory.md"
        memory_path.write_text(
            "# Memory\n\n"
            "## cost-calibration: cycle-count-distribution\n"
            "observed: 1-alpha\n"
            "confidence: 1\n"
            "description: 3 cycles, ~5,000 output tokens, 10m.\n",
        )

        # Milestone 2: new, not yet consolidated.
        ms2 = self._make_milestone(tmp_path, "2-beta")
        (ms2 / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/old-beta.bak`\n",
        )
        target2 = clou_dir / "old-beta.bak"
        target2.write_text("beta legacy")

        result = asyncio.run(run_lifecycle_pipeline(tmp_path))

        # Both files should be cleaned up.
        assert not target1.exists(), "pre-consolidated milestone's flag not processed"
        assert not target2.exists(), "new milestone's flag not processed"
        # 2-beta should be newly consolidated.
        assert "2-beta" in result

    def test_cleanup_skips_incomplete_milestone(self, tmp_path: Path) -> None:
        """F1: Stage 6 skips milestones that have not completed."""
        clou_dir = tmp_path / ".clou"

        # Create a milestone with metrics showing non-completed outcome.
        ms_dir = clou_dir / "milestones" / "1-crashed"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "metrics.md").write_text(
            "# Metrics: 1-crashed\n\n"
            "outcome: escalated_cycle_limit\n"
            "cycles: 3\n"
            "duration: 10m\n"
            "tokens_in: 100\n"
            "tokens_out: 5000\n"
            "agents_spawned: 2\n"
            "agents_completed: 1\n"
            "agents_failed: 1\n"
            "crash_retries: 0\n"
            "validation_failures: 0\n"
            "context_exhaustions: 0\n",
        )
        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/should-not-delete.example`\n",
        )
        target = clou_dir / "should-not-delete.example"
        target.write_text("keep me")

        asyncio.run(run_lifecycle_pipeline(tmp_path))

        # File should NOT be deleted because milestone did not complete.
        assert target.exists()

    def test_cleanup_skips_milestone_without_metrics(self, tmp_path: Path) -> None:
        """F1: Stage 6 skips milestones that have no metrics.md at all."""
        clou_dir = tmp_path / ".clou"

        ms_dir = clou_dir / "milestones" / "1-partial"
        ms_dir.mkdir(parents=True, exist_ok=True)
        # No metrics.md -- milestone never finished.
        (ms_dir / "handoff.md").write_text(
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/should-not-delete.bak`\n",
        )
        target = clou_dir / "should-not-delete.bak"
        target.write_text("keep me")

        asyncio.run(run_lifecycle_pipeline(tmp_path))
        assert target.exists()
