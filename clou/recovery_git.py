"""Git operations for the coordinator loop.

Provides git_commit_phase(), git_revert_golden_context(), and
archive_milestone_episodic() -- all async functions that run git
subprocesses.

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from pathlib import Path

from clou.recovery_checkpoint import _validate_milestone

_log = logging.getLogger(__name__)

#: Max wall-clock any git subprocess may run before the coordinator
#: treats it as hung.  All ``communicate()`` calls in this module are
#: wrapped via :func:`_communicate_or_timeout`; a hang raises
#: ``RuntimeError`` (and kills the subprocess) rather than blocking
#: ``run_coordinator`` forever.  Tests (``test_git_revert_timeout``,
#: ``test_git_commit_phase_timeout``) pin the value to 30 — the
#: monkeypatch on ``asyncio.wait_for`` shortens this to 0.05 so they
#: don't actually wait 30s; the 30 sentinel is the match key.
_GIT_SUBPROCESS_TIMEOUT: float = 30


async def _communicate_or_timeout(
    proc: asyncio.subprocess.Process,
    *,
    operation: str,
    timeout: float = _GIT_SUBPROCESS_TIMEOUT,
) -> tuple[bytes, bytes]:
    """Await ``proc.communicate()`` with a hard timeout.

    On hang: kill the subprocess and raise ``RuntimeError`` so the
    caller (ultimately ``run_coordinator``) does not wedge on a stuck
    git process.  *operation* is embedded in the error message so
    operators can identify which git invocation timed out (e.g.
    ``"git revert"`` vs ``"git commit phase (add)"``).

    The message shape is ``"{operation} timed out after {timeout}s"``
    so regex matches in tests (``"git revert timed out"``,
    ``"timed out"``) remain stable across call sites.
    """
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(
            f"{operation} timed out after {timeout}s"
        ) from None


#: Patterns excluded from selective staging (DB-15 D4).
_STAGING_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".clou/telemetry/*",
    ".clou/sessions/*",
    "*/_filtered_memory.md",
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
    stdout_bytes, _ = await _communicate_or_timeout(
        proc, operation="git commit phase (diff scan)",
    )

    # Also get untracked files.
    proc2 = await asyncio.create_subprocess_exec(
        "git", "ls-files", "--others", "--exclude-standard",
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout2_bytes, _ = await _communicate_or_timeout(
        proc2, operation="git commit phase (ls-files)",
    )

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
    _, stderr_bytes = await _communicate_or_timeout(
        proc, operation="git commit phase (add)",
    )
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
    await _communicate_or_timeout(
        proc, operation="git commit phase (diff cached)",
    )

    if proc.returncode == 0:
        # No changes staged -- nothing to commit
        return

    # Commit with structured message
    message = f"feat({milestone}): complete phase '{phase}'"
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", message,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await _communicate_or_timeout(
        proc, operation="git commit phase (commit)",
    )
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
    _, stderr_bytes = await _communicate_or_timeout(
        proc, operation="git revert",
    )
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
        await _communicate_or_timeout(
            proc2, operation="git revert (clean)",
        )
        # git clean exit code 0 = success; non-zero is unexpected but not fatal
        # for the revert operation, so we log but don't raise.
        if proc2.returncode != 0:
            _log.warning(
                "git clean returned exit %d during revert for %r",
                proc2.returncode,
                milestone,
            )


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
    _, stderr_bytes = await _communicate_or_timeout(
        proc, operation="git archive (rm)",
    )

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        _log.warning("git rm failed for %r: %s", milestone, stderr_text.strip())
        return []

    _log.info("Archived %d episodic files for %r", len(existing), milestone)
    return existing
