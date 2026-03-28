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
    validate_golden_context(project_dir, milestone) -> list[ValidationFinding]
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
_TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
_MILESTONE_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked"})
_PHASE_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
_TERMINAL_STATUSES = frozenset({"completed", "failed"})

# --- Checkpoint-tier validation (DB-12) ---
# These fields drive the orchestrator's control flow.  Strict parsing
# prevents silent state corruption in determine_next_cycle().

_CHECKPOINT_REQUIRED_KEYS = frozenset(
    {"cycle", "step", "next_step", "current_phase", "phases_completed", "phases_total"}
)
_VALID_STEPS = frozenset({"PLAN", "EXECUTE", "ASSESS", "VERIFY", "EXIT"})
_VALID_NEXT_STEPS = frozenset(
    {
        "PLAN",
        "EXECUTE",
        "EXECUTE (rework)",
        "EXECUTE (additional verification)",
        "ASSESS",
        "VERIFY",
        "EXIT",
        "COMPLETE",
        "none",
    }
)


def validate_golden_context(
    project_dir: Path, milestone: str
) -> list[ValidationFinding]:
    """Validate golden context structure after a cycle.

    Returns findings (empty = valid). Only checks files that exist — the
    coordinator creates files as needed, so missing files are not errors.

    Phases with terminal status ("completed" or "failed") in status.md have
    ERROR findings on their execution.md downgraded to WARNING — they already
    passed assessment and should not block progression.
    """
    findings: list[ValidationFinding] = []
    clou_dir = project_dir / ".clou"
    milestone_dir = clou_dir / "milestones" / milestone

    # Coordinator checkpoint (active/coordinator.md)
    checkpoint = clou_dir / "active" / "coordinator.md"
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
    """Strict checkpoint validation for active/coordinator.md.

    Extracts ``key: value`` pairs and validates:
    - All required keys present
    - ``step`` is a valid cycle step
    - ``next_step`` is a valid next-step value
    - ``cycle``, ``phases_completed``, ``phases_total`` are non-negative ints
    - ``phases_completed`` <= ``phases_total`` (when both are valid ints)

    Returns a list of findings (empty = valid).
    All checkpoint issues are ERROR severity — these drive control flow.
    """
    findings: list[ValidationFinding] = []
    prefix = "active/coordinator.md"

    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):\s*(.+)$", content):
        fields[match.group(1)] = match.group(2).strip()

    # Required keys
    missing = _CHECKPOINT_REQUIRED_KEYS - fields.keys()
    if missing:
        for key in sorted(missing):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message=f"missing required key '{key}'",
                    path=prefix,
                )
            )
        # Can't validate further without required keys.
        return findings

    # Enum: step
    if fields["step"] not in _VALID_STEPS:
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

    # Integer fields
    int_errors = False
    for key in ("cycle", "phases_completed", "phases_total"):
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
    if not int_errors:
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
    """Validate execution.md per-phase file structure."""
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
    else:
        summary_block = _section_text(content, "## Summary")
        if not re.search(r"(?m)^status:", summary_block):
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    message="'## Summary' missing 'status:' field",
                    path=rel,
                )
            )

    # Must have ## Tasks with at least one ### T<N>: entry — ERROR (structural)
    if "## Tasks" not in content:
        findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                message="missing '## Tasks'",
                path=rel,
            )
        )
    else:
        tasks_block = _section_text(content, "## Tasks")
        task_headers = re.findall(r"(?m)^### T\d+:", tasks_block)
        if not task_headers:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
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

    # Each milestone entry: **Status:** and valid value — WARNING (formatting)
    entry_blocks = _split_sections(milestones_block, r"### \d+\.")
    for j, block in enumerate(entry_blocks, 1):
        match = re.search(r"\*\*Status:\*\*\s*(\S+)", block)
        if not match:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=f"milestone entry {j} missing '**Status:**'",
                    path=rel,
                )
            )
        elif match.group(1) not in _MILESTONE_STATUSES:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    message=(
                        f"milestone entry {j} has invalid status "
                        f"'{match.group(1)}' "
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
