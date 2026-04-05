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

            # Status values: all tasks should show "completed".
            assert table_content.count("completed") >= 3, (
                "expected at least 3 'completed' status values in Per-Task Data"
            )

            # Tool uses values: verify each task's tool_uses count appears.
            assert "| 2 |" in table_content, "step_a tool_uses (2) missing"
            assert "| 3 |" in table_content, "step_b tool_uses (3) missing"
            assert "| 4 |" in table_content, "step_c tool_uses (4) missing"

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

            # Status value: the single task should show "completed".
            assert "completed" in table_content, (
                "expected 'completed' status in Per-Task Data"
            )

            # Tool uses value: verify only_task's tool_uses count (1) appears.
            assert "| 1 |" in table_content, "only_task tool_uses (1) missing"

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
                "m-diamond", 1, "d3", "right_branch", tokens=6000, tool_uses=3,
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

            # Token values -- each branch has distinct tokens.
            assert "3,000" in table_content, "start_task tokens (3000) missing"
            assert "5,000" in table_content, "left_branch tokens (5000) missing"
            assert "6,000" in table_content, "right_branch tokens (6000) missing"
            assert "8,000" in table_content, "end_task tokens (8000) missing"

            # Status values: all 4 tasks should show "completed".
            assert table_content.count("completed") >= 4, (
                "expected at least 4 'completed' status values in Per-Task Data"
            )

            # Tool uses values: verify task tool_uses counts appear.
            assert "| 1 |" in table_content, "start_task tool_uses (1) missing"
            assert "| 2 |" in table_content, "left_branch tool_uses (2) missing"
            assert "| 3 |" in table_content, "right_branch tool_uses (3) missing"
            assert "| 4 |" in table_content, "end_task tool_uses (4) missing"

            # Correct row count.
            lines = table_content.strip().split("\n")
            data_rows = [
                ln for ln in lines
                if ln.startswith("|") and "Layer" not in ln and "---" not in ln
            ]
            assert len(data_rows) == 4, f"Expected 4 task rows, got {len(data_rows)}"

        finally:
            telemetry._log = old


# ---------------------------------------------------------------------------
# Degradation and edge case tests
# ---------------------------------------------------------------------------

# Compose source for orphaned agent test — two tasks in a gather.
GATHER_TWO_SOURCE = """\
class A: ...
class B: ...
class Combined: ...

async def task_a() -> A:
    \"\"\"Task A.\"\"\"
async def task_b() -> B:
    \"\"\"Task B.\"\"\"
async def combine(a: A, b: B) -> Combined:
    \"\"\"Combine.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    result = await combine(a, b)
"""


class TestNoComposeOmitsTopology:
    """Graceful degradation when compose.py is missing."""

    def test_no_compose_omits_topology(self, tmp_path: Path) -> None:
        """No compose.py written -- topology section omitted, per-task data
        still appears with layer shown as a dash.
        """
        old = setup_log(tmp_path)
        try:
            # Do NOT write compose.py -- just emit telemetry events.
            emit_agent("m-nocomp", 1, "n1", "some_task", tokens=3000, tool_uses=2)
            emit_agent(
                "m-nocomp", 1, "n2", "another_task", tokens=4000, tool_uses=3,
            )
            emit_cycle("m-nocomp", 1, "EXECUTE", "ASSESS")

            write_milestone_summary(tmp_path, "m-nocomp", "completed")

            metrics = (
                tmp_path / ".clou" / "milestones" / "m-nocomp" / "metrics.md"
            )
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # Header present.
            assert content.startswith("# Metrics: m-nocomp")

            # Topology section must NOT appear.
            assert "## Topology" not in content

            # Per-Task Data section IS present (without layer info).
            assert "## Per-Task Data" in content
            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]

            # Layer column shows dash for all tasks.
            assert "\u2014" in table_content, (
                "expected dash (\u2014) for layer column when compose.py is absent"
            )

            # Agent rows still appear.
            assert "some_task" in table_content
            assert "another_task" in table_content

            # Agents table present.
            assert "## Agents" in content

            # Row count: 2 data rows.
            lines = table_content.strip().split("\n")
            data_rows = [
                ln for ln in lines
                if ln.startswith("|") and "Layer" not in ln and "---" not in ln
            ]
            assert len(data_rows) == 2, f"Expected 2 task rows, got {len(data_rows)}"

        finally:
            telemetry._log = old


class TestOrphanedAgentsInPipeline:
    """Edge case: agent.start without agent.end produces orphaned status."""

    def test_orphaned_agents_in_pipeline(self, tmp_path: Path) -> None:
        """One task completes normally, one has only agent.start (orphaned).

        Asserts per-task row shows 'orphaned' status with zero duration,
        and agents_failed counts the orphaned agent.
        """
        old = setup_log(tmp_path)
        try:
            write_compose(tmp_path, "m-orphan", GATHER_TWO_SOURCE)

            # task_a completes normally.
            emit_agent("m-orphan", 1, "o1", "task_a", tokens=5000, tool_uses=3)

            # task_b only starts -- orphaned.
            emit_agent_start("m-orphan", 1, "o2", "task_b")

            emit_cycle("m-orphan", 1, "EXECUTE", "ASSESS")

            write_milestone_summary(tmp_path, "m-orphan", "completed")

            metrics = (
                tmp_path / ".clou" / "milestones" / "m-orphan" / "metrics.md"
            )
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # Header: agents_failed should be 1 (orphaned counts as failed).
            assert "agents_failed: 1" in content

            # Per-Task Data section present.
            assert "## Per-Task Data" in content
            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]

            # Both tasks present.
            assert "task_a" in table_content
            assert "task_b" in table_content

            # task_a shows "completed".
            # Find the task_a row and check status.
            task_a_row = [
                ln for ln in table_content.split("\n")
                if "task_a" in ln and ln.startswith("|")
            ]
            assert len(task_a_row) == 1, "Expected exactly one task_a row"
            assert "completed" in task_a_row[0]

            # task_b shows "orphaned" with zero duration.
            task_b_row = [
                ln for ln in table_content.split("\n")
                if "task_b" in ln and ln.startswith("|")
            ]
            assert len(task_b_row) == 1, "Expected exactly one task_b row"
            assert "orphaned" in task_b_row[0]
            assert "0s" in task_b_row[0], "orphaned task should show 0s duration"

            # Agents table shows task_b as orphaned.
            assert "## Agents" in content
            agents_start = content.index("## Agents")
            # Limit to just the Agents section (up to next ## heading).
            agents_rest = content[agents_start:]
            next_section = agents_rest.find("\n## ", 1)
            agents_section = (
                agents_rest[:next_section] if next_section != -1 else agents_rest
            )
            agent_b_row = [
                ln for ln in agents_section.split("\n")
                if "task_b" in ln and ln.startswith("|")
            ]
            assert len(agent_b_row) == 1, "Expected exactly one task_b agent row"
            assert "orphaned" in agent_b_row[0]

        finally:
            telemetry._log = old


class TestExistingSectionsPreserved:
    """write_milestone_summary overwrites previous metrics.md cleanly."""

    def test_existing_sections_preserved(self, tmp_path: Path) -> None:
        """Write dummy metrics.md, then call write_milestone_summary.

        Asserts old content is completely replaced and new output is
        well-formed.
        """
        old = setup_log(tmp_path)
        try:
            # Pre-populate metrics.md with stale content.
            ms_dir = tmp_path / ".clou" / "milestones" / "m-overwrite"
            ms_dir.mkdir(parents=True, exist_ok=True)
            (ms_dir / "metrics.md").write_text(
                "# Old content\nshould be gone\n", encoding="utf-8",
            )

            write_compose(tmp_path, "m-overwrite", WIDE_GRAPH_SOURCE)
            emit_agent(
                "m-overwrite", 1, "ow1", "task_a", tokens=2000, tool_uses=1,
            )
            emit_agent(
                "m-overwrite", 1, "ow2", "task_b", tokens=3000, tool_uses=2,
            )
            emit_agent(
                "m-overwrite", 1, "ow3", "task_c", tokens=4000, tool_uses=3,
            )
            emit_agent(
                "m-overwrite", 1, "ow4", "combine", tokens=5000, tool_uses=4,
            )
            emit_cycle("m-overwrite", 1, "EXECUTE", "ASSESS")

            write_milestone_summary(tmp_path, "m-overwrite", "completed")

            metrics = ms_dir / "metrics.md"
            assert metrics.exists(), "metrics.md should exist"
            content = metrics.read_text()

            # Old content must be gone.
            assert "Old content" not in content
            assert "should be gone" not in content

            # New content starts with proper header.
            assert content.startswith("# Metrics: m-overwrite")

            # Topology and per-task sections are correct.
            assert "## Topology" in content
            assert "width: 3" in content
            assert "## Per-Task Data" in content

            ptd_start = content.index("## Per-Task Data")
            table_content = content[ptd_start:]
            assert "task_a" in table_content
            assert "task_b" in table_content
            assert "task_c" in table_content
            assert "combine" in table_content

        finally:
            telemetry._log = old


class TestEmptyTelemetry:
    """Graceful handling when no events are emitted for the milestone."""

    def test_empty_telemetry(self, tmp_path: Path) -> None:
        """Init telemetry, emit events for a DIFFERENT milestone, then
        write summary for the target milestone.

        Asserts metrics.md has zero counts and no topology or per-task
        sections.
        """
        old = setup_log(tmp_path)
        try:
            # Emit events for a different milestone to prove filtering works.
            emit_agent(
                "other-milestone", 1, "x1", "irrelevant", tokens=9999,
                tool_uses=9,
            )
            emit_cycle("other-milestone", 1, "EXECUTE", "ASSESS")

            # Write summary for milestone with zero events.
            write_milestone_summary(tmp_path, "m-empty", "completed")

            metrics = (
                tmp_path / ".clou" / "milestones" / "m-empty" / "metrics.md"
            )
            assert metrics.exists(), "metrics.md should be created"
            content = metrics.read_text()

            # Header present with zero counts.
            assert content.startswith("# Metrics: m-empty")
            assert "cycles: 0" in content
            assert "agents_spawned: 0" in content

            # No topology section (no compose.py, and irrelevant anyway).
            assert "## Topology" not in content

            # No per-task data section (no agent events for this milestone).
            assert "## Per-Task Data" not in content

        finally:
            telemetry._log = old
