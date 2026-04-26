"""Tests for clou.ui.task_graph -- pure data model for live task state."""

from __future__ import annotations

from clou.ui.task_graph import TaskGraphModel, TaskState, match_agent_to_task

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SAMPLE_TASKS = [
    {"name": "build_model", "status": "pending"},
    {"name": "build_widget", "status": "pending"},
    {"name": "add_interaction", "status": "pending"},
    {"name": "integrate", "status": "pending"},
]

_SAMPLE_DEPS: dict[str, list[str]] = {
    "build_model": [],
    "build_widget": ["build_model"],
    "add_interaction": ["build_widget"],
    "integrate": ["build_model", "build_widget", "add_interaction"],
}


def _make_model(
    tasks: list[dict[str, str]] | None = None,
    deps: dict[str, list[str]] | None = None,
) -> TaskGraphModel:
    return TaskGraphModel(
        tasks if tasks is not None else _SAMPLE_TASKS,
        deps if deps is not None else _SAMPLE_DEPS,
    )


# ---------------------------------------------------------------------------
# T1: Initialisation from DAG data
# ---------------------------------------------------------------------------


class TestInitFromDagData:
    def test_task_states_populated(self) -> None:
        model = _make_model()
        assert set(model.task_states.keys()) == {
            "build_model",
            "build_widget",
            "add_interaction",
            "integrate",
        }

    def test_all_tasks_start_pending(self) -> None:
        model = _make_model()
        for state in model.task_states.values():
            assert state.status == "pending"

    def test_deps_preserved(self) -> None:
        model = _make_model()
        assert model.deps["build_widget"] == ["build_model"]
        assert model.deps["build_model"] == []
        assert "add_interaction" in model.deps["integrate"]

    def test_empty_tasks(self) -> None:
        model = _make_model(tasks=[], deps={})
        assert model.task_states == {}
        assert model.layers == []


# ---------------------------------------------------------------------------
# T2: Layer computation
# ---------------------------------------------------------------------------


class TestLayerComputation:
    def test_layers_match_dependency_depth(self) -> None:
        model = _make_model()
        # build_model has no deps -> layer 0
        # build_widget depends on build_model -> layer 1
        # add_interaction depends on build_widget -> layer 2
        # integrate depends on all three -> layer 3
        assert len(model.layers) == 4
        assert "build_model" in model.layers[0]
        assert "build_widget" in model.layers[1]
        assert "add_interaction" in model.layers[2]
        assert "integrate" in model.layers[3]

    def test_parallel_tasks_same_layer(self) -> None:
        tasks = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
            {"name": "c", "status": "pending"},
        ]
        deps = {"a": [], "b": [], "c": ["a", "b"]}
        model = _make_model(tasks, deps)
        assert len(model.layers) == 2
        assert set(model.layers[0]) == {"a", "b"}
        assert model.layers[1] == ["c"]

    def test_single_task_no_deps(self) -> None:
        tasks = [{"name": "solo", "status": "pending"}]
        deps: dict[str, list[str]] = {"solo": []}
        model = _make_model(tasks, deps)
        assert model.layers == [["solo"]]


# ---------------------------------------------------------------------------
# T3: Exact match
# ---------------------------------------------------------------------------


class TestMatchExact:
    def test_exact_match(self) -> None:
        result = match_agent_to_task("build_model", ["build_model", "build_widget"])
        assert result == "build_model"

    def test_model_match_agent_exact(self) -> None:
        model = _make_model()
        assert model.match_agent("build_model") == "build_model"


# ---------------------------------------------------------------------------
# T4: Substring match
# ---------------------------------------------------------------------------


class TestMatchSubstring:
    def test_task_name_in_description(self) -> None:
        result = match_agent_to_task(
            "implement build_model task",
            ["build_model", "build_widget"],
        )
        assert result == "build_model"

    def test_description_in_task_name(self) -> None:
        result = match_agent_to_task(
            "model",
            ["build_model", "build_widget"],
        )
        assert result == "build_model"

    def test_case_insensitive_substring(self) -> None:
        result = match_agent_to_task(
            "Implement BUILD_MODEL Task",
            ["build_model", "build_widget"],
        )
        assert result == "build_model"


# ---------------------------------------------------------------------------
# T5: Word-overlap match
# ---------------------------------------------------------------------------


class TestMatchWordOverlap:
    def test_word_overlap_above_threshold(self) -> None:
        # "Build the data model" tokens: {build, the, data, model}
        # "build_model" tokens: {build, model}
        # intersection={build,model}, union={build,the,data,model}
        # Jaccard = 2/4 = 0.5 -- not > 0.5, so no match... let's use a
        # description with higher overlap.
        result = match_agent_to_task(
            "build model",
            ["build_model", "build_widget"],
        )
        # "build model" tokens: {build, model}
        # "build_model" tokens: {build, model}
        # Jaccard = 2/2 = 1.0
        assert result == "build_model"

    def test_word_overlap_with_extra_words(self) -> None:
        # "Build the model" -> {build, the, model}
        # "build_model" -> {build, model}
        # intersection = {build, model}, union = {build, the, model}
        # Jaccard = 2/3 = 0.667 > 0.5
        result = match_agent_to_task(
            "Build the model",
            ["add_interaction", "integrate"],
        )
        # Neither substring matches; word overlap:
        # vs "add_interaction" -> {add, interaction}: intersection = {} -> 0
        # vs "integrate" -> {integrate}: intersection = {} -> 0
        assert result is None

    def test_word_overlap_selects_best(self) -> None:
        # Jaccard = 2/4 = 0.5, not > 0.5 -- no match.
        below = match_agent_to_task(
            "build data model layer",
            ["build_model", "build_widget"],
        )
        assert below is None

        # Perfect word overlap selects the right task.
        result2 = match_agent_to_task(
            "build model",
            ["build_model", "add_interaction"],
        )
        # "build model" substring: "build_model" IS in "build model"? No.
        # "build_model".lower() = "build_model", in "build model"? No underscore.
        # "build model" in "build_model"? No space in task name.
        # Word overlap: {build, model} vs {build, model} = 1.0
        assert result2 == "build_model"


# ---------------------------------------------------------------------------
# T6: No match
# ---------------------------------------------------------------------------


class TestMatchNoMatch:
    def test_unrelated_description_returns_none(self) -> None:
        result = match_agent_to_task(
            "deploy to production server",
            ["build_model", "build_widget"],
        )
        assert result is None

    def test_empty_description(self) -> None:
        result = match_agent_to_task("", ["build_model"])
        assert result is None

    def test_empty_task_names(self) -> None:
        result = match_agent_to_task("build_model", [])
        assert result is None


# ---------------------------------------------------------------------------
# T7: Unmapped agents tracked separately
# ---------------------------------------------------------------------------


class TestUnmappedAgents:
    def test_unmapped_agent_stored(self) -> None:
        model = _make_model()
        # An agent that doesn't match any task.
        matched = model.match_agent("deploy production server")
        assert matched is None
        # The caller (integration code) would store it in unmapped_agents.
        model.unmapped_agents["agent-99"] = TaskState(
            status="active", agent_id="agent-99"
        )
        assert "agent-99" in model.unmapped_agents
        assert model.unmapped_agents["agent-99"].status == "active"


# ---------------------------------------------------------------------------
# T8: Status transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_pending_to_active(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        assert model.task_states["build_model"].status == "active"
        assert model.task_states["build_model"].agent_id == "agent-1"

    def test_active_to_complete(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.complete_task("build_model", "complete", "All tests pass")
        assert model.task_states["build_model"].status == "complete"
        assert model.task_states["build_model"].summary == "All tests pass"

    def test_active_to_failed(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.complete_task("build_model", "failed", "Syntax error")
        assert model.task_states["build_model"].status == "failed"
        assert model.task_states["build_model"].summary == "Syntax error"

    def test_unknown_task_ignored(self) -> None:
        model = _make_model()
        # Should not raise.
        model.activate_task("nonexistent", "agent-1")
        model.complete_task("nonexistent", "complete", "ok")


# ---------------------------------------------------------------------------
# T9: Progress updates
# ---------------------------------------------------------------------------


class TestProgressUpdates:
    def test_tool_count_and_last_tool_updated(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.update_progress("build_model", tool_count=5, last_tool="Read")
        state = model.task_states["build_model"]
        assert state.tool_count == 5
        assert state.last_tool == "Read"

    def test_progress_overwrites_previous(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.update_progress("build_model", tool_count=3, last_tool="Grep")
        model.update_progress("build_model", tool_count=7, last_tool="Edit")
        state = model.task_states["build_model"]
        assert state.tool_count == 7
        assert state.last_tool == "Edit"

    def test_unknown_task_ignored(self) -> None:
        model = _make_model()
        # Should not raise.
        model.update_progress("nonexistent", tool_count=1, last_tool="Read")


# ---------------------------------------------------------------------------
# T10: Tool call recording
# ---------------------------------------------------------------------------


class TestToolCallRecording:
    def test_tool_calls_appended(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "Read file.py")
        model.add_tool_call("build_model", "Edit", "Edit line 42")
        calls = model.task_states["build_model"].tool_calls
        assert len(calls) == 2
        assert calls[0] == ("Read", "Read file.py")
        assert calls[1] == ("Edit", "Edit line 42")

    def test_tool_calls_start_empty(self) -> None:
        model = _make_model()
        assert model.task_states["build_model"].tool_calls == []

    def test_unknown_task_ignored(self) -> None:
        model = _make_model()
        # Should not raise.
        model.add_tool_call("nonexistent", "Read", "Read file.py")


# ---------------------------------------------------------------------------
# T11: Aborted status
# ---------------------------------------------------------------------------


class TestAbortedStatus:
    def test_complete_task_aborted(self) -> None:
        """Task transitions to aborted status via complete_task."""
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.complete_task("build_model", "aborted", "Dependency failed")
        assert model.task_states["build_model"].status == "aborted"
        assert model.task_states["build_model"].summary == "Dependency failed"

    def test_aborted_distinct_from_failed(self) -> None:
        """Aborted and failed are separate terminal states."""
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.activate_task("build_widget", "agent-2")
        model.complete_task("build_model", "failed", "Crash")
        model.complete_task("build_widget", "aborted", "Sibling failed")
        assert model.task_states["build_model"].status == "failed"
        assert model.task_states["build_widget"].status == "aborted"
        assert model.task_states["build_model"].status != model.task_states["build_widget"].status


# ---------------------------------------------------------------------------
# T12: add_tool_call with output_summary
# ---------------------------------------------------------------------------


class TestAddToolCallOutputSummary:
    def test_output_summary_stored(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call(
            "build_model", "Read", "Read file.py", output_summary="42 lines"
        )
        assert inv is not None
        assert inv.output_summary == "42 lines"

    def test_output_summary_defaults_to_empty(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call("build_model", "Read", "Read file.py")
        assert inv is not None
        assert inv.output_summary == ""

    def test_backward_compatible_existing_callers(self) -> None:
        """Existing 3-arg calls continue to work without output_summary."""
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call("build_model", "Edit", "Edit line 42")
        assert inv is not None
        assert inv.input_summary == "Edit line 42"
        assert inv.output_summary == ""

    def test_output_summary_on_unknown_task(self) -> None:
        model = _make_model()
        result = model.add_tool_call(
            "nonexistent", "Read", "x", output_summary="data"
        )
        assert result is None

    def test_output_summary_persisted_in_invocation_list(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call(
            "build_model", "Read", "a.py", output_summary="contents of a.py"
        )
        model.add_tool_call("build_model", "Edit", "b.py")
        invocations = model.task_states["build_model"].tool_invocations
        assert invocations[0].output_summary == "contents of a.py"
        assert invocations[1].output_summary == ""


# ---------------------------------------------------------------------------
# T10: spawn_cycle field
# ---------------------------------------------------------------------------


class TestSpawnCycleField:
    """TaskState.spawn_cycle tags agents with their originating cycle."""

    def test_default_spawn_cycle_empty(self) -> None:
        state = TaskState()
        assert state.spawn_cycle == ""

    def test_spawn_cycle_set(self) -> None:
        state = TaskState(status="active", spawn_cycle="ASSESS")
        assert state.spawn_cycle == "ASSESS"

    def test_dag_tasks_default_spawn_cycle(self) -> None:
        model = _make_model()
        for state in model.task_states.values():
            assert state.spawn_cycle == ""
