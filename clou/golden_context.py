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
    from clou.recovery_checkpoint import AcceptanceVerdict, Checkpoint


# ---------------------------------------------------------------------------
# Canonical enum sets — single source of truth.
# validation.py and recovery.py should import these, not redefine them.
# ---------------------------------------------------------------------------

VALID_STEPS = frozenset({"PLAN", "EXECUTE", "ASSESS", "REPLAN", "VERIFY", "EXIT", "ORIENT"})
#: ``HALTED`` is the next_step written when the engine's
#: trajectory-halt gate fires (M49b).  The supervisor disposes via
#: ``clou_resolve_escalation`` and updates the checkpoint to restore
#: the pre-halt next_step or route to PLAN/EXIT per user choice.
#: M50 I1 vocabulary canonicalization — punctuated legacy tokens
#: ``EXECUTE (rework)`` and ``EXECUTE (additional verification)`` are
#: rejected by the parser and replaced with the structured identifiers
#: ``EXECUTE_REWORK`` / ``EXECUTE_VERIFY``.  Mirrors
#: ``clou.recovery_checkpoint._VALID_NEXT_STEPS`` (the validator the
#: dispatch layer keys on).  See ``clou.vocabulary_migration`` for the
#: one-shot rewrite of persisted artifacts.
#: M50 I1 cycle-3 rework (F4/F15): ``'none'`` is NOT in the render-
#: side vocabulary.  Accepting it here would let ``render_checkpoint``
#: emit literal ``next_step: none`` to disk while ``parse_checkpoint``
#: silently coerces ``none`` → ``COMPLETE``; two distinct persisted
#: representations would map to one in-memory state, breaking
#: idempotency of the render→parse→render fixed point.  ``'none'``
#: remains tolerated on parse (legacy inputs from the EXIT prompt's
#: ``next_step: none`` convention) via ``parse_checkpoint``'s
#: pre-validation coercion; new writes MUST use ``COMPLETE``.
VALID_NEXT_STEPS = frozenset({
    "PLAN", "EXECUTE", "EXECUTE_REWORK", "EXECUTE_VERIFY",
    "ASSESS", "REPLAN", "VERIFY", "EXIT", "COMPLETE", "ORIENT",
    "HALTED",
})
#: ``HALTED_PENDING_REVIEW`` is the cycle_outcome written alongside
#: ``next_step=HALTED`` when the trajectory-halt gate fires (M49b).
#: Its staleness semantics match ``INCONCLUSIVE`` / ``INTERRUPTED``
#: in ``update_staleness``: the counter resets, not accumulates —
#: the halt is a pause-for-review, not a stall.
VALID_CYCLE_OUTCOMES = frozenset({
    "ADVANCED", "INCONCLUSIVE", "INTERRUPTED", "FAILED",
    "HALTED_PENDING_REVIEW",
})
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
    pre_orient_next_step: str = "",
    pre_halt_next_step: str = "",
    last_acceptance_verdict: "AcceptanceVerdict | None" = None,
) -> str:
    """Render a coordinator checkpoint file.

    All fields are always written explicitly — no omissions, no aliases,
    no ambiguity for ``parse_checkpoint()`` or ``validate_checkpoint()``.

    Retry counters, cycle_outcome, convergence, and the M36/M49b
    stash fields are keyword-only to avoid breaking existing callers
    that pass positional arguments.

    ``pre_orient_next_step``: M36 I1 (F2 rework). Non-empty when the
    session-start ORIENT rewrite has stashed the pre-ORIENT
    ``next_step`` for the ORIENT-exit restoration path to restore on
    the next iteration. Empty string = no pending restoration.

    ``pre_halt_next_step``: M49b B6.  Non-empty when the engine halt
    gate has fired and stashed the prior ``next_step`` for the
    supervisor's ``clou_dispose_halt`` tool to restore under the
    ``continue-as-is`` choice.  Empty string = no halt restoration
    pending (cleared by the dispose verb after rewrite).
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
    if pre_orient_next_step and pre_orient_next_step not in VALID_NEXT_STEPS:
        raise ValueError(
            f"invalid pre_orient_next_step {pre_orient_next_step!r}"
        )
    if pre_halt_next_step and pre_halt_next_step not in VALID_NEXT_STEPS:
        raise ValueError(
            f"invalid pre_halt_next_step {pre_halt_next_step!r}"
        )
    # M49b B6: stashing HALTED would be self-referential — the
    # supervisor's continue-as-is would restore HALTED, the gate
    # would re-fire, infinite loop.  Reject explicitly.
    if pre_halt_next_step == "HALTED":
        raise ValueError(
            "pre_halt_next_step must not be 'HALTED' (would loop)"
        )
    # M49b D3 (closes B7/F3): symmetric guard for pre_orient_next_step.
    # The session-start ORIENT dispatch in run_coordinator stashes the
    # current next_step into pre_orient_next_step before rewriting to
    # ORIENT.  If next_step is HALTED at session start (a contract
    # violation per C1, but reachable through the now-closed crash
    # window in supervisor disposition), stashing HALTED would let
    # ORIENT-exit restoration put it back → determine_next_cycle's
    # HALTED case raises.  Without this guard, the supervisor's
    # disposition crash window had a delayed-action loop.  Reject
    # symmetrically with pre_halt_next_step.
    if pre_orient_next_step == "HALTED":
        raise ValueError(
            "pre_orient_next_step must not be 'HALTED' (would loop "
            "via ORIENT-exit restoration)"
        )
    # M36 F2 (round-4): ORIENT-into-ORIENT would let the ORIENT-exit
    # restoration "restore" next_step=ORIENT back onto next_step=ORIENT.
    # Because the restoration block reads pre_orient_next_step and
    # rewrites next_step to that value, a poisoned stash of ORIENT
    # produces a sink state: every iteration restores ORIENT over
    # ORIENT, the cycle never advances, and the milestone burns _MAX_CYCLES.
    # This is architecturally identical to the HALTED guard above —
    # both close a self-reference loop on the stash.
    if pre_orient_next_step == "ORIENT":
        raise ValueError(
            "pre_orient_next_step must not be 'ORIENT' (would loop "
            "via ORIENT-exit restoration)"
        )

    # M52 F38: serialise ``last_acceptance_verdict`` as
    # ``<phase>|<decision>|<content_sha>`` or the literal ``none``
    # sentinel.  Pipes are forbidden in phase names by ``_PHASE_RE``,
    # so the delimiter is unambiguous.  Reject malformed verdicts at
    # render time so a poisoned verdict never lands on disk.
    if last_acceptance_verdict is None:
        verdict_serialised = "none"
    else:
        from clou.recovery_checkpoint import _VALID_VERDICT_DECISIONS
        if last_acceptance_verdict.decision not in _VALID_VERDICT_DECISIONS:
            raise ValueError(
                f"invalid last_acceptance_verdict.decision "
                f"{last_acceptance_verdict.decision!r} (must be one of "
                f"{sorted(_VALID_VERDICT_DECISIONS)})"
            )
        if "|" in last_acceptance_verdict.phase:
            raise ValueError(
                "last_acceptance_verdict.phase must not contain '|' "
                "(pipe is the wire-format delimiter)"
            )
        if "|" in last_acceptance_verdict.content_sha:
            raise ValueError(
                "last_acceptance_verdict.content_sha must not contain "
                "'|' (pipe is the wire-format delimiter)"
            )
        if "\n" in last_acceptance_verdict.phase or "\n" in last_acceptance_verdict.content_sha:
            raise ValueError(
                "last_acceptance_verdict fields must not contain newlines"
            )
        verdict_serialised = (
            f"{last_acceptance_verdict.phase}|"
            f"{last_acceptance_verdict.decision}|"
            f"{last_acceptance_verdict.content_sha}"
        )

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
        f"pre_orient_next_step: {pre_orient_next_step}\n"
        f"pre_halt_next_step: {pre_halt_next_step}\n"
        f"last_acceptance_verdict: {verdict_serialised}\n"
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
    # M50 I1 cycle-4 rework (F20): validate ``next_step`` with the
    # same discipline as every other VALID_NEXT_STEPS check —
    # empty-string is NOT a silent-tolerance sentinel.  The prior
    # ``if next_step and ...`` short-circuit preserved the same
    # silent-tolerance pattern that cycle-3's ``"none"`` narrowing
    # was trying to eliminate.  Empty-string now fails alongside
    # any other invalid value.  Callers that previously relied on
    # the ``next_step=""`` default must supply an explicit token —
    # the rendered status file simply omits the ``next_step:`` line
    # when a dispatch is not yet scheduled, but the contract
    # demands the caller name "no dispatch scheduled" explicitly.
    if next_step not in VALID_NEXT_STEPS:
        raise ValueError(
            f"invalid next_step {next_step!r} (must be one of "
            f"{sorted(VALID_NEXT_STEPS)} — empty string is NOT a "
            f"silent-tolerance sentinel)"
        )
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
