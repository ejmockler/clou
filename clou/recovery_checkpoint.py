"""Checkpoint parsing and cycle determination.

Handles Checkpoint dataclass, ConvergenceState, parse_checkpoint(),
determine_next_cycle(), assess_convergence(), read_cycle_count(),
read_cycle_outcome(), and milestone name validation.

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_log = logging.getLogger(__name__)

#: Cycle-type to memory-pattern-type mapping for filtered retrieval (DB-18 I6).
#: Cycle types not listed here receive no memory.md content.
_MEMORY_TYPE_FILTERS: dict[str, list[str]] = {
    "PLAN": ["decomposition", "cost-calibration", "debt"],
    "ASSESS": ["quality-gate", "escalation"],
}

#: Milestone names must be lowercase alphanumeric with hyphens.
_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Phase names must be lowercase alphanumeric with hyphens or underscores.
#: Whitelist prevents path traversal via .., /, \, null bytes, etc. (F9).
_PHASE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_milestone_name(name: str) -> None:
    """Raise ValueError if *name* is not a valid milestone slug."""
    if not _MILESTONE_RE.match(name):
        msg = f"Invalid milestone name: {name!r} (must match [a-z0-9][a-z0-9-]*)"
        raise ValueError(msg)


# Keep the old private name for internal callers.
_validate_milestone = validate_milestone_name


class CycleOutcome(str, Enum):
    """Typed cycle outcome for checkpoint staleness tracking.

    ADVANCED: phase advancement or successful cycle completion.
    INCONCLUSIVE: cycle could not complete (tools unavailable, etc).
    INTERRUPTED: timeout/sleep/network interruption.
    FAILED: genuine crash or persistent error.
    """

    ADVANCED = "ADVANCED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"


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
        "REPLAN",
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
    # Retry counters -- persisted so they survive process restarts.
    validation_retries: int = 0
    readiness_retries: int = 0
    crash_retries: int = 0
    staleness_count: int = 0
    # Typed cycle outcome -- drives staleness classification.
    cycle_outcome: str = "ADVANCED"
    # ASSESS convergence tracking (structured, not markdown-parsed).
    # -1 = not an ASSESS cycle; 0+ = count of valid+security findings.
    valid_findings: int = -1
    # Consecutive ASSESS cycles with zero valid findings.
    # Coordinator increments when valid_findings==0, resets when >0.
    consecutive_zero_valid: int = 0


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
    Validation errors do NOT prevent parsing -- the orchestrator's
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
        _log.warning("Unknown next_step %r --- defaulting to PLAN", next_step)
        next_step = "PLAN"

    # Backward-compatible: missing cycle_outcome defaults to "ADVANCED"
    # (existing checkpoints without the field were successful cycles).
    raw_outcome = fields.get("cycle_outcome", "ADVANCED")
    try:
        cycle_outcome = CycleOutcome(raw_outcome).value
    except ValueError:
        _log.warning("Unknown cycle_outcome %r --- defaulting to ADVANCED", raw_outcome)
        cycle_outcome = CycleOutcome.ADVANCED.value

    # valid_findings: -1 means "not an ASSESS cycle" (or missing field).
    # Can't use _safe_int here — it clamps negatives to 0 but -1 is a
    # valid sentinel meaning "not an ASSESS cycle".
    try:
        valid_findings = int(fields.get("valid_findings", "-1"))
    except (ValueError, TypeError):
        valid_findings = -1

    return Checkpoint(
        cycle=_safe_int(fields.get("cycle", "0")),
        step=fields.get("step", "PLAN"),
        next_step=next_step,
        current_phase=fields.get("current_phase", fields.get("phase", "")),
        phases_completed=_safe_int(fields.get("phases_completed", "0")),
        phases_total=_safe_int(fields.get("phases_total", "0")),
        validation_retries=_safe_int(fields.get("validation_retries", "0")),
        readiness_retries=_safe_int(fields.get("readiness_retries", "0")),
        crash_retries=_safe_int(fields.get("crash_retries", "0")),
        staleness_count=_safe_int(fields.get("staleness_count", "0")),
        cycle_outcome=cycle_outcome,
        valid_findings=valid_findings,
        consecutive_zero_valid=_safe_int(
            fields.get("consecutive_zero_valid", "0"),
        ),
    )


def assess_convergence(
    checkpoint: Checkpoint | None = None,
    *,
    decisions_content: str | None = None,
    threshold: int = _CONVERGENCE_THRESHOLD,
) -> ConvergenceState:
    """Check whether ASSESS cycles have converged (zero valid findings).

    **Primary path** (structured): reads ``consecutive_zero_valid`` from the
    checkpoint.  No markdown parsing — immune to LLM formatting variance.

    **Fallback path** (legacy): if the checkpoint lacks convergence data
    (pre-existing milestones), falls back to scanning decisions.md for
    ``### Valid:`` / ``### Security:`` headings (the actionable classifications).

    Convergence is reached when consecutive zero-valid-finding ASSESS
    cycles meet or exceed *threshold*.
    """
    # --- Primary: structured checkpoint path ---
    # Use structured path only when the checkpoint has convergence data
    # (valid_findings >= 0 means this checkpoint was written by an ASSESS
    # cycle that included the structured convergence fields).
    if checkpoint is not None and checkpoint.valid_findings >= 0:
        return ConvergenceState(
            consecutive_zero_accepts=checkpoint.consecutive_zero_valid,
            total_assess_cycles=-1,  # Not tracked in checkpoint.
            converged=checkpoint.consecutive_zero_valid >= threshold,
        )

    # --- Fallback: parse decisions.md (backward compat) ---
    if decisions_content is None:
        return ConvergenceState(
            consecutive_zero_accepts=0,
            total_assess_cycles=0,
            converged=False,
        )

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
        # Match actionable classifications: Valid and Security create rework.
        if re.search(r"(?m)^### (Valid|Security):", block):
            break
        consecutive += 1

    return ConvergenceState(
        consecutive_zero_accepts=consecutive,
        total_assess_cycles=len(assess_blocks),
        converged=consecutive >= threshold,
    )


def _filter_memory_for_cycle(
    memory_path: Path,
    cycle_type: str,
    milestones_dir: Path,
    *,
    milestone: str = "",
    cycle_num: int = 0,
) -> str | None:
    """Return filtered memory.md content for *cycle_type*, or None.

    Only includes active patterns whose type is listed in
    ``_MEMORY_TYPE_FILTERS[cycle_type]``.  Patterns with status
    ``fading`` or ``archived`` are excluded.  Returns ``None`` when
    *cycle_type* has no filter entry or when no patterns match.

    When *milestone* is provided, emits a ``memory.patterns_retrieved``
    telemetry event listing the patterns that passed filtering (M35 I1).
    """
    allowed_types = _MEMORY_TYPE_FILTERS.get(cycle_type)
    if allowed_types is None:
        return None

    if not memory_path.exists():
        return None

    # Lazy import to avoid circular dependency
    # (recovery_compaction imports _safe_int from this module).
    from clou.recovery_compaction import (
        _parse_memory,
        _render_memory,
        retrieve_patterns,
    )

    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError:
        return None

    patterns = _parse_memory(content)
    if not patterns:
        return None

    # Gather milestone names for recency scoring.
    all_milestones: list[str] = []
    if milestones_dir.is_dir():
        all_milestones = sorted(
            d.name for d in milestones_dir.iterdir() if d.is_dir()
        )

    # Collect patterns across all allowed types, excluding fading.
    matched: list["MemoryPattern"] = []  # type: ignore[name-defined]
    for type_name in allowed_types:
        retrieved = retrieve_patterns(
            patterns,
            type_filter=type_name,
            all_milestones=all_milestones or None,
        )
        # retrieve_patterns excludes archived/invalidated but allows
        # fading -- we must additionally exclude fading per DB-18 I6.
        matched.extend(p for p in retrieved if p.status == "active")

    if not matched:
        return None

    # M35 I1: emit telemetry event listing retrieved patterns.
    # Only emit when milestone is non-empty to avoid orphaned events (F2).
    if milestone:
        from clou import telemetry

        telemetry.event(
            "memory.patterns_retrieved",
            milestone=milestone,
            cycle_num=cycle_num,
            cycle_type=cycle_type,
            pattern_count=len(matched),
            patterns=[
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                }
                for p in matched
            ],
        )

    return _render_memory(matched)


def score_read_set(
    base_set: list[str],
    telemetry_path: Path | None = None,
    milestone: str | None = None,
    cycle_type: str | None = None,
) -> list[str]:
    """Score and optionally prune a read set based on historical reference density.

    DB-20 Step 3 infrastructure. Currently a pass-through — scoring logic
    will be added once telemetry data accumulates across milestones.
    """
    if telemetry_path is not None and telemetry_path.exists():
        _log.debug(
            "score_read_set: telemetry exists (%s, cycle=%s, %d files) — pass-through",
            milestone, cycle_type, len(base_set),
        )
    return list(base_set)


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
    # checkpoint_path: .clou/milestones/{ms}/active/coordinator.md
    # active/ -> ms/ -> milestones/ -> .clou/
    _clou_dir = checkpoint_path.parent.parent.parent.parent
    _milestones_dir = _clou_dir / "milestones"
    _memory_path = _clou_dir / "memory.md"
    _filtered_path = checkpoint_path.parent / "_filtered_memory.md"

    _plan_set = ["milestone.md", "intents.md", "requirements.md", "project.md"]

    # DB-18 I6: filtered memory retrieval replaces raw memory.md inclusion.
    # Write filtered content to active/_filtered_memory.md for the prompt
    # builder to read as a milestone-relative path.
    # Boundary directory for symlink / resolved-path checks (F7).
    _milestone_dir = checkpoint_path.parent.parent

    def _maybe_add_filtered_memory(
        read_set: list[str], cycle_type: str,
        *, _cycle_num: int = 1,
    ) -> None:
        filtered = _filter_memory_for_cycle(
            _memory_path, cycle_type, _milestones_dir,
            milestone=milestone,
            cycle_num=_cycle_num,
        )
        if filtered is not None:
            # F7: Symlink and boundary validation before write (matches
            # coordinator.py assess_summary.md guard).
            if _filtered_path.is_symlink():
                _log.warning(
                    "Refusing to write symlink: %s",
                    _filtered_path,
                )
                return
            if _filtered_path.exists() and not str(
                _filtered_path.resolve()
            ).startswith(str(_milestone_dir.resolve()) + "/"):
                _log.warning(
                    "_filtered_memory.md resolved outside milestone "
                    "boundary: %s",
                    _filtered_path.resolve(),
                )
                return
            _filtered_path.parent.mkdir(parents=True, exist_ok=True)
            _filtered_path.write_text(filtered, encoding="utf-8")
            read_set.append("active/_filtered_memory.md")
        else:
            # Remove stale file from a previous cycle type to prevent
            # leaking PLAN-cycle memory into an EXECUTE cycle (F1).
            try:
                # F7: Symlink and boundary validation before unlink.
                if _filtered_path.is_symlink():
                    _log.warning(
                        "Refusing to unlink symlink: %s",
                        _filtered_path,
                    )
                elif _filtered_path.exists() and not str(
                    _filtered_path.resolve()
                ).startswith(str(_milestone_dir.resolve()) + "/"):
                    _log.warning(
                        "_filtered_memory.md resolved outside milestone "
                        "boundary: %s",
                        _filtered_path.resolve(),
                    )
                else:
                    _filtered_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not checkpoint_path.exists():
        _maybe_add_filtered_memory(_plan_set, "PLAN", _cycle_num=1)
        return "PLAN", _plan_set

    checkpoint = parse_checkpoint(checkpoint_path.read_text())

    match checkpoint.next_step:
        case "PLAN":
            _maybe_add_filtered_memory(_plan_set, "PLAN", _cycle_num=checkpoint.cycle + 1)
            return "PLAN", _plan_set
        case "EXECUTE" | "EXECUTE (rework)" | "EXECUTE (additional verification)":
            # No memory for EXECUTE -- clean up stale filtered file.
            _maybe_add_filtered_memory([], "EXECUTE", _cycle_num=checkpoint.cycle + 1)
            # Defense-in-depth: reject path traversal in current_phase.
            if not _PHASE_RE.match(checkpoint.current_phase):
                _log.warning(
                    "Invalid current_phase %r --- defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
            # Convergence override: if the coordinator requested rework
            # but ASSESS has converged (zero valid findings for N
            # consecutive rounds), skip rework and advance to VERIFY.
            # Primary: structured checkpoint field (no markdown parsing).
            # Fallback: decisions.md parsing (pre-existing milestones).
            if "rework" in checkpoint.next_step:
                conv = assess_convergence(
                    checkpoint,
                    decisions_content=(
                        decisions_path.read_text()
                        if decisions_path is not None and decisions_path.exists()
                        else None
                    ),
                )
                if conv.converged:
                    _log.info(
                        "ASSESS converged: %d consecutive zero-accept "
                        "rounds --- overriding rework, advancing to VERIFY",
                        conv.consecutive_zero_accepts,
                    )
                    return "VERIFY", [
                        "status.md",
                        "intents.md",
                        "compose.py",
                    ]
            # Read compose.py to find co-layer tasks for gather() groups.
            compose_path = checkpoint_path.parent.parent / "compose.py"
            execute_set = ["status.md", "compose.py"]
            if compose_path.exists():
                try:
                    from clou.graph import get_colayer_tasks
                    source = compose_path.read_text(encoding="utf-8")
                    peers = get_colayer_tasks(source, checkpoint.current_phase)
                    for task_name in peers:
                        execute_set.append(f"phases/{task_name}/phase.md")
                except Exception:
                    execute_set.append(
                        f"phases/{checkpoint.current_phase}/phase.md"
                    )
            else:
                execute_set.append(
                    f"phases/{checkpoint.current_phase}/phase.md"
                )
            return "EXECUTE", execute_set
        case "REPLAN":
            # No memory for REPLAN -- clean up stale filtered file.
            _maybe_add_filtered_memory([], "REPLAN", _cycle_num=checkpoint.cycle + 1)
            # Re-decomposition: ASSESS detected repeated rework failure
            # on the same task and requested re-planning (ADaPT, §9).
            # Read set mirrors PLAN but adds execution artifacts for
            # the failed phase so the coordinator can see what broke.
            # Defense-in-depth: reject path traversal in current_phase.
            if not _PHASE_RE.match(checkpoint.current_phase):
                _log.warning(
                    "Invalid current_phase %r --- defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", _plan_set
            replan_set = list(_plan_set)
            replan_set.extend([
                "compose.py",
                "decisions.md",
            ])
            if checkpoint.current_phase:
                replan_set.append(
                    f"phases/{checkpoint.current_phase}/phase.md"
                )
                replan_set.append(
                    f"phases/{checkpoint.current_phase}/execution.md"
                )
                # Include any coordinator-generated failure shards
                # (``execution-{slug}.md``).  Post-remolding, these are
                # the only shard files that should exist — worker success
                # paths all converge on the canonical ``execution.md``.
                # Recovery surfaces them so the next ASSESS sees the
                # failure context.
                milestone_dir = checkpoint_path.parent.parent
                phase_dir = milestone_dir / "phases" / checkpoint.current_phase
                if phase_dir.is_dir():
                    for shard in sorted(phase_dir.glob("execution-*.md")):
                        rel = f"phases/{checkpoint.current_phase}/{shard.name}"
                        if rel not in replan_set:
                            replan_set.append(rel)
            return "REPLAN", replan_set
        case "ASSESS":
            # Defense-in-depth: reject path traversal in current_phase.
            if not _PHASE_RE.match(checkpoint.current_phase):
                _log.warning(
                    "Invalid current_phase %r --- defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
            # Read compose.py to find co-layer tasks for gather() groups.
            compose_path = checkpoint_path.parent.parent / "compose.py"
            milestone_dir = checkpoint_path.parent.parent
            assess_read: list[str] = []
            if compose_path.exists():
                try:
                    from clou.graph import get_colayer_tasks
                    source = compose_path.read_text(encoding="utf-8")
                    peers = get_colayer_tasks(source, checkpoint.current_phase)
                    for task_name in peers:
                        assess_read.append(
                            f"phases/{task_name}/execution.md"
                        )
                        # Include any coordinator-generated failure shards
                # (``execution-{slug}.md``).  Post-remolding, these are
                # the only shard files that should exist — worker success
                # paths all converge on the canonical ``execution.md``.
                # Recovery surfaces them so the next ASSESS sees the
                # failure context.
                        phase_dir = milestone_dir / "phases" / task_name
                        if phase_dir.is_dir():
                            for shard in sorted(phase_dir.glob("execution-*.md")):
                                rel = f"phases/{task_name}/{shard.name}"
                                if rel not in assess_read:
                                    assess_read.append(rel)
                except Exception:
                    assess_read.append(
                        f"phases/{checkpoint.current_phase}/execution.md"
                    )
            else:
                assess_read.append(
                    f"phases/{checkpoint.current_phase}/execution.md"
                )
            # Always glob failure shards for current phase (fallback covers no-compose case).
            cur_phase_dir = milestone_dir / "phases" / checkpoint.current_phase
            if cur_phase_dir.is_dir():
                for shard in sorted(cur_phase_dir.glob("execution-*.md")):
                    rel = f"phases/{checkpoint.current_phase}/{shard.name}"
                    if rel not in assess_read:
                        assess_read.append(rel)
            assess_read += ["requirements.md", "decisions.md", "assessment.md"]
            # DB-18 I6: include filtered memory for ASSESS cycles.
            _maybe_add_filtered_memory(assess_read, "ASSESS", _cycle_num=checkpoint.cycle + 1)
            return "ASSESS", assess_read
        case "VERIFY":
            _maybe_add_filtered_memory([], "VERIFY", _cycle_num=checkpoint.cycle + 1)
            return "VERIFY", [
                "status.md",
                "intents.md",
                "compose.py",
            ]
        case "EXIT":
            _maybe_add_filtered_memory([], "EXIT", _cycle_num=checkpoint.cycle + 1)
            return "EXIT", [
                "status.md",
                "handoff.md",
                "decisions.md",
            ]
        case "COMPLETE":
            _maybe_add_filtered_memory([], "COMPLETE", _cycle_num=checkpoint.cycle + 1)
            return "COMPLETE", []

    # Unknown next_step fallback -- clean up stale filtered memory.
    _maybe_add_filtered_memory([], "UNKNOWN", _cycle_num=checkpoint.cycle + 1)
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
        # Legacy fallback -- root-scoped path.
        checkpoint_path = project_dir / ".clou" / "active" / "coordinator.md"
    if not checkpoint_path.exists():
        return "PLAN"
    return parse_checkpoint(checkpoint_path.read_text()).next_step
