"""Checkpoint parsing and cycle determination for the coordinator loop.

Public API:
    parse_checkpoint(content: str) -> Checkpoint
    determine_next_cycle(checkpoint_path: Path, milestone: str, ...) -> tuple[str, list[str]]
    assess_convergence(decisions_content: str, threshold: int) -> ConvergenceState
    read_cycle_count(checkpoint_path: Path) -> int
    read_cycle_outcome(project_dir: Path) -> str
    write_cycle_limit_escalation(project_dir, milestone, cycle_count) -> None
    write_agent_crash_escalation(project_dir, milestone) -> None
    write_validation_escalation(project_dir, milestone, errors) -> None
    git_revert_golden_context(project_dir: Path, milestone: str) -> None
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger(__name__)

#: Milestone names must be lowercase alphanumeric with hyphens.
_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validate_milestone(name: str) -> None:
    """Raise ValueError if *name* is not a valid milestone slug."""
    if not _MILESTONE_RE.match(name):
        msg = f"Invalid milestone name: {name!r} (must match [a-z0-9][a-z0-9-]*)"
        raise ValueError(msg)


#: Consecutive zero-accept ASSESS cycles required to declare convergence.
_CONVERGENCE_THRESHOLD = 2

#: Valid next_step values in coordinator checkpoints.
_VALID_NEXT_STEPS = frozenset(
    {
        "PLAN",
        "EXECUTE",
        "EXECUTE (rework)",
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
    """
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):\s*(.+)$", content):
        fields[match.group(1)] = match.group(2).strip()

    next_step = fields.get("next_step", "PLAN")
    if next_step not in _VALID_NEXT_STEPS:
        _log.warning("Unknown next_step %r — defaulting to PLAN", next_step)
        next_step = "PLAN"

    return Checkpoint(
        cycle=_safe_int(fields.get("cycle", "0")),
        step=fields.get("step", "PLAN"),
        next_step=next_step,
        current_phase=fields.get("current_phase", ""),
        phases_completed=_safe_int(fields.get("phases_completed", "0")),
        phases_total=_safe_int(fields.get("phases_total", "0")),
    )


def assess_convergence(
    decisions_content: str,
    threshold: int = _CONVERGENCE_THRESHOLD,
) -> ConvergenceState:
    """Analyze decisions.md for consecutive zero-accepted ASSESS cycles.

    Scans cycle sections newest-first (the file's natural ordering per DB-08).
    Counts consecutive Brutalist Assessment / ASSESS sections that contain
    zero ``### Accepted:`` entries.  Convergence is reached when this count
    meets or exceeds *threshold*.
    """
    headers = list(_ASSESS_HEADER_RE.finditer(decisions_content))

    assess_blocks: list[str] = []
    for i, match in enumerate(headers):
        label = match.group(1).strip().lower()
        if "brutalist" not in label and "assess" not in label:
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
    if not checkpoint_path.exists():
        return "PLAN", ["milestone.md", "requirements.md", "project.md"]

    checkpoint = parse_checkpoint(checkpoint_path.read_text())

    match checkpoint.next_step:
        case "PLAN":
            return "PLAN", ["milestone.md", "requirements.md", "project.md"]
        case "EXECUTE" | "EXECUTE (rework)":
            # Defense-in-depth: reject path traversal in current_phase.
            if ".." in checkpoint.current_phase or "/" in checkpoint.current_phase:
                _log.warning(
                    "Invalid current_phase %r — defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "requirements.md", "project.md"]
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
                        "requirements.md",
                        "compose.py",
                        "active/coordinator.md",
                    ]
            return "EXECUTE", [
                "status.md",
                "compose.py",
                f"phases/{checkpoint.current_phase}/phase.md",
                "active/coordinator.md",
            ]
        case "ASSESS":
            # Defense-in-depth: reject path traversal in current_phase.
            if ".." in checkpoint.current_phase or "/" in checkpoint.current_phase:
                _log.warning(
                    "Invalid current_phase %r — defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "requirements.md", "project.md"]
            return "ASSESS", [
                "status.md",
                "compose.py",
                f"phases/{checkpoint.current_phase}/execution.md",
                "requirements.md",
                "decisions.md",
                "assessment.md",
                "active/coordinator.md",
            ]
        case "VERIFY":
            return "VERIFY", [
                "status.md",
                "requirements.md",
                "compose.py",
                "active/coordinator.md",
            ]
        case "EXIT":
            return "EXIT", [
                "status.md",
                "handoff.md",
                "decisions.md",
                "active/coordinator.md",
            ]
        case "COMPLETE":
            return "COMPLETE", []

    return "PLAN", ["milestone.md", "requirements.md", "project.md"]


def read_cycle_count(checkpoint_path: Path) -> int:
    """Read the cycle count from the checkpoint file.

    Returns 0 if the file does not exist.
    """
    if not checkpoint_path.exists():
        return 0
    return parse_checkpoint(checkpoint_path.read_text()).cycle


def read_cycle_outcome(project_dir: Path) -> str:
    """Read the outcome of the last completed cycle from the checkpoint.

    Returns the ``next_step`` field which indicates what should happen next.
    Returns ``"PLAN"`` if no checkpoint exists.
    """
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
    project_dir: Path, milestone: str, errors: list[str]
) -> None:
    """Write escalation after 3 consecutive validation failures."""
    _validate_milestone(milestone)
    error_list = "\n".join(f"- {e}" for e in errors)
    _write_escalation(
        project_dir=project_dir,
        milestone=milestone,
        slug="validation-failure",
        title="Repeated Validation Failures",
        classification="blocking",
        context=(
            "Golden context validation has failed 3 consecutive times "
            "after cycle completion."
        ),
        issue="The agent team is producing structurally invalid golden context files.",
        evidence=f"Latest validation errors:\n{error_list}",
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


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


async def git_commit_phase(project_dir: Path, milestone: str, phase: str) -> None:
    """Commit all changes after a phase completes.

    Uses ``git add -A`` to stage everything, then ``git diff --cached --quiet``
    to detect changes, then ``git commit``.  No-op if nothing is staged.
    """
    _validate_milestone(milestone)

    # Stage all changes
    proc = await asyncio.create_subprocess_exec(
        "git", "add", "-A",
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


async def git_revert_golden_context(project_dir: Path, milestone: str) -> None:
    """Revert golden context files to pre-cycle state.

    Uses ``git checkout HEAD --`` to restore active context and milestone files.
    """
    _validate_milestone(milestone)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "checkout",
        "HEAD",
        "--",
        ".clou/active/",
        f".clou/milestones/{milestone}/",
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
