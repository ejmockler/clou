"""Execution state sharding for concurrent gather() groups.

When the coordinator dispatches a gather() group with >1 concurrent task,
each worker writes to its own shard file (``execution-{task}.md``) instead
of the shared ``execution.md``.  After all workers complete, the coordinator
merges shards into a unified execution.md.

Narrow graphs (single task per layer) bypass sharding entirely -- workers
write directly to ``execution.md`` with zero overhead.
"""

from __future__ import annotations

import re
from pathlib import Path

# Regex for sanitising task names into filename-safe slugs.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Convert a task name to a filename-safe slug.

    Lowercases, replaces non-alphanumeric runs with hyphens, strips
    leading/trailing hyphens.
    """
    return _SLUG_RE.sub("-", name.lower()).strip("-")


def write_shard_path(milestone: str, phase: str, task: str) -> str:
    """Return the relative path for a task's execution shard.

    The path is relative to the milestone directory
    (``.clou/milestones/{milestone}/``).

    Returns:
        ``phases/{phase}/execution-{slug}.md`` where *slug* is the
        sanitised task name.

    Raises:
        ValueError: If *task* produces an empty slug (e.g. punctuation-only
            or non-ASCII-only input).
    """
    slug = _slugify(task)
    if not slug:
        raise ValueError(
            f"Task name {task!r} produces an empty slug; "
            "cannot generate a valid shard path"
        )
    return f"phases/{phase}/execution-{slug}.md"


def _parse_summary(content: str) -> dict[str, str]:
    """Extract key-value pairs from a ``## Summary`` block.

    Expects lines like ``status: completed`` immediately after the heading.
    Stops at the first blank line or next heading.
    """
    result: dict[str, str] = {}
    in_summary = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Summary"):
            in_summary = True
            continue
        if in_summary:
            if not stripped or stripped.startswith("#"):
                break
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                result[key.strip()] = value.strip()
    return result


def _aggregate_summaries(
    summaries: list[dict[str, str]],
) -> str:
    """Build a merged Summary section from individual shard summaries.

    Aggregates numeric task counts and collects non-``none`` failures/blockers.
    Status is ``completed`` only if all shards are completed; ``failed`` if
    any failed; otherwise ``in_progress``.
    """
    total = 0
    completed = 0
    failed = 0
    in_progress = 0
    failures: list[str] = []
    blockers: list[str] = []

    for s in summaries:
        # Parse the "tasks" line: "N total, N completed, ..."
        tasks_line = s.get("tasks", "")
        for part in tasks_line.split(","):
            part = part.strip()
            if "total" in part:
                total += _extract_int(part)
            elif "completed" in part:
                completed += _extract_int(part)
            elif "failed" in part:
                failed += _extract_int(part)
            elif "in_progress" in part:
                in_progress += _extract_int(part)

        f = s.get("failures", "none")
        if f and f != "none":
            failures.append(f)

        b = s.get("blockers", "none")
        if b and b != "none":
            blockers.append(b)

    # Derive aggregate status.
    if failed > 0:
        status = "failed"
    elif completed == total and total > 0:
        status = "completed"
    else:
        status = "in_progress"

    failures_str = "; ".join(failures) if failures else "none"
    blockers_str = "; ".join(blockers) if blockers else "none"

    return (
        f"## Summary\n"
        f"status: {status}\n"
        f"tasks: {total} total, {completed} completed, "
        f"{failed} failed, {in_progress} in_progress\n"
        f"failures: {failures_str}\n"
        f"blockers: {blockers_str}\n"
    )


def _extract_int(s: str) -> int:
    """Extract the first integer from a string, defaulting to 0."""
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0


def merge_shards(milestone_dir: Path, phase: str) -> str:
    """Merge all execution shard files in a phase directory.

    Reads all ``execution-*.md`` files, concatenates them into a unified
    document with an aggregated Summary section followed by per-task
    sections.

    Args:
        milestone_dir: Absolute path to the milestone directory
            (e.g. ``/project/.clou/milestones/17-foo``).
        phase: Phase slug (e.g. ``shard-infrastructure``).

    Returns:
        Merged content as a string.  Empty string if no shards exist.
        If only one shard exists, returns it as-is (no merge overhead).
    """
    phase_dir = milestone_dir / "phases" / phase
    if not phase_dir.is_dir():
        return ""

    shards = sorted(phase_dir.glob("execution-*.md"))
    if not shards:
        return ""

    if len(shards) == 1:
        return shards[0].read_text(encoding="utf-8")

    # Multiple shards -- merge.
    summaries: list[dict[str, str]] = []
    bodies: list[str] = []

    for shard_path in shards:
        content = shard_path.read_text(encoding="utf-8")
        summary = _parse_summary(content)
        summaries.append(summary)

        # Extract the task name from the filename: execution-{task}.md
        task_name = shard_path.stem.removeprefix("execution-")
        bodies.append(f"---\n### Shard: {task_name}\n\n{content}")

    header = _aggregate_summaries(summaries)
    return header + "\n" + "\n".join(bodies) + "\n"


def clean_stale_shards(milestone_dir: Path, phase: str) -> list[Path]:
    """Remove stale execution shard files from a phase directory.

    Deletes all ``execution-*.md`` files (shards from a previous cycle)
    while leaving ``execution.md`` untouched.  Intended to be called by the
    coordinator before dispatching a new gather() group so that only
    current-cycle shards are present when merge runs.

    Args:
        milestone_dir: Absolute path to the milestone directory.
        phase: Phase slug.

    Returns:
        List of paths that were removed (for logging/diagnostics).
    """
    phase_dir = milestone_dir / "phases" / phase
    if not phase_dir.is_dir():
        return []

    removed: list[Path] = []
    for shard in sorted(phase_dir.glob("execution-*.md")):
        shard.unlink()
        removed.append(shard)
    return removed
