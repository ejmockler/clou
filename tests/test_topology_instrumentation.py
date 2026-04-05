"""Integration tests for the topology instrumentation pipeline.

Exercises the full path: compose.py -> graph.compute_topology -> telemetry
-> write_milestone_summary -> metrics.md.  Each test seeds a compose.py,
emits synthetic telemetry events, calls write_milestone_summary, and
asserts on the resulting metrics.md content.
"""

from __future__ import annotations

from pathlib import Path

from clou import telemetry
from clou.telemetry import (
    SpanLog,
    init,
    write_milestone_summary,
)


# NOTE: The ``from clou.telemetry import init`` binding above deliberately
# captures the *real* init function at import time, before the autouse
# ``_isolate_telemetry`` fixture in conftest.py monkeypatches it.  Tests in
# this file manage telemetry isolation themselves via ``setup_log()`` /
# ``finally`` blocks that save and restore ``telemetry._log``.

# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------


def write_compose(tmp_path: Path, milestone: str, source: str) -> None:
    """Place compose.py at tmp_path/.clou/milestones/{milestone}/compose.py."""
    compose_dir = tmp_path / ".clou" / "milestones" / milestone
    compose_dir.mkdir(parents=True, exist_ok=True)
    (compose_dir / "compose.py").write_text(source, encoding="utf-8")


def setup_log(tmp_path: Path) -> SpanLog | None:
    """Init telemetry and return the previous _log for teardown."""
    old = telemetry._log
    init("test-topo", tmp_path)
    return old


def emit_agent(
    milestone: str,
    cycle_num: int,
    task_id: str,
    description: str,
    *,
    status: str = "completed",
    tokens: int = 10000,
    tool_uses: int = 5,
) -> None:
    """Emit paired agent.start and agent.end events via telemetry.event().

    For orphaned agents (start without end), use emit_agent_start instead.
    Note: t_s is computed automatically by telemetry.event() from
    wall-clock time -- it cannot be overridden.
    """
    telemetry.event(
        "agent.start",
        milestone=milestone,
        cycle_num=cycle_num,
        task_id=task_id,
        description=description,
    )
    telemetry.event(
        "agent.end",
        milestone=milestone,
        cycle_num=cycle_num,
        task_id=task_id,
        status=status,
        total_tokens=tokens,
        tool_uses=tool_uses,
    )


def emit_agent_start(
    milestone: str,
    cycle_num: int,
    task_id: str,
    description: str,
) -> None:
    """Emit only agent.start (no agent.end) -- produces an orphaned agent."""
    telemetry.event(
        "agent.start",
        milestone=milestone,
        cycle_num=cycle_num,
        task_id=task_id,
        description=description,
    )


def emit_cycle(
    milestone: str,
    cycle_num: int,
    cycle_type: str,
    outcome: str,
    *,
    input_tokens: int = 5000,
    output_tokens: int = 1000,
) -> None:
    """Emit a cycle span via telemetry.span() context manager.

    The span records duration_ms automatically.  input_tokens and
    output_tokens are set on the yielded dict.
    """
    with telemetry.span(
        "cycle",
        milestone=milestone,
        cycle_num=cycle_num,
        cycle_type=cycle_type,
    ) as c:
        c["outcome"] = outcome
        c["input_tokens"] = input_tokens
        c["output_tokens"] = output_tokens


# ---------------------------------------------------------------------------
# Compose.py sources for test topologies
# ---------------------------------------------------------------------------

WIDE_GRAPH_SOURCE = """\
class A: ...
class B: ...
class C: ...
class Combined: ...

async def task_a() -> A:
    \"\"\"Task A.\"\"\"
async def task_b() -> B:
    \"\"\"Task B.\"\"\"
async def task_c() -> C:
    \"\"\"Task C.\"\"\"
async def combine(a: A, b: B, c: C) -> Combined:
    \"\"\"Combine results.\"\"\"

async def execute():
    a, b, c = await gather(task_a(), task_b(), task_c())
    result = await combine(a, b, c)
"""


LINEAR_CHAIN_SOURCE = """\
class A: ...
class B: ...
class C: ...

async def step_a() -> A:
    \"\"\"Step A.\"\"\"
async def step_b(a: A) -> B:
    \"\"\"Step B.\"\"\"
async def step_c(b: B) -> C:
    \"\"\"Step C.\"\"\"

async def execute():
    a = await step_a()
    b = await step_b(a)
    c = await step_c(b)
"""


SINGLE_TASK_SOURCE = """\
class Result: ...

async def only_task() -> Result:
    \"\"\"The only task.\"\"\"

async def execute():
    r = await only_task()
"""


DIAMOND_SOURCE = """\
class Start: ...
class Left: ...
class Right: ...
class End: ...

async def start_task() -> Start:
    \"\"\"Start.\"\"\"
async def left_branch(s: Start) -> Left:
    \"\"\"Left branch.\"\"\"
async def right_branch(s: Start) -> Right:
    \"\"\"Right branch.\"\"\"
async def end_task(l: Left, r: Right) -> End:
    \"\"\"End task.\"\"\"

async def execute():
    s = await start_task()
    l, r = await gather(left_branch(s), right_branch(s))
    e = await end_task(l, r)
"""


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestWideGraphPipeline:
    """Full pipeline test: wide graph with gather(task_a, task_b, task_c) -> combine."""

    def test_wide_graph_pipeline(self, tmp_path: Path) -> None:
        """Wide graph: gather(a,b,c) -> combine.

        Asserts metrics.md contains ## Topology with width=3, depth=2,
        and ## Per-Task Data table has all 4 tasks with correct layers.
        """
        old = setup_log(tmp_path)
        try:
            write_compose(tmp_path, "m1", WIDE_GRAPH_SOURCE)

            # Emit agent events for all 4 tasks.
            emit_agent("m1", 2, "a1", "task_a", tokens=8000, tool_uses=3)
            emit_agent("m1", 2, "a2", "task_b", tokens=9000, tool_uses=4)
            emit_agent("m1", 2, "a3", "task_c", tokens=7000, tool_uses=2)
            emit_agent("m1", 2, "a4", "combine", tokens=12000, tool_uses=6)

            # Emit cycle span.
            emit_cycle("m1", 2, "EXECUTE", "ASSESS")

            # Write the summary.
            write_milestone_summary(tmp_path, "m1", "completed")

            # Read metrics.md.
            metrics = tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # -- Topology section assertions --
            assert "## Topology" in content
            assert "width: 3" in content
            assert "depth: 2" in content
            assert "layer_count: 2" in content
            assert "gather_groups: [3]" in content

            # Layers: layer 0 has task_a, task_b, task_c; layer 1 has combine.
            assert "layers:" in content
            # The three tasks should be in one layer (alphabetical).
            assert '["task_a", "task_b", "task_c"]' in content
            assert '["combine"]' in content

            # -- Per-Task Data section assertions --
            assert "## Per-Task Data" in content
            assert "| Layer | Task | Duration | Tokens | Tools | Status |" in content

            # All 4 tasks present.
            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]
            assert "task_a" in table_content
            assert "task_b" in table_content
            assert "task_c" in table_content
            assert "combine" in table_content

            # Layer 0 tasks appear before layer 1 tasks.
            ta_pos = table_content.index("task_a")
            tb_pos = table_content.index("task_b")
            tc_pos = table_content.index("task_c")
            combine_pos = table_content.index("combine")
            assert ta_pos < combine_pos
            assert tb_pos < combine_pos
            assert tc_pos < combine_pos

            # Layer numbers: 0 for a/b/c, 1 for combine.
            assert "| 0 " in table_content
            assert "| 1 " in table_content

            # Token values should appear comma-formatted in the table.
            assert "8,000" in table_content, "task_a tokens (8000) missing"
            assert "9,000" in table_content, "task_b tokens (9000) missing"
            assert "7,000" in table_content, "task_c tokens (7000) missing"
            assert "12,000" in table_content, "combine tokens (12000) missing"

            # Duration should be >= 0 (t_s is auto-computed, so near zero).
            # Just verify the column exists and rows are present.
            lines = table_content.strip().split("\n")
            data_rows = [
                ln for ln in lines
                if ln.startswith("|") and "Layer" not in ln and "---" not in ln
            ]
            assert len(data_rows) == 4, f"Expected 4 task rows, got {len(data_rows)}"

        finally:
            telemetry._log = old


class TestLinearChainPipeline:
    """Pipeline test: linear chain a -> b -> c (fully serial)."""

    def test_linear_chain_pipeline(self, tmp_path: Path) -> None:
        """Linear chain: step_a -> step_b -> step_c.

        Asserts metrics.md contains ## Topology with width=1, depth=3,
        layers=[["step_a"],["step_b"],["step_c"]], gather_groups=[].
        Also asserts ## Per-Task Data has all 3 tasks with correct layers.
        """
        old = setup_log(tmp_path)
        try:
            write_compose(tmp_path, "m-linear", LINEAR_CHAIN_SOURCE)

            # Emit agent events for all 3 tasks.
            emit_agent("m-linear", 1, "l1", "step_a", tokens=5000, tool_uses=2)
            emit_agent("m-linear", 1, "l2", "step_b", tokens=6000, tool_uses=3)
            emit_agent("m-linear", 1, "l3", "step_c", tokens=7000, tool_uses=4)

            # Emit cycle span.
            emit_cycle("m-linear", 1, "EXECUTE", "ASSESS")

            # Write the summary.
            write_milestone_summary(tmp_path, "m-linear", "completed")

            # Read metrics.md.
            metrics = (
                tmp_path / ".clou" / "milestones" / "m-linear" / "metrics.md"
            )
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # -- Topology section assertions --
            assert "## Topology" in content
            assert "width: 1" in content
            assert "depth: 3" in content
            assert "layer_count: 3" in content
            assert "gather_groups: []" in content

            # Layers: each task in its own layer.
            assert "layers:" in content
            assert '["step_a"]' in content
            assert '["step_b"]' in content
            assert '["step_c"]' in content

            # -- Per-Task Data section assertions --
            assert "## Per-Task Data" in content
            assert "| Layer | Task | Duration | Tokens | Tools | Status |" in content

            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]

            # All 3 tasks present.
            assert "step_a" in table_content
            assert "step_b" in table_content
            assert "step_c" in table_content

            # Layer ordering: step_a (layer 0) before step_b (1) before step_c (2).
            sa_pos = table_content.index("step_a")
            sb_pos = table_content.index("step_b")
            sc_pos = table_content.index("step_c")
            assert sa_pos < sb_pos < sc_pos

            # Correct layer numbers.
            assert "| 0 " in table_content
            assert "| 1 " in table_content
            assert "| 2 " in table_content

            # Token values.
            assert "5,000" in table_content, "step_a tokens (5000) missing"
            assert "6,000" in table_content, "step_b tokens (6000) missing"
            assert "7,000" in table_content, "step_c tokens (7000) missing"

            # Correct row count.
            lines = table_content.strip().split("\n")
            data_rows = [
                ln for ln in lines
                if ln.startswith("|") and "Layer" not in ln and "---" not in ln
            ]
            assert len(data_rows) == 3, f"Expected 3 task rows, got {len(data_rows)}"

        finally:
            telemetry._log = old


class TestSingleTaskPipeline:
    """Pipeline test: single task only."""

    def test_single_task_pipeline(self, tmp_path: Path) -> None:
        """Single task: only_task.

        Asserts metrics.md contains ## Topology with width=1, depth=1,
        layers=[["only_task"]], gather_groups=[].
        Also asserts ## Per-Task Data has the one task row.
        """
        old = setup_log(tmp_path)
        try:
            write_compose(tmp_path, "m-single", SINGLE_TASK_SOURCE)

            # Emit agent events for the single task.
            emit_agent("m-single", 1, "s1", "only_task", tokens=4000, tool_uses=1)

            # Emit cycle span.
            emit_cycle("m-single", 1, "EXECUTE", "ASSESS")

            # Write the summary.
            write_milestone_summary(tmp_path, "m-single", "completed")

            # Read metrics.md.
            metrics = (
                tmp_path / ".clou" / "milestones" / "m-single" / "metrics.md"
            )
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # -- Topology section assertions --
            assert "## Topology" in content
            assert "width: 1" in content
            assert "depth: 1" in content
            assert "layer_count: 1" in content
            assert "gather_groups: []" in content

            # Layers: single layer with one task.
            assert "layers:" in content
            assert '["only_task"]' in content

            # -- Per-Task Data section assertions --
            assert "## Per-Task Data" in content
            assert "| Layer | Task | Duration | Tokens | Tools | Status |" in content

            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]

            # Task present.
            assert "only_task" in table_content

            # Layer 0.
            assert "| 0 " in table_content

            # Token value.
            assert "4,000" in table_content, "only_task tokens (4000) missing"

            # Single data row.
            lines = table_content.strip().split("\n")
            data_rows = [
                ln for ln in lines
                if ln.startswith("|") and "Layer" not in ln and "---" not in ln
            ]
            assert len(data_rows) == 1, f"Expected 1 task row, got {len(data_rows)}"

        finally:
            telemetry._log = old


class TestDiamondPipeline:
    """Pipeline test: diamond pattern a -> gather(b, c) -> d."""

    def test_diamond_pipeline(self, tmp_path: Path) -> None:
        """Diamond: start_task -> gather(left_branch, right_branch) -> end_task.

        Asserts metrics.md contains ## Topology with width=2, depth=3,
        gather_groups=[2].  Per-Task Data has all 4 tasks with correct
        layer assignments: start_task=0, branches=1, end_task=2.
        """
        old = setup_log(tmp_path)
        try:
            write_compose(tmp_path, "m-diamond", DIAMOND_SOURCE)

            # Emit agent events for all 4 tasks.
            emit_agent(
                "m-diamond", 1, "d1", "start_task", tokens=3000, tool_uses=1,
            )
            emit_agent(
                "m-diamond", 1, "d2", "left_branch", tokens=5000, tool_uses=2,
            )
            emit_agent(
                "m-diamond", 1, "d3", "right_branch", tokens=5000, tool_uses=2,
            )
            emit_agent(
                "m-diamond", 1, "d4", "end_task", tokens=8000, tool_uses=4,
            )

            # Emit cycle span.
            emit_cycle("m-diamond", 1, "EXECUTE", "ASSESS")

            # Write the summary.
            write_milestone_summary(tmp_path, "m-diamond", "completed")

            # Read metrics.md.
            metrics = (
                tmp_path / ".clou" / "milestones" / "m-diamond" / "metrics.md"
            )
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # -- Topology section assertions --
            assert "## Topology" in content
            assert "width: 2" in content
            assert "depth: 3" in content
            assert "layer_count: 3" in content
            assert "gather_groups: [2]" in content

            # Layers.
            assert "layers:" in content
            assert '["start_task"]' in content
            assert '["left_branch", "right_branch"]' in content
            assert '["end_task"]' in content

            # -- Per-Task Data section assertions --
            assert "## Per-Task Data" in content
            assert "| Layer | Task | Duration | Tokens | Tools | Status |" in content

            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]

            # All 4 tasks present.
            assert "start_task" in table_content
            assert "left_branch" in table_content
            assert "right_branch" in table_content
            assert "end_task" in table_content

            # Layer ordering: start(0) before branches(1) before end(2).
            start_pos = table_content.index("start_task")
            left_pos = table_content.index("left_branch")
            right_pos = table_content.index("right_branch")
            end_pos = table_content.index("end_task")
            assert start_pos < left_pos
            assert start_pos < right_pos
            assert left_pos < end_pos
            assert right_pos < end_pos

            # Correct layer numbers: 0, 1, and 2 all present.
            assert "| 0 " in table_content
            assert "| 1 " in table_content
            assert "| 2 " in table_content

            # Token values.
            assert "3,000" in table_content, "start_task tokens (3000) missing"
            assert "5,000" in table_content, "branch tokens (5000) missing"
            assert "8,000" in table_content, "end_task tokens (8000) missing"

            # Correct row count.
            lines = table_content.strip().split("\n")
            data_rows = [
                ln for ln in lines
                if ln.startswith("|") and "Layer" not in ln and "---" not in ln
            ]
            assert len(data_rows) == 4, f"Expected 4 task rows, got {len(data_rows)}"

        finally:
            telemetry._log = old
