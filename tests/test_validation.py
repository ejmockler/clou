"""Tests for golden context structural validation.

Each test creates a golden context file structure in a temp directory and
exercises the public validate_golden_context() API. No implementation
details are tested directly.
"""

from __future__ import annotations

from pathlib import Path

from clou.validation import validate_golden_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    """Write content to path, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


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
# active/coordinator.md
# ---------------------------------------------------------------------------


def test_valid_coordinator(tmp_path: Path) -> None:
    _write(
        tmp_path / ".clou" / "active" / "coordinator.md",
        "# Coordinator\n\n## Cycle\nCycle 3\n\n## Phase Status\nASSESS: done\n",
    )
    assert validate_golden_context(tmp_path, "m1") == []


def test_coordinator_missing_cycle(tmp_path: Path) -> None:
    _write(
        tmp_path / ".clou" / "active" / "coordinator.md",
        "# Coordinator\n\n## Phase Status\nASSESS: done\n",
    )
    errors = validate_golden_context(tmp_path, "m1")
    assert len(errors) == 1
    assert "'## Cycle'" in errors[0]


def test_coordinator_missing_phase_status(tmp_path: Path) -> None:
    _write(
        tmp_path / ".clou" / "active" / "coordinator.md",
        "# Coordinator\n\n## Cycle\nCycle 1\n",
    )
    errors = validate_golden_context(tmp_path, "m1")
    assert len(errors) == 1
    assert "'## Phase Status'" in errors[0]


def test_coordinator_missing_both(tmp_path: Path) -> None:
    _write(
        tmp_path / ".clou" / "active" / "coordinator.md",
        "# Coordinator\nJust some text.\n",
    )
    errors = validate_golden_context(tmp_path, "m1")
    assert len(errors) == 2


# ---------------------------------------------------------------------------
# execution.md
# ---------------------------------------------------------------------------

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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Summary'" in e for e in errors)


def test_execution_summary_missing_status_field(tmp_path: Path) -> None:
    content = """\
## Summary
No status field here.

## Tasks
### T1: Do something
**Status:** pending
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("'status:' field" in e for e in errors)


def test_execution_missing_tasks(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Tasks'" in e for e in errors)


def test_execution_tasks_no_entries(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed

## Tasks
Nothing here yet.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("no '### T<N>:' entries" in e for e in errors)


def test_execution_task_missing_status(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed

## Tasks
### T1: Do something
No status here.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("task 1 missing '**Status:**'" in e for e in errors)


def test_execution_task_invalid_status(tmp_path: Path) -> None:
    content = """\
## Summary
status: completed

## Tasks
### T1: Do something
**Status:** done
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "execution.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("invalid status 'done'" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Cycle'" in e for e in errors)


def test_decisions_cycle_no_entries(tmp_path: Path) -> None:
    content = """\
# Decisions

## Cycle 1
No decision entries here.
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "decisions.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("Cycle section 1 has no" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("Cycle section 1 has no" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert len(errors) == 1
    assert "Cycle section 2" in errors[0]


# ---------------------------------------------------------------------------
# status.md
# ---------------------------------------------------------------------------


def test_valid_status(tmp_path: Path) -> None:
    content = """\
# Status

Current State: EXECUTE

Phase Progress
| Phase | Status |
| ----- | ------ |
| ASSESS | done |
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    assert validate_golden_context(tmp_path, "m1") == []


def test_status_missing_current_state(tmp_path: Path) -> None:
    content = """\
# Status

Phase Progress
| Phase | Status |
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("Current State" in e for e in errors)


def test_status_missing_phase_progress(tmp_path: Path) -> None:
    content = """\
# Status

Current State: ASSESS
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "status.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("Phase Progress" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Milestones'" in e for e in errors)


def test_roadmap_no_entries(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
No entries yet.
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("no '### N. name' entries" in e for e in errors)


def test_roadmap_entry_missing_status(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
### 1. Auth
No status.
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("milestone entry 1 missing '**Status:**'" in e for e in errors)


def test_roadmap_entry_invalid_status(tmp_path: Path) -> None:
    content = """\
# Roadmap

## Milestones
### 1. Auth
**Status:** done
"""
    _write(tmp_path / ".clou" / "roadmap.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("invalid status 'done'" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Summary'" in e for e in errors)


def test_assessment_summary_missing_status(tmp_path: Path) -> None:
    content = """\
# Assessment: implementation

## Summary
tools_invoked: 2

## Findings
"""
    _write(tmp_path / ".clou" / "milestones" / "m1" / "assessment.md", content)
    errors = validate_golden_context(tmp_path, "m1")
    assert any("'status:' field" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Findings'" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("finding 1 missing '**Severity:**'" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("finding 1 missing '**Finding:**'" in e for e in errors)


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
        tmp_path / ".clou" / "active" / "coordinator.md",
        "## Cycle\nCycle 1\n\n## Phase Status\nOK\n",
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
        "Current State: EXECUTE\n\nPhase Progress\n| P | S |\n",
    )
    _write(
        tmp_path / ".clou" / "roadmap.md",
        "## Milestones\n### 1. Auth\n**Status:** completed\n",
    )
    assert validate_golden_context(tmp_path, "m1") == []


def test_multiple_files_multiple_errors(tmp_path: Path) -> None:
    """Errors from different files are all collected."""
    _write(
        tmp_path / ".clou" / "active" / "coordinator.md",
        "Nothing useful.\n",
    )
    _write(
        tmp_path / ".clou" / "milestones" / "m1" / "execution.md",
        "Nothing useful.\n",
    )
    errors = validate_golden_context(tmp_path, "m1")
    # Coordinator errors + execution errors
    assert len(errors) >= 4


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
    errors = validate_golden_context(tmp_path, "m1")
    assert any("missing '## Summary'" in e for e in errors)
    assert any("missing '## Tasks'" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    # assess is valid, implement is malformed
    assert len(errors) >= 2
    assert any("implement" in e for e in errors)


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
    errors = validate_golden_context(tmp_path, "m1")
    # Flat is valid, phase-level is malformed
    assert len(errors) >= 2
