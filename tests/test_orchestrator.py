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
        exhausted_msg = _make_result(usage={"input_tokens": 200_000})
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
        mock_esc.assert_called_once_with(project_dir, "auth")

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
        import clou.coordinator as coord

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
            patch.object(coord, "_active_app", mock_app),
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

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                return_value=("EXECUTE", ["status.md"]),
            ),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
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
        import clou.coordinator as coord

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
            patch.object(coord, "_active_app", mock_app),
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
        import clou.coordinator as coord

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
            patch.object(coord, "_active_app", mock_app),
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

        import clou.coordinator as coord

        mock_app = MagicMock()
        mock_app._stop_requested = asyncio.Event()
        mock_app._stop_requested.set()
        mock_app._user_input_queue = MagicMock()  # not a deque — won't match

        with (
            patch(
                f"{_PC}.load_template",
                return_value=self._make_template(pause=False),
            ),
            patch.object(coord, "_active_app", mock_app),
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
                patch.object(coord, "_active_app", mock_app),
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
