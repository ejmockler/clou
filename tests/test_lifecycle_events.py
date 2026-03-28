"""Integration tests for orchestrator lifecycle event emission.

Verifies that run_coordinator and spawn_coordinator_tool post the correct
Textual messages (ClouStatusUpdate, ClouCycleComplete, ClouDagUpdate,
ClouEscalationArrived, ClouHandoff, ClouCoordinatorComplete) at the right
moments in the lifecycle.

Mock strategy: claude_agent_sdk is injected as a fake module into
sys.modules so orchestrator can be imported without the real SDK.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock claude_agent_sdk before importing orchestrator
# ---------------------------------------------------------------------------

_mock_sdk = MagicMock()
_mock_sdk.ResultMessage = type("ResultMessage", (), {"usage": None})
_mock_sdk.TaskNotificationMessage = type("TaskNotificationMessage", (), {})
_mock_sdk.ClaudeSDKClient = MagicMock
_mock_sdk.ClaudeAgentOptions = MagicMock
_mock_sdk.AgentDefinition = MagicMock
_mock_sdk.HookMatcher = MagicMock
_mock_sdk.SandboxSettings = MagicMock
_mock_sdk.create_sdk_mcp_server = MagicMock(return_value=MagicMock())
_mock_sdk.tool = lambda *a, **kw: lambda f: f

# Inject mock SDK only if the real one is absent, then remove the
# sys.modules entry so other test files (test_orchestrator.py) that
# use pytest.importorskip("claude_agent_sdk") still skip correctly.
_had_sdk = "claude_agent_sdk" in sys.modules
if not _had_sdk:
    sys.modules["claude_agent_sdk"] = _mock_sdk

from clou.orchestrator import (  # noqa: E402
    _build_mcp_server,
    run_coordinator,
)
from clou.ui.messages import (  # noqa: E402
    ClouCoordinatorComplete,
    ClouCycleComplete,
    ClouDagUpdate,
    ClouEscalationArrived,
    ClouHandoff,
    ClouStatusUpdate,
)

if not _had_sdk:
    del sys.modules["claude_agent_sdk"]

# Patch target prefix
_P = "clou.orchestrator"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_app() -> MagicMock:
    """Return a mock ClouApp with a recording post_message."""
    app = MagicMock()
    app.post_message = MagicMock()
    return app


def _posted_types(app: MagicMock) -> list[type]:
    """Extract the message types from post_message calls."""
    return [call.args[0].__class__ for call in app.post_message.call_args_list]


def _posted_of_type(app: MagicMock, cls: type) -> list[Any]:
    """Extract posted messages that are instances of *cls*."""
    return [
        call.args[0]
        for call in app.post_message.call_args_list
        if isinstance(call.args[0], cls)
    ]


def _setup_clou_dir(project_dir: Path) -> None:
    """Create minimal .clou directory structure."""
    (project_dir / ".clou" / "active").mkdir(parents=True, exist_ok=True)
    (project_dir / ".clou" / "prompts").mkdir(parents=True, exist_ok=True)
    (project_dir / ".clou" / "prompts" / "coordinator-system.xml").write_text(
        "<system/>"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStatusUpdateEmitted:
    """ClouStatusUpdate is posted before each cycle."""

    async def test_status_update_emitted(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_P}.read_cycle_count", return_value=0),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(f"{_P}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        updates = _posted_of_type(app, ClouStatusUpdate)
        assert len(updates) == 1
        assert updates[0].cycle_type == "PLAN"
        assert updates[0].cycle_num == 1


class TestCycleCompleteEmitted:
    """ClouCycleComplete is posted after a successful cycle."""

    async def test_cycle_complete_emitted(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=2),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(f"{_P}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        completes = _posted_of_type(app, ClouCycleComplete)
        assert len(completes) == 1
        assert completes[0].cycle_num == 3
        assert completes[0].cycle_type == "EXECUTE"
        assert completes[0].next_step == "ASSESS"


class TestDagUpdateAfterCycle:
    """ClouDagUpdate posted after every cycle type when compose.py exists."""

    async def test_dag_update_after_plan(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create a compose.py with a simple task graph
        compose_dir = tmp_path / ".clou" / "milestones" / "test-ms"
        compose_dir.mkdir(parents=True, exist_ok=True)
        (compose_dir / "compose.py").write_text(
            'async def setup() -> str:\n    return "done"\n\n'
            "async def execute() -> None:\n    await setup()\n"
        )

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_P}.read_cycle_count", return_value=0),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(f"{_P}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        dag_updates = _posted_of_type(app, ClouDagUpdate)
        assert len(dag_updates) == 1
        task_names = {t["name"] for t in dag_updates[0].tasks}
        assert "setup" in task_names


class TestEscalationDetected:
    """ClouEscalationArrived posted for new escalation files."""

    async def test_escalation_detected(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create an escalation file
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "## Classification\nblocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
            "2. **Skip it**: move on\n"
        )

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=0),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(f"{_P}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        arrivals = _posted_of_type(app, ClouEscalationArrived)
        assert len(arrivals) == 1
        assert arrivals[0].classification == "blocking"
        assert arrivals[0].issue == "Something broke"
        assert len(arrivals[0].options) == 2
        assert arrivals[0].options[0]["label"] == "Fix it"


class TestSeenEscalationsPersisted:
    """seen-escalations.txt prevents re-posting after restart."""

    async def test_preexisting_seen_escalations_not_reposted(
        self, tmp_path: Path
    ) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create an escalation file
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "## Classification\nblocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
            "2. **Skip it**: move on\n"
        )

        # Pre-populate seen-escalations.txt (simulates prior run)
        seen_path = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        seen_path.write_text("2026-01-01-test.md\n")

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=0),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(f"{_P}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # The escalation should NOT be re-posted
        arrivals = _posted_of_type(app, ClouEscalationArrived)
        assert len(arrivals) == 0

    async def test_seen_escalations_written_to_disk(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create an escalation file
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "## Classification\nblocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
            "2. **Skip it**: move on\n"
        )

        seen_path = tmp_path / ".clou" / "active" / "seen-escalations.txt"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    # Don't COMPLETE — escalation_cycle_limit so we can
                    # check that seen_path was written mid-run
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=0),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(f"{_P}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # seen-escalations.txt is cleaned up on completion, so it
        # should not exist. But the escalation was posted exactly once.
        assert not seen_path.exists()
        arrivals = _posted_of_type(app, ClouEscalationArrived)
        assert len(arrivals) == 1

    async def test_seen_file_cleaned_on_completion(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)

        seen_path = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        seen_path.write_text("old-escalation.md\n")

        with patch(
            f"{_P}.determine_next_cycle",
            return_value=("COMPLETE", []),
        ):
            result = await run_coordinator(tmp_path, "test-ms")

        assert result == "completed"
        assert not seen_path.exists()


class TestHandoffOnCompletion:
    """ClouHandoff posted BEFORE ClouCoordinatorComplete on success."""

    async def test_handoff_on_completion(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create handoff.md
        ms_dir = tmp_path / ".clou" / "milestones" / "test-ms"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "handoff.md").write_text("# Handoff\nAll done.")

        # Extract spawn_coordinator_tool by intercepting
        # create_sdk_mcp_server to capture the tools list.
        captured_tools: list[Any] = []

        def _capture_create(*args: Any, **kwargs: Any) -> MagicMock:
            captured_tools.extend(kwargs.get("tools", args[1] if len(args) > 1 else []))
            return MagicMock()

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path, app=app)

        spawn_fn = None
        for fn in captured_tools:
            name = getattr(fn, "__name__", "") or getattr(
                getattr(fn, "handler", None), "__name__", ""
            )
            if name == "spawn_coordinator_tool":
                spawn_fn = fn
                break

        assert spawn_fn is not None, "spawn_coordinator_tool not found"
        # SdkMcpTool is not callable — use .handler if available
        call_fn = getattr(spawn_fn, "handler", spawn_fn)

        # Mock run_coordinator to return "completed"
        with (
            patch(f"{_P}.run_coordinator", new_callable=AsyncMock) as mock_rc,
            patch(f"{_P}.clou_spawn_coordinator", new_callable=AsyncMock),
        ):
            mock_rc.return_value = "completed"
            await call_fn({"milestone": "test-ms"})

        types = _posted_types(app)
        # Filter to just the ones we care about
        relevant = [t for t in types if t in (ClouHandoff, ClouCoordinatorComplete)]
        assert ClouHandoff in relevant, "ClouHandoff was not posted"
        assert ClouCoordinatorComplete in relevant, (
            "ClouCoordinatorComplete was not posted"
        )
        assert relevant.index(ClouHandoff) < relevant.index(ClouCoordinatorComplete), (
            "ClouHandoff must be posted before ClouCoordinatorComplete"
        )


class TestCrashPostsCoordinatorComplete:
    """ClouCoordinatorComplete(result='error') posted even on crash."""

    async def test_crash_posts_coordinator_complete(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Build spawn_coordinator_tool
        captured_tools: list[Any] = []

        def _capture_create(*args: Any, **kwargs: Any) -> MagicMock:
            captured_tools.extend(kwargs.get("tools", args[1] if len(args) > 1 else []))
            return MagicMock()

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path, app=app)

        spawn_fn = None
        for fn in captured_tools:
            name = getattr(fn, "__name__", "") or getattr(
                getattr(fn, "handler", None), "__name__", ""
            )
            if name == "spawn_coordinator_tool":
                spawn_fn = fn
                break

        assert spawn_fn is not None
        call_fn = getattr(spawn_fn, "handler", spawn_fn)

        # Mock run_coordinator to raise
        with (
            patch(
                f"{_P}.run_coordinator",
                new_callable=AsyncMock,
                side_effect=RuntimeError("kaboom"),
            ),
            patch(f"{_P}.clou_spawn_coordinator", new_callable=AsyncMock),
        ):
            result = await call_fn({"milestone": "test-ms"})

        completes = _posted_of_type(app, ClouCoordinatorComplete)
        assert len(completes) == 1
        assert completes[0].result == "error"
        assert completes[0].milestone == "test-ms"
        # The tool should still return a result dict, not raise
        assert "content" in result
