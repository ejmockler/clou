"""Execution artifact path derivation — code owns format AND path.

Canonical invariant: one ``execution.md`` per phase directory.
Workers always write to :func:`canonical_execution_path` regardless of
whether the DAG layer contains one function or many — compose.py expresses
parallelism at the phase level (multiple functions in ``gather()``), not
within a phase.

The ``execution-{slug}.md`` form is reserved for **coordinator-generated
failure shards** (timeouts, budget exceedances) where the coordinator
records a structured failure record for a terminated task without the
worker having a chance to write its own execution.md.  That slug path is
deterministic via :func:`_slugify` — callers pass a task name, code
computes the slug — so no LLM-owned slugification enters the filesystem.
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


def canonical_execution_path(phase: str) -> str:
    """Return the canonical execution artifact path for a phase.

    The path is relative to the milestone directory
    (``.clou/milestones/{milestone}/``).  This is the ONLY path workers
    should be briefed to write — there is exactly one execution.md per
    phase directory, regardless of gather-group membership.

    Returns:
        ``phases/{phase}/execution.md``.
    """
    return f"phases/{phase}/execution.md"


def failure_shard_path(milestone: str, phase: str, task: str) -> str:
    """Return the path for a coordinator-generated failure shard.

    Used ONLY when the coordinator terminates a task (timeout, budget
    exceeded) and must record the failure record itself because the
    worker never finished.  The slug is computed by :func:`_slugify`
    from the task name — deterministic, no LLM involvement.

    For normal worker output, use :func:`canonical_execution_path`.

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
            "cannot generate a valid failure shard path"
        )
    return f"phases/{phase}/execution-{slug}.md"


def _clean_stale_shards_in_dir(
    phase_dir: Path,
) -> tuple[list[Path], list[tuple[Path, OSError]]]:
    """Internal sweeper: remove every ``execution-*.md`` in *phase_dir*.

    Per-file error isolation — one unlink failure does NOT abort the
    sweep.  Symlinks are refused as defense-in-depth (a ``.clou/`` symlink
    shouldn't exist, but if one does, dereferencing it on unlink would
    remove the target rather than the orphan).

    Returns:
        Tuple ``(removed, failed)`` where *removed* is the list of
        deleted paths and *failed* is a list of ``(path, exception)``
        tuples for shards that could not be removed.
    """
    removed: list[Path] = []
    failed: list[tuple[Path, OSError]] = []
    if not phase_dir.is_dir():
        return removed, failed
    for shard in sorted(phase_dir.glob("execution-*.md")):
        if shard.is_symlink():
            failed.append((
                shard,
                OSError(f"refusing to unlink symlink: {shard}"),
            ))
            continue
        try:
            shard.unlink()
        except OSError as exc:
            failed.append((shard, exc))
            continue
        removed.append(shard)
    return removed, failed


def clean_stale_shards(milestone_dir: Path, phase: str) -> list[Path]:
    """Remove stale ``execution-*.md`` shard files from a phase directory.

    Deletes every ``execution-*.md`` file (shards from a previous cycle,
    or orphans from an earlier topology) while leaving the canonical
    ``execution.md`` untouched.  Per-file failures are swallowed and
    the function returns the list of successfully removed paths —
    callers who need failure visibility should use
    :func:`clean_stale_shards_for_layer`, which surfaces them.

    Args:
        milestone_dir: Absolute path to the milestone directory.
        phase: Phase slug.

    Returns:
        List of paths that were removed.
    """
    removed, _failed = _clean_stale_shards_in_dir(
        milestone_dir / "phases" / phase,
    )
    return removed


def clean_stale_shards_for_layer(
    milestone_dir: Path,
    phase_names: list[str],
) -> tuple[dict[str, list[Path]], dict[str, list[tuple[Path, OSError]]]]:
    """Clean stale shards across every phase in a DAG layer.

    The prior single-phase cleanup was called against
    ``checkpoint.current_phase`` only — which for gather() layers
    containing multiple functions in parallel left the other phases'
    orphan shards in place.  This iterates every phase in the active
    layer, closing that gap.  Per-file failures are isolated: one
    un-unlinkable shard does not prevent others from being cleaned.

    Args:
        milestone_dir: Absolute path to the milestone directory.
        phase_names: Phase slugs in the layer to sweep.

    Returns:
        Tuple ``(removed, failed)``:

        * ``removed`` — mapping phase → list of deleted paths.  Phases
          with nothing to clean are omitted.
        * ``failed``  — mapping phase → list of ``(path, exception)``
          pairs for shards that could not be removed (permission,
          symlink refusal, transient filesystem error).  Phases with
          no failures are omitted.  Callers should surface these at
          least as warnings — silent cleanup failure was a root cause
          of the slug-drift class of bug.
    """
    removed: dict[str, list[Path]] = {}
    failed: dict[str, list[tuple[Path, OSError]]] = {}
    for phase in phase_names:
        phase_removed, phase_failed = _clean_stale_shards_in_dir(
            milestone_dir / "phases" / phase,
        )
        if phase_removed:
            removed[phase] = phase_removed
        if phase_failed:
            failed[phase] = phase_failed
    return removed, failed
