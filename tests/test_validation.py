"""Tests for golden context structural validation.

Each test creates a golden context file structure in a temp directory and
exercises the public validate_golden_context() API. No implementation
details are tested directly.

Phase 4 (validator-resilience): tests updated to work with
list[ValidationFinding] return type. New tests for severity classification,
errors_only/warnings_only helpers.
"""

from __future__ import annotations

from pathlib import Path

from clou.harness import ArtifactForm
from clou.validation import (
    ANTI_PATTERN_KEYS,
    Severity,
    ValidationFinding,
    _template_to_regex,
    errors_only,
    validate_artifact_form,
    validate_delivery,
    validate_golden_context,
    validate_readiness,
    warnings_only,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    """Write content to path, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _messages(findings: list[ValidationFinding]) -> list[str]:
    """Extract message strings from findings for backward-compatible assertions."""
    return [f.message for f in findings]


# ---------------------------------------------------------------------------
# Empty / missing files — no errors
# ---------------------------------------------------------------------------


def test_empty_project_no_errors(tmp_path: Path) -> None:
    """A project with no .clou dir at all produces no errors."""
    assert validate_golden_context(tmp_path, "m1") == []


def test_empty_clou_dir_no_errors(tmp_path: Path) -> None:
    """An empty .clou dir produces no errors."""
    (tmp_path / ".clou").mkdir()
    assert validate_golden_context(tmp_path, "m1") == []


# ---------------------------------------------------------------------------
# active/coordinator.md — checkpoint-tier validation (DB-12)
# ---------------------------------------------------------------------------

VALID_CHECKPOINT = """\
# Coordinator State

cycle: 3
step: ASSESS
next_step: VERIFY
current_phase: implementation
phases_completed: 2
phases_total: 3
"""


def test_valid_coordinator(tmp_path: Path) -> None:
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", VALID_CHECKPOINT)
    assert validate_golden_context(tmp_path, "m1") == []


def test_coordinator_missing_required_key(tmp_path: Path) -> None:
    """Missing a required key produces an error per missing key."""
    # Missing step and next_step
    content = "cycle: 1\ncurrent_phase: p1\nphases_completed: 0\nphases_total: 1\n"
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing required key 'step'" in m for m in msgs)
    assert any("missing required key 'next_step'" in m for m in msgs)


def test_coordinator_invalid_step(tmp_path: Path) -> None:
    """Invalid step value is rejected."""
    content = VALID_CHECKPOINT.replace("step: ASSESS", "step: BANANA")
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("invalid step 'BANANA'" in m for m in msgs)


def test_coordinator_invalid_next_step(tmp_path: Path) -> None:
    """Invalid next_step value is rejected."""
    content = VALID_CHECKPOINT.replace("next_step: VERIFY", "next_step: NOPE")
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("invalid next_step 'NOPE'" in m for m in msgs)


def test_coordinator_non_integer_cycle(tmp_path: Path) -> None:
    """Non-integer cycle value is rejected."""
    content = VALID_CHECKPOINT.replace("cycle: 3", "cycle: boom")
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("must be an integer" in m for m in msgs)


def test_coordinator_negative_phases(tmp_path: Path) -> None:
    """Negative integer values are rejected."""
    content = VALID_CHECKPOINT.replace("phases_completed: 2", "phases_completed: -1")
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("non-negative" in m for m in msgs)


def test_coordinator_completed_exceeds_total(tmp_path: Path) -> None:
    """phases_completed > phases_total is rejected."""
    content = VALID_CHECKPOINT.replace("phases_completed: 2", "phases_completed: 5")
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("exceeds phases_total" in m for m in msgs)


def test_coordinator_all_keys_missing(tmp_path: Path) -> None:
    """Content with no key-value pairs produces errors for all required keys."""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md",
        "# Coordinator\nJust some text.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    assert len(findings) == 6  # one per required key


def test_coordinator_valid_next_step_rework(tmp_path: Path) -> None:
    """'EXECUTE (rework)' is a valid next_step."""
    content = VALID_CHECKPOINT.replace(
        "next_step: VERIFY", "next_step: EXECUTE (rework)"
    )
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_coordinator_valid_next_step_complete(tmp_path: Path) -> None:
    """'COMPLETE' is a valid next_step."""
    content = VALID_CHECKPOINT.replace("next_step: VERIFY", "next_step: COMPLETE")
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


# ---------------------------------------------------------------------------
# execution.md
# ---------------------------------------------------------------------------

VALID_STATUS = """\
# Status

## Current State
phase: implementation
cycle: 3
last_updated: 2026-03-25

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
| implementation | in_progress |
"""

VALID_EXECUTION = """\
## Summary
status: completed

## Tasks
### T1: Set up database
**Status:** completed
Some details here.

### T2: Write API
**Status:** in_progress
More details.
"""


def test_valid_execution(tmp_path: Path) -> None:
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", VALID_EXECUTION)
    assert validate_golden_context(tmp_path, "m1") == []


def test_execution_missing_summary(tmp_path: Path) -> None:
    content = """\
## Tasks
### T1: Do something
**Status:** pending
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Summary'" in m for m in msgs)


def test_execution_summary_missing_status_field(tmp_path: Path) -> None:
    content = """\
## Summary
No status field here.

## Tasks
### T1: Do something
**Status:** pending
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("'status:' field" in m for m in msgs)


def test_execution_missing_tasks(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Tasks'" in m for m in msgs)


def test_execution_tasks_no_entries(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed

## Tasks
Nothing here yet.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("no '### T<N>:' entries" in m for m in msgs)


def test_execution_task_missing_status(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed

## Tasks
### T1: Do something
No status here.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("task 1 missing '**Status:**'" in m for m in msgs)


def test_execution_task_invalid_status(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed

## Tasks
### T1: Do something
**Status:** done
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("invalid status 'done'" in m for m in msgs)


def test_execution_all_valid_task_statuses(tmp_path: Path) -> None:
    """All four valid task statuses should pass."""
    content = """\
## Summary
status: in_progress

## Tasks
### T1: A
**Status:** pending

### T2: B
**Status:** in_progress

### T3: C
**Status:** completed

### T4: D
**Status:** failed
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


# ---------------------------------------------------------------------------
# decisions.md
# ---------------------------------------------------------------------------


def test_valid_decisions(tmp_path: Path) -> None:
    content = """\
# Decisions

## Cycle 1
### Accepted: Use PostgreSQL
Reasoning here.

## Cycle 2
### Overridden: Switch to SQLite
Changed our mind.

### Tradeoff: Performance vs simplicity
Chose simplicity.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_decisions_no_cycle_section(tmp_path: Path) -> None:
    content = """\
# Decisions
Some text but no cycle sections.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Cycle'" in m for m in msgs)


def test_decisions_cycle_no_entries(tmp_path: Path) -> None:
    content = """\
# Decisions

## Cycle 1
No decision entries here.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("Cycle section 1 has no" in m for m in msgs)


def test_decisions_assess_section_zero_findings_valid(tmp_path: Path) -> None:
    """An ASSESS/Brutalist cycle section with zero findings is valid (convergence)."""
    content = """\
## Cycle 3 — Brutalist Assessment
No new findings. Convergence reached.

## Cycle 2
### Accepted: Use PostgreSQL
Good choice.

## Cycle 1
### Accepted: Initial setup
Done.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_decisions_assess_keyword_section_zero_findings_valid(tmp_path: Path) -> None:
    """ASSESS keyword in section header also exempts from entry requirement."""
    content = """\
## Cycle 2 — ASSESS
Zero findings this round.

## Cycle 1
### Tradeoff: Speed vs safety
Chose safety.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_decisions_non_assess_section_still_requires_entries(tmp_path: Path) -> None:
    """Non-ASSESS sections without entries still fail validation."""
    content = """\
## Cycle 2 — EXECUTE
No entries.

## Cycle 1
### Accepted: Something
OK.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("Cycle section 1 has no" in m for m in msgs)


def test_decisions_mixed_valid_invalid_cycles(tmp_path: Path) -> None:
    """First cycle valid, second cycle missing entries."""
    content = """\
## Cycle 1
### Accepted: Something
Good.

## Cycle 2
No entries here.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    assert len(findings) == 1
    assert "Cycle section 2" in findings[0].message


# ---------------------------------------------------------------------------
# status.md
# ---------------------------------------------------------------------------


def test_valid_status(tmp_path: Path) -> None:
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", VALID_STATUS)
    assert validate_golden_context(tmp_path, "m1") == []


def test_status_missing_current_state(tmp_path: Path) -> None:
    content = """\
# Status

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("Current State" in m for m in msgs)


def test_status_missing_phase_progress(tmp_path: Path) -> None:
    content = """\
# Status

## Current State
phase: implementation
cycle: 1
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("Phase Progress" in m for m in msgs)


def test_status_missing_phase_key(tmp_path: Path) -> None:
    """Current State section missing 'phase:' key."""
    content = """\
# Status

## Current State
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | completed |
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing 'phase:'" in m for m in msgs)


def test_status_invalid_phase_status(tmp_path: Path) -> None:
    """Invalid status value in Phase Progress table."""
    content = """\
# Status

## Current State
phase: p1
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | bananas |
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("invalid phase status" in m for m in msgs)


def test_status_empty_phase_progress_table(tmp_path: Path) -> None:
    """Phase Progress section with header but no data rows."""
    content = """\
# Status

## Current State
phase: p1
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("no table rows" in m for m in msgs)


# ---------------------------------------------------------------------------
# roadmap.md
# ---------------------------------------------------------------------------


def test_valid_roadmap(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
### 1. Authentication
**Status:** completed

### 2. Dashboard
**Status:** in_progress
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_roadmap_missing_milestones_section(tmp_path: Path) -> None:
    content = """\
# Roadmap
Some text.
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Milestones'" in m for m in msgs)


def test_roadmap_no_entries(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
No entries yet.
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("no '### N. name' entries" in m for m in msgs)


def test_roadmap_entry_missing_status(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
### 1. Auth
No status.
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("milestone entry 1 missing '**Status:**'" in m for m in msgs)


def test_roadmap_entry_invalid_status(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
### 1. Auth
**Status:** done
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("invalid status 'done'" in m for m in msgs)


def test_roadmap_all_valid_statuses(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
### 1. A
**Status:** pending

### 2. B
**Status:** in_progress

### 3. C
**Status:** completed

### 4. D
**Status:** blocked
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


# ---------------------------------------------------------------------------
# assessment.md
# ---------------------------------------------------------------------------

VALID_ASSESSMENT = """\
# Assessment: implementation

## Summary
status: completed
tools_invoked: 3
findings: 2 total, 0 critical, 1 major, 1 minor
phase_evaluated: implementation

## Tools Invoked

- roast_codebase: invoked
- roast_architecture: invoked
- roast_security: skipped (no auth code)

## Findings

### F1: Missing error handling in API client
**Severity:** major
**Source tool:** roast_codebase
**Source models:** claude, codex
**Affected files:**
  - src/api.py
**Finding:** "The API client has no error handling for network failures"
**Context:** Found in the main request method.

### F2: Inconsistent naming
**Severity:** minor
**Source tool:** roast_codebase
**Source models:** gemini
**Affected files:**
  - src/utils.py
**Finding:** "Function names mix camelCase and snake_case"
**Context:** Style inconsistency across utility module.
"""


def test_valid_assessment(tmp_path: Path) -> None:
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", VALID_ASSESSMENT)
    assert validate_golden_context(tmp_path, "m1") == []


def test_assessment_missing_summary(tmp_path: Path) -> None:
    content = """\
# Assessment: implementation

## Findings

### F1: Something
**Severity:** minor
**Finding:** "A finding"
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Summary'" in m for m in msgs)


def test_assessment_summary_missing_status(tmp_path: Path) -> None:
    content = """\
# Assessment: implementation

## Summary
tools_invoked: 2

## Findings
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("'status:' field" in m for m in msgs)


def test_assessment_blocked_status_valid(tmp_path: Path) -> None:
    """Blocked status is a valid terminal state — no findings required."""
    content = """\
# Assessment: implementation

## Summary
status: blocked
error: Brutalist MCP unavailable
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_assessment_missing_findings_section(tmp_path: Path) -> None:
    content = """\
# Assessment: implementation

## Summary
status: completed
tools_invoked: 1
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Findings'" in m for m in msgs)


def test_assessment_finding_missing_severity(tmp_path: Path) -> None:
    content = """\
# Assessment: implementation

## Summary
status: completed

## Findings

### F1: A finding
**Finding:** "Some quote"
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("finding 1 missing '**Severity:**'" in m for m in msgs)


def test_assessment_finding_missing_quote(tmp_path: Path) -> None:
    content = """\
# Assessment: implementation

## Summary
status: completed

## Findings

### F1: A finding
**Severity:** major
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("finding 1 missing '**Finding:**'" in m for m in msgs)


def test_assessment_empty_findings_valid(tmp_path: Path) -> None:
    """Zero findings is valid — the assessor may find nothing."""
    content = """\
# Assessment: implementation

## Summary
status: completed
findings: 0 total

## Findings

No findings.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


# ---------------------------------------------------------------------------
# Cross-file / integration
# ---------------------------------------------------------------------------


def test_multiple_files_all_valid(tmp_path: Path) -> None:
    """All golden context files present and valid."""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md",
        VALID_CHECKPOINT,
    )
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "execution.md",
        VALID_EXECUTION,
    )
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "decisions.md",
        "## Cycle 1\n### Accepted: X\nOK.\n",
    )
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "status.md",
        VALID_STATUS,
    )
    _write(
        tmp_path / ".clou" / "roadmap.md",
        "## Milestones\n### 1. Auth\n**Status:** completed\n",
    )
    assert validate_golden_context(tmp_path, "m1") == []


def test_multiple_files_multiple_errors(tmp_path: Path) -> None:
    """Errors from different files are all collected."""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md",
        "Nothing useful.\n",
    )
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "execution.md",
        "Nothing useful.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    # Coordinator errors + execution errors
    assert len(findings) >= 4


def test_different_milestone_name(tmp_path: Path) -> None:
    """Milestone name is used to locate milestone-specific files."""
    _write(
        tmp_path / ".clou" / "milestones" / "v2-api" / "execution.md",
        VALID_EXECUTION,
    )
    # Checking m1 should not find v2-api's execution.md
    assert validate_golden_context(tmp_path, "m1") == []
    # Checking v2-api should find and validate it
    assert validate_golden_context(tmp_path, "v2-api") == []


# ---------------------------------------------------------------------------
# Phase-level execution.md (nested under phases/)
# ---------------------------------------------------------------------------


def test_phase_level_execution_valid(tmp_path: Path) -> None:
    """execution.md under phases/*/execution.md is validated."""
    _write(
        tmp_path
        / ".clou"
        / "milestones"
        / "m1"
        / "phases"
        / "implement"
        / "execution.md",
        VALID_EXECUTION,
    )
    assert validate_golden_context(tmp_path, "m1") == []


def test_phase_level_no_execution_files(tmp_path: Path) -> None:
    """No execution.md files at all is fine — early milestones may not have phases."""
    (tmp_path / ".clou" / "milestones" / "m1" / "phases" / "assess").mkdir(parents=True)
    assert validate_golden_context(tmp_path, "m1") == []


def test_phase_level_execution_malformed(tmp_path: Path) -> None:
    """Malformed phase-level execution.md should produce errors."""
    _write(
        tmp_path
        / ".clou"
        / "milestones"
        / "m1"
        / "phases"
        / "implement"
        / "execution.md",
        "Nothing useful.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    msgs = _messages(findings)
    assert any("missing '## Summary'" in m for m in msgs)
    assert any("missing '## Tasks'" in m for m in msgs)


def test_phase_level_multiple_phases_validated(tmp_path: Path) -> None:
    """Multiple phase execution.md files are each validated independently."""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "phases" / "assess" / "execution.md",
        VALID_EXECUTION,
    )
    _write(
        tmp_path
        / ".clou"
        / "milestones"
        / "m1"
        / "phases"
        / "implement"
        / "execution.md",
        "Nothing useful.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    # assess is valid, implement is malformed
    assert len(findings) >= 2
    assert any("implement" in f.path for f in findings)


def test_phase_level_and_flat_both_checked(tmp_path: Path) -> None:
    """Both flat and phase-level execution.md are validated when both exist."""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "execution.md",
        VALID_EXECUTION,
    )
    _write(
        tmp_path
        / ".clou"
        / "milestones"
        / "m1"
        / "phases"
        / "implement"
        / "execution.md",
        "Nothing useful.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    # Flat is valid, phase-level is malformed
    assert len(findings) >= 2


# ---------------------------------------------------------------------------
# Severity classification — new tests for validator-resilience
# ---------------------------------------------------------------------------


def test_checkpoint_errors_are_errors(tmp_path: Path) -> None:
    """Missing keys and bad enums in coordinator checkpoint produce ERROR severity."""
    content = "cycle: 1\ncurrent_phase: p1\nphases_completed: 0\nphases_total: 1\n"
    _write(tmp_path / ".clou" / "milestones" / "m1" / "active" / "coordinator.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    assert all(f.severity == Severity.ERROR for f in findings)
    assert len(findings) >= 2  # at least step and next_step missing


def test_status_errors_are_errors(tmp_path: Path) -> None:
    """Missing Current State and Phase Progress in status.md produce ERROR severity."""
    content = "# Status\nSome text.\n"
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    assert len(errs) >= 1
    msgs = _messages(errs)
    assert any("Current State" in m for m in msgs)


def test_execution_structural_errors_are_errors(tmp_path: Path) -> None:
    """Missing ## Summary and ## Tasks in execution.md produce ERROR severity."""
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "execution.md",
        "Nothing useful.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    assert len(errs) >= 2
    msgs = _messages(errs)
    assert any("## Summary" in m for m in msgs)
    assert any("## Tasks" in m for m in msgs)


def test_formatting_issues_are_warnings(tmp_path: Path) -> None:
    """Missing **Status:** in roadmap entries and invalid task status are WARNINGs."""
    # Roadmap entry missing **Status:**
    roadmap = """\
# Roadmap

## Milestones
### 1. Auth
No status.
"""
    _write(tmp_path / ".clou" / "roadmap.md", roadmap)
    findings = validate_golden_context(tmp_path, "m1")
    warns = warnings_only(findings)
    assert len(warns) >= 1
    assert all(f.severity == Severity.WARNING for f in warns)
    assert any("**Status:**" in f.message for f in warns)


def test_decision_missing_entries_is_warning(tmp_path: Path) -> None:
    """Non-ASSESS cycle section without decision entries is WARNING severity."""
    content = """\
## Cycle 2 — EXECUTE
No entries.

## Cycle 1
### Accepted: Something
OK.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    warns = warnings_only(findings)
    assert len(warns) >= 1
    assert any("Cycle section" in f.message for f in warns)


def test_assessment_missing_field_markup_is_warning(tmp_path: Path) -> None:
    """Missing **Severity:** or **Finding:** in assessment findings is WARNING."""
    content = """\
# Assessment: implementation

## Summary
status: completed

## Findings

### F1: A finding
No severity or finding fields.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    warns = warnings_only(findings)
    assert len(warns) >= 1
    msgs = _messages(warns)
    assert any("**Severity:**" in m or "**Finding:**" in m for m in msgs)


def test_task_invalid_status_is_warning(tmp_path: Path) -> None:
    """Invalid task status values produce WARNING severity."""
    content = """\
## Summary
status: completed

## Tasks
### T1: Do something
**Status:** done
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    warns = warnings_only(findings)
    assert len(warns) >= 1
    assert any("invalid status 'done'" in f.message for f in warns)


def test_invalid_phase_status_is_warning(tmp_path: Path) -> None:
    """Invalid phase status in status.md table produces WARNING severity."""
    content = """\
# Status

## Current State
phase: p1
cycle: 1

## Phase Progress
| Phase | Status |
|-------|--------|
| setup | bananas |
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    warns = warnings_only(findings)
    assert len(warns) >= 1
    assert any("invalid phase status" in f.message for f in warns)


def test_roadmap_invalid_milestone_status_is_warning(tmp_path: Path) -> None:
    """Invalid milestone status in roadmap produces WARNING severity."""
    content = """\
# Roadmap

## Milestones
### 1. Auth
**Status:** done
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    findings = validate_golden_context(tmp_path, "m1")
    warns = warnings_only(findings)
    assert len(warns) >= 1
    assert any("invalid status 'done'" in f.message for f in warns)


def test_errors_only_helper(tmp_path: Path) -> None:
    """errors_only() filters to only ERROR-severity findings."""
    mixed = [
        ValidationFinding(Severity.ERROR, "error msg", "file.md"),
        ValidationFinding(Severity.WARNING, "warning msg", "file.md"),
        ValidationFinding(Severity.ERROR, "another error", "other.md"),
    ]
    result = errors_only(mixed)
    assert len(result) == 2
    assert all(f.severity == Severity.ERROR for f in result)
    assert {f.message for f in result} == {"error msg", "another error"}


def test_warnings_only_helper(tmp_path: Path) -> None:
    """warnings_only() filters to only WARNING-severity findings."""
    mixed = [
        ValidationFinding(Severity.ERROR, "error msg", "file.md"),
        ValidationFinding(Severity.WARNING, "warning msg", "file.md"),
        ValidationFinding(Severity.WARNING, "another warning", "other.md"),
    ]
    result = warnings_only(mixed)
    assert len(result) == 2
    assert all(f.severity == Severity.WARNING for f in result)
    assert {f.message for f in result} == {"warning msg", "another warning"}


def test_errors_only_empty_list() -> None:
    """errors_only() on empty list returns empty."""
    assert errors_only([]) == []


def test_warnings_only_no_warnings() -> None:
    """warnings_only() returns empty when all findings are errors."""
    findings = [
        ValidationFinding(Severity.ERROR, "err", "f.md"),
    ]
    assert warnings_only(findings) == []


def test_finding_str_renders_as_path_message() -> None:
    """ValidationFinding.__str__ renders as 'path message' for backward compat."""
    f = ValidationFinding(Severity.ERROR, "missing key", "active/coordinator.md")
    assert str(f) == "active/coordinator.md missing key"


def test_finding_has_path_attribute() -> None:
    """ValidationFinding carries the relative path to the problematic file."""
    f = ValidationFinding(Severity.ERROR, "missing key", "active/coordinator.md")
    assert f.path == "active/coordinator.md"
    assert f.severity == Severity.ERROR
    assert f.message == "missing key"


# ---------------------------------------------------------------------------
# Validation scoping — terminal phases exempt from blocking errors (R1, R4)
# ---------------------------------------------------------------------------

# A status.md that marks the "setup" phase as completed and "impl" as pending.
_STATUS_MD_SETUP_COMPLETED = """\
# Status

## Current State
phase: impl
cycle: 2
last_updated: 2026-03-28

## Phase Progress
| Phase | Status | Summary |
|---|---|---|
| setup | completed | --- |
| impl | in_progress | --- |
"""

# An execution.md that is missing the ## Tasks heading — normally produces ERROR.
_BAD_EXECUTION_NO_TASKS = """\
## Summary
status: completed
"""


def test_completed_phase_errors_downgraded_to_warning(tmp_path: Path) -> None:
    """R4 regression: completed phase with missing '## Tasks'.

    Produces WARNING, not ERROR.
    """
    ms_dir = tmp_path / ".clou" / "milestones" / "m1"
    _write(ms_dir / "status.md", _STATUS_MD_SETUP_COMPLETED)
    _write(ms_dir / "phases" / "setup" / "execution.md", _BAD_EXECUTION_NO_TASKS)
    findings = validate_golden_context(tmp_path, "m1")
    # The missing '## Tasks' finding should exist but as WARNING.
    assert any("missing '## Tasks'" in f.message for f in findings)
    errs = errors_only(findings)
    # No ERROR findings from the completed phase's execution.md.
    setup_errors = [f for f in errs if "setup" in f.path]
    assert setup_errors == []
    # The finding is present as a WARNING.
    warns = warnings_only(findings)
    setup_warns = [f for f in warns if "setup" in f.path]
    assert any("missing '## Tasks'" in f.message for f in setup_warns)


def test_pending_phase_errors_not_downgraded(tmp_path: Path) -> None:
    """Pending phase with missing '## Tasks' still produces ERROR — not exempt."""
    status_md = """\
# Status

## Current State
phase: impl
cycle: 1

## Phase Progress
| Phase | Status |
|---|---|
| impl | pending |
"""
    ms_dir = tmp_path / ".clou" / "milestones" / "m1"
    _write(ms_dir / "status.md", status_md)
    _write(ms_dir / "phases" / "impl" / "execution.md", _BAD_EXECUTION_NO_TASKS)
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    impl_errors = [f for f in errs if "impl" in f.path]
    assert any("missing '## Tasks'" in f.message for f in impl_errors)


def test_failed_phase_errors_downgraded_to_warning(tmp_path: Path) -> None:
    """Failed phase with missing '## Tasks' produces WARNING, not ERROR — terminal."""
    status_md = """\
# Status

## Current State
phase: other
cycle: 2

## Phase Progress
| Phase | Status |
|---|---|
| broken | failed |
"""
    ms_dir = tmp_path / ".clou" / "milestones" / "m1"
    _write(ms_dir / "status.md", status_md)
    _write(ms_dir / "phases" / "broken" / "execution.md", _BAD_EXECUTION_NO_TASKS)
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    broken_errors = [f for f in errs if "broken" in f.path]
    assert broken_errors == []
    warns = warnings_only(findings)
    broken_warns = [f for f in warns if "broken" in f.path]
    assert any("missing '## Tasks'" in f.message for f in broken_warns)


def test_mixed_completed_and_pending_phases(tmp_path: Path) -> None:
    """Mixed: completed phase bad execution.md -> WARNING.

    Pending phase bad execution.md -> ERROR.
    """
    status_md = """\
# Status

## Current State
phase: impl
cycle: 3

## Phase Progress
| Phase | Status | Summary |
|---|---|---|
| setup | completed | done |
| impl | in_progress | working |
"""
    ms_dir = tmp_path / ".clou" / "milestones" / "m1"
    _write(ms_dir / "status.md", status_md)
    # Both phases have bad execution.md (missing ## Tasks).
    _write(ms_dir / "phases" / "setup" / "execution.md", _BAD_EXECUTION_NO_TASKS)
    _write(ms_dir / "phases" / "impl" / "execution.md", _BAD_EXECUTION_NO_TASKS)
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    # Only the pending/in_progress phase should have ERROR findings.
    error_paths = [f.path for f in errs]
    assert any("impl" in p for p in error_paths)
    assert not any("setup" in p for p in error_paths)
    # The completed phase should have its findings as WARNING.
    warns = warnings_only(findings)
    warn_paths = [f.path for f in warns]
    assert any("setup" in p for p in warn_paths)


def test_no_status_md_no_downgrade(tmp_path: Path) -> None:
    """Without status.md, no phase exemptions apply — all errors remain as ERROR."""
    ms_dir = tmp_path / ".clou" / "milestones" / "m1"
    # No status.md written.
    _write(ms_dir / "phases" / "setup" / "execution.md", _BAD_EXECUTION_NO_TASKS)
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    assert any("missing '## Tasks'" in f.message for f in errs)


def test_flat_execution_not_exempt(tmp_path: Path) -> None:
    """The flat milestone-level execution.md is never subject to phase exemption."""
    status_md = """\
# Status

## Current State
phase: setup
cycle: 1

## Phase Progress
| Phase | Status |
|---|---|
| setup | completed |
"""
    ms_dir = tmp_path / ".clou" / "milestones" / "m1"
    _write(ms_dir / "status.md", status_md)
    _write(ms_dir / "execution.md", _BAD_EXECUTION_NO_TASKS)
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    assert any("missing '## Tasks'" in f.message for f in errs)


def test_mixed_errors_and_warnings_across_files(tmp_path: Path) -> None:
    """Validation returns both errors and warnings from different files."""
    # Invalid task status (WARNING) in execution.md
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "execution.md",
        """\
## Summary
status: completed

## Tasks
### T1: Do something
**Status:** done
""",
    )
    # Missing ## Milestones in roadmap (ERROR)
    _write(
        tmp_path / ".clou" / "roadmap.md",
        "# Roadmap\nSome text.\n",
    )
    findings = validate_golden_context(tmp_path, "m1")
    errs = errors_only(findings)
    warns = warnings_only(findings)
    assert len(errs) >= 1  # roadmap missing milestones
    assert len(warns) >= 1  # task invalid status


# ---------------------------------------------------------------------------
# ArtifactForm validation (DB-14)
# ---------------------------------------------------------------------------

_INTENTS_FORM = ArtifactForm(
    criterion_template="When {trigger}, {observable_outcome}",
    anti_patterns=(
        "file paths or module names as criterion subject",
        "implementation verbs (extract, refactor, build) as criterion",
    ),
)


class TestTemplateToRegex:
    """_template_to_regex converts criterion templates to matching regexes."""

    def test_simple_template(self) -> None:
        pat = _template_to_regex("When {trigger}, {observable_outcome}")
        assert pat.match("When the user logs in, they see a dashboard")
        assert pat.match("- When the user logs in, they see a dashboard")
        assert pat.match("* When agents complete, the graph updates")

    def test_case_insensitive(self) -> None:
        pat = _template_to_regex("When {trigger}, {observable_outcome}")
        assert pat.match("when the user logs in, they see a dashboard")
        assert pat.match("WHEN THE USER LOGS IN, THEY SEE A DASHBOARD")

    def test_no_match_without_template_structure(self) -> None:
        pat = _template_to_regex("When {trigger}, {observable_outcome}")
        assert not pat.match("TaskGraphWidget with keyboard nav")
        assert not pat.match("Extract the TurnController")

    def test_template_with_special_chars(self) -> None:
        pat = _template_to_regex("Given {ctx}, when {act}, then {out}")
        assert pat.match("Given a logged-in user, when they click save, then data persists")


class TestValidateArtifactForm:
    """validate_artifact_form checks content against ArtifactForm."""

    def test_valid_intents(self) -> None:
        content = "- When the user opens the app, they see a dashboard\n"
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert len(findings) == 0

    def test_empty_content_warns(self) -> None:
        findings = validate_artifact_form("", _INTENTS_FORM, "intents.md")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "empty" in findings[0].message

    def test_no_criteria_lines_warns(self) -> None:
        content = "# Just a header\n\nSome preamble text.\n"
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert any("no criteria" in f.message for f in findings)

    def test_non_matching_criterion_warns(self) -> None:
        content = "- TaskGraphWidget with keyboard nav\n"
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert any("does not match" in f.message for f in findings)

    def test_preamble_not_flagged(self) -> None:
        content = (
            "# Intents\n"
            "These are the observable outcomes:\n\n"
            "- When the user opens the app, they see a dashboard\n"
        )
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert len(findings) == 0

    def test_file_path_anti_pattern(self) -> None:
        content = "- When user edits clou/ui/app.py, changes are reflected\n"
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert any("file path" in f.message for f in findings)

    def test_implementation_artifact_anti_pattern(self) -> None:
        content = "- When widget TaskGraph renders, it shows status\n"
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert any("implementation artifact" in f.message for f in findings)

    def test_implementation_verb_anti_pattern(self) -> None:
        content = "- Extract the TurnController into standalone module\n"
        findings = validate_artifact_form(content, _INTENTS_FORM, "intents.md")
        assert any("implementation verb" in f.message for f in findings)

    def test_required_sections(self) -> None:
        form = ArtifactForm(sections=("Functional", "Non-Functional"))
        content = "# Functional\n- stuff\n"
        findings = validate_artifact_form(content, form, "test.md")
        assert any("Non-Functional" in f.message for f in findings)

    def test_all_sections_present_passes(self) -> None:
        form = ArtifactForm(sections=("Functional", "Non-Functional"))
        content = "## Functional\n- stuff\n## Non-Functional\n- more\n"
        findings = validate_artifact_form(content, form, "test.md")
        assert len(findings) == 0

    def test_form_with_no_constraints_passes(self) -> None:
        form = ArtifactForm()
        findings = validate_artifact_form("anything goes", form, "test.md")
        assert len(findings) == 0


class TestAntiPatternKeys:
    """ANTI_PATTERN_KEYS is consistent with _ANTI_PATTERN_MATCHERS."""

    def test_known_keys_exist(self) -> None:
        assert "file path" in ANTI_PATTERN_KEYS
        assert "implementation" in ANTI_PATTERN_KEYS
        assert "file inspection" in ANTI_PATTERN_KEYS

    def test_validate_template_catches_unknown_keys(self) -> None:
        from clou.harness import HarnessTemplate, validate_template

        tmpl = HarnessTemplate(
            name="test",
            description="test",
            agents={},
            quality_gates=[],
            verification_modalities=[],
            mcp_servers={},
            write_permissions={},
            artifact_forms={
                "intents": ArtifactForm(
                    anti_patterns=("SQL injection patterns",),
                ),
            },
        )
        errors = validate_template(tmpl)
        assert any("SQL injection" in e for e in errors)

    def test_validate_template_passes_known_keys(self) -> None:
        from clou.harness import HarnessTemplate, validate_template

        tmpl = HarnessTemplate(
            name="test",
            description="test",
            agents={},
            quality_gates=[],
            verification_modalities=[],
            mcp_servers={},
            write_permissions={},
            artifact_forms={
                "intents": ArtifactForm(
                    anti_patterns=("file paths in criteria",),
                ),
            },
        )
        errors = validate_template(tmpl)
        assert not any("anti-pattern" in e for e in errors)


class TestValidateGoldenContextWithTemplate:
    """validate_golden_context with template drives ArtifactForm validation."""

    def test_intents_validated_via_template(self, tmp_path: Path) -> None:
        """intents.md with bad content produces warnings when template provided."""
        from clou.harness import HarnessTemplate

        ms_dir = tmp_path / ".clou" / "milestones" / "m1"
        ms_dir.mkdir(parents=True)
        _write(ms_dir / "intents.md", "- Extract the TurnController\n")
        tmpl = HarnessTemplate(
            name="test",
            description="test",
            agents={},
            quality_gates=[],
            verification_modalities=[],
            mcp_servers={},
            write_permissions={},
            artifact_forms={
                "intents": ArtifactForm(
                    criterion_template="When {trigger}, {observable_outcome}",
                    anti_patterns=("implementation verbs",),
                ),
            },
        )
        findings = validate_golden_context(tmp_path, "m1", template=tmpl)
        warns = warnings_only(findings)
        assert any("does not match" in w.message for w in warns)
        assert any("implementation verb" in w.message for w in warns)

    def test_no_template_skips_form_validation(self, tmp_path: Path) -> None:
        """Without template, intents.md is not form-validated."""
        ms_dir = tmp_path / ".clou" / "milestones" / "m1"
        ms_dir.mkdir(parents=True)
        _write(ms_dir / "intents.md", "- totally invalid stuff\n")
        findings = validate_golden_context(tmp_path, "m1")
        # No template → no artifact form warnings.
        assert not any("does not match" in f.message for f in findings)


class TestFileInspectionAntiPattern:
    """The file_inspection anti-pattern fires on inspection-style criteria."""

    def test_file_exists_flagged(self) -> None:
        form = ArtifactForm(
            anti_patterns=("criteria verifiable by file inspection alone",),
        )
        content = "- When the module exists, the system works\n"
        findings = validate_artifact_form(content, form, "intents.md")
        assert any("file inspection" in f.message for f in findings)

    def test_file_contains_flagged(self) -> None:
        form = ArtifactForm(
            anti_patterns=("criteria verifiable by file inspection alone",),
        )
        content = "- When the directory contains tests, coverage is high\n"
        findings = validate_artifact_form(content, form, "intents.md")
        assert any("file inspection" in f.message for f in findings)


class TestBoldTextNotCaptured:
    """Bold markdown text (**Note:**) should not be treated as criteria."""

    def test_bold_note_not_flagged(self) -> None:
        form = ArtifactForm(
            criterion_template="When {trigger}, {observable_outcome}",
        )
        content = "**Note:** This is context.\n- When user logs in, they see home\n"
        findings = validate_artifact_form(content, form, "intents.md")
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Communication validation — delivery and readiness
# ---------------------------------------------------------------------------


class TestValidateDelivery:
    """Post-cycle delivery verification — did the coordinator write its state?"""

    def test_both_present(self, tmp_path: Path) -> None:
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        cp = milestone_dir / "active" / "coordinator.md"
        cp.parent.mkdir(parents=True)
        cp.write_text("cycle: 1\n")
        (milestone_dir / "status.md").write_text("phase: p1\n")

        assert validate_delivery(milestone_dir, cp, "m1") == []

    def test_checkpoint_missing(self, tmp_path: Path) -> None:
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        (milestone_dir / "status.md").write_text("phase: p1\n")
        cp = milestone_dir / "active" / "coordinator.md"

        findings = validate_delivery(milestone_dir, cp, "m1")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "checkpoint not delivered" in findings[0].message
        assert findings[0].path == "milestones/m1/active/coordinator.md"

    def test_status_missing(self, tmp_path: Path) -> None:
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        cp = milestone_dir / "active" / "coordinator.md"
        cp.parent.mkdir(parents=True)
        cp.write_text("cycle: 1\n")

        findings = validate_delivery(milestone_dir, cp, "m1")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "status not delivered" in findings[0].message
        assert findings[0].path == "milestones/m1/status.md"

    def test_both_missing(self, tmp_path: Path) -> None:
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        cp = milestone_dir / "active" / "coordinator.md"

        findings = validate_delivery(milestone_dir, cp, "m1")
        assert len(findings) == 2
        assert all(f.severity == Severity.ERROR for f in findings)

    def test_delivery_matching_next_step(self, tmp_path: Path) -> None:
        """Both files have same next_step -> no cross-validation finding."""
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        cp = milestone_dir / "active" / "coordinator.md"
        cp.parent.mkdir(parents=True)
        cp.write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: impl\nphases_completed: 1\nphases_total: 3\n"
        )
        _write(
            milestone_dir / "status.md",
            "## Current State\nphase: impl\ncycle: 2\nnext_step: ASSESS\n"
            "## Phase Progress\n| Phase | Status |\n|---|---|\n| impl | in_progress |\n",
        )

        findings = validate_delivery(milestone_dir, cp, "m1")
        assert findings == []

    def test_delivery_divergent_next_step(self, tmp_path: Path) -> None:
        """Different next_step -> ERROR finding."""
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        cp = milestone_dir / "active" / "coordinator.md"
        cp.parent.mkdir(parents=True)
        cp.write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: impl\nphases_completed: 1\nphases_total: 3\n"
        )
        _write(
            milestone_dir / "status.md",
            "## Current State\nphase: impl\ncycle: 2\nnext_step: VERIFY\n"
            "## Phase Progress\n| Phase | Status |\n|---|---|\n| impl | in_progress |\n",
        )

        findings = validate_delivery(milestone_dir, cp, "m1")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "diverges" in findings[0].message
        assert "VERIFY" in findings[0].message
        assert "ASSESS" in findings[0].message
        assert findings[0].path == "milestones/m1/active/coordinator.md"

    def test_delivery_status_missing_next_step(self, tmp_path: Path) -> None:
        """status.md lacks next_step -> no cross-validation finding (graceful skip)."""
        milestone_dir = tmp_path / "milestone"
        milestone_dir.mkdir()
        cp = milestone_dir / "active" / "coordinator.md"
        cp.parent.mkdir(parents=True)
        cp.write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: impl\nphases_completed: 1\nphases_total: 3\n"
        )
        _write(
            milestone_dir / "status.md",
            "## Current State\nphase: impl\ncycle: 2\n"
            "## Phase Progress\n| Phase | Status |\n|---|---|\n| impl | in_progress |\n",
        )

        findings = validate_delivery(milestone_dir, cp, "m1")
        assert findings == []


class TestValidateReadiness:
    """Pre-cycle readiness verification — can the next cycle proceed?"""

    def test_all_files_present(self, tmp_path: Path) -> None:
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        _write(md / "status.md", "ok")
        _write(md / "compose.py", "ok")

        findings = validate_readiness(
            clou, md, ["status.md", "compose.py"], "EXECUTE", "m1",
        )
        assert findings == []

    def test_structural_file_missing_is_error(self, tmp_path: Path) -> None:
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        _write(md / "status.md", "ok")

        findings = validate_readiness(
            clou, md, ["status.md", "compose.py"], "EXECUTE", "m1",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "compose.py" in findings[0].message
        assert findings[0].path == "milestones/m1/compose.py"

    def test_narrative_file_missing_is_warning(self, tmp_path: Path) -> None:
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        _write(md / "status.md", "ok")

        findings = validate_readiness(
            clou, md, ["status.md", "decisions.md"], "ASSESS", "m1",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "decisions.md" in findings[0].message
        assert findings[0].path == "milestones/m1/decisions.md"

    def test_empty_read_set(self, tmp_path: Path) -> None:
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        md.mkdir(parents=True)
        assert validate_readiness(clou, md, [], "COMPLETE", "m1") == []

    def test_checkpoint_missing_is_error(self, tmp_path: Path) -> None:
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        md.mkdir(parents=True)

        findings = validate_readiness(
            clou, md, ["active/coordinator.md"], "EXECUTE", "m1",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR

    def test_status_missing_is_error(self, tmp_path: Path) -> None:
        """status.md is structural — its absence blocks the cycle."""
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        _write(md / "compose.py", "ok")

        findings = validate_readiness(
            clou, md, ["status.md", "compose.py"], "EXECUTE", "m1",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "status.md" in findings[0].message

    def test_root_scoped_project_md(self, tmp_path: Path) -> None:
        """project.md resolves under clou_dir, not milestone_dir."""
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        md.mkdir(parents=True)
        _write(clou / "project.md", "ok")
        _write(md / "milestone.md", "ok")

        findings = validate_readiness(
            clou, md, ["milestone.md", "project.md"], "PLAN", "m1",
        )
        assert findings == []

    def test_root_scoped_project_md_missing(self, tmp_path: Path) -> None:
        """Missing root-scoped project.md — WARNING, path is .clou/-relative."""
        clou = tmp_path / ".clou"
        md = clou / "milestones" / "m1"
        md.mkdir(parents=True)

        findings = validate_readiness(clou, md, ["project.md"], "PLAN", "m1")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        # Root-scoped paths stay as-is (already .clou/-relative).
        assert findings[0].path == "project.md"
