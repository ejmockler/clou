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
from pathlib import Path

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
    # Retry counters -- persisted so they survive process restarts.
    validation_retries: int = 0
    readiness_retries: int = 0
    crash_retries: int = 0
    staleness_count: int = 0


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
                    "Invalid current_phase %r --- defaulting to PLAN",
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
                        "rounds --- overriding rework, advancing to VERIFY",
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
                    "Invalid current_phase %r --- defaulting to PLAN",
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
        # Legacy fallback -- root-scoped path.
        checkpoint_path = project_dir / ".clou" / "active" / "coordinator.md"
    if not checkpoint_path.exists():
        return "PLAN"
    return parse_checkpoint(checkpoint_path.read_text()).next_step
