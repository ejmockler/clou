"""Tests for prompt loading and cycle prompt construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.prompts import _BUNDLED_PROMPTS, build_cycle_prompt, load_prompt

# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------


def test_load_prompt_reads_from_bundled_dir(tmp_path: Path) -> None:
    """load_prompt reads from the bundled _prompts/ directory, not per-project."""
    result = load_prompt("coordinator", tmp_path)
    # Should match the bundled coordinator-system.xml content.
    expected = (_BUNDLED_PROMPTS / "coordinator-system.xml").read_text()
    assert result == expected


def test_load_prompt_substitutes_variables(tmp_path: Path) -> None:
    result = load_prompt("coordinator", tmp_path, milestone="m01-auth")
    assert "m01-auth" in result
    assert "{{milestone}}" not in result


def test_load_prompt_leaves_unknown_placeholders(tmp_path: Path) -> None:
    """Placeholders not provided as kwargs are left as-is."""
    # The bundled coordinator template has {{milestone}} — pass nothing.
    result = load_prompt("coordinator", tmp_path)
    # milestone placeholder should remain since we didn't supply it
    assert "{{milestone}}" in result


def test_load_prompt_missing_tier_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent", Path("/tmp"))


def test_load_prompt_different_tiers(tmp_path: Path) -> None:
    for tier in ("supervisor", "coordinator", "worker", "assessor", "verifier"):
        result = load_prompt(tier, tmp_path)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# build_cycle_prompt
# ---------------------------------------------------------------------------


def test_build_cycle_prompt_basic(tmp_path: Path) -> None:
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01-auth",
        cycle_type="Build",
        read_set=["compose.py", "status.md"],
    )
    assert "This cycle: Build." in result
    assert "coordinator-build.md" in result
    assert "- .clou/milestones/m01-auth/compose.py" in result
    assert "- .clou/milestones/m01-auth/status.md" in result


def test_build_cycle_prompt_protocol_uses_absolute_path(tmp_path: Path) -> None:
    """Protocol file reference uses absolute path to bundled prompts."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="Build",
        read_set=[],
    )
    expected_path = str(_BUNDLED_PROMPTS / "coordinator-build.md")
    assert expected_path in result


def test_build_cycle_prompt_project_md_routing(tmp_path: Path) -> None:
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01-auth",
        cycle_type="Plan",
        read_set=["project.md", "spec.md"],
    )
    assert "- .clou/project.md" in result
    assert "- .clou/milestones/m01-auth/spec.md" in result


def test_build_cycle_prompt_no_validation_errors(tmp_path: Path) -> None:
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="Build",
        read_set=["compose.py"],
    )
    assert "WARNING" not in result


def test_build_cycle_prompt_with_validation_errors(tmp_path: Path) -> None:
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="Build",
        read_set=["compose.py"],
        validation_errors=["missing field: status", "invalid type for phase"],
    )
    assert "WARNING: Previous cycle produced malformed golden context." in result
    assert "  - missing field: status" in result
    assert "  - invalid type for phase" in result
    assert "conform to schema" in result


def test_build_cycle_prompt_protocol_file_uses_lowercase(tmp_path: Path) -> None:
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="BUILD",
        read_set=[],
    )
    assert "coordinator-build.md" in result


def test_build_cycle_prompt_empty_validation_errors(tmp_path: Path) -> None:
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="Build",
        read_set=["compose.py"],
        validation_errors=[],
    )
    assert "WARNING" not in result


def test_build_cycle_prompt_project_md_prefix_only(tmp_path: Path) -> None:
    """Only files starting with 'project.md' get the .clou/ prefix."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="Build",
        read_set=["project.md.bak", "my-project.md"],
    )
    # project.md.bak starts with "project.md" -> .clou/ prefix
    assert "- .clou/project.md.bak" in result
    # my-project.md does NOT start with "project.md" -> milestone prefix
    assert "- .clou/milestones/m01/my-project.md" in result


def test_build_cycle_prompt_active_coordinator_routing(tmp_path: Path) -> None:
    """active/coordinator.md routes to .clou/active/, not milestone prefix."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="EXECUTE",
        read_set=["status.md", "active/coordinator.md"],
    )
    assert "- .clou/active/coordinator.md" in result
    assert "- .clou/milestones/m01/status.md" in result
    # Must NOT produce the wrong path
    assert ".clou/milestones/m01/active/coordinator.md" not in result
