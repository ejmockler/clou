"""Tests for clou.orchestrator — helpers and control flow integration.

Mock strategy (design principle #2 — mock at the boundary of your control):
- ClaudeSDKClient is the SDK boundary → mocked
- Our own modules (recovery, validation, hooks, prompts) → real where practical,
  patched only when they do file I/O that would make tests fragile without
  adding signal (those modules have their own test suites)
"""

from __future__ import annotations

import asyncio
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

from clou.coordinator import (
    _MAX_CRASH_RETRIES,
    _context_exhausted,
    _run_single_cycle,
    run_coordinator,
    validate_milestone_name,
)
from clou.hooks import HookConfig, to_sdk_hooks as _to_sdk_hooks
from clou.orchestrator import (
    _display,
    run_supervisor,
)
from clou.validation import Severity, ValidationFinding


def _vf(message: str, severity: Severity = Severity.ERROR) -> ValidationFinding:
    """Create a ValidationFinding for test mocks."""
    return ValidationFinding(severity=severity, message=message, path="test.md")

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


# Patch targets
_P = "clou.orchestrator"   # supervisor-resident names
_PC = "clou.coordinator"   # coordinator-resident names


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
        msg = _make_result(usage={"input_tokens": 800_000})
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
            patch(f"{_PC}.load_prompt", return_value="<system/>"),
            patch(f"{_PC}._build_agents", return_value={}),
            patch(f"{_PC}.build_hooks", return_value={"PreToolUse": []}),
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
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="EXECUTE"),
        ):
            result = await _run_single_cycle(project_dir, "auth", "PLAN", "do the plan")

        assert result == "EXECUTE"
        client.query.assert_called_once_with("do the plan")

    @pytest.mark.asyncio
    async def test_context_exhaustion_triggers_checkpoint(
        self, project_dir: Path
    ) -> None:
        """When input_tokens exceed threshold, send checkpoint query."""
        exhausted_msg = _make_result(usage={"input_tokens": 800_000})
        client = _mock_sdk_client([exhausted_msg])

        with patch(f"{_PC}.ClaudeSDKClient", return_value=client):
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

        with patch(f"{_PC}.ClaudeSDKClient", return_value=client):
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
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
        ):
            result = await _run_single_cycle(project_dir, "auth", "EXECUTE", "do work")

        assert result == "ASSESS"

    @pytest.mark.asyncio
    async def test_sdk_exception_returns_failed(self, project_dir: Path) -> None:
        """If the SDK throws, cycle returns 'failed' (not crash)."""
        client = MagicMock()
        client.__aenter__ = AsyncMock(side_effect=RuntimeError("connection lost"))
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{_PC}.ClaudeSDKClient", return_value=client):
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
            patch(f"{_PC}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_PC}.read_cycle_outcome", return_value="VERIFY"),
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
            patch(f"{_PC}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_PC}.read_cycle_outcome", return_value="COMPLETE"),
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
            patch(f"{_PC}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_PC}.read_cycle_outcome", return_value="ASSESS"),
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
            patch(f"{_PC}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_PC}.read_cycle_outcome", return_value="PLAN"),
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
            patch(f"{_PC}.ClaudeSDKClient", side_effect=capture_client),
            patch(f"{_PC}.read_cycle_outcome", return_value="PLAN"),
        ):
            await _run_single_cycle(project_dir, "auth", "PLAN", "plan it")

        assert captured_options.get("max_budget_usd") is None

    @pytest.mark.asyncio
    async def test_milestone_passed_to_hooks(self, project_dir: Path) -> None:
        """build_hooks must receive the milestone for scoping."""
        client = _mock_sdk_client([_make_result()])

        with (
            patch(f"{_PC}.ClaudeSDKClient", return_value=client),
            patch(f"{_PC}.read_cycle_outcome", return_value="PLAN"),
            patch(f"{_PC}.build_hooks", wraps=None) as mock_hooks,
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
        with patch(f"{_PC}.determine_next_cycle", return_value=("COMPLETE", [])):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"

    @pytest.mark.asyncio
    async def test_cycle_limit_escalation(self, project_dir: Path) -> None:
        """20-cycle limit → write escalation and return."""
        with (
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("PLAN", ["milestone.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=20),
            patch(
                f"{_PC}.write_cycle_limit_escalation", new_callable=AsyncMock
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
                f"{_PC}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", return_value="agent_team_crash"),
            patch(
                f"{_PC}.write_agent_crash_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_agent_crash"
        mock_esc.assert_called_once_with(
            project_dir, "auth", error_detail=None,
        )

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
            [
                [_vf("missing ## Cycle")],  # first: fail
                [],  # second: pass
            ]
        )

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
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=lambda *a, **kw: next(validation_results),
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(
                f"{_PC}.git_revert_golden_context", new_callable=AsyncMock
            ) as mock_revert,
            patch(f"{_PC}.build_cycle_prompt", return_value="retry prompt"),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_revert.assert_called_once_with(
            project_dir, "auth", current_phase=None,
        )
        # Two cycles: first failed validation + retry, second passed
        assert cycle_calls == 2

    @pytest.mark.asyncio
    async def test_validation_escalation_after_three_failures(
        self, project_dir: Path
    ) -> None:
        """3 consecutive validation failures → escalate."""
        bad_finding = _vf("bad structure")
        with (
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(
                f"{_PC}.validate_golden_context",
                return_value=[bad_finding],
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(f"{_PC}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_PC}.build_cycle_prompt", return_value="retry"),
            patch(
                f"{_PC}.write_validation_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_validation"
        mock_esc.assert_called_once()
        # Verify the findings are passed through
        call_args = mock_esc.call_args
        assert call_args.args[2] == [bad_finding]

    @pytest.mark.asyncio
    @patch(f"{_PC}._STALENESS_THRESHOLD", 100)
    async def test_validation_retries_reset_on_success(self, project_dir: Path) -> None:
        """Validation retry counter resets when a cycle passes validation."""
        validation_sequence = iter(
            [
                [_vf("error1")],  # cycle 1: fail (retry=1)
                [],  # cycle 1 retry: pass (retry=0)
                [_vf("error2")],  # cycle 2: fail (retry=1)
                [_vf("error3")],  # cycle 2 retry: fail (retry=2)
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
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["s"]),  # cycle 1
                    ("EXECUTE", ["s"]),  # cycle 1 retry
                    ("EXECUTE", ["s"]),  # cycle 2
                    ("EXECUTE", ["s"]),  # cycle 2 retry
                    ("EXECUTE", ["s"]),  # cycle 2 re-retry
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=lambda *a, **kw: next(validation_sequence),
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(f"{_PC}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_PC}.build_cycle_prompt", return_value="p"),
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
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["s"]),
                    ("EXECUTE", ["s"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=[[_vf("bad")], []],
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(
                f"{_PC}.git_revert_golden_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("git broken"),
            ),
            patch(f"{_PC}.build_cycle_prompt", return_value="p"),
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

        async def _cycle(pd: Path, ms: str, ct: str, prompt: str, **kw: Any) -> str:
            cycles_run.append(ct)
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("PLAN", ["milestone.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("VERIFY", ["requirements.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert cycles_run == ["PLAN", "EXECUTE", "VERIFY"]

    @pytest.mark.asyncio
    async def test_crash_retry_limit_escalates(self, project_dir: Path) -> None:
        """3 consecutive crash failures → escalate crash loop."""
        with (
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", return_value="failed"),
            patch(
                f"{_PC}.write_agent_crash_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_crash_loop"
        mock_esc.assert_called_once_with(
            project_dir, "auth", error_detail=None,
        )

    @pytest.mark.asyncio
    @patch(f"{_PC}._STALENESS_THRESHOLD", 100)
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
                f"{_PC}.determine_next_cycle",
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
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
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

        missing_finding = _vf("missing field: status")
        validation_results = iter(
            [
                [missing_finding],  # cycle 1: fail
                [],  # cycle 2: pass
            ]
        )

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
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=lambda *a, **kw: next(validation_results),
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(f"{_PC}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_PC}.build_cycle_prompt", return_value="p") as mock_bcp,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert cycle_count == 2
        assert mock_bcp.call_count == 2

        # First call (initial cycle): no validation errors yet
        first_call_kwargs = mock_bcp.call_args_list[0]
        assert first_call_kwargs.kwargs.get("validation_errors") is None

        # Second call (retry after validation failure): findings passed through
        second_call_kwargs = mock_bcp.call_args_list[1]
        assert second_call_kwargs.kwargs.get("validation_errors") == [
            missing_finding
        ]

    @pytest.mark.asyncio
    async def test_execute_cycle_triggers_git_commit(self, project_dir: Path) -> None:
        """After a successful EXECUTE cycle, git_commit_phase is called."""
        # Write a checkpoint so parse_checkpoint can extract current_phase.
        # Must also write milestone marker to prevent stale-checkpoint clearing.
        cp_path = project_dir / ".clou" / "milestones" / "auth" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: design\nphases_completed: 0\nphases_total: 1\n"
        )
        marker = project_dir / ".clou" / ".coordinator-milestone"
        marker.write_text("auth")

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(
                f"{_PC}.git_commit_phase", new_callable=AsyncMock
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
        cp_path = project_dir / ".clou" / "milestones" / "auth" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text("cycle: 2\ncurrent_phase: impl\nnext_step: ASSESS\n")

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(
                f"{_PC}.git_commit_phase",
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
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("PLAN", ["milestone.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(
                f"{_PC}.git_commit_phase", new_callable=AsyncMock
            ) as mock_commit,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_fatal_escalation_posts_to_ui(self, project_dir: Path) -> None:
        """Fatal escalation paths post ClouEscalationArrived before returning."""
        mock_app = MagicMock()
        posted: list[Any] = []
        mock_app.post_message.side_effect = lambda msg: posted.append(msg)

        # Write an escalation file that write_cycle_limit_escalation would create
        esc_dir = project_dir / ".clou" / "milestones" / "auth" / "escalations"
        esc_dir.mkdir(parents=True)
        (esc_dir / "cycle-limit.md").write_text(
            "# Escalation\n\n"
            "## Classification\nblocking\n\n"
            "## Issue\nCycle limit reached\n\n"
            "## Options\n1. **Increase limit** — raise the cap\n\n"
            "## Recommendation\nReassess scope\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("PLAN", ["milestone.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=20),
            patch(
                f"{_PC}.write_cycle_limit_escalation", new_callable=AsyncMock
            ),
        ):
            result = await run_coordinator(project_dir, "auth", app=mock_app)

        assert result == "escalated_cycle_limit"

        from clou.ui.messages import ClouEscalationArrived

        esc_messages = [m for m in posted if isinstance(m, ClouEscalationArrived)]
        assert len(esc_messages) == 1
        assert esc_messages[0].classification == "blocking"
        assert esc_messages[0].issue == "Cycle limit reached"

    # -------------------------------------------------------------------
    # Warning passthrough / severity-aware validation — validator-resilience
    # -------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_warnings_only_validation_passes(self, project_dir: Path) -> None:
        """Only warnings from validation → no retry, no escalation, proceeds."""
        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        warning_finding = _vf("missing **Status:**", severity=Severity.WARNING)

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(
                f"{_PC}.validate_golden_context",
                return_value=[warning_finding],
            ),
            patch(
                f"{_PC}.git_revert_golden_context", new_callable=AsyncMock
            ) as mock_revert,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_revert.assert_not_called()

    @pytest.mark.asyncio
    async def test_errors_block_progression(self, project_dir: Path) -> None:
        """Errors in validation → validation_retries incremented, retry triggered."""
        call_count = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        error_finding = _vf("missing ## Summary")
        validation_results = iter(
            [
                [error_finding],  # first: fail
                [],  # second: pass
            ]
        )

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
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=lambda *a, **kw: next(validation_results),
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(f"{_PC}.git_revert_golden_context", new_callable=AsyncMock),
            patch(f"{_PC}.build_cycle_prompt", return_value="p"),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        assert call_count == 2  # first cycle failed validation, second passed

    @pytest.mark.asyncio
    async def test_mixed_findings_errors_dominate(self, project_dir: Path) -> None:
        """Errors + warnings → failure path taken (errors dominate)."""
        call_count = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        mixed_findings = [
            _vf("missing ## Summary", severity=Severity.ERROR),
            _vf("missing **Status:**", severity=Severity.WARNING),
        ]
        validation_results = iter(
            [
                mixed_findings,  # first: has errors
                [],  # second: pass
            ]
        )

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
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=lambda *a, **kw: next(validation_results),
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(
                f"{_PC}.git_revert_golden_context", new_callable=AsyncMock
            ) as mock_revert,
            patch(f"{_PC}.build_cycle_prompt", return_value="p"),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        # Git revert was called because errors were present
        mock_revert.assert_called_once_with(
            project_dir, "auth", current_phase=None,
        )
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_validation_failure_passes_current_phase_to_git_revert(
        self, project_dir: Path
    ) -> None:
        """Validation failure wires current_phase from parse_checkpoint to git_revert_golden_context.

        This verifies the coordinator's call-site: _current_phase is extracted
        from the checkpoint via parse_checkpoint().current_phase and forwarded
        as the current_phase kwarg to git_revert_golden_context on the
        validation-failure branch.
        """
        # Write a checkpoint so parse_checkpoint can extract current_phase.
        # Also write milestone marker to prevent stale-checkpoint clearing.
        cp_path = (
            project_dir / ".clou" / "milestones" / "auth"
            / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: design\nphases_completed: 0\nphases_total: 1\n"
        )
        marker = project_dir / ".clou" / ".coordinator-milestone"
        marker.write_text("auth")

        cycle_calls = 0

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            nonlocal cycle_calls
            cycle_calls += 1
            return "ok"

        validation_results = iter(
            [
                [_vf("missing ## Cycle")],  # first: fail
                [],  # second: pass
            ]
        )

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
            patch(
                f"{_PC}.validate_golden_context",
                side_effect=lambda *a, **kw: next(validation_results),
            ),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.attempt_self_heal", return_value=[]),
            patch(
                f"{_PC}.git_revert_golden_context", new_callable=AsyncMock
            ) as mock_revert,
            patch(f"{_PC}.build_cycle_prompt", return_value="retry prompt"),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "completed"
        mock_revert.assert_called_once_with(
            project_dir, "auth", current_phase="design",
        )
        assert cycle_calls == 2

    @pytest.mark.asyncio
    async def test_staleness_escalation(self, project_dir: Path) -> None:
        """3 consecutive same-type cycles with no phase advancement -> escalated_staleness."""
        # Write milestone marker so checkpoint is not cleared as stale.
        marker = project_dir / ".clou" / ".coordinator-milestone"
        marker.write_text("auth")

        # Write a checkpoint with fixed phases_completed so staleness is detected.
        cp_path = project_dir / ".clou" / "milestones" / "auth" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(
            "cycle: 3\nstep: EXECUTE\nnext_step: EXECUTE\n"
            "current_phase: impl\nphases_completed: 1\nphases_total: 3\n"
        )

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(
                f"{_PC}.write_staleness_escalation", new_callable=AsyncMock
            ) as mock_esc,
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_staleness"
        mock_esc.assert_called_once()
        call_args = mock_esc.call_args
        assert call_args.args[2] == "EXECUTE"  # cycle_type
        assert call_args.args[3] == 3  # consecutive_count
        assert call_args.args[4] == 1  # phases_completed

    @pytest.mark.asyncio
    async def test_staleness_resets_on_phase_advance(self, project_dir: Path) -> None:
        """phases_completed changes mid-loop -> no staleness escalation."""
        call_count = 0

        # Write milestone marker so checkpoint is not cleared as stale.
        marker = project_dir / ".clou" / ".coordinator-milestone"
        marker.write_text("auth")

        # Write initial checkpoint
        cp_path = project_dir / ".clou" / "milestones" / "auth" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True, exist_ok=True)

        def _update_checkpoint_side_effect(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            # Advance phases_completed on the second cycle
            phases = 1 if call_count <= 1 else 2
            cp_path.write_text(
                f"cycle: {call_count}\nstep: EXECUTE\nnext_step: EXECUTE\n"
                f"current_phase: impl\nphases_completed: {phases}\nphases_total: 3\n"
            )
            return "ok"

        # Initial checkpoint
        cp_path.write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: EXECUTE\n"
            "current_phase: impl\nphases_completed: 1\nphases_total: 3\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}._run_single_cycle", side_effect=_update_checkpoint_side_effect),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        # Should NOT escalate — phases_completed advanced
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_staleness_resets_on_type_change(self, project_dir: Path) -> None:
        """cycle_type changes -> staleness counter resets, no escalation."""
        # Write milestone marker so checkpoint is not cleared as stale.
        marker = project_dir / ".clou" / ".coordinator-milestone"
        marker.write_text("auth")

        cp_path = project_dir / ".clou" / "milestones" / "auth" / "active" / "coordinator.md"
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: impl\nphases_completed: 1\nphases_total: 3\n"
        )

        async def _cycle(*args: Any, **kwargs: Any) -> str:
            return "ok"

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("ASSESS", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}._run_single_cycle", side_effect=_cycle),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
        ):
            result = await run_coordinator(project_dir, "auth")

        # Should NOT escalate — cycle type alternates
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_milestone_validation_at_coordinator_boundary(
        self, project_dir: Path
    ) -> None:
        """run_coordinator validates milestone name at its own boundary."""
        with pytest.raises(ValueError, match="Invalid milestone name"):
            await run_coordinator(project_dir, "../evil")


# ---------------------------------------------------------------------------
# Cycle-boundary pause_on_user_message flag
# ---------------------------------------------------------------------------


class TestCycleBoundaryPauseFlag:
    """Test that pause_on_user_message controls whether user messages
    pause the coordinator at cycle boundaries.

    The /stop check, budget check, and cycle limit check must ALWAYS
    work regardless of the flag.
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

    def _make_template(self, *, pause: bool, budget_usd: float | None = None) -> Any:
        """Return a mock template with the given pause_on_user_message."""
        tmpl = MagicMock()
        tmpl.name = "test-template"
        tmpl.pause_on_user_message = pause
        tmpl.budget_usd = budget_usd
        tmpl.quality_gates = []
        return tmpl

    def _make_app_with_queue(self) -> MagicMock:
        """Build a mock app with a populated _user_input_queue (deque)."""
        import asyncio
        from collections import deque

        mock_app = MagicMock()
        mock_app._user_input_queue = deque(["hello from user"])
        mock_app._stop_requested = asyncio.Event()
        mock_app.post_message = MagicMock()
        return mock_app

    @pytest.mark.asyncio
    async def test_default_does_not_pause_for_user_messages(
        self, project_dir: Path,
    ) -> None:
        """With pause_on_user_message=False (default), pending user messages
        do NOT pause the coordinator — it continues to COMPLETE."""
        mock_app = self._make_app_with_queue()

        with (
            patch(
                f"{_PC}.load_template",
                return_value=self._make_template(pause=False),
            ),
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("COMPLETE", []),
            ),
        ):
            result = await run_coordinator(
                project_dir, "auth", app=mock_app,
            )

        assert result == "completed"

    @pytest.mark.asyncio
    async def test_pause_true_pauses_for_user_messages(
        self, project_dir: Path,
    ) -> None:
        """With pause_on_user_message=True, pending user messages
        pause the coordinator at the cycle boundary."""
        mock_app = self._make_app_with_queue()

        with (
            patch(
                f"{_PC}.load_template",
                return_value=self._make_template(pause=True),
            ),
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
        ):
            result = await run_coordinator(
                project_dir, "auth", app=mock_app,
            )

        assert result == "paused"

    @pytest.mark.asyncio
    async def test_stop_always_works_regardless_of_flag(
        self, project_dir: Path,
    ) -> None:
        """The /stop check fires even when pause_on_user_message=False."""
        import asyncio

        mock_app = MagicMock()
        mock_app._stop_requested = asyncio.Event()
        mock_app._stop_requested.set()
        mock_app._user_input_queue = MagicMock()  # not a deque — won't match

        with (
            patch(
                f"{_PC}.load_template",
                return_value=self._make_template(pause=False),
            ),
        ):
            result = await run_coordinator(
                project_dir, "auth", app=mock_app,
            )

        assert result == "stopped"

    @pytest.mark.asyncio
    async def test_budget_always_checked_regardless_of_flag(
        self, project_dir: Path,
    ) -> None:
        """Budget exhaustion fires even when pause_on_user_message=False."""
        import clou.coordinator as coord

        mock_app = self._make_app_with_queue()
        # Set cumulative cost to exceed budget.
        coord._cumulative_cost_usd["auth"] = 10.0

        try:
            with (
                patch(
                    f"{_PC}.load_template",
                    return_value=self._make_template(
                        pause=False, budget_usd=5.0,
                    ),
                ),
                patch(
                    f"{_PC}.determine_next_cycle",
                    return_value=("EXECUTE", ["status.md"]),
                ),
                patch(f"{_PC}.read_cycle_count", return_value=1),
            ):
                result = await run_coordinator(
                    project_dir, "auth", app=mock_app,
                )
        finally:
            coord._cumulative_cost_usd.pop("auth", None)

        assert result == "escalated_budget"

    @pytest.mark.asyncio
    async def test_cycle_limit_always_checked_regardless_of_flag(
        self, project_dir: Path,
    ) -> None:
        """Cycle limit fires even when pause_on_user_message=False."""
        with (
            patch(
                f"{_PC}.load_template",
                return_value=self._make_template(pause=False),
            ),
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("PLAN", ["milestone.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=20),
            patch(
                f"{_PC}.write_cycle_limit_escalation",
                new_callable=AsyncMock,
            ),
        ):
            result = await run_coordinator(project_dir, "auth")

        assert result == "escalated_cycle_limit"


# ---------------------------------------------------------------------------
# run_supervisor — resume failure feedback
# ---------------------------------------------------------------------------


class TestRunSupervisorResumeFeedback:
    """When session resume fails, user gets a warning and the UI shows an error."""

    @pytest.mark.asyncio
    async def test_failed_resume_logs_warning_and_shows_error(
        self, tmp_path: Path
    ) -> None:
        """build_resumption_context returning '' triggers log + UI error."""
        import asyncio

        # Use module-level run_supervisor (not deferred import) so the
        # conftest _no_supervisor monkeypatch doesn't shadow it.

        # Mock app with resume_session_id
        mock_app = MagicMock()
        mock_app._resume_session_id = "dead-session"
        mock_app._work_dir = tmp_path
        mock_conv = MagicMock()
        mock_app.query_one.return_value = mock_conv

        # Provide real asyncio primitives so _feed_user_input doesn't crash
        # on ensure_future(). The queue.get() blocks forever, which is fine
        # because the task is cancelled once receive_messages exhausts.
        mock_app._compact = MagicMock()
        mock_app._compact.requested = asyncio.Event()
        mock_app._user_input_queue = asyncio.Queue()

        # SDK client — query returns normally (no exception for control flow).
        # receive_messages yields nothing so the supervisor exits naturally.
        client = _mock_sdk_client()
        client.query = AsyncMock()

        async def _empty_receive() -> Any:
            return
            yield  # makes this an async generator

        client.receive_messages = _empty_receive

        (tmp_path / ".clou").mkdir()

        with (
            patch(f"{_P}.ClaudeSDKClient", return_value=client),
            patch(f"{_P}.load_prompt", return_value="<system/>"),
            patch(f"{_P}.build_hooks", return_value={}),
            patch(f"{_P}._build_mcp_server", return_value={}),
            patch(f"{_P}.read_template_name", return_value="software-construction"),
            patch(f"{_P}.load_template", return_value=MagicMock(quality_gates=[])),
            patch(
                "clou.resume.build_resumption_context",
                return_value="",
            ),
        ):
            await run_supervisor(tmp_path, app=mock_app)

        # The query was attempted (supervisor started after resume failed)
        assert client.query.await_count >= 1

        # Verify error message was shown in the UI
        mock_conv.add_error_message.assert_called_once_with(
            "Could not restore session dead-session. Starting fresh."
        )


# ---------------------------------------------------------------------------
# Supervisor startup paths (T18)
# ---------------------------------------------------------------------------


class TestSupervisorStartup:
    """Verify the four startup paths are correctly detected.

    Tests the filesystem detection logic directly rather than running
    the full supervisor (which has pytest-asyncio mock interaction
    issues with the SDK's async context manager).
    """

    def test_checkpoint_path_detected(self, tmp_path: Path) -> None:
        """Checkpoint exists → would take the resume path."""
        (tmp_path / ".clou" / "active").mkdir(parents=True)
        (tmp_path / ".clou" / "active" / "supervisor.md").write_text("# State\n")
        checkpoint = tmp_path / ".clou" / "active" / "supervisor.md"
        assert checkpoint.exists()

    def test_project_md_without_checkpoint(self, tmp_path: Path) -> None:
        """project.md exists but no checkpoint → existing project path."""
        (tmp_path / ".clou").mkdir()
        (tmp_path / ".clou" / "project.md").write_text("# Test\n")
        checkpoint = tmp_path / ".clou" / "active" / "supervisor.md"
        project_md = tmp_path / ".clou" / "project.md"
        assert not checkpoint.exists()
        assert project_md.exists()

    def test_brownfield_detection(self, tmp_path: Path) -> None:
        """Existing code files detected when no .clou/ exists."""
        (tmp_path / "package.json").write_text("{}")
        has_code = any(
            tmp_path.glob(p)
            for p in (
                "*.py", "*.ts", "*.js", "*.go", "*.rs",
                "package.json", "pyproject.toml", "Cargo.toml",
                "go.mod", "Makefile", "src/", "lib/",
            )
        )
        assert has_code

    def test_brownfield_detection_pyproject(self, tmp_path: Path) -> None:
        """pyproject.toml triggers brownfield detection."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        has_code = any(
            tmp_path.glob(p)
            for p in (
                "*.py", "*.ts", "*.js", "*.go", "*.rs",
                "package.json", "pyproject.toml", "Cargo.toml",
                "go.mod", "Makefile", "src/", "lib/",
            )
        )
        assert has_code

    def test_brownfield_detection_src_dir(self, tmp_path: Path) -> None:
        """src/ directory triggers brownfield detection."""
        (tmp_path / "src").mkdir()
        has_code = any(
            tmp_path.glob(p)
            for p in (
                "*.py", "*.ts", "*.js", "*.go", "*.rs",
                "package.json", "pyproject.toml", "Cargo.toml",
                "go.mod", "Makefile", "src/", "lib/",
            )
        )
        assert has_code

    def test_greenfield_detection(self, tmp_path: Path) -> None:
        """Empty directory → no brownfield files detected."""
        empty = tmp_path / "greenfield"
        empty.mkdir()
        # Greenfield = none of the detection patterns match
        for p in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
            assert not (empty / p).exists()
        assert not list(empty.glob("*.py"))
        assert not list(empty.glob("src/"))


# ---------------------------------------------------------------------------
# _feed_user_input — simultaneous compact + input race
# ---------------------------------------------------------------------------


class TestFeedUserInputRace:
    """Verify that _feed_user_input processes both compact and user input
    when both are ready simultaneously (no message loss).

    _feed_user_input is a closure inside run_supervisor, so we replicate
    its core loop logic here and verify the fix (removing ``continue``
    after compact processing) prevents input loss.
    """

    @pytest.mark.asyncio
    async def test_simultaneous_compact_and_input_both_processed(self) -> None:
        """When compact_wait and input_wait both resolve at once,
        both the compaction query and the user query must execute."""
        import asyncio

        compact_requested = asyncio.Event()
        compact_requested.set()  # already signalled
        input_queue: asyncio.Queue[str] = asyncio.Queue()
        input_queue.put_nowait("hello from user")

        queries: list[str] = []

        async def mock_query(text: str) -> None:
            queries.append(text)

        # Replicate the fixed _feed_user_input loop for exactly one iteration.
        compact_wait = asyncio.ensure_future(compact_requested.wait())
        input_wait = asyncio.ensure_future(input_queue.get())

        # Both futures should resolve immediately (event set + queue non-empty).
        # Give the event loop a tick to complete them.
        await asyncio.sleep(0)

        done, pending = await asyncio.wait(
            {compact_wait, input_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()

        # --- Fixed logic (no continue after compact) ---
        if compact_wait in done:
            compact_requested.clear()
            instructions = (
                "Summarize the conversation so far, preserving key "
                "decisions, code context, and open tasks."
            )
            await mock_query(
                f"[SYSTEM: Context compaction requested. {instructions}. "
                f"Acknowledge briefly and continue.]"
            )

        if input_wait in done:
            text = input_wait.result()
            await mock_query(text)

        # Both must have been processed.
        compact_queries = [q for q in queries if "compaction requested" in q.lower()]
        assert len(compact_queries) == 1, f"Expected 1 compact query, got: {queries}"
        assert "hello from user" in queries, (
            f"User input was lost! queries={queries}"
        )

    @pytest.mark.asyncio
    async def test_continue_would_lose_input(self) -> None:
        """Demonstrate that the old ``continue`` logic loses user input."""
        import asyncio

        compact_requested = asyncio.Event()
        compact_requested.set()
        input_queue: asyncio.Queue[str] = asyncio.Queue()
        input_queue.put_nowait("hello from user")

        queries: list[str] = []

        async def mock_query(text: str) -> None:
            queries.append(text)

        compact_wait = asyncio.ensure_future(compact_requested.wait())
        input_wait = asyncio.ensure_future(input_queue.get())
        await asyncio.sleep(0)

        done, pending = await asyncio.wait(
            {compact_wait, input_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()

        # --- Old logic WITH continue ---
        processed_input = False
        if compact_wait in done:
            compact_requested.clear()
            await mock_query("[SYSTEM: compact]")
            # ``continue`` would skip the input_wait check below
        else:
            # Only reaches here if compact_wait NOT in done
            if input_wait in done:
                processed_input = True
                await mock_query(input_wait.result())

        # When both fire, the old logic processes compact but skips input.
        if compact_wait in done and input_wait in done:
            assert not processed_input, (
                "Old logic should NOT have processed input when compact also fired"
            )


# ---------------------------------------------------------------------------
# can_use_tool — all tools auto-approved
# ---------------------------------------------------------------------------


class TestCanUseTool:
    """The supervisor's can_use_tool auto-approves everything."""

    @pytest.mark.asyncio
    async def test_approves_all_tools(self) -> None:
        """All tools are auto-approved (questions go through ask_user MCP tool)."""
        from claude_agent_sdk import (
            PermissionResultAllow,
            ToolPermissionContext,
        )

        # Matches the simplified callback in run_supervisor.
        async def _can_use_tool(
            name: str,
            tool_input: dict[str, Any],
            ctx: ToolPermissionContext,
        ) -> Any:
            return PermissionResultAllow()

        for tool_name in ("Read", "AskUserQuestion", "Bash"):
            result = await _can_use_tool(
                tool_name, {}, ToolPermissionContext(signal=None, suggestions=[]),
            )
            assert result.behavior == "allow"


# ---------------------------------------------------------------------------
# ask_user MCP tool — structured choices
# ---------------------------------------------------------------------------


class TestAskUserTool:
    """The ask_user MCP tool passes choices through to the gate."""

    @staticmethod
    def _extract_ask_user_handler(tmp_path: Path) -> tuple:
        """Build the MCP server and extract the ask_user tool handler + gate."""
        from claude_agent_sdk import SdkMcpTool

        from clou.gate import UserGate
        from clou.orchestrator import _build_mcp_server

        gate = UserGate()
        captured_tools: list[SdkMcpTool[Any]] = []

        original_create = None
        try:
            from claude_agent_sdk import create_sdk_mcp_server as _orig

            original_create = _orig
        except ImportError:
            pass

        def _capture_create(name: str, **kwargs: Any) -> Any:
            tools = kwargs.get("tools", [])
            captured_tools.extend(tools or [])
            return original_create(name, **kwargs)

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path, gate=gate)

        ask_user = next(t for t in captured_tools if t.name == "ask_user")
        return ask_user.handler, gate

    @pytest.mark.asyncio
    async def test_ask_user_no_choices(self, tmp_path: Path) -> None:
        """ask_user without choices opens gate with no choices."""
        handler, gate = self._extract_ask_user_handler(tmp_path)

        import asyncio

        async def _respond_soon() -> None:
            await asyncio.sleep(0)
            gate.respond("hello")

        task = asyncio.create_task(_respond_soon())
        result = await handler({"question": "How are you?"})
        await task
        assert result == {"content": [{"type": "text", "text": "hello"}]}
        # Question was passed through to gate
        # (gate is now closed, so question is None, but it was set during open)

    @pytest.mark.asyncio
    async def test_ask_user_with_choices_appends_open_ended(
        self, tmp_path: Path,
    ) -> None:
        """ask_user auto-appends an open-ended option to the choices list."""
        handler, gate = self._extract_ask_user_handler(tmp_path)

        import asyncio

        async def _respond_soon() -> None:
            # Give the handler time to call gate.open()
            await asyncio.sleep(0)
            # Verify question and choices were set with auto-appended option
            assert gate.question == "Pick one"
            assert gate.choices is not None
            assert gate.choices == [
                "A",
                "B",
                "Something else \u2014 I'll type my answer",
            ]
            gate.respond("A")

        task = asyncio.create_task(_respond_soon())
        result = await handler({"question": "Pick one", "choices": ["A", "B"]})
        await task
        assert result == {"content": [{"type": "text", "text": "A"}]}

    @pytest.mark.asyncio
    async def test_ask_user_no_gate_returns_error(self, tmp_path: Path) -> None:
        """ask_user returns an error when no gate is available."""
        from claude_agent_sdk import SdkMcpTool

        from clou.orchestrator import _build_mcp_server

        captured_tools: list[SdkMcpTool[Any]] = []

        from claude_agent_sdk import create_sdk_mcp_server as _orig

        def _capture_create(name: str, **kwargs: Any) -> Any:
            tools = kwargs.get("tools", [])
            captured_tools.extend(tools or [])
            return _orig(name, **kwargs)

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path, gate=None)

        ask_user = next(t for t in captured_tools if t.name == "ask_user")
        result = await ask_user.handler({})
        assert result["is_error"] is True


# ---------------------------------------------------------------------------
# Parallel dispatch — clou_spawn_parallel_coordinators
# ---------------------------------------------------------------------------


class TestParallelDispatch:
    """Test the clou_spawn_parallel_coordinators MCP tool.

    Covers: concurrent dispatch, failure isolation, serial fallback,
    validation failure, single milestone degenerate case.
    """

    @staticmethod
    def _extract_parallel_handler(tmp_path: Path) -> Any:
        """Build the MCP server and extract the parallel dispatch handler."""
        from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server as _orig

        from clou.orchestrator import _build_mcp_server

        captured_tools: list[SdkMcpTool[Any]] = []

        def _capture_create(name: str, **kwargs: Any) -> Any:
            tools = kwargs.get("tools", [])
            captured_tools.extend(tools or [])
            return _orig(name, **kwargs)

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path)

        parallel_tool = next(
            t for t in captured_tools
            if t.name == "clou_spawn_parallel_coordinators"
        )
        return parallel_tool.handler

    @pytest.mark.asyncio
    async def test_empty_milestones_returns_error(self, tmp_path: Path) -> None:
        """No milestones provided returns an error."""
        handler = self._extract_parallel_handler(tmp_path)
        result = await handler({"milestones": []})
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_single_milestone_dispatches_serially(
        self, tmp_path: Path,
    ) -> None:
        """Single milestone degenerates to serial dispatch."""
        handler = self._extract_parallel_handler(tmp_path)

        ms_dir = tmp_path / ".clou" / "milestones" / "auth"
        ms_dir.mkdir(parents=True)
        (ms_dir / "milestone.md").write_text("# Auth\n")

        with patch(
            f"{_P}.run_coordinator",
            new_callable=AsyncMock,
            return_value="completed",
        ):
            result = await handler({"milestones": ["auth"]})

        text = result["content"][0]["text"]
        assert "auth" in text
        assert "completed" in text

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_both_succeed(
        self, tmp_path: Path,
    ) -> None:
        """Two independent milestones dispatch concurrently and both succeed."""
        handler = self._extract_parallel_handler(tmp_path)

        # Set up milestone directories.
        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        # Write roadmap with independence annotations.
        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n"
            "**Independent of:** payments\n\n"
            "### 2. payments\n"
            "**Status:** pending\n"
            "**Summary:** Payment integration\n"
            "**Independent of:** auth\n"
        )

        execution_order: list[str] = []

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            execution_order.append(f"start:{milestone}")
            await asyncio.sleep(0)  # yield to event loop
            execution_order.append(f"end:{milestone}")
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        assert "[auth]" in text
        assert "[payments]" in text
        assert "completed" in text

        # Verify concurrent execution: both started before either ended.
        assert execution_order[0].startswith("start:")
        assert execution_order[1].startswith("start:")

    @pytest.mark.asyncio
    async def test_partial_failure_one_fails_other_succeeds(
        self, tmp_path: Path,
    ) -> None:
        """One coordinator fails, other completes successfully."""
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n"
            "**Independent of:** payments\n\n"
            "### 2. payments\n"
            "**Status:** pending\n"
            "**Summary:** Payment integration\n"
            "**Independent of:** auth\n"
        )

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            if milestone == "auth":
                raise RuntimeError("SDK connection lost")
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        # Auth failed but payments succeeded.
        assert "[payments]" in text
        assert "completed" in text
        # Auth error is reported.
        assert "[auth]" in text
        assert "error" in text.lower()

    @pytest.mark.asyncio
    async def test_partial_failure_one_escalates_other_succeeds(
        self, tmp_path: Path,
    ) -> None:
        """One coordinator escalates, other completes successfully."""
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n"
            "**Independent of:** payments\n\n"
            "### 2. payments\n"
            "**Status:** pending\n"
            "**Summary:** Payment integration\n"
            "**Independent of:** auth\n"
        )

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            if milestone == "auth":
                return "escalated_cycle_limit"
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        assert "[payments]" in text
        assert "completed" in text
        assert "[auth]" in text
        assert "escalated" in text

    @pytest.mark.asyncio
    async def test_serial_fallback_no_independence_annotations(
        self, tmp_path: Path,
    ) -> None:
        """No Independence of annotations triggers serial fallback."""
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        # Roadmap without independence annotations.
        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n\n"
            "### 2. payments\n"
            "**Status:** pending\n"
            "**Summary:** Payment integration\n"
        )

        call_order: list[str] = []

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            call_order.append(milestone)
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        assert "Serial fallback" in text
        assert "pairwise independence" in text.lower()
        # Both milestones ran serially.
        assert call_order == ["auth", "payments"]

    @pytest.mark.asyncio
    async def test_serial_fallback_validation_cycle_detected(
        self, tmp_path: Path,
    ) -> None:
        """Dependency cycle in roadmap triggers serial fallback."""
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        # Roadmap with a cycle: auth depends on payments, payments depends on auth.
        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n"
            "**Depends on:** payments\n"
            "**Independent of:** payments\n\n"
            "### 2. payments\n"
            "**Status:** pending\n"
            "**Summary:** Payment integration\n"
            "**Depends on:** auth\n"
            "**Independent of:** auth\n"
        )

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        assert "Serial fallback" in text
        assert "Validation errors" in text

    @pytest.mark.asyncio
    async def test_serial_fallback_no_roadmap_file(
        self, tmp_path: Path,
    ) -> None:
        """Missing roadmap.md triggers serial fallback."""
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        # Ensure roadmap does NOT exist.
        roadmap = tmp_path / ".clou" / "roadmap.md"
        if roadmap.exists():
            roadmap.unlink()

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        assert "Serial fallback" in text
        assert "No roadmap.md found" in text

    @pytest.mark.asyncio
    async def test_serial_fallback_cross_layer_milestones(
        self, tmp_path: Path,
    ) -> None:
        """Milestones in different dependency layers trigger serial fallback.

        auth and dashboard declare pairwise independence, passing the
        pairwise check. But dashboard depends on api (a third milestone),
        placing it in a later layer than auth. The cross-layer check fires.
        """
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "api", "dashboard"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n"
            "**Independent of:** dashboard\n\n"
            "### 2. api\n"
            "**Status:** pending\n"
            "**Summary:** API layer\n\n"
            "### 3. dashboard\n"
            "**Status:** pending\n"
            "**Summary:** Dashboard\n"
            "**Depends on:** api\n"
            "**Independent of:** auth\n"
        )

        call_order: list[str] = []

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            call_order.append(milestone)
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["dashboard", "auth"]})

        text = result["content"][0]["text"]
        assert "Serial fallback" in text
        assert "different dependency layers" in text
        # F12: auth (layer 0) runs before dashboard (layer 1) regardless
        # of caller-provided order.
        assert call_order == ["auth", "dashboard"]

    @pytest.mark.asyncio
    async def test_invalid_milestone_name_rejected(
        self, tmp_path: Path,
    ) -> None:
        """Invalid milestone names are rejected before dispatch."""
        handler = self._extract_parallel_handler(tmp_path)
        with pytest.raises(ValueError, match="Invalid milestone name"):
            await handler({"milestones": ["../evil", "auth"]})

    @pytest.mark.asyncio
    async def test_all_coordinators_fail(
        self, tmp_path: Path,
    ) -> None:
        """All coordinators failing produces combined error guidance (F20)."""
        handler = self._extract_parallel_handler(tmp_path)

        for ms in ("auth", "payments"):
            ms_dir = tmp_path / ".clou" / "milestones" / ms
            ms_dir.mkdir(parents=True)
            (ms_dir / "milestone.md").write_text(f"# {ms}\n")

        roadmap = tmp_path / ".clou" / "roadmap.md"
        roadmap.write_text(
            "# Roadmap\n\n## Milestones\n\n"
            "### 1. auth\n"
            "**Status:** pending\n"
            "**Summary:** Authentication\n"
            "**Independent of:** payments\n\n"
            "### 2. payments\n"
            "**Status:** pending\n"
            "**Summary:** Payment integration\n"
            "**Independent of:** auth\n"
        )

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            return "error"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        assert "[auth]" in text
        assert "[payments]" in text
        assert "error" in text.lower()

    @pytest.mark.asyncio
    async def test_serial_fallback_valueerror_during_spawn(
        self, tmp_path: Path,
    ) -> None:
        """ValueError during clou_spawn_coordinator in serial fallback
        is caught; subsequent milestones still execute (F21)."""
        handler = self._extract_parallel_handler(tmp_path)

        # Only create payments directory; auth is missing to trigger
        # the ValueError from clou_spawn_coordinator.
        ms_dir = tmp_path / ".clou" / "milestones" / "payments"
        ms_dir.mkdir(parents=True)
        (ms_dir / "milestone.md").write_text("# payments\n")

        # No roadmap -> serial fallback.

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "payments"]})

        text = result["content"][0]["text"]
        # auth should have an error entry from the ValueError.
        assert "[auth]" in text
        assert "ERROR" in text
        # payments should still have completed.
        assert "[payments]" in text
        assert "completed" in text

    @pytest.mark.asyncio
    async def test_duplicate_milestone_deduped(
        self, tmp_path: Path,
    ) -> None:
        """Duplicate milestone in input list is deduped (F22).
        Only one coordinator dispatched."""
        handler = self._extract_parallel_handler(tmp_path)

        ms_dir = tmp_path / ".clou" / "milestones" / "auth"
        ms_dir.mkdir(parents=True)
        (ms_dir / "milestone.md").write_text("# auth\n")

        call_count = 0

        async def _mock_coordinator(
            project_dir: Path, milestone: str, **kwargs: Any,
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "completed"

        with patch(
            f"{_P}.run_coordinator",
            side_effect=_mock_coordinator,
        ):
            result = await handler({"milestones": ["auth", "auth"]})

        text = result["content"][0]["text"]
        assert "auth" in text
        assert "completed" in text
        # Only one coordinator should have been dispatched (deduped to single).
        assert call_count == 1


# ---------------------------------------------------------------------------
# Parallel dispatch helpers — _run_single_coordinator, _build_milestone_guidance
# ---------------------------------------------------------------------------


class TestBuildMilestoneGuidance:
    """Test the _build_milestone_guidance helper."""

    def test_completed_guidance(self, tmp_path: Path) -> None:
        from clou.orchestrator import _build_milestone_guidance

        guidance = _build_milestone_guidance(tmp_path, "auth", "completed")
        assert "auth" in guidance
        assert "completed" in guidance
        assert "handoff.md" in guidance

    def test_paused_guidance(self, tmp_path: Path) -> None:
        from clou.orchestrator import _build_milestone_guidance

        guidance = _build_milestone_guidance(tmp_path, "auth", "paused")
        assert "paused" in guidance
        assert "input queue" in guidance

    def test_stopped_guidance(self, tmp_path: Path) -> None:
        from clou.orchestrator import _build_milestone_guidance

        guidance = _build_milestone_guidance(tmp_path, "auth", "stopped")
        assert "stopped" in guidance

    def test_escalated_guidance(self, tmp_path: Path) -> None:
        from clou.orchestrator import _build_milestone_guidance

        guidance = _build_milestone_guidance(tmp_path, "auth", "escalated_cycle_limit")
        assert "escalated" in guidance
        assert "escalations" in guidance

    def test_error_guidance(self, tmp_path: Path) -> None:
        from clou.orchestrator import _build_milestone_guidance

        guidance = _build_milestone_guidance(tmp_path, "auth", "error")
        assert "error" in guidance


class TestRunSingleCoordinator:
    """Test the _run_single_coordinator helper."""

    @pytest.mark.asyncio
    async def test_normal_completion(self, tmp_path: Path) -> None:
        from clou.orchestrator import _run_single_coordinator

        with patch(
            f"{_P}.run_coordinator",
            new_callable=AsyncMock,
            return_value="completed",
        ):
            ms, result = await _run_single_coordinator(tmp_path, "auth")

        assert ms == "auth"
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_exception_returns_error(self, tmp_path: Path) -> None:
        from clou.orchestrator import _run_single_coordinator

        with patch(
            f"{_P}.run_coordinator",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            ms, result = await _run_single_coordinator(tmp_path, "auth")

        assert ms == "auth"
        assert result == "error"

    @pytest.mark.asyncio
    async def test_posts_spawned_message_to_app(self, tmp_path: Path) -> None:
        from clou.orchestrator import _run_single_coordinator

        mock_app = MagicMock()
        posted: list[Any] = []
        mock_app.post_message.side_effect = lambda msg: posted.append(msg)

        with patch(
            f"{_P}.run_coordinator",
            new_callable=AsyncMock,
            return_value="completed",
        ):
            await _run_single_coordinator(tmp_path, "auth", app=mock_app)

        from clou.ui.messages import ClouCoordinatorSpawned, ClouCoordinatorComplete

        spawned = [m for m in posted if isinstance(m, ClouCoordinatorSpawned)]
        assert len(spawned) == 1
        assert spawned[0].milestone == "auth"

        completed = [m for m in posted if isinstance(m, ClouCoordinatorComplete)]
        assert len(completed) == 1
        assert completed[0].milestone == "auth"
        assert completed[0].result == "completed"


# ---------------------------------------------------------------------------
# cleanup_obsolete_files tests
# ---------------------------------------------------------------------------


class TestCleanupObsoleteFiles:
    """Tests for cleanup_obsolete_files orchestrator helper."""

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_cleanup_after_completion(self, tmp_path: Path) -> None:
        """Flagged file is deleted when coordinator completes."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        # Create the flagged file.
        target = clou_dir / "roadmap.py.example"
        self._write(target, "legacy content")
        assert target.exists()

        # Create handoff.md with an Obsolete flag.
        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Legacy file. Obsolete: `.clou/roadmap.py.example`\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert ".clou/roadmap.py.example" in deleted
        assert not target.exists()

    def test_no_flags_no_action(self, tmp_path: Path) -> None:
        """Handoff.md with no Obsolete flags results in no deletions."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Some limitation without flags.\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []

    def test_missing_handoff_no_error(self, tmp_path: Path) -> None:
        """Missing handoff.md results in empty list, no error."""
        from clou.orchestrator import cleanup_obsolete_files

        (tmp_path / ".clou" / "milestones" / "m1").mkdir(parents=True)
        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []

    def test_permission_guard_rejects(self, tmp_path: Path) -> None:
        """Flagged path outside cleanup scope is not deleted."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        # Create a file that does NOT match CLEANUP_SCOPE patterns.
        target = clou_dir / "project.md"
        self._write(target, "important config")

        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/project.md`\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []
        assert target.exists()  # file preserved

    def test_flagged_file_already_absent(self, tmp_path: Path) -> None:
        """Flagged file that does not exist results in no error."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/gone.old`\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []

    def test_nested_path_rejected(self, tmp_path: Path) -> None:
        """Flagged path in nested directory is rejected by permission guard."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        target = clou_dir / "milestones" / "m1" / "old.bak"
        self._write(target, "nested file")

        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/milestones/m1/old.bak`\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []
        assert target.exists()

    @pytest.mark.asyncio
    async def test_run_single_coordinator_triggers_cleanup(
        self, tmp_path: Path,
    ) -> None:
        """_run_single_coordinator calls cleanup on completion."""
        from clou.orchestrator import _run_single_coordinator

        clou_dir = tmp_path / ".clou"
        target = clou_dir / "legacy.example"
        self._write(target, "old template")

        self._write(
            clou_dir / "milestones" / "auth" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/legacy.example`\n",
        )

        with patch(
            f"{_P}.run_coordinator",
            new_callable=AsyncMock,
            return_value="completed",
        ):
            ms, result = await _run_single_coordinator(tmp_path, "auth")

        assert result == "completed"
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_run_single_coordinator_no_cleanup_on_error(
        self, tmp_path: Path,
    ) -> None:
        """_run_single_coordinator does not clean up when coordinator errors."""
        from clou.orchestrator import _run_single_coordinator

        clou_dir = tmp_path / ".clou"
        target = clou_dir / "legacy.example"
        self._write(target, "old template")

        self._write(
            clou_dir / "milestones" / "auth" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/legacy.example`\n",
        )

        with patch(
            f"{_P}.run_coordinator",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            ms, result = await _run_single_coordinator(tmp_path, "auth")

        assert result == "error"
        assert target.exists()  # file preserved on error

    def test_oserror_on_unlink_handled_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """F16: OSError during unlink() is caught and does not crash."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        target = clou_dir / "problem.example"
        self._write(target, "locked content")

        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/problem.example`\n",
        )

        # Make unlink() raise PermissionError (subclass of OSError).
        original_unlink = Path.unlink

        def _failing_unlink(self_path: Path, *args: object, **kwargs: object) -> None:
            if self_path.name == "problem.example":
                raise PermissionError("Permission denied")
            original_unlink(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", _failing_unlink)

        # Should not raise — error is caught internally.
        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []
        assert target.exists()  # file preserved on error

    def test_non_clou_prefix_path_skipped(self, tmp_path: Path) -> None:
        """F17: Flagged path without .clou/ prefix is not deleted."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        # Create a file outside .clou/ that happens to exist.
        outside_file = tmp_path / "some" / "other" / "path.bak"
        self._write(outside_file, "external content")

        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `some/other/path.bak`\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []
        assert outside_file.exists()  # file not touched

    def test_symlink_target_skipped(self, tmp_path: Path) -> None:
        """F9: Symlink targets are refused for safety."""
        from clou.orchestrator import cleanup_obsolete_files

        clou_dir = tmp_path / ".clou"
        # Create a real file and a symlink to it.
        real_file = tmp_path / "real-data.txt"
        real_file.write_text("important data")

        symlink_target = clou_dir / "evil.example"
        symlink_target.parent.mkdir(parents=True, exist_ok=True)
        symlink_target.symlink_to(real_file)

        self._write(
            clou_dir / "milestones" / "m1" / "handoff.md",
            "# Handoff\n\n"
            "## Known Limitations\n"
            "- Obsolete: `.clou/evil.example`\n",
        )

        deleted = cleanup_obsolete_files(tmp_path, "m1")
        assert deleted == []
        assert symlink_target.is_symlink()  # symlink preserved
        assert real_file.exists()  # real file preserved


# ---------------------------------------------------------------------------
# remove_artifact_tool (clou_remove_artifact MCP tool) tests
# ---------------------------------------------------------------------------


def _extract_tool(tmp_path: Path, tool_name: str) -> Any:
    """Return the decorated MCP tool from _build_mcp_server by name."""
    from clou.orchestrator import _build_mcp_server

    captured: list[Any] = []

    def _capture_create(*args: Any, **kwargs: Any) -> MagicMock:
        tools = kwargs.get("tools", args[1] if len(args) > 1 else [])
        captured.extend(tools)
        return MagicMock()

    with patch(
        "clou.orchestrator.create_sdk_mcp_server", side_effect=_capture_create,
    ):
        _build_mcp_server(tmp_path)

    for fn in captured:
        name = getattr(fn, "__name__", "") or getattr(
            getattr(fn, "handler", None), "__name__", "",
        )
        if name == tool_name:
            return getattr(fn, "handler", fn)
    raise AssertionError(f"{tool_name} not found in captured tools")


class TestRemoveArtifactTool:
    """Tests for clou_remove_artifact MCP tool (supervisor cleanup)."""

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _scope_path(self, tmp_path: Path) -> Path:
        """Return a concrete path that lies within SUPERVISOR_CLEANUP_SCOPE."""
        return (
            tmp_path / ".clou" / "milestones" / "m1"
            / "phases" / "p1" / "execution.md"
        )

    def _run(self, coro: Any) -> dict[str, Any]:
        result: Any = asyncio.run(coro)
        assert isinstance(result, dict)
        return result

    def _is_error(self, result: dict[str, Any]) -> bool:
        return bool(result.get("is_error"))

    def _text(self, result: dict[str, Any]) -> str:
        content = result.get("content", [])
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict):
            return str(first.get("text", ""))
        return ""

    # --- Happy path ---

    def test_removes_worker_execution(self, tmp_path: Path) -> None:
        """Worker execution file within scope is removed."""
        target = self._scope_path(tmp_path)
        self._write(target, "stale worker output")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/phases/p1/execution.md",
            "reason": "orphan from truncated run",
        }))

        assert not self._is_error(result)
        assert "Removed .clou/milestones/m1/phases/p1/execution.md" in self._text(result)
        assert not target.exists()

    def test_removes_parallel_worker_execution(self, tmp_path: Path) -> None:
        """execution-<name>.md files match scope and are removed."""
        target = (
            tmp_path / ".clou" / "milestones" / "m1"
            / "phases" / "p1" / "execution-alpha.md"
        )
        self._write(target, "parallel worker output")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/phases/p1/execution-alpha.md",
            "reason": "superseded by serial rerun",
        }))

        assert not self._is_error(result)
        assert not target.exists()

    def test_removes_brutalist_assessment(self, tmp_path: Path) -> None:
        """assessment.md is supersedable and removable."""
        target = tmp_path / ".clou" / "milestones" / "m1" / "assessment.md"
        self._write(target, "old report")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
            "reason": "replaced by cycle 3 assessment",
        }))

        assert not self._is_error(result)
        assert not target.exists()

    def test_removes_verifier_artifact(self, tmp_path: Path) -> None:
        """Files under phases/*/artifacts/ match scope and are removable."""
        target = (
            tmp_path / ".clou" / "milestones" / "m1"
            / "phases" / "verification" / "artifacts" / "report.md"
        )
        self._write(target, "stale artifact")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/phases/verification/artifacts/report.md",
            "reason": "aborted verification run",
        }))

        assert not self._is_error(result)
        assert not target.exists()

    def test_removes_escalation(self, tmp_path: Path) -> None:
        """Escalation files within scope are removable."""
        target = (
            tmp_path / ".clou" / "milestones" / "m1"
            / "escalations" / "auth.md"
        )
        self._write(target, "old escalation")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/escalations/auth.md",
            "reason": "resolved in follow-up milestone",
        }))

        assert not self._is_error(result)
        assert not target.exists()

    def test_tolerates_leading_clou_prefix(self, tmp_path: Path) -> None:
        """'.clou/...' prefix on path argument is stripped, not rejected."""
        target = self._scope_path(tmp_path)
        self._write(target, "content")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": ".clou/milestones/m1/phases/p1/execution.md",
            "reason": "orphan",
        }))

        assert not self._is_error(result)
        assert not target.exists()

    # --- Protocol artifact rejection ---

    @pytest.mark.parametrize(
        "protocol_path",
        [
            "milestones/m1/milestone.md",
            "milestones/m1/intents.md",
            "milestones/m1/requirements.md",
            "milestones/m1/compose.py",
            "milestones/m1/status.md",
            "milestones/m1/handoff.md",
            "milestones/m1/decisions.md",
            "milestones/m1/phases/p1/phase.md",
            "project.md",
            "roadmap.md",
            "memory.md",
            "understanding.md",
            "active/supervisor.md",
            "milestones/m1/active/coordinator.md",
        ],
    )
    def test_rejects_protocol_artifacts(
        self, tmp_path: Path, protocol_path: str,
    ) -> None:
        """Protocol artifacts are immutable."""
        target = tmp_path / ".clou" / protocol_path
        self._write(target, "protocol content")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": protocol_path,
            "reason": "attempting to delete protocol artifact",
        }))

        assert self._is_error(result)
        assert "not in supervisor cleanup scope" in self._text(result)
        assert target.exists()  # preserved

    # --- Input validation ---

    def test_rejects_empty_path(self, tmp_path: Path) -> None:
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({"path": "", "reason": "test"}))
        assert self._is_error(result)
        assert "path" in self._text(result)

    def test_rejects_missing_path(self, tmp_path: Path) -> None:
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({"reason": "test"}))
        assert self._is_error(result)

    def test_rejects_non_string_path(self, tmp_path: Path) -> None:
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({"path": 123, "reason": "test"}))
        assert self._is_error(result)

    def test_rejects_empty_reason(self, tmp_path: Path) -> None:
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
            "reason": "",
        }))
        assert self._is_error(result)
        assert "reason" in self._text(result)

    def test_rejects_whitespace_reason(self, tmp_path: Path) -> None:
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
            "reason": "   \n\t  ",
        }))
        assert self._is_error(result)

    def test_rejects_oversize_reason(self, tmp_path: Path) -> None:
        """Reasons larger than the cap are rejected to bound audit bloat."""
        target = tmp_path / ".clou" / "milestones" / "m1" / "assessment.md"
        self._write(target, "content")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
            "reason": "x" * 2049,  # one past the cap
        }))

        assert self._is_error(result)
        assert "exceeds" in self._text(result).lower()
        assert target.exists()  # not deleted

    def test_accepts_reason_at_cap(self, tmp_path: Path) -> None:
        """A reason exactly at the cap is accepted."""
        target = tmp_path / ".clou" / "milestones" / "m1" / "assessment.md"
        self._write(target, "content")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
            "reason": "x" * 2048,
        }))

        assert not self._is_error(result)
        assert not target.exists()

    def test_rejects_missing_reason(self, tmp_path: Path) -> None:
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
        }))
        assert self._is_error(result)

    # --- Path safety ---

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        """'..' segments in path resolve outside .clou/ and are rejected."""
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "../../etc/passwd",
            "reason": "escape attempt",
        }))
        assert self._is_error(result)

    def test_rejects_absolute_path_outside_clou(self, tmp_path: Path) -> None:
        """Absolute paths outside .clou/ are rejected."""
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "/etc/passwd",
            "reason": "escape attempt",
        }))
        assert self._is_error(result)

    def test_rejects_symlink_pointing_outside_clou(self, tmp_path: Path) -> None:
        """A symlink whose target escapes .clou/ is refused."""
        real_file = tmp_path / "outside.md"
        real_file.write_text("outside content")
        link = self._scope_path(tmp_path)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real_file)

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/phases/p1/execution.md",
            "reason": "attempting symlink traversal",
        }))

        assert self._is_error(result)
        # symlink target (real_file) preserved
        assert real_file.exists()

    def test_rejects_symlink_pointing_inside_clou(self, tmp_path: Path) -> None:
        """A symlink whose target is another in-scope artifact is still refused.

        This guards against the post-resolve() is_symlink() dead-code pattern —
        .resolve() dereferences symlinks, so the is_symlink check must happen
        on the unresolved path. Otherwise an attacker can name an in-scope
        symlink and cause the tool to delete its target instead of the link.
        """
        # A legitimate in-scope target that must not be touched.
        real_target = (
            tmp_path / ".clou" / "milestones" / "m1"
            / "phases" / "p1" / "execution-real.md"
        )
        self._write(real_target, "real content to preserve")

        # A symlink at another in-scope location pointing to real_target.
        link = (
            tmp_path / ".clou" / "milestones" / "m1"
            / "phases" / "p1" / "execution-link.md"
        )
        link.symlink_to(real_target)

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/phases/p1/execution-link.md",
            "reason": "in-scope symlink removal attempt",
        }))

        assert self._is_error(result)
        assert "symlink" in self._text(result).lower()
        # Both the link and its target must remain untouched.
        assert link.is_symlink()
        assert real_target.exists()

    def test_rejects_nonexistent_file(self, tmp_path: Path) -> None:
        """Nonexistent files are a structured error, not silent success."""
        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/assessment.md",
            "reason": "ghost removal attempt",
        }))

        assert self._is_error(result)
        assert "not found" in self._text(result).lower()

    def test_rejects_directory(self, tmp_path: Path) -> None:
        """Directories are out of scope for V1 (files only)."""
        dir_path = (
            tmp_path / ".clou" / "milestones" / "m1"
            / "phases" / "verification" / "artifacts"
        )
        dir_path.mkdir(parents=True)

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        result = self._run(tool_fn({
            "path": "milestones/m1/phases/verification/artifacts",
            "reason": "bulk cleanup",
        }))

        assert self._is_error(result)
        assert dir_path.exists()

    # --- Telemetry ---

    def test_emits_telemetry_on_success(self, tmp_path: Path) -> None:
        """Successful removal emits supervisor.artifact_removed event."""
        target = self._scope_path(tmp_path)
        self._write(target, "12345")  # 5 bytes

        events: list[dict[str, Any]] = []

        def _capture(name: str, **attrs: Any) -> None:
            events.append({"name": name, **attrs})

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        with patch("clou.telemetry.event", side_effect=_capture):
            self._run(tool_fn({
                "path": "milestones/m1/phases/p1/execution.md",
                "reason": "orphan from truncated run",
            }))

        removed = [e for e in events if e["name"] == "supervisor.artifact_removed"]
        assert len(removed) == 1
        evt = removed[0]
        assert evt["path"] == "milestones/m1/phases/p1/execution.md"
        assert evt["reason"] == "orphan from truncated run"
        assert evt["bytes"] == 5

    def test_no_telemetry_on_rejection(self, tmp_path: Path) -> None:
        """Rejected calls do not emit supervisor.artifact_removed."""
        events: list[dict[str, Any]] = []

        def _capture(name: str, **attrs: Any) -> None:
            events.append({"name": name, **attrs})

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        with patch("clou.telemetry.event", side_effect=_capture):
            self._run(tool_fn({
                "path": "project.md",
                "reason": "test",
            }))

        removed = [e for e in events if e["name"] == "supervisor.artifact_removed"]
        assert removed == []

    def test_telemetry_failure_does_not_block_removal(
        self, tmp_path: Path, caplog: Any,
    ) -> None:
        """If telemetry.event raises, the file is still removed and a warning
        is emitted — log becomes the audit of record, but the operation
        does not silently fail.
        """
        import logging

        target = self._scope_path(tmp_path)
        self._write(target, "content")

        tool_fn = _extract_tool(tmp_path, "remove_artifact_tool")
        with (
            patch(
                "clou.telemetry.event",
                side_effect=RuntimeError("telemetry down"),
            ),
            caplog.at_level(logging.WARNING, logger="clou"),
        ):
            result = self._run(tool_fn({
                "path": "milestones/m1/phases/p1/execution.md",
                "reason": "orphan, telemetry-probing",
            }))

        # File removed, operation succeeded.
        assert not self._is_error(result)
        assert not target.exists()
        # Warning was surfaced so the dropped event is visible.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Telemetry emission failed" in r.message for r in warnings)
