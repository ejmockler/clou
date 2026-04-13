"""Tests for agent observability data layer.

Covers: ToolInvocation model, categorize_tool helper, MAX_TOOL_HISTORY bound,
updated add_tool_call returning ToolInvocation, backward-compat tool_calls
property, ClouToolCallRecorded + RequestAgentDetail messages.
"""

from __future__ import annotations

from clou.ui.messages import ClouToolCallRecorded, RequestAgentDetail
from clou.ui.task_graph import (
    MAX_TOOL_HISTORY,
    TaskGraphModel,
    TaskState,
    ToolInvocation,
    categorize_tool,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SAMPLE_TASKS = [
    {"name": "build_model", "status": "pending"},
    {"name": "build_widget", "status": "pending"},
]

_SAMPLE_DEPS: dict[str, list[str]] = {
    "build_model": [],
    "build_widget": ["build_model"],
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
# T1: ToolInvocation dataclass
# ---------------------------------------------------------------------------


class TestToolInvocation:
    def test_construction(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=1.0, category="reads")
        assert inv.name == "Read"
        assert inv.timestamp == 1.0
        assert inv.category == "reads"
        assert inv.input_summary == ""
        assert inv.output_summary == ""
        assert inv.duration_ms is None

    def test_defaults(self) -> None:
        inv = ToolInvocation(name="Agent", timestamp=0.0)
        assert inv.category == "other"
        assert inv.input_summary == ""
        assert inv.output_summary == ""
        assert inv.duration_ms is None

    def test_full_construction(self) -> None:
        inv = ToolInvocation(
            name="Bash",
            timestamp=5.0,
            category="shell",
            input_summary="ls -la",
            output_summary="total 42",
            duration_ms=150.0,
        )
        assert inv.name == "Bash"
        assert inv.category == "shell"
        assert inv.input_summary == "ls -la"
        assert inv.output_summary == "total 42"
        assert inv.duration_ms == 150.0


# ---------------------------------------------------------------------------
# T2: categorize_tool
# ---------------------------------------------------------------------------


class TestCategorizeTool:
    def test_reads(self) -> None:
        assert categorize_tool("Read") == "reads"
        assert categorize_tool("Glob") == "reads"

    def test_writes(self) -> None:
        assert categorize_tool("Edit") == "writes"
        assert categorize_tool("Write") == "writes"
        assert categorize_tool("MultiEdit") == "writes"
        assert categorize_tool("NotebookEdit") == "writes"

    def test_shell(self) -> None:
        assert categorize_tool("Bash") == "shell"

    def test_searches(self) -> None:
        assert categorize_tool("Grep") == "searches"
        assert categorize_tool("WebSearch") == "searches"
        assert categorize_tool("WebFetch") == "searches"

    def test_unknown_tool_returns_other(self) -> None:
        assert categorize_tool("Agent") == "other"
        assert categorize_tool("CustomTool") == "other"
        assert categorize_tool("") == "other"


# ---------------------------------------------------------------------------
# T3: MAX_TOOL_HISTORY constant
# ---------------------------------------------------------------------------


class TestMaxToolHistory:
    def test_constant_value(self) -> None:
        assert MAX_TOOL_HISTORY == 500

    def test_constant_is_int(self) -> None:
        assert isinstance(MAX_TOOL_HISTORY, int)


# ---------------------------------------------------------------------------
# T4: Updated add_tool_call returns ToolInvocation
# ---------------------------------------------------------------------------


class TestAddToolCallReturnsInvocation:
    def test_returns_invocation(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        result = model.add_tool_call("build_model", "Read", "Read file.py")
        assert result is not None
        assert isinstance(result, ToolInvocation)
        assert result.name == "Read"
        assert result.category == "reads"
        assert result.input_summary == "Read file.py"

    def test_returns_none_for_unknown_task(self) -> None:
        model = _make_model()
        result = model.add_tool_call("nonexistent", "Read", "x")
        assert result is None

    def test_invocation_has_timestamp(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call("build_model", "Bash", "run tests")
        assert inv is not None
        assert inv.timestamp > 0

    def test_invocation_stored_in_tool_invocations(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call("build_model", "Read", "f.py")
        state = model.task_states["build_model"]
        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0] is inv

    def test_multiple_invocations_ordered(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "a.py")
        model.add_tool_call("build_model", "Edit", "b.py")
        model.add_tool_call("build_model", "Bash", "test")
        state = model.task_states["build_model"]
        names = [inv.name for inv in state.tool_invocations]
        assert names == ["Read", "Edit", "Bash"]

    def test_category_auto_assigned(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call("build_model", "Grep", "search")
        assert inv is not None
        assert inv.category == "searches"

    def test_unmapped_agent_lookup(self) -> None:
        """add_tool_call should also check unmapped_agents."""
        model = _make_model()
        model.unmapped_agents["rogue-agent"] = TaskState(
            status="active", agent_id="rogue-agent"
        )
        inv = model.add_tool_call("rogue-agent", "Read", "x.py")
        assert inv is not None
        assert inv.name == "Read"
        assert len(model.unmapped_agents["rogue-agent"].tool_invocations) == 1


# ---------------------------------------------------------------------------
# T5: MAX_TOOL_HISTORY enforcement
# ---------------------------------------------------------------------------


class TestMaxToolHistoryBound:
    def test_truncation_at_boundary(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        for i in range(MAX_TOOL_HISTORY + 50):
            model.add_tool_call("build_model", "Read", f"file_{i}.py")
        state = model.task_states["build_model"]
        assert len(state.tool_invocations) == MAX_TOOL_HISTORY

    def test_preserves_most_recent(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        for i in range(MAX_TOOL_HISTORY + 10):
            model.add_tool_call("build_model", "Read", f"file_{i}.py")
        state = model.task_states["build_model"]
        # The oldest 10 should have been pruned; last entry should be the most recent.
        last_inv = state.tool_invocations[-1]
        assert last_inv.input_summary == f"file_{MAX_TOOL_HISTORY + 9}.py"
        first_inv = state.tool_invocations[0]
        assert first_inv.input_summary == "file_10.py"

    def test_below_limit_no_truncation(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        for i in range(10):
            model.add_tool_call("build_model", "Edit", f"file_{i}.py")
        state = model.task_states["build_model"]
        assert len(state.tool_invocations) == 10


# ---------------------------------------------------------------------------
# T6: Backward-compatible tool_calls property
# ---------------------------------------------------------------------------


class TestToolCallsBackwardCompat:
    def test_empty_invocations_returns_empty_list(self) -> None:
        state = TaskState()
        assert state.tool_calls == []

    def test_returns_tuples_from_invocations(self) -> None:
        state = TaskState()
        state.tool_invocations.append(
            ToolInvocation(name="Read", timestamp=1.0, category="reads", input_summary="a.py")
        )
        state.tool_invocations.append(
            ToolInvocation(name="Edit", timestamp=2.0, category="writes", input_summary="b.py")
        )
        calls = state.tool_calls
        assert calls == [("Read", "a.py"), ("Edit", "b.py")]

    def test_iteration_pattern_preserved(self) -> None:
        """The existing widget iterates with `for name, _ in state.tool_calls:`."""
        state = TaskState()
        state.tool_invocations.append(
            ToolInvocation(name="Bash", timestamp=1.0, category="shell")
        )
        for name, brief in state.tool_calls:
            assert name == "Bash"
            assert brief == ""

    def test_indexing_pattern_preserved(self) -> None:
        """The existing widget indexes with `tool_name, brief = state.tool_calls[idx]`."""
        state = TaskState()
        state.tool_invocations.append(
            ToolInvocation(name="Grep", timestamp=1.0, category="searches", input_summary="pattern")
        )
        tool_name, brief = state.tool_calls[0]
        assert tool_name == "Grep"
        assert brief == "pattern"

    def test_len_pattern_preserved(self) -> None:
        """The existing widget checks `len(state.tool_calls)`."""
        state = TaskState()
        assert len(state.tool_calls) == 0
        state.tool_invocations.append(
            ToolInvocation(name="Read", timestamp=1.0, category="reads")
        )
        assert len(state.tool_calls) == 1

    def test_truthiness_pattern_preserved(self) -> None:
        """The existing widget checks `if state.tool_calls:`."""
        state = TaskState()
        assert not state.tool_calls
        state.tool_invocations.append(
            ToolInvocation(name="Read", timestamp=1.0, category="reads")
        )
        assert state.tool_calls

    def test_via_model_add_tool_call(self) -> None:
        """End-to-end: model.add_tool_call populates backward compat."""
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call("build_model", "Read", "Read file.py")
        model.add_tool_call("build_model", "Edit", "Edit line 42")
        calls = model.task_states["build_model"].tool_calls
        assert len(calls) == 2
        assert calls[0] == ("Read", "Read file.py")
        assert calls[1] == ("Edit", "Edit line 42")


# ---------------------------------------------------------------------------
# T7: ClouToolCallRecorded message
# ---------------------------------------------------------------------------


class TestClouToolCallRecorded:
    def test_construction(self) -> None:
        inv = ToolInvocation(name="Read", timestamp=1.0, category="reads")
        msg = ClouToolCallRecorded(task_name="build_model", invocation=inv)
        assert msg.task_name == "build_model"
        assert msg.invocation is inv

    def test_invocation_typed_as_tool_invocation(self) -> None:
        """invocation parameter is typed as ToolInvocation."""
        inv = ToolInvocation(name="Edit", timestamp=2.0, category="writes")
        msg = ClouToolCallRecorded(task_name="t", invocation=inv)
        assert isinstance(msg.invocation, ToolInvocation)
        assert msg.invocation.name == "Edit"


# ---------------------------------------------------------------------------
# T8: RequestAgentDetail message
# ---------------------------------------------------------------------------


class TestRequestAgentDetail:
    def test_construction(self) -> None:
        msg = RequestAgentDetail(task_name="build_widget")
        assert msg.task_name == "build_widget"
