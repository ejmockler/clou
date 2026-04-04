"""Integration tests for the topology instrumentation pipeline.

Exercises the full path: compose.py -> graph.compute_topology -> telemetry
-> write_milestone_summary -> metrics.md.  Each test seeds a compose.py,
emits synthetic telemetry events, calls write_milestone_summary, and
asserts on the resulting metrics.md content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clou import telemetry
from clou.graph import compute_topology
from clou.telemetry import (
    SpanLog,
    extract_task_data,
    init,
    read_log,
    write_milestone_summary,
)


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
