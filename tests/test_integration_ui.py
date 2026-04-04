"""Tests for orchestrator <-> Textual app integration.

Verifies that:
- Supervisor messages are routed through bridge when app is provided
- Coordinator messages produce breath events via bridge
- Lifecycle: _active_app is set/cleared by run_coordinator
- Backward compatibility: orchestrator works without app (app=None)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import ResultMessage

from clou.coordinator import (
    _run_single_cycle,
    run_coordinator,
)
from clou.orchestrator import run_supervisor

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

_P = "clou.orchestrator"   # supervisor-resident names
_PC = "clou.coordinator"   # coordinator-resident names


def _make_result(
    usage: dict[str, Any] | None = None,
    result: str | None = "done",
) -> ResultMessage:
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


def _make_assistant_msg(
    text: str = "hello",
) -> SimpleNamespace:
    """Fake AssistantMessage with a TextBlock."""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        model="opus",
    )


def _make_tool_use_msg(
    name: str = "Write",
    tool_input: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Fake AssistantMessage with a ToolUseBlock."""
    block = SimpleNamespace(name=name, input=tool_input or {})
    return SimpleNamespace(content=[block], model="opus")


def _mock_sdk_client(
    messages: list[object] | None = None,
) -> MagicMock:
    client = MagicMock()
    client.query = AsyncMock()

    async def _receive_response() -> Any:
        for msg in messages or [_make_result()]:
            yield msg

    async def _receive_messages() -> Any:
        for msg in messages or [_make_result()]:
            yield msg

    client.receive_response = _receive_response
    client.receive_messages = _receive_messages
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Supervisor routing
# ---------------------------------------------------------------------------


class TestSupervisorRouting:
    """Supervisor messages are routed through bridge when app is given."""

    @pytest.fixture(autouse=True)
    def _patch_io(self) -> Any:
        with (
            patch(f"{_P}.load_prompt", return_value="<system/>"),
            patch(f"{_P}.build_hooks", return_value={}),
            patch(
                f"{_P}._build_mcp_server",
                return_value=MagicMock(),
            ),
            patch(f"{_P}.read_template_name", return_value="software-construction"),
            patch(f"{_P}.load_template", return_value=MagicMock(quality_gates=[])),
        ):
            yield

    @pytest.mark.asyncio
    async def test_route_supervisor_called(self, tmp_path: Path) -> None:
        """route_supervisor_message is called when app is provided."""
        text_msg = _make_assistant_msg("hello from supervisor")
        client = _mock_sdk_client([text_msg])
        mock_app = MagicMock()
        mock_app._resume_session_id = None

        with (
            patch(f"{_P}.ClaudeSDKClient", return_value=client),
            patch(
                "clou.ui.bridge.route_supervisor_message",
            ) as mock_route,
        ):
            (tmp_path / ".clou" / "active").mkdir(parents=True)
            await run_supervisor(tmp_path, app=mock_app)

        # _post resolves to app.query_one(ConversationWidget).post_message
        # (falls back to app.post_message only when query_one raises).
        mock_route.assert_called_once_with(
            text_msg, mock_app.query_one.return_value.post_message
        )

    @pytest.mark.asyncio
    async def test_display_used_when_no_app(self, tmp_path: Path) -> None:
        """_display is used when app is None."""
        text_msg = _make_assistant_msg("hello")
        client = _mock_sdk_client([text_msg])

        with (
            patch(f"{_P}.ClaudeSDKClient", return_value=client),
            patch(f"{_P}._display") as mock_display,
        ):
            (tmp_path / ".clou" / "active").mkdir(parents=True)
            await run_supervisor(tmp_path, app=None)

        mock_display.assert_called_once_with(text_msg)


# ---------------------------------------------------------------------------
# Coordinator routing
# ---------------------------------------------------------------------------


class TestCoordinatorRouting:
    """Coordinator messages route through bridge when _active_app is set."""

    @pytest.fixture(autouse=True)
    def _patch_io(self) -> Any:
        with (
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(
                f"{_PC}.build_hooks",
                return_value={"PreToolUse": []},
            ),
        ):
            yield

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.mark.asyncio
    async def test_route_coordinator_called(self, project_dir: Path) -> None:
        """route_coordinator_message called for each SDK message."""
        tool_msg = _make_tool_use_msg("Write", {"file_path": "/proj/compose.py"})
        final = _make_result(usage={"input_tokens": 100})
        client = _mock_sdk_client([tool_msg, final])
        mock_app = MagicMock()

        import clou.coordinator as coord

        old_app = coord._active_app
        coord._active_app = mock_app

        try:
            with (
                patch(
                    f"{_PC}.ClaudeSDKClient",
                    return_value=client,
                ),
                patch(
                    f"{_PC}.read_cycle_outcome",
                    return_value="ASSESS",
                ),
                patch(
                    "clou.ui.bridge.route_coordinator_message",
                ) as mock_route,
            ):
                await _run_single_cycle(project_dir, "auth", "EXECUTE", "do work", app=mock_app)
        finally:
            coord._active_app = old_app

        assert mock_route.call_count == 2
        first = mock_route.call_args_list[0]
        assert first.args[0] is tool_msg
        assert first.args[1] == "auth"
        assert first.args[2] == "EXECUTE"

    @pytest.mark.asyncio
    async def test_no_routing_when_no_app(self, project_dir: Path) -> None:
        """No routing when _active_app is None."""
        client = _mock_sdk_client([_make_result(usage={"input_tokens": 100})])

        import clou.coordinator as coord

        old_app = coord._active_app
        coord._active_app = None

        try:
            with (
                patch(
                    f"{_PC}.ClaudeSDKClient",
                    return_value=client,
                ),
                patch(
                    f"{_PC}.read_cycle_outcome",
                    return_value="ASSESS",
                ),
                patch(
                    "clou.ui.bridge.route_coordinator_message",
                ) as mock_route,
            ):
                await _run_single_cycle(project_dir, "auth", "PLAN", "plan it")
        finally:
            coord._active_app = old_app

        mock_route.assert_not_called()


# ---------------------------------------------------------------------------
# Lifecycle: _active_app management
# ---------------------------------------------------------------------------


class TestActiveAppLifecycle:
    """run_coordinator sets and cleans up _active_app."""

    @pytest.mark.asyncio
    async def test_app_set_during_coordinator(self, tmp_path: Path) -> None:
        """_active_app is cleaned up after run_coordinator returns."""
        mock_app = MagicMock()

        with patch(
            f"{_PC}.determine_next_cycle",
            return_value=("COMPLETE", []),
        ):
            await run_coordinator(tmp_path, "auth", app=mock_app)

        import clou.coordinator as coord

        assert coord._active_app is None

    @pytest.mark.asyncio
    async def test_app_cleaned_on_error(self, tmp_path: Path) -> None:
        """_active_app is None even after run_coordinator raises."""
        import clou.coordinator as coord

        mock_app = MagicMock()

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await run_coordinator(tmp_path, "auth", app=mock_app)

        assert coord._active_app is None

    @pytest.mark.asyncio
    async def test_app_none_by_default(self, tmp_path: Path) -> None:
        """_active_app is None when no app is passed."""
        import clou.coordinator as coord

        with patch(
            f"{_PC}.determine_next_cycle",
            return_value=("COMPLETE", []),
        ):
            await run_coordinator(tmp_path, "auth")

        assert coord._active_app is None


# ---------------------------------------------------------------------------
# _build_mcp_server integration
# ---------------------------------------------------------------------------


class TestBuildMcpServerIntegration:
    """_build_mcp_server posts lifecycle messages when app is given."""

    @pytest.mark.asyncio
    async def test_build_mcp_server_accepts_app(self, tmp_path: Path) -> None:
        """_build_mcp_server accepts an app parameter without error."""
        from clou.orchestrator import _build_mcp_server

        mock_app = MagicMock()
        mock_app.post_message = MagicMock(return_value=True)

        # Build the server with app — this creates closures
        # that capture the app reference.
        with (
            patch(
                f"{_P}.clou_spawn_coordinator",
                new_callable=AsyncMock,
            ),
            patch(
                f"{_P}.validate_milestone_name",
            ),
            patch(
                f"{_P}.run_coordinator",
                new_callable=AsyncMock,
                return_value="completed",
            ),
        ):
            server = _build_mcp_server(tmp_path, app=mock_app)

            # Verify the app parameter was accepted without
            # error and the server was created.
            assert server is not None

    @pytest.mark.asyncio
    async def test_server_created_without_app(self, tmp_path: Path) -> None:
        """_build_mcp_server works without app (backward compat)."""
        from clou.orchestrator import _build_mcp_server

        server = _build_mcp_server(tmp_path)
        assert server is not None
