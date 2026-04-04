"""Integration tests against the live Claude Agent SDK.

These tests verify that Clou's orchestrator code works with the real SDK —
that our ClaudeAgentOptions, hooks, agent definitions, and MCP tools are
accepted by the actual subprocess transport.

Requires:
    - ``claude`` CLI installed and on PATH
    - Valid authentication (any method the CLI supports)

Skip behavior:
    All tests skip gracefully when auth is unavailable. This is detected
    via ``claude auth status`` — method-agnostic, no coupling to specific
    env vars.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SandboxSettings,
)
from conftest import requires_claude_auth

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def integration_project(tmp_path: Path) -> Path:
    """Create a minimal .clou/ project scaffold for integration tests."""
    prompts = tmp_path / ".clou" / "prompts"
    prompts.mkdir(parents=True)

    # Minimal system prompts
    (prompts / "coordinator-system.xml").write_text(
        "<identity>You are a test coordinator.</identity>\n"
        "<invariants>Respond concisely.</invariants>\n"
    )
    (prompts / "worker-system.xml").write_text(
        "<identity>You are a test worker.</identity>\n"
    )
    (prompts / "verifier-system.xml").write_text(
        "<identity>You are a test verifier.</identity>\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# SDK Client Tests
# ---------------------------------------------------------------------------


@requires_claude_auth
class TestSDKHandshake:
    """Verify our options format is accepted by the real SDK."""

    async def test_minimal_client_connects(self) -> None:
        """ClaudeSDKClient connects and disconnects without error."""
        options = ClaudeAgentOptions(
            system_prompt="You are a test assistant. Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error

    async def test_sandbox_settings_accepted(self) -> None:
        """SandboxSettings passes through to the SDK without error."""
        options = ClaudeAgentOptions(
            system_prompt="Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
            sandbox=SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True),
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error

    async def test_effort_parameter_accepted(self) -> None:
        """effort='max' is accepted without error."""
        options = ClaudeAgentOptions(
            system_prompt="Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
            effort="max",
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error

    async def test_max_budget_accepted(self) -> None:
        """max_budget_usd is accepted without error."""
        options = ClaudeAgentOptions(
            system_prompt="Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
            max_budget_usd=0.10,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error


# ---------------------------------------------------------------------------
# Hook Tests
# ---------------------------------------------------------------------------


@requires_claude_auth
class TestHooksIntegration:
    """Verify our hook format is accepted by the real SDK."""

    async def test_hooks_format_accepted(self) -> None:
        """Our HookMatcher dict format doesn't error at connection time."""
        hook_called = False

        async def pre_hook(tool_name: str, tool_input: dict) -> dict:
            nonlocal hook_called
            hook_called = True
            return {"decision": "approve"}

        hooks = {
            "PreToolUse": [
                HookMatcher(
                    matcher={"tool_name": "Read"},
                    hooks=[pre_hook],
                )
            ]
        }
        options = ClaudeAgentOptions(
            system_prompt="Respond with one word. Do not use tools.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
            hooks=hooks,
        )
        # Connection succeeds — hooks format is valid
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error


# ---------------------------------------------------------------------------
# Agent Definition Tests
# ---------------------------------------------------------------------------


@requires_claude_auth
class TestAgentDefinitionsIntegration:
    """Verify our AgentDefinition format is accepted by the real SDK."""

    async def test_agent_definitions_accepted(self, integration_project: Path) -> None:
        """Our agent definition dict is valid for the SDK."""
        agents = {
            "implementer": AgentDefinition(
                description="Test agent for implementation.",
                prompt="You are a test worker. Respond concisely.",
                tools=["Read", "Write", "Bash"],
                model="sonnet",
            ),
        }
        options = ClaudeAgentOptions(
            system_prompt="You are a coordinator. Respond with one word.",
            permission_mode="bypassPermissions",
            cwd=str(integration_project),
            model="sonnet",
            agents=agents,
            max_turns=1,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error


# ---------------------------------------------------------------------------
# Result Message Tests
# ---------------------------------------------------------------------------


@requires_claude_auth
class TestResultMessage:
    """Verify ResultMessage fields match our expectations."""

    async def test_result_has_usage(self) -> None:
        """ResultMessage includes usage dict with token counts."""
        options = ClaudeAgentOptions(
            system_prompt="Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert msg.usage is not None
                    assert "input_tokens" in msg.usage
                    assert "output_tokens" in msg.usage

    async def test_result_has_session_id(self) -> None:
        """ResultMessage includes a session_id we can use for tracking."""
        options = ClaudeAgentOptions(
            system_prompt="Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert msg.session_id is not None
                    assert len(msg.session_id) > 0


# ---------------------------------------------------------------------------
# Orchestrator Integration
# ---------------------------------------------------------------------------


@requires_claude_auth
class TestOrchestratorIntegration:
    """Verify orchestrator helpers produce SDK-compatible output."""

    async def test_load_prompt_output_accepted(self, integration_project: Path) -> None:
        """System prompt loaded by load_prompt() is accepted by SDK."""
        from clou.prompts import load_prompt

        prompt = load_prompt(
            "coordinator", integration_project, milestone="test-milestone"
        )
        options = ClaudeAgentOptions(
            system_prompt=prompt,
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error

    async def test_build_hooks_output_accepted(self, integration_project: Path) -> None:
        """Hooks built by build_hooks() are accepted by SDK."""
        from clou.hooks import build_hooks
        from clou.hooks import to_sdk_hooks as _to_sdk_hooks

        hook_configs = build_hooks(
            "coordinator", integration_project, milestone="test-milestone"
        )
        sdk_hooks = _to_sdk_hooks(hook_configs)

        options = ClaudeAgentOptions(
            system_prompt="Respond with one word.",
            permission_mode="bypassPermissions",
            model="sonnet",
            max_turns=1,
            hooks=sdk_hooks,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error

    async def test_full_coordinator_options(self, integration_project: Path) -> None:
        """The full ClaudeAgentOptions we build for coordinators is valid."""
        from clou.hooks import build_hooks
        from clou.hooks import to_sdk_hooks as _to_sdk_hooks

        hook_configs = build_hooks(
            "coordinator", integration_project, milestone="test-milestone"
        )
        agents = {
            "implementer": AgentDefinition(
                description="Implement code changes.",
                prompt="You are a test worker.",
                tools=["Read", "Write", "Bash", "Grep", "Glob"],
                model="sonnet",
            ),
            "verifier": AgentDefinition(
                description="Verify milestone completion.",
                prompt="You are a test verifier.",
                tools=["Read", "Write", "Bash", "Grep", "Glob"],
                model="sonnet",
            ),
        }

        options = ClaudeAgentOptions(
            system_prompt="You are a coordinator. Respond with one word.",
            permission_mode="bypassPermissions",
            cwd=str(integration_project),
            model="sonnet",
            agents=agents,
            hooks=_to_sdk_hooks(hook_configs),
            max_turns=1,
            max_budget_usd=0.10,
            effort="high",
            sandbox=SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True),
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say 'ok'.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    assert not msg.is_error
                    assert msg.usage is not None
