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
# M36 I1 — ORIENT cycle prompt (observation-first prefix)
# ---------------------------------------------------------------------------


def test_orient_prompt_loads_protocol_file(tmp_path: Path) -> None:
    """ORIENT cycle prompt points at coordinator-orient.md and the content
    comes from the bundled protocol file shape."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="ORIENT",
        read_set=[
            "intents.md", "status.md", "active/git-diff-stat.txt",
        ],
        cycle_num=1,
    )
    # Protocol file path in prompt points at the bundled ORIENT file.
    expected_protocol = str(_BUNDLED_PROMPTS / "coordinator-orient.md")
    assert expected_protocol in result
    # And the file actually exists (the plumbing ships the prompt).
    assert (_BUNDLED_PROMPTS / "coordinator-orient.md").exists()


def test_orient_protocol_file_has_cycle_wrapper() -> None:
    """coordinator-orient.md follows the <cycle type="ORIENT"> shape."""
    text = (_BUNDLED_PROMPTS / "coordinator-orient.md").read_text()
    assert '<cycle type="ORIENT">' in text
    assert "</cycle>" in text
    assert "<objective>" in text
    assert "<procedure>" in text
    # The procedure must reference the MCP judgment-writer tool.
    assert "clou_write_judgment" in text


def test_orient_prompt_write_paths_contain_judgment_file(
    tmp_path: Path,
) -> None:
    """ORIENT write_paths names the cycle-specific judgment file."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="ORIENT",
        read_set=["intents.md"],
        cycle_num=3,
    )
    # Cycle-03 judgment path (zero-padded two digits) under the milestone.
    assert ".clou/milestones/m01/judgments/cycle-03-judgment.md" in result


def test_orient_prompt_write_paths_pad_cycle_number(tmp_path: Path) -> None:
    """Cycle numbers less than 10 are zero-padded (cycle-01, cycle-09)."""
    for n in (1, 7, 9):
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="ORIENT",
            read_set=["intents.md"],
            cycle_num=n,
        )
        assert f"judgments/cycle-{n:02d}-judgment.md" in result


def test_orient_prompt_write_paths_exclude_checkpoint_and_status(
    tmp_path: Path,
) -> None:
    """ORIENT does not write the checkpoint or status.md; those paths
    must be absent from the write_paths list so the permission model
    stays honest."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="ORIENT",
        read_set=["intents.md"],
        cycle_num=1,
    )
    # The "Write your state to these exact paths:" section must not list
    # the checkpoint file or status.md for ORIENT.
    write_section = result.split(
        "Write your state to these exact paths:", 1
    )[1].split("Execute the")[0]
    assert "active/coordinator.md" not in write_section
    assert "status.md  (progress journal)" not in write_section
    # Judgment file is the only write path.
    assert "judgments/cycle-01-judgment.md" in write_section


def test_orient_prompt_references_mcp_writer(tmp_path: Path) -> None:
    """ORIENT write path line names the MCP writer tool so the
    coordinator cannot miss the routing."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="ORIENT",
        read_set=["intents.md"],
        cycle_num=2,
    )
    assert "clou_write_judgment" in result


def test_orient_prompt_read_set_formatting(tmp_path: Path) -> None:
    """Read-set file listing uses the same milestone-prefix shape as the
    other cycle types — no special-casing ORIENT's read-set rendering."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="ORIENT",
        read_set=[
            "intents.md",
            "status.md",
            "phases/p1/execution.md",
            "active/git-diff-stat.txt",
        ],
        cycle_num=1,
    )
    assert "- .clou/milestones/m01/intents.md" in result
    assert "- .clou/milestones/m01/status.md" in result
    assert "- .clou/milestones/m01/phases/p1/execution.md" in result
    assert "- .clou/milestones/m01/active/git-diff-stat.txt" in result


def test_orient_prompt_fallback_template_when_no_cycle_num(
    tmp_path: Path,
) -> None:
    """Without cycle_num, the write-path shape still mentions
    judgments/cycle-XX-judgment.md (template form) so readers know the
    destination filename shape. The MCP tool formats the real path
    from its own cycle argument at write time."""
    result = build_cycle_prompt(
        project_dir=tmp_path,
        milestone="m01",
        cycle_type="ORIENT",
        read_set=["intents.md"],
    )
    assert "judgments/cycle-" in result


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

    def test_execute_prompt_includes_intent_mapping(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """EXECUTE cycle includes intent mapping when compose.py has docstrings."""
        # Create compose.py with intent IDs in docstrings.
        ms_dir = tmp_path / ".clou" / "milestones" / "m01"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text(
            'async def validation_scoping():\n'
            '    """Scope validation rules. I1 I2"""\n'
            '    pass\n'
            '\n'
            'async def dag_dispatch_context():\n'
            '    """Add DAG context. I3"""\n'
            '    pass\n'
        )
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md"],
            dag_data=dag_data,
        )
        assert "Intent mapping:" in result
        assert '"validation_scoping": ["I1", "I2"]' in result
        assert '"dag_dispatch_context": ["I3"]' in result

    def test_assess_prompt_includes_intent_mapping(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """ASSESS cycle includes intent mapping for per-intent evaluation."""
        ms_dir = tmp_path / ".clou" / "milestones" / "m01"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text(
            'async def validation_scoping():\n'
            '    """Scope validation rules. I1 I2"""\n'
            '    pass\n'
            '\n'
            'async def dag_dispatch_context():\n'
            '    """Add DAG context. I3"""\n'
            '    pass\n'
        )
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="ASSESS",
            read_set=["status.md"],
            dag_data=dag_data,
        )
        # ASSESS should NOT get DAG Context section.
        assert "DAG Context" not in result
        # But ASSESS SHOULD get intent mapping.
        assert "Intent mapping:" in result
        assert '"validation_scoping": ["I1", "I2"]' in result
        assert '"dag_dispatch_context": ["I3"]' in result

    def test_assess_prompt_no_intent_mapping_without_compose(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """ASSESS cycle without compose.py omits intent mapping."""
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="ASSESS",
            read_set=["status.md"],
            dag_data=dag_data,
        )
        assert "Intent mapping:" not in result

    def test_plan_prompt_no_intent_mapping(
        self,
        tmp_path: Path,
        dag_data: tuple[list[dict[str, str]], dict[str, list[str]]],
    ) -> None:
        """PLAN and VERIFY cycles do not include intent mapping."""
        ms_dir = tmp_path / ".clou" / "milestones" / "m01"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text(
            'async def some_task():\n'
            '    """Does stuff. I1"""\n'
            '    pass\n'
        )
        for cycle_type in ("PLAN", "VERIFY"):
            result = build_cycle_prompt(
                project_dir=tmp_path,
                milestone="m01",
                cycle_type=cycle_type,
                read_set=["status.md"],
                dag_data=dag_data,
            )
            assert "Intent mapping:" not in result, f"Intent mapping in {cycle_type}"

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


# ---------------------------------------------------------------------------
# Canonical execution.md path generation — post-M17, post-slug-drift remolding.
# Sharded execution-{task_slug}.md paths were dropped because the slug was
# LLM-freeformed and drifted across cycles, leaving orphans that broke
# validation.  Now every phase has exactly one execution.md regardless of
# gather()-group membership.
# ---------------------------------------------------------------------------


class TestCanonicalExecutionPaths:
    """EXECUTE prompts list phase-level execution.md paths, not slugged shards."""

    def test_execute_prompt_gather_layer_lists_one_execution_per_phase(
        self, tmp_path: Path,
    ) -> None:
        """gather() layers list execution.md per phase, no shard suffix."""
        tasks = [
            {"name": "build_a", "status": "pending"},
            {"name": "build_b", "status": "pending"},
            {"name": "integrate", "status": "pending"},
        ]
        deps: dict[str, list[str]] = {
            "build_a": [],
            "build_b": [],
            "integrate": ["build_a", "build_b"],
        }
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md", "compose.py"],
            dag_data=(tasks, deps),
            current_phase="build_a",
        )
        # Both phases in the co-layer have their execution.md listed.
        assert "phases/build_a/execution.md" in result
        assert "phases/build_b/execution.md" in result
        # The old per-task-shard form is gone.
        assert "execution-{task}.md" not in result
        assert "per-task shards for gather() groups" not in result

    def test_execute_prompt_serial_no_shards(self, tmp_path: Path) -> None:
        """Serial layers (all single-task) produce no shard write paths."""
        tasks = [
            {"name": "step_a", "status": "pending"},
            {"name": "step_b", "status": "pending"},
        ]
        deps: dict[str, list[str]] = {"step_a": [], "step_b": ["step_a"]}
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md", "compose.py"],
            dag_data=(tasks, deps),
            current_phase="design",
        )
        # Standard execution.md present.
        assert "phases/design/execution.md" in result
        # No shard pattern -- all layers have exactly 1 task.
        assert "execution-{task}.md" not in result

    def test_execute_prompt_no_dag_data_no_shards(self, tmp_path: Path) -> None:
        """Without dag_data (narrow/legacy), no shard paths appear."""
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="EXECUTE",
            read_set=["status.md"],
            current_phase="impl",
        )
        assert "execution.md" in result
        assert "execution-{task}.md" not in result

    def test_assess_prompt_with_shards(self, tmp_path: Path) -> None:
        """ASSESS read set includes shard files when present on disk.

        This tests the full pipeline: recovery.determine_next_cycle
        discovers shards, and the read_set flows into build_cycle_prompt.
        """
        from clou.recovery import determine_next_cycle

        # Set up milestone directory structure with checkpoint and shards.
        ms_dir = tmp_path / "milestones" / "m01"
        cp_path = ms_dir / "active" / "coordinator.md"
        phase_dir = ms_dir / "phases" / "impl"
        phase_dir.mkdir(parents=True)
        cp_path.parent.mkdir(parents=True)
        cp_path.write_text(
            "cycle: 3\nstep: EXECUTE\nnext_step: ASSESS\ncurrent_phase: impl\n"
        )
        # Write execution.md (merged) and two shard files.
        (phase_dir / "execution.md").write_text("## Summary\nstatus: completed\n")
        (phase_dir / "execution-build-a.md").write_text("## Summary\nstatus: completed\n")
        (phase_dir / "execution-build-b.md").write_text("## Summary\nstatus: completed\n")

        cycle_type, read_set = determine_next_cycle(cp_path, "m01")
        assert cycle_type == "ASSESS"
        assert "phases/impl/execution.md" in read_set
        assert "phases/impl/execution-build-a.md" in read_set
        assert "phases/impl/execution-build-b.md" in read_set

        # Now feed into build_cycle_prompt and verify the prompt text.
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m01",
            cycle_type="ASSESS",
            read_set=read_set,
            current_phase="impl",
        )
        assert "execution-build-a.md" in result
        assert "execution-build-b.md" in result


# ---------------------------------------------------------------------------
# Zero-escalations routing: prompt content assertions (F1/F2).
#
# These tests protect against silent regressions of the routing rule:
# a future edit that removes `clou_propose_milestone` references from
# prompts, or re-introduces "write escalation" as the default for
# architectural findings, would break one of these assertions.  They
# are golden-file assertions that codify the routing contract in the
# test suite so it survives prompt edits by reviewers who miss the
# policy context.  Per the brutalist C3-review 3-of-3 finding: without
# content assertions, the proposal infrastructure depends entirely on
# prompt text that any edit could silently revert.
# ---------------------------------------------------------------------------


class TestZeroEscalationsRouting:
    """Prompts route architectural findings to proposals by default."""

    def _read_bundled(self, name: str) -> str:
        return (_BUNDLED_PROMPTS / name).read_text()

    def test_coordinator_system_invariant_names_propose_tool(self) -> None:
        """The coordinator-system.xml invariants must reference
        clou_propose_milestone so the coordinator's identity/role
        prompt (always loaded) carries the routing rule.
        """
        text = self._read_bundled("coordinator-system.xml")
        assert "clou_propose_milestone" in text
        assert "Escalations are rare" in text

    def test_coordinator_assess_advertises_proposal_default(self) -> None:
        text = self._read_bundled("coordinator-assess.md")
        # The routing-rule block must be present.
        assert "clou_propose_milestone" in text
        assert "clou_file_escalation" in text
        # Architectural default is proposal, not escalation.
        assert "Propose follow-up milestone" in text

    def test_coordinator_assess_has_no_unqualified_write_escalation(
        self,
    ) -> None:
        """The classification table must not advertise "Write escalation"
        as the architectural action without qualification.  The rule
        requires coordinators to choose propose vs escalate, not default
        to escalate.
        """
        text = self._read_bundled("coordinator-assess.md")
        # The specific legacy phrasing we replaced.
        assert "| architectural  | Write escalation" not in text

    def test_coordinator_verify_has_proposal_routing(self) -> None:
        """VERIFY cycle also routes cross-cutting findings to proposals
        (brutalist flagged this file was missed in the first sweep).
        """
        text = self._read_bundled("coordinator-verify.md")
        assert "clou_propose_milestone" in text

    def test_assess_evaluator_has_proposal_routing(self) -> None:
        text = self._read_bundled("assess-evaluator.md")
        assert "clou_propose_milestone" in text
        assert "Propose follow-up milestone" in text

    def test_supervisor_reads_proposals_on_startup(self) -> None:
        """Consumer-starvation closure: supervisor prompt must tell
        the supervisor to call clou_list_proposals.  Without this,
        proposals accumulate unread.
        """
        text = self._read_bundled("supervisor.md")
        assert "clou_list_proposals" in text
        # And one of the disposition paths names the disposition tool.
        assert "clou_dispose_proposal" in text

    def test_file_escalation_tool_description_discourages_architectural(
        self,
    ) -> None:
        """The clou_file_escalation tool description must point
        architectural findings at clou_propose_milestone; otherwise
        the LLM picks the escalation tool because its schema still
        looks architectural.
        """
        # We inspect the tool list built for the coordinator.  A unit
        # test covers the full schema in test_coordinator_tools.py;
        # here we just assert the text advertises the routing rule.
        import pytest
        pytest.importorskip("claude_agent_sdk")
        from clou.coordinator_tools import _build_coordinator_tools

        tools = _build_coordinator_tools(Path("/tmp"), "ms")
        file_esc = next(
            t for t in tools if getattr(t, "name", "") == "clou_file_escalation"
        )
        description = getattr(file_esc, "description", "") or ""
        # The description should name the proposal tool as the
        # architectural route, and mark escalations as fallback.
        assert "clou_propose_milestone" in description
        assert "in-milestone" in description.lower()

    def test_project_local_coordinator_assess_matches_bundled_routing(
        self, tmp_path: Path,
    ) -> None:
        """Split-brain guard: the project-local .clou/prompts/ mirror
        (the file coordinators actually read at runtime via the
        cycle-prompt pointer) must carry the same proposal routing
        as the bundled version.  Without this, coordinators read
        stale protocol while orchestrator loads the new one.
        """
        # This is CLOU's own project; check in-tree not tmp_path.
        project_local = Path(__file__).resolve().parents[1] / ".clou" / "prompts" / "coordinator-assess.md"
        if not project_local.exists():
            pytest.skip("no project-local .clou/prompts/ in this checkout")
        text = project_local.read_text()
        assert "clou_propose_milestone" in text, (
            f"project-local coordinator-assess.md missing propose tool "
            f"reference at {project_local}"
        )


# ---------------------------------------------------------------------------
# M50 I1: vocabulary canonicalization in prompt copy
# ---------------------------------------------------------------------------


class TestPromptCopyUsesStructuredCycleTokens:
    """The bundled prompt copy displays structured cycle-type tokens.

    M50 I1: ``EXECUTE (rework)`` / ``EXECUTE (additional verification)``
    were renamed to ``EXECUTE_REWORK`` / ``EXECUTE_VERIFY``.  The
    routing-copy bullets in coordinator-assess.md and
    coordinator-verify.md must show the structured tokens verbatim
    (these are the strings the LLM types into the checkpoint and
    that ``parse_checkpoint`` then validates).

    The legacy punctuated forms are tolerated only in two places:
    (a) the ``_LEGACY_NEXT_STEPS`` mapping data in
    ``recovery_checkpoint.py`` and the migration helper itself, and
    (b) inline documentation in coordinator-orient.md that explains
    the rename.  Neither of those is a routing-copy surface.
    """

    @pytest.mark.parametrize("filename", [
        "coordinator-assess.md",
        "coordinator-verify.md",
    ])
    def test_routing_prompt_displays_structured_rework_token(
        self, filename: str,
    ) -> None:
        """Routing copy in ASSESS / VERIFY uses ``EXECUTE_REWORK``."""
        text = (_BUNDLED_PROMPTS / filename).read_text()
        assert "EXECUTE_REWORK" in text, (
            f"{filename} should display the structured "
            f"EXECUTE_REWORK token in its routing copy"
        )

    def test_verify_routing_prompt_displays_structured_verify_token(
        self,
    ) -> None:
        """coordinator-verify.md uses ``EXECUTE_VERIFY`` (additional pass)."""
        text = (_BUNDLED_PROMPTS / "coordinator-verify.md").read_text()
        assert "EXECUTE_VERIFY" in text

    @pytest.mark.parametrize("filename", [
        "coordinator-assess.md",
        "coordinator-verify.md",
    ])
    def test_routing_prompt_does_not_use_legacy_punctuated_token(
        self, filename: str,
    ) -> None:
        """Routing copy never instructs the LLM to type the legacy form."""
        text = (_BUNDLED_PROMPTS / filename).read_text()
        assert "EXECUTE (rework)" not in text, (
            f"{filename} contains the legacy punctuated token; "
            f"the routing copy must use EXECUTE_REWORK"
        )
        assert "EXECUTE (additional verification)" not in text, (
            f"{filename} contains the legacy punctuated token; "
            f"the routing copy must use EXECUTE_VERIFY"
        )

    def test_orient_prompt_documents_legacy_rejection(self) -> None:
        """coordinator-orient.md retains a doc note that the legacy
        punctuated forms are rejected at parse time.

        This is the ONE intentional reference to the legacy tokens
        in the bundled prompts -- it informs the coordinator LLM why
        the validator will refuse a paraphrased checkpoint write.
        """
        text = (_BUNDLED_PROMPTS / "coordinator-orient.md").read_text()
        assert "EXECUTE_REWORK" in text
        assert "EXECUTE_VERIFY" in text
        assert "rejected" in text.lower()


# ---------------------------------------------------------------------------
# Structural existence: every dispatchable cycle-type has a resolvable
# protocol file.  M50 I1 cycle-4 rework (F5):
# ``build_cycle_prompt`` derives ``coordinator-{cycle_type.lower()}.md``
# for non-EXECUTE-family cycles.  When cycle-2/3 preserved the
# structured EXECUTE tokens (``EXECUTE_REWORK`` / ``EXECUTE_VERIFY``)
# through ``determine_next_cycle``, the naive lower-casing produced
# ``coordinator-execute_rework.md`` and ``coordinator-execute_verify.md``
# — filenames that do NOT exist under ``clou/_prompts/``.  The fix
# routes every EXECUTE-family token through the ``execute`` protocol
# stem via ``is_execute_family``.  This test pins the runtime contract
# that every dispatchable cycle type resolves to a real prompt file on
# disk, so the class of defect cannot return by reintroducing a new
# structured token without a matching routing rule.
# ---------------------------------------------------------------------------


class TestEveryDispatchableCycleHasProtocolFile:
    """For every dispatchable cycle-type token in
    :data:`recovery_checkpoint._VALID_NEXT_STEPS`, the protocol-file
    path that :func:`build_cycle_prompt` embeds in the cycle prompt
    must point to an existing file under ``clou/_prompts/``.

    ``COMPLETE`` and ``HALTED`` are not dispatchable — they signal
    milestone termination / engine halt, so the orchestrator does not
    build a cycle prompt for them.  Every other token in the set
    triggers a ``build_cycle_prompt`` call at runtime.
    """

    def test_every_dispatchable_cycle_type_resolves_to_existing_protocol_file(
        self, tmp_path: Path,
    ) -> None:
        from clou.recovery_checkpoint import _VALID_NEXT_STEPS

        # Terminal / engine-halt tokens do not dispatch a cycle, so
        # ``build_cycle_prompt`` is never invoked for them.
        dispatchable = _VALID_NEXT_STEPS - {"COMPLETE", "HALTED"}

        missing: list[tuple[str, str]] = []
        for token in sorted(dispatchable):
            prompt_text = build_cycle_prompt(
                project_dir=tmp_path,
                milestone="m-probe",
                cycle_type=token,
                read_set=[],
            )
            # Extract the protocol-file path the prompt tells the
            # coordinator to read.  It is a single ``- /abs/path`` line
            # under the "Read your protocol file first:" header.
            marker = "Read your protocol file first:\n- "
            assert marker in prompt_text, (
                f"build_cycle_prompt({token!r}) did not emit the "
                f"expected protocol-file marker; prompt shape changed"
            )
            after = prompt_text.split(marker, 1)[1]
            path_str = after.split("\n", 1)[0].strip()
            protocol_path = Path(path_str)
            if not protocol_path.exists():
                missing.append((token, path_str))

        assert not missing, (
            "Every dispatchable cycle-type token must resolve to an "
            "existing bundled protocol file.  Missing:\n"
            + "\n".join(f"  - {t} -> {p}" for t, p in missing)
        )

    @pytest.mark.parametrize(
        "cycle_type",
        ["EXECUTE", "EXECUTE_REWORK", "EXECUTE_VERIFY"],
    )
    def test_execute_family_tokens_route_to_execute_protocol_stem(
        self, cycle_type: str, tmp_path: Path,
    ) -> None:
        """All EXECUTE-family tokens share ``coordinator-execute.md``.

        The three tokens carry distinct telemetry/dispatch semantics
        (plain execute, post-ASSESS rework, post-VERIFY additional
        pass) but they all run the same EXECUTE protocol — only the
        discriminator differs.  Regression guard against a future
        change that reintroduces ``coordinator-execute_rework.md``.
        """
        result = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m-probe",
            cycle_type=cycle_type,
            read_set=[],
        )
        assert "coordinator-execute.md" in result
        assert "coordinator-execute_rework.md" not in result
        assert "coordinator-execute_verify.md" not in result
