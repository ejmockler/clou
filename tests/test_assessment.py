"""Tests for clou/assessment.py — schema + render + parse round-trip."""

from __future__ import annotations

from clou.assessment import (
    AssessmentForm,
    AssessmentSummary,
    Classification,
    Finding,
    ToolInvocation,
    VALID_CLASSIFICATIONS,
    VALID_SEVERITIES,
    VALID_STATUSES,
    merge_classifications,
    parse_assessment,
    render_assessment,
)


# ---------------------------------------------------------------------------
# Canonical render
# ---------------------------------------------------------------------------


def test_render_minimal_completed() -> None:
    form = AssessmentForm(
        phase_name="build_feature",
        summary=AssessmentSummary(
            status="completed",
            tools_invoked=1,
            findings_total=0,
            phase_evaluated="build_feature",
        ),
    )
    out = render_assessment(form)
    assert "# Assessment: build_feature" in out
    assert "## Summary" in out
    assert "status: completed" in out
    assert "tools_invoked: 1" in out
    assert "findings: 0 total, 0 critical, 0 major, 0 minor" in out
    # No empty Findings section when there are no findings.
    assert "## Findings" not in out


def test_render_with_findings_produces_findings_section() -> None:
    form = AssessmentForm(
        phase_name="p1",
        summary=AssessmentSummary(
            status="completed",
            tools_invoked=1,
            findings_total=2,
            findings_critical=0,
            findings_major=1,
            findings_minor=1,
            phase_evaluated="p1",
        ),
        tools=(
            ToolInvocation(tool="roast_codebase", status="invoked"),
        ),
        findings=(
            Finding(
                number=1,
                title="Flag meaning is off",
                severity="major",
                source_tool="roast_codebase",
                source_models=("CODEX",),
                affected_files=("src/cli.ts",),
                finding_text='"spawned means called spawnAsync"',
                context="found during codebase roast",
            ),
            Finding(
                number=2,
                title="Label escape order",
                severity="minor",
                source_tool="roast_codebase",
                affected_files=("src/metrics/index.ts",),
            ),
        ),
    )
    out = render_assessment(form)
    assert "## Findings" in out
    assert "### F1: Flag meaning is off" in out
    assert "**Severity:** major" in out
    assert "**Source tool:** roast_codebase" in out
    assert "**Source models:** CODEX" in out
    assert "  - src/cli.ts" in out
    assert "### F2: Label escape order" in out
    assert "**Severity:** minor" in out


def test_render_degraded_includes_internal_reviewers_and_gate_error() -> None:
    form = AssessmentForm(
        phase_name="p1",
        summary=AssessmentSummary(
            status="degraded",
            tools_invoked=0,
            findings_total=0,
            phase_evaluated="p1",
            internal_reviewers=2,
            gate_error="quota exhausted",
        ),
    )
    out = render_assessment(form)
    assert "status: degraded" in out
    assert "internal_reviewers: 2" in out
    assert "gate_error: quota exhausted" in out


def test_render_with_classifications_indexes_by_finding_title() -> None:
    form = AssessmentForm(
        phase_name="p1",
        summary=AssessmentSummary(
            status="completed",
            tools_invoked=1,
            findings_total=1,
            findings_major=1,
            phase_evaluated="p1",
        ),
        findings=(
            Finding(
                number=1,
                title="Something",
                severity="major",
            ),
        ),
        classifications=(
            Classification(
                finding_number=1,
                classification="valid",
                action="Fix at src/x.py",
                reasoning="breaks invariant",
            ),
        ),
    )
    out = render_assessment(form)
    assert "## Classifications" in out
    # Title sourced from findings, not repeated by evaluator input.
    assert "### F1: Something" in out
    assert "**Classification:** valid" in out
    assert "**Action:** Fix at src/x.py" in out


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_canonical_round_trip_preserves_form() -> None:
    form = AssessmentForm(
        phase_name="build_feature",
        summary=AssessmentSummary(
            status="completed",
            tools_invoked=2,
            findings_total=2,
            findings_critical=1,
            findings_major=1,
            phase_evaluated="build_feature",
        ),
        tools=(
            ToolInvocation(tool="roast", domain="codebase", status="invoked"),
            ToolInvocation(tool="roast", domain="security", status="invoked"),
        ),
        findings=(
            Finding(
                number=1,
                title="Leak",
                severity="critical",
                source_tool="roast",
                source_models=("CODEX", "CLAUDE"),
                affected_files=("src/a.ts", "src/b.ts"),
                finding_text="leak at line 42",
                context="boundary function",
            ),
            Finding(
                number=2,
                title="Nit",
                severity="major",
                source_tool="roast",
            ),
        ),
        classifications=(
            Classification(
                finding_number=1,
                classification="security",
                action="Patch",
                reasoning="disclosure",
            ),
        ),
    )
    rendered = render_assessment(form)
    reparsed = parse_assessment(rendered)

    assert reparsed.phase_name == form.phase_name
    assert reparsed.summary == form.summary
    assert len(reparsed.findings) == len(form.findings)
    assert reparsed.findings[0].title == "Leak"
    assert reparsed.findings[0].severity == "critical"
    assert reparsed.findings[0].source_models == ("CODEX", "CLAUDE")
    assert reparsed.findings[0].affected_files == ("src/a.ts", "src/b.ts")
    assert reparsed.classifications == form.classifications


# ---------------------------------------------------------------------------
# Drift-tolerant parsing
# ---------------------------------------------------------------------------


_PHASE_ORGANIZED_EXAMPLE = """\
# Assessment: Layer 1 Cycle 2 Rework

## Classification Summary (Cycle 2 evaluator counts)

**5 findings evaluated across 2 phases.**

## Summary

status: completed
tools_invoked: 2
findings: 3 total, 0 critical, 2 major, 1 minor
phase_evaluated: instrument_cli_spawn, instrument_debate_module

## Phase: instrument_cli_spawn

### F1: Metric label escape order
**Severity:** major
**Source tool:** roast_codebase
**Source models:** CODEX
**Affected files:**
  - src/metrics/index.ts
**Finding:** "escape order wrong"
**Context:** label-value pipeline

### F2: Spawned flag misnamed
**Severity:** major
**Source tool:** roast_codebase
**Affected files:**
  - src/cli/spawn.ts

## Phase: instrument_debate_module

### F3: Debug-level proposition logging
**Severity:** minor
**Source tool:** roast_security
**Affected files:**
  - src/debate/round.ts
**Finding:** "raw position content logged"

## Positive verification (claims CONFIRMED by critics)

- safeMetric wrapper pattern used consistently
"""


def test_parse_accepts_phase_organized_drift() -> None:
    """``## Phase: X`` subsections with F-entries are accepted."""
    form = parse_assessment(_PHASE_ORGANIZED_EXAMPLE)
    assert form.phase_name == "Layer 1 Cycle 2 Rework"
    assert form.summary.status == "completed"
    assert form.summary.findings_total == 3
    assert len(form.findings) == 3
    # F1 and F2 tagged with their ## Phase: header.
    assert form.findings[0].phase == "instrument_cli_spawn"
    assert form.findings[1].phase == "instrument_cli_spawn"
    assert form.findings[2].phase == "instrument_debate_module"
    # Severities carried through.
    assert form.findings[0].severity == "major"
    assert form.findings[2].severity == "minor"


def test_parse_drifted_renders_to_canonical() -> None:
    """Re-rendering a parsed drift produces canonical ``## Findings`` form."""
    form = parse_assessment(_PHASE_ORGANIZED_EXAMPLE)
    canonical = render_assessment(form)
    # Canonical structure.
    assert "## Findings" in canonical
    # Drifted section names are gone.
    assert "## Phase: instrument_cli_spawn" not in canonical
    assert "## Phase: instrument_debate_module" not in canonical
    assert "## Positive verification" not in canonical
    # But phase tag preserved per-finding.
    assert "**Phase:** instrument_cli_spawn" in canonical
    assert "**Phase:** instrument_debate_module" in canonical


def test_parse_canonical_has_no_phase_tags_when_single_phase() -> None:
    """Single-phase canonical assessment: findings carry no per-finding phase tag."""
    single_phase = """\
# Assessment: impl

## Summary
status: completed
tools_invoked: 1
findings: 1 total, 0 critical, 1 major, 0 minor
phase_evaluated: impl

## Findings

### F1: Something
**Severity:** major
**Source tool:** roast
"""
    form = parse_assessment(single_phase)
    assert len(form.findings) == 1
    assert form.findings[0].phase is None


def test_parse_missing_summary_produces_default_completed() -> None:
    """Totally missing summary → default values, not crash."""
    minimal = "# Assessment: x\n"
    form = parse_assessment(minimal)
    assert form.phase_name == "x"
    assert form.summary.status == "completed"
    assert form.summary.findings_total == 0


def test_parse_blocked_status_round_trips() -> None:
    blocked = """\
# Assessment: p1

## Summary
status: blocked
tools_invoked: 0
findings: 0 total, 0 critical, 0 major, 0 minor
phase_evaluated: p1
gate_error: blocked by user
"""
    form = parse_assessment(blocked)
    assert form.summary.status == "blocked"
    assert form.summary.gate_error == "blocked by user"


def test_parse_affected_files_bullet_list() -> None:
    body = """\
# Assessment: p1

## Summary
status: completed
tools_invoked: 1
findings: 1 total, 0 critical, 1 major, 0 minor
phase_evaluated: p1

## Findings

### F1: Stuff
**Severity:** major
**Affected files:**
  - src/a.ts
  - src/b.ts
  - src/c.ts
**Finding:** "x"
"""
    form = parse_assessment(body)
    assert len(form.findings) == 1
    assert form.findings[0].affected_files == (
        "src/a.ts", "src/b.ts", "src/c.ts",
    )


# ---------------------------------------------------------------------------
# Merge classifications
# ---------------------------------------------------------------------------


def test_merge_classifications_adds_new_entries() -> None:
    form = AssessmentForm(
        phase_name="p1",
        summary=AssessmentSummary(
            status="completed",
            tools_invoked=1,
            findings_total=2,
            findings_major=2,
            phase_evaluated="p1",
        ),
        findings=(
            Finding(number=1, title="A", severity="major"),
            Finding(number=2, title="B", severity="major"),
        ),
    )
    merged = merge_classifications(
        form,
        [
            Classification(
                finding_number=1, classification="valid",
                action="fix", reasoning="breaks invariant",
            ),
            Classification(
                finding_number=2, classification="noise",
                reasoning="style only",
            ),
        ],
    )
    assert len(merged.classifications) == 2
    assert {c.finding_number for c in merged.classifications} == {1, 2}


def test_merge_classifications_last_writer_wins_per_finding() -> None:
    form = AssessmentForm(
        phase_name="p1",
        summary=AssessmentSummary(
            status="completed",
            tools_invoked=1,
            findings_total=1,
            phase_evaluated="p1",
        ),
        classifications=(
            Classification(
                finding_number=1, classification="noise",
                reasoning="old",
            ),
        ),
    )
    merged = merge_classifications(
        form,
        [Classification(
            finding_number=1, classification="valid",
            action="fix", reasoning="new",
        )],
    )
    assert len(merged.classifications) == 1
    c = merged.classifications[0]
    assert c.classification == "valid"
    assert c.reasoning == "new"


# ---------------------------------------------------------------------------
# Enum integrity
# ---------------------------------------------------------------------------


def test_enum_sets_are_the_expected_shape() -> None:
    assert set(VALID_SEVERITIES) == {"critical", "major", "minor"}
    assert "completed" in VALID_STATUSES
    assert "degraded" in VALID_STATUSES
    assert "blocked" in VALID_STATUSES
    assert "valid" in VALID_CLASSIFICATIONS
    assert "security" in VALID_CLASSIFICATIONS
