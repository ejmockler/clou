"""Checkpoint parsing and cycle determination for the coordinator loop.

Public API:
    parse_checkpoint(content: str) -> Checkpoint
    determine_next_cycle(checkpoint_path: Path, milestone: str, ...) -> tuple[str, list[str]]
    assess_convergence(decisions_content: str, threshold: int) -> ConvergenceState
    read_cycle_count(checkpoint_path: Path) -> int
    read_cycle_outcome(project_dir: Path) -> str
    write_cycle_limit_escalation(project_dir, milestone, cycle_count) -> None
    write_agent_crash_escalation(project_dir, milestone) -> None
    write_validation_escalation(project_dir, milestone, findings) -> None
    attempt_self_heal(project_dir, milestone, errors) -> list[str]
    log_self_heal_attempt(project_dir, milestone, fixes, remaining_errors) -> None
    git_revert_golden_context(project_dir: Path, milestone: str, current_phase: str | None = None) -> None
"""

from __future__ import annotations

import ast as _ast
import asyncio
import fnmatch
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: Milestone names must be lowercase alphanumeric with hyphens.
_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_milestone_name(name: str) -> None:
    """Raise ValueError if *name* is not a valid milestone slug."""
    if not _MILESTONE_RE.match(name):
        msg = f"Invalid milestone name: {name!r} (must match [a-z0-9][a-z0-9-]*)"
        raise ValueError(msg)


# Keep the old private name for internal callers.
_validate_milestone = validate_milestone_name


#: Consecutive zero-accept ASSESS cycles required to declare convergence.
_CONVERGENCE_THRESHOLD = 2

#: Valid next_step values in coordinator checkpoints.
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
    }
)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Parsed coordinator checkpoint state."""

    cycle: int = 0
    step: str = "PLAN"
    next_step: str = "PLAN"
    current_phase: str = ""
    phases_completed: int = 0
    phases_total: int = 0


@dataclass(frozen=True, slots=True)
class ConvergenceState:
    """Result of analyzing decisions.md for ASSESS cycle convergence."""

    consecutive_zero_accepts: int
    total_assess_cycles: int
    converged: bool


# Matches "## Cycle N — Brutalist Assessment" or similar ASSESS headers.
_ASSESS_HEADER_RE = re.compile(r"(?m)^## Cycle\s+\d+\s*[—–-]\s*(.+)$")


def _safe_int(value: str, default: int = 0) -> int:
    """Convert a string to a non-negative int, returning *default* on failure.

    Handles malformed agent-written values gracefully (e.g. ``"boom"``).
    """
    try:
        result = int(value)
        return max(result, 0)  # Non-negative
    except (ValueError, TypeError):
        return default


def parse_checkpoint(content: str) -> Checkpoint:
    """Parse a coordinator checkpoint markdown file and extract structured state.

    Extracts ``key: value`` pairs from lines and maps them to Checkpoint fields.
    Missing fields fall back to dataclass defaults.

    Runs DB-12 checkpoint validation first and logs warnings for any errors.
    Validation errors do NOT prevent parsing — the orchestrator's
    ``validate_golden_context()`` call at cycle boundaries is the enforcement
    point.  This log provides early visibility.
    """
    from clou.validation import validate_checkpoint

    errors = validate_checkpoint(content)
    for err in errors:
        _log.warning("Checkpoint validation: %s", err)

    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):\s*(.+)$", content):
        fields[match.group(1)] = match.group(2).strip()

    next_step = fields.get("next_step", "PLAN")
    if next_step.lower() == "none":
        next_step = "COMPLETE"
    if next_step not in _VALID_NEXT_STEPS:
        _log.warning("Unknown next_step %r — defaulting to PLAN", next_step)
        next_step = "PLAN"

    return Checkpoint(
        cycle=_safe_int(fields.get("cycle", "0")),
        step=fields.get("step", "PLAN"),
        next_step=next_step,
        current_phase=fields.get("current_phase", fields.get("phase", "")),
        phases_completed=_safe_int(fields.get("phases_completed", "0")),
        phases_total=_safe_int(fields.get("phases_total", "0")),
    )


def assess_convergence(
    decisions_content: str,
    threshold: int = _CONVERGENCE_THRESHOLD,
) -> ConvergenceState:
    """Analyze decisions.md for consecutive zero-accepted ASSESS cycles.

    Scans cycle sections newest-first (the file's natural ordering per DB-08).
    Counts consecutive Quality Gate Assessment / ASSESS sections that
    contain zero ``### Accepted:`` entries.  Convergence is reached when
    this count meets or exceeds *threshold*.
    """
    headers = list(_ASSESS_HEADER_RE.finditer(decisions_content))

    assess_blocks: list[str] = []
    for i, match in enumerate(headers):
        label = match.group(1).strip().lower()
        if (
            "assess" not in label
            and "quality gate" not in label
            and "brutalist" not in label
        ):
            continue
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(decisions_content)
        assess_blocks.append(decisions_content[start:end])

    consecutive = 0
    for block in assess_blocks:
        if re.search(r"(?m)^### Accepted:", block):
            break
        consecutive += 1

    return ConvergenceState(
        consecutive_zero_accepts=consecutive,
        total_assess_cycles=len(assess_blocks),
        converged=consecutive >= threshold,
    )


def determine_next_cycle(
    checkpoint_path: Path,
    milestone: str,
    *,
    decisions_path: Path | None = None,
) -> tuple[str, list[str]]:
    """Read checkpoint and determine the next cycle type and read set.

    When *decisions_path* is provided and the checkpoint requests rework
    (``EXECUTE (rework)``), convergence is checked: if the last N consecutive
    ASSESS cycles had zero accepted findings, rework is skipped and the
    cycle advances to VERIFY.
    """
    # PLAN read set includes memory.md when it exists (DB-18 D4).
    # checkpoint_path: .clou/milestones/{ms}/active/coordinator.md
    # memory.md: .clou/memory.md
    _plan_set = ["milestone.md", "intents.md", "requirements.md", "project.md"]
    # active/ -> ms/ -> milestones/ -> .clou/
    _clou_dir = checkpoint_path.parent.parent.parent.parent
    if (_clou_dir / "memory.md").exists():
        _plan_set.append("memory.md")

    if not checkpoint_path.exists():
        return "PLAN", _plan_set

    checkpoint = parse_checkpoint(checkpoint_path.read_text())

    match checkpoint.next_step:
        case "PLAN":
            return "PLAN", _plan_set
        case "EXECUTE" | "EXECUTE (rework)" | "EXECUTE (additional verification)":
            # Defense-in-depth: reject path traversal in current_phase.
            if ".." in checkpoint.current_phase or "/" in checkpoint.current_phase:
                _log.warning(
                    "Invalid current_phase %r — defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
            # Convergence override: if the coordinator requested rework
            # but ASSESS has converged (zero accepted findings for N
            # consecutive rounds), skip rework and advance to VERIFY.
            if (
                "rework" in checkpoint.next_step
                and decisions_path is not None
                and decisions_path.exists()
            ):
                conv = assess_convergence(decisions_path.read_text())
                if conv.converged:
                    _log.info(
                        "ASSESS converged: %d consecutive zero-accept "
                        "rounds — overriding rework, advancing to VERIFY",
                        conv.consecutive_zero_accepts,
                    )
                    return "VERIFY", [
                        "status.md",
                        "intents.md",
                        "compose.py",
                    ]
            return "EXECUTE", [
                "status.md",
                "compose.py",
                f"phases/{checkpoint.current_phase}/phase.md",
            ]
        case "ASSESS":
            # Defense-in-depth: reject path traversal in current_phase.
            if ".." in checkpoint.current_phase or "/" in checkpoint.current_phase:
                _log.warning(
                    "Invalid current_phase %r — defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
            assess_read = [
                "status.md",
                "compose.py",
                f"phases/{checkpoint.current_phase}/execution.md",
                "requirements.md",
                "decisions.md",
                "assessment.md",
            ]
            # Include execution shard files when they exist (gather()
            # groups produce execution-{task}.md alongside execution.md).
            milestone_dir = checkpoint_path.parent.parent
            phase_dir = milestone_dir / "phases" / checkpoint.current_phase
            if phase_dir.is_dir():
                for shard in sorted(phase_dir.glob("execution-*.md")):
                    rel = f"phases/{checkpoint.current_phase}/{shard.name}"
                    if rel not in assess_read:
                        assess_read.append(rel)
            return "ASSESS", assess_read
        case "VERIFY":
            return "VERIFY", [
                "status.md",
                "intents.md",
                "compose.py",
            ]
        case "EXIT":
            return "EXIT", [
                "status.md",
                "handoff.md",
                "decisions.md",
            ]
        case "COMPLETE":
            return "COMPLETE", []

    return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]


def read_cycle_count(checkpoint_path: Path) -> int:
    """Read the cycle count from the checkpoint file.

    Returns 0 if the file does not exist.
    """
    if not checkpoint_path.exists():
        return 0
    return parse_checkpoint(checkpoint_path.read_text()).cycle


def read_cycle_outcome(project_dir: Path, milestone: str = "") -> str:
    """Read the outcome of the last completed cycle from the checkpoint.

    Returns the ``next_step`` field which indicates what should happen next.
    Returns ``"PLAN"`` if no checkpoint exists.

    When *milestone* is provided, reads the milestone-scoped checkpoint
    (``milestones/{milestone}/active/coordinator.md``).
    """
    if milestone:
        checkpoint_path = (
            project_dir / ".clou" / "milestones" / milestone / "active" / "coordinator.md"
        )
    else:
        # Legacy fallback — root-scoped path.
        checkpoint_path = project_dir / ".clou" / "active" / "coordinator.md"
    if not checkpoint_path.exists():
        return "PLAN"
    return parse_checkpoint(checkpoint_path.read_text()).next_step


# ---------------------------------------------------------------------------
# Escalation writers
# ---------------------------------------------------------------------------


def _escalation_dir(project_dir: Path, milestone: str) -> Path:
    """Return the escalations directory, creating it if needed."""
    d = project_dir / ".clou" / "milestones" / milestone / "escalations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_escalation(
    project_dir: Path,
    milestone: str,
    slug: str,
    title: str,
    classification: str,
    context: str,
    issue: str,
    evidence: str,
    options: list[str],
    recommendation: str,
) -> Path:
    """Write a structured escalation markdown file and return its path."""
    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%d-%H%M%S")
    iso_filed = now.isoformat()
    options_block = "\n".join(f"{i}. {opt}" for i, opt in enumerate(options, 1))
    content = (
        f"# Escalation: {title}\n"
        f"\n"
        f"**Classification:** {classification}\n"
        f"**Filed:** {iso_filed}\n"
        f"\n"
        f"## Context\n"
        f"{context}\n"
        f"\n"
        f"## Issue\n"
        f"{issue}\n"
        f"\n"
        f"## Evidence\n"
        f"{evidence}\n"
        f"\n"
        f"## Options\n"
        f"{options_block}\n"
        f"\n"
        f"## Recommendation\n"
        f"{recommendation}\n"
        f"\n"
        f"## Disposition\n"
        f"status: open\n"
    )
    path = _escalation_dir(project_dir, milestone) / f"{ts}-{slug}.md"
    path.write_text(content)
    return path


async def write_cycle_limit_escalation(
    project_dir: Path, milestone: str, cycle_count: int
) -> None:
    """Write escalation when 20-cycle limit is reached."""
    _validate_milestone(milestone)
    _write_escalation(
        project_dir=project_dir,
        milestone=milestone,
        slug="cycle-limit",
        title="Cycle Limit Reached",
        classification="blocking",
        context=(
            f"The coordinator has completed {cycle_count} cycles "
            f"without reaching COMPLETE."
        ),
        issue=(
            f"Cycle count ({cycle_count}) has reached the 20-cycle limit. "
            f"The milestone may be stuck or underspecified."
        ),
        evidence=f"cycle_count={cycle_count}",
        options=[
            "Increase the cycle limit and continue execution",
            "Reassess the milestone scope and break it into smaller milestones",
            "Manually intervene to unblock progress",
        ],
        recommendation=(
            "Reassess the milestone scope — 20 cycles suggests the work "
            "is too large or the requirements are unclear."
        ),
    )


async def write_agent_crash_escalation(project_dir: Path, milestone: str) -> None:
    """Write escalation when agent team crashes."""
    _validate_milestone(milestone)
    _write_escalation(
        project_dir=project_dir,
        milestone=milestone,
        slug="agent-crash",
        title="Agent Crash",
        classification="blocking",
        context="An agent team process terminated unexpectedly during execution.",
        issue="The agent team crashed and could not complete its assigned work.",
        evidence="Agent subprocess exited with non-zero status.",
        options=[
            "Retry the cycle with the same configuration",
            "Revert golden context and retry from the previous checkpoint",
            "Escalate to the user for manual intervention",
        ],
        recommendation=(
            "Revert golden context to pre-cycle state and retry. "
            "If the crash recurs, escalate to the user."
        ),
    )


async def write_validation_escalation(
    project_dir: Path,
    milestone: str,
    findings: list[str] | list[Any],
) -> None:
    """Write escalation after 3 consecutive validation failures.

    Accepts either plain strings (legacy) or ``ValidationFinding`` objects.
    When structured findings are provided, the evidence section includes a
    severity breakdown (errors vs warnings).
    """
    from clou.validation import Severity, ValidationFinding

    _validate_milestone(milestone)

    # Normalise to structured findings for uniform handling.
    structured: list[ValidationFinding] = []
    plain_strings: list[str] = []
    for item in findings:
        if isinstance(item, ValidationFinding):
            structured.append(item)
        else:
            plain_strings.append(str(item))

    if structured:
        error_items = [f for f in structured if f.severity == Severity.ERROR]
        warning_items = [f for f in structured if f.severity == Severity.WARNING]

        evidence_parts: list[str] = []
        if error_items:
            evidence_parts.append("Errors (blocking):")
            evidence_parts.extend(f"- {e.message}" for e in error_items)
        if warning_items:
            if evidence_parts:
                evidence_parts.append("")
            evidence_parts.append("Warnings (non-blocking):")
            evidence_parts.extend(f"- {w.message}" for w in warning_items)
        evidence = "\n".join(evidence_parts)
        classification = "blocking" if error_items else "informational"
    else:
        evidence = "Latest validation errors:\n" + "\n".join(
            f"- {e}" for e in plain_strings
        )
        classification = "blocking"

    _write_escalation(
        project_dir=project_dir,
        milestone=milestone,
        slug="validation-failure",
        title="Repeated Validation Failures",
        classification=classification,
        context=(
            "Golden context validation has failed 3 consecutive times "
            "after cycle completion."
        ),
        issue="The agent team is producing structurally invalid golden context files.",
        evidence=evidence,
        options=[
            "Retry with stricter prompt guidance on file format",
            "Revert golden context and re-execute with format examples",
            "Escalate to the user to fix golden context manually",
        ],
        recommendation=(
            "Revert golden context and retry with explicit format "
            "examples in the prompt."
        ),
    )


async def write_staleness_escalation(
    project_dir: Path,
    milestone: str,
    cycle_type: str,
    consecutive_count: int,
    phases_completed: int,
    next_step: str,
) -> None:
    """Write escalation when the same cycle type repeats without phase advancement."""
    _validate_milestone(milestone)
    _write_escalation(
        project_dir=project_dir,
        milestone=milestone,
        slug="staleness",
        title="Staleness Detected",
        classification="blocking",
        context=(
            f"The coordinator has repeated the same cycle type '{cycle_type}' "
            f"for {consecutive_count} consecutive cycles with no phase advancement."
        ),
        issue=(
            f"Cycle type '{cycle_type}' has repeated {consecutive_count} times "
            f"with phases_completed stuck at {phases_completed}. "
            f"The orchestrator may be stuck in a loop."
        ),
        evidence=(
            f"cycle_type: {cycle_type}\n"
            f"consecutive_count: {consecutive_count}\n"
            f"phases_completed: {phases_completed}\n"
            f"next_step: {next_step}"
        ),
        options=[
            "Review checkpoint write permissions — the coordinator may not be able to update its state",
            "Verify golden context consistency — status.md and checkpoint may have diverged",
            "Increase staleness threshold if the phase naturally requires many cycles",
        ],
        recommendation=(
            "Check checkpoint write permissions and verify that "
            "status.md/checkpoint next_step values are aligned."
        ),
    )


# ---------------------------------------------------------------------------
# Self-heal
# ---------------------------------------------------------------------------


def _is_coordinator_writable(rel_path: str, milestone: str) -> bool:
    """Return True if *rel_path* is a coordinator-owned file.

    *rel_path* is relative to ``.clou/`` (e.g. ``milestones/my-ms/status.md``).
    Includes both Write-permitted files (from hooks) and protocol artifact
    files that are written via MCP tools (not Write).  Self-heal needs
    access to both as defense-in-depth.
    """
    from clou.golden_context import PROTOCOL_ARTIFACT_PATTERNS
    from clou.hooks import WRITE_PERMISSIONS

    patterns = list(WRITE_PERMISSIONS.get("coordinator", []))
    # Protocol artifacts are written by MCP tools, not Write, but
    # self-heal (Python code) still needs to fix them.
    patterns.extend(PROTOCOL_ARTIFACT_PATTERNS)
    return any(fnmatch.fnmatch(rel_path, p) for p in patterns)


#: Field aliases agents commonly write instead of canonical names.
_CHECKPOINT_FIELD_ALIASES: dict[str, str] = {
    "phase": "current_phase",
    "current_cycle": "cycle",
    "current_step": "step",
    "cycle_type": "step",
}

#: Canonical checkpoint fields with default values when absent.
_CHECKPOINT_DEFAULTS: dict[str, str] = {
    "step": "PLAN",
    "current_phase": "",
    "phases_completed": "0",
    "phases_total": "0",
}


def _normalise_checkpoint(content: str) -> tuple[str, list[str]]:
    """Normalise a coordinator checkpoint via parse→re-render.

    Returns ``(new_content, list_of_descriptions)``.  Idempotent.
    Uses ``parse_checkpoint`` (which handles aliases and defaults)
    then ``render_checkpoint`` (which guarantees canonical format).
    """
    from clou.golden_context import render_checkpoint

    fixes: list[str] = []

    # Parse existing fields to detect what's present.
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):\s*(.+)$", content):
        fields[match.group(1)] = match.group(2).strip()

    # Bail if required keys are missing — nothing safe to normalise.
    if "cycle" not in fields and "current_cycle" not in fields:
        return content, fixes
    if "next_step" not in fields:
        return content, fixes

    # Parse (handles aliases, defaults) then re-render (canonical format).
    cp = parse_checkpoint(content)
    new_content = render_checkpoint(
        cycle=cp.cycle,
        step=cp.step,
        next_step=cp.next_step,
        current_phase=cp.current_phase,
        phases_completed=cp.phases_completed,
        phases_total=cp.phases_total,
    )

    if new_content != content:
        fixes.append("re-rendered checkpoint via serializer")

    return new_content, fixes


#: Common status value misspellings and their canonical forms.
_STATUS_NORMALISATION: dict[str, str] = {
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "in_Progress": "in_progress",
    "In Progress": "in_progress",
    "IN PROGRESS": "in_progress",
    "In_Progress": "in_progress",
    "IN_PROGRESS": "in_progress",
}


def _normalise_status_in_table(content: str) -> tuple[str, list[str]]:
    """Normalise status values in a markdown Phase Progress table.

    Scans table rows for non-canonical status values and replaces them.
    Returns ``(new_content, list_of_descriptions)`` where descriptions
    explain each fix applied.  The function is idempotent.
    """
    fixes: list[str] = []
    lines = content.split("\n")
    new_lines: list[str] = []

    in_progress_table = False
    for line in lines:
        # Detect Phase Progress section (case-insensitive).
        if re.match(r"^##\s+Phase\s+Progress", line, re.I):
            in_progress_table = True
            new_lines.append(line)
            continue

        # Leave table region on next section header.
        if in_progress_table and re.match(r"^##\s+", line) and not re.match(
            r"^##\s+Phase\s+Progress", line, re.I
        ):
            in_progress_table = False

        if in_progress_table and "|" in line:
            # Skip header/divider rows.
            if re.match(r"^\s*\|[-\s|:]+\|\s*$", line) or re.match(
                r"^\s*\|.*Phase.*Status", line, re.I
            ):
                new_lines.append(line)
                continue

            cells = line.split("|")
            modified = False
            for idx, cell in enumerate(cells):
                stripped = cell.strip()
                lower = stripped.lower()
                if lower in {v.lower() for v in _STATUS_NORMALISATION}:
                    # Look up the canonical form.
                    canonical: str | None = None
                    for bad, good in _STATUS_NORMALISATION.items():
                        if bad.lower() == lower:
                            canonical = good
                            break
                    if canonical is not None and stripped != canonical:
                        fixes.append(
                            f"normalised status '{stripped}' -> '{canonical}'"
                        )
                        cells[idx] = cell.replace(stripped, canonical)
                        modified = True
            if modified:
                new_lines.append("|".join(cells))
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    return "\n".join(new_lines), fixes


def _add_missing_current_state_fields(
    content: str, milestone: str, project_dir: Path
) -> tuple[str, list[str]]:
    """Add missing phase:/cycle:/status: fields to the Current State section.

    Reads defaults from the coordinator checkpoint when available.
    Returns ``(new_content, list_of_descriptions)``.  Idempotent.
    """
    fixes: list[str] = []

    # Only operate if Current State section exists.
    if not re.search(r"(?mi)^##\s+Current\s+State", content):
        return content, fixes

    # Find the Current State section boundaries.
    cs_match = re.search(r"(?mi)^(##\s+Current\s+State)\s*\n", content)
    if not cs_match:
        return content, fixes

    section_start = cs_match.end()
    # Find next ## header or end of file.
    next_header = re.search(r"(?m)^##\s+", content[section_start:])
    section_end = section_start + next_header.start() if next_header else len(content)
    section_text = content[section_start:section_end]

    # Read checkpoint defaults (milestone-scoped path).
    checkpoint_path = project_dir / ".clou" / "milestones" / milestone / "active" / "coordinator.md"
    default_phase = "unknown"
    default_cycle = "1"
    if checkpoint_path.exists():
        cp = parse_checkpoint(checkpoint_path.read_text())
        if cp.current_phase:
            default_phase = cp.current_phase
        default_cycle = str(max(cp.cycle, 1))

    additions: list[str] = []

    if not re.search(r"(?m)^phase:", section_text):
        additions.append(f"phase: {default_phase}")
        fixes.append(f"added missing 'phase: {default_phase}' to Current State")

    if not re.search(r"(?m)^cycle:", section_text):
        additions.append(f"cycle: {default_cycle}")
        fixes.append(f"added missing 'cycle: {default_cycle}' to Current State")

    if not re.search(r"(?m)^status:", section_text):
        additions.append("status: in_progress")
        fixes.append("added missing 'status: in_progress' to Current State")

    if additions:
        # Insert after the section header line (before existing content).
        insert_text = "\n".join(additions) + "\n"
        content = content[:section_start] + insert_text + content[section_start:]

    return content, fixes


def attempt_self_heal(
    project_dir: Path,
    milestone: str,
    errors: list[Any],
) -> list[str]:
    """Attempt to fix validation errors in coordinator-writable files.

    Returns a list of descriptions of what was fixed (empty = nothing fixable).
    Only modifies files matching coordinator ``WRITE_PERMISSIONS`` patterns.
    Fixes are deterministic, safe, and idempotent.
    """
    from clou.validation import ValidationFinding

    _validate_milestone(milestone)

    all_fixes: list[str] = []

    # Group errors by file path.
    path_errors: dict[str, list[Any]] = {}
    for err in errors:
        if isinstance(err, ValidationFinding):
            path_errors.setdefault(err.path, []).append(err)
        else:
            # Legacy string errors -- cannot determine path, skip.
            continue

    for rel_path, _file_errors in path_errors.items():
        # Only touch coordinator-writable files.
        # The rel_path from validation is relative to .clou/ (e.g.
        # "milestones/my-ms/status.md").
        if not _is_coordinator_writable(rel_path, milestone):
            _log.debug(
                "Self-heal: skipping %r — not coordinator-writable", rel_path,
            )
            continue

        abs_path = project_dir / ".clou" / rel_path
        if not abs_path.exists():
            continue

        content = abs_path.read_text()
        original = content
        fixes: list[str] = []

        # Apply fixers based on the file type.
        if rel_path.endswith("active/coordinator.md"):
            content, cp_fixes = _normalise_checkpoint(content)
            fixes.extend(cp_fixes)

        if rel_path.endswith("status.md"):
            content, table_fixes = _normalise_status_in_table(content)
            fixes.extend(table_fixes)

            content, field_fixes = _add_missing_current_state_fields(
                content, milestone, project_dir,
            )
            fixes.extend(field_fixes)

        if fixes and content != original:
            abs_path.write_text(content)
            for fix_desc in fixes:
                _log.info("Self-heal [%s]: %s", rel_path, fix_desc)
            all_fixes.extend(fixes)

    return all_fixes


def log_self_heal_attempt(
    project_dir: Path,
    milestone: str,
    fixes: list[str],
    remaining_errors: list[Any],
) -> None:
    """Log self-heal attempt to decisions.md (coordinator-writable).

    Appends a brief note documenting what was auto-fixed and what
    errors remain (if any).
    """
    from clou.validation import ValidationFinding

    _validate_milestone(milestone)

    decisions_path = (
        project_dir / ".clou" / "milestones" / milestone / "decisions.md"
    )

    now = datetime.now(UTC).isoformat()
    lines: list[str] = [
        "",
        f"### Self-Heal ({now})",
        "",
    ]

    if fixes:
        lines.append("**Auto-fixed:**")
        for fix in fixes:
            lines.append(f"- {fix}")
        lines.append("")

    if remaining_errors:
        lines.append("**Remaining errors (unfixable):**")
        for err in remaining_errors:
            if isinstance(err, ValidationFinding):
                lines.append(f"- [{err.path}] {err.message}")
            else:
                lines.append(f"- {err}")
        lines.append("")

    note = "\n".join(lines)

    if decisions_path.exists():
        decisions_path.write_text(decisions_path.read_text() + note)
    else:
        # Create the file if it doesn't exist — the coordinator is
        # allowed to write decisions.md.
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        decisions_path.write_text(f"# Decisions\n{note}")


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


#: Patterns excluded from selective staging (DB-15 D4).
_STAGING_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".clou/telemetry/*",
    ".clou/sessions/*",
    "node_modules/*",
    "__pycache__/*",
    "*.pyc",
    ".env",
    ".env.*",
    "*.egg-info/*",
    ".mypy_cache/*",
    ".pytest_cache/*",
    "dist/*",
    "build/*",
)


async def git_commit_phase(project_dir: Path, milestone: str, phase: str) -> None:
    """Commit changes after a phase completes.

    Uses selective staging (DB-15 D4): stages files from ``git diff``
    filtered by exclude patterns, instead of ``git add -A``.  Golden
    context files under ``.clou/`` are included (they're part of the
    milestone record).
    """
    _validate_milestone(milestone)

    # Get list of changed files (unstaged + untracked).
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--name-only",
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("git diff --name-only timed out after 30s") from None

    # Also get untracked files.
    proc2 = await asyncio.create_subprocess_exec(
        "git", "ls-files", "--others", "--exclude-standard",
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout2_bytes, _ = await asyncio.wait_for(proc2.communicate(), timeout=30)
    except TimeoutError:
        proc2.kill()
        await proc2.communicate()
        raise RuntimeError("git ls-files timed out after 30s") from None

    changed = set(stdout_bytes.decode(errors="replace").splitlines())
    changed |= set(stdout2_bytes.decode(errors="replace").splitlines())
    changed.discard("")

    # Stage ONLY files within the milestone directory (V4: prevents
    # committing user's unrelated changes or other milestone's context).
    milestone_prefix = f".clou/milestones/{milestone}/"
    to_stage = [
        f for f in changed
        if f.startswith(milestone_prefix)
        and not any(fnmatch.fnmatch(f, pat) for pat in _STAGING_EXCLUDE_PATTERNS)
    ]

    if not to_stage:
        return  # Nothing to commit.

    # Stage selected files.
    proc = await asyncio.create_subprocess_exec(
        "git", "add", "--", *to_stage,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("git add timed out after 30s") from None
    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        msg = f"git add failed (exit {proc.returncode}): {stderr_text.strip()}"
        raise RuntimeError(msg)

    # Check if there are staged changes (exit 0 = no changes)
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--cached", "--quiet",
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("git diff timed out after 30s") from None

    if proc.returncode == 0:
        # No changes staged — nothing to commit
        return

    # Commit with structured message
    message = f"feat({milestone}): complete phase '{phase}'"
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", message,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("git commit timed out after 30s") from None
    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        msg = f"git commit failed (exit {proc.returncode}): {stderr_text.strip()}"
        raise RuntimeError(msg)


async def git_revert_golden_context(
    project_dir: Path,
    milestone: str,
    current_phase: str | None = None,
) -> None:
    """Revert coordinator-owned golden context files to pre-cycle state.

    Only reverts files the coordinator owns (DB-07 ownership).
    Supervisor-authored files (milestone.md, intents.md, requirements.md)
    are NOT reverted --- they are immutable after handoff.

    V6: When *current_phase* is provided, only that phase's directory under
    ``phases/`` is reverted, preserving execution.md files in completed phases.
    V10: After checkout revert, untracked files in the reverted paths are
    removed via ``git clean -fd``.
    """
    _validate_milestone(milestone)
    # Defense-in-depth: reject path traversal in current_phase.
    if current_phase and (".." in current_phase or "/" in current_phase):
        msg = f"Invalid current_phase: {current_phase!r} (must not contain '..' or '/')"
        raise ValueError(msg)
    ms = f".clou/milestones/{milestone}"
    # Coordinator-owned files only --- NOT milestone.md, intents.md, requirements.md.
    coordinator_paths = [
        f"{ms}/active/",
        f"{ms}/status.md",
        f"{ms}/compose.py",
        f"{ms}/decisions.md",
        f"{ms}/assessment.md",
        f"{ms}/escalations/",
    ]
    # V6: Scope phases/ revert to current phase only (preserves completed
    # phases' execution.md files).
    if current_phase:
        coordinator_paths.append(f"{ms}/phases/{current_phase}/")
    else:
        coordinator_paths.append(f"{ms}/phases/")

    proc = await asyncio.create_subprocess_exec(
        "git",
        "checkout",
        "HEAD",
        "--",
        *coordinator_paths,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("git revert timed out after 30s") from None
    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        msg = f"git revert failed (exit {proc.returncode}): {stderr_text.strip()}"
        raise RuntimeError(msg)

    # V10: Clean untracked files in the milestone directory.  Scoped to only
    # the coordinator-owned paths that were reverted above.
    clean_paths = [p for p in coordinator_paths if p.endswith("/")]
    if clean_paths:
        proc2 = await asyncio.create_subprocess_exec(
            "git",
            "clean",
            "-fd",
            "--",
            *clean_paths,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc2.communicate(), timeout=30)
        except TimeoutError:
            proc2.kill()
            await proc2.communicate()
            raise RuntimeError("git clean timed out after 30s") from None
        # git clean exit code 0 = success; non-zero is unexpected but not fatal
        # for the revert operation, so we log but don't raise.
        if proc2.returncode != 0:
            _log.warning(
                "git clean returned exit %d during revert for %r",
                proc2.returncode,
                milestone,
            )


# ---------------------------------------------------------------------------
# Decisions compaction (DB-15 Tension 3)
# ---------------------------------------------------------------------------

_CYCLE_GROUP_RE = re.compile(r"(?m)^## Cycle \d+")


def compact_decisions(
    path: Path,
    *,
    keep_recent: int = 3,
    token_threshold: int = 4000,
) -> bool:
    """Compact old cycle groups in decisions.md.

    Keeps the most recent *keep_recent* cycle groups in full detail.
    Older groups are reduced to one-line summaries preserving finding
    counts and titles.  Full text is preserved in git history.

    Returns True if compaction was performed.
    """
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")

    # Rough token estimate: ~4 chars/token.
    if len(content) < token_threshold * 4:
        return False

    # Split into cycle groups.  Each group starts with "## Cycle N".
    splits = list(_CYCLE_GROUP_RE.finditer(content))
    if len(splits) <= keep_recent:
        return False  # Not enough groups to compact.

    # Everything before the first cycle group (preamble/heading).
    preamble = content[: splits[0].start()]

    # Collect groups newest-first (decisions.md is newest-first).
    groups: list[str] = []
    for i, match in enumerate(splits):
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        groups.append(content[match.start() : end])

    # Keep recent groups verbatim, compact older ones.
    recent = groups[:keep_recent]
    old = groups[keep_recent:]

    compacted_lines: list[str] = []
    for group in old:
        # Extract the heading line.
        heading_end = group.index("\n") if "\n" in group else len(group)
        heading = group[:heading_end].strip()

        # Count accepted/overridden findings.
        accepted = len(re.findall(r"(?m)^### Accepted:", group))
        overridden = len(re.findall(r"(?m)^### Overridden:", group))

        compacted_lines.append(
            f"{heading} (compacted)\n"
            f"Accepted: {accepted} | Overridden: {overridden}\n"
        )

    result = preamble + "".join(recent) + "\n".join(compacted_lines)
    path.write_text(result, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Memory consolidation (DB-18)
# ---------------------------------------------------------------------------


@dataclass
class MemoryPattern:
    """A single pattern entry in memory.md."""

    name: str
    type: str  # decomposition | quality-gate | cost-calibration | escalation | debt
    observed: list[str] = field(default_factory=list)
    reinforced: int = 1
    last_active: str = ""
    invalidated: str = ""
    invalidation_reason: str = ""
    status: str = "active"  # active | fading | archived
    description: str = ""


#: Known metadata field names in memory.md pattern entries.
_MEMORY_FIELDS = frozenset({
    "type", "observed", "reinforced", "last_active",
    "invalidated", "invalidation_reason", "status",
})


def _parse_memory(content: str) -> list[MemoryPattern]:
    """Parse memory.md into structured pattern entries."""
    patterns: list[MemoryPattern] = []
    # Split on ### headers (pattern entries).
    sections = re.split(r"(?m)^### ", content)
    for section in sections[1:]:  # skip preamble
        lines = section.strip().split("\n")
        if not lines:
            continue
        name = lines[0].strip()
        fields: dict[str, str] = {}
        desc_lines: list[str] = []
        for line in lines[1:]:
            # Stop at section headers (## Archived, ## Patterns, etc.)
            if line.startswith("## "):
                break
            m = re.match(r"^(\w[\w_]*):\s*(.+)$", line)
            # Only treat as a field if the key is in the known set.
            if m and m.group(1) in _MEMORY_FIELDS:
                fields[m.group(1)] = m.group(2).strip()
            elif line.strip():
                desc_lines.append(line.strip())

        observed_raw = fields.get("observed", "")
        observed = [o.strip() for o in observed_raw.split(",") if o.strip()]

        patterns.append(MemoryPattern(
            name=name,
            type=fields.get("type", ""),
            observed=observed,
            reinforced=_safe_int(fields.get("reinforced", "1"), 1),
            last_active=fields.get("last_active", ""),
            invalidated=fields.get("invalidated", ""),
            invalidation_reason=fields.get("invalidation_reason", ""),
            status=fields.get("status", "active"),
            description=" ".join(desc_lines),
        ))
    return patterns


def _render_memory(patterns: list[MemoryPattern]) -> str:
    """Render pattern entries back to memory.md format."""
    lines = ["# Operational Memory", "", "## Patterns", ""]

    active = [p for p in patterns if p.status == "active"]
    fading = [p for p in patterns if p.status == "fading"]
    archived = [p for p in patterns if p.status == "archived"]

    for p in active + fading:
        lines.append(f"### {p.name}")
        lines.append(f"type: {p.type}")
        lines.append(f"observed: {', '.join(p.observed)}")
        lines.append(f"reinforced: {p.reinforced}")
        lines.append(f"last_active: {p.last_active}")
        if p.invalidated:
            lines.append(f"invalidated: {p.invalidated}")
            if p.invalidation_reason:
                lines.append(f"invalidation_reason: {p.invalidation_reason}")
        if p.status == "fading":
            lines.append(f"status: fading")
        lines.append("")
        if p.description:
            lines.append(p.description)
        lines.append("")

    if archived:
        lines.append("## Archived")
        lines.append("")
        for p in archived:
            lines.append(f"### {p.name}")
            lines.append(f"type: {p.type}")
            lines.append(f"observed: {', '.join(p.observed)}")
            lines.append(f"reinforced: {p.reinforced}")
            lines.append(f"last_active: {p.last_active}")
            if p.invalidated:
                lines.append(f"invalidated: {p.invalidated}")
                if p.invalidation_reason:
                    lines.append(f"invalidation_reason: {p.invalidation_reason}")
            lines.append(f"status: archived")
            lines.append("")
            if p.description:
                lines.append(p.description)
            lines.append("")

    return "\n".join(lines)


def _parse_metrics_header(content: str) -> dict[str, str]:
    """Extract key: value pairs from the metrics.md header."""
    fields: dict[str, str] = {}
    for line in content.split("\n"):
        if line.startswith("#"):
            continue
        m = re.match(r"^(\w[\w_]*):\s*(.+)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
        elif line.strip() == "":
            # Stop at first blank line after header block.
            if fields:
                break
    return fields


def _count_metrics_section_rows(content: str, section_header: str) -> int:
    """Count data rows in a metrics.md table section.

    Skips the header row and separator row. Returns 0 if section is absent.
    """
    count = 0
    in_section = False
    for line in content.split("\n"):
        if line.strip() == section_header:
            in_section = True
            continue
        if in_section and line.startswith("##"):
            break
        if in_section and line.startswith("|") and not line.startswith("|--"):
            # Skip the column header row (contains letters).
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells and not all(c.replace(" ", "").isalpha() for c in cells):
                count += 1
    return count


def _count_qg_unavailable(content: str) -> int:
    """Count quality gate tool unavailability events from ## Quality Gate table.

    The table has columns: Cycle | Tools Invoked | Tools Unavailable | Tool Count.
    Returns the number of rows where Tools Unavailable is not 'none'.
    """
    count = 0
    in_section = False
    header_skipped = False
    for line in content.split("\n"):
        if line.strip() == "## Quality Gate":
            in_section = True
            continue
        if in_section and line.startswith("##"):
            break
        if in_section and line.startswith("|") and not line.startswith("|--"):
            if not header_skipped:
                header_skipped = True
                continue
            cells = [c.strip() for c in line.split("|")]
            # Tools Unavailable is the 4th column (index 3 after split).
            if len(cells) >= 4 and cells[3] != "none":
                count += 1
    return count


def _parse_cycle_types(content: str) -> list[str]:
    """Extract cycle type sequence from the metrics.md Cycles table."""
    types: list[str] = []
    in_table = False
    for line in content.split("\n"):
        if "## Cycles" in line:
            in_table = True
            continue
        if in_table and line.startswith("##"):
            break
        if in_table and line.startswith("|") and not line.startswith("|--") and "Type" not in line:
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 3:
                types.append(cells[2])  # Type column
    return types


def _analyze_compose(compose_path: Path) -> tuple[int, bool]:
    """Analyze compose.py topology via graph.py's existing AST infrastructure.

    Returns (phase_count, has_gather). Uses extract_dag_data() from
    clou.graph (the canonical AST analysis, already validated by DB-02).
    """
    if not compose_path.exists():
        return 0, False

    try:
        from clou.graph import extract_dag_data

        source = compose_path.read_text(encoding="utf-8")
        tasks, deps = extract_dag_data(source)
    except (OSError, SyntaxError, Exception):
        return 0, False

    # Phase count: extract_dag_data excludes execute/execute_milestone
    # but includes verify. Filter it out — it's infrastructure, not a phase.
    phase_count = sum(1 for t in tasks if t["name"] != "verify")

    # Detect gather: look for tasks with shared dependencies (parallel).
    # If multiple tasks have the same set of deps (or no deps), they
    # were likely in a gather() group.  Cross-check via AST for gather().
    has_gather = False
    try:
        tree = _ast.parse(source)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.AsyncFunctionDef) and node.name in (
                "execute", "execute_milestone",
            ):
                for child in _ast.walk(node):
                    if isinstance(child, _ast.Call):
                        func = child.func
                        if (
                            isinstance(func, _ast.Name) and func.id == "gather"
                        ) or (
                            isinstance(func, _ast.Attribute) and func.attr == "gather"
                        ):
                            has_gather = True
                            break
                break
    except (SyntaxError, Exception):
        pass

    return phase_count, has_gather


def _reinforce_or_create(
    patterns: list[MemoryPattern],
    name: str,
    type_: str,
    milestone: str,
    description: str,
) -> None:
    """Reinforce an existing pattern or create a new one.

    Idempotent per milestone: calling twice with the same milestone
    does not double-count reinforcement.
    """
    for p in patterns:
        if p.name == name:
            if milestone not in p.observed:
                p.observed.append(milestone)
                p.reinforced += 1
            p.last_active = milestone
            p.description = description
            # Un-invalidate if re-confirmed.
            if p.invalidated:
                p.invalidated = ""
                p.invalidation_reason = ""
            if p.status != "active":
                p.status = "active"
            return
    patterns.append(MemoryPattern(
        name=name,
        type=type_,
        observed=[milestone],
        reinforced=1,
        last_active=milestone,
        description=description,
    ))


def _apply_decay(
    patterns: list[MemoryPattern],
    current_milestone: str,
    all_milestones: list[str],
    *,
    fading_threshold: int = 5,
    archive_threshold: int = 10,
) -> None:
    """Apply milestone-distance decay to patterns (DB-18 D3).

    Patterns with reinforced >= 5 are durable and exempt from decay.
    """
    if not all_milestones:
        return

    milestone_index: dict[str, int] = {
        m: i for i, m in enumerate(all_milestones)
    }
    current_idx = milestone_index.get(current_milestone)
    if current_idx is None:
        return

    for p in patterns:
        # High-confidence patterns don't decay.
        if p.reinforced >= 5:
            continue
        # Already invalidated patterns stay archived.
        if p.invalidated:
            p.status = "archived"
            continue

        last_idx = milestone_index.get(p.last_active)
        if last_idx is None:
            continue

        distance = current_idx - last_idx
        if distance >= archive_threshold:
            p.status = "archived"
        elif distance >= fading_threshold and p.reinforced < 3:
            p.status = "fading"


_NUMERIC_PREFIX_RE = re.compile(r"^(\d+)")


def _milestone_sort_key(name: str) -> tuple[int, str]:
    """Sort key for milestone names: numeric prefix first, then lexicographic."""
    m = _NUMERIC_PREFIX_RE.match(name)
    if m:
        return (int(m.group(1)), name)
    return (0, name)


def _consolidated_milestones(memory_path: Path) -> set[str]:
    """Return the set of milestones structurally consolidated into memory.md.

    Only considers orchestrator-authored patterns (cost-calibration) to
    avoid false positives from supervisor annotations that mention a
    milestone before structural consolidation ran for it.
    """
    if not memory_path.exists():
        return set()
    patterns = _parse_memory(memory_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for p in patterns:
        if p.type == "cost-calibration":
            seen.update(p.observed)
    return seen


def consolidate_pending(project_dir: Path) -> int:
    """Consolidate any completed milestones not yet in memory.md (DB-18).

    Compares milestone directories (those with metrics.md) against the
    milestones already recorded in memory.md's ``observed`` fields.
    Consolidates the difference in chronological order.

    This is the self-healing path: handles first-time bootstrap, crash
    recovery, and code deployment across existing projects. Called at
    supervisor startup before the SDK session reads memory.md.

    Non-destructive — does not archive episodic files. Archival only
    runs at the moment of completion (orchestrator finally block).

    Returns count of milestones consolidated.
    """
    clou_dir = project_dir / ".clou"
    milestones_dir = clou_dir / "milestones"
    if not milestones_dir.exists():
        return 0

    memory_path = clou_dir / "memory.md"
    already_consolidated = _consolidated_milestones(memory_path)

    # Find milestones with metrics.md that aren't in memory.md yet.
    pending: list[str] = []
    for ms_dir in milestones_dir.iterdir():
        if not ms_dir.is_dir():
            continue
        if (ms_dir / "metrics.md").exists() and ms_dir.name not in already_consolidated:
            pending.append(ms_dir.name)

    if not pending:
        return 0

    # Consolidate in chronological order so decay is correct.
    pending.sort(key=_milestone_sort_key)

    count = 0
    for ms_name in pending:
        try:
            if consolidate_milestone(project_dir, ms_name):
                count += 1
                _log.info("Pending consolidation: %s", ms_name)
        except Exception:
            _log.warning(
                "Pending consolidation failed for %r", ms_name, exc_info=True,
            )

    return count


def consolidate_milestone(
    project_dir: Path,
    milestone: str,
) -> bool:
    """Consolidate a completed milestone into operational memory (DB-18).

    Reads metrics.md (orchestrator-written) and compose.py (AST-parsed)
    to extract structural patterns. Updates .clou/memory.md with new or
    reinforced patterns. Applies milestone-distance decay.

    Archival of episodic files is handled separately by
    ``archive_milestone_episodic()``.

    Returns True if consolidation was performed.
    """
    _validate_milestone(milestone)

    clou_dir = project_dir / ".clou"
    ms_dir = clou_dir / "milestones" / milestone

    # Read metrics.md — the primary data source (orchestrator-written).
    metrics_path = ms_dir / "metrics.md"
    if not metrics_path.exists():
        _log.warning("consolidate_milestone: no metrics.md for %r", milestone)
        return False

    metrics_content = metrics_path.read_text(encoding="utf-8")
    header = _parse_metrics_header(metrics_content)

    # Read compose.py via AST — decomposition topology.
    compose_path = ms_dir / "compose.py"
    phase_count, has_gather = _analyze_compose(compose_path)

    # Read existing memory.md (or start fresh).
    memory_path = clou_dir / "memory.md"
    if memory_path.exists():
        patterns = _parse_memory(memory_path.read_text(encoding="utf-8"))
    else:
        patterns = []

    # Build ordered milestone list for decay calculation.
    # Sort by numeric prefix (milestones are named "N-slug").
    all_milestones = sorted(
        [d.name for d in (clou_dir / "milestones").iterdir() if d.is_dir()],
        key=_milestone_sort_key,
    )

    # --- Extract patterns from deterministic data sources ---

    # 1. Cost calibration: cycles, tokens, duration.
    cycles = _safe_int(header.get("cycles", "0"))
    tokens_out = _safe_int(header.get("tokens_out", "0"))
    duration = header.get("duration", "unknown")
    _reinforce_or_create(
        patterns,
        name="cycle-count-distribution",
        type_="cost-calibration",
        milestone=milestone,
        description=(
            f"{cycles} cycles, ~{tokens_out:,} output tokens, {duration}."
        ),
    )

    # 2. Decomposition topology from compose.py AST.
    if phase_count > 0:
        topology = "parallel (gather)" if has_gather else "sequential"
        _reinforce_or_create(
            patterns,
            name="decomposition-topology",
            type_="decomposition",
            milestone=milestone,
            description=(
                f"{phase_count} phases, {topology} execution."
            ),
        )

    # 3. Escalation patterns from metrics header.
    validation_failures = _safe_int(header.get("validation_failures", "0"))
    crash_retries = _safe_int(header.get("crash_retries", "0"))
    if validation_failures > 0 or crash_retries > 0:
        _reinforce_or_create(
            patterns,
            name="validation-noise",
            type_="debt",
            milestone=milestone,
            description=(
                f"{validation_failures} validation failures, "
                f"{crash_retries} crash retries."
            ),
        )

    # 4. Rework — read from ## Rework section (DB-18 telemetry extension).
    rework_count = _count_metrics_section_rows(metrics_content, "## Rework")
    if rework_count > 0:
        _reinforce_or_create(
            patterns,
            name="rework-frequency",
            type_="escalation",
            milestone=milestone,
            description=f"{rework_count} rework cycle(s) triggered.",
        )

    # 5. Quality gate — read from ## Quality Gate section (DB-18).
    qg_rows = _count_metrics_section_rows(metrics_content, "## Quality Gate")
    if qg_rows > 0:
        # Parse the quality gate table for tool unavailability.
        unavail_count = _count_qg_unavailable(metrics_content)
        if unavail_count > 0:
            _reinforce_or_create(
                patterns,
                name="quality-gate-availability",
                type_="quality-gate",
                milestone=milestone,
                description=(
                    f"Quality gate had {unavail_count} unavailable tool(s) "
                    f"across {qg_rows} invocation(s)."
                ),
            )

    # 6. Escalation — read from ## Escalations section (DB-18).
    esc_rows = _count_metrics_section_rows(metrics_content, "## Escalations")
    if esc_rows > 0:
        _reinforce_or_create(
            patterns,
            name="escalation-frequency",
            type_="escalation",
            milestone=milestone,
            description=f"{esc_rows} escalation(s) created.",
        )

    # 7. Outcome pattern.
    outcome = header.get("outcome", "unknown")
    if outcome.startswith("escalated"):
        _reinforce_or_create(
            patterns,
            name="escalation-outcome",
            type_="escalation",
            milestone=milestone,
            description=f"Milestone ended with outcome: {outcome}.",
        )

    # --- Apply decay ---
    _apply_decay(patterns, milestone, all_milestones)

    # --- Write memory.md ---
    memory_path.write_text(_render_memory(patterns), encoding="utf-8")
    _log.info("Consolidated %r into memory.md (%d patterns)", milestone, len(patterns))

    return True


async def archive_milestone_episodic(
    project_dir: Path,
    milestone: str,
) -> list[str]:
    """Archive episodic files for a completed milestone (DB-18).

    Removes decisions.md, assessment.md, execution.md, escalations/,
    and active/coordinator.md from the working tree. Git history
    preserves full episodic detail.

    Returns list of archived file paths (relative to project_dir).
    """
    _validate_milestone(milestone)

    ms_prefix = f".clou/milestones/{milestone}"
    archivable = [
        f"{ms_prefix}/decisions.md",
        f"{ms_prefix}/assessment.md",
        f"{ms_prefix}/active/coordinator.md",
    ]

    # Add phase execution.md files.
    phases_dir = project_dir / ".clou" / "milestones" / milestone / "phases"
    if phases_dir.exists():
        for phase_dir in phases_dir.iterdir():
            if phase_dir.is_dir():
                exec_file = phase_dir / "execution.md"
                if exec_file.exists():
                    archivable.append(
                        f"{ms_prefix}/phases/{phase_dir.name}/execution.md"
                    )

    # Add escalation files.
    esc_dir = project_dir / ".clou" / "milestones" / milestone / "escalations"
    if esc_dir.exists():
        for esc_file in esc_dir.iterdir():
            if esc_file.is_file() and esc_file.suffix == ".md":
                archivable.append(
                    f"{ms_prefix}/escalations/{esc_file.name}"
                )

    # Filter to files that actually exist.
    existing = [
        f for f in archivable
        if (project_dir / f).exists()
    ]

    if not existing:
        return []

    # git rm the files (preserves in history).
    # --ignore-unmatch: don't fail if a file isn't in the index
    # (e.g., uncommitted new files from a failed cycle).
    proc = await asyncio.create_subprocess_exec(
        "git", "rm", "--quiet", "--ignore-unmatch", "--", *existing,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("git rm timed out after 30s") from None

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        _log.warning("git rm failed for %r: %s", milestone, stderr_text.strip())
        return []

    _log.info("Archived %d episodic files for %r", len(existing), milestone)
    return existing
