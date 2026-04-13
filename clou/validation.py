"""Golden context structural validation (DB-12: Validation Tiers).

Validates golden context file structure at coordinator cycle boundaries.
Three validation tiers:

- **Structural** (compose.py): AST validation via graph.py — not in this module.
- **Checkpoint** (active/coordinator.md, status.md): Strict key-value parsing
  with required keys, enum validation, and consistency checks.
- **Narrative** (execution.md, decisions.md, assessment.md): Form-only —
  required section headers and valid status values.  Content quality is
  the quality gate's responsibility, not the orchestrator's.

Validation findings carry severity classification:
- ERROR: blocks progression — downstream consumers cannot understand the
  golden context without this information.
- WARNING: logged but does not block — cosmetic/formatting issues where
  comprehension is preserved.

Public API:
    validate_golden_context(project_dir, milestone, template) -> list[ValidationFinding]
    validate_delivery(milestone_dir, checkpoint_path, milestone) -> list[ValidationFinding]
    validate_readiness(clou_dir, milestone_dir, read_set, cycle_type, milestone) -> list[ValidationFinding]
    validate_artifact_form(content, form, rel) -> list[ValidationFinding]
    validate_checkpoint(content: str) -> list[ValidationFinding]
    validate_status_checkpoint(content: str, rel: str) -> list[ValidationFinding]
    errors_only(findings: list[ValidationFinding]) -> list[ValidationFinding]
    warnings_only(findings: list[ValidationFinding]) -> list[ValidationFinding]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from clou.golden_context import (
    PHASE_STATUSES as _PHASE_STATUSES,
    TASK_STATUSES as _TASK_STATUSES,
    VALID_CYCLE_OUTCOMES as _VALID_CYCLE_OUTCOMES,
    VALID_NEXT_STEPS as _VALID_NEXT_STEPS,
    VALID_STEPS as _VALID_STEPS,
)

if TYPE_CHECKING:
    from clou.harness import ArtifactForm, HarnessTemplate


class Severity(Enum):
    """Validation finding severity level."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """A single validation finding with severity classification.

    Attributes:
        severity: ERROR (blocks progression) or WARNING (log only).
        message: Human-readable description of the issue.
        path: Relative path to the file with the issue (e.g. "active/coordinator.md").
    """

    severity: Severity
    message: str
    path: str

    def __str__(self) -> str:
        """Render as the legacy string format for backward compatibility."""
        return f"{self.path} {self.message}"


# ---------------------------------------------------------------------------
# Helpers for filtering findings
# ---------------------------------------------------------------------------


def errors_only(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    """Return only ERROR-severity findings."""
    return [f for f in findings if f.severity == Severity.ERROR]


def warnings_only(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    """Return only WARNING-severity findings."""
    return [f for f in findings if f.severity == Severity.WARNING]


# Valid status values per file type.
# _TASK_STATUSES, _PHASE_STATUSES, _VALID_STEPS, _VALID_NEXT_STEPS are
# imported from golden_context.py (single source of truth).
_MILESTONE_STATUSES = frozenset({
    "pending", "in_progress", "completed", "blocked",
    # Inline heading variants: "### N. Name — current/sketch"
    "current", "sketch",
})
_TERMINAL_STATUSES = frozenset({"completed", "failed"})

# --- Checkpoint-tier validation (DB-12) ---
_CHECKPOINT_REQUIRED_KEYS = frozenset({"cycle", "next_step"})
_CHECKPOINT_OPTIONAL_KEYS = frozenset(
    {
        "step", "current_phase", "phase", "phases_completed", "phases_total",
        "validation_retries", "readiness_retries", "crash_retries",
        "staleness_count", "cycle_outcome",
        "valid_findings", "consecutive_zero_valid",
    }
)
_CHECKPOINT_ALIASES: dict[str, str] = {"phase": "current_phase"}


# ---------------------------------------------------------------------------
# Communication validation — LLM-Modulo (§9): verify agent outputs externally
# ---------------------------------------------------------------------------

#: Files whose absence means the cycle cannot proceed (structural).
#: These drive control flow directly — compose.py for agent dispatch,
#: checkpoint for cycle determination, status.md for phase tracking.
_STRUCTURAL_FILES = frozenset({
    "compose.py",
    "active/coordinator.md",
    "status.md",
})

#: Files that resolve under .clou/ (root-scoped) rather than
#: .clou/milestones/{milestone}/ (milestone-scoped). DB-18 adds memory.md.
_ROOT_SCOPED_FILES = frozenset({"project.md", "memory.md"})


def validate_delivery(
    milestone_dir: Path,
    checkpoint_path: Path,
    milestone: str,
) -> list[ValidationFinding]:
    """Verify the coordinator delivered its state after a cycle.

    The golden context is a blackboard (research-foundations §10).
    Stigmergy only works when messages arrive.  This function verifies
    message *delivery*, not message *content* — that is
    ``validate_golden_context``'s job.

    Checks:
    - checkpoint_path exists (coordinator wrote its state transfer)
    - status.md exists (coordinator wrote its progress journal)
    - Cross-validation: status.md next_step matches checkpoint next_step

    Finding paths are ``.clou/``-relative for consistency with
    ``validate_golden_context`` and the self-heal pipeline.
    """
    ms_prefix = f"milestones/{milestone}"
    findings: list[ValidationFinding] = []

    if not checkpoint_path.exists():
        findings.append(ValidationFinding(
            Severity.ERROR,
            "coordinator checkpoint not delivered — state transfer between cycles broken",
            f"{ms_prefix}/active/coordinator.md",
        ))

    status_path = milestone_dir / "status.md"
    if not status_path.exists():
        findings.append(ValidationFinding(
            Severity.ERROR,
            "milestone status not delivered — progress journal missing",
            f"{ms_prefix}/status.md",
        ))

    # Cross-validation: compare shared fields between checkpoint and
    # status.md.  Divergence means the state transfer is broken — one
    # file was written but the other wasn't (crash, permission failure,
    # or validation rejection mid-write).  Each mismatch is an ERROR.
    if checkpoint_path.exists() and status_path.exists():
        from clou.recovery import parse_checkpoint

        cp = parse_checkpoint(checkpoint_path.read_text())
        status_content = status_path.read_text()
        status_state = _section_text(status_content, "## Current State")
        if not status_state:
            for variant in ("## Current state", "## current state"):
                status_state = _section_text(status_content, variant)
                if status_state:
                    break

        def _status_field(field: str) -> str | None:
            m = re.search(rf"(?m)^{field}:\s*(.+)$", status_state)
            return m.group(1).strip() if m else None

        # next_step
        status_next = _status_field("next_step")
        if status_next and status_next != cp.next_step:
            findings.append(ValidationFinding(
                Severity.ERROR,
                f"status.md next_step '{status_next}' diverges from checkpoint "
                f"next_step '{cp.next_step}'",
                f"{ms_prefix}/active/coordinator.md",
            ))

        # cycle count
        status_cycle = _status_field("cycle")
        if status_cycle and status_cycle.isdigit():
            if int(status_cycle) != cp.cycle:
                findings.append(ValidationFinding(
                    Severity.ERROR,
                    f"status.md cycle {status_cycle} diverges from checkpoint "
                    f"cycle {cp.cycle}",
                    f"{ms_prefix}/active/coordinator.md",
                ))

        # current phase — when checkpoint has the default empty value the
        # field was absent, not intentionally blank.  Downgrade to WARNING
        # (the absent key is already warned about by validate_checkpoint);
        # only ERROR when both sides have real values that disagree.
        status_phase = _status_field("phase")
        if status_phase and not cp.current_phase:
            findings.append(ValidationFinding(
                Severity.WARNING,
                f"status.md has phase '{status_phase}' but checkpoint "
                f"current_phase is absent",
                f"{ms_prefix}/active/coordinator.md",
            ))
        elif status_phase and cp.current_phase and status_phase != cp.current_phase:
            findings.append(ValidationFinding(
                Severity.ERROR,
                f"status.md phase '{status_phase}' diverges from checkpoint "
                f"current_phase '{cp.current_phase}'",
                f"{ms_prefix}/active/coordinator.md",
            ))

        # phases_completed vs completed count in status table
        phase_table = _section_text(status_content, "## Phase Progress")
        if phase_table:
            completed_count = len(re.findall(
                r"(?m)^\|[^|]+\|\s*completed\s*\|", phase_table,
            ))
            if completed_count != cp.phases_completed:
                findings.append(ValidationFinding(
                    Severity.WARNING,
                    f"status.md shows {completed_count} completed phases but "
                    f"checkpoint has phases_completed={cp.phases_completed}",
                    f"{ms_prefix}/active/coordinator.md",
                ))

    return findings


def validate_readiness(
    clou_dir: Path,
    milestone_dir: Path,
    read_set: list[str],
    cycle_type: str,
    milestone: str,
) -> list[ValidationFinding]:
    """Verify the context a cycle needs actually exists before dispatch.

    The read_set from ``determine_next_cycle`` is the orchestrator's
    assumption about what files the coordinator will consume.  Each
    assumption is an untested claim about LLM-written artifacts.
    This function tests those claims (LLM-Modulo §9).

    Path resolution mirrors ``build_cycle_prompt``: paths starting with
    ``project.md`` or ``active/`` resolve under *clou_dir* (root-scoped);
    all others resolve under *milestone_dir*.

    Finding paths are ``.clou/``-relative for consistency with
    ``validate_golden_context`` and the self-heal pipeline.

    Severity:
    - ERROR for structural files (compose.py, checkpoint, status.md) —
      the cycle cannot proceed without them.
    - WARNING for narrative files — the coordinator adapts gracefully.

    Note: the read_set for ASSESS and EXIT includes files that are
    *created* during those cycles (assessment.md, handoff.md).  These
    emit non-blocking WARNINGs on first invocation, which is expected.
    """
    ms_prefix = f"milestones/{milestone}"
    findings: list[ValidationFinding] = []

    for rel_path in read_set:
        # Mirror prompts.py resolution: only project.md is root-scoped.
        root_scoped = rel_path in _ROOT_SCOPED_FILES
        if root_scoped:
            full_path = clou_dir / rel_path
        else:
            full_path = milestone_dir / rel_path
        if full_path.exists():
            continue
        severity = (
            Severity.ERROR
            if rel_path in _STRUCTURAL_FILES
            else Severity.WARNING
        )
        # .clou/-relative finding path.
        finding_path = rel_path if root_scoped else f"{ms_prefix}/{rel_path}"
        findings.append(ValidationFinding(
            severity,
            f"{cycle_type} cycle needs {rel_path} but it does not exist",
            finding_path,
        ))

    return findings


def validate_golden_context(
    project_dir: Path,
    milestone: str,
    template: HarnessTemplate | None = None,
) -> list[ValidationFinding]:
    """Validate golden context structure after a cycle.

    Returns findings (empty = valid). Only checks files that exist — the
    coordinator creates files as needed, so missing files are not errors.

    When *template* is provided, any file matching a key in
    ``template.artifact_forms`` is validated against its ``ArtifactForm``
    (DB-14).

    Phases with terminal status ("completed" or "failed") in status.md have
    ERROR findings on their execution.md downgraded to WARNING — they already
    passed assessment and should not block progression.
    """
    findings: list[ValidationFinding] = []
    clou_dir = project_dir / ".clou"
    milestone_dir = clou_dir / "milestones" / milestone

    # Coordinator checkpoint (active/coordinator.md)
    checkpoint = milestone_dir / "active" / "coordinator.md"
    if checkpoint.exists():
        findings += _validate_coordinator(checkpoint)

    # Read phase statuses from status.md for terminal-phase exemption.
    status_path = milestone_dir / "status.md"
    phase_statuses = _parse_phase_statuses(status_path)

    # Per-phase: execution.md — check phase subdirectories (the actual write path)
    # and the flat path for backwards compatibility.
    # The flat execution.md is not phase-specific — no exemption applied.
    execution_flat = milestone_dir / "execution.md"
    if execution_flat.exists():
        findings += _validate_execution(execution_flat)

    for execution_phase in sorted(milestone_dir.glob("phases/*/execution.md")):
        phase_findings = _validate_execution(execution_phase)
        phase_name = execution_phase.parent.name
        if phase_statuses.get(phase_name.lower()) in _TERMINAL_STATUSES:
            phase_findings = _downgrade_errors(phase_findings)
        findings += phase_findings

    # Per-milestone: decisions.md
    decisions = milestone_dir / "decisions.md"
    if decisions.exists():
        findings += _validate_decisions(decisions)

    # Per-milestone: assessment.md
    assessment = milestone_dir / "assessment.md"
    if assessment.exists():
        findings += _validate_assessment(assessment)

    # Per-milestone: status.md
    status = milestone_dir / "status.md"
    if status.exists():
        findings += _validate_status(status)

    # ArtifactForm-driven validation (DB-14) — any file with a form
    # in the template gets narrative-tier form checking.
    if template is not None:
        for artifact_name, form in template.artifact_forms.items():
            artifact_path = milestone_dir / f"{artifact_name}.md"
            if artifact_path.exists():
                findings += validate_artifact_form(
                    artifact_path.read_text(),
                    form,
                    _rel(artifact_path),
                )

    # Project-level: roadmap.md
    roadmap = clou_dir / "roadmap.md"
    if roadmap.exists():
        findings += _validate_roadmap(roadmap)

    return findings


# ---------------------------------------------------------------------------
# Per-file validators
# ---------------------------------------------------------------------------


def _validate_coordinator(path: Path) -> list[ValidationFinding]:
    """Validate active/coordinator.md checkpoint structure (DB-12 checkpoint tier).

    Strict key-value parsing: required keys must be present, enum fields
    validated, integer fields non-negative.  The markdown format is preserved
    for human legibility — only the parser becomes stricter.
    """
    return validate_checkpoint(path.read_text())


def validate_checkpoint(content: str) -> list[ValidationFinding]:
    """Checkpoint validation for active/coordinator.md.

    Extracts ``key: value`` pairs and validates:
    - Required keys present (``cycle``, ``next_step`` — the two fields that
      drive control flow in ``determine_next_cycle()``)
    - ``step`` is a valid cycle step (when present)
    - ``next_step`` is a valid next-step value
    - Integer fields are non-negative (when present)
    - ``phases_completed`` <= ``phases_total`` (when both are valid ints)

    Optional keys (``step``, ``current_phase``/``phase``, ``phases_completed``,
    ``phases_total``) produce WARNINGs when absent — ``parse_checkpoint()``
    handles them gracefully with defaults so their absence does not break
    control flow.

    Accepts ``phase`` as an alias for ``current_phase`` (agents commonly
    write this variant).
    """
    findings: list[ValidationFinding] = []
    prefix = "active/coordinator.md"

    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):\s*(.+)$", content):
        fields[match.group(1)] = match.group(2).strip()

    # Resolve aliases (e.g. phase -> current_phase) for downstream checks.
    for alias, canonical in _CHECKPOINT_ALIASES.items():
        if alias in fields and canonical not in fields:
            fields[canonical] = fields[alias]

    # Required keys — ERROR (control-flow breaking).
    missing_required = _CHECKPOINT_REQUIRED_KEYS - fields.keys()
    if missing_required:
        for key in sorted(missing_required):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message=f"missing required key '{key}'",
                    path=prefix,
                )
            )
        return findings

    # Optional keys — WARNING (parse_checkpoint defaults them).
    missing_optional = _CHECKPOINT_OPTIONAL_KEYS - fields.keys()
    # Don't warn about aliases that resolved.
    missing_optional -= {v for _, v in _CHECKPOINT_ALIASES.items() if v in fields}
    # Don't warn about the alias side when the canonical is present.
    missing_optional -= set(_CHECKPOINT_ALIASES.keys())
    for key in sorted(missing_optional):
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                message=f"optional key '{key}' missing (defaulted by parser)",
                path=prefix,
            )
        )

    # Enum: step (only validate when present)
    if "step" in fields and fields["step"] not in _VALID_STEPS:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message=(
                    f"invalid step '{fields['step']}' "
                    f"(expected one of: {', '.join(sorted(_VALID_STEPS))})"
                ),
                path=prefix,
            )
        )

    # Enum: next_step
    if fields["next_step"] not in _VALID_NEXT_STEPS:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message=(
                    f"invalid next_step '{fields['next_step']}' "
                    f"(expected one of: {', '.join(sorted(_VALID_NEXT_STEPS))})"
                ),
                path=prefix,
            )
        )

    # Enum: cycle_outcome (only validate when present -- backward-compatible)
    if "cycle_outcome" in fields and fields["cycle_outcome"] not in _VALID_CYCLE_OUTCOMES:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                message=(
                    f"invalid cycle_outcome '{fields['cycle_outcome']}' "
                    f"(expected one of: {', '.join(sorted(_VALID_CYCLE_OUTCOMES))})"
                ),
                path=prefix,
            )
        )

    # Integer fields (only validate when present)
    int_errors = False
    for key in (
        "cycle", "phases_completed", "phases_total",
        "validation_retries", "readiness_retries", "crash_retries",
        "staleness_count",
    ):
        if key not in fields:
            continue
        try:
            val = int(fields[key])
            if val < 0:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        message=f"'{key}' must be non-negative (got {val})",
                        path=prefix,
                    )
                )
                int_errors = True
        except ValueError:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message=f"'{key}' must be an integer (got '{fields[key]}')",
                    path=prefix,
                )
            )
            int_errors = True

    # Consistency: phases_completed <= phases_total
    if (
        not int_errors
        and "phases_completed" in fields
        and "phases_total" in fields
    ):
        completed = int(fields["phases_completed"])
        total = int(fields["phases_total"])
        if completed > total:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message=(
                        f"phases_completed ({completed}) "
                        f"exceeds phases_total ({total})"
                    ),
                    path=prefix,
                )
            )

    return findings


def _validate_execution(path: Path) -> list[ValidationFinding]:
    """Validate execution.md per-phase file structure.

    Protocol tools (clou_write_execution) guarantee correct structure.
    These checks are defense-in-depth for files written outside tools
    (e.g. worker agents, legacy paths).  Severity is WARNING — format
    issues in execution.md should not block milestone progression.
    """
    rel = _rel(path)
    findings: list[ValidationFinding] = []
    content = path.read_text()

    # ## Summary with status: field — WARNING (defense-in-depth)
    if "## Summary" not in content:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                message="missing '## Summary'",
                path=rel,
            )
        )
    else:
        summary_block = _section_text(content, "## Summary")
        if not re.search(r"(?m)^status:", summary_block):
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message="'## Summary' missing 'status:' field",
                    path=rel,
                )
            )

    # ## Tasks with at least one ### T<N>: entry — WARNING (defense-in-depth).
    # Task entries may appear under ## Summary (common LLM variant) or
    # under ## Tasks (tool-written format).  Check for ### T<N>: anywhere
    # as a fallback if ## Tasks is missing.
    has_tasks_section = "## Tasks" in content
    has_task_entries = bool(re.search(r"(?m)^### T\d+:", content))

    if not has_tasks_section and not has_task_entries:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                message="missing '## Tasks' section and no '### T<N>:' entries found",
                path=rel,
            )
        )
    elif has_tasks_section:
        tasks_block = _section_text(content, "## Tasks")
        task_headers = re.findall(r"(?m)^### T\d+:", tasks_block)
        if not task_headers:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message="'## Tasks' has no '### T<N>:' entries",
                    path=rel,
                )
            )
        else:
            # Each task must have **Status:** with valid value — WARNING (formatting)
            findings += _check_task_statuses(tasks_block, rel)

    return findings


def _validate_decisions(path: Path) -> list[ValidationFinding]:
    """Validate decisions.md per-milestone file structure."""
    rel = _rel(path)
    findings: list[ValidationFinding] = []
    content = path.read_text()

    # Must contain at least one ## Cycle section — ERROR (structural)
    cycle_sections = re.findall(r"(?m)^## Cycle", content)
    if not cycle_sections:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing '## Cycle' section",
                path=rel,
            )
        )
        return findings

    # Each cycle section must have at least one decision entry — WARNING
    # (non-ASSESS sections without entries are a formatting issue,
    # comprehension is preserved)
    cycle_blocks = _split_sections(content, "## Cycle")
    for i, block in enumerate(cycle_blocks, 1):
        has_entry = bool(re.search(r"(?m)^### (Accepted|Overridden|Tradeoff):", block))
        if not has_entry:
            # ASSESS sections with zero findings are valid (convergence).
            first_line = block.strip().split("\n", 1)[0].lower()
            if (
                "assess" in first_line
                or "quality gate" in first_line
                or "brutalist" in first_line
            ):
                continue
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=(
                        f"Cycle section {i} has no "
                        f"'### Accepted:', '### Overridden:', or '### Tradeoff:' entry"
                    ),
                    path=rel,
                )
            )

    return findings


def _validate_assessment(path: Path) -> list[ValidationFinding]:
    """Validate assessment.md per-milestone file structure."""
    rel = _rel(path)
    findings: list[ValidationFinding] = []
    content = path.read_text()

    # Must have ## Summary with status: field — ERROR (structural)
    if "## Summary" not in content:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing '## Summary'",
                path=rel,
            )
        )
        return findings

    summary_block = _section_text(content, "## Summary")
    status_match = re.search(r"(?m)^status:\s*(\S+)", summary_block)
    if not status_match:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="'## Summary' missing 'status:' field",
                path=rel,
            )
        )
        return findings

    # "blocked" is a valid terminal state — no findings required.
    # "degraded" proceeds like "completed" — findings are expected.
    if status_match.group(1) == "blocked":
        return findings

    # Must have ## Findings section — ERROR (structural)
    if "## Findings" not in content:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing '## Findings'",
                path=rel,
            )
        )
        return findings

    # Each finding must have **Severity:** and **Finding:** — WARNING (formatting)
    findings_block = _section_text(content, "## Findings")
    finding_headers = re.findall(r"(?m)^### F\d+:", findings_block)
    if finding_headers:
        finding_blocks = _split_sections(findings_block, r"### F\d+:")
        for k, block in enumerate(finding_blocks, 1):
            if not re.search(r"\*\*Severity:\*\*", block):
                findings.append(
                    ValidationFinding(
                        severity=Severity.WARNING,
                        message=f"finding {k} missing '**Severity:**'",
                        path=rel,
                    )
                )
            if not re.search(r"\*\*Finding:\*\*", block):
                findings.append(
                    ValidationFinding(
                        severity=Severity.WARNING,
                        message=f"finding {k} missing '**Finding:**'",
                        path=rel,
                    )
                )

    return findings


def _validate_status(path: Path) -> list[ValidationFinding]:
    """Validate status.md per-milestone file structure (DB-12 checkpoint tier).

    Strict structured parsing: Current State section must have key-value
    pairs, Phase Progress table must have rows with valid status values.
    """
    return validate_status_checkpoint(path.read_text(), _rel(path))


def validate_status_checkpoint(
    content: str, rel: str = "status.md"
) -> list[ValidationFinding]:
    """Strict status.md validation.

    Validates:
    - Current State section exists with ``phase:`` and ``cycle:`` keys
    - Phase Progress section exists with at least one table row
    - Phase status values in table are from the valid set

    Returns a list of findings (empty = valid).
    """
    findings: list[ValidationFinding] = []

    # Current State section with key-value pairs — ERROR (structural)
    if not re.search(r"(?mi)current state", content):
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing 'Current State' section",
                path=rel,
            )
        )
        return findings

    state_block = _section_text(content, "## Current State")
    if not state_block:
        # Try case-insensitive match for the section header
        for variant in ("## Current state", "## current state"):
            state_block = _section_text(content, variant)
            if state_block:
                break

    if state_block:
        if not re.search(r"(?m)^phase:", state_block):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message="'Current State' missing 'phase:' field",
                    path=rel,
                )
            )
        if not re.search(r"(?m)^cycle:", state_block):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message="'Current State' missing 'cycle:' field",
                    path=rel,
                )
            )

    # Phase Progress table — ERROR (structural, missing section / no rows)
    if not re.search(r"(?mi)phase progress", content):
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing 'Phase Progress' section",
                path=rel,
            )
        )
        return findings

    # Look for markdown table rows (lines with | separators, not header/divider)
    progress_block = _section_text(content, "## Phase Progress")
    if not progress_block:
        for variant in ("## Phase progress", "## phase progress"):
            progress_block = _section_text(content, variant)
            if progress_block:
                break

    if progress_block:
        table_rows = [
            line
            for line in progress_block.strip().splitlines()
            if "|" in line
            and not re.match(r"^\s*\|[-\s|:]+\|\s*$", line)  # skip divider
            and not re.match(r"^\s*\|.*Phase.*Status", line, re.I)  # skip header
        ]
        if not table_rows:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message="'Phase Progress' has no table rows",
                    path=rel,
                )
            )
        else:
            # Validate status values in table cells — WARNING (invalid value,
            # not missing section)
            for row in table_rows:
                cells = [c.strip() for c in row.split("|") if c.strip()]
                # Status is typically the second column
                if len(cells) >= 2:
                    status_val = cells[1].strip().lower()
                    if status_val and status_val not in _PHASE_STATUSES:
                        findings.append(
                            ValidationFinding(
                                severity=Severity.WARNING,
                                message=(
                                    f"invalid phase status '{cells[1].strip()}'"
                                    f" (expected one of: "
                                    f"{', '.join(sorted(_PHASE_STATUSES))})"
                                ),
                                path=rel,
                            )
                        )

    return findings


def _validate_roadmap(path: Path) -> list[ValidationFinding]:
    """Validate roadmap.md project-level file structure."""
    rel = _rel(path)
    findings: list[ValidationFinding] = []
    content = path.read_text()

    # Missing ## Milestones — ERROR (structural)
    if "## Milestones" not in content:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing '## Milestones'",
                path=rel,
            )
        )
        return findings

    milestones_block = _section_text(content, "## Milestones")
    entries = re.findall(r"(?m)^### \d+\. ", milestones_block)
    # No milestone entries — ERROR (structural)
    if not entries:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="'## Milestones' has no '### N. name' entries",
                path=rel,
            )
        )
        return findings

    # Each milestone entry needs a status — either **Status:** in body or
    # inline "— status" in the heading (e.g. "### 1. Name — completed").
    # Both are WARNING-severity (formatting, not structural).
    entry_blocks = _split_sections(milestones_block, r"### \d+\.")
    # Extract the heading from each split boundary rather than a separate
    # regex, so heading and block indices always align.
    heading_starts = list(re.finditer(r"(?m)^### \d+\.", milestones_block))
    for j, block in enumerate(entry_blocks, 1):
        # Try **Status:** in body first.
        match = re.search(r"\*\*Status:\*\*\s*(\S+)", block)
        if match:
            status_val = match.group(1)
        else:
            # Try inline "— status" in heading.  Only match em-dash (—)
            # or en-dash (–), not plain hyphen (-) which appears in names.
            heading_match = heading_starts[j - 1] if j - 1 < len(heading_starts) else None
            if heading_match:
                heading_end = heading_match.end()
                heading_line = milestones_block[heading_match.start():].split("\n", 1)[0]
            else:
                heading_line = ""
            inline = re.search(r"[—–]\s+(\S+)\s*$", heading_line)
            status_val = inline.group(1) if inline else None

        if not status_val:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=f"milestone entry {j} missing status "
                    f"(expected '**Status:**' or inline '— status')",
                    path=rel,
                )
            )
        elif status_val not in _MILESTONE_STATUSES:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=(
                        f"milestone entry {j} has invalid status "
                        f"'{status_val}' "
                        f"(expected one of: {', '.join(sorted(_MILESTONE_STATUSES))})"
                    ),
                    path=rel,
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    """Return a short relative path for error messages.

    Walks up from path looking for ``.clou`` to produce a path relative to it.
    Falls back to the file name.
    """
    parts = path.parts
    for i, part in enumerate(parts):
        if part == ".clou":
            return "/".join(parts[i + 1 :])
    return path.name


def _section_text(content: str, header: str) -> str:
    """Extract text from *header* until the next section of equal or higher level."""
    level = len(header) - len(header.lstrip("#"))
    pattern = re.escape(header)
    match = re.search(rf"(?m)^{pattern}", content)
    if not match:
        return ""
    start = match.end()
    # Find next header at same or higher level
    next_header = re.search(rf"(?m)^#{{1,{level}}} ", content[start:])
    if next_header:
        return content[start : start + next_header.start()]
    return content[start:]


def _split_sections(content: str, header_pattern: str) -> list[str]:
    """Split content into blocks starting at each *header_pattern* match.

    Returns the text *after* each header until the next header of the same
    pattern (or end of string).
    """
    splits = list(re.finditer(rf"(?m)^{header_pattern}", content))
    blocks: list[str] = []
    for i, m in enumerate(splits):
        start = m.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        blocks.append(content[start:end])
    return blocks


def _parse_phase_statuses(status_path: Path) -> dict[str, str]:
    """Read the Phase Progress table from status.md.

    Returns a mapping of ``{phase_name: status_value}`` with both keys
    and values lowercased.  Returns an empty dict when the file does not
    exist or has no parseable Phase Progress table.
    """
    if not status_path.exists():
        return {}
    content = status_path.read_text()
    progress_block = _section_text(content, "## Phase Progress")
    if not progress_block:
        for variant in ("## Phase progress", "## phase progress"):
            progress_block = _section_text(content, variant)
            if progress_block:
                break
    if not progress_block:
        return {}
    result: dict[str, str] = {}
    for line in progress_block.strip().splitlines():
        if "|" not in line:
            continue
        # Skip header and divider rows.
        if re.match(r"^\s*\|[-\s|:]+\|\s*$", line):
            continue
        if re.match(r"^\s*\|.*Phase.*Status", line, re.I):
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) >= 2:
            phase_name = cells[0].strip().lower()
            status_val = cells[1].strip().lower()
            result[phase_name] = status_val
    return result


def _downgrade_errors(
    findings: list[ValidationFinding],
) -> list[ValidationFinding]:
    """Downgrade ERROR findings to WARNING, preserving message and path.

    Used for terminal phases whose execution.md should not block progression.
    """
    result: list[ValidationFinding] = []
    for f in findings:
        if f.severity == Severity.ERROR:
            result.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=f.message,
                    path=f.path,
                )
            )
        else:
            result.append(f)
    return result


def _check_task_statuses(
    tasks_block: str, rel: str
) -> list[ValidationFinding]:
    """Check each task entry in ## Tasks for a valid **Status:** field.

    Missing or invalid task statuses are WARNING severity — formatting
    issues that don't prevent comprehension of the golden context.
    """
    findings: list[ValidationFinding] = []
    task_blocks = _split_sections(tasks_block, r"### T\d+:")
    for k, block in enumerate(task_blocks, 1):
        match = re.search(r"\*\*Status:\*\*\s*(\S+)", block)
        if not match:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=f"task {k} missing '**Status:**'",
                    path=rel,
                )
            )
        elif match.group(1) not in _TASK_STATUSES:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=(
                        f"task {k} has invalid status "
                        f"'{match.group(1)}' "
                        f"(expected one of: {', '.join(sorted(_TASK_STATUSES))})"
                    ),
                    path=rel,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# ArtifactForm-driven validation (DB-14)
# ---------------------------------------------------------------------------

#: Known anti-pattern regexes keyed by description substring.
#: When an ArtifactForm.anti_patterns entry contains a key phrase,
#: the corresponding regex is used.  This keeps forms declarative
#: (string descriptions) while enforcement is mechanical.
_ANTI_PATTERN_MATCHERS: list[tuple[str, re.Pattern[str], str]] = [
    # Only fire when a file path is the *subject* of the criterion (appears
    # right after the bullet/When prefix), not when a behavioural criterion
    # incidentally mentions a path as a location.
    ("file path", re.compile(r"^[-*]?\s*(?:[Ww]hen\s+)?[a-zA-Z0-9_.-]+/[a-zA-Z0-9_/.-]+\.(py|ts|tsx|js|jsx|md|css|html|json|yaml|yml|toml|go|rs|sh)\b"), "file path in criterion"),
    ("implementation", re.compile(r"\b(class|module|function|method|widget)\s+[A-Z]"), "implementation artifact name in criterion"),
    ("implementation", re.compile(r"\b(extract|refactor|build|implement|create|add)\s+(a\s+|the\s+)?\w+", re.IGNORECASE), "implementation verb as criterion action"),
    ("file inspection", re.compile(r"\b(file|module|class|directory|folder)\s+(exists?|contains?|has)\b", re.IGNORECASE), "criterion verifiable by file inspection"),
]

#: Known matcher keys — used by ``validate_template`` to warn on
#: anti-pattern descriptions that don't map to any active matcher.
ANTI_PATTERN_KEYS: frozenset[str] = frozenset(
    key for key, _, _ in _ANTI_PATTERN_MATCHERS
)


def _template_to_regex(template: str) -> re.Pattern[str]:
    """Convert a criterion_template like ``"When {trigger}, {observable_outcome}"``
    into a loose matching regex.

    Placeholder tokens ``{...}`` become ``.+`` (match any text).
    Leading list markers (``- `` or ``* ``) are allowed.
    Match is case-insensitive.
    """
    # Escape the literal parts, then replace escaped placeholders.
    escaped = re.escape(template)
    # \{...\} from escaping → .+
    pattern = re.sub(r"\\{[^}]*\\}", ".+", escaped)
    return re.compile(rf"(?i)^[-*]?\s*{pattern}")


def validate_artifact_form(
    content: str,
    form: ArtifactForm,
    rel: str,
) -> list[ValidationFinding]:
    """Validate content against an ArtifactForm (DB-14, narrative tier).

    Pure function — no file I/O.  Used by both ``validate_golden_context``
    (cycle-boundary) and the PostToolUse hook (write-time).

    All findings are WARNING severity — form violations don't block
    progression but give agents immediate feedback.
    """
    findings: list[ValidationFinding] = []
    stripped = content.strip()

    if not stripped:
        findings.append(
            ValidationFinding(
                severity=Severity.WARNING,
                message="artifact is empty — no content defined",
                path=rel,
            )
        )
        return findings

    # Section checks.
    if form.sections:
        for section in form.sections:
            # Accept ## or ### or #### prefix.
            if not re.search(rf"(?m)^#{{1,4}}\s+{re.escape(section)}", stripped):
                findings.append(
                    ValidationFinding(
                        severity=Severity.WARNING,
                        message=f"missing required section '{section}'",
                        path=rel,
                    )
                )

    # Criterion template checks.
    #
    # Only bullet/list lines are treated as criteria — preamble text
    # (paragraphs, notes) is ignored to avoid false positives.
    if form.criterion_template:
        criterion_re = _template_to_regex(form.criterion_template)
        criteria_lines = [
            line.strip()
            for line in stripped.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and (line.strip().startswith(("- ", "* ", "When "))
                 or line.strip().startswith(("when ",)))
        ]

        if not criteria_lines:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message="no criteria lines found",
                    path=rel,
                )
            )
        else:
            for line in criteria_lines:
                if not criterion_re.match(line):
                    findings.append(
                        ValidationFinding(
                            severity=Severity.WARNING,
                            message=(
                                f"criterion does not match "
                                f"'{form.criterion_template}' template: "
                                f"'{line[:80]}'"
                            ),
                            path=rel,
                        )
                    )

    # Anti-pattern checks.
    if form.anti_patterns:
        criteria_lines = criteria_lines if form.criterion_template else [
            line.strip()
            for line in stripped.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and (line.strip().startswith(("- ", "* ", "When "))
                 or line.strip().startswith(("when ",)))
        ]
        for line in criteria_lines:
            for key, pattern, desc in _ANTI_PATTERN_MATCHERS:
                # Fire if any declared anti-pattern description contains this key.
                if any(key in ap.lower() for ap in form.anti_patterns):
                    if pattern.search(line):
                        findings.append(
                            ValidationFinding(
                                severity=Severity.WARNING,
                                message=f"{desc}: '{line[:80]}'",
                                path=rel,
                            )
                        )

    return findings
