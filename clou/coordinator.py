"""Coordinator cycle engine — session-per-cycle milestone execution.

Extracted from orchestrator.py.  Contains the coordinator's multi-cycle
loop (``run_coordinator``), single-cycle execution (``_run_single_cycle``),
and their shared helpers: token tracking, agent team construction,
milestone validation, environment probing, and context exhaustion detection.

The supervisor session and CLI entry points remain in ``orchestrator.py``.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clou.ui.app import ClouApp

from clou.ui.bridge import parse_escalation
from clou.ui.messages import (
    ClouBreathEvent,
    ClouBudgetWarning,
    ClouCoordinatorPaused,
    ClouCycleComplete,
    ClouDagUpdate,
    ClouEscalationArrived,
    ClouStatusUpdate,
)

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    SandboxSettings,
    TaskNotificationMessage,
)

from clou.coordinator_tools import build_coordinator_mcp_server
from clou.golden_context import render_checkpoint
from clou.harness import (
    HarnessTemplate,
    load_template,
    read_template_name,
    template_mcp_servers,
)
from clou.hooks import build_hooks, to_sdk_hooks
from clou.ui.bridge import is_task_complete, is_task_progress, is_task_started
from clou.ui.task_graph import match_agent_to_task
from clou.prompts import build_cycle_prompt, load_prompt
from clou.recovery import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_COOLDOWN,
    ErrorKind,
    archive_milestone_episodic,
    assess_convergence,
    attempt_self_heal,
    classify_error,
    consolidate_milestone,
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    log_self_heal_attempt,
    parse_checkpoint,
    read_cycle_count,
    read_cycle_outcome,
    validate_milestone_name,
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_staleness_escalation,
    write_validation_escalation,
)
from clou.tokens import (
    MODEL,
    context_exhausted as _context_exhausted,
    cumulative_cost_usd as _cumulative_cost_usd,
    track as _track,
    tracker as _tracker,
)
from clou.validation import (
    ValidationFinding,
    errors_only,
    validate_delivery,
    validate_golden_context,
    validate_readiness,
    warnings_only,
)
from clou.shard import clean_stale_shards
from clou import telemetry

log = logging.getLogger("clou")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CYCLES = 20
_NEXT_STEP: dict[str, str] = {
    "PLAN": "EXECUTE",
    "EXECUTE": "ASSESS",
    "ASSESS": "VERIFY",
    "REPLAN": "EXECUTE",
    "VERIFY": "EXIT",
    "EXIT": "COMPLETE",
}
_MAX_VALIDATION_RETRIES = 3
_MAX_CRASH_RETRIES = 3
_STALENESS_THRESHOLD = 3
_MAX_BUDGET_USD: float | None = None  # No per-cycle cost cap by default
_MAX_TURNS: int = 200  # Per-cycle turn limit — prevents infinite agent loops

#: Phase names: lowercase alphanumeric with hyphens or underscores.
#: Defense-in-depth duplicate of recovery_checkpoint._PHASE_RE (F22).
_PHASE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")


class StalenessState:
    """Mutable staleness tracking state -- extracted for testability."""

    __slots__ = (
        "count", "prev_cycle_type", "prev_phases_completed",
        "saw_type_change", "last_cycle_outcome",
    )

    def __init__(
        self,
        *,
        count: int = 0,
        prev_cycle_type: str | None = None,
        prev_phases_completed: int = -1,
        saw_type_change: bool = False,
        last_cycle_outcome: str = "ADVANCED",
    ) -> None:
        self.count = count
        self.prev_cycle_type = prev_cycle_type
        self.prev_phases_completed = prev_phases_completed
        self.saw_type_change = saw_type_change
        self.last_cycle_outcome = last_cycle_outcome


def update_staleness(
    state: StalenessState,
    cycle_type: str,
    phases_completed: int,
) -> None:
    """Update staleness state after a cycle completes.

    Rules:
    - INCONCLUSIVE / INTERRUPTED outcome: reset counter (not stuck).
    - Cycle type changed: track the change, reset counter to 1.
    - Phase advancement: reset counter to 1 (real progress).
    - Same type, no advancement, but type changed since last reset:
      reset counter to 1 (rework pattern, not staleness).
    - Otherwise: increment counter.
    """
    if state.last_cycle_outcome in ("INCONCLUSIVE", "INTERRUPTED"):
        state.count = 0
        state.prev_cycle_type = cycle_type
        state.prev_phases_completed = phases_completed
        state.saw_type_change = False
    elif cycle_type != state.prev_cycle_type:
        state.saw_type_change = True
        state.count = 1
        state.prev_cycle_type = cycle_type
        state.prev_phases_completed = phases_completed
    elif phases_completed != state.prev_phases_completed:
        state.count = 1
        state.saw_type_change = False
        state.prev_phases_completed = phases_completed
    else:
        if state.saw_type_change:
            state.saw_type_change = False
            state.count = 1
        else:
            state.count += 1


# Encapsulated coordinator state — no module-level mutable variables.
# Access via get_active_app() / set_active_app() rather than global keyword.
from types import SimpleNamespace as _SimpleNamespace

_state = _SimpleNamespace(app=None)


def get_active_app() -> ClouApp | None:
    """Return the active Textual app, or None if not running."""
    return _state.app


def set_active_app(app: ClouApp | None) -> None:
    """Set (or clear) the active Textual app reference."""
    _state.app = app


_ENV_PROBE_MAX_LINES: int = 20
_DEFAULT_TIMEOUT_SECONDS: int = 600  # 10 minutes default per-task timeout


# ---------------------------------------------------------------------------
# Selective abort helpers
# ---------------------------------------------------------------------------


def _compute_abort_set(
    failed_task: str,
    deps: dict[str, list[str]],
    active_tasks: set[str],
) -> set[str]:
    """Compute which active tasks should be aborted after a failure.

    Returns the set of active task names that transitively depend on
    *failed_task*.  Tasks with no dependency path to the failed task
    are not included -- they continue executing.

    Args:
        failed_task: Name of the task that failed.
        deps: Dependency map from ``extract_dag_data`` (task -> list of
              tasks it depends on).
        active_tasks: Set of currently running task names.
    """
    # Build reverse dependency map: task -> set of tasks that depend on it.
    dependents: dict[str, set[str]] = {}
    for task, task_deps in deps.items():
        for dep in task_deps:
            dependents.setdefault(dep, set()).add(task)

    # BFS from failed_task through dependents.
    to_abort: set[str] = set()
    queue = list(dependents.get(failed_task, set()))
    while queue:
        task = queue.pop()
        if task in to_abort:
            continue
        to_abort.add(task)
        for downstream in dependents.get(task, set()):
            if downstream not in to_abort:
                queue.append(downstream)

    # Only abort tasks that are actually still active.
    return to_abort & active_tasks


# ---------------------------------------------------------------------------
# Timeout classification
# ---------------------------------------------------------------------------


def classify_timeout(
    last_messages: list[object],
    active_task_ids: set[str],
    task_start_times: dict[str, float],
    effective_timeout: float,
) -> tuple[str, str]:
    """Classify a timeout event as an interruption or a genuine crash.

    Inspects the message history and agent activity to distinguish between
    sleep/network interruptions (where the agent was recently active) and
    genuine process crashes (where the agent was never active or stopped
    responding long ago).

    Returns ``(classification, evidence)`` where *classification* is
    ``"interrupted"`` or ``"crashed"``.

    Classification rules (evaluated in order):
    1. No messages ever received from any task -> ``"crashed"``
       (process never started).
    2. Last message was a ``TaskNotificationMessage`` with status ``"failed"``
       -> ``"crashed"`` (agent reported its own failure).
    3. Active agents still running -> ``"interrupted"`` (agents are alive
       but blocked on long-running tool calls, e.g. MCP brutalist panel).
    4. Last message was a task progress message with recent tool use
       -> ``"interrupted"`` (agent was active, likely waiting on network/sleep).
    5. Default: ``"crashed"`` (conservative -- false negatives are worse
       than false positives).
    """
    # Rule 1: no messages at all.
    if not last_messages:
        return ("crashed", "no messages received from any task")

    last = last_messages[-1]

    # Rule 2: last message reports a task failure.
    if (
        isinstance(last, TaskNotificationMessage)
        and getattr(last, "status", None) == "failed"
    ):
        summary = getattr(last, "summary", "")
        return (
            "crashed",
            f"last message was task failure notification: {summary!r}",
        )

    # Rule 3: agents are still active (spawned but not yet completed).
    # They may be blocked on a long-running MCP tool call (e.g. the
    # brutalist multi-model panel takes 2-12 minutes).  During that
    # time no TaskProgressMessage is emitted because the agent is
    # waiting for the tool response, not making new tool calls.
    # This is work-in-progress, not a crash.
    if active_task_ids:
        return (
            "interrupted",
            f"{len(active_task_ids)} agent(s) still active "
            f"(likely blocked on in-flight tool call)",
        )

    # Rule 4: last message was a progress message (agent was actively
    # using tools).  The idle watchdog already only fires after
    # effective_timeout seconds of silence, so if the *last* message
    # in our ring buffer is a progress message, it means the agent was
    # working right up until the timeout -- likely a long-running
    # network call or sleep.
    if is_task_progress(last):
        tool = getattr(last, "last_tool_name", "unknown")
        return (
            "interrupted",
            f"last message was task progress (tool={tool!r}); "
            f"agent was active before timeout",
        )

    # Rule 5: conservative default.
    msg_type = type(last).__name__
    return ("crashed", f"conservative default (last message type: {msg_type})")


def _write_failure_shard(
    milestone_dir: Path,
    phase: str,
    task_name: str,
    failure_type: str,
    error_detail: str,
    dependency_impact: list[str],
) -> Path:
    """Write a coordinator-generated failure record to a task's shard file.

    Used when the coordinator terminates a task (timeout or budget
    exceeded) and the worker cannot write its own failure record.

    Returns:
        The path of the written shard file.
    """
    # Defense-in-depth: reject path traversal in phase.
    if ".." in phase or "/" in phase:
        msg = f"Invalid phase: {phase!r} (must not contain '..' or '/')"
        raise ValueError(msg)

    from clou.shard import write_shard_path

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rel_path = write_shard_path(milestone_dir.name, phase, task_name)
    shard_path = milestone_dir / rel_path

    impact_str = ", ".join(dependency_impact) if dependency_impact else "none"
    content = (
        f"## Summary\n"
        f"status: failed\n"
        f"started: {now}\n"
        f"completed: {now}\n"
        f"tasks: 1 total, 0 completed, 1 failed, 0 in_progress\n"
        f"failures: {task_name}\n"
        f"blockers: none\n\n"
        f"### T1: {task_name}\n"
        f"**Status:** failed\n"
        f"**Failure Type:** {failure_type}\n"
        f"**Error:** {error_detail}\n"
        f"**Partial Work:** See {rel_path} for any partial output\n"
        f"**Dependency Impact:** Downstream tasks blocked: {impact_str}\n"
        f"**Files changed:** unknown (terminated before completion)\n"
    )

    shard_path.parent.mkdir(parents=True, exist_ok=True)
    shard_path.write_text(content, encoding="utf-8")
    return shard_path


async def _capture_working_tree_state(project_dir: Path) -> str | None:
    """Capture git diff --stat for the working tree (truncated).

    Returns the diff stat output (max ``_ENV_PROBE_MAX_LINES`` lines),
    or None if clean or error.  Used to make partial work from failed
    cycles visible to the next cycle's coordinator (DB-15 D6:
    describe the environment, don't try to control it).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode(errors="replace").strip()
        if not output:
            return None
        lines = output.splitlines()
        if len(lines) > _ENV_PROBE_MAX_LINES:
            shown = "\n".join(lines[:_ENV_PROBE_MAX_LINES])
            return f"{shown}\n... and {len(lines) - _ENV_PROBE_MAX_LINES} more files"
        return output
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Agent team definitions
# ---------------------------------------------------------------------------


def _build_agents(
    project_dir: Path,
    milestone: str,
    template: HarnessTemplate | None = None,
) -> dict[str, AgentDefinition]:
    """Build AgentDefinition dict for coordinator's agent teams.

    When *template* is provided, agent definitions are derived from the
    template's ``agents`` dict.  Otherwise falls back to the default
    software-construction template.
    """
    if template is None:
        template = load_template("software-construction")

    return {
        name: AgentDefinition(
            description=spec.description,
            prompt=load_prompt(spec.prompt_ref, project_dir, milestone=milestone),
            tools=spec.tools,
            model=spec.model,
        )
        for name, spec in template.agents.items()
    }


# ---------------------------------------------------------------------------
# Coordinator session-per-cycle loop
# ---------------------------------------------------------------------------


async def run_coordinator(
    project_dir: Path,
    milestone: str,
    app: ClouApp | None = None,
) -> str:
    """Run a coordinator for a single milestone via session-per-cycle loop."""
    set_active_app(app)

    validate_milestone_name(milestone)

    # Load the active harness template once per milestone.
    tmpl_name = read_template_name(project_dir)
    tmpl = load_template(tmpl_name)
    log.info("Using harness template: %s", tmpl.name)

    _pause_on_message: bool = getattr(tmpl, "pause_on_user_message", False)

    clou_dir = project_dir / ".clou"
    checkpoint_path = clou_dir / "milestones" / milestone / "active" / "coordinator.md"
    # Outside .clou/active/ so git_revert_golden_context doesn't touch it.
    milestone_marker = clou_dir / ".coordinator-milestone"

    # Restore retry counters from checkpoint (survives process restart).
    _initial_cp = (
        parse_checkpoint(checkpoint_path.read_text())
        if checkpoint_path.exists()
        else None
    )
    validation_retries = _initial_cp.validation_retries if _initial_cp else 0
    readiness_retries = _initial_cp.readiness_retries if _initial_cp else 0
    crash_retries = _initial_cp.crash_retries if _initial_cp else 0
    pending_validation_errors: list[ValidationFinding] | None = None
    _pending_working_tree: str | None = None

    # Staleness detection state (F3).
    _stale = StalenessState(
        count=_initial_cp.staleness_count if _initial_cp else 0,
        last_cycle_outcome=_initial_cp.cycle_outcome if _initial_cp else "ADVANCED",
    )

    decisions_path = clou_dir / "milestones" / milestone / "decisions.md"
    seen_path = clou_dir / "active" / "seen-escalations.txt"
    seen_escalations: set[str] = set()
    if seen_path.exists():
        seen_escalations = set(seen_path.read_text().splitlines())

    # DB-15 D5: Reset cycle count if the LATEST escalation was resolved.
    # Check only the most recent escalation (sorted by timestamp filename).
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    if esc_dir.is_dir() and checkpoint_path.exists():
        esc_files = sorted(esc_dir.glob("*.md"))
        latest = esc_files[-1] if esc_files else None
        _esc_text = latest.read_text(encoding="utf-8") if latest else ""
        resolved = bool(
            latest
            and re.search(
                r"(?m)^status:\s*(resolved|overridden)",
                _esc_text,
            )
        )
        if resolved:
            # Only act if not already consumed (prevent replay on re-spawn).
            cp = parse_checkpoint(checkpoint_path.read_text())
            if cp.cycle > 0:
                # Emit escalation.resolved telemetry (G4).
                # Inside cp.cycle > 0 guard to prevent replay on restart.
                # Classification uses markdown bold format: **Classification:** ...
                _esc_class_match = re.search(
                    r"\*\*Classification:\*\*\s*(.+)",
                    _esc_text,
                )
                telemetry.event(
                    "escalation.resolved",
                    milestone=milestone,
                    cycle_num=cp.cycle + 1,
                    classification=(
                        _esc_class_match.group(1).strip()
                        if _esc_class_match else "unknown"
                    ),
                )
                checkpoint_path.write_text(
                    render_checkpoint(
                        cycle=0,
                        step=cp.step,
                        next_step=cp.next_step,
                        current_phase=cp.current_phase,
                        phases_completed=cp.phases_completed,
                        phases_total=cp.phases_total,
                        validation_retries=cp.validation_retries,
                        readiness_retries=cp.readiness_retries,
                        crash_retries=cp.crash_retries,
                        staleness_count=cp.staleness_count,
                        cycle_outcome=cp.cycle_outcome,
                        valid_findings=cp.valid_findings,
                        consecutive_zero_valid=cp.consecutive_zero_valid,
                    )
                )
            log.info(
                "Cycle count reset for %r after resolved escalation",
                milestone,
            )

    def _post_new_escalations() -> None:
        """Scan for new escalation files and post them to the UI."""
        _app = get_active_app()
        if _app is None:
            return
        esc_dir = clou_dir / "milestones" / milestone / "escalations"
        if not esc_dir.is_dir():
            return
        for esc_file in sorted(esc_dir.glob("*.md")):
            if esc_file.name not in seen_escalations:
                seen_escalations.add(esc_file.name)
                seen_path.write_text(
                    "\n".join(sorted(seen_escalations)) + "\n"
                )
                try:
                    data = parse_escalation(esc_file)
                    _app.post_message(
                        ClouEscalationArrived(
                            path=esc_file,
                            classification=data["classification"],
                            issue=data["issue"],
                            options=data["options"],
                        )
                    )
                except Exception:
                    log.debug(
                        "Could not parse escalation %s",
                        esc_file,
                        exc_info=True,
                    )

    # Clear stale checkpoint from a previous milestone.
    # Serial execution guarantees one coordinator at a time, so a
    # checkpoint belonging to a different milestone is always stale.
    if checkpoint_path.exists():
        prev = milestone_marker.read_text().strip() if milestone_marker.exists() else ""
        if prev != milestone:
            checkpoint_path.unlink()
    milestone_marker.parent.mkdir(parents=True, exist_ok=True)
    milestone_marker.write_text(milestone)

    def _persist_retry_counters() -> None:
        """Merge current retry counters and cycle outcome into the checkpoint.

        Reads the existing checkpoint, re-renders it with the current
        retry counter values and cycle outcome, and writes it back.
        No-op if the checkpoint does not exist yet.
        """
        if not checkpoint_path.exists():
            return
        cp = parse_checkpoint(checkpoint_path.read_text())
        checkpoint_path.write_text(
            render_checkpoint(
                cycle=cp.cycle,
                step=cp.step,
                next_step=cp.next_step,
                current_phase=cp.current_phase,
                phases_completed=cp.phases_completed,
                phases_total=cp.phases_total,
                validation_retries=validation_retries,
                readiness_retries=readiness_retries,
                crash_retries=crash_retries,
                staleness_count=_stale.count,
                cycle_outcome=_stale.last_cycle_outcome,
                valid_findings=cp.valid_findings,
                consecutive_zero_valid=cp.consecutive_zero_valid,
            )
        )

    _ms_outcome = "unknown"
    _last_rework_decision_cycle: int | None = None  # G6: decision outcome tracking
    telemetry.event("milestone.start", milestone=milestone)
    try:
        while True:
            # --- Cycle-boundary checks (DB-15) ---

            # Check for /stop request.
            _app = get_active_app()
            if (
                _app is not None
                and hasattr(_app, "_stop_requested")
                and isinstance(_app._stop_requested, asyncio.Event)
                and _app._stop_requested.is_set()
            ):
                _app._stop_requested.clear()
                log.info("Stop requested for %r at cycle boundary", milestone)
                _ms_outcome = "stopped"
                return "stopped"

            # Check for user messages at cycle boundary.
            # If the user typed during autonomous work, pause the coordinator
            # and let the supervisor handle it — but only when the harness
            # template opts in via pause_on_user_message (default: False).
            if (
                _pause_on_message
                and _app is not None
                and hasattr(_app, "_user_input_queue")
                and isinstance(_app._user_input_queue, deque)
                and _app._user_input_queue
            ):
                cycle_count_now = read_cycle_count(checkpoint_path)
                log.info(
                    "User message pending at cycle boundary for %r "
                    "(cycle %d) — pausing coordinator",
                    milestone,
                    cycle_count_now,
                )
                _app.post_message(
                    ClouCoordinatorPaused(
                        cycle_num=cycle_count_now,
                        reason="user message pending",
                    )
                )
                _ms_outcome = "paused"
                return "paused"

            # Check budget (DB-15 D2a).
            if tmpl.budget_usd is not None:
                spent = _cumulative_cost_usd.get(milestone, 0.0)
                pct = spent / tmpl.budget_usd if tmpl.budget_usd > 0 else 0.0
                if pct >= 1.0:
                    log.warning(
                        "Budget exhausted for %r: $%.2f / $%.2f",
                        milestone, spent, tmpl.budget_usd,
                    )
                    _ms_outcome = "escalated_budget"
                    return "escalated_budget"
                if pct >= 0.5 and _app is not None:
                    threshold = 75 if pct >= 0.75 else 50
                    _app.post_message(
                        ClouBudgetWarning(
                            spent_usd=spent,
                            budget_usd=tmpl.budget_usd,
                            pct=threshold,
                        )
                    )

            # Check checkpoint for rework before determine_next_cycle
            # collapses the next_step variants (DB-18).
            _rework_requested = False
            _cp_pre = None
            if checkpoint_path.exists():
                _cp_pre = parse_checkpoint(checkpoint_path.read_text())
                if "rework" in _cp_pre.next_step.lower():
                    _rework_requested = True

            cycle_type, read_set = determine_next_cycle(
                checkpoint_path,
                milestone,
                decisions_path=decisions_path,
            )

            # Emit rework telemetry only when checkpoint explicitly
            # requested rework, not on any ASSESS→EXECUTE transition (DB-18).
            if _rework_requested and cycle_type == "EXECUTE":
                telemetry.event(
                    "cycle.rework",
                    milestone=milestone,
                    cycle_num=read_cycle_count(checkpoint_path) + 1,
                    from_step="ASSESS",
                    to_step="EXECUTE",
                    phase=_cp_pre.current_phase,
                )
                _last_rework_decision_cycle = (
                    read_cycle_count(checkpoint_path) + 1
                )

            try:
                telemetry.event(
                    "read_set.composition",
                    milestone=milestone,
                    cycle_num=read_cycle_count(checkpoint_path) + 1,
                    cycle_type=cycle_type,
                    file_count=len(read_set),
                    files=read_set,
                )
            except Exception:
                pass  # telemetry must never break the orchestrator

            # Emit quality gate decision telemetry (I3/F3).
            # _cp_pre.step tells us what the previous cycle type was;
            # cycle_type tells us what comes next after the gate.
            # Use _cp_pre.cycle + 1 for the current cycle number —
            # consistent with _run_single_cycle's cycle_num parameter.
            if _cp_pre is not None:
                try:
                    _prev_step = _cp_pre.step
                    _current_cycle_num = _cp_pre.cycle + 1
                    if _prev_step == "ASSESS":
                        if cycle_type == "EXECUTE" and _rework_requested:
                            _gate_decision = "rework"
                        elif cycle_type == "EXECUTE":
                            _gate_decision = "advance"
                        else:
                            _gate_decision = "accept"
                        telemetry.event(
                            "quality_gate.decision",
                            milestone=milestone,
                            cycle_num=_current_cycle_num,
                            gate_type="assess",
                            decision=_gate_decision,
                        )
                    elif _prev_step == "VERIFY":
                        if cycle_type == "EXECUTE" and _rework_requested:
                            _gate_decision = "rework"
                        elif cycle_type == "EXECUTE":
                            _gate_decision = "advance"
                        else:
                            _gate_decision = "accept"
                        telemetry.event(
                            "quality_gate.decision",
                            milestone=milestone,
                            cycle_num=_current_cycle_num,
                            gate_type="verify",
                            decision=_gate_decision,
                        )
                except Exception:
                    log.warning("Gate telemetry emission failed", exc_info=True)

            if cycle_type == "COMPLETE":
                log.info("Milestone %r complete", milestone)
                if seen_path.exists():
                    seen_path.unlink()
                _ms_outcome = "completed"
                return "completed"

            cycle_count = read_cycle_count(checkpoint_path)
            if cycle_count >= _MAX_CYCLES:
                log.warning("Milestone %r hit %d-cycle limit", milestone, _MAX_CYCLES)
                await write_cycle_limit_escalation(project_dir, milestone, cycle_count)
                telemetry.event(
                    "escalation.created", milestone=milestone,
                    cycle_num=cycle_count + 1, classification="cycle_limit",
                    severity="blocking",
                )
                _post_new_escalations()
                _ms_outcome = "escalated_cycle_limit"
                return "escalated_cycle_limit"

            # Staleness detection (F3): track consecutive same-type cycles
            # with no phase advancement.
            _cp_now = (
                parse_checkpoint(checkpoint_path.read_text())
                if checkpoint_path.exists()
                else None
            )
            _phases_now = _cp_now.phases_completed if _cp_now else 0

            update_staleness(_stale, cycle_type, _phases_now)

            if _stale.count >= _STALENESS_THRESHOLD:
                _cp_next = _cp_now.next_step if _cp_now else "unknown"
                log.warning(
                    "Staleness detected for %r: %s repeated %d times "
                    "with phases_completed=%d",
                    milestone, cycle_type, _stale.count, _phases_now,
                )
                await write_staleness_escalation(
                    project_dir, milestone, cycle_type,
                    _stale.count, _phases_now, _cp_next,
                )
                telemetry.event(
                    "escalation.created", milestone=milestone,
                    cycle_num=cycle_count + 1, classification="staleness",
                    severity="blocking",
                )
                _post_new_escalations()
                _ms_outcome = "escalated_staleness"
                return "escalated_staleness"

            # Pre-cycle readiness: verify the context this cycle needs exists.
            milestone_dir = clou_dir / "milestones" / milestone
            readiness = validate_readiness(
                clou_dir, milestone_dir, read_set, cycle_type, milestone,
            )
            readiness_errors = errors_only(readiness)
            if readiness_errors:
                readiness_retries += 1
                log.warning(
                    "Readiness check failed for %r %s (attempt %d/%d): %s",
                    milestone, cycle_type,
                    readiness_retries, _MAX_VALIDATION_RETRIES,
                    [e.message for e in readiness_errors],
                )
                telemetry.event(
                    "readiness_failed", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                    error_count=len(readiness_errors),
                    attempt=readiness_retries,
                )
                if readiness_retries >= _MAX_VALIDATION_RETRIES:
                    await write_validation_escalation(
                        project_dir, milestone, readiness,
                    )
                    telemetry.event(
                        "escalation.created", milestone=milestone,
                        cycle_num=cycle_count + 1,
                        classification="validation_failure",
                        severity="blocking",
                    )
                    _post_new_escalations()
                    _ms_outcome = "escalated_validation"
                    return "escalated_validation"
                _persist_retry_counters()
                continue
            readiness_warnings = warnings_only(readiness)
            if readiness_warnings:
                log.info(
                    "Readiness warnings for %r %s (non-blocking): %s",
                    milestone, cycle_type,
                    [w.message for w in readiness_warnings],
                )

            # Extract DAG before prompt building — same data feeds UI and prompt.
            dag_data = None
            compose_path = clou_dir / "milestones" / milestone / "compose.py"
            if compose_path.exists():
                try:
                    from clou.graph import extract_dag_data

                    source = compose_path.read_text(encoding="utf-8")
                    dag_data = extract_dag_data(source)
                except Exception:
                    log.debug("Could not parse DAG from compose.py", exc_info=True)

            # For EXECUTE cycles, probe environment state even without
            # a prior failure — the agent team should see the codebase
            # as it actually is (describe-and-adapt, DB-15).
            env_state = _pending_working_tree
            if env_state is None and cycle_type == "EXECUTE":
                env_state = await _capture_working_tree_state(project_dir)

            # Extract current_phase from checkpoint for path resolution.
            _current_phase: str | None = None
            if checkpoint_path.exists():
                try:
                    _current_phase = parse_checkpoint(
                        checkpoint_path.read_text()
                    ).current_phase or None
                except Exception:
                    pass

            # Compute routing context for ASSESS cycles — deterministic
            # graph computation so the coordinator only judges findings.
            _routing_context: dict[str, Any] | None = None
            if cycle_type == "ASSESS" and dag_data is not None:
                try:
                    from clou.graph import compute_layers

                    _cp_rc = parse_checkpoint(checkpoint_path.read_text())
                    tasks_list_rc, deps_dict_rc = dag_data
                    layers_rc = compute_layers(tasks_list_rc, deps_dict_rc)

                    # Find current layer and next phase
                    current_layer: list[str] = []
                    next_phase = "all_complete"
                    for i, layer in enumerate(layers_rc):
                        if _cp_rc.current_phase in layer:
                            current_layer = layer
                            if i + 1 < len(layers_rc):
                                next_phase = layers_rc[i + 1][0]
                            break

                    _routing_context = {
                        "current_layer": current_layer,
                        "next_phase": next_phase,
                        "layer_size": len(current_layer),
                        "phases_completed": _cp_rc.phases_completed,
                        "phases_total": _cp_rc.phases_total,
                    }
                except Exception:
                    log.debug(
                        "Could not compute routing context for ASSESS",
                        exc_info=True,
                    )

            # DB-20: Pre-compose ASSESS context into a summary file.
            # Replaces the raw 5+ file read set with <=2 pre-composed files.
            if cycle_type == "ASSESS" and _current_phase:
                # F7: Normalize to lowercase before _PHASE_RE check so
                # mixed-case phase names from golden_context.py still match.
                _current_phase = _current_phase.lower()
                # F22: Validate _current_phase against _PHASE_RE before
                # passing to precompose_assess_context (defense in depth).
                if not _PHASE_RE.match(_current_phase):
                    log.warning(
                        "ASSESS pre-composition skipped: invalid phase %r",
                        _current_phase,
                    )
                else:
                    try:
                        from clou.precompose import precompose_assess_context
                        from clou.graph import get_colayer_tasks

                        _co_layer: list[str] = [_current_phase]
                        if compose_path.exists():
                            try:
                                _compose_src = compose_path.read_text(
                                    encoding="utf-8",
                                )
                                _co_layer = get_colayer_tasks(
                                    _compose_src, _current_phase,
                                )
                            except Exception as _colayer_exc:
                                # F7: Log the exception and emit telemetry so
                                # scope narrowing is observable.
                                log.warning(
                                    "get_colayer_tasks failed for phase %r; "
                                    "falling back to single-task layer",
                                    _current_phase,
                                    exc_info=True,
                                )
                                telemetry.event(
                                    "precompose.colayer_fallback",
                                    milestone=milestone,
                                    cycle_num=read_cycle_count(
                                        checkpoint_path,
                                    ) + 1,
                                    phase=_current_phase,
                                    error=str(_colayer_exc),
                                )
                            else:
                                # F1: Detect trivial fallback — get_colayer_tasks
                                # returns [task_name] on internal parse failure
                                # without raising.  Make this observable.
                                if _co_layer == [_current_phase]:
                                    log.warning(
                                        "get_colayer_tasks returned trivial "
                                        "fallback for phase %r",
                                        _current_phase,
                                    )
                                    telemetry.event(
                                        "precompose.colayer_fallback",
                                        milestone=milestone,
                                        cycle_num=read_cycle_count(
                                            checkpoint_path,
                                        ) + 1,
                                        phase=_current_phase,
                                        error="trivial fallback: parse "
                                              "returned [task_name]",
                                    )

                        # F2: precompose_assess_context is now synchronous.
                        _summary_path = precompose_assess_context(
                            milestone_dir, _current_phase, _co_layer,
                        )

                        # F4: Post-precompose validation — verify the summary
                        # was created and is non-empty before replacing read set.
                        if (
                            not _summary_path.exists()
                            or _summary_path.stat().st_size == 0
                        ):
                            raise RuntimeError(
                                f"assess_summary.md missing or empty: "
                                f"{_summary_path}"
                            )

                        # Replace read set: summary + requirements.md for evaluator.
                        _summary_rel = str(
                            _summary_path.relative_to(milestone_dir),
                        )
                        _precomposed_set = [_summary_rel, "requirements.md"]
                        # Preserve filtered memory if it was in the original set.
                        if "active/_filtered_memory.md" in read_set:
                            _precomposed_set.append(
                                "active/_filtered_memory.md",
                            )
                        log.info(
                            "ASSESS pre-composition: %d -> %d files",
                            len(read_set),
                            len(_precomposed_set),
                        )
                        telemetry.event(
                            "precompose.assess",
                            milestone=milestone,
                            cycle_num=read_cycle_count(checkpoint_path) + 1,
                            original_files=len(read_set),
                            precomposed_files=len(_precomposed_set),
                        )
                        # F3: Emit post-precompose composition event so the
                        # Cognitive Load table uses post-precompose data.  The
                        # original read_set.composition emitted earlier
                        # (line ~706) remains for before/after comparison.
                        telemetry.event(
                            "read_set.composition",
                            milestone=milestone,
                            cycle_num=read_cycle_count(checkpoint_path) + 1,
                            cycle_type=cycle_type,
                            file_count=len(_precomposed_set),
                            files=_precomposed_set,
                            pre_composed=True,
                        )
                        read_set = _precomposed_set
                    except Exception:
                        log.warning(
                            "ASSESS pre-composition failed; using raw read set",
                            exc_info=True,
                        )
                        # F6: Delete stale assess_summary.md on precomposition
                        # failure to prevent the coordinator from reading stale
                        # data from a prior cycle.
                        # F2: Symlink and boundary validation before unlink.
                        _stale_summary = milestone_dir / "active" / "assess_summary.md"
                        if _stale_summary.exists():
                            try:
                                # Reject symlinks — never follow a link.
                                if _stale_summary.is_symlink():
                                    log.warning(
                                        "Refusing to unlink symlink: %s",
                                        _stale_summary,
                                    )
                                # Boundary check: resolved path must be under
                                # the milestone directory.
                                elif not str(
                                    _stale_summary.resolve()
                                ).startswith(
                                    str(milestone_dir.resolve()) + "/"
                                ):
                                    log.warning(
                                        "assess_summary.md resolved outside "
                                        "milestone boundary: %s",
                                        _stale_summary.resolve(),
                                    )
                                else:
                                    _stale_summary.unlink()
                            except OSError:
                                log.debug(
                                    "Could not delete stale assess_summary.md",
                                    exc_info=True,
                                )

            prompt = build_cycle_prompt(
                project_dir,
                milestone,
                cycle_type,
                read_set,
                validation_errors=pending_validation_errors,
                template=tmpl,
                dag_data=dag_data if cycle_type == "EXECUTE" else None,
                working_tree_state=env_state,
                current_phase=_current_phase,
                routing_context=_routing_context,
            )
            pending_validation_errors = None  # consumed
            _pending_working_tree = None

            log.info(
                "Milestone %r: cycle %d, type %s",
                milestone,
                cycle_count + 1,
                cycle_type,
            )

            if _app is not None:
                _app.post_message(
                    ClouStatusUpdate(
                        cycle_type=cycle_type,
                        cycle_num=cycle_count + 1,
                        phase="",
                    )
                )

                # Post DAG at cycle start too — compose.py exists from PLAN onward.
                if dag_data is not None:
                    try:
                        tasks, deps = dag_data
                        _app.post_message(ClouDagUpdate(tasks=tasks, deps=deps))
                    except Exception:
                        log.debug("Could not post DAG to UI", exc_info=True)

            _tok_before = _tracker.coordinator(milestone)
            _tracker.reset_cycle_peak()
            with telemetry.span(
                "cycle", milestone=milestone, cycle_num=cycle_count + 1,
                cycle_type=cycle_type, phase=_current_phase or "",
            ) as _cy:
                _crash_context: dict[str, str] = {}
                status = await _run_single_cycle(
                    project_dir, milestone, cycle_type, prompt,
                    cycle_num=cycle_count + 1, template=tmpl, app=app,
                    crash_context=_crash_context,
                )
                _tok_after = _tracker.coordinator(milestone)
                _cy["outcome"] = status
                _cy["input_tokens"] = _tok_after["input"] - _tok_before["input"]
                _cy["output_tokens"] = _tok_after["output"] - _tok_before["output"]
                _cy["peak_input_tokens"] = _tracker.cycle_peak_input

                # Convergence tracking: enrich cycle span with checkpoint
                # convergence fields written by the coordinator agent.
                if checkpoint_path.exists():
                    try:
                        _cp_post = parse_checkpoint(
                            checkpoint_path.read_text()
                        )
                        _cy["valid_findings"] = _cp_post.valid_findings
                        _cy["consecutive_zero_valid"] = (
                            _cp_post.consecutive_zero_valid
                        )
                    except Exception:
                        pass

            # Compositional telemetry: reference density (DB-18).
            try:
                from clou.telemetry import compute_reference_density
                ms_dir = clou_dir / "milestones" / milestone
                output_parts = []
                for f in ["status.md", "decisions.md", "active/coordinator.md"]:
                    p = ms_dir / f
                    if p.exists():
                        output_parts.append(p.read_text(encoding="utf-8"))
                output_text = "\n".join(output_parts)
                refs = compute_reference_density(read_set, output_text)
                referenced = sum(refs.values())
                telemetry.event(
                    "read_set.reference_density",
                    milestone=milestone,
                    cycle_num=cycle_count + 1,
                    referenced_count=referenced,
                    total_count=len(refs),
                    density=round(referenced / len(refs), 3) if refs else 0.0,
                    unreferenced=sorted(f for f, r in refs.items() if not r),
                )
            except Exception:
                pass  # telemetry must never break the orchestrator

            # Compositional span telemetry (DB-20 Step 2).
            if cycle_type == "ASSESS":
                try:
                    from clou.telemetry import compute_compositional_span
                    span_data = compute_compositional_span(read_set)
                    telemetry.event(
                        "cognitive.compositional_span",
                        milestone=milestone,
                        cycle_num=cycle_count + 1,
                        cycle_type="ASSESS",
                        span=span_data["span"],
                        chain=span_data["chain"],
                        pre_composed=span_data["pre_composed"],
                    )
                except Exception:
                    pass  # telemetry must never break the orchestrator

            # Decision accuracy feedback (G6): after an ASSESS cycle that
            # follows a rework EXECUTE, check whether the rework was productive.
            # Must run after the cycle completes so the post-ASSESS checkpoint
            # has valid_findings set by the coordinator agent.
            if (
                _last_rework_decision_cycle is not None
                and cycle_type == "ASSESS"
                and checkpoint_path.exists()
            ):
                try:
                    _cp_g6 = parse_checkpoint(checkpoint_path.read_text())
                    if _cp_g6.valid_findings >= 0:
                        telemetry.event(
                            "quality_gate.decision_outcome",
                            milestone=milestone,
                            cycle_num=cycle_count + 1,
                            original_decision="rework",
                            rework_cycle=_last_rework_decision_cycle,
                            subsequent_valid_findings=_cp_g6.valid_findings,
                            was_productive=_cp_g6.valid_findings > 0,
                        )
                        _last_rework_decision_cycle = None
                except Exception:
                    pass

            # Classify cycle outcome for staleness tracking.
            if status == "failed":
                _stale.last_cycle_outcome = "FAILED"
            elif status == "agent_team_crash":
                _stale.last_cycle_outcome = "FAILED"
            elif status in ("exhausted", "interrupted"):
                _stale.last_cycle_outcome = "INTERRUPTED"
            else:
                _stale.last_cycle_outcome = "ADVANCED"

            if status == "failed":
                crash_retries += 1
                telemetry.event(
                    "crash", milestone=milestone,
                    cycle_num=cycle_count + 1, attempt=crash_retries,
                )
                log.warning(
                    "Cycle crashed for %r (attempt %d/%d), retrying",
                    milestone,
                    crash_retries,
                    _MAX_CRASH_RETRIES,
                )
                if crash_retries >= _MAX_CRASH_RETRIES:
                    log.error("Milestone %r hit crash retry limit", milestone)
                    await write_agent_crash_escalation(
                        project_dir, milestone,
                        error_detail=_crash_context.get("detail"),
                    )
                    telemetry.event(
                        "escalation.created", milestone=milestone,
                        cycle_num=cycle_count + 1,
                        classification="agent_crash",
                        severity="blocking",
                    )
                    _post_new_escalations()
                    _ms_outcome = "escalated_crash_loop"
                    return "escalated_crash_loop"
                _persist_retry_counters()
                continue

            if status == "agent_team_crash":
                telemetry.event(
                    "agent_crash", milestone=milestone,
                    cycle_num=cycle_count + 1,
                )
                log.error("Agent team crash for %r, escalating", milestone)
                await write_agent_crash_escalation(
                    project_dir, milestone,
                    error_detail=_crash_context.get("detail"),
                )
                telemetry.event(
                    "escalation.created", milestone=milestone,
                    cycle_num=cycle_count + 1,
                    classification="agent_crash",
                    severity="blocking",
                )
                _post_new_escalations()
                _ms_outcome = "escalated_agent_crash"
                return "escalated_agent_crash"

            if status == "interrupted":
                telemetry.event(
                    "timeout_interrupted", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                )
                log.warning(
                    "Timeout classified as interruption for %r, "
                    "continuing from checkpoint",
                    milestone,
                )
                # Interruption is not a crash -- the agent was recently
                # active (e.g. waiting on network/sleep).  Don't
                # increment crash_retries; persist checkpoint and retry.
                # Apply cooldown before retrying to avoid rapid restart
                # loops (R4: configurable cooldown for transient errors).
                await asyncio.sleep(DEFAULT_RETRY_COOLDOWN)
                _persist_retry_counters()
                continue

            if status == "exhausted":
                telemetry.event(
                    "context_exhausted", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                )
                log.warning(
                    "Context exhausted for %r, continuing from checkpoint",
                    milestone,
                )
                # Exhaustion is not a crash — the agent wrote a mid-cycle
                # checkpoint.  Skip validation (golden context is partial)
                # and let determine_next_cycle route from the checkpoint.
                crash_retries = 0
                _persist_retry_counters()
                continue

            # Post-cycle delivery: verify the coordinator wrote its state.
            delivery = validate_delivery(
                clou_dir / "milestones" / milestone,
                checkpoint_path,
                milestone,
            )
            delivery_errors = errors_only(delivery)
            if delivery_errors:
                log.warning(
                    "Delivery check failed for %r: %s",
                    milestone,
                    [e.message for e in delivery_errors],
                )
                telemetry.event(
                    "delivery_failed", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                    error_count=len(delivery_errors),
                )

            # Validate golden context structure (content checks).
            findings = validate_golden_context(project_dir, milestone, template=tmpl)
            # Merge delivery errors into the content findings.
            findings.extend(delivery)
            validation_errors = errors_only(findings)
            validation_warnings = warnings_only(findings)

            if validation_warnings:
                log.info(
                    "Validation warnings for %r (non-blocking): %s",
                    milestone,
                    [w.message for w in validation_warnings],
                )
                telemetry.event(
                    "validation_warnings", milestone=milestone,
                    cycle_num=cycle_count + 1,
                    warning_count=len(validation_warnings),
                )

            if validation_errors:
                # Try self-heal before counting as a failure.
                healed = attempt_self_heal(
                    project_dir, milestone, validation_errors,
                )
                if healed:
                    log.info(
                        "Self-healed %d issue(s) for %r: %s",
                        len(healed), milestone, healed,
                    )
                    telemetry.event(
                        "self_heal", milestone=milestone,
                        cycle_num=cycle_count + 1,
                        fix_count=len(healed),
                    )
                    # Re-validate after heal.  Re-attach delivery findings
                    # — self-heal can't fix missing files, so delivery errors
                    # persist and must not be silently dropped.
                    findings = validate_golden_context(project_dir, milestone, template=tmpl)
                    findings.extend(delivery)
                    validation_errors = errors_only(findings)
                    validation_warnings = warnings_only(findings)
                    log_self_heal_attempt(
                        project_dir, milestone, healed, validation_errors,
                    )

                if validation_warnings and healed:
                    # Log warnings again after re-validation.
                    log.info(
                        "Validation warnings for %r after self-heal "
                        "(non-blocking): %s",
                        milestone,
                        [w.message for w in validation_warnings],
                    )

            if validation_errors:
                validation_retries += 1
                telemetry.event(
                    "validation_failure", milestone=milestone,
                    cycle_num=cycle_count + 1, attempt=validation_retries,
                    error_count=len(validation_errors),
                    warning_count=len(validation_warnings),
                )
                log.warning(
                    "Validation failed for %r (attempt %d/%d): %s",
                    milestone,
                    validation_retries,
                    _MAX_VALIDATION_RETRIES,
                    validation_errors,
                )
                if validation_retries >= _MAX_VALIDATION_RETRIES:
                    await write_validation_escalation(
                        project_dir, milestone, findings
                    )
                    telemetry.event(
                        "escalation.created", milestone=milestone,
                        cycle_num=cycle_count + 1,
                        classification="validation_failure",
                        severity="blocking",
                    )
                    _post_new_escalations()
                    _ms_outcome = "escalated_validation"
                    return "escalated_validation"
                # Capture working tree state BEFORE reverting golden context.
                # This makes partial code changes from the failed cycle
                # visible to the retry coordinator (describe-and-adapt).
                _pending_working_tree = await _capture_working_tree_state(
                    project_dir
                )
                try:
                    await git_revert_golden_context(
                        project_dir, milestone, current_phase=_current_phase,
                    )
                except RuntimeError:
                    log.exception("Git revert failed for %r", milestone)
                pending_validation_errors = findings
                _persist_retry_counters()
                continue
            else:
                # Distribution distance telemetry for PLAN cycles (DB-18).
                if cycle_type == "PLAN":
                    try:
                        from clou.telemetry import emit_distribution_distance
                        emit_distribution_distance(milestone, validation_retries)
                    except Exception:
                        pass  # telemetry must never break the orchestrator
                validation_retries = 0
                readiness_retries = 0
                crash_retries = 0

            # Scan decisions.md for memory pattern references (M35 I2).
            # Must run BEFORE compact_decisions() so the scanner sees the
            # original (pre-compaction) decisions content (F7).
            if cycle_type in ("PLAN", "ASSESS"):
                _filtered_path = checkpoint_path.parent / "_filtered_memory.md"
                if _filtered_path.exists() and decisions_path.exists():
                    try:
                        from clou.recovery import scan_pattern_references

                        scan_pattern_references(
                            _filtered_path,
                            decisions_path,
                            milestone=milestone,
                            cycle_num=cycle_count + 1,
                            cycle_type=cycle_type,
                        )
                    except Exception:
                        log.warning(
                            "Pattern reference scan failed for %r",
                            milestone, exc_info=True,
                        )

            # Compact decisions.md if it's grown too large (DB-15 D3).
            if decisions_path.exists():
                from clou.recovery import compact_decisions

                if compact_decisions(decisions_path):
                    log.info("Compacted decisions.md for %r", milestone)

            # Coordinator-only git commit at phase completion
            if cycle_type == "EXECUTE" and checkpoint_path.exists():
                cp = parse_checkpoint(checkpoint_path.read_text())
                if cp.current_phase:
                    try:
                        await git_commit_phase(project_dir, milestone, cp.current_phase)
                    except RuntimeError:
                        log.warning(
                            "Git commit failed for phase %r",
                            cp.current_phase,
                        )

            if _app is not None:
                _app.post_message(
                    ClouCycleComplete(
                        cycle_num=cycle_count + 1,
                        cycle_type=cycle_type,
                        next_step=_NEXT_STEP.get(cycle_type, ""),
                        phase_status={},
                    )
                )

            if _app is not None:
                compose_path = clou_dir / "milestones" / milestone / "compose.py"
                if compose_path.exists():
                    try:
                        from clou.graph import extract_dag_data

                        source = compose_path.read_text(encoding="utf-8")
                        tasks, deps = extract_dag_data(source)
                        _app.post_message(ClouDagUpdate(tasks=tasks, deps=deps))
                    except Exception:
                        log.debug("Could not parse DAG from compose.py", exc_info=True)

            _persist_retry_counters()
            _post_new_escalations()

        return "completed"  # unreachable, but satisfies mypy
    finally:
        try:
            telemetry.write_milestone_summary(project_dir, milestone, _ms_outcome)
            telemetry.event("milestone.end", milestone=milestone, outcome=_ms_outcome)
        except Exception:
            log.warning("telemetry summary write failed for %r", milestone, exc_info=True)

        try:
            from clou.telemetry import emit_channel_capacity
            ms_dir = clou_dir / "milestones" / milestone
            emit_channel_capacity(ms_dir, milestone)
        except Exception:
            pass  # telemetry must never break the orchestrator

        # Consolidate operational memory and archive episodic files (DB-18).
        # Runs after metrics.md is written so consolidation can read it.
        # Archive only if consolidation succeeded — don't delete episodic
        # evidence before patterns have been extracted.
        if _ms_outcome == "completed":
            try:
                consolidated = consolidate_milestone(project_dir, milestone)
                if consolidated:
                    await archive_milestone_episodic(project_dir, milestone)
            except Exception:
                log.warning(
                    "memory consolidation failed for %r", milestone, exc_info=True,
                )
                try:
                    _fapp = get_active_app()
                    if _fapp is not None:
                        _fapp.post_message(
                            ClouBreathEvent(
                                text=f"memory consolidation failed for {milestone}",
                                cycle_type="FINALIZE",
                                phase=None,
                            )
                        )
                except Exception:
                    log.debug("failed to post consolidation warning to UI", exc_info=True)
        set_active_app(None)


# ---------------------------------------------------------------------------
# Single cycle execution
# ---------------------------------------------------------------------------


async def _run_single_cycle(
    project_dir: Path,
    milestone: str,
    cycle_type: str,
    prompt: str,
    *,
    cycle_num: int = 0,
    template: HarnessTemplate | None = None,
    app: ClouApp | None = None,
    crash_context: dict[str, str] | None = None,
) -> str:
    """Run one coordinator cycle as a fresh session.

    *crash_context*, when provided, is a mutable dict that crash sites
    populate with ``"detail"`` — the error message or evidence string
    from the failing agent.  The caller reads it after the function
    returns to include in escalation files.
    """
    if template is None:
        template = load_template("software-construction")

    # Clear transcript store at cycle start so each cycle gets a fresh slate.
    from clou.transcript import get_store as _get_transcript_store

    _get_transcript_store().clear()

    hooks = to_sdk_hooks(
        build_hooks(
            "coordinator", project_dir, milestone=milestone, template=template,
        )
    )

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("coordinator", project_dir, milestone=milestone),
        permission_mode="bypassPermissions",
        cwd=str(project_dir),
        model=MODEL,
        agents=_build_agents(project_dir, milestone, template),
        hooks=hooks,
        max_budget_usd=_MAX_BUDGET_USD,
        max_turns=_MAX_TURNS,
        effort="max" if cycle_type in ("ASSESS", "VERIFY") else "high",
        mcp_servers={
            **template_mcp_servers(template),
            "clou_coordinator": build_coordinator_mcp_server(project_dir, milestone),
        },
        sandbox=SandboxSettings(
            enabled=True,
            autoAllowBashIfSandboxed=True,
            allowUnsandboxedCommands=False,
        ),
    )

    # Quality gate tool tracking (DB-18).
    # During ASSESS cycles, observe which MCP tools the quality gate
    # agent calls via TaskProgressMessage.last_tool_name.  At cycle end,
    # compare against the template's expected tools to detect unavailability.
    _qg_expected: set[str] = set()
    _qg_seen: set[str] = set()
    if cycle_type == "ASSESS" and template is not None:
        for gate in template.quality_gates:
            agent_name = gate.assess_agent
            agent_spec = template.agents.get(agent_name)
            if agent_spec is not None:
                _qg_expected |= {
                    t for t in agent_spec.tools
                    if t.startswith("mcp__")
                }

    # --- Runtime safeguard state ---
    # Parse DAG deps from prompt for selective abort (if present).
    _dag_deps: dict[str, list[str]] = {}
    _dag_task_names: list[str] = []
    _dag_resource_bounds: dict[str, dict[str, int]] = {}
    clou_dir = project_dir / ".clou"
    compose_path = clou_dir / "milestones" / milestone / "compose.py"
    if compose_path.exists():
        try:
            from clou.graph import extract_dag_data

            source = compose_path.read_text(encoding="utf-8")
            dag_tasks, dag_deps_raw = extract_dag_data(source)
            _dag_deps = dag_deps_raw
            _dag_task_names = [t["name"] for t in dag_tasks]
            for t in dag_tasks:
                bounds = t.get("resource_bounds")
                if isinstance(bounds, dict):
                    _dag_resource_bounds[t["name"]] = bounds
        except Exception:
            log.debug("Could not parse DAG for runtime safeguards", exc_info=True)

    # Track agent task_id -> task_name mapping and per-task token/time usage.
    _task_id_to_name: dict[str, str] = {}
    _active_task_ids: set[str] = set()
    _task_tokens: dict[str, int] = {}  # task_name -> cumulative tokens
    _task_start_time: dict[str, float] = {}  # task_id -> monotonic start time
    _aborted_task_ids: set[str] = set()  # task_ids already being aborted

    # Agent tier tracking: description -> tier, task_id -> tier.
    _desc_to_tier: dict[str, str] = {}
    _task_id_to_tier: dict[str, str] = {}
    if template is not None:
        for _agent_spec in template.agents.values():
            _desc_to_tier[_agent_spec.description] = _agent_spec.tier

    # Per-task retry counts for transient error classification (R4).
    # Tracks how many times each task has been retried within this cycle.
    # Reset per-cycle (does not survive across cycles).
    _task_retry_counts: dict[str, int] = {}
    # Whether this cycle had any terminal task failures (drives crash_retries).
    _had_terminal_failure: bool = False

    # Ring buffer of recent coordinator messages for timeout classification.
    # Keeps the last 3 messages so classify_timeout() can inspect what the
    # agent was doing when the idle watchdog fired.
    _recent_messages: deque[object] = deque(maxlen=3)

    # Compute effective wall-clock timeout for the entire dispatch.
    # Uses the maximum timeout_seconds across all tasks with resource
    # bounds, falling back to _DEFAULT_TIMEOUT_SECONDS.  This provides
    # an unconditional ceiling even when tasks suppress progress messages.
    _effective_timeout: float = _DEFAULT_TIMEOUT_SECONDS
    if _dag_resource_bounds:
        per_task_timeouts = [
            b.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
            for b in _dag_resource_bounds.values()
        ]
        _effective_timeout = float(max(per_task_timeouts))
    if _effective_timeout <= 0:
        _effective_timeout = float(_DEFAULT_TIMEOUT_SECONDS)

    # Clean stale execution shards before dispatching a gather() group.
    # Shards from a prior crashed cycle would confuse merge_shards if
    # left in the phase directory.  Only activates when the DAG has >1
    # task (potential gather group).
    if len(_dag_task_names) > 1:
        try:
            cp_path_pre = (
                clou_dir / "milestones" / milestone
                / "active" / "coordinator.md"
            )
            if cp_path_pre.exists():
                _phase_for_clean = parse_checkpoint(
                    cp_path_pre.read_text(encoding="utf-8")
                ).current_phase
                if _phase_for_clean:
                    # F5: Lowercase-normalize and validate against
                    # _PHASE_RE before passing to clean_stale_shards
                    # (defense in depth, same pattern as line 924-927).
                    _phase_for_clean = _phase_for_clean.lower()
                    if not _PHASE_RE.match(_phase_for_clean):
                        log.warning(
                            "Stale shard cleanup skipped: invalid phase %r",
                            _phase_for_clean,
                        )
                    else:
                        ms_dir_pre = clou_dir / "milestones" / milestone
                        removed = clean_stale_shards(ms_dir_pre, _phase_for_clean)
                        if removed:
                            log.info(
                                "Cleaned %d stale shard(s) before dispatch: %s",
                                len(removed),
                                [str(p) for p in removed],
                            )
        except Exception:
            log.debug("Could not clean stale shards", exc_info=True)

    try:
        async with ClaudeSDKClient(options=options) as coordinator:
            await coordinator.query(prompt)

            try:
                # Idle watchdog: fires only when the system is genuinely
                # idle — no active agents AND no coordinator messages for
                # _effective_timeout seconds.  While agents are active
                # (spawned but not yet completed), the watchdog is
                # suppressed — the system is working, just waiting on
                # tool responses (e.g. MCP brutalist panel, 2-12 min).
                # Per-task resource bounds handle individual agent
                # timeouts; the idle watchdog catches coordinator stalls.
                loop = asyncio.get_event_loop()
                async with asyncio.timeout(_effective_timeout) as _idle_cm:
                    async for msg in coordinator.receive_response():
                        _recent_messages.append(msg)
                        _track(msg, tier="coordinator", milestone=milestone)

                        if app is not None:
                            from contextlib import suppress

                            from clou.ui.bridge import route_coordinator_message
                            from clou.ui.widgets.breath import BreathWidget

                            # Post to the BreathWidget so handlers fire there.
                            # app.post_message doesn't propagate downward in
                            # Textual -- messages bubble up, not down.
                            _coord_post = app.post_message
                            with suppress(LookupError):
                                _coord_post = app.query_one(
                                    BreathWidget
                                ).post_message

                            route_coordinator_message(
                                msg,
                                milestone,
                                cycle_type,
                                _coord_post,
                            )

                        # Agent lifecycle telemetry -- uses shared classification
                        # helpers from bridge.py instead of duplicating duck-typing.
                        # NOTE: This block runs BEFORE the timeout reschedule
                        # so that _active_task_ids is populated when the
                        # multiplier check fires.  Previously, the reschedule
                        # ran first, creating a race where TaskStartedMessage
                        # processing added the task AFTER the multiplier check
                        # had already used the un-extended timeout.
                        if is_task_started(msg):
                            _tier = _desc_to_tier.get(
                                msg.description, "unknown",
                            )
                            _task_id_to_tier[msg.task_id] = _tier
                            telemetry.event(
                                "agent.start",
                                milestone=milestone,
                                cycle_num=cycle_num,
                                task_id=msg.task_id,
                                description=msg.description,
                                tier=_tier,
                            )
                            # Always track as active — the idle watchdog
                            # needs to know ANY agent is alive, not just
                            # DAG-matched ones.  ASSESS agents (brutalist
                            # quality gate) don't match DAG task names but
                            # are still legitimate work-in-progress.
                            _active_task_ids.add(msg.task_id)
                            # Map task_id to task_name via fuzzy matching
                            # (for per-task resource enforcement).
                            matched = match_agent_to_task(
                                msg.description, _dag_task_names,
                            )
                            if matched:
                                _task_id_to_name[msg.task_id] = matched
                                _task_tokens.setdefault(matched, 0)
                                import time as _time_mod
                                _task_start_time[msg.task_id] = _time_mod.monotonic()
                                # Register agent_id -> task_name for
                                # transcript UI lookup.
                                _get_transcript_store().register_task_mapping(
                                    msg.task_id, matched,
                                )

                        elif is_task_progress(msg):
                            # TaskProgressMessage -- track quality gate tools.
                            tool_name = msg.last_tool_name
                            if isinstance(tool_name, str) and tool_name in _qg_expected:
                                _qg_seen.add(tool_name)

                            # Per-task resource enforcement (budget and timeout).
                            task_name = _task_id_to_name.get(msg.task_id)
                            if task_name and msg.task_id not in _aborted_task_ids:
                                _violation_type: str | None = None
                                _violation_detail: str = ""

                                # --- Token budget check ---
                                usage = getattr(msg, "usage", None)
                                if usage and isinstance(usage, dict):
                                    total = usage.get("total_tokens", 0)
                                    _task_tokens[task_name] = total

                                    bounds = _dag_resource_bounds.get(task_name, {})
                                    budget = bounds.get("tokens")
                                    if budget is not None and total > budget:
                                        _violation_type = "budget_exceeded"
                                        _violation_detail = (
                                            f"Task terminated after "
                                            f"{total} tokens "
                                            f"(budget: {budget})"
                                        )

                                # --- Wall-clock timeout check ---
                                if _violation_type is None:
                                    import time as _time_mod
                                    start = _task_start_time.get(msg.task_id)
                                    if start is not None:
                                        elapsed = _time_mod.monotonic() - start
                                        bounds = _dag_resource_bounds.get(
                                            task_name, {},
                                        )
                                        limit = bounds.get(
                                            "timeout_seconds",
                                            _DEFAULT_TIMEOUT_SECONDS,
                                        )
                                        if elapsed > limit:
                                            _violation_type = "timeout"
                                            _violation_detail = (
                                                f"Task terminated after "
                                                f"{int(elapsed)}s "
                                                f"(limit: {limit}s)"
                                            )

                                # --- Enforce violation ---
                                if _violation_type is not None:
                                    log.warning(
                                        "%s for task %r in %r: %s",
                                        _violation_type, task_name,
                                        milestone, _violation_detail,
                                    )
                                    _aborted_task_ids.add(msg.task_id)
                                    try:
                                        coordinator.stop_task(msg.task_id)
                                    except Exception:
                                        log.debug(
                                            "stop_task failed for %r",
                                            msg.task_id, exc_info=True,
                                        )
                                    # Write failure shard.
                                    # Use the task's own function name as
                                    # the phase directory — workers write
                                    # shards under phases/{function_name}/,
                                    # not under the coordinator's
                                    # current_phase.  Fall back to
                                    # current_phase for backward compat.
                                    _shard_phase = task_name
                                    if not _shard_phase:
                                        try:
                                            from clou.recovery import parse_checkpoint as _pc
                                            cp_path = (
                                                clou_dir / "milestones" / milestone
                                                / "active" / "coordinator.md"
                                            )
                                            if cp_path.exists():
                                                _shard_phase = _pc(
                                                    cp_path.read_text(encoding="utf-8")
                                                ).current_phase
                                        except Exception:
                                            pass
                                    if _shard_phase:
                                        # F1: Lowercase-normalize and validate
                                        # against _PHASE_RE before passing to
                                        # _write_failure_shard (defense in
                                        # depth, same pattern as line 924-931).
                                        _shard_phase = _shard_phase.lower()
                                        if not _PHASE_RE.match(_shard_phase):
                                            log.warning(
                                                "Failure shard skipped: "
                                                "invalid phase %r",
                                                _shard_phase,
                                            )
                                        else:
                                            active_names = {
                                                _task_id_to_name[tid]
                                                for tid in _active_task_ids
                                                if tid in _task_id_to_name
                                                and tid != msg.task_id
                                            }
                                            impact = sorted(
                                                _compute_abort_set(
                                                    task_name, _dag_deps,
                                                    active_names,
                                                )
                                            )
                                            ms_dir = (
                                                clou_dir / "milestones"
                                                / milestone
                                            )
                                            _write_failure_shard(
                                                ms_dir, _shard_phase,
                                                task_name, _violation_type,
                                                _violation_detail,
                                                impact,
                                            )

                        elif is_task_complete(msg):
                            _au = getattr(msg, "usage", {}) or {}
                            telemetry.event(
                                "agent.end",
                                milestone=milestone,
                                cycle_num=cycle_num,
                                task_id=msg.task_id,
                                status=msg.status,
                                total_tokens=_au.get("total_tokens", 0),
                                input_tokens=_au.get("input_tokens", 0),
                                output_tokens=_au.get("output_tokens", 0),
                                tool_uses=_au.get("tool_uses", 0),
                                tier=_task_id_to_tier.get(
                                    msg.task_id, "unknown",
                                ),
                            )
                            # Remove from active set.
                            _active_task_ids.discard(msg.task_id)

                        # Reschedule the idle watchdog AFTER agent lifecycle
                        # Reschedule the idle watchdog.  While agents are
                        # active, suppress entirely — they may be blocked
                        # on in-flight tool calls that emit no progress.
                        # The per-task resource enforcement (budget and
                        # wall-clock checks on TaskProgressMessage) handles
                        # runaway agents.  The idle watchdog's job is to
                        # catch coordinator stalls when NO work is happening.
                        if _active_task_ids:
                            _idle_cm.reschedule(loop.time() + 86400)
                        else:
                            _idle_cm.reschedule(loop.time() + _effective_timeout)

                        if _context_exhausted(msg):
                            log.warning(
                                "Context exhaustion in %r cycle %s",
                                milestone,
                                cycle_type,
                            )
                            await coordinator.query(
                                "Context approaching limit. Write a mid-cycle "
                                "checkpoint to active/coordinator.md with partial "
                                "progress, then exit."
                            )
                            return "exhausted"

                        # --- Selective abort on task failure ---
                        if isinstance(msg, TaskNotificationMessage) and msg.status == "failed":
                            failed_task_name = _task_id_to_name.get(msg.task_id)
                            _active_task_ids.discard(msg.task_id)

                            # --- Error classification (R4) ---
                            # Classify the error before deciding abort vs retry.
                            _error_msg = getattr(msg, "summary", "") or ""
                            _retry_count = _task_retry_counts.get(
                                failed_task_name or "", 0,
                            )
                            _err_kind, _err_reason = classify_error(
                                _error_msg,
                                failed_task_name or "(unknown)",
                                retry_count=_retry_count,
                                max_retries=DEFAULT_MAX_RETRIES,
                            )
                            telemetry.event(
                                "error_classification",
                                milestone=milestone,
                                cycle_num=cycle_num,
                                task_name=failed_task_name or "(unknown)",
                                error_kind=_err_kind.value,
                                reason=_err_reason,
                                retry_count=_retry_count,
                            )

                            if (
                                _err_kind == ErrorKind.TRANSIENT
                                and failed_task_name
                            ):
                                # Transient error: log and let siblings
                                # continue.  Don't abort dependents -- the
                                # task will be retried on the next cycle.
                                _task_retry_counts[failed_task_name] = (
                                    _retry_count + 1
                                )
                                log.warning(
                                    "Transient error for task %r in %r, "
                                    "retry %d/%d after cooldown: %s",
                                    failed_task_name,
                                    milestone,
                                    _retry_count + 1,
                                    DEFAULT_MAX_RETRIES,
                                    _err_reason,
                                )
                                # Continue the message loop -- independent
                                # siblings keep running, and this cycle will
                                # return "interrupted" so the outer loop
                                # retries without incrementing crash_retries.
                                continue

                            # Terminal error: mark the cycle and proceed
                            # with existing abort/escalation logic.
                            _had_terminal_failure = True
                            log.warning(
                                "Terminal error for task %r in %r, "
                                "escalating immediately: %s",
                                failed_task_name or "(unknown)",
                                milestone,
                                _err_reason,
                            )

                            if not _dag_deps or not failed_task_name:
                                # No DAG context or unknown task -- fall back to
                                # original blanket-abort behavior.
                                log.error(
                                    "Agent team crash in %r: %s",
                                    milestone,
                                    msg.summary,
                                )
                                if crash_context is not None:
                                    crash_context["detail"] = (
                                        f"Task {failed_task_name or '(unknown)'} "
                                        f"failed: {msg.summary}"
                                    )
                                await coordinator.query(
                                    "Agent team member crashed. Preserve all "
                                    "execution.md entries. Do NOT retry. Write "
                                    "checkpoint and exit."
                                )
                                return "agent_team_crash"

                            # Compute which active tasks depend on the failed one.
                            active_names = {
                                _task_id_to_name[tid]
                                for tid in _active_task_ids
                                if tid in _task_id_to_name
                            }
                            abort_set = _compute_abort_set(
                                failed_task_name, _dag_deps, active_names,
                            )

                            log.warning(
                                "Task %r failed in %r. Aborting dependents: %s. "
                                "Independent siblings continue: %s",
                                failed_task_name,
                                milestone,
                                sorted(abort_set) if abort_set else "none",
                                sorted(active_names - abort_set) if (active_names - abort_set) else "none",
                            )

                            # Abort dependent tasks via SDK stop_task.
                            name_to_id = {
                                name: tid
                                for tid, name in _task_id_to_name.items()
                                if tid in _active_task_ids
                            }
                            for abort_name in abort_set:
                                abort_tid = name_to_id.get(abort_name)
                                if abort_tid and abort_tid not in _aborted_task_ids:
                                    _aborted_task_ids.add(abort_tid)
                                    try:
                                        coordinator.stop_task(abort_tid)
                                    except Exception:
                                        log.debug(
                                            "stop_task failed for %r (%s)",
                                            abort_name, abort_tid,
                                            exc_info=True,
                                        )

                            # If ALL remaining active tasks are being aborted,
                            # tell the coordinator to checkpoint and exit.
                            remaining = active_names - abort_set
                            if not remaining:
                                if crash_context is not None:
                                    crash_context["detail"] = (
                                        f"Task {failed_task_name!r} failed "
                                        f"(all dependents aborted): "
                                        f"{msg.summary}"
                                    )
                                await coordinator.query(
                                    f"Task {failed_task_name!r} failed. All "
                                    f"remaining tasks depend on it and are being "
                                    f"aborted. Preserve execution.md entries. "
                                    f"Write checkpoint and exit."
                                )
                                return "agent_team_crash"

                            # Otherwise, let independent siblings finish.
                            # The coordinator message loop continues naturally.

            except TimeoutError:
                # Idle watchdog fired -- no coordinator message for
                # _effective_timeout seconds.  Classify whether this is
                # an interruption (agent was recently active) or a
                # genuine crash (agent stopped responding).
                classification, evidence = classify_timeout(
                    list(_recent_messages),
                    _active_task_ids,
                    _task_start_time,
                    _effective_timeout,
                )
                log.warning(
                    "Idle watchdog fired for %r after %.0fs: "
                    "classification=%s, evidence=%s",
                    milestone, _effective_timeout,
                    classification, evidence,
                )

                # Read current_phase as fallback only — each task
                # should use its own function name as the phase
                # directory for shard placement.
                _fallback_phase = None
                try:
                    cp_path = (
                        clou_dir / "milestones" / milestone
                        / "active" / "coordinator.md"
                    )
                    if cp_path.exists():
                        _fallback_phase = parse_checkpoint(
                            cp_path.read_text(encoding="utf-8")
                        ).current_phase
                except Exception:
                    pass

                # F1: Lowercase-normalize and validate _fallback_phase
                # against _PHASE_RE before it can reach _write_failure_shard
                # (defense in depth, same pattern as line 924-931).
                if _fallback_phase:
                    _fallback_phase = _fallback_phase.lower()
                    if not _PHASE_RE.match(_fallback_phase):
                        log.warning(
                            "Failure shard fallback skipped: "
                            "invalid phase %r",
                            _fallback_phase,
                        )
                        _fallback_phase = None

                ms_dir = clou_dir / "milestones" / milestone
                for tid in list(_active_task_ids):
                    task_name = _task_id_to_name.get(tid)
                    if not task_name:
                        continue
                    # Compute dependency impact for this timed-out task.
                    active_names = {
                        _task_id_to_name[t]
                        for t in _active_task_ids
                        if t in _task_id_to_name and t != tid
                    }
                    impact = sorted(
                        _compute_abort_set(task_name, _dag_deps, active_names)
                    )
                    # Use the task's function name as the phase
                    # directory; fall back to coordinator's
                    # current_phase for backward compatibility.
                    _shard_phase = task_name or _fallback_phase
                    if _shard_phase:
                        _write_failure_shard(
                            ms_dir, _shard_phase,
                            task_name, "timeout",
                            f"Task terminated after {int(_effective_timeout)}s "
                            f"idle (no coordinator progress)",
                            impact,
                        )

                if classification == "interrupted":
                    return "interrupted"
                if crash_context is not None:
                    crash_context["detail"] = (
                        f"Idle watchdog timeout after {int(_effective_timeout)}s: "
                        f"classification={classification}, evidence={evidence}"
                    )
                return "agent_team_crash"

        # Convergence-aware gate status (DB-18).
        # If _qg_seen is empty during an ASSESS cycle, distinguish
        # "coordinator suppressed the gate (convergence)" from
        # "MCP tools genuinely unavailable."
        _converged = False
        if cycle_type == "ASSESS" and _qg_expected and not _qg_seen:
            _ckpt_path = clou_dir / "milestones" / milestone / "checkpoint.md"
            if _ckpt_path.exists():
                try:
                    _ckpt = parse_checkpoint(
                        _ckpt_path.read_text(encoding="utf-8")
                    )
                    _conv = assess_convergence(_ckpt)
                    _converged = _conv.converged
                except Exception:
                    pass  # Fall through to degraded.

        # Emit quality gate telemetry after ASSESS cycle completes (DB-18).
        if _qg_expected:
            if _qg_seen == _qg_expected:
                _qg_status = "full"
            elif _qg_seen:
                _qg_status = "partial"
            elif _converged:
                _qg_status = "converged"
            else:
                _qg_status = "degraded"
            telemetry.event(
                "quality_gate.result",
                milestone=milestone,
                cycle_num=cycle_num,
                status=_qg_status,
                tools_invoked=sorted(_qg_seen),
                tools_unavailable=(
                    sorted(_qg_expected - _qg_seen)
                    if _qg_status != "converged" else []
                ),
                tools_suppressed=(
                    sorted(_qg_expected - _qg_seen)
                    if _qg_status == "converged" else []
                ),
                tools_invoked_count=len(_qg_seen),
            )
            if _qg_status == "degraded":
                log.warning(
                    "Quality gate degraded for %r cycle %d: "
                    "no expected tools invoked (expected: %s)",
                    milestone, cycle_num, sorted(_qg_expected),
                )
                telemetry.event(
                    "quality_gate.degraded",
                    milestone=milestone,
                    cycle_num=cycle_num,
                    expected_tools=sorted(_qg_expected),
                )

        # If the cycle had transient (but no terminal) failures,
        # return "interrupted" so the outer loop retries without
        # incrementing crash_retries (R4).
        if _task_retry_counts and not _had_terminal_failure:
            log.info(
                "Cycle completed with transient failures for %r: %s",
                milestone,
                {k: v for k, v in _task_retry_counts.items()},
            )
            return "interrupted"

        return read_cycle_outcome(project_dir, milestone)

    except Exception:
        log.exception("Coordinator cycle crashed for %r", milestone)
        return "failed"
