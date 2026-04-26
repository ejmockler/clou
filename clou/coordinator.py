"""Coordinator cycle engine — session-per-cycle milestone execution.

Extracted from orchestrator.py.  Contains the coordinator's multi-cycle
loop (``run_coordinator``), single-cycle execution (``_run_single_cycle``),
and their shared helpers: token tracking, agent team construction,
milestone validation, environment probing, and context exhaustion detection.

The supervisor session and CLI entry points remain in ``orchestrator.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import tempfile
import time
import unicodedata
import datetime
import logging
import re
import subprocess
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clou.ui.app import ClouApp

from clou.escalation import (
    RESOLVED_DISPOSITION_STATUSES,
    find_open_engine_gated_escalation,
    parse_escalation,
    parse_latest_disposition,
)
from clou.ui.messages import (
    ClouBreathEvent,
    ClouBudgetWarning,
    ClouCoordinatorPaused,
    ClouCycleComplete,
    ClouDagUpdate,
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
    DEFAULT_HOLD_COOLDOWN,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_COOLDOWN,
    ErrorKind,
    archive_milestone_episodic,
    assess_convergence,
    attempt_self_heal,
    classify_error,
    compute_hold_wait,
    consolidate_milestone,
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    is_execute_family,
    is_rework_requested,
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
from clou.shard import clean_stale_shards_for_layer
from clou import telemetry

log = logging.getLogger("clou")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CYCLES = 20
# M50 I1 cycle-3 rework (F3): EXECUTE_REWORK and EXECUTE_VERIFY are
# EXECUTE-family dispatches — after each, the orchestrator reports
# ASSESS as the next cycle (same as plain EXECUTE).  Without these
# entries, ``_NEXT_STEP.get(cycle_type, "")`` at line ~2815 returns
# an empty string on rework/verify completion, leaving
# ``ClouCycleComplete.next_step`` blank in the UI.
#
# M50 I1 cycle-4 rework (F3): rows with multiple legitimate
# successors are INTENTIONALLY OMITTED.  VERIFY can route to
# EXIT (criteria satisfied), EXECUTE_REWORK (perception gap
# requiring code changes), or EXECUTE_VERIFY (perception gap
# requiring another verification pass) — per
# ``coordinator-verify.md`` lines 50-52.  REPLAN can route to
# EXECUTE or back to PLAN depending on the replan outcome.  A
# static dict cannot express "it depends on the checkpoint the
# coordinator just wrote," so the honest behavior is to omit
# these source rows and fall through to the
# ``_NEXT_STEP.get(cycle_type, "")`` empty-string default.  The
# UI (``ClouCycleComplete``) tolerates an empty ``next_step``
# and renders a best-effort label.
#
# The alternative — reading the actual posted-checkpoint
# ``next_step`` after the cycle write — is the "correct"
# solution but requires threading the just-written checkpoint
# through the post-message call site.  Option (a) from the
# F3 decisions entry: cheaper, same honesty, same UI tolerance.
_NEXT_STEP: dict[str, str] = {
    "PLAN": "EXECUTE",
    "EXECUTE": "ASSESS",
    "EXECUTE_REWORK": "ASSESS",
    "EXECUTE_VERIFY": "ASSESS",
    "ASSESS": "VERIFY",
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


#: File patterns whose changes signal a cycle made progress.
#: Used by ``snapshot_milestone_artifacts`` to capture a before/after
#: state for rework-vs-stall classification (P1, zero-escalations).
#: Excludes ``active/`` (coordinator checkpoints mutate every cycle by
#: design — they'd always report progress, defeating the point) and
#: ``status.md`` (render-only view of the checkpoint).
_ARTIFACT_GLOB_PATTERNS: tuple[str, ...] = (
    "decisions.md",
    "assessment.md",
    "handoff.md",
    "compose.py",
    "phases/*/execution.md",
    "phases/*/phase.md",
    "phases/*/failures/*.md",
    "escalations/*.md",
)


def snapshot_milestone_artifacts(milestone_dir: Path) -> dict[str, str]:
    """Return a content-hash snapshot of milestone artifact state.

    Maps relative path -> hex sha256 digest.  Used by the staleness
    detector to decide whether a cycle made artifact progress (rework
    or otherwise) vs genuinely stalled.  If the snapshot at cycle end
    differs from the snapshot at cycle start, the cycle produced
    evidence on disk and is not a stall.

    Content hash rather than (size, mtime) because (brutalist 3-of-3
    P1-review):
    - ``git checkout`` updates mtime without changing content, so
      revert operations would falsely register as evidence.
    - Touch / formatter-rewrites with identical content (a no-op
      ``compact_decisions()`` call, a hook that re-saves the file)
      would falsely register as evidence.
    - Content hash is immune to these: identical content → identical
      hash, regardless of mtime or inode.

    Symlink protection: resolves each matched path and refuses to
    hash files outside the milestone directory.  A malicious phase
    directory symlink pointing to a rapidly-changing external file
    (e.g., a system log) would otherwise manufacture fake progress
    evidence every cycle.

    Performance: a milestone directory has O(10s) of files at O(10KB)
    each.  Hashing O(100KB) per snapshot * 2 snapshots per cycle is
    trivial vs LLM round-trip times.
    """
    snapshot: dict[str, str] = {}
    if not milestone_dir.is_dir():
        return snapshot
    try:
        milestone_resolved = milestone_dir.resolve()
    except OSError:
        return snapshot
    for pattern in _ARTIFACT_GLOB_PATTERNS:
        for path in milestone_dir.glob(pattern):
            if not path.is_file():
                continue
            # Symlink escape guard: the resolved path must stay within
            # the milestone directory.  A symlink that escapes is
            # silently skipped (not hashed, not counted).
            try:
                resolved = path.resolve()
                resolved.relative_to(milestone_resolved)
            except (OSError, ValueError):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            rel = str(path.relative_to(milestone_dir))
            snapshot[rel] = hashlib.sha256(data).hexdigest()
    return snapshot


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* via tmp-file rename (atomic on POSIX).

    M36 F29: the coordinator's ORIENT plumbing writes the checkpoint
    up to five times per iteration (session-start rewrite, ORIENT-exit
    restoration, retry-counter persistence, escalation reset, plus the
    MCP ``clou_write_checkpoint`` tool).  A signal between ``open()``
    and the buffer flush leaves a truncated file; ``parse_checkpoint``
    silently defaults every missing field, so a partially-written
    checkpoint parses as ``cycle=0, step=PLAN`` — indistinguishable
    from a legitimate fresh milestone.  The atomic helper closes the
    window by writing to a per-call unique temp file in the same
    directory and calling ``os.replace`` to swap into position.  Mirror
    of :func:`clou.supervisor_tools._atomic_write` (same rationale).

    The tmp file is placed in the same directory as the target so
    ``os.replace`` stays on one filesystem (cross-device rename falls
    back to copy+unlink, which is NOT atomic).  ``fsync`` is
    best-effort — we try it for durability across power loss but
    swallow ``OSError`` for filesystems that don't support it (tmpfs).
    """
    parent = target.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, mode="w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def update_staleness(
    state: StalenessState,
    cycle_type: str,
    phases_completed: int,
    *,
    cycle_produced_evidence: bool = False,
) -> None:
    """Update staleness state after a cycle completes.

    Rules (evaluated in order, first match wins):
    - INCONCLUSIVE / INTERRUPTED / HALTED_PENDING_REVIEW outcome:
      reset counter (not stuck).  HALTED_PENDING_REVIEW (M49b) is a
      pause-for-review — the coordinator exited cleanly pending
      supervisor disposition of an open trajectory_halt escalation,
      not a stall — so it joins the same reset class as
      INCONCLUSIVE/INTERRUPTED.  The pre-halt staleness count is
      preserved across the halt via the escalation's resolution
      flow; a truly stalled milestone halted on its first F28 still
      escalates via the staleness path after the configured
      threshold post-resume.
    - **Cycle produced artifact evidence (P1): reset counter.**
      Rework cycles edit phases/*/execution.md, decisions.md, or
      assessment.md without incrementing phases_completed.  The old
      detector misclassified these as stalls; evidence-based
      classification eliminates that false positive.  (zero-escalations.)
    - Cycle type changed: track the change, reset counter to 1.
    - Phase advancement: reset counter to 1 (real progress).
    - Same type, no advancement, but type changed since last reset:
      reset counter to 1 (rework pattern, not staleness).
    - Otherwise: increment counter.
    """
    if state.last_cycle_outcome in (
        "INCONCLUSIVE", "INTERRUPTED", "HALTED_PENDING_REVIEW",
    ):
        state.count = 0
        state.prev_cycle_type = cycle_type
        state.prev_phases_completed = phases_completed
        state.saw_type_change = False
    elif cycle_produced_evidence:
        # Rework or any other cycle that touched milestone artifacts
        # made real progress, even if phases_completed didn't move.
        # Reset to 1 so a truly-stalled run of three evidence-free
        # cycles still catches the real stall.
        state.count = 1
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
# Ceiling for the idle watchdog that wraps the coordinator's receive-response
# loop.  Fires only when the coordinator is genuinely idle (no active agents
# AND no messages streaming).  Wide margin so that user gates, slow MCP tool
# calls, and long-thinking LLM turns never trip it.  Per-task limits are only
# enforced when a DAG author declares ``@resource_bounds(timeout_seconds=...)``
# — there is no implicit default per-task timeout.
_IDLE_WATCHDOG_CEILING_SECONDS: int = 86_400  # 24h


# Keyword fallback for when the SDK's TaskStartedMessage.task_type is
# absent.  Matches specific nouns ("brutalist", "roast") anywhere and
# first-word verbs ("classify", "verify") at position 0 of the
# description.  "Implement verification harness" does NOT match
# "verifier" because "verification" is neither the first word nor a
# listed noun.
_TIER_NOUN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # (tier, nouns that indicate this tier from any position).
    ("brutalist", ("brutalist", "roast")),
]
_TIER_VERB_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # (tier, imperative verbs that indicate this tier as first word).
    ("assess-evaluator", ("classify",)),
    ("verifier", ("verify", "verifier")),
]


def infer_agent_tier(
    description: str,
    vocabulary: set[str],
    *,
    task_type: str | None = None,
) -> str:
    """Map a runtime agent description to its tier.

    When the SDK provides ``task_type`` (the ``subagent_type`` used at
    dispatch), honour it directly — it is the authoritative tier.
    Otherwise fall back to keyword inference:

    - Specific nouns (e.g., ``brutalist``, ``roast``) match anywhere.
    - Imperative verbs (``classify``, ``verify``) match only as the
      first word, so ``"Implement verification harness"`` does NOT
      classify as verifier.

    *vocabulary* is the set of tier names declared in the harness
    template.  Returns ``"worker"`` when no match fits.
    """
    if task_type is not None and task_type in vocabulary:
        return task_type
    tokens = [
        t.strip(".,:;!?()[]{}\"'`").lower()
        for t in description.split()
    ]
    token_set = set(tokens)
    for tier, nouns in _TIER_NOUN_KEYWORDS:
        if tier in vocabulary and any(n in token_set for n in nouns):
            return tier
    if tokens:
        first = tokens[0]
        for tier, verbs in _TIER_VERB_KEYWORDS:
            if tier in vocabulary and first in verbs:
                return tier
    return "worker"


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

    from clou.shard import failure_shard_path

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rel_path = failure_shard_path(milestone_dir.name, phase, task_name)
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
        stdout, _ = await proc.communicate()
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
# Passive escalation notifier (F5 / F14 / F29)
# ---------------------------------------------------------------------------
#
# Module-level so unit tests can exercise the ordering and mark-seen
# semantics without standing up a full ``run_coordinator`` session.
# ``run_coordinator`` keeps a thin closure wrapper (see
# ``_announce_new_escalations`` inside the function) that binds the
# per-milestone state and calls into this helper.


def announce_new_escalations(
    *,
    clou_dir: Path,
    milestone: str,
    seen_escalations: set[str],
    seen_path: Path,
    app: "ClouApp | None",
) -> None:
    """Announce newly-filed escalation files as passive breath events.

    Escalations are agent-to-agent decision records (see
    ``project_escalations_are_agent_to_agent.md``); user-facing
    decisions flow through ``ask_user_mcp``.  This helper posts a
    status-line event when a new file appears, without opening a
    modal or transitioning to DECISION mode.  ``seen-escalations.txt``
    bookkeeping is preserved so DB-15 D5's cycle-count-reset logic
    still has the file it expects.

    F14 (cycle 2) contract:

    - **Mark-seen ordering (F14a / F5).**  ``seen_escalations.add()``
      runs only AFTER a successful ``post_message``.  A transient UI
      failure (``post_message`` raises) does NOT mark the file seen —
      the next invocation retries.  A parse-error still counts as a
      successful signal (the distinct "parse-error" breath event
      fires once, then marks seen so we don't spam every cycle with
      the same drift).
    - **Headless defer (F14a / F29a).**  When ``app is None`` the
      function returns immediately without marking any file seen;
      the next invocation with an attached app re-scans and announces.
    - **Batched seen-write (F14b / F5).**  ``seen_path.write_text``
      runs ONCE after the loop, not per file.
    - **Parse-error short-circuit (F14d / F29b).**  A parse failure
      emits a distinct ``"escalation filed (parse-error): {filename}"``
      event and marks seen.  This fires ONCE per drifted file; the
      next VERIFY cycle picks up on the drift.  Without the
      mark-seen the status line would re-announce the same drifted
      file every cycle (log flood).
    """
    if app is None:
        # F14a / F29a: do not mark seen; defer until the app attaches.
        # The next invocation with an attached app re-scans and
        # announces any still-unseen files.
        return
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    if not esc_dir.is_dir():
        return
    newly_announced: list[str] = []
    for esc_file in sorted(esc_dir.glob("*.md")):
        if esc_file.name in seen_escalations:
            continue
        # Parse for classification; a parse failure surfaces as a
        # distinct breath event rather than silently collapsing to
        # "unknown" (F29b).  The parser contract says it never raises,
        # but we still wrap in Exception because a malformed UTF-8
        # boundary or an unreadable file could surface here.  Read
        # errors are caught upstream; defensive catch only.
        parse_failed = False
        classification = "unknown"
        try:
            form = parse_escalation(esc_file)
            classification = (
                getattr(form, "classification", "") or "unknown"
            ).strip() or "unknown"
        except Exception:
            parse_failed = True
            log.warning(
                "Could not parse escalation %s — filing as parse-error",
                esc_file,
                exc_info=True,
            )
        if parse_failed:
            text = f"escalation filed (parse-error): {esc_file.name}"
        else:
            text = f"escalation filed: {classification}: {esc_file.name}"
        try:
            app.post_message(
                ClouBreathEvent(
                    text=text,
                    cycle_type="",
                    phase=None,
                )
            )
        except Exception:
            # F14a / F5: do NOT mark seen on post failure — next cycle
            # retries and the announcement is not lost.
            log.debug(
                "Could not announce escalation %s",
                esc_file,
                exc_info=True,
            )
            continue
        # F14d: after a successful post (parse-error OR clean parse),
        # commit to seen so we don't spam the status line on every
        # subsequent cycle.
        seen_escalations.add(esc_file.name)
        newly_announced.append(esc_file.name)
    # F14b / F5: single write after the loop (O(1) instead of O(N)).
    # Skip entirely when nothing changed to avoid needless disk I/O.
    if newly_announced:
        try:
            seen_path.write_text(
                "\n".join(sorted(seen_escalations)) + "\n"
            )
        except OSError:
            # Best-effort bookkeeping: an unwritable seen file
            # means next restart re-announces; tolerable.
            log.warning(
                "Could not persist seen-escalations.txt at %s",
                seen_path,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Coordinator session-per-cycle loop
# ---------------------------------------------------------------------------


def _apply_halt_gate(
    esc_dir: Path,
    checkpoint_path: Path,
    milestone: str,
    *,
    origin: str,
) -> str | None:
    """M49b C1+C2: shared halt-gate helper used at both the cycle-loop
    top (B4 / "cycle_start") and the pre-COMPLETE exit point (C2 /
    "pre_complete").  Both gates form a sandwich: a worker that files
    ``clou_halt_trajectory`` mid-cycle cannot escape via either the
    next-iteration boundary OR a COMPLETE checkpoint write before the
    boundary fires.

    Scans *esc_dir* for any open escalation whose classification is
    in ``ENGINE_GATED_CLASSIFICATIONS``.  If found, rewrites the
    checkpoint to ``next_step=HALTED, cycle_outcome=HALTED_PENDING_REVIEW``
    and returns ``"halted_pending_review"`` so the caller can return
    that string from ``run_coordinator``.  If not found, returns
    ``None`` and the caller continues normal flow.

    Fail-open semantics (closes B5/L5): the underlying scan helper
    is documented to fail-open on parse corruption, but Path-level
    errors (PermissionError, OSError) propagate.  We catch any
    exception here and treat it as "no halt found" so a transient FS
    issue does not crash ``run_coordinator``.

    *origin* is a free-form label embedded in telemetry so operators
    can distinguish loop-top halts (the common case) from
    pre-COMPLETE halts (the race-condition case).
    """
    try:
        match = find_open_engine_gated_escalation(esc_dir)
    except Exception:
        log.warning(
            "halt-gate scan failed at %s; treating as no-halt-found "
            "so engine continues",
            origin,
            exc_info=True,
        )
        return None
    if match is None:
        return None

    esc_path, form = match
    # Title embeds the reason label ("Trajectory halt:
    # anti_convergence (cycle N)"); fall back to raw classification
    # if someone filed with an empty title.
    reason = form.title or form.classification
    cycle = read_cycle_count(checkpoint_path)
    log.info(
        "M49b: trajectory-halt gate fired (%s) for %r "
        "(escalation: %s) — exiting run_coordinator",
        origin,
        milestone,
        esc_path,
    )
    if checkpoint_path.exists():
        # M50 I1 cycle-1 round-2 rework (F1 — CRITICAL): defend the
        # checkpoint read identically to ``read_cycle_count`` above.
        # If the underlying file is the same one that fed the halt
        # signal (e.g., vocabulary-migration partial failure marked
        # the active checkpoint as unreadable), re-raising
        # OSError / UnicodeDecodeError here crashes the halt-gate
        # before the HALTED checkpoint can be persisted.  Skip the
        # rewrite on read failure — the existing escalation file is
        # the load-bearing artifact; the supervisor disposes via
        # ``clou_dispose_halt`` regardless of whether the in-memory
        # checkpoint has been rewritten.
        try:
            cp = parse_checkpoint(checkpoint_path.read_text())
        except (OSError, UnicodeDecodeError):
            log.warning(
                "_apply_halt_gate: cannot read checkpoint %s; "
                "skipping HALTED rewrite, escalation already filed",
                checkpoint_path,
                exc_info=True,
            )
            cp = None

        if cp is not None:
            # M49b B6: stash the prior next_step so the supervisor's
            # ``clou_dispose_halt`` can restore it under continue-as-is.
            # If a stash already exists from a prior un-disposed halt,
            # preserve it (the older context is the correct restoration
            # target).  Refuse to stash HALTED itself: stashing HALTED
            # would create a restoration loop.
            if cp.pre_halt_next_step:
                stash = cp.pre_halt_next_step
            elif cp.next_step != "HALTED":
                stash = cp.next_step
            else:
                stash = ""
            try:
                checkpoint_path.write_text(
                    render_checkpoint(
                        cycle=cp.cycle,
                        step=cp.step,
                        next_step="HALTED",
                        current_phase=cp.current_phase,
                        phases_completed=cp.phases_completed,
                        phases_total=cp.phases_total,
                        validation_retries=cp.validation_retries,
                        readiness_retries=cp.readiness_retries,
                        crash_retries=cp.crash_retries,
                        staleness_count=cp.staleness_count,
                        cycle_outcome="HALTED_PENDING_REVIEW",
                        valid_findings=cp.valid_findings,
                        consecutive_zero_valid=cp.consecutive_zero_valid,
                        pre_orient_next_step=cp.pre_orient_next_step,
                        pre_halt_next_step=stash,
                        # M52 F38: passthrough — halt does not produce a
                        # gate verdict, so inherit whatever the prior
                        # cycle recorded.
                        last_acceptance_verdict=cp.last_acceptance_verdict,
                    )
                )
            except OSError:
                log.warning(
                    "_apply_halt_gate: cannot write HALTED rewrite to "
                    "%s; escalation already filed",
                    checkpoint_path,
                    exc_info=True,
                )
    telemetry.event(
        "cycle.halted",
        milestone=milestone,
        reason=reason,
        cycle_num=cycle,
        escalation_path=str(esc_path),
        origin=origin,
    )
    return "halted_pending_review"


def _run_phase_acceptance_gate(
    *,
    project_dir: Path,
    milestone: str,
    phase: str,
    checkpoint_path: Path,
) -> None:
    """Run the phase-acceptance gate (M52 F32) for *phase*.

    Reads the phase's ``phase.md`` to discover the declared
    deliverable type, reads the worker's ``execution.md``, calls
    :func:`clou.phase_acceptance.check_phase_acceptance`, and writes
    the verdict into the checkpoint envelope so the next
    ``clou_write_checkpoint`` call sees it via
    ``prev_cp.last_acceptance_verdict``.

    Called once per ASSESS cycle before the LLM ASSESS prompt fires.
    No-ops gracefully (with telemetry) when:

    - The phase has no ``phase.md`` (orphan phase / scaffolding race).
    - The phase's ``phase.md`` lacks a typed deliverable declaration
      (legacy / pre-M52 phases — the F41 migration shim handles
      these via the bootstrap path in ``clou_write_checkpoint``).
    - ``execution.md`` is missing (worker hasn't produced output yet).

    All file I/O is wrapped in defensive ``try/except`` so the
    gate is fail-soft: a gate that can't read its inputs leaves the
    prior verdict in place rather than blocking the cycle.
    """
    from clou.artifacts import parse_phase_deliverable_type
    from clou.phase_acceptance import (
        Advance,
        check_phase_acceptance,
    )
    from clou.recovery_checkpoint import AcceptanceVerdict

    ms_dir = project_dir / ".clou" / "milestones" / milestone
    phase_md_path = ms_dir / "phases" / phase / "phase.md"
    execution_md_path = ms_dir / "phases" / phase / "execution.md"

    if not phase_md_path.exists():
        telemetry.event(
            "phase_acceptance.skipped",
            milestone=milestone,
            phase=phase,
            reason="phase_md_missing",
        )
        return
    if not execution_md_path.exists():
        telemetry.event(
            "phase_acceptance.skipped",
            milestone=milestone,
            phase=phase,
            reason="execution_md_missing",
        )
        return
    if not checkpoint_path.exists():
        telemetry.event(
            "phase_acceptance.skipped",
            milestone=milestone,
            phase=phase,
            reason="checkpoint_missing",
        )
        return

    try:
        phase_md_text = phase_md_path.read_text(encoding="utf-8")
        execution_text = execution_md_path.read_text(encoding="utf-8")
    except OSError:
        log.warning(
            "_run_phase_acceptance_gate: I/O failure reading phase "
            "inputs for %r/%r; skipping gate",
            milestone, phase, exc_info=True,
        )
        telemetry.event(
            "phase_acceptance.skipped",
            milestone=milestone,
            phase=phase,
            reason="io_error",
        )
        return

    declared_type = parse_phase_deliverable_type(phase_md_text)
    if declared_type is None:
        # Legacy phase.md: pre-M52 ``Single deliverable file: <path>``
        # format with no typed declaration.  The migration shim (F41)
        # in ``clou_write_checkpoint`` allows the first advance via
        # the bootstrap path; the engine doesn't write a verdict here.
        telemetry.event(
            "phase_acceptance.skipped",
            milestone=milestone,
            phase=phase,
            reason="legacy_phase_md",
        )
        return

    result = check_phase_acceptance(
        milestone=milestone,
        phase=phase,
        declared_deliverable_type=declared_type,
        execution_md_text=execution_text,
    )

    if isinstance(result, Advance):
        verdict = AcceptanceVerdict(
            phase=result.phase,
            decision="Advance",
            content_sha=result.content_sha,
        )
    else:  # GateDeadlock
        verdict = AcceptanceVerdict(
            phase=result.phase,
            decision="GateDeadlock",
            content_sha="",
        )

    # Persist the verdict by re-rendering the checkpoint with the
    # verdict field updated.  This is an engine-side write — it does
    # NOT bump phases_completed; the LLM still drives that via
    # ``clou_write_checkpoint`` (F32 single-writer protocol).  The
    # only field that changes is ``last_acceptance_verdict``.
    try:
        cp = parse_checkpoint(checkpoint_path.read_text(encoding="utf-8"))
        new_body = render_checkpoint(
            cycle=cp.cycle,
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
            pre_orient_next_step=cp.pre_orient_next_step,
            pre_halt_next_step=cp.pre_halt_next_step,
            last_acceptance_verdict=verdict,
        )
        _atomic_write(checkpoint_path, new_body)
    except (OSError, ValueError):
        log.warning(
            "_run_phase_acceptance_gate: failed to persist verdict "
            "for %r/%r; gate result will not be visible to the LLM",
            milestone, phase, exc_info=True,
        )
        telemetry.event(
            "phase_acceptance.persist_failed",
            milestone=milestone,
            phase=phase,
            verdict_decision=verdict.decision,
        )


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
    # M50 I1 cycle-1 round-2 rework (F1 — CRITICAL): if
    # ``checkpoint_path`` exists but is unreadable (chmod 000, invalid
    # UTF-8, file lock), ``read_text()`` raises ``OSError`` /
    # ``UnicodeDecodeError`` and crashes ``run_coordinator`` BEFORE
    # the migration sweep + halt-gate can engage.  The migration
    # sweep records unreadable files in its ``failed`` list and the
    # halt-gate filing block (below) escalates them; treating an
    # unreadable checkpoint here as "no parsed state" lets the
    # session reach those defensive surfaces with retry counters at
    # the dataclass defaults.
    _initial_cp = None
    if checkpoint_path.exists():
        try:
            _initial_cp = parse_checkpoint(
                checkpoint_path.read_text()
            )
        except (OSError, UnicodeDecodeError):
            log.warning(
                "run_coordinator: cannot read initial checkpoint %s; "
                "continuing with default retry counters so the "
                "migration sweep + halt-gate can engage",
                checkpoint_path,
                exc_info=True,
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
    # P1: rework-vs-stall baseline.  Content hash of relevant
    # milestone artifacts; compared at the staleness-check point to
    # decide whether the cycle produced artifact evidence.  Updated
    # at every check AND explicitly after recovery operations
    # (revert, commit, compaction) so the baseline reflects post-
    # recovery state -- brutalist P1-review finding that revert
    # changes appear as phantom evidence otherwise.
    _artifact_snapshot: dict[str, str] = (
        snapshot_milestone_artifacts(clou_dir / "milestones" / milestone)
    )

    decisions_path = clou_dir / "milestones" / milestone / "decisions.md"
    # C2: seen-escalations.txt is per-milestone to close the cross-
    # coordinator race filed as an architectural escalation in M41
    # cycle-1 ASSESS (`20260421-120000-seen-escalations-global-race-
    # carryover.md`).  Under parallel dispatch, two coordinators
    # running concurrently against a global file read-modify-wrote
    # each other's additions; scoping the file to the milestone
    # directory (which each coordinator owns exclusively) eliminates
    # the race structurally.  The old global path was
    # ``.clou/active/seen-escalations.txt``; we read-migrate it once
    # on first use if the new per-milestone file doesn't exist yet.
    seen_path = clou_dir / "milestones" / milestone / "active" / "seen-escalations.txt"
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    _legacy_seen_path = clou_dir / "active" / "seen-escalations.txt"
    seen_escalations: set[str] = set()
    # F28: degrade to empty set on corrupted / unreadable bookkeeping.
    # A fail-hard read here would brick the coordinator at startup;
    # passive notification is tolerant.
    if seen_path.exists():
        try:
            seen_escalations = set(seen_path.read_text().splitlines())
        except (UnicodeDecodeError, OSError):
            log.warning(
                "Corrupted seen-escalations.txt at %s; starting fresh. "
                "New escalations will be re-announced.",
                seen_path,
                exc_info=True,
            )
            seen_escalations = set()
    elif _legacy_seen_path.exists():
        # One-shot migration from the old global path.  Only inherit
        # entries that correspond to files in THIS milestone's
        # escalations directory -- the global file accumulated entries
        # across milestones, but this milestone's seen set only
        # diffs against its own ``escalations/`` directory.
        #
        # Filename-collision caveat: if two milestones happened to
        # share an escalation basename, this intersection could inherit
        # the wrong milestone's seen state.  In practice escalation
        # filenames are timestamped (``YYYYMMDD-HHMMSS-*.md``) or
        # uniquely prefixed per milestone, so collisions are unlikely;
        # worst case is a single false-negative announcement for the
        # duplicated filename.  Documented here; not worth a broader
        # migration-namespacing scheme for a one-shot boundary.
        try:
            _legacy_entries = set(_legacy_seen_path.read_text().splitlines())
        except (UnicodeDecodeError, OSError):
            _legacy_entries = set()
        _this_esc_dir = clou_dir / "milestones" / milestone / "escalations"
        if _this_esc_dir.is_dir():
            _this_esc_names = {
                p.name for p in _this_esc_dir.glob("*.md")
            }
            seen_escalations = _legacy_entries & _this_esc_names
        if seen_escalations:
            # Persist the migrated set immediately so the migration is
            # truly one-shot.  Without this write, the next startup
            # would see ``seen_path`` missing, re-read the legacy file,
            # and re-migrate -- the "one-shot" contract in the comment
            # above would be a lie.
            try:
                seen_path.write_text(
                    "\n".join(sorted(seen_escalations)) + "\n"
                )
            except OSError:
                log.warning(
                    "Could not persist migrated seen-escalations to %s; "
                    "migration will repeat next startup.",
                    seen_path,
                    exc_info=True,
                )
            log.info(
                "Migrated %d seen-escalation entries from %s to %s",
                len(seen_escalations),
                _legacy_seen_path,
                seen_path,
            )

    # DB-15 D5: Reset cycle count if the LATEST escalation was resolved.
    # Check only the most recent escalation (sorted by timestamp filename).
    #
    # F1 partial (cycle 2, security): the resolved-escalation gate below
    # must route through the tolerant parser in ``clou/escalation.py``
    # rather than a raw ``re.search``.  The old regex matched any
    # ``^status: resolved`` line anywhere in the file — including plain
    # prose inside evidence / recommendation bodies, which are NOT
    # escaped by ``_escape_field`` (that helper only targets heading
    # markers).  A drifted coordinator (or a forged legacy file) could
    # therefore forge a cycle-count reset by writing the literal line
    # ``status: resolved`` inside any free-text field.
    #
    # Delegating to :func:`clou.escalation.parse_latest_disposition`
    # honours ``find_last_disposition_span`` so only the canonical
    # trailing ``## Disposition`` block decides the reset.  Writer
    # (supervisor's resolver) and reader (this block) now read the same
    # anchor — the cycle-2 F27 three-reader consolidation.
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    if esc_dir.is_dir() and checkpoint_path.exists():
        esc_files = sorted(esc_dir.glob("*.md"))
        latest = esc_files[-1] if esc_files else None
        _esc_text = latest.read_text(encoding="utf-8") if latest else ""
        _esc_status = ""
        if latest:
            try:
                _esc_status = parse_latest_disposition(_esc_text)
            except Exception:  # pragma: no cover - parser never raises
                _esc_status = ""
        resolved = _esc_status in RESOLVED_DISPOSITION_STATUSES
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
                        # M36 I1 (F2 rework): preserve ORIENT stash
                        # across escalation reset.
                        pre_orient_next_step=cp.pre_orient_next_step,
                        # M49b B6: preserve halt-restoration stash
                        # across escalation reset.
                        pre_halt_next_step=cp.pre_halt_next_step,
                        # M52 F38: passthrough — escalation reset is
                        # orthogonal to the gate; inherit the verdict.
                        last_acceptance_verdict=cp.last_acceptance_verdict,
                    )
                )
            log.info(
                "Cycle count reset for %r after resolved escalation",
                milestone,
            )

    def _announce_new_escalations() -> None:
        """Announce newly-filed escalation files as passive breath events.

        Thin wrapper around :func:`announce_new_escalations` that binds
        the enclosing ``run_coordinator`` scope (``clou_dir``,
        ``milestone``, ``seen_escalations``, ``seen_path``) so callers
        in the cycle loop remain unchanged.  The module-level helper is
        separately testable — see ``tests/test_coordinator.py``.
        """
        announce_new_escalations(
            clou_dir=clou_dir,
            milestone=milestone,
            seen_escalations=seen_escalations,
            seen_path=seen_path,
            app=get_active_app(),
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
                # M36 I1 (F2 rework): preserve ORIENT stash across
                # retry-counter writes. F13 cited _persist_retry_counters
                # as the primary wipe site that turned disagreement
                # telemetry into confident-wrong signal on retry paths.
                pre_orient_next_step=cp.pre_orient_next_step,
                # M49b B6: preserve halt-restoration stash across
                # retry-counter writes (same wipe-class concern).
                pre_halt_next_step=cp.pre_halt_next_step,
                # M52 F38: same wipe-class — retry-counter writes are
                # not gate-producing, so inherit the verdict.
                last_acceptance_verdict=cp.last_acceptance_verdict,
            )
        )

    _ms_outcome = "unknown"
    _last_rework_decision_cycle: int | None = None  # G6: decision outcome tracking
    # Track which (message, path) pairs have already emitted a validation
    # warning in this milestone run.  Warnings are cumulative because
    # validate_golden_context rescans all phase directories and completed
    # phases have errors downgraded to warnings (validation.py:347-348).
    # Report deltas so the signal reflects *new* structural issues this
    # cycle, not the growing residue of prior cycles.
    _warnings_seen: set[tuple[str, str]] = set()
    # M36 I1: ORIENT-first dispatch flag. Every fresh ``run_coordinator``
    # invocation (new milestone, crash re-entry, interrupt resume) starts
    # here; the while-loop top rewrites ``next_step`` to ``ORIENT`` once
    # if the checkpoint isn't already pointed at ORIENT. This is an
    # in-memory flag — we do NOT persist it to the checkpoint, because
    # "is this the first iteration of THIS Python process" is exactly
    # the signal we want. A crash re-entry creates a new process and
    # therefore a new first-iteration window, which is what gives us
    # observation-first dispatch on every session.
    # M50 I1: one-shot vocabulary migration at session start.  Rewrites
    # any persisted legacy cycle-type tokens (``EXECUTE (rework)`` /
    # ``EXECUTE (additional verification)``) under ``.clou/`` to the
    # structured identifiers (``EXECUTE_REWORK`` / ``EXECUTE_VERIFY``).
    # Idempotent — running on already-migrated state returns zero
    # counts and produces no writes.  Cycle-2 rework (F28/F8/F35):
    # narrow the exception scope to OSError — filesystem blips must
    # not crash session start, but a programming error (ImportError,
    # AttributeError, TypeError) MUST propagate rather than silently
    # downgrade to a warning.  The prior bare-except masked the
    # migration surface from its own tests: a broken import or
    # regression in the rewrite helper would be invisible at session
    # start.  Telemetry records the per-family rewrite counts so the
    # event is auditable from metrics.md; partial-failure paths
    # (F1/F15) surface via a distinct ``vocabulary_migration.partial_failure``
    # event so operators can distinguish "nothing needed migration"
    # from "some files could not be rewritten".
    from clou.vocabulary_migration import migrate_legacy_tokens
    try:
        _migration_counts = migrate_legacy_tokens(clou_dir)
    except OSError:
        # M50 I1 cycle-3 rework (F10): the migration helper catches
        # per-file read/write OSErrors INTERNALLY and records them in
        # ``counts["failed"]`` so the sweep never aborts mid-stream.
        # This outer ``except OSError`` therefore only catches a much
        # narrower surface: errors in the sweep-level directory
        # traversal itself (``iterdir``, ``is_dir``, ``glob`` on
        # ``milestones/`` or ``prompts/``) — e.g. if ``.clou/`` exists
        # but was replaced with a regular file, or the directory is
        # mounted with EIO.  Per-file failures NEVER reach this
        # handler; they surface via the ``failed`` list below.
        # ImportError / AttributeError / TypeError / RuntimeError
        # propagate past this handler so a programming regression
        # surfaces loudly at session start.
        log.warning(
            "M50 I1: vocabulary migration hit sweep-level filesystem "
            "error (e.g., .clou/ unreadable or corrupt dir entry); "
            "continuing without sweep",
            exc_info=True,
        )
    else:
        _rewrite_counts = {
            k: v for k, v in _migration_counts.items()
            if k != "failed"
        }
        _failed_paths = _migration_counts.get("failed", []) or []
        if any(_rewrite_counts.values()):
            log.info(
                "M50 I1: vocabulary migration rewrote %s",
                _rewrite_counts,
            )
            telemetry.event(
                "vocabulary_migration.rewrote",
                milestone=milestone,
                counts=_rewrite_counts,
            )
        if _failed_paths:
            # Partial-failure signal: distinct event name so dashboards
            # can alert on "some files skipped" without confusing it
            # with "nothing needed migration".
            log.warning(
                "M50 I1: vocabulary migration partial failure — "
                "could not rewrite %d file(s): %s",
                len(_failed_paths),
                [str(p) for p in _failed_paths],
            )
            telemetry.event(
                "vocabulary_migration.partial_failure",
                milestone=milestone,
                failed=[str(p) for p in _failed_paths],
            )
            # M50 I1 cycle-2 EXECUTE_REWORK (F4 path a — engine halt-gate).
            # The cycle-4 surface logged the partial failure and continued
            # into the dispatch loop; if the active milestone's
            # ``active/coordinator.md`` was in the failed list, the next
            # ``parse_checkpoint`` would read the unmigrated legacy token,
            # reject it, default ``next_step`` to ``PLAN``, and silently
            # re-plan a completed phase — bypassing M49b's engine halt
            # gate (the architectural surface designed to handle
            # exactly this class of unrecoverable inconsistency).
            #
            # Path (a) from the supervisor's re-scope brief (preferred
            # because it preserves migration coverage on active state):
            # when the active milestone's checkpoint is in the failed
            # list, file a structured ``trajectory_halt`` escalation.
            # The next loop iteration's ``_apply_halt_gate`` picks it
            # up via ``find_open_engine_gated_escalation``, rewrites
            # the checkpoint to HALTED_PENDING_REVIEW, and exits
            # ``run_coordinator`` cleanly — the supervisor disposes
            # via ``clou_dispose_halt`` after consulting the user.
            #
            # Other-milestone failures remain best-effort (telemetry
            # only) — those checkpoints are not on the dispatch path
            # this session.  The narrow guard ("failing path belongs
            # to the active milestone") was named explicitly in
            # CLAUDE FINDING 2 (decisions.md F4 Action block).
            # M50 I1 cycle-1 round-2 rework (F2 — CRITICAL):
            # raw string equality silently false-negatives on
            # macOS NFD/NFC, symlinks, case-insensitive volumes,
            # relative-vs-absolute path drift.  Compare via
            # ``Path.resolve()`` (matches the canonical pattern at
            # ``coordinator.py:235``) and fall back to
            # ``os.path.samefile`` only when both paths exist —
            # ``samefile`` raises if either path is missing, which
            # is an expected case for in-memory failure lists where
            # a stub or non-existent path may be reported.
            try:
                _active_cp_resolved = checkpoint_path.resolve()
            except OSError:
                _active_cp_resolved = checkpoint_path
            _active_cp_str = str(checkpoint_path)
            _active_failed = False
            for _p in _failed_paths:
                _candidate = Path(_p) if not isinstance(_p, Path) else _p
                try:
                    if _candidate.resolve() == _active_cp_resolved:
                        _active_failed = True
                        break
                except OSError:
                    # Resolution failed for one or both paths; fall
                    # back to NFC-normalised string comparison so
                    # macOS NFD↔NFC drift still matches.
                    if (
                        unicodedata.normalize("NFC", str(_candidate))
                        == unicodedata.normalize("NFC", _active_cp_str)
                    ):
                        _active_failed = True
                        break
            if _active_failed:
                # M50 I1 cycle-1 round-2 rework (F4 — MAJOR): wrap the
                # entire halt-filing block in an outer try/except so
                # any failure (FS unreadable, slug exhaustion under
                # squat-DoS attack, EIO mid-write) degrades to
                # log-and-continue rather than crashing
                # ``run_coordinator``.  Filing the halt is a defensive
                # surface; it must NEVER itself crash the engine the
                # caller relies on to exit cleanly.
                try:
                    from clou.escalation import (
                        EscalationForm,
                        EscalationOption,
                        HALT_OPTION_LABELS,
                        render_escalation,
                    )
                    from datetime import UTC, datetime as _dt

                    _now = _dt.now(UTC)
                    _ts = _now.strftime("%Y%m%d-%H%M%S")
                    _cycle_num = read_cycle_count(checkpoint_path)
                    _halt_options = tuple(
                        EscalationOption(
                            label=_label,
                            description={
                                "continue-as-is": (
                                    "Re-dispatch with current scope. "
                                    "Use after the user has manually "
                                    "rewritten the legacy cycle-type "
                                    "token in the active checkpoint."
                                ),
                                "re-scope": (
                                    "Route to PLAN with new scope. "
                                    "Use if the on-disk legacy token "
                                    "is too ambiguous to recover "
                                    "programmatically."
                                ),
                                "abandon": (
                                    "Route to EXIT; milestone outcome "
                                    "escalated_trajectory.  Use only "
                                    "when the milestone is "
                                    "unrecoverable."
                                ),
                            }.get(_label, ""),
                        )
                        for _label in HALT_OPTION_LABELS
                    )
                    _halt_form = EscalationForm(
                        title=(
                            f"Trajectory halt: vocabulary_migration."
                            f"partial_failure (cycle {_cycle_num})"
                        ),
                        classification="trajectory_halt",
                        filed=_now.isoformat(),
                        context=(
                            "Session-start vocabulary migration "
                            "could not rewrite the active milestone's "
                            "checkpoint.  The legacy cycle-type token "
                            "on disk is rejected by parse_checkpoint "
                            "at the next read, which would default "
                            "next_step to PLAN and silently re-plan a "
                            "completed phase.  Halting via M49b "
                            "engine halt gate so the supervisor "
                            "disposes before the dispatch loop runs."
                        ),
                        issue=(
                            f"migrate_legacy_tokens reported the "
                            f"active checkpoint in its `failed` "
                            f"list:\n"
                            f"  - `{_active_cp_str}`\n\n"
                            "Remediation requires either (a) fixing "
                            "the underlying filesystem condition "
                            "(file lock, readonly mount, ENOSPC, "
                            "EIO) and re-running, or (b) manually "
                            "rewriting the legacy token in the "
                            "active checkpoint and disposing this "
                            "halt with continue-as-is."
                        ),
                        evidence="\n".join(
                            f"- `{p}`" for p in _failed_paths
                        ),
                        options=_halt_options,
                        recommendation=(
                            "The coordinator detected an "
                            "unrecoverable session-start migration "
                            "failure on the active checkpoint. "
                            "Inspect the failing path, fix the "
                            "underlying condition, and dispose this "
                            "halt via `clou_dispose_halt` (M49b "
                            "engine-gated)."
                        ),
                        disposition_status="open",
                    )
                    _halt_content = render_escalation(_halt_form)
                    # M50 I1 cycle-1 round-2 rework (F3 — CRITICAL
                    # security CWE-59): symlink boundary escape.  A
                    # poisoned ``.clou/milestones/{ms}/escalations``
                    # symlink pointing at ``~/.ssh`` or ``~/.aws``
                    # turns this branch into an arbitrary
                    # write-redirection primitive.  Resolve
                    # ``clou_dir`` once (canonical defense pattern at
                    # ``recovery_checkpoint.py:658-676`` and
                    # ``coordinator.py:2360-2383``), reject the path
                    # if it is itself a symlink, refuse to follow it
                    # to a destination outside ``clou_dir``, and use
                    # ``O_NOFOLLOW`` on the actual halt write so a
                    # late-bound symlink is rejected by the kernel.
                    try:
                        _clou_resolved = clou_dir.resolve(
                            strict=False
                        )
                    except OSError:
                        _clou_resolved = clou_dir
                    _halt_dir = (
                        clou_dir / "milestones" / milestone
                        / "escalations"
                    )
                    if _halt_dir.is_symlink():
                        raise OSError(
                            f"refusing to write halt under symlinked "
                            f"escalations dir: {_halt_dir}"
                        )
                    _halt_dir.mkdir(parents=True, exist_ok=True)
                    # Verify the resolved escalations directory is
                    # rooted at ``clou_dir`` — an attacker who pre-
                    # placed a symlink BEFORE mkdir would still be
                    # caught here even if the symlink check raced.
                    try:
                        _halt_dir_resolved = _halt_dir.resolve(
                            strict=False
                        )
                    except OSError:
                        _halt_dir_resolved = _halt_dir
                    try:
                        _halt_dir_resolved.relative_to(_clou_resolved)
                    except ValueError as _exc:
                        raise OSError(
                            f"halt dir resolved outside clou_dir "
                            f"boundary: {_halt_dir_resolved} not "
                            f"under {_clou_resolved}"
                        ) from _exc
                    # M50 I1 cycle-1 round-2 rework (F5 — MAJOR
                    # security CWE-340/409): predictable timestamp
                    # slugs let a cooperative-multitenant attacker
                    # squat ``{ts}-trajectory-halt-{1..999}.md`` and
                    # exhaust the suffix loop, crashing the
                    # coordinator AND re-opening the silent re-plan
                    # window F4 was designed to prevent.  Append a
                    # 32-bit random suffix; predictability buys
                    # nothing because the timestamp prefix is for
                    # sort ordering, not uniqueness.  With random
                    # suffix, suffix-loop collisions become
                    # astronomically improbable; reduce the loop
                    # bound to 16.
                    _rand = secrets.token_hex(4)
                    _base_stem = (
                        f"{_ts}-trajectory-halt-{_rand}"
                    )
                    _halt_path = _halt_dir / f"{_base_stem}.md"
                    _suffix = 1
                    while True:
                        try:
                            # ``O_NOFOLLOW`` rejects symlinks bound
                            # late between mkdir and open; raises
                            # OSError(ELOOP) which the outer
                            # try/except (F4) converts to a clean
                            # log-and-continue.
                            _fd = os.open(
                                str(_halt_path),
                                os.O_WRONLY
                                | os.O_CREAT
                                | os.O_EXCL
                                | os.O_NOFOLLOW,
                                0o600,
                            )
                            try:
                                os.write(
                                    _fd, _halt_content.encode("utf-8")
                                )
                            finally:
                                os.close(_fd)
                            break
                        except FileExistsError:
                            _halt_path = _halt_dir / (
                                f"{_base_stem}-{_suffix}.md"
                            )
                            _suffix += 1
                            if _suffix > 16:
                                raise RuntimeError(
                                    "exhausted slug suffix range "
                                    "filing vocabulary_migration."
                                    "partial_failure halt"
                                )
                    log.warning(
                        "M50 I1 (F4 path a): active checkpoint %s "
                        "in migration failed list; filed engine-"
                        "gated trajectory_halt at %s",
                        _active_cp_str,
                        _halt_path,
                    )
                    telemetry.event(
                        "vocabulary_migration.partial_failure."
                        "halt_filed",
                        milestone=milestone,
                        halt_path=str(_halt_path),
                        failed=[str(p) for p in _failed_paths],
                    )
                except Exception:
                    log.warning(
                        "M50 I1 (F4 outer guard): vocabulary-"
                        "migration partial-failure halt-filing "
                        "raised; degrading to log-and-continue so "
                        "the engine stays up",
                        exc_info=True,
                    )
                    telemetry.event(
                        "vocabulary_migration.partial_failure."
                        "halt_filing_failed",
                        milestone=milestone,
                        failed=[str(p) for p in _failed_paths],
                    )

    first_iteration = True
    telemetry.event("milestone.start", milestone=milestone)
    try:
        while True:
            # --- Cycle-boundary checks (DB-15) ---

            # F29c: single post-cycle announcement hook. Each iteration
            # begins by flushing any escalations written by the previous
            # iteration (or by recovery paths that returned early). The
            # outer ``finally`` block also flushes once more so the very
            # last iteration's writes are still announced before the UI
            # handle is released.
            # F14c (cycle 2): a filesystem blip (EIO, ENOSPC, permission
            # flap) inside the announcer must not crash the coordinator
            # loop — passive notification is best-effort bookkeeping, not
            # load-bearing control flow.
            try:
                _announce_new_escalations()
            except Exception:
                log.warning(
                    "passive escalation notifier failed at cycle start",
                    exc_info=True,
                )

            # M49b B4: trajectory-halt engine gate (Option A placement).
            # Scan ``escalations/*.md`` via the testable helper for any
            # OPEN escalation whose classification is in
            # ENGINE_GATED_CLASSIFICATIONS (currently just
            # "trajectory_halt").  If found, write a
            # HALTED_PENDING_REVIEW checkpoint and exit run_coordinator
            # cleanly without constructing the SDK client.  Placement
            # is AFTER announce_new_escalations (so the UI sees the
            # halt before the engine exits) and BEFORE the /stop
            # check (so structured halt takes priority over
            # unstructured kill), ORIENT rewrite, determine_next_cycle,
            # staleness, readiness, prompt build, and _run_single_cycle.
            # The scan helper fails-open on parse corruption; see
            # :func:`find_open_engine_gated_escalation` for details.
            _halt_esc_dir = (
                clou_dir / "milestones" / milestone / "escalations"
            )
            _halt_outcome = _apply_halt_gate(
                _halt_esc_dir,
                checkpoint_path,
                milestone,
                origin="cycle_start",
            )
            if _halt_outcome is not None:
                _ms_outcome = _halt_outcome
                return _halt_outcome

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

            # M36 I1 (Edit A): graceful git diff capture.
            # Run ``git diff --stat`` from the project root every
            # iteration and drop the output at
            # ``active/git-diff-stat.txt``. ORIENT reads this file;
            # running the capture every cycle (not just ORIENT) keeps
            # the view fresh for back-to-back sessions without needing
            # to route git access through the ORIENT branch. Graceful
            # degradation: any error (no git, not a repo, shell blip,
            # timeout) falls back to empty string — the coordinator
            # must never fail because git is unavailable.
            try:
                _diff_result = subprocess.run(
                    ["git", "diff", "--stat"],
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                _diff_output = _diff_result.stdout or ""
            except (subprocess.SubprocessError, FileNotFoundError, OSError):
                _diff_output = ""
            _diff_path = clou_dir / "milestones" / milestone / "active" / "git-diff-stat.txt"
            try:
                _diff_path.parent.mkdir(parents=True, exist_ok=True)
                _diff_path.write_text(_diff_output, encoding="utf-8")
            except OSError:
                # If even writing the empty file fails, swallow and
                # move on — ORIENT's reader tolerates missing content.
                log.debug(
                    "could not write git-diff-stat.txt for %r",
                    milestone, exc_info=True,
                )

            # M36 I1 (F1 rework): ORIENT-exit restoration.
            # Every NON-first iteration (post session-start seed) checks
            # whether next_step is still ORIENT with a non-empty
            # pre_orient_next_step.  If so, the prior ORIENT cycle has
            # completed; restore the saved step so dispatch resumes
            # where it would have gone and clear the stash to signal
            # "no pending restoration".  This is the restoration
            # code the cycle-1/cycle-2 critics flagged as missing —
            # without it, every session would dispatch ORIENT forever
            # (until _MAX_CYCLES).  Without this block, the prompt's
            # "orchestrator restores" claim is a lie (F1 +
            # coordinator-orient.md correction).
            #
            # Order matters: this block fires BEFORE the session-start
            # rewrite so iteration-1 (first_iteration=True) is gated
            # out.  On iteration-2+, first_iteration is False, the
            # session-start rewrite is skipped, and this block carries
            # the prior ORIENT's stash back into next_step.
            #
            # M36 F1 (round-4): the read_text()+parse_checkpoint() calls
            # MUST be inside the try/except.  Previously only the rewrite
            # was guarded; any transient read or parse failure (partial
            # write, encoding glitch, filesystem blip) crashed
            # run_coordinator before dispatch.  The whole block is now
            # wrapped; on any failure log a warning and skip restoration
            # (the next iteration's session-start rewrite will no-op
            # because next_step is already ORIENT).
            if not first_iteration and checkpoint_path.exists():
                try:
                    _cp_exit = parse_checkpoint(
                        checkpoint_path.read_text(encoding="utf-8"),
                    )
                except (OSError, ValueError):
                    log.warning(
                        "Could not read/parse checkpoint for ORIENT-exit "
                        "restoration at %s",
                        checkpoint_path, exc_info=True,
                    )
                    _cp_exit = None

                if (
                    _cp_exit is not None
                    and _cp_exit.next_step == "ORIENT"
                    and _cp_exit.pre_orient_next_step
                ):
                    _restored_step = _cp_exit.pre_orient_next_step
                    try:
                        # M36 F6 (round-4): retry counters have
                        # cycle-type semantics — they accumulate against
                        # the work the current cycle is doing (readiness
                        # bounces for ORIENT, validation bounces for
                        # ORIENT, etc.).  Carrying them onto the restored
                        # EXECUTE/PLAN/ASSESS cycle would face an
                        # already-armed retry ceiling before the restored
                        # cycle has done any work.  Reset all four to
                        # zero on restoration.
                        _restored_body = render_checkpoint(
                            cycle=_cp_exit.cycle,
                            step=_cp_exit.step,
                            next_step=_restored_step,
                            current_phase=_cp_exit.current_phase,
                            phases_completed=_cp_exit.phases_completed,
                            phases_total=_cp_exit.phases_total,
                            validation_retries=0,
                            readiness_retries=0,
                            crash_retries=0,
                            staleness_count=0,
                            cycle_outcome=_cp_exit.cycle_outcome,
                            valid_findings=_cp_exit.valid_findings,
                            consecutive_zero_valid=(
                                _cp_exit.consecutive_zero_valid
                            ),
                            # Clear the stash so the next iteration
                            # sees "no pending restoration".
                            pre_orient_next_step="",
                            # M49b B6: ORIENT restoration is orthogonal
                            # to halt restoration; preserve halt stash.
                            pre_halt_next_step=_cp_exit.pre_halt_next_step,
                            # M52 F38: ORIENT restoration is orthogonal
                            # to gate verdict; inherit.
                            last_acceptance_verdict=(
                                _cp_exit.last_acceptance_verdict
                            ),
                        )
                        # M36 F29 (round-4): atomic write closes the
                        # truncation window.  Previously a signal
                        # between open() and flush could leave the
                        # checkpoint half-written; parse_checkpoint
                        # silently defaults missing fields so a
                        # truncated file parsed as cycle=0, step=PLAN
                        # — indistinguishable from a legitimate fresh
                        # milestone.
                        _atomic_write(checkpoint_path, _restored_body)
                        # M36 F15 (round-4): re-render status.md so it
                        # reflects the restored next_step.  Without this
                        # the checkpoint's next_step is (say) EXECUTE
                        # while status.md still shows ORIENT — a
                        # split-brain state the dispatch layer may
                        # consume (readiness validators, UI surfaces,
                        # the agent's own observation in subsequent
                        # cycles).  Mirror the seed pattern at the
                        # fresh-milestone branch below and the MCP
                        # tool's side effect.  Guard with try/except
                        # OSError — status.md loss is recoverable on
                        # the next MCP rewrite; the checkpoint
                        # rewrite is the primary contract.
                        try:
                            from clou.golden_context import render_status
                            _status_path = (
                                clou_dir / "milestones" / milestone
                                / "status.md"
                            )
                            _status_body = render_status(
                                milestone=milestone,
                                phase=_cp_exit.current_phase or "",
                                cycle=_cp_exit.cycle,
                                next_step=_restored_step,
                            )
                            _status_path.parent.mkdir(
                                parents=True, exist_ok=True,
                            )
                            _atomic_write(_status_path, _status_body)
                        except (OSError, ValueError):
                            log.warning(
                                "Could not re-render status.md after "
                                "ORIENT-exit restoration for %r",
                                milestone, exc_info=True,
                            )
                        log.info(
                            "M36: ORIENT-exit restoration for %r "
                            "(next_step restored to %r)",
                            milestone, _restored_step,
                        )
                    except (OSError, ValueError):
                        log.warning(
                            "Could not restore next_step after ORIENT "
                            "at %s",
                            checkpoint_path, exc_info=True,
                        )

            # M36 I1 (Edit B): first-iteration ORIENT dispatch.
            # On the first iteration of every ``run_coordinator``
            # invocation, rewrite the checkpoint's ``next_step`` to
            # ``ORIENT`` unless it's already ``ORIENT``. Preserve the
            # prior ``next_step`` in the typed ``pre_orient_next_step``
            # field (F2 rework) so the ORIENT-exit restoration path
            # (F1 rework) can restore dispatch on the next iteration
            # AND the disagreement-telemetry layer can compare against
            # the judgment's ``next_action``.
            if first_iteration:
                if checkpoint_path.exists():
                    _cp_session = parse_checkpoint(
                        checkpoint_path.read_text()
                    )
                    # M49b D3: do NOT stash HALTED into
                    # pre_orient_next_step — would loop via ORIENT-exit
                    # restoration.  If we reach here with HALTED, the
                    # halt gate above already fell through (escalation
                    # is resolved or absent), which means the supervisor
                    # disposed without rewriting next_step (B6 contract
                    # violation, possibly via the now-closed crash
                    # window in dispose_halt).  Skip the ORIENT
                    # interposition; determine_next_cycle (M49b C1)
                    # will raise on next_step=HALTED with a clear
                    # contract-violation message.
                    #
                    # M52 follow-up: also skip COMPLETE.  A milestone
                    # whose checkpoint says ``next_step=COMPLETE`` is
                    # terminal — dispatching ORIENT before the COMPLETE
                    # branch fires would burn a full cycle of API tokens
                    # to re-judge an already-shipped milestone.  The
                    # rare case where a user wants to re-evaluate a
                    # COMPLETE checkpoint is covered by manually
                    # rewriting the checkpoint to a non-terminal step;
                    # the default path is direct exit.
                    if (
                        _cp_session.next_step != "ORIENT"
                        and _cp_session.next_step != "HALTED"
                        and _cp_session.next_step != "COMPLETE"
                    ):
                        _pre_orient_step = _cp_session.next_step
                        _rewritten = render_checkpoint(
                            cycle=_cp_session.cycle,
                            step=_cp_session.step,
                            next_step="ORIENT",
                            current_phase=_cp_session.current_phase,
                            phases_completed=_cp_session.phases_completed,
                            phases_total=_cp_session.phases_total,
                            validation_retries=_cp_session.validation_retries,
                            readiness_retries=_cp_session.readiness_retries,
                            crash_retries=_cp_session.crash_retries,
                            staleness_count=_cp_session.staleness_count,
                            cycle_outcome=_cp_session.cycle_outcome,
                            valid_findings=_cp_session.valid_findings,
                            consecutive_zero_valid=(
                                _cp_session.consecutive_zero_valid
                            ),
                            pre_orient_next_step=_pre_orient_step,
                            # M49b B6: preserve halt-restoration stash
                            # across session-start ORIENT dispatch.
                            pre_halt_next_step=_cp_session.pre_halt_next_step,
                            # M52 F38: same wipe-class — session-start
                            # rewrite is orthogonal to the gate; inherit.
                            last_acceptance_verdict=(
                                _cp_session.last_acceptance_verdict
                            ),
                        )
                        try:
                            checkpoint_path.parent.mkdir(
                                parents=True, exist_ok=True,
                            )
                            # M36 F29 (round-4): atomic write.
                            _atomic_write(checkpoint_path, _rewritten)
                            log.info(
                                "M36: session-start ORIENT dispatch for %r "
                                "(preserved pre_orient_next_step=%r)",
                                milestone, _pre_orient_step,
                            )
                        except OSError:
                            log.warning(
                                "Could not rewrite checkpoint for ORIENT "
                                "dispatch at %s",
                                checkpoint_path, exc_info=True,
                            )
                # On a fresh milestone (no checkpoint yet), the PLAN
                # branch of determine_next_cycle runs as the first
                # cycle — but ORIENT should still be the first thing
                # the coordinator does. Seed a minimal ORIENT-pointed
                # checkpoint so the PLAN fallback is skipped and the
                # ORIENT branch fires instead. pre_orient_next_step
                # here is ``PLAN`` because that's what dispatch WOULD
                # have chosen without ORIENT.
                #
                # M36 F17 (round-4): the seed sequence is TOCTOU-safe —
                # (a) status.md is written BEFORE the checkpoint so
                # readiness cannot observe "next_step=ORIENT with
                # status.md absent" between the two writes; (b) the
                # entire sequence is wrapped in one try/except so a
                # partial seed is reverted (best effort) rather than
                # left half-done; (c) phase_progress is included when
                # compose.py exists at seed time so the status.md
                # structure is complete rather than a placeholder.
                else:
                    try:
                        # Build status.md body first — needs no disk
                        # state beyond compose.py being readable.
                        from clou.golden_context import (
                            _extract_phase_names,
                            render_status,
                        )
                        _ms_dir_fresh = clou_dir / "milestones" / milestone
                        _phase_names_seed = _extract_phase_names(
                            _ms_dir_fresh,
                        )
                        _phase_progress_seed: dict[str, str] | None = None
                        if _phase_names_seed:
                            _phase_progress_seed = {
                                name: "pending"
                                for name in _phase_names_seed
                            }
                        _status_body = render_status(
                            milestone=milestone,
                            phase="",
                            cycle=0,
                            next_step="ORIENT",
                            phase_progress=_phase_progress_seed,
                        )
                        # Build checkpoint body second — still in memory,
                        # so any ValueError from render_checkpoint (e.g.
                        # bad step vocabulary) aborts before any write.
                        _seed_body = render_checkpoint(
                            cycle=0,
                            step="PLAN",
                            next_step="ORIENT",
                            current_phase="",
                            phases_completed=0,
                            phases_total=0,
                            pre_orient_next_step="PLAN",
                        )
                        checkpoint_path.parent.mkdir(
                            parents=True, exist_ok=True,
                        )
                        # Write status.md FIRST (F17: reverse the prior
                        # ordering to eliminate the window where the
                        # checkpoint points at ORIENT but status.md is
                        # missing).  If this fails, no checkpoint is
                        # written either — the OSError aborts the
                        # try-block before the second write.
                        _status_path = _ms_dir_fresh / "status.md"
                        if not _status_path.exists():
                            _status_path.parent.mkdir(
                                parents=True, exist_ok=True,
                            )
                            # M36 F29 (round-4): atomic write.
                            _atomic_write(_status_path, _status_body)
                        # Write checkpoint SECOND, after status.md is
                        # durably on disk.  A crash between the two
                        # writes leaves status.md present without a
                        # checkpoint — a harmless state (the next
                        # session re-runs the seed path from scratch).
                        _atomic_write(checkpoint_path, _seed_body)
                        log.info(
                            "M36: seeded ORIENT-first checkpoint for "
                            "fresh milestone %r (pre_orient_next_step=PLAN)",
                            milestone,
                        )
                    except (OSError, ValueError):
                        log.warning(
                            "Could not seed ORIENT checkpoint at %s "
                            "(rollback: best-effort cleanup of partial "
                            "state)",
                            checkpoint_path, exc_info=True,
                        )
                        # Rollback: if the checkpoint was written but
                        # the render_status/mkdir failed, remove the
                        # checkpoint so the next session restarts from
                        # scratch rather than observing a half-seeded
                        # state.  This is belt-and-braces — the
                        # ordering above already keeps partial states
                        # harmless, but an overlapping signal could
                        # still partial-write the checkpoint.
                        try:
                            if checkpoint_path.exists():
                                # Only unlink our half-complete seed;
                                # pre-existing checkpoints must not be
                                # clobbered (we took the "else" branch
                                # because .exists() was False).
                                checkpoint_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                first_iteration = False

            # Check checkpoint for rework before determine_next_cycle
            # collapses the next_step variants (DB-18).
            #
            # M36 F16 (round-4): dispatch authority clarification.
            # Pre-M36 the coordinator's dispatch authority was a pure
            # function of ``checkpoint.next_step`` and
            # ``determine_next_cycle``.  M36 introduces two pre-dispatch
            # mutations to that authority:
            #
            #   Iteration 1 (first_iteration=True): the session-start
            #   rewrite above forcibly sets ``next_step="ORIENT"`` and
            #   stashes the prior step into ``pre_orient_next_step``.
            #   Dispatch now observes ORIENT, not the step the
            #   coordinator's prior exit chose.
            #
            #   Iteration 2+ (first_iteration=False): if
            #   ``next_step == "ORIENT"`` with non-empty stash, the
            #   ORIENT-exit restoration block above reconstructs the
            #   effective step from ``pre_orient_next_step``.  Dispatch
            #   then observes the restored step.
            #
            # Net effect on M36: dispatch authority is preserved in
            # sequence (ORIENT → original step) but the single-step
            # invariant from pre-M36 is gone.  The rework-detection
            # block below reflects both data paths: it reads the typed
            # ``pre_orient_next_step`` field as the source of truth for
            # the effective step — which works for iteration 1
            # (ORIENT-pending) and iteration 2+ (ORIENT-restored) alike.
            # M37 promotes the judgment artifact to gating; until then
            # this is observational plumbing.
            _rework_requested = False
            _cp_pre = None
            if checkpoint_path.exists():
                _cp_pre = parse_checkpoint(checkpoint_path.read_text())
                # M50 I1 cycle-2 rework (F6/F8/F33/F35): delegate to
                # :func:`is_rework_requested` so the effective-step rule
                # (next_step, falling back to pre_orient_next_step when
                # ORIENT is pending or live) lives in one place.
                # Previously the precedence was re-derived inline here
                # AND in tests that wanted to certify the behaviour;
                # the extracted helper lets tests call production code
                # instead of replicating the literal fallback.
                _rework_requested = is_rework_requested(_cp_pre)

            cycle_type, read_set = determine_next_cycle(
                checkpoint_path,
                milestone,
                decisions_path=decisions_path,
            )

            # Emit rework telemetry only when checkpoint explicitly
            # requested rework, not on any ASSESS→EXECUTE transition (DB-18).
            # M50 I1 cycle-2 rework (F5/F12): the structured cycle_type
            # is preserved on the payload so downstream metrics can
            # distinguish rework from verification from plain execute.
            if _rework_requested and is_execute_family(cycle_type):
                telemetry.event(
                    "cycle.rework",
                    milestone=milestone,
                    cycle_num=read_cycle_count(checkpoint_path) + 1,
                    from_step="ASSESS",
                    to_step=cycle_type,
                    cycle_type=cycle_type,
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
                        if is_execute_family(cycle_type) and _rework_requested:
                            _gate_decision = "rework"
                        elif is_execute_family(cycle_type):
                            _gate_decision = "advance"
                        else:
                            _gate_decision = "accept"
                        telemetry.event(
                            "quality_gate.decision",
                            milestone=milestone,
                            cycle_num=_current_cycle_num,
                            gate_type="assess",
                            decision=_gate_decision,
                            cycle_type=cycle_type,
                        )
                    elif _prev_step == "VERIFY":
                        if is_execute_family(cycle_type) and _rework_requested:
                            _gate_decision = "rework"
                        elif is_execute_family(cycle_type):
                            _gate_decision = "advance"
                        else:
                            _gate_decision = "accept"
                        telemetry.event(
                            "quality_gate.decision",
                            milestone=milestone,
                            cycle_num=_current_cycle_num,
                            gate_type="verify",
                            decision=_gate_decision,
                            cycle_type=cycle_type,
                        )
                except Exception:
                    log.warning("Gate telemetry emission failed", exc_info=True)

            if cycle_type == "COMPLETE":
                # M49b C2 (closes B5/L4): pre-COMPLETE halt gate.  The
                # loop-top gate runs at the START of each iteration —
                # if a worker files ``clou_halt_trajectory`` mid-cycle
                # and the in-flight cycle then writes
                # ``next_step=COMPLETE`` (B3 hook + prompt are advisory,
                # not enforceable), the COMPLETE branch would fire
                # before the next iteration's gate could see the halt.
                # The two gates form a sandwich: a halt filed during
                # the current cycle is caught here BEFORE
                # cleanup_obsolete_files runs and the milestone is
                # marked done.
                _halt_outcome = _apply_halt_gate(
                    _halt_esc_dir,
                    checkpoint_path,
                    milestone,
                    origin="pre_complete",
                )
                if _halt_outcome is not None:
                    _ms_outcome = _halt_outcome
                    return _halt_outcome
                log.info("Milestone %r complete", milestone)
                # C2-review: TOCTOU-safe unlink.  Previously the exists()
                # check and unlink() were a race window.  Although the
                # per-milestone scope eliminates cross-coordinator
                # collision on the seen file, a cleanup path that used
                # to read "if it exists, delete it" was already a bug.
                seen_path.unlink(missing_ok=True)
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
                # F29c: announcement flushed by the outer finally hook.
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

            # P1: evidence-based rework-vs-stall classification.  If the
            # milestone's artifact snapshot changed since the last
            # staleness check, the cycle made progress (even if
            # phases_completed didn't advance — as in rework cycles).
            # This eliminates the false positive that fired on M41
            # cycle 3 EXECUTE rework (telemetry 2026-04-21).
            #
            # Attribution: the evidence belongs to the cycle that just
            # completed (prev_cycle_type / cycle_count), not the one
            # about to run.  Telemetry labels the cycle that produced
            # the evidence.
            _current_snapshot = snapshot_milestone_artifacts(
                clou_dir / "milestones" / milestone
            )
            _cycle_produced_evidence = (
                _current_snapshot != _artifact_snapshot
            )
            if _cycle_produced_evidence:
                _changed_paths = sorted(
                    set(_current_snapshot) ^ set(_artifact_snapshot)
                ) + sorted(
                    k for k in set(_current_snapshot) & set(_artifact_snapshot)
                    if _current_snapshot[k] != _artifact_snapshot[k]
                )
                try:
                    telemetry.event(
                        "staleness.evidence_reset",
                        milestone=milestone,
                        cycle_num=cycle_count,  # cycle that produced the evidence
                        cycle_type=_stale.prev_cycle_type or cycle_type,
                        changed_paths=_changed_paths,
                    )
                except Exception:
                    # Telemetry must never break the orchestrator loop
                    # (brutalist P1-review: missing try/except here
                    # would silently skip staleness update on
                    # telemetry failure).
                    log.warning(
                        "staleness.evidence_reset emission failed",
                        exc_info=True,
                    )
            _artifact_snapshot = _current_snapshot

            update_staleness(
                _stale,
                cycle_type,
                _phases_now,
                cycle_produced_evidence=_cycle_produced_evidence,
            )

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
                # F29c: announcement flushed by the outer finally hook.
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
                    # F29c: announcement flushed by the outer finally hook.
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
            if env_state is None and is_execute_family(cycle_type):
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

            # M52 F32: phase-acceptance gate.  Runs at the START of an
            # ASSESS cycle, BEFORE the LLM ASSESS prompt fires, so the
            # verdict is persisted in the checkpoint envelope and
            # available to the LLM via ``prev_cp.last_acceptance_verdict``
            # when it later calls ``clou_write_checkpoint``.  The gate
            # is fail-soft (logs and skips on missing inputs) and a
            # no-op for legacy phase.md without a typed deliverable
            # (the F41 migration shim handles those via the bootstrap
            # path in the tool-side validation).
            if cycle_type == "ASSESS" and _current_phase:
                try:
                    _run_phase_acceptance_gate(
                        project_dir=project_dir,
                        milestone=milestone,
                        phase=_current_phase,
                        checkpoint_path=checkpoint_path,
                    )
                except Exception:
                    log.warning(
                        "_run_phase_acceptance_gate raised; cycle "
                        "continues with prior verdict in place",
                        exc_info=True,
                    )

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
                dag_data=dag_data if is_execute_family(cycle_type) else None,
                working_tree_state=env_state,
                current_phase=_current_phase,
                routing_context=_routing_context,
                # M36 I1: feed the active cycle number so ORIENT's
                # write_paths resolve the judgment file with the
                # correct cycle-NN prefix.
                cycle_num=cycle_count + 1,
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

            # M36 F27 (round-4): enforce the "ORIENT is observational"
            # contract.  The coordinator prompt instructs the LLM not
            # to mutate the checkpoint during ORIENT, but that is a
            # prompt contract — unenforceable in code before now.  If
            # the LLM rewrites next_step mid-ORIENT (via
            # clou_write_checkpoint), the ORIENT-exit restoration on
            # the next iteration reads next_step != "ORIENT" and
            # skips, silently losing the pre_orient_next_step stash
            # and letting the LLM's wrong value dispatch.  Assertion:
            # if the cycle that just ran was ORIENT but the checkpoint
            # no longer says next_step=="ORIENT" post-cycle, the LLM
            # escaped the observation-only contract.  Force-restore
            # from pre_orient_next_step (if non-empty) and emit
            # telemetry distinguishing the escape from a clean exit.
            # Fires BEFORE disagreement telemetry so the stashed
            # pre_orient_next_step is still available for compare.
            if cycle_type == "ORIENT" and checkpoint_path.exists():
                try:
                    _cp_post_orient = parse_checkpoint(
                        checkpoint_path.read_text(encoding="utf-8"),
                    )
                except (OSError, ValueError):
                    _cp_post_orient = None
                if _cp_post_orient is not None:
                    _llm_escaped = (
                        _cp_post_orient.next_step != "ORIENT"
                    )
                    try:
                        telemetry.event(
                            "cycle.orient_exit",
                            milestone=milestone,
                            cycle_num=cycle_count + 1,
                            escaped_contract=_llm_escaped,
                            observed_next_step=_cp_post_orient.next_step,
                        )
                    except Exception:
                        log.debug(
                            "cycle.orient_exit emission failed",
                            exc_info=True,
                        )
                    if (
                        _llm_escaped
                        and _cp_post_orient.pre_orient_next_step
                    ):
                        log.warning(
                            "M36 F27: LLM escaped ORIENT contract for "
                            "%r — next_step was rewritten to %r "
                            "mid-cycle; force-restoring from "
                            "pre_orient_next_step=%r",
                            milestone,
                            _cp_post_orient.next_step,
                            _cp_post_orient.pre_orient_next_step,
                        )
                        _forced_step = (
                            _cp_post_orient.pre_orient_next_step
                        )
                        try:
                            _forced_body = render_checkpoint(
                                cycle=_cp_post_orient.cycle,
                                step=_cp_post_orient.step,
                                # Force back to ORIENT — the next
                                # iteration's ORIENT-exit restoration
                                # block will then perform the normal
                                # restoration path, resetting retry
                                # counters and rendering status.md.
                                next_step="ORIENT",
                                current_phase=_cp_post_orient.current_phase,
                                phases_completed=(
                                    _cp_post_orient.phases_completed
                                ),
                                phases_total=(
                                    _cp_post_orient.phases_total
                                ),
                                validation_retries=(
                                    _cp_post_orient.validation_retries
                                ),
                                readiness_retries=(
                                    _cp_post_orient.readiness_retries
                                ),
                                crash_retries=(
                                    _cp_post_orient.crash_retries
                                ),
                                staleness_count=(
                                    _cp_post_orient.staleness_count
                                ),
                                cycle_outcome=(
                                    _cp_post_orient.cycle_outcome
                                ),
                                valid_findings=(
                                    _cp_post_orient.valid_findings
                                ),
                                consecutive_zero_valid=(
                                    _cp_post_orient
                                    .consecutive_zero_valid
                                ),
                                pre_orient_next_step=_forced_step,
                                pre_halt_next_step=(
                                    _cp_post_orient.pre_halt_next_step
                                ),
                                # M52 F38: F27 force-restore is
                                # orthogonal to the gate; inherit.
                                last_acceptance_verdict=(
                                    _cp_post_orient
                                    .last_acceptance_verdict
                                ),
                            )
                            _atomic_write(checkpoint_path, _forced_body)
                        except (OSError, ValueError):
                            log.warning(
                                "M36 F27: force-restore failed; leaving "
                                "LLM-rewritten next_step in place",
                                exc_info=True,
                            )

            # M36 I3: disagreement telemetry.
            # After an ORIENT cycle completes the coordinator LLM should
            # have written a judgment file via the ``clou_write_judgment``
            # MCP tool. Parse it, compare ``judgment.next_action`` against
            # the typed ``pre_orient_next_step`` field (F2 rework) in
            # the checkpoint, and emit a ``cycle.judgment`` span event
            # so ``metrics.md`` can render the disagreement signal in
            # its own section. Dispatch is unchanged — this is purely
            # observational (M37 gates on it).
            if cycle_type == "ORIENT":
                try:
                    from clou.judgment import (
                        JUDGMENT_PATH_TEMPLATE,
                        parse_judgment,
                    )

                    _judgment_path = (
                        clou_dir / "milestones" / milestone
                        / JUDGMENT_PATH_TEMPLATE.format(cycle=cycle_count + 1)
                    )
                    if _judgment_path.exists():
                        try:
                            _judgment_text = _judgment_path.read_text(
                                encoding="utf-8",
                            )
                            _form = parse_judgment(_judgment_text)
                        except Exception:
                            log.warning(
                                "judgment parse failed for cycle %d at %s",
                                cycle_count + 1, _judgment_path, exc_info=True,
                            )
                        else:
                            # F2 rework: typed pre_orient_next_step
                            # field survives retry/escalation-reset/
                            # self-heal round-trips.  No regex scrape.
                            _pre_orient = ""
                            if checkpoint_path.exists():
                                try:
                                    _cp_tlm = parse_checkpoint(
                                        checkpoint_path.read_text(
                                            encoding="utf-8",
                                        ),
                                    )
                                    _pre_orient = (
                                        _cp_tlm.pre_orient_next_step
                                    )
                                except Exception:
                                    pass
                            # F13 rework: guard against
                            # confident-wrong telemetry when
                            # ``pre_orient_next_step`` is empty.
                            # Empty = no ORIENT stash in this
                            # checkpoint (either the session-start
                            # rewrite didn't run, or the restoration
                            # already cleared it).  Emit the event
                            # with ``agreement=None`` to signal
                            # "unknown" rather than a False that
                            # reads as genuine disagreement on
                            # dashboards.  Post-F2 typed round-trip
                            # makes this structurally rare — but
                            # defense in depth remains valuable.
                            if _pre_orient:
                                _agreement_value: bool | None = (
                                    _form.next_action == _pre_orient
                                )
                            else:
                                _agreement_value = None
                            telemetry.event(
                                "cycle.judgment",
                                milestone=milestone,
                                cycle=cycle_count + 1,
                                judgment_next_action=_form.next_action,
                                orchestrator_next_cycle=_pre_orient,
                                agreement=_agreement_value,
                            )
                except Exception:
                    # Telemetry must never break the coordinator loop.
                    log.debug(
                        "cycle.judgment emission failed", exc_info=True,
                    )

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
                    # F29c: announcement flushed by the outer finally hook.
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
                # F29c: announcement flushed by the outer finally hook.
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
                _current_keys = {
                    (w.message, w.path) for w in validation_warnings
                }
                _new_keys = _current_keys - _warnings_seen
                _warnings_seen |= _current_keys
                log.info(
                    "Validation warnings for %r (non-blocking): "
                    "%d total, %d new this cycle: %s",
                    milestone,
                    len(_current_keys),
                    len(_new_keys),
                    [w.message for w in validation_warnings],
                )
                telemetry.event(
                    "validation_warnings", milestone=milestone,
                    cycle_num=cycle_count + 1,
                    warning_count=len(_new_keys),
                    warning_total=len(_current_keys),
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
                    # Use warning_total (cumulative from this validation
                    # pass) to match the semantics of validation_warnings'
                    # warning_total field.  Never call it warning_count
                    # here — that field name means "new this cycle" in
                    # the validation_warnings event and using it with
                    # total-semantics here was a schema footgun (BR3).
                    warning_total=len(validation_warnings),
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
                    # F29c: announcement flushed by the outer finally hook.
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
                # P1: re-snapshot after revert so the next cycle's
                # evidence comparison reflects post-revert state.
                # Without this, the revert's file restorations would
                # appear as phantom evidence and mask a legitimate
                # stuck-retry loop.
                _artifact_snapshot = snapshot_milestone_artifacts(
                    clou_dir / "milestones" / milestone
                )
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
            if is_execute_family(cycle_type) and checkpoint_path.exists():
                cp = parse_checkpoint(checkpoint_path.read_text())
                if cp.current_phase:
                    try:
                        await git_commit_phase(project_dir, milestone, cp.current_phase)
                    except RuntimeError:
                        log.warning(
                            "Git commit failed for phase %r",
                            cp.current_phase,
                        )

            # P1: re-snapshot after compact_decisions + git_commit_phase
            # so the next cycle's evidence comparison is against the
            # post-housekeeping state.  Compaction rewrites decisions.md
            # (content-hash still captures semantic equality, but an
            # actual size reduction from compaction is a real change);
            # git commit touches the index but not the working tree
            # files (content-hash catches this: no change if content
            # identical).  Belt-and-suspenders: explicit re-baseline.
            _artifact_snapshot = snapshot_milestone_artifacts(
                clou_dir / "milestones" / milestone
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
            # F29c: announcement flushed at the top of the next iteration
            # (and by the outer finally hook for the very last cycle).

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

        # F29c: flush any escalations written during the final cycle
        # (or during the finally block's own cleanup paths). Done before
        # ``set_active_app(None)`` so the passive notifier still has a
        # live UI handle to post against.
        # F14f (cycle 2): same guard posture as the top-of-loop
        # invocation.  The notifier is best-effort; a filesystem
        # exception here must not prevent ``set_active_app(None)`` or
        # mask the real milestone outcome.  The mark-seen ordering is
        # handled inside the notifier (defer-until-successful-post),
        # so the outer try/except is purely a last-resort backstop.
        try:
            _announce_new_escalations()
        except Exception:
            log.warning(
                "final escalation announce failed", exc_info=True,
            )

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
    # Which template tier owns the quality gate (typically "brutalist").
    # Used to detect crashes that cause _qg_seen to stay empty — we need
    # to distinguish "agent crashed before invoking its tool" from
    # "coordinator suppressed the gate" in the final status.
    _qg_tier: str | None = None
    # Set True when any agent whose tier matches _qg_tier ends in a
    # non-"completed" state (failed, timeout, budget_exceeded, etc.).
    _qg_tier_crashed: bool = False
    if cycle_type == "ASSESS" and template is not None:
        for gate in template.quality_gates:
            agent_name = gate.assess_agent
            agent_spec = template.agents.get(agent_name)
            if agent_spec is not None:
                _qg_expected |= {
                    t for t in agent_spec.tools
                    if t.startswith("mcp__")
                }
                # First gate's tier wins; in practice harnesses declare one
                # assess_agent per gate so the "first" is effectively the
                # only one.
                if _qg_tier is None:
                    _qg_tier = agent_spec.tier

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

    # Agent tier tracking.
    # Runtime agent descriptions are task-specific strings ("Brutalist quality
    # gate assessment", "Classify findings", "Implement phase X") generated by
    # the coordinator LLM, not verbatim template descriptions.  We infer tier
    # from keywords present in the runtime description.  The template's
    # per-agent tier set is the authoritative vocabulary; unknown descriptions
    # fall back to "worker" (the generic implementer tier) rather than
    # "unknown", which was a pure telemetry dead-letter.
    _task_id_to_tier: dict[str, str] = {}
    _tier_vocabulary: set[str] = set()
    if template is not None:
        _tier_vocabulary = {spec.tier for spec in template.agents.values()}

    # Per-task retry counts for transient error classification (R4).
    # Tracks how many times each task has been retried within this cycle.
    # Reset per-cycle (does not survive across cycles).
    _task_retry_counts: dict[str, int] = {}
    # Whether this cycle had any terminal task failures (drives crash_retries).
    _had_terminal_failure: bool = False
    # Rate-limit HOLD coalescing: if multiple workers fail with rate-limit
    # in the same message batch, sleep once -- not N times serially.
    # Unix timestamp (time.time()) when the current hold is expected to end,
    # or None if no hold is active.
    _hold_active_until: float | None = None

    # Ring buffer of recent coordinator messages for timeout classification.
    # Keeps the last 3 messages so classify_timeout() can inspect what the
    # agent was doing when the idle watchdog fired.
    _recent_messages: deque[object] = deque(maxlen=3)

    # Idle-watchdog ceiling.  Only fires when the coordinator is genuinely
    # idle (see reschedule logic below).  When a DAG author has opted in by
    # declaring ``@resource_bounds(timeout_seconds=...)``, honour the largest
    # declared value (their explicit intent).  Otherwise, fall back to a very
    # generous ceiling so user gates, slow MCP tool calls, and long-thinking
    # LLM turns never trip it.
    _declared_timeouts = [
        t for t in (
            b.get("timeout_seconds") for b in _dag_resource_bounds.values()
        ) if t is not None
    ]
    if _declared_timeouts:
        _effective_timeout: float = float(max(_declared_timeouts))
    else:
        _effective_timeout = float(_IDLE_WATCHDOG_CEILING_SECONDS)

    # Stale shard cleanup: ONLY at EXECUTE start, ONLY for the active layer.
    #
    # Scoping rationale:
    #   * cycle_type == "EXECUTE" — ASSESS/VERIFY/REPLAN read coordinator-
    #     generated failure shards (execution-{slug}.md from timeouts/budget
    #     aborts) as evidence via :mod:`clou.recovery_checkpoint` and
    #     :mod:`clou.precompose`.  Cleaning before those cycles would erase
    #     the evidence mid-review.  Cleanup belongs at EXECUTE start,
    #     immediately before new workers dispatch — by then, anything
    #     left over from a prior cycle is genuinely stale.
    #   * Layer scope, not DAG scope — sweeping every phase in the DAG
    #     on every EXECUTE would erase legitimate failure shards from
    #     already-completed upstream phases.  The sweep is bounded to
    #     the co-layer of ``checkpoint.current_phase`` (or the entry
    #     layer on first dispatch), which matches the set of workers
    #     about to run.
    _sanitized_phases: list[str] = []
    if is_execute_family(cycle_type) and _dag_task_names:
        try:
            from clou.graph import compute_layers as _cl_compute_layers

            _layers = _cl_compute_layers(
                [{"name": n, "status": "pending"} for n in _dag_task_names],
                _dag_deps,
            )
        except Exception:
            _layers = []
            log.debug(
                "Layer computation failed; skipping stale shard cleanup",
                exc_info=True,
            )

        # Determine the active phase from the checkpoint if present.
        _active_phase: str | None = None
        _cp_path_pre = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        if _cp_path_pre.exists():
            try:
                _active_phase = parse_checkpoint(
                    _cp_path_pre.read_text(encoding="utf-8"),
                ).current_phase or None
            except Exception:
                _active_phase = None

        # Pick the layer containing the active phase; fall back to
        # the entry layer when there's no checkpoint (fresh start).
        _active_layer: list[str] = []
        if _layers:
            if _active_phase:
                _active_phase_lc = _active_phase.lower()
                for _layer in _layers:
                    if any(n.lower() == _active_phase_lc for n in _layer):
                        _active_layer = list(_layer)
                        break
            if not _active_layer:
                _active_layer = list(_layers[0])

        # Sanitize: lowercase + _PHASE_RE validate each candidate phase.
        for _name in _active_layer:
            _normalized = _name.lower()
            if _PHASE_RE.match(_normalized):
                _sanitized_phases.append(_normalized)
            else:
                log.warning(
                    "Stale shard cleanup skipped for invalid phase %r",
                    _name,
                )

        if _sanitized_phases:
            try:
                ms_dir_pre = clou_dir / "milestones" / milestone
                removed, cleanup_failures = clean_stale_shards_for_layer(
                    ms_dir_pre, _sanitized_phases,
                )
            except Exception:
                # Catastrophic failure in the helper itself (e.g. invalid
                # milestone_dir).  Surface as a warning with telemetry —
                # silent cleanup failure was the root cause of the
                # slug-drift incident; treat every opacity as a bug.
                log.warning(
                    "Stale shard cleanup raised before sweeping",
                    exc_info=True,
                )
                telemetry.event(
                    "shard.cleanup_error",
                    milestone=milestone,
                    scope="helper_raised",
                )
            else:
                total_removed = sum(len(paths) for paths in removed.values())
                if total_removed:
                    log.info(
                        "Cleaned %d stale shard(s) across %d phase(s) "
                        "before dispatch: %s",
                        total_removed,
                        len(removed),
                        {
                            phase: [str(p) for p in paths]
                            for phase, paths in removed.items()
                        },
                    )
                    telemetry.event(
                        "shard.cleaned_stale",
                        milestone=milestone,
                        phase_count=len(removed),
                        total_removed=total_removed,
                    )
                if cleanup_failures:
                    # Per-file failures are NOT fatal — dispatch
                    # continues — but they MUST be visible so a
                    # filesystem permission issue or lingering symlink
                    # doesn't silently keep orphans alive.
                    failure_summary = {
                        phase: [
                            {"path": str(p), "error": str(exc)}
                            for p, exc in fails
                        ]
                        for phase, fails in cleanup_failures.items()
                    }
                    total_failed = sum(
                        len(fails) for fails in cleanup_failures.values()
                    )
                    log.warning(
                        "Stale shard cleanup: %d shard(s) could not be "
                        "removed across %d phase(s): %s",
                        total_failed,
                        len(cleanup_failures),
                        failure_summary,
                    )
                    telemetry.event(
                        "shard.cleanup_failed",
                        milestone=milestone,
                        phase_count=len(cleanup_failures),
                        total_failed=total_failed,
                    )

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

                        # Typed RateLimitEvent -> telemetry for observability.
                        # The SDK handles actual dispatch hold internally; we
                        # emit an event so consumers can correlate long gaps
                        # in agent activity with known rate-limit windows.
                        # (Without this, rate-limit periods appear as silent
                        # telemetry gaps indistinguishable from crashes.)
                        if hasattr(msg, "rate_limit_info"):
                            _rli = msg.rate_limit_info
                            telemetry.event(
                                "rate_limit.event",
                                milestone=milestone,
                                cycle_num=cycle_num,
                                status=getattr(_rli, "status", ""),
                                resets_at=getattr(_rli, "resets_at", None),
                                tier="coordinator",
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
                            _tier = infer_agent_tier(
                                msg.description,
                                _tier_vocabulary,
                                task_type=getattr(msg, "task_type", None),
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
                                # Only enforced when the DAG author explicitly
                                # declared ``@resource_bounds(timeout_seconds=...)``.
                                # No implicit default — long-running agents
                                # (test runs, brutalist panels) must be allowed
                                # to finish unless the author opted in.
                                if _violation_type is None:
                                    import time as _time_mod
                                    start = _task_start_time.get(msg.task_id)
                                    if start is not None:
                                        bounds = _dag_resource_bounds.get(
                                            task_name, {},
                                        )
                                        limit = bounds.get("timeout_seconds")
                                        if limit is not None:
                                            elapsed = _time_mod.monotonic() - start
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
                                    # Synthetic agent.end: the SDK does not
                                    # fire TaskNotificationMessage for tasks
                                    # killed via stop_task, so emit here to
                                    # keep telemetry complete (otherwise the
                                    # span log orphans this agent.start).
                                    _aborted_tier = _task_id_to_tier.get(
                                        msg.task_id, "worker",
                                    )
                                    telemetry.event(
                                        "agent.end",
                                        milestone=milestone,
                                        cycle_num=cycle_num,
                                        task_id=msg.task_id,
                                        status=_violation_type,
                                        total_tokens=_task_tokens.get(
                                            task_name, 0,
                                        ),
                                        tool_uses=0,
                                        tier=_aborted_tier,
                                    )
                                    if (
                                        _qg_tier is not None
                                        and _aborted_tier == _qg_tier
                                    ):
                                        _qg_tier_crashed = True
                                    _active_task_ids.discard(msg.task_id)
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
                            # Skip if we already emitted a synthetic agent.end
                            # for this task when we called stop_task — the SDK
                            # may still deliver a TaskNotificationMessage
                            # (status="cancelled"/"failed") for the killed
                            # task, and we don't want duplicate agent.end
                            # records in the span log.
                            if msg.task_id in _aborted_task_ids:
                                _active_task_ids.discard(msg.task_id)
                            else:
                                _au = getattr(msg, "usage", {}) or {}
                                _completed_tier = _task_id_to_tier.get(
                                    msg.task_id, "worker",
                                )
                                # The SDK's TaskUsage TypedDict provides
                                # total_tokens, tool_uses, duration_ms — no
                                # input/output split.  Only emit what the SDK
                                # actually gives us; do not fabricate zeros
                                # for absent fields.
                                telemetry.event(
                                    "agent.end",
                                    milestone=milestone,
                                    cycle_num=cycle_num,
                                    task_id=msg.task_id,
                                    status=msg.status,
                                    total_tokens=_au.get("total_tokens", 0),
                                    tool_uses=_au.get("tool_uses", 0),
                                    duration_ms=_au.get("duration_ms", 0),
                                    tier=_completed_tier,
                                )
                                if (
                                    msg.status != "completed"
                                    and _qg_tier is not None
                                    and _completed_tier == _qg_tier
                                ):
                                    _qg_tier_crashed = True
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
                        if (
                            isinstance(msg, TaskNotificationMessage)
                            and msg.status == "failed"
                            and msg.task_id not in _aborted_task_ids
                        ):
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
                                _err_kind == ErrorKind.HOLD
                                and failed_task_name
                            ):
                                # Rate-limit / quota HOLD: wait for reset;
                                # do NOT increment retry count (retries
                                # burn the same capacity that's limited);
                                # do NOT escalate (zero-escalations
                                # principle for scheduled-capacity).
                                #
                                # Coalesce simultaneous holds: if a hold
                                # is already active (set by a sibling task
                                # that just failed with the same signal),
                                # don't stack another full sleep.  Use
                                # nonlocal-bound _hold_active_until from
                                # the enclosing cycle scope.
                                _now = time.time()
                                if (
                                    _hold_active_until is not None
                                    and _now < _hold_active_until
                                ):
                                    # Hold already active -- siblings
                                    # coalesce, no additional sleep.
                                    telemetry.event(
                                        "rate_limit.hold_coalesced",
                                        milestone=milestone,
                                        cycle_num=cycle_num,
                                        task_name=failed_task_name,
                                        remaining_s=(
                                            _hold_active_until - _now
                                        ),
                                        reason=_err_reason,
                                    )
                                    continue

                                # resets_at is not available at this
                                # callsite (the typed SDK RateLimitEvent
                                # is routed separately in bridge.py);
                                # fall back to DEFAULT_HOLD_COOLDOWN.
                                _hold_wait = compute_hold_wait(
                                    resets_at=None,
                                    now=_now,
                                )
                                _hold_active_until = _now + _hold_wait
                                telemetry.event(
                                    "rate_limit.hold_started",
                                    milestone=milestone,
                                    cycle_num=cycle_num,
                                    task_name=failed_task_name,
                                    wait_s=_hold_wait,
                                    reason=_err_reason,
                                )
                                log.warning(
                                    "Rate-limit HOLD for task %r in %r, "
                                    "waiting %.1fs (not counted against "
                                    "retry budget): %s",
                                    failed_task_name,
                                    milestone,
                                    _hold_wait,
                                    _err_reason,
                                )
                                await asyncio.sleep(_hold_wait)
                                telemetry.event(
                                    "rate_limit.hold_ended",
                                    milestone=milestone,
                                    cycle_num=cycle_num,
                                    task_name=failed_task_name,
                                    waited_s=_hold_wait,
                                )
                                _hold_active_until = None
                                # Continue without touching retry count;
                                # cycle returns "interrupted" so outer
                                # loop retries without incrementing
                                # crash_retries.
                                continue

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
                                    # Synthetic agent.end — see the per-task
                                    # enforcement block for rationale.  The
                                    # dependent is being killed because its
                                    # upstream sibling failed; record it so
                                    # telemetry reflects the abort instead of
                                    # orphaning the agent.start.
                                    _dep_tier = _task_id_to_tier.get(
                                        abort_tid, "worker",
                                    )
                                    telemetry.event(
                                        "agent.end",
                                        milestone=milestone,
                                        cycle_num=cycle_num,
                                        task_id=abort_tid,
                                        status="aborted_dependent",
                                        total_tokens=_task_tokens.get(
                                            abort_name, 0,
                                        ),
                                        tool_uses=0,
                                        tier=_dep_tier,
                                    )
                                    if (
                                        _qg_tier is not None
                                        and _dep_tier == _qg_tier
                                    ):
                                        _qg_tier_crashed = True
                                    _active_task_ids.discard(abort_tid)

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
                        # Lowercase-normalize and validate against
                        # _PHASE_RE before _write_failure_shard
                        # (same pattern as the budget-abort path at
                        # line 1987-1998 — keeps the two failure-shard
                        # sites symmetric on phase-name discipline).
                        _shard_phase = _shard_phase.lower()
                        if not _PHASE_RE.match(_shard_phase):
                            log.warning(
                                "Failure shard skipped (idle watchdog): "
                                "invalid phase %r",
                                _shard_phase,
                            )
                        else:
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

        # Convergence-aware gate status (DB-18 + DB-22).
        # When _qg_seen is empty at end of ASSESS, disambiguate causes:
        #   - unavailable_crashed: gate-tier agent died before invoking
        #     its tool (F11 — brutalist crash).
        #   - converged:  structured czv >= threshold AND coordinator
        #                 wrote a ``### Convergence:`` entry in decisions.md
        #                 for this cycle (F10 — drift-proofed).
        #   - converging: czv >= threshold-1 AND entry present (F6 — near
        #                 convergence, legitimate soft suppression).
        #   - drift_skipped: coordinator suppressed without the entry —
        #                    the prompt-drift failure we're calling out.
        #                    Covers czv>=2 AND czv in {0,1} without decl.
        #   - degraded:   signal missing (checkpoint unreadable) or MCP
        #                 genuinely unavailable.
        #
        # _consec_zv uses -1 as a sentinel for "couldn't read checkpoint"
        # — kept distinct from 0 (real zero) so we can classify parse
        # failures as degraded rather than drift.
        _consec_zv = -1
        # Pre-existing path bug (inherited by F6/F10/F11): the prior
        # code read ``checkpoint.md`` at the milestone root, which never
        # exists.  The real checkpoint lives at ``active/coordinator.md``
        # (same path used everywhere else in this file — cf. line 521,
        # 2081, 2390).  Historical telemetry consequence: every
        # suppressed-gate cycle fell through to ``degraded`` because
        # ``_converged`` could never flip.
        _ckpt_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        if (
            cycle_type == "ASSESS"
            and _qg_expected
            and not _qg_seen
            and _ckpt_path.exists()
        ):
            try:
                _ckpt = parse_checkpoint(
                    _ckpt_path.read_text(encoding="utf-8"),
                )
                _consec_zv = _ckpt.consecutive_zero_valid
            except Exception:
                # Log rather than silently fall through: a corrupt or
                # schema-drifted checkpoint would otherwise make every
                # cycle look like drift with no way to diagnose.
                log.warning(
                    "Checkpoint parse failed for %r cycle %d; gate "
                    "status will be 'degraded' (unable to determine "
                    "convergence state)",
                    milestone, cycle_num, exc_info=True,
                )

        _has_convergence_decl = False
        if cycle_type == "ASSESS" and _qg_expected and not _qg_seen:
            _dec_path = (
                clou_dir / "milestones" / milestone / "decisions.md"
            )
            if _dec_path.exists():
                try:
                    from clou.recovery_compaction import (
                        _extract_cycle_section,
                    )
                    _dec_text = _dec_path.read_text(encoding="utf-8")
                    _section = _extract_cycle_section(_dec_text, cycle_num)
                    if _section and "### Convergence:" in _section:
                        _has_convergence_decl = True
                except Exception:
                    log.warning(
                        "Convergence-declaration check failed for %r "
                        "cycle %d; treating as no declaration",
                        milestone, cycle_num, exc_info=True,
                    )

        # Emit quality gate telemetry after ASSESS cycle completes.
        if _qg_expected:
            if _qg_seen == _qg_expected:
                _qg_status = "full"
            elif _qg_seen:
                _qg_status = "partial"
            elif _qg_tier_crashed:
                _qg_status = "unavailable_crashed"
            elif _consec_zv < 0:
                # Checkpoint unreadable — we can't distinguish
                # drift from convergence.  Degraded is the honest
                # label.
                _qg_status = "degraded"
            elif _has_convergence_decl and _consec_zv >= 2:
                _qg_status = "converged"
            elif _has_convergence_decl and _consec_zv >= 1:
                _qg_status = "converging"
            elif not _has_convergence_decl:
                # Any czv >= 0 without the explicit declaration
                # is drift.  czv==1 without declaration (premature
                # suppression after one clean round) is exactly
                # the pattern we want surfaced.
                _qg_status = "drift_skipped"
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
                    if _qg_status not in ("converged", "converging") else []
                ),
                tools_suppressed=(
                    sorted(_qg_expected - _qg_seen)
                    if _qg_status in ("converged", "converging") else []
                ),
                tools_invoked_count=len(_qg_seen),
            )
            if _qg_status in ("degraded", "drift_skipped"):
                log.warning(
                    "Quality gate %s for %r cycle %d: "
                    "no expected tools invoked (expected: %s, "
                    "consec_zero_valid=%d, convergence_decl=%s)",
                    _qg_status,
                    milestone, cycle_num, sorted(_qg_expected),
                    _consec_zv, _has_convergence_decl,
                )
                telemetry.event(
                    "quality_gate.degraded",
                    milestone=milestone,
                    cycle_num=cycle_num,
                    status=_qg_status,
                    expected_tools=sorted(_qg_expected),
                    consecutive_zero_valid=_consec_zv,
                    has_convergence_decl=_has_convergence_decl,
                )
            elif _qg_status == "unavailable_crashed":
                log.warning(
                    "Quality gate agent (tier=%r) crashed in %r cycle %d "
                    "before invoking its tool",
                    _qg_tier, milestone, cycle_num,
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
