"""Golden context protocol artifact serializers.

Code owns the format; the LLM owns the values.  These pure functions
are the single source of truth for protocol artifact markdown AND
for the valid enum sets that validation.py and recovery.py import.

The coordinator calls MCP tools backed by these serializers instead of
writing freeform markdown via Write/Edit.

Narrative artifacts (decisions.md, assessment.md, intents.md) remain
freeform — those benefit from LLM prose.  Protocol artifacts
(checkpoint, status, execution structure) have fixed schemas where
format tokens are wasted LLM capacity.

Public API:
    render_checkpoint(cycle, step, next_step, ...) -> str
    render_status(milestone, phase, cycle, next_step, phase_progress) -> str
    render_execution_summary(status, tasks_total, ...) -> str
    render_execution_task(task_id, name, status, ...) -> str
    assemble_execution(summary, tasks) -> str
    sanitize_phase(phase) -> str
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Canonical enum sets — single source of truth.
# validation.py and recovery.py should import these, not redefine them.
# ---------------------------------------------------------------------------

VALID_STEPS = frozenset({"PLAN", "EXECUTE", "ASSESS", "VERIFY", "EXIT"})
VALID_NEXT_STEPS = frozenset({
    "PLAN", "EXECUTE", "EXECUTE (rework)", "EXECUTE (additional verification)",
    "ASSESS", "VERIFY", "EXIT", "COMPLETE", "none",
})
TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
PHASE_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})

# Phase name pattern — alphanumeric, hyphens, underscores only.
_PHASE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def sanitize_phase(phase: str) -> str:
    """Validate and return a phase name, rejecting path traversal."""
    if not phase or not _PHASE_RE.match(phase):
        raise ValueError(
            f"invalid phase name {phase!r} — must be alphanumeric with "
            f"hyphens/underscores, no path separators"
        )
    return phase


# ---------------------------------------------------------------------------
# Checkpoint: active/coordinator.md
# ---------------------------------------------------------------------------


def render_checkpoint(
    cycle: int,
    step: str,
    next_step: str,
    current_phase: str = "",
    phases_completed: int = 0,
    phases_total: int = 0,
) -> str:
    """Render a coordinator checkpoint file.

    All fields are always written explicitly — no omissions, no aliases,
    no ambiguity for ``parse_checkpoint()`` or ``validate_checkpoint()``.
    """
    if cycle < 0:
        raise ValueError(f"cycle must be non-negative, got {cycle}")
    if step not in VALID_STEPS:
        raise ValueError(f"invalid step {step!r}")
    if next_step not in VALID_NEXT_STEPS:
        raise ValueError(f"invalid next_step {next_step!r}")
    if phases_completed < 0:
        raise ValueError(f"phases_completed must be non-negative, got {phases_completed}")
    if phases_total < 0:
        raise ValueError(f"phases_total must be non-negative, got {phases_total}")
    if phases_completed > phases_total:
        raise ValueError(
            f"phases_completed ({phases_completed}) exceeds "
            f"phases_total ({phases_total})"
        )

    return (
        f"cycle: {cycle}\n"
        f"step: {step}\n"
        f"next_step: {next_step}\n"
        f"current_phase: {current_phase}\n"
        f"phases_completed: {phases_completed}\n"
        f"phases_total: {phases_total}\n"
    )


# ---------------------------------------------------------------------------
# Status: status.md
# ---------------------------------------------------------------------------


def render_status(
    milestone: str,
    phase: str,
    cycle: int,
    next_step: str = "",
    phase_progress: dict[str, str] | None = None,
    notes: str = "",
) -> str:
    """Render a milestone status file.

    ``phase_progress`` maps phase names to status values
    (pending/in_progress/completed/failed).  Invalid status values
    are rejected.
    """
    if cycle < 0:
        raise ValueError(f"cycle must be non-negative, got {cycle}")
    if next_step and next_step not in VALID_NEXT_STEPS:
        raise ValueError(f"invalid next_step {next_step!r}")
    if phase_progress:
        for p_name, p_status in phase_progress.items():
            if p_status not in PHASE_STATUSES:
                raise ValueError(
                    f"invalid phase status {p_status!r} for phase {p_name!r}"
                )

    lines = [
        f"# Status: {milestone}",
        "",
        "## Current State",
        f"phase: {phase}",
        f"cycle: {cycle}",
    ]
    if next_step:
        lines.append(f"next_step: {next_step}")

    lines.extend(["", "## Phase Progress"])
    if phase_progress:
        lines.append("| Phase | Status |")
        lines.append("|---|---|")
        for p_name, p_status in phase_progress.items():
            lines.append(f"| {p_name} | {p_status} |")
    else:
        lines.append("| Phase | Status |")
        lines.append("|---|---|")
        lines.append(f"| {phase} | in_progress |")

    if notes:
        lines.extend(["", "## Notes", notes])

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Execution: phases/{phase}/execution.md
# ---------------------------------------------------------------------------


def render_execution_summary(
    status: str = "in_progress",
    tasks_total: int = 0,
    tasks_completed: int = 0,
    tasks_failed: int = 0,
    tasks_in_progress: int = 0,
    failures: str = "none",
    blockers: str = "none",
) -> str:
    """Render the ``## Summary`` block of an execution.md file."""
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid execution status {status!r}")
    return (
        "## Summary\n"
        f"status: {status}\n"
        f"tasks: {tasks_total} total, {tasks_completed} completed, "
        f"{tasks_failed} failed, {tasks_in_progress} in_progress\n"
        f"failures: {failures}\n"
        f"blockers: {blockers}\n"
    )


def render_execution_task(
    task_id: int,
    name: str,
    status: str = "pending",
    files_changed: list[str] | None = None,
    tests: str = "",
    notes: str = "",
) -> str:
    """Render a single ``### T<N>:`` task entry for execution.md."""
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid task status {status!r}")
    lines = [
        f"### T{task_id}: {name}",
        f"**Status:** {status}",
    ]
    if files_changed:
        lines.append("**Files changed:**")
        for f in files_changed:
            lines.append(f"  - {f}")
    if tests:
        lines.append(f"**Tests:** {tests}")
    if notes:
        lines.append(f"**Notes:** {notes}")

    return "\n".join(lines) + "\n"


def assemble_execution(
    summary: str,
    tasks: list[str],
) -> str:
    """Assemble a complete execution.md from summary and task blocks.

    Wraps tasks under ``## Tasks`` — the section header that validation
    requires.
    """
    parts = [summary, "", "## Tasks", ""]
    parts.extend(tasks)
    return "\n".join(parts)
