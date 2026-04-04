"""Tests for execution state sharding (clou/shard.py) and hook permissions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from clou.hooks import WRITE_PERMISSIONS, build_hooks
import pytest as _pytest_import  # for parametrize; pytest also imported by fixture

from clou.shard import (
    _slugify,
    clean_stale_shards,
    merge_shards,
    write_shard_path,
)


# ---------------------------------------------------------------------------
# write_shard_path
# ---------------------------------------------------------------------------


def test_write_shard_path_basic() -> None:
    """Simple task name produces the expected relative path."""
    result = write_shard_path("17-runtime-safeguards", "shard-infrastructure", "build")
    assert result == "phases/shard-infrastructure/execution-build.md"


def test_write_shard_path_sanitization() -> None:
    """Special characters, spaces, and mixed case are sanitised."""
    result = write_shard_path("m1", "p1", "Build Shard Infrastructure!!")
    assert result == "phases/p1/execution-build-shard-infrastructure.md"

    result2 = write_shard_path("m1", "p1", "task_with_underscores")
    assert result2 == "phases/p1/execution-task-with-underscores.md"

    result3 = write_shard_path("m1", "p1", "  UPPER--CASE  ")
    assert result3 == "phases/p1/execution-upper-case.md"


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
def test_write_shard_path_rejects_empty_slug(bad_name: str) -> None:
    """Task names that produce empty slugs raise ValueError."""
    with _pytest_import.raises(ValueError, match="empty slug"):
        write_shard_path("m1", "p1", bad_name)


# ---------------------------------------------------------------------------
# merge_shards
# ---------------------------------------------------------------------------


def test_merge_shards_empty(tmp_path: Path) -> None:
    """No shards returns empty string."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    result = merge_shards(milestone_dir, "my-phase")
    assert result == ""


def test_merge_shards_nonexistent_dir(tmp_path: Path) -> None:
    """Non-existent phase directory returns empty string."""
    result = merge_shards(tmp_path, "no-such-phase")
    assert result == ""


def test_merge_shards_single(tmp_path: Path) -> None:
    """Single shard returned as-is with no merge overhead."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    content = "## Summary\nstatus: completed\ntasks: 2 total, 2 completed, 0 failed, 0 in_progress\nfailures: none\nblockers: none\n\n### T1: Do something\n**Status:** completed\n"
    (phase_dir / "execution-alpha.md").write_text(content)

    result = merge_shards(milestone_dir, "my-phase")
    assert result == content


def test_merge_shards_multiple(tmp_path: Path) -> None:
    """Multiple shards are merged with aggregated summary."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    shard_a = (
        "## Summary\n"
        "status: completed\n"
        "tasks: 2 total, 2 completed, 0 failed, 0 in_progress\n"
        "failures: none\n"
        "blockers: none\n"
        "\n### T1: Alpha work\n**Status:** completed\n"
    )
    shard_b = (
        "## Summary\n"
        "status: completed\n"
        "tasks: 3 total, 3 completed, 0 failed, 0 in_progress\n"
        "failures: none\n"
        "blockers: none\n"
        "\n### T1: Beta work\n**Status:** completed\n"
    )

    (phase_dir / "execution-alpha.md").write_text(shard_a)
    (phase_dir / "execution-beta.md").write_text(shard_b)

    result = merge_shards(milestone_dir, "my-phase")

    # Aggregated summary: 5 total, 5 completed
    assert "status: completed" in result
    assert "5 total" in result
    assert "5 completed" in result
    assert "0 failed" in result
    assert "failures: none" in result

    # Both shards present
    assert "### Shard: alpha" in result
    assert "### Shard: beta" in result


def test_merge_shards_with_failures(tmp_path: Path) -> None:
    """Merged status is 'failed' when any shard has failures."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    shard_ok = (
        "## Summary\n"
        "status: completed\n"
        "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
        "failures: none\n"
        "blockers: none\n"
    )
    shard_fail = (
        "## Summary\n"
        "status: failed\n"
        "tasks: 2 total, 1 completed, 1 failed, 0 in_progress\n"
        "failures: T2 crashed\n"
        "blockers: none\n"
    )

    (phase_dir / "execution-good.md").write_text(shard_ok)
    (phase_dir / "execution-bad.md").write_text(shard_fail)

    result = merge_shards(milestone_dir, "my-phase")
    assert "status: failed" in result
    assert "1 failed" in result
    assert "failures: T2 crashed" in result


def test_merge_shards_deterministic(tmp_path: Path) -> None:
    """Same input always produces the same output (alphabetical sort)."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    for name in ("charlie", "alpha", "bravo"):
        content = (
            "## Summary\n"
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
            "blockers: none\n"
        )
        (phase_dir / f"execution-{name}.md").write_text(content)

    result1 = merge_shards(milestone_dir, "my-phase")
    result2 = merge_shards(milestone_dir, "my-phase")
    assert result1 == result2

    # Verify alphabetical ordering
    alpha_pos = result1.index("### Shard: alpha")
    bravo_pos = result1.index("### Shard: bravo")
    charlie_pos = result1.index("### Shard: charlie")
    assert alpha_pos < bravo_pos < charlie_pos


def test_merge_ignores_regular_execution_md(tmp_path: Path) -> None:
    """The regular execution.md is not included in shard merge."""
    milestone_dir = tmp_path / "milestone"
    phase_dir = milestone_dir / "phases" / "my-phase"
    phase_dir.mkdir(parents=True)

    (phase_dir / "execution.md").write_text("not a shard\n")
    (phase_dir / "execution-task.md").write_text(
        "## Summary\n"
        "status: completed\n"
        "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
        "failures: none\n"
        "blockers: none\n"
    )

    result = merge_shards(milestone_dir, "my-phase")
    # Single shard -- returned as-is; the regular execution.md is not included
    assert "not a shard" not in result


# ---------------------------------------------------------------------------
# clean_stale_shards
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
# Hook permission matching
# ---------------------------------------------------------------------------


def test_hook_permission_shard() -> None:
    """Worker write permissions match execution-*.md shard files."""
    worker_perms = WRITE_PERMISSIONS["worker"]
    import fnmatch

    # Standard execution.md should match
    assert any(
        fnmatch.fnmatch("milestones/m1/phases/p1/execution.md", p)
        for p in worker_perms
    )

    # Shard files should match
    assert any(
        fnmatch.fnmatch("milestones/m1/phases/p1/execution-build.md", p)
        for p in worker_perms
    )
    assert any(
        fnmatch.fnmatch(
            "milestones/m1/phases/p1/execution-build-shard-infrastructure.md", p
        )
        for p in worker_perms
    )


def test_hook_permission_shard_scoped() -> None:
    """Scoped worker permissions (with milestone) also match shard files."""
    from clou.hooks import _scoped_permissions

    scoped = _scoped_permissions("worker", "17-runtime-safeguards")
    import fnmatch

    path = "milestones/17-runtime-safeguards/phases/p1/execution-task.md"
    assert any(fnmatch.fnmatch(path, p) for p in scoped)

    # Different milestone should NOT match
    wrong_path = "milestones/other/phases/p1/execution-task.md"
    assert not any(fnmatch.fnmatch(wrong_path, p) for p in scoped)


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


def test_hook_enforcement_allows_shard_write(tmp_path: Path) -> None:
    """PreToolUse hook allows worker to write to a shard file."""
    project_dir = tmp_path / "project"
    clou_dir = project_dir / ".clou"
    clou_dir.mkdir(parents=True)

    hooks = build_hooks("coordinator", project_dir, milestone="m1")
    pre_hook = hooks["PreToolUse"][0].hooks[0]

    shard_path = str(clou_dir / "milestones" / "m1" / "phases" / "p1" / "execution-build.md")
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
    assert not _is_denied(result)


def test_hook_enforcement_denies_wrong_milestone_shard(tmp_path: Path) -> None:
    """PreToolUse hook denies worker writing shard to wrong milestone."""
    project_dir = tmp_path / "project"
    clou_dir = project_dir / ".clou"
    clou_dir.mkdir(parents=True)

    hooks = build_hooks("coordinator", project_dir, milestone="m1")
    pre_hook = hooks["PreToolUse"][0].hooks[0]

    shard_path = str(clou_dir / "milestones" / "other" / "phases" / "p1" / "execution-build.md")
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
