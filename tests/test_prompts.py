"""Tests for prompt loading and cycle prompt construction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clou.prompts import _BUNDLED_PROMPTS, _compute_layers, build_cycle_prompt, load_prompt

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
    for tier in ("supervisor", "coordinator", "worker", "assessor", "assess-evaluator", "verifier"):
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


def test_build_cycle_prompt_only_project_md_is_root_scoped(tmp_path: Path) -> None:
    """Only exact 'project.md' gets the .clou/ prefix; variants go to milestone."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="Build",
        read_set=["project.md", "project.md.bak", "my-project.md"],
    )
    # Exact project.md → .clou/ root prefix
    assert "- .clou/project.md" in result
    # project.md.bak is NOT project.md → milestone prefix
    assert "- .clou/milestones/m01/project.md.bak" in result
    # my-project.md → milestone prefix
    assert "- .clou/milestones/m01/my-project.md" in result


def test_build_cycle_prompt_all_non_project_go_to_milestone(tmp_path: Path) -> None:
    """All read set entries except project.md resolve under the milestone."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="EXECUTE",
        read_set=["status.md", "compose.py"],
    )
    assert "- .clou/milestones/m01/status.md" in result
    assert "- .clou/milestones/m01/compose.py" in result


# ---------------------------------------------------------------------------
# Working tree state injection (DB-15 D6)
# ---------------------------------------------------------------------------


def test_working_tree_state_in_retry_prompt(tmp_path: Path) -> None:
    """Failed cycle's working tree state appears in retry prompt."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="EXECUTE",
        read_set=["status.md"],
        validation_errors=["missing '## Summary'"],
        working_tree_state="src/main.py | 10 +++++\n 1 file changed",
    )
    assert "ENVIRONMENT" in result
    assert "src/main.py" in result
    assert "previous failed cycle" in result


def test_working_tree_state_proactive(tmp_path: Path) -> None:
    """Proactive environment state for EXECUTE without validation errors."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="EXECUTE",
        read_set=["status.md"],
        working_tree_state="src/utils.py | 3 +++\n 1 file changed",
    )
    assert "ENVIRONMENT" in result
    assert "src/utils.py" in result
    # Should NOT mention "failed cycle" when no validation errors
    assert "failed cycle" not in result


def test_no_working_tree_state_when_clean(tmp_path: Path) -> None:
    """No environment section when working tree is clean."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="EXECUTE",
        read_set=["status.md"],
    )
    assert "ENVIRONMENT" not in result


# ---------------------------------------------------------------------------
# _compute_layers -- topological layer grouping
# ---------------------------------------------------------------------------


class TestComputeLayers:
    """Test the topological layer helper used for DAG dispatch context."""

    def test_single_task_no_deps(self) -> None:
        tasks = [{"name": "setup", "status": "pending"}]
        deps: dict[str, list[str]] = {"setup": []}
        layers = _compute_layers(tasks, deps)
        assert layers == [["setup"]]

    def test_two_independent_tasks(self) -> None:
        """Independent tasks land in the same layer (parallel)."""
        tasks = [
            {"name": "alpha", "status": "pending"},
            {"name": "beta", "status": "pending"},
        ]
        deps: dict[str, list[str]] = {"alpha": [], "beta": []}
        layers = _compute_layers(tasks, deps)
        assert layers == [["alpha", "beta"]]

    def test_linear_chain(self) -> None:
        """A -> B -> C produces three layers."""
        tasks = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
            {"name": "c", "status": "pending"},
        ]
        deps = {"a": [], "b": ["a"], "c": ["b"]}
        layers = _compute_layers(tasks, deps)
        assert layers == [["a"], ["b"], ["c"]]

    def test_diamond_graph(self) -> None:
        """Diamond: A -> {B, C} -> D."""
        tasks = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
            {"name": "c", "status": "pending"},
            {"name": "d", "status": "pending"},
        ]
        deps = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
        layers = _compute_layers(tasks, deps)
        assert layers == [["a"], ["b", "c"], ["d"]]

    def test_mixed_parallel_and_sequential(self) -> None:
        """Realistic compose.py graph with gather + sequential."""
        tasks = [
            {"name": "validation_scoping", "status": "pending"},
            {"name": "dag_dispatch_context", "status": "pending"},
            {"name": "integration_tests", "status": "pending"},
        ]
        deps = {
            "validation_scoping": [],
            "dag_dispatch_context": [],
            "integration_tests": ["validation_scoping", "dag_dispatch_context"],
        }
        layers = _compute_layers(tasks, deps)
        assert layers == [
            ["dag_dispatch_context", "validation_scoping"],
            ["integration_tests"],
        ]

    def test_empty_tasks(self) -> None:
        layers = _compute_layers([], {})
        assert layers == []

    def test_deterministic_ordering(self) -> None:
        """Tasks within a layer are sorted alphabetically for determinism."""
        tasks = [
            {"name": "zebra", "status": "pending"},
            {"name": "alpha", "status": "pending"},
            {"name": "mid", "status": "pending"},
        ]
        deps: dict[str, list[str]] = {"zebra": [], "alpha": [], "mid": []}
        layers = _compute_layers(tasks, deps)
        assert layers == [["alpha", "mid", "zebra"]]


# ---------------------------------------------------------------------------
# build_cycle_prompt -- DAG context integration (R5)
# ---------------------------------------------------------------------------


class TestBuildCyclePromptDagContext:
    """DAG data included in EXECUTE prompts for dispatch decisions."""

    @pytest.fixture
    def dag_data(
        self,
    ) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
        """A graph with both parallel and sequential tasks."""
        tasks = [
            {"name": "validation_scoping", "status": "pending"},
            {"name": "dag_dispatch_context", "status": "pending"},
            {"name": "integration_tests", "status": "pending"},
        ]
        deps = {
            "validation_scoping": [],
            "dag_dispatch_context": [],
            "integration_tests": ["validation_scoping", "dag_dispatch_context"],
        }
        return tasks, deps

    def test_execute_prompt_includes_dag_context(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """EXECUTE cycle with dag_data includes DAG Context section."""
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md"],
            dag_data=dag_data,
        )
        assert "## DAG Context" in result
        assert "do not re-derive from source" in result

        # Task names present
        assert "validation_scoping" in result
        assert "dag_dispatch_context" in result
        assert "integration_tests" in result

        # Dependencies present as JSON
        tasks_list, deps_dict = dag_data
        assert json.dumps(deps_dict) in result

        # Layers present as JSON
        expected_layers = [
            ["dag_dispatch_context", "validation_scoping"],
            ["integration_tests"],
        ]
        assert json.dumps(expected_layers) in result

    def test_no_dag_data_omits_dag_context(self, tmp_path: Path) -> None:
        """Without dag_data, no DAG Context section appears (backward compat)."""
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md"],
        )
        assert "DAG Context" not in result

    def test_non_execute_cycle_omits_dag_context(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """Non-EXECUTE cycles do not include DAG Context even if dag_data provided."""
        for cycle_type in ("PLAN", "ASSESS", "VERIFY"):
            result = build_cycle_prompt(
                project_dir=tmp_path,
                milestone="m01",
                cycle_type=cycle_type,
                read_set=["status.md"],
                dag_data=dag_data,
            )
            assert "DAG Context" not in result, f"DAG Context in {cycle_type} prompt"

    def test_dag_context_coexists_with_validation_errors(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """DAG context and validation errors can both appear in the same prompt."""
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md"],
            dag_data=dag_data,
            validation_errors=["missing ## Summary"],
        )
        assert "## DAG Context" in result
        assert "WARNING: Previous cycle produced malformed golden context." in result
        assert "missing ## Summary" in result
