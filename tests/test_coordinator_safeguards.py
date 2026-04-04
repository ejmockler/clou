"""Tests for runtime safeguard controls in clou.coordinator.

Covers selective abort, timeout termination, budget termination,
and narrow-graph unchanged behavior.

Mock strategy: mock at SDK boundary (ClaudeSDKClient), test our
control flow logic with real helper functions.
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (
    ResultMessage,
    TaskNotificationMessage,
)

from clou.coordinator import (
    _compute_abort_set,
    _DEFAULT_TIMEOUT_SECONDS,
    _run_single_cycle,
    _write_failure_shard,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_result(
    usage: dict[str, Any] | None = None,
    result: str | None = "done",
) -> ResultMessage:
    """Create a ResultMessage with required fields filled in."""
    return ResultMessage(
        subtype="result",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="test",
        usage=usage,
        result=result,
    )


def _make_task_notification(
    task_id: str = "task-1",
    status: str = "completed",
    summary: str = "task done",
) -> TaskNotificationMessage:
    """Create a TaskNotificationMessage."""
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status=status,
        output_file=None,
        summary=summary,
        uuid="uuid-1",
        session_id="test",
    )


def _make_task_started(task_id: str, description: str) -> SimpleNamespace:
    """Create a task-started message (duck-typed)."""
    return SimpleNamespace(
        task_id=task_id,
        description=description,
    )


def _make_task_progress(
    task_id: str,
    total_tokens: int = 0,
    tool_uses: int = 0,
) -> SimpleNamespace:
    """Create a task-progress message (duck-typed)."""
    return SimpleNamespace(
        task_id=task_id,
        description="",
        last_tool_name="Bash",
        usage={"total_tokens": total_tokens, "tool_uses": tool_uses},
    )


def _mock_sdk_client(
    messages: list[object] | None = None,
) -> MagicMock:
    """Build a mock ClaudeSDKClient that yields given messages."""
    client = MagicMock()
    client.query = AsyncMock()
    client.stop_task = MagicMock()

    async def _receive_response():
        for msg in messages or [_make_result()]:
            yield msg

    client.receive_response = _receive_response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


_PC = "clou.coordinator"


# ---------------------------------------------------------------------------
# _compute_abort_set tests
# ---------------------------------------------------------------------------


class TestComputeAbortSet:
    """Unit tests for the selective abort dependency analysis."""

    def test_no_deps_returns_empty(self) -> None:
        """Independent tasks: no abort set."""
        deps = {"A": [], "B": [], "C": []}
        result = _compute_abort_set("A", deps, {"B", "C"})
        assert result == set()

    def test_direct_dependent(self) -> None:
        """B depends on A: if A fails, B is aborted."""
        deps = {"A": [], "B": ["A"], "C": []}
        result = _compute_abort_set("A", deps, {"B", "C"})
        assert result == {"B"}

    def test_transitive_dependents(self) -> None:
        """A -> B -> C: if A fails, both B and C are aborted."""
        deps = {"A": [], "B": ["A"], "C": ["B"]}
        result = _compute_abort_set("A", deps, {"B", "C"})
        assert result == {"B", "C"}

    def test_diamond_dependency(self) -> None:
        """Diamond: A -> C, B -> C. If A fails, C is aborted but B continues."""
        deps = {"A": [], "B": [], "C": ["A", "B"]}
        result = _compute_abort_set("A", deps, {"B", "C"})
        assert result == {"C"}

    def test_only_active_tasks_aborted(self) -> None:
        """Only tasks in the active set are returned."""
        deps = {"A": [], "B": ["A"], "C": ["A"]}
        # C is not active (already completed)
        result = _compute_abort_set("A", deps, {"B"})
        assert result == {"B"}

    def test_failed_task_not_in_result(self) -> None:
        """The failed task itself is not in the abort set."""
        deps = {"A": [], "B": ["A"]}
        result = _compute_abort_set("A", deps, {"A", "B"})
        assert "A" not in result
        assert result == {"B"}

    def test_chain_all_aborted(self) -> None:
        """A -> B -> C -> D: all downstream aborted."""
        deps = {"A": [], "B": ["A"], "C": ["B"], "D": ["C"]}
        result = _compute_abort_set("A", deps, {"B", "C", "D"})
        assert result == {"B", "C", "D"}

    def test_empty_active_set(self) -> None:
        """No active tasks: empty abort set."""
        deps = {"A": [], "B": ["A"]}
        result = _compute_abort_set("A", deps, set())
        assert result == set()


# ---------------------------------------------------------------------------
# _write_failure_shard tests
# ---------------------------------------------------------------------------


class TestWriteFailureShard:
    """Test coordinator-written failure records."""

    def test_writes_timeout_shard(self, tmp_path: Path) -> None:
        """Timeout failure shard has correct structure."""
        ms_dir = tmp_path / "milestones" / "test-ms"
        ms_dir.mkdir(parents=True)
        # Set name so write_shard_path can use it.
        # We pass ms_dir directly; write_shard_path uses milestone_dir.name
        path = _write_failure_shard(
            ms_dir, "my-phase", "build_thing",
            "timeout", "Task terminated after 600s",
            ["deploy_thing"],
        )
        assert path.exists()
        content = path.read_text()
        assert "status: failed" in content
        assert "**Failure Type:** timeout" in content
        assert "Task terminated after 600s" in content
        assert "deploy_thing" in content

    def test_writes_budget_shard(self, tmp_path: Path) -> None:
        """Budget failure shard has correct structure."""
        ms_dir = tmp_path / "milestones" / "test-ms"
        ms_dir.mkdir(parents=True)
        path = _write_failure_shard(
            ms_dir, "my-phase", "expensive_task",
            "budget_exceeded", "Task terminated after 50000 tokens (budget: 40000)",
            [],
        )
        content = path.read_text()
        assert "**Failure Type:** budget_exceeded" in content
        assert "50000 tokens" in content
        assert "Downstream tasks blocked: none" in content

    def test_shard_path_structure(self, tmp_path: Path) -> None:
        """Shard file goes to the correct path."""
        ms_dir = tmp_path / "test-ms"
        ms_dir.mkdir()
        path = _write_failure_shard(
            ms_dir, "runtime-controls", "my_task",
            "timeout", "timeout detail", [],
        )
        assert "execution-my-task.md" in path.name
        assert "runtime-controls" in str(path)


# ---------------------------------------------------------------------------
# Integration tests — selective abort in _run_single_cycle
# ---------------------------------------------------------------------------


class TestSelectiveAbort:
    """Integration tests for selective abort behavior in the message loop."""

    @pytest.fixture(autouse=True)
    def _patch_prompt_io(self) -> Any:
        """Patch file-I/O functions that aren't under test."""
        with (
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(f"{_PC}.build_hooks", return_value={"PreToolUse": []}),
        ):
            yield

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        """Set up a project dir with compose.py for DAG extraction."""
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        return tmp_path

    def _write_compose(self, project_dir: Path, milestone: str, source: str) -> None:
        """Write a compose.py for test DAG extraction."""
        ms_dir = project_dir / ".clou" / "milestones" / milestone
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "compose.py").write_text(source, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_selective_abort_diamond(self, project_dir: Path) -> None:
        """Diamond: A -> C, B -> C. If A fails, C aborted but B continues."""
        self._write_compose(project_dir, "ms", '''
class R: ...
class S: ...
class T: ...

async def task_a() -> R:
    """Do A."""

async def task_b() -> S:
    """Do B."""

async def task_c(a: R, b: S) -> T:
    """Do C depending on A and B."""

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await task_c(a, b)
''')
        # Message sequence: A starts, B starts, C starts, A fails.
        messages = [
            _make_task_started("tid-a", "task_a"),
            _make_task_started("tid-b", "task_b"),
            _make_task_started("tid-c", "task_c"),
            _make_task_notification("tid-a", "failed", "task A crashed"),
            # B completes normally after A fails.
            _make_task_notification("tid-b", "completed", "B done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # C should have been stopped (dependent of A).
        client.stop_task.assert_called_once_with("tid-c")
        # B is independent -- should NOT be stopped.
        assert all(
            call.args[0] != "tid-b"
            for call in client.stop_task.call_args_list
        )
        # Cycle should continue (not return agent_team_crash) since B survives.
        assert result == "ASSESS"

    @pytest.mark.asyncio
    async def test_selective_abort_independent(self, project_dir: Path) -> None:
        """Fully independent gather(): if A fails, B and C continue."""
        self._write_compose(project_dir, "ms", '''
class R: ...
class S: ...
class T: ...

async def task_a() -> R:
    """Do A."""

async def task_b() -> S:
    """Do B."""

async def task_c() -> T:
    """Do C."""

async def execute():
    a, b, c = await gather(task_a(), task_b(), task_c())
''')
        messages = [
            _make_task_started("tid-a", "task_a"),
            _make_task_started("tid-b", "task_b"),
            _make_task_started("tid-c", "task_c"),
            _make_task_notification("tid-a", "failed", "A crashed"),
            _make_task_notification("tid-b", "completed", "B done"),
            _make_task_notification("tid-c", "completed", "C done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # No tasks should be stopped -- all are independent.
        client.stop_task.assert_not_called()
        assert result == "ASSESS"

    @pytest.mark.asyncio
    async def test_selective_abort_chain(self, project_dir: Path) -> None:
        """Chain A -> B -> C: if A fails, B and C are aborted."""
        self._write_compose(project_dir, "ms", '''
class R: ...
class S: ...
class T: ...

async def task_a() -> R:
    """Do A."""

async def task_b(a: R) -> S:
    """Do B."""

async def task_c(b: S) -> T:
    """Do C."""

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
''')
        messages = [
            _make_task_started("tid-a", "task_a"),
            _make_task_started("tid-b", "task_b"),
            _make_task_started("tid-c", "task_c"),
            _make_task_notification("tid-a", "failed", "A crashed"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # Both B and C depend on A, and no independent survivors remain.
        stopped_ids = {call.args[0] for call in client.stop_task.call_args_list}
        assert "tid-b" in stopped_ids
        assert "tid-c" in stopped_ids
        # All tasks aborted -> agent_team_crash.
        assert result == "agent_team_crash"

    @pytest.mark.asyncio
    async def test_narrow_graph_unchanged(self, project_dir: Path) -> None:
        """Single-task layer (no gather): behavior unchanged from before."""
        self._write_compose(project_dir, "ms", '''
class R: ...
class S: ...
class T: ...

async def task_a() -> R:
    """Do A."""

async def task_b(a: R) -> S:
    """Do B."""

async def task_c(b: S) -> T:
    """Do C."""

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
''')
        # Normal completion: started, completed, result.
        messages = [
            _make_task_started("tid-a", "task_a"),
            _make_task_notification("tid-a", "completed", "A done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        assert result == "ASSESS"
        client.stop_task.assert_not_called()


# ---------------------------------------------------------------------------
# Budget termination tests
# ---------------------------------------------------------------------------


class TestBudgetTermination:
    """Test per-task token budget enforcement."""

    @pytest.fixture(autouse=True)
    def _patch_prompt_io(self) -> Any:
        with (
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(f"{_PC}.build_hooks", return_value={"PreToolUse": []}),
        ):
            yield

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        return tmp_path

    @pytest.mark.asyncio
    async def test_budget_exceeded_stops_task(self, project_dir: Path) -> None:
        """Task exceeding token budget is stopped via SDK."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text('''
class R: ...
class S: ...
class T: ...

def resource_bounds(tokens=None, timeout_seconds=None):
    def decorator(func):
        return func
    return decorator

@resource_bounds(tokens=1000)
async def expensive_task() -> R:
    """Uses lots of tokens."""

async def cheap_task() -> S:
    """Uses few tokens."""

async def final_task(a: R, b: S) -> T:
    """Final step."""

async def execute():
    a, b = await gather(expensive_task(), cheap_task())
    c = await final_task(a, b)
''', encoding="utf-8")

        # Write a checkpoint so we can get the current phase.
        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n"
        )

        messages = [
            _make_task_started("tid-exp", "expensive_task"),
            _make_task_started("tid-chp", "cheap_task"),
            # expensive_task exceeds budget.
            _make_task_progress("tid-exp", total_tokens=1500),
            _make_task_notification("tid-chp", "completed", "cheap done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # The expensive task should be stopped.
        client.stop_task.assert_called_once_with("tid-exp")

        # A failure shard should be written.
        shard_dir = ms_dir / "phases" / "build-phase"
        shards = list(shard_dir.glob("execution-*.md"))
        assert len(shards) == 1
        content = shards[0].read_text()
        assert "budget_exceeded" in content
        assert "1500 tokens" in content


# ---------------------------------------------------------------------------
# Timeout termination tests
# ---------------------------------------------------------------------------


class TestTimeoutTermination:
    """Test per-task wall-clock timeout enforcement."""

    @pytest.fixture(autouse=True)
    def _patch_prompt_io(self) -> Any:
        with (
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(f"{_PC}.build_hooks", return_value={"PreToolUse": []}),
        ):
            yield

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        return tmp_path

    @pytest.mark.asyncio
    async def test_timeout_stops_task(self, project_dir: Path) -> None:
        """Task exceeding timeout is stopped via SDK."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text('''
class R: ...
class S: ...
class T: ...

def resource_bounds(tokens=None, timeout_seconds=None):
    def decorator(func):
        return func
    return decorator

@resource_bounds(timeout_seconds=10)
async def slow_task() -> R:
    """Takes too long."""

async def fast_task() -> S:
    """Runs quickly."""

async def final_task(a: R, b: S) -> T:
    """Final step."""

async def execute():
    a, b = await gather(slow_task(), fast_task())
    c = await final_task(a, b)
''', encoding="utf-8")

        # Write a checkpoint.
        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n"
        )

        messages = [
            _make_task_started("tid-slow", "slow_task"),
            _make_task_started("tid-fast", "fast_task"),
            # Progress message -- timeout is checked here.
            _make_task_progress("tid-slow", total_tokens=500),
            _make_task_notification("tid-fast", "completed", "fast done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        # Simulate time progression for timeout detection.
        # We use a counter-based callable instead of a finite iterator
        # because asyncio.timeout() also calls time.monotonic() internally.
        # First few calls return 0 (task start recording), then later
        # calls return 15 (exceeding the 10s timeout for slow_task).
        import time
        _real_monotonic = time.monotonic
        _call_count = 0

        def _fake_monotonic() -> float:
            nonlocal _call_count
            _call_count += 1
            # First 4 calls: task starts + asyncio internals -> return 0.
            # Subsequent calls: return 15 to trigger timeout during
            # progress check for slow_task.
            if _call_count <= 4:
                return 0.0
            return 15.0

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
            patch("time.monotonic", side_effect=_fake_monotonic),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # The slow task should be stopped.
        client.stop_task.assert_called_once_with("tid-slow")

        # A failure shard should be written with timeout type.
        shard_dir = ms_dir / "phases" / "build-phase"
        shards = list(shard_dir.glob("execution-*.md"))
        assert len(shards) == 1
        content = shards[0].read_text()
        assert "timeout" in content

    @pytest.mark.asyncio
    async def test_asyncio_timeout_fires_on_hung_task(self, project_dir: Path) -> None:
        """Idle watchdog fires when a task hangs with no progress messages.

        This tests the idle watchdog that catches tasks that suppress
        telemetry entirely -- the progress-based check in the elif branch
        would never trigger for such tasks.  The watchdog is rescheduled
        on every coordinator message, so a genuinely idle stream is
        required to trip it.
        """
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text('''
class R: ...

def resource_bounds(tokens=None, timeout_seconds=None):
    def decorator(func):
        return func
    return decorator

@resource_bounds(timeout_seconds=1)
async def hung_task() -> R:
    """Hangs forever with no progress messages."""

async def execute():
    r = await hung_task()
''', encoding="utf-8")

        # Write a checkpoint so current_phase is available for shard writing.
        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n"
        )

        # The task starts but then the iterator hangs -- no progress, no
        # completion, no result.  asyncio.timeout should fire.
        _hung = asyncio.Event()

        async def _hanging_receive():
            yield _make_task_started("tid-hung", "hung_task")
            # Block indefinitely -- simulates a hung task with no telemetry.
            await _hung.wait()

        client = MagicMock()
        client.query = AsyncMock()
        client.stop_task = MagicMock()
        client.receive_response = _hanging_receive
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # asyncio.timeout should have fired, returning agent_team_crash.
        assert result == "agent_team_crash"

        # A failure shard should be written for the hung task.
        shard_dir = ms_dir / "phases" / "build-phase"
        shards = list(shard_dir.glob("execution-*.md"))
        assert len(shards) == 1
        content = shards[0].read_text()
        assert "timeout" in content
        assert "idle" in content
        assert "hung_task" in content or "hung-task" in content


# ---------------------------------------------------------------------------
# Failure shard format tests
# ---------------------------------------------------------------------------


class TestFailureShardFormat:
    """Verify failure shard content matches the expected schema."""

    def test_timeout_includes_all_fields(self, tmp_path: Path) -> None:
        ms_dir = tmp_path / "test-ms"
        ms_dir.mkdir()
        path = _write_failure_shard(
            ms_dir, "phase-1", "slow_task", "timeout",
            "Task terminated after 600s",
            ["downstream_a", "downstream_b"],
        )
        content = path.read_text()
        # Verify all required fields from the schema.
        assert "## Summary" in content
        assert "status: failed" in content
        assert "tasks: 1 total, 0 completed, 1 failed, 0 in_progress" in content
        assert "failures: slow_task" in content
        assert "### T1: slow_task" in content
        assert "**Status:** failed" in content
        assert "**Failure Type:** timeout" in content
        assert "**Error:** Task terminated after 600s" in content
        assert "**Partial Work:**" in content
        assert "downstream_a, downstream_b" in content
        assert "**Files changed:** unknown (terminated before completion)" in content

    def test_no_dependency_impact(self, tmp_path: Path) -> None:
        ms_dir = tmp_path / "test-ms"
        ms_dir.mkdir()
        path = _write_failure_shard(
            ms_dir, "phase-1", "leaf_task", "budget_exceeded",
            "Exceeded budget", [],
        )
        content = path.read_text()
        assert "Downstream tasks blocked: none" in content


# ---------------------------------------------------------------------------
# Stale shard cleaning tests
# ---------------------------------------------------------------------------


class TestStaleShardCleaning:
    """Verify stale shards are cleaned before gather() dispatch."""

    @pytest.fixture(autouse=True)
    def _patch_prompt_io(self) -> Any:
        with (
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(f"{_PC}.build_hooks", return_value={"PreToolUse": []}),
        ):
            yield

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        return tmp_path

    @pytest.mark.asyncio
    async def test_stale_shards_cleaned_before_gather_dispatch(
        self, project_dir: Path,
    ) -> None:
        """Stale execution-*.md files from prior cycles are removed before dispatch."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)

        # Write a compose.py with a gather() group (>1 task).
        (ms_dir / "compose.py").write_text('''
class R: ...
class S: ...

async def task_a() -> R:
    """Do A."""

async def task_b() -> S:
    """Do B."""

async def execute():
    a, b = await gather(task_a(), task_b())
''', encoding="utf-8")

        # Write a checkpoint so current_phase is available.
        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n",
            encoding="utf-8",
        )

        # Create stale shard files from a prior crashed cycle.
        phase_dir = ms_dir / "phases" / "build-phase"
        phase_dir.mkdir(parents=True)
        stale_a = phase_dir / "execution-task-a.md"
        stale_b = phase_dir / "execution-task-b.md"
        stale_a.write_text("## Summary\nstatus: in_progress\n", encoding="utf-8")
        stale_b.write_text("## Summary\nstatus: failed\n", encoding="utf-8")

        # Also create a normal execution.md which should NOT be removed.
        normal_exec = phase_dir / "execution.md"
        normal_exec.write_text("## Summary\nstatus: completed\n", encoding="utf-8")

        # Normal completion messages.
        messages = [
            _make_task_started("tid-a", "task_a"),
            _make_task_started("tid-b", "task_b"),
            _make_task_notification("tid-a", "completed", "A done"),
            _make_task_notification("tid-b", "completed", "B done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        assert result == "ASSESS"

        # Stale shards should have been removed before dispatch.
        assert not stale_a.exists(), "Stale shard execution-task-a.md was not cleaned"
        assert not stale_b.exists(), "Stale shard execution-task-b.md was not cleaned"

        # Normal execution.md should be untouched.
        assert normal_exec.exists(), "execution.md was incorrectly removed"

    @pytest.mark.asyncio
    async def test_no_cleaning_for_single_task(
        self, project_dir: Path,
    ) -> None:
        """Single-task DAGs (no gather) skip stale shard cleaning."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)

        # Write a compose.py with a single task (no gather).
        (ms_dir / "compose.py").write_text('''
class R: ...

async def task_a() -> R:
    """Do A."""

async def execute():
    a = await task_a()
''', encoding="utf-8")

        # Write a checkpoint.
        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n",
            encoding="utf-8",
        )

        # Create a shard file that should NOT be cleaned (single-task DAG).
        phase_dir = ms_dir / "phases" / "build-phase"
        phase_dir.mkdir(parents=True)
        shard = phase_dir / "execution-task-a.md"
        shard.write_text("## Summary\nstatus: in_progress\n", encoding="utf-8")

        messages = [
            _make_task_started("tid-a", "task_a"),
            _make_task_notification("tid-a", "completed", "A done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        assert result == "ASSESS"
        # Single-task DAG: shard should NOT be cleaned.
        assert shard.exists(), "Shard was incorrectly cleaned for single-task DAG"
