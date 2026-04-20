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
    classify_timeout,
    run_coordinator,
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
        # failure_shard_path uses milestone_dir.name for the slug path.
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

        # asyncio.timeout should have fired.  With the agent still active
        # (TaskStartedMessage was received), classify_timeout returns
        # "interrupted" — the agent is blocked on a long-running call,
        # not crashed.
        assert result == "interrupted"

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
        """Stale execution-*.md in every gather-layer phase dir is swept.

        Compose.py expresses parallelism at the phase level — each
        function is its own phase directory.  The layer-wide cleanup
        must sweep every DAG task's phase dir, not only the
        checkpoint's ``current_phase``.  The prior bug swept only one,
        leaving orphans in the other parallel phases.
        """
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)

        # Gather() group across two parallel function/phases.
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

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        # current_phase names one of the layer's phases; layer-wide
        # sweep still covers BOTH.
        (active_dir / "coordinator.md").write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: task_a\nphases_completed: 0\nphases_total: 2\n",
            encoding="utf-8",
        )

        # Stale shards planted in BOTH phase dirs — the prior single-
        # phase cleanup would only have swept task_a's dir.
        phase_a = ms_dir / "phases" / "task_a"
        phase_a.mkdir(parents=True)
        stale_a = phase_a / "execution-old-cycle.md"
        stale_a.write_text("## Summary\nstatus: in_progress\n", encoding="utf-8")
        normal_a = phase_a / "execution.md"
        normal_a.write_text("## Summary\nstatus: completed\n", encoding="utf-8")

        phase_b = ms_dir / "phases" / "task_b"
        phase_b.mkdir(parents=True)
        stale_b = phase_b / "execution-orphan-slug.md"
        stale_b.write_text("## Summary\nstatus: failed\n", encoding="utf-8")

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

        # Stale shards removed from BOTH phases (the whole layer, not
        # just current_phase).
        assert not stale_a.exists(), (
            "Stale shard in task_a/ was not cleaned"
        )
        assert not stale_b.exists(), (
            "Stale shard in task_b/ was not cleaned — layer-wide sweep "
            "regressed to single-phase behavior"
        )
        # Canonical execution.md preserved.
        assert normal_a.exists(), "Canonical execution.md was incorrectly removed"

    @pytest.mark.asyncio
    async def test_cleaning_covers_single_task_dag(
        self, project_dir: Path,
    ) -> None:
        """Single-task DAGs also get their phase swept before dispatch.

        The prior invariant was ``gather-only cleanup``, which led to
        stale shards surviving in solo-task phase directories across
        cycles and tripping validation.  The layer-wide remolding
        sweeps every DAG phase regardless of layer width — by the time
        the cycle starts, nothing is ``current`` and every
        ``execution-*.md`` is stale by definition.
        """
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)

        # Single-task DAG: function name must match _PHASE_RE.
        (ms_dir / "compose.py").write_text('''
class R: ...

async def task_a() -> R:
    """Do A."""

async def execute():
    a = await task_a()
''', encoding="utf-8")

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: task_a\nphases_completed: 0\nphases_total: 1\n",
            encoding="utf-8",
        )

        # Plant a stale shard in the single task's phase dir.
        phase_dir = ms_dir / "phases" / "task_a"
        phase_dir.mkdir(parents=True)
        shard = phase_dir / "execution-prior.md"
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
        # Layer-wide sweep removes the stale shard in the single-task DAG.
        assert not shard.exists(), (
            "Stale shard was not cleaned in single-task DAG"
        )


# ---------------------------------------------------------------------------
# Slug-drift remolding integration test — the "never again" invariant.
# ---------------------------------------------------------------------------


class TestSlugDriftRemoldingInvariant:
    """Prove the three remolding protections compose correctly.

    The original incident (~/.config/clou/.clou/milestones/quality-
    infrastructure/) accumulated duplicate execution-<slug>.md files in
    a gather() layer of three parallel functions.  Each cycle wrote a
    new slug variant, stale shards accumulated, and validation trapped
    on malformed orphans — blocking the 4th phase from ever running.

    The fixes that make it architecturally impossible:
      1. **Boundary enforcement.** Worker ``WRITE_PERMISSIONS`` no
         longer include ``execution-*.md`` — even a misbriefed worker
         can't create a drifted path via the Write tool.
      2. **Layer-wide cleanup at EXECUTE start.** Every phase in the
         active DAG layer is swept for ``execution-*.md`` orphans
         before workers dispatch.  Cleanup is gated on
         ``cycle_type == "EXECUTE"`` so ASSESS/REPLAN/VERIFY cycles
         preserve legitimate failure shards as evidence.
      3. **Drift telemetry.** Every sweep emits ``shard.cleaned_stale``
         with per-phase counts; per-file failures emit
         ``shard.cleanup_failed``; helper crashes emit
         ``shard.cleanup_error``.  Silent cleanup failure was a root
         cause of the original incident.

    This test composes all three against a 3-function gather layer
    that mirrors the brutalist-mcp-server topology.
    """

    @pytest.fixture(autouse=True)
    def _patch_prompt_io(self) -> Any:
        with (
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(f"{_PC}.build_hooks", return_value={"PreToolUse": []}),
        ):
            yield

    def _three_function_gather_compose(self) -> str:
        """compose.py matching the brutalist-mcp-server quality-infrastructure shape."""
        return '''
class R: ...

async def set_coverage_thresholds() -> R:
    """Thresholds."""

async def create_metrics_module() -> R:
    """Metrics."""

async def extend_logger() -> R:
    """Logger."""

async def execute():
    a, b, c = await gather(
        set_coverage_thresholds(),
        create_metrics_module(),
        extend_logger(),
    )
'''

    @pytest.mark.asyncio
    async def test_gather_layer_orphans_from_prior_cycles_never_accumulate(
        self, tmp_path: Path,
    ) -> None:
        """Rework cycle on a 3-function gather() layer leaves no orphans."""
        project_dir = tmp_path
        clou_dir = project_dir / ".clou"
        clou_dir.mkdir()
        ms_dir = clou_dir / "milestones" / "quality-infrastructure"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text(
            self._three_function_gather_compose(), encoding="utf-8",
        )

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: extend_logger\n"
            "phases_completed: 0\nphases_total: 4\n",
            encoding="utf-8",
        )

        # Plant prior-cycle orphans — the exact slug-drift pattern that
        # caused the original incident: mixed hyphen/underscore slugs
        # across the 3 parallel phases.
        orphans: list[Path] = []
        for phase, slug_a, slug_b in [
            ("set_coverage_thresholds",
             "execution-set-coverage-thresholds.md",
             "execution-set_coverage_thresholds.md"),
            ("create_metrics_module",
             "execution-create-metrics-module.md",
             "execution-create_metrics_module.md"),
            ("extend_logger",
             "execution-extend-logger.md",
             "execution-extend_logger.md"),
        ]:
            phase_dir = ms_dir / "phases" / phase
            phase_dir.mkdir(parents=True)
            for slug in (slug_a, slug_b):
                orphan = phase_dir / slug
                orphan.write_text(
                    "## Summary\nstatus: in_progress\n",
                    encoding="utf-8",
                )
                orphans.append(orphan)
            # Canonical execution.md from the prior cycle — must survive.
            (phase_dir / "execution.md").write_text(
                "## Summary\nstatus: completed\n", encoding="utf-8",
            )

        # Mock the SDK surface (no real coordinator subprocess).
        messages = [
            _make_task_started("ta", "set_coverage_thresholds"),
            _make_task_started("tb", "create_metrics_module"),
            _make_task_started("tc", "extend_logger"),
            _make_task_notification("ta", "completed", "done"),
            _make_task_notification("tb", "completed", "done"),
            _make_task_notification("tc", "completed", "done"),
            _make_result(usage={"input_tokens": 100}),
        ]
        client = _mock_sdk_client(messages)

        # Capture telemetry to verify drift events fire.
        events: list[dict[str, Any]] = []

        def _capture(name: str, **attrs: Any) -> None:
            events.append({"name": name, **attrs})

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
            patch("clou.telemetry.event", side_effect=_capture),
        ):
            result = await _run_single_cycle(
                project_dir, "quality-infrastructure",
                "EXECUTE", "do work",
            )

        assert result == "ASSESS"

        # (2) Every orphan across every phase in the active layer is gone.
        for orphan in orphans:
            assert not orphan.exists(), (
                f"Orphan {orphan} survived remolding cleanup — "
                "the original slug-drift bug regressed"
            )

        # Canonical execution.md preserved in every phase dir.
        for phase in (
            "set_coverage_thresholds",
            "create_metrics_module",
            "extend_logger",
        ):
            canonical = ms_dir / "phases" / phase / "execution.md"
            assert canonical.exists(), (
                f"Canonical execution.md missing in {phase}/"
            )

        # (3) Drift telemetry fired with the layer-wide counts.
        swept = [e for e in events if e["name"] == "shard.cleaned_stale"]
        assert len(swept) == 1, (
            "shard.cleaned_stale should fire once per EXECUTE sweep"
        )
        evt = swept[0]
        # All three phases in the layer produced removals.
        assert evt["phase_count"] == 3
        # Two orphans per phase × three phases = six total.
        assert evt["total_removed"] == 6
        assert evt["milestone"] == "quality-infrastructure"

    @pytest.mark.asyncio
    async def test_cleanup_skipped_for_non_execute_cycles(
        self, tmp_path: Path,
    ) -> None:
        """ASSESS/REPLAN/VERIFY cycles MUST preserve failure shards.

        Recovery and precompose surface coordinator-generated failure
        shards (written by _write_failure_shard on timeout/budget abort)
        so ASSESS can see the failure context.  Cleanup at the start
        of those cycles would erase the evidence.
        """
        project_dir = tmp_path
        clou_dir = project_dir / ".clou"
        clou_dir.mkdir()
        ms_dir = clou_dir / "milestones" / "ms"
        ms_dir.mkdir(parents=True)

        (ms_dir / "compose.py").write_text(
            self._three_function_gather_compose(), encoding="utf-8",
        )

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: extend_logger\n"
            "phases_completed: 1\nphases_total: 4\n",
            encoding="utf-8",
        )

        # A legitimate failure shard from a timeout in the PREVIOUS
        # EXECUTE — ASSESS is about to read this as evidence.
        phase_dir = ms_dir / "phases" / "extend_logger"
        phase_dir.mkdir(parents=True)
        failure_shard = phase_dir / "execution-extend-logger.md"
        failure_shard.write_text(
            "## Summary\nstatus: failed\nfailures: timeout\n",
            encoding="utf-8",
        )

        messages = [_make_result(usage={"input_tokens": 50})]
        client = _mock_sdk_client(messages)

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="VERIFY"),
        ):
            result = await _run_single_cycle(
                project_dir, "ms", "ASSESS", "assess",
            )

        assert result == "VERIFY"
        # The failure shard must survive the ASSESS cycle — ASSESS
        # reads it as evidence, cleanup would destroy that evidence.
        assert failure_shard.exists(), (
            "ASSESS cleanup destroyed failure shard evidence — "
            "cycle-type gating regressed"
        )


# ---------------------------------------------------------------------------
# classify_timeout unit tests
# ---------------------------------------------------------------------------


class TestClassifyTimeout:
    """Unit tests for the timeout classification function."""

    def test_no_messages_classified_as_crashed(self) -> None:
        """No messages received -> crashed (process never started)."""
        classification, evidence = classify_timeout(
            last_messages=[],
            active_task_ids={"tid-1"},
            task_start_times={"tid-1": 0.0},
            effective_timeout=600.0,
        )
        assert classification == "crashed"
        assert "no messages" in evidence

    def test_last_message_failed_notification_classified_as_crashed(self) -> None:
        """Last message is a failed TaskNotificationMessage -> crashed."""
        failed_msg = _make_task_notification("tid-1", "failed", "task crashed")
        classification, evidence = classify_timeout(
            last_messages=[
                _make_task_progress("tid-1", total_tokens=100),
                failed_msg,
            ],
            active_task_ids={"tid-1"},
            task_start_times={"tid-1": 0.0},
            effective_timeout=600.0,
        )
        assert classification == "crashed"
        assert "failure notification" in evidence

    def test_last_message_progress_classified_as_interrupted(self) -> None:
        """Last message is a progress message -> interrupted (agent was active).

        Rule 3 (active agents) takes precedence over rule 4 (progress
        message) when active_task_ids is non-empty.
        """
        progress_msg = _make_task_progress("tid-1", total_tokens=500)
        classification, evidence = classify_timeout(
            last_messages=[
                _make_task_started("tid-1", "my_task"),
                progress_msg,
            ],
            active_task_ids={"tid-1"},
            task_start_times={"tid-1": 0.0},
            effective_timeout=600.0,
        )
        assert classification == "interrupted"
        assert "active" in evidence

    def test_last_message_progress_no_active_agents_classified_as_interrupted(self) -> None:
        """Last message is progress but no active agents -> rule 4 fires (tool name in evidence)."""
        progress_msg = _make_task_progress("tid-1", total_tokens=500)
        classification, evidence = classify_timeout(
            last_messages=[progress_msg],
            active_task_ids=set(),
            task_start_times={},
            effective_timeout=600.0,
        )
        assert classification == "interrupted"
        assert "Bash" in evidence  # default tool name from _make_task_progress

    def test_last_message_result_classified_as_crashed(self) -> None:
        """Last message is a ResultMessage (not progress) -> crashed (conservative)."""
        result_msg = _make_result(usage={"input_tokens": 100})
        classification, evidence = classify_timeout(
            last_messages=[result_msg],
            active_task_ids=set(),
            task_start_times={},
            effective_timeout=600.0,
        )
        assert classification == "crashed"
        assert "conservative default" in evidence

    def test_last_message_completed_notification_classified_as_crashed(self) -> None:
        """Last message is a completed TaskNotificationMessage -> crashed (default).

        A completed notification is not a progress message and not a failure,
        so the conservative default applies.
        """
        completed_msg = _make_task_notification("tid-1", "completed", "done")
        classification, evidence = classify_timeout(
            last_messages=[completed_msg],
            active_task_ids=set(),
            task_start_times={},
            effective_timeout=600.0,
        )
        assert classification == "crashed"
        assert "conservative default" in evidence

    def test_classification_uses_last_message_only(self) -> None:
        """Classification is based on the last message, not earlier ones."""
        # First message is progress, but last message is a failed notification.
        progress_msg = _make_task_progress("tid-1", total_tokens=500)
        failed_msg = _make_task_notification("tid-1", "failed", "task crashed")
        classification, _ = classify_timeout(
            last_messages=[progress_msg, failed_msg],
            active_task_ids={"tid-1"},
            task_start_times={"tid-1": 0.0},
            effective_timeout=600.0,
        )
        assert classification == "crashed"

    def test_started_then_nothing_with_active_agents_is_interrupted(self) -> None:
        """Only a task-started message but agent still active -> interrupted.

        The agent may be blocked on a long-running MCP tool call (e.g.
        brutalist multi-model panel).  Active agents are work-in-progress,
        not crashes.
        """
        started_msg = _make_task_started("tid-1", "my_task")
        classification, evidence = classify_timeout(
            last_messages=[started_msg],
            active_task_ids={"tid-1"},
            task_start_times={"tid-1": 0.0},
            effective_timeout=600.0,
        )
        assert classification == "interrupted"
        assert "active" in evidence

    def test_started_then_nothing_no_active_agents_is_crashed(self) -> None:
        """Task started but no longer in active set -> crashed (default)."""
        started_msg = _make_task_started("tid-1", "my_task")
        classification, evidence = classify_timeout(
            last_messages=[started_msg],
            active_task_ids=set(),
            task_start_times={},
            effective_timeout=600.0,
        )
        assert classification == "crashed"
        assert "conservative default" in evidence


# ---------------------------------------------------------------------------
# Timeout classification integration tests
# ---------------------------------------------------------------------------


class TestTimeoutClassificationIntegration:
    """Integration: idle watchdog fires with recent progress -> interrupted."""

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
    async def test_idle_watchdog_with_recent_progress_returns_interrupted(
        self, project_dir: Path,
    ) -> None:
        """When the last message is a progress message, timeout -> interrupted."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text('''
class R: ...

def resource_bounds(tokens=None, timeout_seconds=None):
    def decorator(func):
        return func
    return decorator

@resource_bounds(timeout_seconds=1)
async def slow_task() -> R:
    """Task that gets interrupted."""

async def execute():
    r = await slow_task()
''', encoding="utf-8")

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n"
        )

        # The task starts, makes progress, then hangs (idle watchdog fires).
        _hung = asyncio.Event()

        async def _receive_with_progress():
            yield _make_task_started("tid-slow", "slow_task")
            yield _make_task_progress("tid-slow", total_tokens=500)
            # Block indefinitely -- simulates waiting on network/sleep.
            await _hung.wait()

        client = MagicMock()
        client.query = AsyncMock()
        client.stop_task = MagicMock()
        client.receive_response = _receive_with_progress
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{_PC}.ClaudeSDKClient", return_value=client):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # Last message was progress -> classification should be "interrupted".
        assert result == "interrupted"

    @pytest.mark.asyncio
    async def test_idle_watchdog_no_messages_returns_crash(
        self, project_dir: Path,
    ) -> None:
        """When no messages are received at all, timeout -> agent_team_crash."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text('''
class R: ...

def resource_bounds(tokens=None, timeout_seconds=None):
    def decorator(func):
        return func
    return decorator

@resource_bounds(timeout_seconds=1)
async def dead_task() -> R:
    """Task that never sends messages."""

async def execute():
    r = await dead_task()
''', encoding="utf-8")

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n"
        )

        # No messages at all -- hangs immediately.
        _hung = asyncio.Event()

        async def _receive_nothing():
            await _hung.wait()
            # Never yields anything -- empty async generator.
            return
            yield  # noqa: unreachable -- makes this an async generator

        client = MagicMock()
        client.query = AsyncMock()
        client.stop_task = MagicMock()
        client.receive_response = _receive_nothing
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{_PC}.ClaudeSDKClient", return_value=client):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # No messages -> classification should be "crashed".
        assert result == "agent_team_crash"

    @pytest.mark.asyncio
    async def test_idle_watchdog_failed_notification_returns_crash(
        self, project_dir: Path,
    ) -> None:
        """When the last message is a failed notification, timeout -> crash."""
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True)
        (ms_dir / "compose.py").write_text('''
class R: ...

def resource_bounds(tokens=None, timeout_seconds=None):
    def decorator(func):
        return func
    return decorator

@resource_bounds(timeout_seconds=1)
async def failing_task() -> R:
    """Task that fails then hangs."""

async def execute():
    r = await failing_task()
''', encoding="utf-8")

        active_dir = ms_dir / "active"
        active_dir.mkdir()
        (active_dir / "coordinator.md").write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build-phase\nphases_completed: 0\nphases_total: 1\n"
        )

        _hung = asyncio.Event()

        async def _receive_fail_then_hang():
            yield _make_task_started("tid-fail", "failing_task")
            yield _make_task_notification("tid-fail", "failed", "crash")
            # Hang after failure notification.
            await _hung.wait()

        client = MagicMock()
        client.query = AsyncMock()
        client.stop_task = MagicMock()
        client.receive_response = _receive_fail_then_hang
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{_PC}.ClaudeSDKClient", return_value=client):
            result = await _run_single_cycle(
                project_dir, "ms", "EXECUTE", "do work",
            )

        # Last message was a failed notification -> should be "agent_team_crash".
        assert result == "agent_team_crash"

    @pytest.mark.asyncio
    async def test_run_coordinator_interrupted_no_crash_increment(
        self, project_dir: Path,
    ) -> None:
        """Outer run_coordinator loop: interrupted does not increment crash_retries.

        Exercises lines 890-904 of coordinator.py.  When _run_single_cycle
        returns "interrupted", the loop should:
        - NOT increment crash_retries
        - Emit a "timeout_interrupted" telemetry event
        - Continue to the next cycle (not escalate or abort)
        """
        cycle_calls = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal cycle_calls
            cycle_calls += 1
            # First cycle: interrupted (timeout classified as interruption).
            # Second cycle: normal completion.
            return "interrupted" if cycle_calls == 1 else "ok"

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        # Loop continued past "interrupted" and completed normally.
        assert result == "completed"
        assert cycle_calls == 2, (
            f"Expected 2 cycle calls (interrupted + ok), got {cycle_calls}"
        )

        # "timeout_interrupted" telemetry event was emitted.
        interrupted_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "timeout_interrupted"
        ]
        assert len(interrupted_events) == 1, (
            f"Expected 1 timeout_interrupted event, got {len(interrupted_events)}"
        )
        evt_name, evt_attrs = interrupted_events[0]
        assert evt_attrs["milestone"] == "ms"
        assert evt_attrs["cycle_type"] == "EXECUTE"

        # crash_retries was NOT incremented: no "crash" event was emitted
        # (the "crash" event is only emitted when crash_retries increments
        # in the "failed" handler).  Also, no escalation was triggered.
        crash_events = [
            name for name, _ in telemetry_events if name == "crash"
        ]
        assert len(crash_events) == 0, (
            "crash_retries should not increment for interrupted status"
        )


class TestGateTelemetry:
    """Quality gate decision telemetry emission (I3/F3)."""

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

    def _write_checkpoint(
        self, project_dir: Path, milestone: str, step: str, next_step: str,
        cycle: int = 3, phase: str = "impl",
    ) -> None:
        """Write a coordinator checkpoint at the expected path.

        Also writes the milestone marker so the checkpoint is not
        deleted as stale at the start of run_coordinator().
        """
        active_dir = project_dir / ".clou" / "milestones" / milestone / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        (active_dir / "coordinator.md").write_text(
            f"cycle: {cycle}\nstep: {step}\nnext_step: {next_step}\n"
            f"current_phase: {phase}\nphases_completed: 0\nphases_total: 1\n"
        )
        # Write milestone marker so run_coordinator doesn't delete checkpoint
        # as stale (line ~552-555 of coordinator.py).
        marker = project_dir / ".clou" / ".coordinator-milestone"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(milestone)

    @pytest.mark.asyncio
    async def test_assess_accept_emits_gate_decision(
        self, project_dir: Path,
    ) -> None:
        """ASSESS -> VERIFY transition emits assess/accept gate decision."""
        self._write_checkpoint(project_dir, "ms", "ASSESS", "VERIFY")

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("VERIFY", ["intents.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        assert len(gate_events) >= 1
        name, attrs = gate_events[0]
        assert attrs["gate_type"] == "assess"
        assert attrs["decision"] == "accept"
        assert attrs["milestone"] == "ms"
        assert "cycle_num" in attrs

    @pytest.mark.asyncio
    async def test_assess_rework_emits_gate_decision(
        self, project_dir: Path,
    ) -> None:
        """ASSESS -> EXECUTE (rework) emits assess/rework gate decision."""
        self._write_checkpoint(
            project_dir, "ms", "ASSESS", "EXECUTE (rework)",
        )

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        cycle_calls = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal cycle_calls
            cycle_calls += 1
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        assert len(gate_events) >= 1
        name, attrs = gate_events[0]
        assert attrs["gate_type"] == "assess"
        assert attrs["decision"] == "rework"
        assert attrs["milestone"] == "ms"
        assert "cycle_num" in attrs

    @pytest.mark.asyncio
    async def test_verify_accept_emits_gate_decision(
        self, project_dir: Path,
    ) -> None:
        """VERIFY -> EXIT transition emits verify/accept gate decision."""
        self._write_checkpoint(project_dir, "ms", "VERIFY", "EXIT")

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("EXIT", ["handoff.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=5),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        assert len(gate_events) >= 1
        # Find the verify gate event (there may also be an EXIT->COMPLETE event).
        verify_events = [
            (n, a) for n, a in gate_events if a["gate_type"] == "verify"
        ]
        assert len(verify_events) >= 1
        name, attrs = verify_events[0]
        assert attrs["decision"] == "accept"
        assert attrs["milestone"] == "ms"
        assert "cycle_num" in attrs

    @pytest.mark.asyncio
    async def test_verify_rework_emits_gate_decision(
        self, project_dir: Path,
    ) -> None:
        """VERIFY -> EXECUTE (rework) emits verify/rework gate decision."""
        self._write_checkpoint(
            project_dir, "ms", "VERIFY", "EXECUTE (rework)",
        )

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        cycle_calls = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal cycle_calls
            cycle_calls += 1
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=5),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        assert len(gate_events) >= 1
        name, attrs = gate_events[0]
        assert attrs["gate_type"] == "verify"
        assert attrs["decision"] == "rework"
        assert attrs["milestone"] == "ms"
        assert "cycle_num" in attrs

    @pytest.mark.asyncio
    async def test_no_gate_event_for_execute_cycle(
        self, project_dir: Path,
    ) -> None:
        """EXECUTE -> ASSESS has no gate decision (EXECUTE is not a gate)."""
        self._write_checkpoint(project_dir, "ms", "EXECUTE", "ASSESS")

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("ASSESS", ["assessment.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=2),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        # First iteration: step=EXECUTE, no gate event.
        # Second iteration: checkpoint is gone after COMPLETE, so no event.
        # There may be a gate event from the second iteration if the checkpoint
        # was updated by _run_single_cycle, but the first iteration should not
        # produce a gate_type="execute" event (no such gate type exists).
        execute_gate_events = [
            (n, a) for n, a in gate_events
            if a.get("gate_type") == "execute"
        ]
        assert len(execute_gate_events) == 0

    @pytest.mark.asyncio
    async def test_no_gate_event_without_checkpoint(
        self, project_dir: Path,
    ) -> None:
        """No checkpoint -> no gate decision event emitted."""
        # Don't write any checkpoint -- ensure ms dir exists but no checkpoint.
        ms_dir = project_dir / ".clou" / "milestones" / "ms"
        ms_dir.mkdir(parents=True, exist_ok=True)

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        assert len(gate_events) == 0

    @pytest.mark.asyncio
    async def test_gate_telemetry_failure_does_not_crash(
        self, project_dir: Path,
    ) -> None:
        """Telemetry emission failure is caught and logged, not propagated."""
        self._write_checkpoint(project_dir, "ms", "ASSESS", "VERIFY")

        call_count = 0

        def _failing_event(name: str, **attrs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if name == "quality_gate.decision":
                raise RuntimeError("telemetry backend unavailable")

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("VERIFY", ["intents.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_failing_event),
        ):
            # Should not raise -- error isolation catches the exception.
            result = await run_coordinator(project_dir, "ms")

        assert result == "completed"
        # The event function was called (gate telemetry attempted).
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_gate_events_include_required_fields(
        self, project_dir: Path,
    ) -> None:
        """All gate decision events include milestone, cycle_num, gate_type, decision."""
        self._write_checkpoint(project_dir, "ms", "ASSESS", "VERIFY")

        telemetry_events: list[tuple[str, dict[str, Any]]] = []

        def _capture_event(name: str, **attrs: Any) -> None:
            telemetry_events.append((name, attrs))

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("VERIFY", ["intents.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        ):
            await run_coordinator(project_dir, "ms")

        gate_events = [
            (name, attrs) for name, attrs in telemetry_events
            if name == "quality_gate.decision"
        ]
        assert len(gate_events) >= 1

        required_fields = {"milestone", "cycle_num", "gate_type", "decision"}
        for _, attrs in gate_events:
            missing = required_fields - set(attrs.keys())
            assert not missing, f"Missing fields: {missing}"
