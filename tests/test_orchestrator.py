"""Tests for clou.orchestrator — helpers and control flow integration.

Mock strategy (design principle #2 — mock at the boundary of your control):
- ClaudeSDKClient is the SDK boundary → mocked
- Our own modules (recovery, validation, hooks, prompts) → real where practical,
  patched only when they do file I/O that would make tests fragile without
  adding signal (those modules have their own test suites)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (
    HookMatcher,
    ResultMessage,
    TaskNotificationMessage,
)

from clou.hooks import HookConfig
from clou.orchestrator import (
    _MAX_CRASH_RETRIES,
    _context_exhausted,
    _display,
    _run_single_cycle,
    _to_sdk_hooks,
    run_coordinator,
    validate_milestone_name,
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
    status: str = "completed",
    summary: str = "task done",
) -> TaskNotificationMessage:
    """Create a TaskNotificationMessage."""
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id="task-1",
        status=status,
        output_file=None,
        summary=summary,
        uuid="uuid-1",
        session_id="test",
    )


def _mock_sdk_client(messages: list[object] | None = None) -> MagicMock:
    """Build a mock ClaudeSDKClient that yields given messages.

    The mock supports the async context manager protocol and
    query() + receive_response() methods used by _run_single_cycle.
    """
    client = MagicMock()
    client.query = AsyncMock()

    async def _receive_response():
        for msg in messages or [_make_result()]:
            yield msg

    client.receive_response = _receive_response

    # Async context manager
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return client


# Patch targets — all in clou.orchestrator namespace
_P = "clou.orchestrator"


# ---------------------------------------------------------------------------
# validate_milestone_name
# ---------------------------------------------------------------------------


class TestValidateMilestoneName:
    """Milestone names must be lowercase alphanumeric + hyphens."""

    def test_valid_simple(self) -> None:
        validate_milestone_name("auth")

    def test_valid_with_hyphens(self) -> None:
        validate_milestone_name("user-auth-v2")

    def test_valid_number_start(self) -> None:
        validate_milestone_name("1st-milestone")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid milestone name"):
            validate_milestone_name("Auth")

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="Invalid milestone name"):
            validate_milestone_name("../evil")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid milestone name"):
            validate_milestone_name("")

    def test_rejects_hyphen_start(self) -> None:
        with pytest.raises(ValueError, match="Invalid milestone name"):
            validate_milestone_name("-auth")

    def test_rejects_special_chars(self) -> None:
        for bad in ["user_auth", "v1.0", "a/b", "user auth"]:
            with pytest.raises(ValueError):
                validate_milestone_name(bad)


# ---------------------------------------------------------------------------
# _to_sdk_hooks
# ---------------------------------------------------------------------------


class TestToSdkHooks:
    """Convert internal HookConfig to SDK HookMatcher."""

    def test_converts_single_event(self) -> None:
        callback = AsyncMock()
        configs = {
            "PreToolUse": [HookConfig(matcher="Write", hooks=[callback])],
        }
        result = _to_sdk_hooks(configs)

        assert "PreToolUse" in result
        hook_matcher = result["PreToolUse"][0]
        assert isinstance(hook_matcher, HookMatcher)
        assert hook_matcher.matcher == "Write"

    def test_converts_multiple_events(self) -> None:
        configs = {
            "PreToolUse": [HookConfig(matcher="Write", hooks=[AsyncMock()])],
            "PostToolUse": [HookConfig(matcher="Edit", hooks=[AsyncMock()])],
        }
        result = _to_sdk_hooks(configs)
        assert set(result.keys()) == {"PreToolUse", "PostToolUse"}


# ---------------------------------------------------------------------------
# _display
# ---------------------------------------------------------------------------


class TestDisplay:
    """Display function writes message content to stdout."""

    def test_ignores_result_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        _display(_make_result())
        assert capsys.readouterr().out == ""

    def test_writes_string_content(self, capsys: pytest.CaptureFixture[str]) -> None:
        _display(SimpleNamespace(content="hello world"))
        assert capsys.readouterr().out == "hello world"

    def test_writes_block_content(self, capsys: pytest.CaptureFixture[str]) -> None:
        _display(SimpleNamespace(content=[SimpleNamespace(text="block")]))
        assert capsys.readouterr().out == "block"


# ---------------------------------------------------------------------------
# _context_exhausted
# ---------------------------------------------------------------------------


class TestContextExhausted:
    def test_below_threshold(self) -> None:
        msg = _make_result(usage={"input_tokens": 100_000})
        assert _context_exhausted(msg) is False

    def test_above_threshold(self) -> None:
        msg = _make_result(usage={"input_tokens": 200_000})
        assert _context_exhausted(msg) is True


# ---------------------------------------------------------------------------
# _run_single_cycle — SDK boundary mock
# ---------------------------------------------------------------------------


class TestRunSingleCycle:
    """Test the single-cycle execution with mocked SDK client.

    load_prompt and _build_agents are patched — they do file I/O for
    prompt templates and are already tested in test_prompts.py. What we're
    testing here is the control flow: how _run_single_cycle responds to
    SDK messages (context exhaustion, agent crash, exceptions).
    """

    @pytest.fixture(autouse=True)
    def _patch_prompt_io(self) -> Any:
        """Patch file-I/O functions that aren't under test."""
        with (
            patch(f"{_P}.load_prompt", return_value="<system/>"),
            patch(f"{_P}._build_agents", return_value={}),
            patch(f"{_P}.build_hooks", return_value={"PreToolUse": []}),
        ):
            yield

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.mark.asyncio
    async def test_normal_completion(self, project_dir: Path) -> None:
        """Normal cycle: SDK returns ResultMessage, we read cycle outcome."""
        client = _mock_sdk_client([_make_result(usage={"input_tokens": 1000})])

        with (
            patch(f"{_P}.ClaudeSDKClient", return_value=client),
            patch(f"{_P}.read_cycle_outcome", return_value="EXECUTE"),
        ):
            result = await _run_single_cycle(project_dir, "auth", "PLAN", "do the plan")

        assert result == "EXECUTE"
        client.query.assert_called_once_with("do the plan")

    @pytest.mark.asyncio
    async def test_context_exhaustion_triggers_checkpoint(
        self, project_dir: Path
    ) -> None:
        """When input_tokens exceed threshold, send checkpoint query."""
        exhausted_msg = _make_result(usage={"input_tokens": 200_000})
        client = _mock_sdk_client([exhausted_msg])

        with patch(f"{_P}.ClaudeSDKClient", return_value=client):
            result = await _run_single_cycle(project_dir, "auth", "EXECUTE", "do work")

        assert result == "exhausted"
        # Should have called query twice: initial + checkpoint
        assert client.query.call_count == 2
        checkpoint_call = client.query.call_args_list[1]
        assert "checkpoint" in checkpoint_call.args[0].lower()

    @pytest.mark.asyncio
    async def test_agent_crash_detected(self, project_dir: Path) -> None:
        """TaskNotificationMessage with status=failed triggers crash path."""
        crash_msg = _make_task_notification(status="failed", summary="OOM killed")
        client = _mock_sdk_client([crash_msg])

        with patch(f"{_P}.ClaudeSDKClient", return_value=client):
            result = await _run_single_cycle(project_dir, "auth", "EXECUTE", "do work")

        assert result == "agent_team_crash"
        assert client.query.call_count == 2
        crash_call = client.query.call_args_list[1]
        assert "crashed" in crash_call.args[0].lower()

    @pytest.mark.asyncio
    async def test_completed_task_not_treated_as_crash(self, project_dir: Path) -> None:
        """TaskNotificationMessage with status=completed is normal."""
        ok_msg = _make_task_notification(status="completed")
        final = _make_result(usage={"input_tokens": 1000})
        client = _mock_sdk_client([ok_msg, final])

        with (
            patch(f"{_P}.ClaudeSDKClient", return_value=client),
            patch(f"{_P}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(project_dir, "auth", "EXECUTE", "do work")

        assert result == "ASSESS"

    @pytest.mark.asyncio
    async def test_sdk_exception_returns_failed(self, project_dir: Path) -> None:
        """If the SDK throws, cycle returns 'failed' (not crash)."""
        client = MagicMock()
        client.__aenter__ = AsyncMock(side_effect=RuntimeError("connection lost"))
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{_P}.ClaudeSDKClient", return_value=client):
            result = await _run_single_cycle(project_dir, "auth", "PLAN", "plan it")

        assert result == "failed"

    @pytest.mark.asyncio
    async def test_effort_max_for_assess(self, project_dir: Path) -> None:
        """ASSESS cycle type should use effort='max'."""
        client = _mock_sdk_client([_make_result()])
        captured_options = {}

        def capture_client(*args: Any, **kwargs: Any) -> MagicMock:
            captured_options.update(kwargs.get("options", {}).__dict__)
            return client

        with (
            patch(f"{_P}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_P}.read_cycle_outcome", return_value="VERIFY"),
        ):
            await _run_single_cycle(project_dir, "auth", "ASSESS", "assess it")

        assert captured_options.get("effort") == "max"

    @pytest.mark.asyncio
    async def test_effort_max_for_verify(self, project_dir: Path) -> None:
        """VERIFY cycle type should use effort='max'."""
        client = _mock_sdk_client([_make_result()])
        captured_options = {}

        def capture_client(*args: Any, **kwargs: Any) -> MagicMock:
            captured_options.update(kwargs.get("options", {}).__dict__)
            return client

        with (
            patch(f"{_P}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_P}.read_cycle_outcome", return_value="COMPLETE"),
        ):
            await _run_single_cycle(project_dir, "auth", "VERIFY", "verify it")

        assert captured_options.get("effort") == "max"

    @pytest.mark.asyncio
    async def test_effort_high_for_execute(self, project_dir: Path) -> None:
        """EXECUTE cycle type should use effort='high'."""
        client = _mock_sdk_client([_make_result()])
        captured_options = {}

        def capture_client(*args: Any, **kwargs: Any) -> MagicMock:
            captured_options.update(kwargs.get("options", {}).__dict__)
            return client

        with (
            patch(f"{_P}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_P}.read_cycle_outcome", return_value="ASSESS"),
        ):
            await _run_single_cycle(project_dir, "auth", "EXECUTE", "do it")

        assert captured_options.get("effort") == "high"

    @pytest.mark.asyncio
    async def test_sandbox_enabled(self, project_dir: Path) -> None:
        """Coordinator sessions must have sandbox enabled."""
        client = _mock_sdk_client([_make_result()])
        captured_options = {}

        def capture_client(*args: Any, **kwargs: Any) -> MagicMock:
            opts = kwargs.get("options")
            if opts:
                captured_options.update(opts.__dict__)
            return client

        with (
            patch(f"{_P}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_P}.read_cycle_outcome", return_value="PLAN"),
        ):
            await _run_single_cycle(project_dir, "auth", "PLAN", "plan it")

        sandbox = captured_options.get("sandbox")
        assert sandbox is not None
        assert sandbox.get("enabled") is True

    @pytest.mark.asyncio
    async def test_max_budget_defaults_unlimited(self, project_dir: Path) -> None:
        """Coordinator sessions default to unlimited budget (None)."""
        client = _mock_sdk_client([_make_result()])
        captured_options = {}

        def capture_client(*args: Any, **kwargs: Any) -> MagicMock:
            opts = kwargs.get("options")
            if opts:
                captured_options.update(opts.__dict__)
            return client

        with (
            patch(f"{_P}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_P}.read_cycle_outcome", return_value="PLAN"),
        ):
            await _run_single_cycle(project_dir, "auth", "PLAN", "plan it")

        assert captured_options.get("max_budget_usd") is None

    @pytest.mark.asyncio
    async def test_milestone_passed_to_hooks(self, project_dir: Path) -> None:
        """build_hooks must receive the milestone for scoping."""
        client = _mock_sdk_client([_make_result()])

        with (
            patch(f"{_P}.ClaudeSDKClient", return_value=client),
            patch(f"{_P}.read_cycle_outcome", return_value="PLAN"),
            patch(f"{_P}.build_hooks", wraps=None) as mock_hooks,
        ):
            mock_hooks.return_value = {"PreToolUse": []}
            await _run_single_cycle(project_dir, "auth", "PLAN", "plan it")

        mock_hooks.assert_called_once()
        _, kwargs = mock_hooks.call_args
        assert kwargs.get("milestone") == "auth"


# ---------------------------------------------------------------------------
# run_coordinator — cycle loop control flow
# ---------------------------------------------------------------------------


class TestRunCoordinator:
    """Test the session-per-cycle loop in run_coordinator.

    These tests verify the orchestrator's control flow decisions:
    cycle routing, escalation triggers, validation retries, crash recovery.
    """

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        """Create minimal .clou structure."""
        active = tmp_path / ".clou" / "active"
        active.mkdir(parents=True)
        prompts = tmp_path / ".clou" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "coordinator-system.xml").write_text("<system/>")
        return tmp_path

    @pytest.mark.asyncio
    async def test_immediate_completion(self, project_dir: Path) -> None:
        """COMPLETE cycle type → return 'completed' without running."""
        with patch(f"{_P}.determine_next_cycle", return_value=("COMPLETE", [])):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"

    @pytest.mark.asyncio
    async def test_cycle_limit_escalation(self, project_dir: Path) -> None:
        """20-cycle limit → write escalation and return."""
        with (
            patch(
                f"{_P}.determine_next_cycle",
                return_value=("PLAN", ["milestone.md"]),
            ),
            patch(f"{_P}.read_cycle_count", return_value=20),
            patch(
                f"{_P}.write_cycle_limit_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_cycle_limit"
        mock_esc.assert_called_once_with(project_dir, "auth", 20)

    @pytest.mark.asyncio
    async def test_agent_crash_escalation(self, project_dir: Path) -> None:
        """Agent team crash → write escalation and return."""
        with (
            patch(
                f"{_P}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", return_value="agent_team_crash"),
            patch(
                f"{_P}.write_agent_crash_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_agent_crash"
        mock_esc.assert_called_once_with(project_dir, "auth")

    @pytest.mark.asyncio
    async def test_failed_cycle_retries(self, project_dir: Path) -> None:
        """Failed cycle → retry (not escalate). Second attempt succeeds."""
        call_count = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            return "failed" if call_count == 1 else "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert call_count == 2  # first failed, second succeeded

    @pytest.mark.asyncio
    async def test_validation_failure_triggers_git_revert(
        self, project_dir: Path
    ) -> None:
        """Validation failure → git revert → retry with errors in prompt."""
        cycle_calls = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal cycle_calls
            cycle_calls += 1
            return "ok"

        validation_results = iter(
            [["missing ## Cycle"], []]  # first: fail  # second: pass
        )

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(
                f"{_P}.validate_golden_context",
                side_effect=lambda *a: next(validation_results),
            ),
            patch(
                f"{_P}.git_revert_golden_context", new_callable=AsyncMock
            ) as mock_revert,
            patch(f"{_P}.build_cycle_prompt", return_value="retry prompt"),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_revert.assert_called_once_with(project_dir, "auth")
        # Two cycles: first failed validation + retry, second passed
        assert cycle_calls == 2

    @pytest.mark.asyncio
    async def test_validation_escalation_after_three_failures(
        self, project_dir: Path
    ) -> None:
        """3 consecutive validation failures → escalate."""
        with (
            patch(
                f"{_P}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(
                f"{_P}.validate_golden_context",
                return_value=["bad structure"],
            ),
            patch(f"{_P}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_P}.build_cycle_prompt", return_value="retry"),
            patch(
                f"{_P}.write_validation_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_validation"
        mock_esc.assert_called_once()
        # Verify the errors are passed through
        call_args = mock_esc.call_args
        assert call_args.args[2] == ["bad structure"]

    @pytest.mark.asyncio
    async def test_validation_retries_reset_on_success(self, project_dir: Path) -> None:
        """Validation retry counter resets when a cycle passes validation."""
        validation_sequence = iter(
            [
                ["error1"],  # cycle 1: fail (retry=1)
                [],  # cycle 1 retry: pass (retry=0)
                ["error2"],  # cycle 2: fail (retry=1)
                ["error3"],  # cycle 2 retry: fail (retry=2)
                [],  # cycle 2 re-retry: pass (retry=0)
            ]
        )

        cycle_count = 0

        async def _cycle(*a: Any, **kw: Any) -> str:
            nonlocal cycle_count
            cycle_count += 1
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["s"]),  # cycle 1
                    ("EXECUTE", ["s"]),  # cycle 1 retry
                    ("EXECUTE", ["s"]),  # cycle 2
                    ("EXECUTE", ["s"]),  # cycle 2 retry
                    ("EXECUTE", ["s"]),  # cycle 2 re-retry
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(
                f"{_P}.validate_golden_context",
                side_effect=lambda *a: next(validation_sequence),
            ),
            patch(f"{_P}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_P}.build_cycle_prompt", return_value="p"),
        ):
            result = await run_coordinator(project_dir, "auth")

        # Should NOT escalate — retries never hit 3 consecutively
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_git_revert_failure_doesnt_crash_loop(
        self, project_dir: Path
    ) -> None:
        """Git revert failure is logged but loop continues."""
        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["s"]),
                    ("EXECUTE", ["s"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", return_value="ok"),
            patch(
                f"{_P}.validate_golden_context",
                side_effect=[["bad"], []],
            ),
            patch(
                f"{_P}.git_revert_golden_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("git broken"),
            ),
            patch(f"{_P}.build_cycle_prompt", return_value="p"),
        ):
            result = await run_coordinator(project_dir, "auth")

        # Loop survived the git failure and completed
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_full_lifecycle_plan_execute_verify_complete(
        self, project_dir: Path
    ) -> None:
        """Full lifecycle: PLAN → EXECUTE → VERIFY → COMPLETE."""
        cycles_run: list[str] = []

        async def _cycle(pd: Path, ms: str, ct: str, prompt: str) -> str:
            cycles_run.append(ct)
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("PLAN", ["milestone.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("VERIFY", ["requirements.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert cycles_run == ["PLAN", "EXECUTE", "VERIFY"]

    @pytest.mark.asyncio
    async def test_crash_retry_limit_escalates(self, project_dir: Path) -> None:
        """3 consecutive crash failures → escalate crash loop."""
        with (
            patch(
                f"{_P}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", return_value="failed"),
            patch(
                f"{_P}.write_agent_crash_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_crash_loop"
        mock_esc.assert_called_once_with(project_dir, "auth")

    @pytest.mark.asyncio
    async def test_crash_retries_reset_on_success(self, project_dir: Path) -> None:
        """Crash retry counter resets after a successful cycle."""
        call_count = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            # Fail twice, succeed, then fail twice more, succeed, complete
            if call_count in (1, 2, 4, 5):
                return "failed"
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["s"]),  # call 1: failed
                    ("EXECUTE", ["s"]),  # call 2: failed
                    ("EXECUTE", ["s"]),  # call 3: ok
                    ("EXECUTE", ["s"]),  # call 4: failed (reset)
                    ("EXECUTE", ["s"]),  # call 5: failed
                    ("EXECUTE", ["s"]),  # call 6: ok
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        # Should NOT escalate — crash retries reset after each success
        assert result == "completed"
        assert call_count == 6

    @pytest.mark.asyncio
    async def test_crash_retry_constant_is_three(self) -> None:
        """The crash retry limit constant is 3."""
        assert _MAX_CRASH_RETRIES == 3

    @pytest.mark.asyncio
    async def test_exhausted_continues_from_checkpoint(self, project_dir: Path) -> None:
        """Exhausted status resets crash_retries and continues the loop."""
        call_count = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "exhausted"
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert call_count == 2  # first exhausted, second succeeded

    @pytest.mark.asyncio
    async def test_pending_validation_errors_wired_to_build_cycle_prompt(
        self, project_dir: Path
    ) -> None:
        """Validation errors from cycle N are passed to build_cycle_prompt in cycle N+1,
        then cleared (None) on the cycle after that."""
        cycle_count = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal cycle_count
            cycle_count += 1
            return "ok"

        validation_results = iter(
            [["missing field: status"], []]  # cycle 1: fail  # cycle 2: pass
        )

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(
                f"{_P}.validate_golden_context",
                side_effect=lambda *a: next(validation_results),
            ),
            patch(f"{_P}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_P}.build_cycle_prompt", return_value="p") as mock_bcp,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert cycle_count == 2
        assert mock_bcp.call_count == 2

        # First call (initial cycle): no validation errors yet
        first_call_kwargs = mock_bcp.call_args_list[0]
        assert first_call_kwargs.kwargs.get("validation_errors") is None

        # Second call (retry after validation failure): errors passed through
        second_call_kwargs = mock_bcp.call_args_list[1]
        assert second_call_kwargs.kwargs.get("validation_errors") == [
            "missing field: status"
        ]

    @pytest.mark.asyncio
    async def test_execute_cycle_triggers_git_commit(self, project_dir: Path) -> None:
        """After a successful EXECUTE cycle, git_commit_phase is called."""
        # Write a checkpoint so parse_checkpoint can extract current_phase
        cp_path = project_dir / ".clou" / "active" / "coordinator.md"
        cp_path.write_text("cycle: 2\ncurrent_phase: design\nnext_step: ASSESS\n")

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(
                f"{_P}.git_commit_phase", new_callable=AsyncMock
            ) as mock_commit,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_commit.assert_called_once_with(project_dir, "auth", "design")

    @pytest.mark.asyncio
    async def test_git_commit_failure_does_not_crash_loop(
        self, project_dir: Path
    ) -> None:
        """Git commit failure is logged but loop continues."""
        cp_path = project_dir / ".clou" / "active" / "coordinator.md"
        cp_path.write_text("cycle: 2\ncurrent_phase: impl\nnext_step: ASSESS\n")

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(
                f"{_P}.git_commit_phase",
                new_callable=AsyncMock,
                side_effect=RuntimeError("git broken"),
            ),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"

    @pytest.mark.asyncio
    async def test_non_execute_cycle_skips_git_commit(self, project_dir: Path) -> None:
        """PLAN cycle does not trigger git_commit_phase."""
        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_P}.determine_next_cycle",
                side_effect=[
                    ("PLAN", ["milestone.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_P}.read_cycle_count", return_value=1),
            patch(f"{_P}._run_single_cycle", side_effect=_cycle),
            patch(f"{_P}.validate_golden_context", return_value=[]),
            patch(
                f"{_P}.git_commit_phase", new_callable=AsyncMock
            ) as mock_commit,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_milestone_validation_at_coordinator_boundary(
        self, project_dir: Path
    ) -> None:
        """run_coordinator validates milestone name at its own boundary."""
        with pytest.raises(ValueError, match="Invalid milestone name"):
            await run_coordinator(project_dir, "../evil")
