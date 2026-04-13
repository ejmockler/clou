"""Escalation file writers.

Creates timestamped markdown escalation files for cycle-limit, agent-crash,
validation-failure, and staleness conditions.

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clou.recovery_checkpoint import _validate_milestone


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
            "Reassess the milestone scope --- 20 cycles suggests the work "
            "is too large or the requirements are unclear."
        ),
    )


async def write_agent_crash_escalation(
    project_dir: Path,
    milestone: str,
    *,
    error_detail: str | None = None,
) -> None:
    """Write escalation when agent team crashes.

    *error_detail*, when provided, is included in the evidence section
    so that the supervisor (or user) can diagnose the root cause without
    needing to reproduce the crash.
    """
    _validate_milestone(milestone)
    evidence = "Agent subprocess exited with non-zero status."
    if error_detail:
        evidence = f"{evidence}\n\nError detail: {error_detail}"
    _write_escalation(
        project_dir=project_dir,
        milestone=milestone,
        slug="agent-crash",
        title="Agent Crash",
        classification="blocking",
        context="An agent team process terminated unexpectedly during execution.",
        issue="The agent team crashed and could not complete its assigned work.",
        evidence=evidence,
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
            "Review checkpoint write permissions --- the coordinator may not be able to update its state",
            "Verify golden context consistency --- status.md and checkpoint may have diverged",
            "Increase staleness threshold if the phase naturally requires many cycles",
        ],
        recommendation=(
            "Check checkpoint write permissions and verify that "
            "status.md/checkpoint next_step values are aligned."
        ),
    )
