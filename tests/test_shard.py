"""Tests for execution artifact path derivation (clou/shard.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest as _pytest_import

from clou.hooks import WRITE_PERMISSIONS, build_hooks
from clou.shard import (
    _slugify,
    canonical_execution_path,
    clean_stale_shards,
    clean_stale_shards_for_layer,
    failure_shard_path,
)


# ---------------------------------------------------------------------------
# canonical_execution_path — the worker-success canonical path
# ---------------------------------------------------------------------------


def test_canonical_execution_path_returns_standard_shape() -> None:
    """One ``execution.md`` per phase — no slug, no freeform."""
    assert (
        canonical_execution_path("set_coverage_thresholds")
        == "phases/set_coverage_thresholds/execution.md"
    )


def test_canonical_execution_path_preserves_phase_casing() -> None:
    """Phase names are passed through verbatim — validation is upstream."""
    assert (
        canonical_execution_path("Mixed_Case-Phase")
        == "phases/Mixed_Case-Phase/execution.md"
    )


def test_canonical_execution_path_is_deterministic() -> None:
    """Same phase name → same path, every time."""
    a = canonical_execution_path("extend_logger")
    b = canonical_execution_path("extend_logger")
    assert a == b


# ---------------------------------------------------------------------------
# failure_shard_path — coordinator-generated failure records only
# ---------------------------------------------------------------------------


def test_failure_shard_path_basic() -> None:
    """Simple task name produces a deterministic slugified path."""
    result = failure_shard_path(
        "17-runtime-safeguards", "shard-infrastructure", "build",
    )
    assert result == "phases/shard-infrastructure/execution-build.md"


def test_failure_shard_path_sanitization() -> None:
    """Special characters, spaces, and mixed case are sanitised."""
    assert (
        failure_shard_path("m1", "p1", "Build Shard Infrastructure!!")
        == "phases/p1/execution-build-shard-infrastructure.md"
    )
    assert (
        failure_shard_path("m1", "p1", "task_with_underscores")
        == "phases/p1/execution-task-with-underscores.md"
    )
    assert (
        failure_shard_path("m1", "p1", "  UPPER--CASE  ")
        == "phases/p1/execution-upper-case.md"
    )


def test_slugify_empty() -> None:
    """Edge case: empty string produces empty slug."""
    assert _slugify("") == ""


def test_slugify_numbers_preserved() -> None:
    """Numbers are preserved in slugs."""
    assert _slugify("task-42") == "task-42"


@_pytest_import.mark.parametrize(
    "bad_name",
    [
        "",           # empty string
        "!!!",        # punctuation-only
        "---",        # hyphens-only (stripped to empty)
        "...",        # dots-only
        "@#$%^&*()",  # symbols-only
    ],
)
def test_failure_shard_path_rejects_empty_slug(bad_name: str) -> None:
    """Task names that produce empty slugs raise ValueError."""
    with _pytest_import.raises(ValueError, match="empty slug"):
        failure_shard_path("m1", "p1", bad_name)


# ---------------------------------------------------------------------------
# clean_stale_shards — single-phase sweep
# ---------------------------------------------------------------------------


def test_clean_stale_shards_removes_shard_files(tmp_path: Path) -> None:
    """Stale execution-*.md files are removed."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    (phase_dir / "execution-alpha.md").write_text("stale shard A\n")
    (phase_dir / "execution-beta.md").write_text("stale shard B\n")

    removed = clean_stale_shards(milestone_dir, "my-phase")
    assert len(removed) == 2
    assert not list(phase_dir.glob("execution-*.md"))


def test_clean_stale_shards_preserves_execution_md(tmp_path: Path) -> None:
    """The main execution.md is NOT deleted by clean_stale_shards."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    (phase_dir / "execution.md").write_text("main execution state\n")
    (phase_dir / "execution-old-task.md").write_text("stale shard\n")

    removed = clean_stale_shards(milestone_dir, "my-phase")
    assert len(removed) == 1
    assert (phase_dir / "execution.md").exists()
    assert (phase_dir / "execution.md").read_text() == "main execution state\n"


def test_clean_stale_shards_nonexistent_dir(tmp_path: Path) -> None:
    """Non-existent phase directory returns empty list without error."""
    removed = clean_stale_shards(tmp_path, "no-such-phase")
    assert removed == []


def test_clean_stale_shards_empty_dir(tmp_path: Path) -> None:
    """Phase directory with no shard files returns empty list."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    (phase_dir / "execution.md").write_text("main\n")

    removed = clean_stale_shards(milestone_dir, "my-phase")
    assert removed == []


# ---------------------------------------------------------------------------
# clean_stale_shards_for_layer — gather-group layer sweep
# ---------------------------------------------------------------------------


def test_clean_stale_shards_for_layer_sweeps_all_phases(tmp_path: Path) -> None:
    """Every phase in the layer has its stale shards cleaned.

    This pins the bug we're fixing: the prior single-phase cleanup left
    stale shards in the other phases of a gather() group, amplifying
    validation failures across cycles.
    """
    milestone_dir = tmp_path / "milestone"
    for phase in ("set_coverage_thresholds", "create_metrics_module", "extend_logger"):
        pd = milestone_dir / "phases" / phase
        pd.mkdir(parents=True)
        (pd / "execution-stale.md").write_text("old\n")
        (pd / "execution.md").write_text("current\n")

    removed, failed = clean_stale_shards_for_layer(
        milestone_dir,
        ["set_coverage_thresholds", "create_metrics_module", "extend_logger"],
    )

    # Every phase had one stale shard removed, none failed.
    assert set(removed.keys()) == {
        "set_coverage_thresholds", "create_metrics_module", "extend_logger",
    }
    assert failed == {}
    for phase, paths in removed.items():
        assert len(paths) == 1
    # Canonical execution.md preserved everywhere.
    for phase in removed:
        assert (milestone_dir / "phases" / phase / "execution.md").exists()


def test_clean_stale_shards_for_layer_omits_clean_phases(tmp_path: Path) -> None:
    """Phases with nothing to clean are omitted from the result mapping."""
    milestone_dir = tmp_path / "milestone"
    # Phase A has a stale shard, phase B is clean, phase C doesn't exist.
    pd_a = milestone_dir / "phases" / "phase_a"
    pd_a.mkdir(parents=True)
    (pd_a / "execution-stale.md").write_text("old\n")

    pd_b = milestone_dir / "phases" / "phase_b"
    pd_b.mkdir(parents=True)
    (pd_b / "execution.md").write_text("current\n")

    removed, failed = clean_stale_shards_for_layer(
        milestone_dir, ["phase_a", "phase_b", "phase_c"],
    )
    assert set(removed.keys()) == {"phase_a"}
    assert len(removed["phase_a"]) == 1
    assert failed == {}


def test_clean_stale_shards_for_layer_empty_layer(tmp_path: Path) -> None:
    """An empty phase list returns empty mappings, no error."""
    removed, failed = clean_stale_shards_for_layer(tmp_path, [])
    assert removed == {}
    assert failed == {}


def test_clean_stale_shards_for_layer_reports_per_file_failures(
    tmp_path: Path,
) -> None:
    """Per-file failures are surfaced without aborting the sweep.

    Pins the invariant that silent cleanup failure was a root cause of
    the slug-drift incident: one un-unlinkable shard must not prevent
    others in the same phase (or other phases) from being cleaned, and
    the failure must be reported to the caller.
    """
    milestone_dir = tmp_path / "milestone"
    phase_a = milestone_dir / "phases" / "phase_a"
    phase_a.mkdir(parents=True)

    # A real file that cleans normally.
    good = phase_a / "execution-good.md"
    good.write_text("good\n")

    # A symlink that cleanup must refuse to follow (defense-in-depth).
    external = tmp_path / "outside.md"
    external.write_text("external target\n")
    link = phase_a / "execution-link.md"
    link.symlink_to(external)

    removed, failed = clean_stale_shards_for_layer(
        milestone_dir, ["phase_a"],
    )

    # The regular shard was removed.
    assert removed.get("phase_a") == [good]
    # The symlink was refused and reported as a failure.
    assert "phase_a" in failed
    fail_entries = failed["phase_a"]
    assert len(fail_entries) == 1
    fail_path, fail_exc = fail_entries[0]
    assert fail_path == link
    assert "symlink" in str(fail_exc).lower()
    # Target file outside .clou/ is untouched.
    assert external.exists()
    assert link.is_symlink()


# ---------------------------------------------------------------------------
# Hook permission matching
# ---------------------------------------------------------------------------


def test_hook_permission_allows_canonical_execution_md() -> None:
    """Worker write permissions match the canonical execution.md path."""
    worker_perms = WRITE_PERMISSIONS["worker"]
    import fnmatch

    # Canonical execution.md is the only worker-writable execution artifact.
    assert any(
        fnmatch.fnmatch("milestones/m1/phases/p1/execution.md", p)
        for p in worker_perms
    )


def test_hook_permission_denies_sharded_execution_paths() -> None:
    """Worker permissions must NOT match execution-<slug>.md.

    Those are coordinator-generated failure shards written in-process by
    Python (bypassing the hook).  Granting workers permission to write
    them was the live drift vector that caused the slug-drift incident:
    a worker with stale briefing could freeform a slug and the hook
    would allow it.  This test pins that the permission is now tight.
    """
    worker_perms = WRITE_PERMISSIONS["worker"]
    import fnmatch

    for bad_path in (
        "milestones/m1/phases/p1/execution-build.md",
        "milestones/m1/phases/p1/execution-extend-logger.md",
        "milestones/m1/phases/p1/execution-extend_logger.md",
        "milestones/m1/phases/p1/execution-anything-else.md",
    ):
        assert not any(fnmatch.fnmatch(bad_path, p) for p in worker_perms), (
            f"Worker should NOT be able to write {bad_path}"
        )


def test_hook_permission_scoped_denies_sharded_paths() -> None:
    """Scoped worker permissions also reject execution-<slug>.md."""
    from clou.hooks import _scoped_permissions

    scoped = _scoped_permissions("worker", "17-runtime-safeguards")
    import fnmatch

    # Canonical path still allowed.
    ok_path = "milestones/17-runtime-safeguards/phases/p1/execution.md"
    assert any(fnmatch.fnmatch(ok_path, p) for p in scoped)

    # Slugged shard path denied even in scope.
    bad_path = "milestones/17-runtime-safeguards/phases/p1/execution-task.md"
    assert not any(fnmatch.fnmatch(bad_path, p) for p in scoped)


def _run(coro: object) -> dict[str, object]:
    """Run an async coroutine and return the result."""
    result: object = asyncio.run(coro)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result


def _is_denied(result: dict[str, object]) -> bool:
    """Check whether a PreToolUse hook response blocks the tool."""
    hso = result.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        return False
    return hso.get("permissionDecision") == "deny"


def test_hook_enforcement_allows_canonical_execution_md(tmp_path: Path) -> None:
    """PreToolUse hook allows worker to write to canonical execution.md."""
    project_dir = tmp_path / "project"
    clou_dir = project_dir / ".clou"
    clou_dir.mkdir(parents=True)

    hooks = build_hooks("coordinator", project_dir, milestone="m1")
    pre_hook = hooks["PreToolUse"][0].hooks[0]

    ok_path = str(
        clou_dir / "milestones" / "m1" / "phases" / "p1" / "execution.md"
    )
    result = _run(
        pre_hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": ok_path},
                "agent_type": "implementer",
            },
            None,
            {},
        )
    )
    assert not _is_denied(result)


def test_hook_enforcement_denies_sharded_path_for_worker(tmp_path: Path) -> None:
    """Worker tool-call writes to execution-<slug>.md are denied at the hook.

    Enforcement at the boundary — the prompt alone cannot stop drift.
    """
    project_dir = tmp_path / "project"
    clou_dir = project_dir / ".clou"
    clou_dir.mkdir(parents=True)

    hooks = build_hooks("coordinator", project_dir, milestone="m1")
    pre_hook = hooks["PreToolUse"][0].hooks[0]

    shard_path = str(
        clou_dir / "milestones" / "m1" / "phases" / "p1" / "execution-build.md"
    )
    result = _run(
        pre_hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": shard_path},
                "agent_type": "implementer",
            },
            None,
            {},
        )
    )
    assert _is_denied(result)
