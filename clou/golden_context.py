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
    render_status_from_checkpoint(milestone, checkpoint, phase_names) -> str
    render_execution_summary(status, tasks_total, ...) -> str
    render_execution_task(task_id, name, status, ...) -> str
    assemble_execution(summary, tasks) -> str
    sanitize_phase(phase) -> str

Internal (shared across modules):
    _extract_phase_names(ms_dir) -> list[str]
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clou.recovery_checkpoint import Checkpoint


# ---------------------------------------------------------------------------
# Canonical enum sets — single source of truth.
# validation.py and recovery.py should import these, not redefine them.
# ---------------------------------------------------------------------------

VALID_STEPS = frozenset({"PLAN", "EXECUTE", "ASSESS", "REPLAN", "VERIFY", "EXIT"})
VALID_NEXT_STEPS = frozenset({
    "PLAN", "EXECUTE", "EXECUTE (rework)", "EXECUTE (additional verification)",
    "ASSESS", "REPLAN", "VERIFY", "EXIT", "COMPLETE", "none",
})
VALID_CYCLE_OUTCOMES = frozenset({"ADVANCED", "INCONCLUSIVE", "INTERRUPTED", "FAILED"})
TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
PHASE_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})

# Protocol artifact glob patterns — files written via MCP tools, not Write.
# Used by hooks.py (to exclude from Write permissions) and recovery.py
# (to include in self-heal scope).  Single source of truth.
PROTOCOL_ARTIFACT_PATTERNS = (
    "milestones/*/status.md",
    "milestones/*/active/coordinator.md",
)

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


_log = logging.getLogger(__name__)


def _extract_phase_names(ms_dir: Path) -> list[str]:
    """Extract ordered phase names from compose.py via the DAG parser.

    Returns an empty list if compose.py does not exist or cannot be parsed.
    Phase names are the function names from ``extract_dag_data()``, in
    the order they appear in the source.
    """
    compose_path = ms_dir / "compose.py"
    if not compose_path.exists():
        return []
    try:
        from clou.graph import extract_dag_data

        source = compose_path.read_text(encoding="utf-8")
        tasks, _deps = extract_dag_data(source)
        return [t["name"] for t in tasks]
    except Exception:
        _log.warning("Could not extract phase names from compose.py", exc_info=True)
        return []


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
    *,
    validation_retries: int = 0,
    readiness_retries: int = 0,
    crash_retries: int = 0,
    staleness_count: int = 0,
    cycle_outcome: str = "ADVANCED",
    valid_findings: int = -1,
    consecutive_zero_valid: int = 0,
) -> str:
    """Render a coordinator checkpoint file.

    All fields are always written explicitly — no omissions, no aliases,
    no ambiguity for ``parse_checkpoint()`` or ``validate_checkpoint()``.

    Retry counters, cycle_outcome, and convergence fields are keyword-only
    to avoid breaking existing callers that pass positional arguments.
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
    if cycle_outcome not in VALID_CYCLE_OUTCOMES:
        raise ValueError(f"invalid cycle_outcome {cycle_outcome!r}")

    return (
        f"cycle: {cycle}\n"
        f"step: {step}\n"
        f"next_step: {next_step}\n"
        f"current_phase: {current_phase}\n"
        f"phases_completed: {phases_completed}\n"
        f"phases_total: {phases_total}\n"
        f"validation_retries: {validation_retries}\n"
        f"readiness_retries: {readiness_retries}\n"
        f"crash_retries: {crash_retries}\n"
        f"staleness_count: {staleness_count}\n"
        f"cycle_outcome: {cycle_outcome}\n"
        f"valid_findings: {valid_findings}\n"
        f"consecutive_zero_valid: {consecutive_zero_valid}\n"
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


def render_status_from_checkpoint(
    milestone: str,
    checkpoint: Checkpoint,
    phase_names: list[str] | None = None,
) -> str:
    """Derive a status.md rendering entirely from a Checkpoint instance.

    ``checkpoint`` is a ``Checkpoint`` dataclass from
    ``clou.recovery_checkpoint``.

    ``phase_names`` is the ordered list of phase names from compose.py.
    When provided, phase progress is derived:
      - phases before ``phases_completed`` index -> "completed"
      - the phase at ``phases_completed`` index (if it matches
        ``current_phase``) -> "in_progress"
      - remaining phases -> "pending"

    When ``phase_names`` is None or empty, falls back to a single-row
    table showing ``current_phase`` as in_progress.
    """
    current_phase: str = checkpoint.current_phase or ""
    cycle: int = checkpoint.cycle
    next_step: str = checkpoint.next_step
    phases_completed: int = checkpoint.phases_completed

    phase_progress: dict[str, str] | None = None

    if phase_names:
        phase_progress = {}
        for i, name in enumerate(phase_names):
            if i < phases_completed:
                phase_progress[name] = "completed"
            elif name == current_phase:
                phase_progress[name] = "in_progress"
            else:
                phase_progress[name] = "pending"

    return render_status(
        milestone=milestone,
        phase=current_phase or "unknown",
        cycle=cycle,
        next_step=next_step,
        phase_progress=phase_progress,
    )


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
