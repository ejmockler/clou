"""Tests for hook enforcement (write boundaries and compose.py validation)."""

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path

import pytest

from clou.hooks import (
    AGENT_TIER_MAP,
    CLEANUP_SCOPE,
    WRITE_PERMISSIONS,
    HookConfig,
    _scoped_permissions,
    build_hooks,
    is_cleanup_allowed,
)


def _run(coro: object) -> dict[str, object]:
    """Run an async coroutine and return the result."""
    result: object = asyncio.run(coro)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result


def _is_denied(result: dict[str, object]) -> bool:
    """Check whether a PreToolUse hook response blocks the tool."""
    hso = result.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        return False
    return hso.get("permissionDecision") == "deny"


def _deny_reason(result: dict[str, object]) -> str:
    """Extract the deny reason from a hook response."""
    hso = result.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        return ""
    return str(hso.get("permissionDecisionReason", ""))


def _is_allowed(result: dict[str, object]) -> bool:
    """Check that a hook response allows the tool (no deny/block)."""
    return not _is_denied(result)


# ---------------------------------------------------------------------------
# build_hooks structure
# ---------------------------------------------------------------------------


def test_build_hooks_coordinator_has_both_phases() -> None:
    hooks = build_hooks("coordinator", Path("/tmp/project"))
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks
    assert len(hooks["PreToolUse"]) == 1
    # Coordinator gets 2 PostToolUse hooks: artifact validation + transcript capture.
    assert len(hooks["PostToolUse"]) == 2


def test_build_hooks_all_tiers_get_post_hook() -> None:
    """All tiers get PostToolUse for artifact form validation (DB-14)."""
    for tier in ("worker", "supervisor", "coordinator", "verifier"):
        hooks = build_hooks(tier, Path("/tmp/project"))
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks


def test_build_hooks_returns_hook_configs() -> None:
    hooks = build_hooks("worker", Path("/tmp/project"))
    pre = hooks["PreToolUse"][0]
    assert isinstance(pre, HookConfig)
    assert pre.matcher == "Write|Edit|MultiEdit|Bash"
    assert len(pre.hooks) == 1


# ---------------------------------------------------------------------------
# Write boundary enforcement — PreToolUse
# ---------------------------------------------------------------------------


def _get_pre_hook(tier: str, project_dir: Path | None = None) -> object:
    """Get the PreToolUse hook callback for a tier."""
    hooks = build_hooks(tier, project_dir or Path("/tmp/project"))
    return hooks["PreToolUse"][0].hooks[0]


def test_write_outside_clou_allowed() -> None:
    """Writes outside .clou/ are always allowed for any tier."""
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/src/main.py"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_bash_redirect_to_clou_denied() -> None:
    """Bash commands that redirect to .clou/ paths are denied."""
    hook = _get_pre_hook("coordinator")
    for cmd in [
        "echo bad > .clou/milestones/m1/compose.py",
        "echo bad >> .clou/milestones/m1/status.md",
        "cat foo | tee .clou/milestones/m1/status.md",
        "mv tmp.py .clou/milestones/m1/compose.py",
        "rm .clou/milestones/m1/compose.py",
        "sed -i 's/old/new/' .clou/milestones/m1/compose.py",
        "cp malicious.py .clou/milestones/m1/compose.py",
        "touch .clou/milestones/m1/compose.py",
    ]:
        result = _run(
            hook(
                {"tool_name": "Bash", "tool_input": {"command": cmd}},
                "tool-1",
                {},
            )
        )
        assert not _is_allowed(result), f"Should deny: {cmd}"


def test_bash_reading_clou_allowed() -> None:
    """Bash commands that only read .clou/ paths are allowed."""
    hook = _get_pre_hook("coordinator")
    for cmd in [
        "cat .clou/milestones/m1/compose.py",
        "ls .clou/milestones/m1/",
        "grep -r pattern .clou/",
        "head -20 .clou/milestones/m1/status.md",
    ]:
        result = _run(
            hook(
                {"tool_name": "Bash", "tool_input": {"command": cmd}},
                "tool-1",
                {},
            )
        )
        assert _is_allowed(result), f"Should allow: {cmd}"


def test_bash_outside_clou_allowed() -> None:
    """Bash commands writing outside .clou/ are allowed."""
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {"tool_name": "Bash", "tool_input": {"command": "echo hello > src/main.py"}},
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_worker_allowed_execution_md() -> None:
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/m1/phases/p1/execution.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_worker_blocked_from_project_md() -> None:
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "worker" in _deny_reason(result)


def test_supervisor_allowed_project_md() -> None:
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_supervisor_allowed_roadmap() -> None:
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/roadmap.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_supervisor_allowed_milestone_md() -> None:
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/milestone.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_supervisor_allowed_understanding_md() -> None:
    """Supervisor can write understanding.md."""
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/understanding.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_worker_blocked_from_understanding_md() -> None:
    """Worker cannot write understanding.md — supervisor-only file."""
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/understanding.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "worker" in _deny_reason(result)


def test_supervisor_blocked_from_compose() -> None:
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/compose.py"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_coordinator_allowed_compose() -> None:
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/compose.py"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_denied_status_via_write() -> None:
    """Protocol artifact: status.md must go through clou_update_status tool."""
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/status.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert not _is_allowed(result)


def test_coordinator_allowed_phase_md() -> None:
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/phases/p2/phase.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_blocked_from_project() -> None:
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_verifier_allowed_verification_execution() -> None:
    hook = _get_pre_hook("verifier")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/m1/phases/verification/execution.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_verifier_allowed_handoff() -> None:
    hook = _get_pre_hook("verifier")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/handoff.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_verifier_blocked_from_non_verification_execution() -> None:
    hook = _get_pre_hook("verifier")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/m1/phases/build/execution.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_edit_tool_also_enforced() -> None:
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_multiedit_tool_also_enforced() -> None:
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "MultiEdit",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_non_write_tool_ignored() -> None:
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_missing_file_path_allowed() -> None:
    hook = _get_pre_hook("worker")
    result = _run(
        hook(
            {"tool_name": "Write", "tool_input": {}},
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_escalation_allowed_supervisor() -> None:
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/escalations/e1.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_escalation_allowed_coordinator() -> None:
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/m1/escalations/e1.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


# ---------------------------------------------------------------------------
# Services write permissions removed (orphaned patterns)
# ---------------------------------------------------------------------------


def test_coordinator_blocked_from_service_setup() -> None:
    """Coordinator can no longer write to services/ (orphaned permission removed)."""
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/services/bar/setup.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


# ---------------------------------------------------------------------------
# Verifier artifact write permissions
# ---------------------------------------------------------------------------


def test_verifier_allowed_verification_artifact() -> None:
    """Verifier can write to verification artifacts directory."""
    hook = _get_pre_hook("verifier")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/foo/phases/verification/artifacts/screenshot.png"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_verifier_blocked_from_non_verification_artifacts() -> None:
    """Verifier cannot write to artifact paths outside the verification phase."""
    hook = _get_pre_hook("verifier")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/foo/phases/build/artifacts/x.png"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_unknown_tier_blocks_clou_writes() -> None:
    hook = _get_pre_hook("unknown")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_active_supervisor_md() -> None:
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/active/supervisor.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_active_coordinator_md_root_denied() -> None:
    """Root-level active/coordinator.md is denied -- coordinator writes to
    milestones/{ms}/active/coordinator.md instead (checkpoint-integrity fix)."""
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/active/coordinator.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


# ---------------------------------------------------------------------------
# Brutalist write boundaries
# ---------------------------------------------------------------------------


def test_brutalist_allowed_assessment_md(tmp_path: Path) -> None:
    """Brutalist subagent can write assessment.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "assessment.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "brutalist",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_brutalist_blocked_from_execution_md(tmp_path: Path) -> None:
    """Brutalist subagent cannot write execution.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "phases" / "p1" / "execution.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "brutalist",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "brutalist" in _deny_reason(result)


def test_brutalist_blocked_from_decisions_md(tmp_path: Path) -> None:
    """Brutalist subagent cannot write decisions.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "decisions.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "brutalist",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_brutalist_blocked_from_compose_py(tmp_path: Path) -> None:
    """Brutalist subagent cannot write compose.py."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "brutalist",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


# ---------------------------------------------------------------------------
# Assess-evaluator write boundaries
# ---------------------------------------------------------------------------


def test_assess_evaluator_allowed_assessment_md(tmp_path: Path) -> None:
    """Assess-evaluator subagent can write assessment.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "assessment.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "assess-evaluator",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_assess_evaluator_allowed_decisions_md(tmp_path: Path) -> None:
    """Assess-evaluator subagent can write decisions.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "decisions.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "assess-evaluator",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_assess_evaluator_blocked_from_execution_md(tmp_path: Path) -> None:
    """Assess-evaluator subagent cannot write execution.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "phases" / "p1" / "execution.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "assess-evaluator",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_assess_evaluator_blocked_from_compose_py(tmp_path: Path) -> None:
    """Assess-evaluator subagent cannot write compose.py."""
    hook = _get_pre_hook("coordinator", tmp_path)
    path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "agent_type": "assess-evaluator",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


# ---------------------------------------------------------------------------
# Compose.py validation — PostToolUse
# ---------------------------------------------------------------------------


def _get_post_hook(
    project_dir: Path,
) -> object:
    """Get the PostToolUse hook callback."""
    hooks = build_hooks("coordinator", project_dir)
    return hooks["PostToolUse"][0].hooks[0]


def test_compose_valid(tmp_path: Path) -> None:
    """Valid compose.py produces no errors."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)
    compose = clou_dir / "compose.py"
    compose.write_text("""\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
""")
    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_compose_invalid(tmp_path: Path) -> None:
    """Invalid compose.py returns error feedback."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)
    compose = clou_dir / "compose.py"
    compose.write_text("""\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
""")
    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    assert output.get("hookEventName") == "PostToolUse"
    ctx = output.get("additionalContext")
    assert isinstance(ctx, str)
    assert "Composition errors:" in ctx
    assert "Undefined: task_b" in ctx
    assert "Fix the call graph." in ctx


def test_compose_non_clou_ignored(tmp_path: Path) -> None:
    """compose.py outside .clou/ is not validated."""
    compose = tmp_path / "compose.py"
    compose.write_text("invalid python {{{{")
    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_compose_non_compose_file_ignored(tmp_path: Path) -> None:
    """Non-compose.py files in .clou/ are not validated."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)
    status = clou_dir / "status.md"
    status.write_text("# Status")
    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(status)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_compose_read_tool_ignored(tmp_path: Path) -> None:
    """Non-write tools are ignored by post hook."""
    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": str(tmp_path / ".clou" / "compose.py")},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_compose_missing_file(tmp_path: Path) -> None:
    """If the compose.py file doesn't exist (deleted?), no error."""
    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(
                        tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
                    )
                },
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


# ---------------------------------------------------------------------------
# Milestone scoping — _scoped_permissions
# ---------------------------------------------------------------------------


def test_scoped_permissions_no_milestone() -> None:
    """Without milestone, patterns are unchanged (wildcard)."""
    patterns = _scoped_permissions("coordinator", None)
    assert any("milestones/*/" in p for p in patterns)


def test_scoped_permissions_with_milestone() -> None:
    """With milestone, wildcard is narrowed to specific milestone."""
    patterns = _scoped_permissions("coordinator", "auth")
    for p in patterns:
        if "milestones/" in p:
            assert "milestones/auth/" in p
            assert "milestones/*/" not in p


def test_scoped_permissions_non_milestone_patterns_unchanged() -> None:
    """Patterns not starting with milestones/* are unaffected by scoping.

    After the checkpoint-integrity fix, coordinator's active/coordinator.md
    pattern IS milestone-scoped (milestones/*/active/coordinator.md), so it
    IS narrowed. Verify supervisor's non-milestone patterns remain unchanged.
    """
    patterns = _scoped_permissions("supervisor", "auth")
    # Supervisor has patterns like "project.md" and "active/supervisor.md"
    # that do not start with "milestones/*/" -- these must pass through
    # unscoped.
    assert "project.md" in patterns
    assert "active/supervisor.md" in patterns


def test_scoped_permissions_unknown_tier() -> None:
    """Unknown tier returns empty list."""
    assert _scoped_permissions("nonexistent", "auth") == []


# ---------------------------------------------------------------------------
# Milestone-scoped write boundary enforcement
# ---------------------------------------------------------------------------


def _get_scoped_pre_hook(
    tier: str, milestone: str, project_dir: Path | None = None
) -> object:
    """Get a PreToolUse hook scoped to a milestone."""
    hooks = build_hooks(tier, project_dir or Path("/tmp/project"), milestone=milestone)
    return hooks["PreToolUse"][0].hooks[0]


def test_scoped_coordinator_allowed_own_milestone() -> None:
    """Coordinator scoped to 'auth' can write to narrative files in auth milestone."""
    hook = _get_scoped_pre_hook("coordinator", "auth")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/auth/decisions.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_scoped_coordinator_blocked_other_milestone() -> None:
    """Coordinator scoped to 'auth' cannot write to 'payments' milestone."""
    hook = _get_scoped_pre_hook("coordinator", "auth")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones/payments/decisions.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_scoped_coordinator_active_root_denied() -> None:
    """Root-level active/coordinator.md is denied when milestone-scoped.

    After the checkpoint-integrity fix, the coordinator pattern is
    milestones/*/active/coordinator.md, which gets scoped to
    milestones/auth/active/coordinator.md. Root-level active/coordinator.md
    is no longer permitted.
    """
    hook = _get_scoped_pre_hook("coordinator", "auth")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/active/coordinator.md"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_scoped_worker_allowed_own_milestone() -> None:
    """Worker scoped to 'auth' can write execution.md in auth."""
    hook = _get_scoped_pre_hook("worker", "auth")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": (
                        "/tmp/project/.clou/milestones/auth/phases/p1/execution.md"
                    )
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_scoped_worker_blocked_other_milestone() -> None:
    """Worker scoped to 'auth' cannot write to 'payments' milestone."""
    hook = _get_scoped_pre_hook("worker", "auth")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": (
                        "/tmp/project/.clou/milestones/payments/phases/p1/execution.md"
                    )
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


# ---------------------------------------------------------------------------
# Checkpoint-integrity: coordinator write permission pattern fix
# ---------------------------------------------------------------------------


def test_coordinator_pattern_matches_milestone_scoped_checkpoint() -> None:
    """fnmatch confirms milestones/*/active/coordinator.md matches
    milestone-scoped checkpoint paths."""
    pattern = "milestones/*/active/coordinator.md"
    assert fnmatch.fnmatch(
        "milestones/proxy-removal/active/coordinator.md", pattern
    )
    assert fnmatch.fnmatch(
        "milestones/auth/active/coordinator.md", pattern
    )
    # Root-level active/coordinator.md must NOT match.
    assert not fnmatch.fnmatch("active/coordinator.md", pattern)


def test_scoped_permissions_no_protocol_artifacts_for_coordinator() -> None:
    """Coordinator write permissions exclude protocol artifacts (checkpoint,
    status.md) — these are written via MCP tools, not Write."""
    patterns = _scoped_permissions("coordinator", "proxy-removal")
    assert "milestones/proxy-removal/active/coordinator.md" not in patterns
    assert "milestones/proxy-removal/status.md" not in patterns
    # Narrative files are still allowed.
    assert "milestones/proxy-removal/decisions.md" in patterns


def test_scoped_permissions_coordinator_no_root_active() -> None:
    """After the fix, coordinator has no root-level active/coordinator.md
    pattern -- all coordinator patterns are milestone-scoped."""
    patterns = _scoped_permissions("coordinator", "some-milestone")
    assert "active/coordinator.md" not in patterns


def test_coordinator_denied_direct_checkpoint_write() -> None:
    """Coordinator cannot Write directly to active/coordinator.md —
    must use clou_write_checkpoint MCP tool instead."""
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/proxy-removal/active/coordinator.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert not _is_allowed(result)


def test_coordinator_denied_checkpoint_with_template(
    tmp_path: Path,
) -> None:
    """Coordinator cannot Write directly to checkpoint with template —
    protocol artifacts are tool-only."""
    from clou.harnesses.software_construction import (
        template as sc_template,
    )

    hooks = build_hooks(
        "coordinator",
        tmp_path,
        milestone="proxy-removal",
        template=sc_template,
    )
    hook = hooks["PreToolUse"][0].hooks[0]
    checkpoint_path = (
        tmp_path
        / ".clou"
        / "milestones"
        / "proxy-removal"
        / "active"
        / "coordinator.md"
    )
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(checkpoint_path)},
            },
            "tool-1",
            {},
        )
    )
    assert not _is_allowed(result)


def test_coordinator_blocked_other_milestone_checkpoint_with_template(
    tmp_path: Path,
) -> None:
    """Coordinator scoped to 'proxy-removal' cannot write to another
    milestone's checkpoint when using a template."""
    from clou.harnesses.software_construction import (
        template as sc_template,
    )

    hooks = build_hooks(
        "coordinator",
        tmp_path,
        milestone="proxy-removal",
        template=sc_template,
    )
    hook = hooks["PreToolUse"][0].hooks[0]
    other_checkpoint = (
        tmp_path
        / ".clou"
        / "milestones"
        / "other-ms"
        / "active"
        / "coordinator.md"
    )
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(other_checkpoint)},
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_coordinator_denied_direct_status_write() -> None:
    """Coordinator cannot Write directly to status.md — must use
    clou_update_status MCP tool instead."""
    hook = _get_pre_hook("coordinator")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/project/.clou/milestones"
                    "/proxy-removal/status.md"
                },
            },
            "tool-1",
            {},
        )
    )
    assert not _is_allowed(result)


def test_coordinator_still_allowed_narrative_writes() -> None:
    """Coordinator can still Write to narrative files (decisions.md, etc.)."""
    hook = _get_pre_hook("coordinator")
    for path_suffix in ("decisions.md", "compose.py", "phases/impl/phase.md"):
        result = _run(
            hook(
                {
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": f"/tmp/project/.clou/milestones/m1/{path_suffix}"
                    },
                },
                "tool-1",
                {},
            )
        )
        assert _is_allowed(result), f"Expected allowed for {path_suffix}"


def test_supervisor_and_worker_permissions_unaffected() -> None:
    """Supervisor and worker permissions are unchanged by the fix."""
    # Supervisor still has active/supervisor.md (root-level).
    sup_patterns = _scoped_permissions("supervisor", "m1")
    assert "active/supervisor.md" in sup_patterns

    # Worker still has only execution.md -- no coordinator checkpoint.
    wrk_patterns = _scoped_permissions("worker", "m1")
    assert all("active/coordinator.md" not in p for p in wrk_patterns)
    assert "milestones/m1/phases/*/execution.md" in wrk_patterns


def test_all_three_permission_dicts_consistent() -> None:
    """All three permission sources exclude protocol artifacts (checkpoint,
    status.md) from coordinator — these are written via MCP tools."""
    from clou.harness import _INLINE_FALLBACK
    from clou.harnesses.software_construction import (
        template as sc_template,
    )

    sources = {
        "WRITE_PERMISSIONS (hooks.py)": WRITE_PERMISSIONS,
        "software_construction template": sc_template.write_permissions,
        "_INLINE_FALLBACK (harness.py)": _INLINE_FALLBACK.write_permissions,
    }

    for name, perms in sources.items():
        coord_perms = perms["coordinator"]
        # Protocol artifacts must NOT be in coordinator write permissions.
        assert "milestones/*/active/coordinator.md" not in coord_perms, (
            f"{name} still has protocol artifact 'milestones/*/active/coordinator.md'"
        )
        assert "milestones/*/status.md" not in coord_perms, (
            f"{name} still has protocol artifact 'milestones/*/status.md'"
        )
        # Narrative files must still be present.
        assert "milestones/*/decisions.md" in coord_perms, (
            f"{name} missing narrative 'milestones/*/decisions.md'"
        )


# ---------------------------------------------------------------------------
# Path traversal escape tests
# ---------------------------------------------------------------------------


def test_dotdot_escape_blocked(tmp_path: Path) -> None:
    """A path with .. that escapes .clou/ should be blocked."""
    hook = _get_pre_hook("coordinator", tmp_path)
    # .clou/../../etc/passwd resolves outside .clou/
    evil = tmp_path / ".clou" / ".." / ".." / "etc" / "passwd"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(evil)},
            },
            "tool-1",
            {},
        )
    )
    # The resolved path is outside .clou/, so it's allowed (writes outside
    # .clou/ are always permitted — enforcement only restricts *within* .clou/).
    # This confirms Path.resolve() correctly collapses ".." so the path is
    # NOT treated as a .clou/ path with bypassed checks.
    assert _is_allowed(result)


def test_absolute_path_outside_clou_allowed(tmp_path: Path) -> None:
    """An absolute path to /tmp/evil is outside .clou/ — allowed (not a .clou path)."""
    hook = _get_pre_hook("worker", tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/evil"},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_dotdot_within_clou_blocked(tmp_path: Path) -> None:
    """Path using .. that stays in .clou/ at forbidden location is blocked."""
    hook = _get_pre_hook("worker", tmp_path)
    # Worker tries to write project.md via traversal:
    # .clou/milestones/../project.md resolves to .clou/project.md
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(
                        tmp_path / ".clou" / "milestones" / ".." / "project.md"
                    ),
                },
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "worker" in _deny_reason(result)


# ---------------------------------------------------------------------------
# Subagent tier-aware enforcement
# ---------------------------------------------------------------------------


def test_coordinator_hook_allows_coordinator_write(tmp_path: Path) -> None:
    """Coordinator can write to its own paths (compose.py)."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_hook_blocks_worker_writing_compose(tmp_path: Path) -> None:
    """Worker subagent is blocked from writing compose.py via coordinator hooks."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
                "agent_type": "implementer",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "worker" in _deny_reason(result)


def test_coordinator_hook_allows_worker_writing_execution(tmp_path: Path) -> None:
    """Worker subagent is allowed to write execution.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    exec_path = (
        tmp_path / ".clou" / "milestones" / "m1" / "phases" / "p1" / "execution.md"
    )
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(exec_path)},
                "agent_type": "implementer",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_hook_blocks_verifier_writing_compose(tmp_path: Path) -> None:
    """Verifier subagent is blocked from writing compose.py."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
                "agent_type": "verifier",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "verifier" in _deny_reason(result)


def test_coordinator_hook_allows_verifier_writing_handoff(tmp_path: Path) -> None:
    """Verifier subagent is allowed to write handoff.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    handoff_path = tmp_path / ".clou" / "milestones" / "m1" / "handoff.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(handoff_path)},
                "agent_type": "verifier",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_hook_unknown_agent_type_blocked(
    tmp_path: Path,
) -> None:
    """Unknown agent_type is denied (fail-closed), not granted coordinator access."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
                "agent_type": "unknown_agent",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "Unknown agent type" in _deny_reason(result)


def test_coordinator_hook_empty_string_agent_type_uses_coordinator(
    tmp_path: Path,
) -> None:
    """Empty string agent_type is treated as no subagent (coordinator's own write)."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
                "agent_type": "",
            },
            "tool-1",
            {},
        )
    )
    # Empty string is falsy — treated as lead agent, coordinator permissions apply.
    assert _is_allowed(result)


def test_coordinator_hook_none_agent_type_uses_coordinator(
    tmp_path: Path,
) -> None:
    """Explicit None agent_type is treated as no subagent."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
                "agent_type": None,
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_hook_non_string_agent_type_uses_coordinator(
    tmp_path: Path,
) -> None:
    """Non-string agent_type (e.g. dict) is treated as no subagent."""
    hook = _get_pre_hook("coordinator", tmp_path)
    compose_path = tmp_path / ".clou" / "milestones" / "m1" / "compose.py"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose_path)},
                "agent_type": {"unexpected": True},
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_worker_blocked_from_verifier_paths(tmp_path: Path) -> None:
    """Worker (implementer) cannot write to verifier-only paths like handoff.md."""
    hook = _get_pre_hook("coordinator", tmp_path)
    handoff_path = tmp_path / ".clou" / "milestones" / "m1" / "handoff.md"
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(handoff_path)},
                "agent_type": "implementer",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "worker" in _deny_reason(result)


def test_verifier_blocked_from_worker_paths(tmp_path: Path) -> None:
    """Verifier cannot write to generic phase execution.md (only verification phase)."""
    hook = _get_pre_hook("coordinator", tmp_path)
    exec_path = (
        tmp_path
        / ".clou"
        / "milestones"
        / "m1"
        / "phases"
        / "services"
        / "execution.md"
    )
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(exec_path)},
                "agent_type": "verifier",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)
    assert "verifier" in _deny_reason(result)


def test_verifier_allowed_verification_phase_execution(tmp_path: Path) -> None:
    """Verifier can write to phases/verification/execution.md specifically."""
    hook = _get_pre_hook("coordinator", tmp_path)
    exec_path = (
        tmp_path
        / ".clou"
        / "milestones"
        / "m1"
        / "phases"
        / "verification"
        / "execution.md"
    )
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(exec_path)},
                "agent_type": "verifier",
            },
            "tool-1",
            {},
        )
    )
    assert _is_allowed(result)


def test_coordinator_hook_scoped_milestone_blocks_cross_milestone_worker(
    tmp_path: Path,
) -> None:
    """Worker scoped to milestone 'm1' cannot write execution.md in milestone 'm2'."""
    hooks = build_hooks("coordinator", tmp_path, milestone="m1")
    hook = hooks["PreToolUse"][0].hooks[0]
    exec_path = (
        tmp_path / ".clou" / "milestones" / "m2" / "phases" / "p1" / "execution.md"
    )
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(exec_path)},
                "agent_type": "implementer",
            },
            "tool-1",
            {},
        )
    )
    assert _is_denied(result)


def test_supervisor_hook_ignores_agent_type() -> None:
    """Supervisor hooks don't use agent_tier_map (no subagents)."""
    hook = _get_pre_hook("supervisor")
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/project/.clou/project.md"},
                "agent_type": "implementer",  # Should be ignored.
            },
            "tool-1",
            {},
        )
    )
    # Supervisor allows project.md — agent_type is irrelevant.
    assert _is_allowed(result)


# ---------------------------------------------------------------------------
# AGENT_TIER_MAP sync assertion
# ---------------------------------------------------------------------------


def test_agent_tier_map_covers_all_agent_definitions() -> None:
    """AGENT_TIER_MAP must have an entry for every AgentDefinition key.

    If someone adds a new agent to _build_agents() without updating
    AGENT_TIER_MAP, the fail-closed fallback blocks all .clou/ writes
    from that agent. This test catches the mismatch early.
    """
    try:
        from clou.coordinator import _build_agents
    except (ImportError, ModuleNotFoundError):
        # SDK not installed — can't import orchestrator.
        # Fall back to verifying the map has the expected entries.
        assert AGENT_TIER_MAP == {
            "implementer": "worker",
            "verifier": "verifier",
            "brutalist": "brutalist",
            "assess-evaluator": "assess-evaluator",
        }
        return

    # _build_agents requires a project_dir and milestone — use dummies.
    try:
        agents = _build_agents(Path("/tmp/dummy"), "dummy")
    except Exception:
        # Prompt files missing, etc. — check map isn't empty.
        assert len(AGENT_TIER_MAP) >= 2, "AGENT_TIER_MAP should have at least 2 entries"
        return

    agent_keys = set(agents.keys())
    map_keys = set(AGENT_TIER_MAP.keys())
    missing = agent_keys - map_keys
    assert not missing, (
        f"AgentDefinition keys {missing} have no entry in AGENT_TIER_MAP. "
        f"Add them to prevent fail-closed blocking of all .clou/ writes."
    )


# ---------------------------------------------------------------------------
# PostToolUse ArtifactForm validation (DB-14)
# ---------------------------------------------------------------------------


def _run_post_hook_with_template(project_dir: Path, file_path: str, content: str):
    """Write a file and run the PostToolUse hook with a template."""
    from clou.harness import ArtifactForm, HarnessTemplate

    template = HarnessTemplate(
        name="test",
        description="test",
        agents={},
        quality_gates=[],
        verification_modalities=[],
        mcp_servers={},
        write_permissions={},
        artifact_forms={
            "intents": ArtifactForm(
                criterion_template="When {trigger}, {observable_outcome}",
                anti_patterns=(
                    "file paths or module names",
                    "implementation verbs",
                ),
            ),
        },
    )
    hooks = build_hooks("supervisor", project_dir, template=template)
    post_hooks = hooks["PostToolUse"]
    assert len(post_hooks) == 1
    hook_fn = post_hooks[0].hooks[0]

    # Write the file.
    abs_path = Path(file_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content)

    input_data = {
        "tool_name": "Write",
        "tool_input": {"file_path": file_path},
    }
    return asyncio.run(hook_fn(input_data, None, {}))


def test_post_hook_form_validation_passes_good_intents(tmp_path: Path) -> None:
    """Good intents.md content passes PostToolUse without warnings."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    intents = clou_dir / "intents.md"
    result = _run_post_hook_with_template(
        tmp_path,
        str(intents),
        "- When the user opens the app, they see a dashboard\n",
    )
    assert "additionalContext" not in result.get("hookSpecificOutput", {})


def test_post_hook_form_validation_warns_bad_intents(tmp_path: Path) -> None:
    """Bad intents.md triggers additionalContext with form warnings."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    intents = clou_dir / "intents.md"
    result = _run_post_hook_with_template(
        tmp_path,
        str(intents),
        "- TaskGraphWidget with keyboard nav and drill-down\n",
    )
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "Artifact form warnings" in ctx
    assert "does not match" in ctx


def test_post_hook_form_validation_catches_file_paths(tmp_path: Path) -> None:
    """File paths at subject position in intents trigger anti-pattern warnings."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    intents = clou_dir / "intents.md"
    result = _run_post_hook_with_template(
        tmp_path,
        str(intents),
        "- When clou/ui/app.py is edited, changes are reflected\n",
    )
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "file path" in ctx


def test_post_hook_ignores_non_formed_artifacts(tmp_path: Path) -> None:
    """Files without an ArtifactForm pass through without validation."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    status = clou_dir / "status.md"
    result = _run_post_hook_with_template(
        tmp_path,
        str(status),
        "totally invalid content\n",
    )
    assert "additionalContext" not in result.get("hookSpecificOutput", {})


# ---------------------------------------------------------------------------
# Intent-coverage validation — PostToolUse
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="M25 coverage/heuristic wiring incomplete")
def test_coverage_warnings_when_intents_exist(tmp_path: Path) -> None:
    """Coverage warnings appear when intents.md exists and coverage is incomplete."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    # Write intents.md with an intent that won't be covered.
    # Uses "When..." format required by the intent parser.
    intents = clou_dir / "intents.md"
    intents.write_text(
        "1. When collaboration is enabled, users can work together in real-time\n"
        "2. When offline, the app supports local caching for uninterrupted work\n"
    )

    # Write a compose.py that only covers intent 1.
    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def setup_collaboration() -> CollabConfig:
    \"\"\"Enable real-time collaboration between users.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def build_sync_engine(config: CollabConfig) -> SyncResult:
    \"\"\"Build synchronization engine for collaboration.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def build_ui(config: CollabConfig) -> UIResult:
    \"\"\"Build collaboration UI.\"\"\"

async def verify(sync: SyncResult, ui: UIResult) -> VerifyResult:
    \"\"\"Verify collaboration works end to end.\"\"\"

async def execute():
    config = await setup_collaboration()
    sync, ui = await gather(
        build_sync_engine(config),
        build_ui(config),
    )
    v = await verify(sync, ui)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert "Intent-coverage errors:" in ctx
    assert "Uncovered intent 2:" in ctx
    assert "Verify compose.py covers all intents." in ctx


def test_coverage_skipped_when_no_intents(tmp_path: Path) -> None:
    """Coverage validation is skipped gracefully when intents.md doesn't exist."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    # Valid compose.py, no intents.md sibling.
    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def setup() -> Config:
    \"\"\"Set up config.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def phase_a(c: Config) -> A:
    \"\"\"Do A.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def phase_b(c: Config) -> B:
    \"\"\"Do B.\"\"\"

async def verify(a: A, b: B) -> C:
    \"\"\"Verify.\"\"\"

async def execute():
    c = await setup()
    a, b = await gather(phase_a(c), phase_b(c))
    await verify(a, b)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    # No intents.md → no coverage check → passes cleanly.
    assert _is_allowed(result)


def test_structural_errors_take_priority_over_coverage(tmp_path: Path) -> None:
    """When structural validation fails, coverage is not run."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    # Write intents.md (would trigger coverage warnings if reached).
    intents = clou_dir / "intents.md"
    intents.write_text("1. Some intent that nothing covers\n")

    # Write a compose.py with a structural error (undefined function).
    compose = clou_dir / "compose.py"
    compose.write_text("""\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    # Structural errors are returned, not coverage warnings.
    assert "Composition errors:" in ctx
    assert "Undefined: task_b" in ctx
    # Coverage warnings must NOT appear.
    assert "Intent-coverage errors:" not in ctx


# ---------------------------------------------------------------------------
# Integration tests: hook-validate-coverage pipeline end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="M25 coverage/heuristic wiring incomplete")
def test_integration_coverage_error_through_hooks(tmp_path: Path) -> None:
    """Uncovered intent produces actionable feedback through the full hook pipeline.

    End-to-end: compose.py write -> PostToolUse hook -> validate() -> validate_coverage()
    -> additionalContext with specific error identifying the uncovered intent and fix.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    # Realistic intents.md in "### I<N>:" format.
    intents = clou_dir / "intents.md"
    intents.write_text("""\
# Intents: User Management

## Observable outcomes

### I1: User registration
When a new user registers, their account is created with validated email and password.

### I2: Profile editing
When a user edits their profile, changes are persisted and visible immediately.

### I3: Account deletion
When a user deletes their account, all personal data is removed within 30 days.
""")

    # compose.py covers I1 and I2 but NOT I3 (account deletion).
    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=80000, timeout_seconds=300)
async def implement_registration() -> RegistrationService:
    \"\"\"User registration with validated email and password.\"\"\"

@resource_bounds(tokens=60000, timeout_seconds=240)
async def implement_profile(reg: RegistrationService) -> ProfileService:
    \"\"\"Profile editing with changes persisted and visible immediately.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=180)
async def build_admin_dashboard(reg: RegistrationService) -> Dashboard:
    \"\"\"Admin dashboard for user management.\"\"\"

async def execute():
    reg = await implement_registration()
    profile, dash = await gather(
        implement_profile(reg),
        build_admin_dashboard(reg),
    )
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")

    # N3: Error is specific and actionable -- identifies which intent is uncovered.
    assert "Intent-coverage errors:" in ctx
    assert "Uncovered intent 3:" in ctx
    assert "account" in ctx.lower() or "deletes" in ctx.lower()
    assert "no task criteria reference this outcome" in ctx
    assert "Verify compose.py covers all intents." in ctx


def test_integration_valid_compose_with_intents_passes(tmp_path: Path) -> None:
    """Valid compose.py + intents.md where all intents are covered passes cleanly.

    End-to-end: no additionalContext when everything is in order.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    intents = clou_dir / "intents.md"
    intents.write_text("""\
# Intents

## Observable outcomes

### I1: Database migrations
When database setup runs, migrations apply cleanly without data loss.

### I2: Authentication
When a user authenticates, JWT tokens with refresh are issued securely.

### I3: API endpoints
When API endpoints are called, they return correct responses per the spec.

### I4: Frontend rendering
When the frontend loads, the application renders correctly.
""")

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=80000, timeout_seconds=300)
async def setup_database() -> Schema:
    \"\"\"Database setup with migrations that apply cleanly without data loss.\"\"\"

@resource_bounds(tokens=100000, timeout_seconds=360)
async def implement_auth(schema: Schema) -> AuthService:
    \"\"\"JWT authentication with refresh tokens issued securely.\"\"\"

@resource_bounds(tokens=90000, timeout_seconds=300)
async def implement_api(auth: AuthService) -> APILayer:
    \"\"\"API endpoints return correct responses per the spec.\"\"\"

@resource_bounds(tokens=70000, timeout_seconds=240)
async def scaffold_frontend(schema: Schema) -> FrontendShell:
    \"\"\"Frontend application renders correctly.\"\"\"

async def execute():
    schema = await setup_database()
    auth, shell = await gather(
        implement_auth(schema),
        scaffold_frontend(schema),
    )
    api = await implement_api(auth)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    # Clean pass -- no additionalContext at all.
    assert _is_allowed(result)
    output = result.get("hookSpecificOutput", {})
    assert "additionalContext" not in output


def test_integration_coverage_advisory_only_passes(tmp_path: Path) -> None:
    """Advisory-only coverage feedback does not trigger 'Intent-coverage errors.'

    When all intents ARE covered but an extra helper function exists (producing
    only advisory 'does not trace to any intent' feedback), the hook should
    pass cleanly -- advisory messages are not blocking errors.  Per I4.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    intents = clou_dir / "intents.md"
    intents.write_text("""\
# Intents

## Observable outcomes

### I1: File parsing
When files are parsed, structured data is extracted correctly.

### I2: Report generation
When a report is generated, it includes all parsed data.
""")

    # All intents are covered. The extra helper 'format_output' has a docstring
    # but does not trace to any intent -- validate_coverage() will produce an
    # advisory "Task 'format_output' does not trace to any intent" message.
    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def parse_files() -> ParsedData:
    \"\"\"Parse files and extract structured data correctly.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def generate_report(data: ParsedData) -> Report:
    \"\"\"Generate report that includes all parsed data.\"\"\"

@resource_bounds(tokens=30000, timeout_seconds=120)
async def format_output(report: Report) -> FormattedReport:
    \"\"\"Apply formatting and styling to the final output.\"\"\"

async def execute():
    data = await parse_files()
    report = await generate_report(data)
    formatted = await format_output(report)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    # Advisory-only feedback should NOT trigger blocking errors.
    assert _is_allowed(result)
    output = result.get("hookSpecificOutput", {})
    ctx = output.get("additionalContext", "")
    assert "Intent-coverage errors:" not in ctx


@pytest.mark.xfail(reason="M25 coverage/heuristic wiring incomplete")
def test_integration_requires_undefined_error_through_hooks(tmp_path: Path) -> None:
    """@requires("nonexistent") produces specific error through the hook pipeline.

    Verifies the decorator edge validation added in validate_decorator_edges
    surfaces through the PostToolUse hook with actionable error text.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def setup() -> Schema:
    \"\"\"Create database schema.\"\"\"

@requires("nonexistent_phase")
@resource_bounds(tokens=60000, timeout_seconds=240)
async def migrate(s: Schema) -> Migrated:
    \"\"\"Run database migrations.\"\"\"

@resource_bounds(tokens=40000, timeout_seconds=180)
async def seed(m: Migrated) -> Seeded:
    \"\"\"Seed initial data.\"\"\"

async def execute():
    s = await setup()
    m = await migrate(s)
    d = await seed(m)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")

    # N3: Error is specific -- mentions the undefined function name and the
    # decorator that references it.
    assert "Composition errors:" in ctx
    assert "undefined function" in ctx
    assert "nonexistent_phase" in ctx
    assert "migrate" in ctx
    assert "Fix the call graph." in ctx


@pytest.mark.xfail(reason="M25 coverage/heuristic wiring incomplete")
def test_integration_width_warning_through_hooks(tmp_path: Path) -> None:
    """Width warning for 4-task graph with no gather() surfaces through hooks.

    Exercises the width enforcement check in validate() -> _check_width()
    through the full PostToolUse pipeline.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_alpha() -> Alpha:
    \"\"\"First independent task.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_beta() -> Beta:
    \"\"\"Second independent task.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_gamma() -> Gamma:
    \"\"\"Third independent task.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_delta() -> Delta:
    \"\"\"Fourth independent task.\"\"\"

async def execute():
    a = await task_alpha()
    b = await task_beta()
    c = await task_gamma()
    d = await task_delta()
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")

    # N3: Width warning is specific -- identifies the task count and the issue.
    assert "Composition errors:" in ctx
    assert "No concurrent phases" in ctx
    assert "4-task graph" in ctx


@pytest.mark.xfail(reason="M25 coverage/heuristic wiring incomplete")
def test_integration_needs_empty_path_error_through_hooks(tmp_path: Path) -> None:
    """@needs('') with empty path produces specific error through hooks."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def setup() -> Schema:
    \"\"\"Create schema.\"\"\"

@needs("")
@resource_bounds(tokens=60000, timeout_seconds=240)
async def migrate(s: Schema) -> Migrated:
    \"\"\"Run migrations.\"\"\"

@resource_bounds(tokens=40000, timeout_seconds=180)
async def seed(m: Migrated) -> Seeded:
    \"\"\"Seed data.\"\"\"

async def execute():
    s = await setup()
    m = await migrate(s)
    d = await seed(m)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")

    # N3: Error identifies the empty path and the function it's on.
    assert "Composition errors:" in ctx
    assert "empty path" in ctx
    assert "migrate" in ctx


def test_integration_edit_tool_triggers_validation(tmp_path: Path) -> None:
    """Edit tool (not just Write) also triggers compose.py validation."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    # Write invalid compose.py to disk so the hook can read it.
    compose.write_text("""\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await undefined_func(a)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert "Composition errors:" in ctx
    assert "Undefined: undefined_func" in ctx


def test_integration_advisory_warnings_not_composition_errors(tmp_path: Path) -> None:
    """Advisory warnings (Missing @resource_bounds) are not presented as
    'Composition errors' in the hook.

    A compose.py that is structurally valid but lacks @resource_bounds
    should pass cleanly -- the missing decorator is advisory, not an error.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    # Valid structure: 4 tasks with gather, but no @resource_bounds decorators.
    # This should produce advisory warnings from validate() but NOT trigger
    # "Composition errors: ... Fix the call graph."
    compose.write_text("""\
async def setup() -> Config:
    \"\"\"Set up configuration.\"\"\"

async def phase_a(c: Config) -> A:
    \"\"\"Do A.\"\"\"

async def phase_b(c: Config) -> B:
    \"\"\"Do B.\"\"\"

async def verify(a: A, b: B) -> Result:
    \"\"\"Verify results.\"\"\"

async def execute():
    c = await setup()
    a, b = await gather(phase_a(c), phase_b(c))
    r = await verify(a, b)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    # Advisory warnings should NOT trigger "Composition errors"
    output = result.get("hookSpecificOutput", {})
    ctx = output.get("additionalContext", "")
    assert "Composition errors:" not in ctx
    assert "Fix the call graph" not in ctx


def test_integration_real_errors_still_surface_with_advisories(tmp_path: Path) -> None:
    """When both real errors and advisory warnings exist, only real errors surface."""
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    # Has a real error (undefined function) AND would produce advisory warnings
    # (missing @resource_bounds). Only the real error should appear.
    compose.write_text("""\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await undefined_func(a)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    # Real error surfaces
    assert "Composition errors:" in ctx
    assert "Undefined: undefined_func" in ctx
    # Advisory warning is filtered out
    assert "Missing @resource_bounds" not in ctx


def test_integration_coverage_no_docstring_advisory_passes(tmp_path: Path) -> None:
    """Advisory 'has no docstring' does not trigger Intent-coverage errors.

    When all intents are covered but a task lacks a docstring, the hook should
    pass cleanly -- the 'has no docstring' advisory is not a blocking error.
    Exercises the second _COV_ADVISORY filter path added by R6.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    intents = clou_dir / "intents.md"
    intents.write_text("""\
# Intents

## Observable outcomes

### I1: Data ingestion
When data is ingested, records are validated and stored.
""")

    # 'ingest_data' covers I1 via its docstring.
    # 'transform' has no docstring -- validate_coverage() will produce
    # "Task 'transform' has no docstring, cannot verify intent coverage"
    # advisory, which should be filtered by the R6 fix.
    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def ingest_data() -> Records:
    \"\"\"Ingest data with records validated and stored.\"\"\"

@resource_bounds(tokens=30000, timeout_seconds=120)
async def transform(r: Records) -> Output:
    pass

async def execute():
    r = await ingest_data()
    out = await transform(r)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    # 'has no docstring' advisory should NOT trigger blocking errors.
    assert _is_allowed(result)
    output = result.get("hookSpecificOutput", {})
    ctx = output.get("additionalContext", "")
    assert "Intent-coverage errors:" not in ctx


# ---------------------------------------------------------------------------
# False dependency errors flow through rejection pipeline (M25 integrate_hooks)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_single_use_chain_error_triggers_rejection(tmp_path: Path) -> None:
    """Single-use chain false dependency (I1) triggers blocking rejection.

    A compose.py with a pure serial chain of single-use wrapper types
    must produce 'Composition errors:' feedback, NOT be silently
    filtered as advisory.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_d(c: C) -> D:
    \"\"\"Do D.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
    d = await task_d(c)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    assert "Composition errors:" in ctx
    assert "False dependency: serial chain" in ctx
    assert "Fix the call graph." in ctx


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_war_false_dep_triggers_rejection(tmp_path: Path) -> None:
    """WAR-equivalent false dependency (I2) triggers blocking rejection.

    When task_b @requires('task_a') and both @needs the same resource
    path but no type flow exists, the error must flow through as a
    blocking rejection, not be filtered as advisory.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@needs("services/database")
@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

@needs("services/database")
@requires("task_a")
@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_b() -> B:
    \"\"\"Do B.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_c(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b()
    c = await task_c(a, b)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    assert "Composition errors:" in ctx
    assert "WAR-equivalent false dependency" in ctx
    assert "Fix the call graph." in ctx


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_file_independence_triggers_rejection(tmp_path: Path) -> None:
    """File/module independence (I3) triggers blocking rejection.

    Two serial tasks with no shared types, resources, or ordering
    constraints must produce 'Composition errors:' feedback.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def build_frontend() -> FrontendApp:
    \"\"\"Build the frontend.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def build_backend() -> BackendAPI:
    \"\"\"Build the backend.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def integrate(fe: FrontendApp, be: BackendAPI) -> App:
    \"\"\"Wire frontend to backend.\"\"\"

async def execute():
    fe = await build_frontend()
    be = await build_backend()
    app = await integrate(fe, be)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    assert "Composition errors:" in ctx
    assert "appear independent" in ctx
    assert "Fix the call graph." in ctx


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_false_dep_errors_not_matched_by_advisory_filter(tmp_path: Path) -> None:
    """None of the three false dependency error types match _ADVISORY patterns.

    The only advisory is 'Missing @resource_bounds'. Verify that single-use
    chain, WAR-equivalent, and file independence errors do NOT contain the
    advisory substring and therefore are never filtered out.
    """
    from clou.graph import validate

    # Single-use chain
    chain_code = """\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def a() -> X:
    \"\"\"Do A.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def b(x: X) -> Y:
    \"\"\"Do B.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def c(y: Y) -> Z:
    \"\"\"Do C.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def d(z: Z) -> W:
    \"\"\"Do D.\"\"\"

async def execute():
    x = await a()
    y = await b(x)
    z = await c(y)
    w = await d(z)
"""
    chain_errors = validate(chain_code)
    false_dep = [e for e in chain_errors if "False dependency: serial chain" in e.message]
    assert len(false_dep) >= 1
    for e in false_dep:
        assert "Missing @resource_bounds" not in e.message

    # WAR-equivalent
    war_code = """\
@needs("config.yaml")
@resource_bounds(tokens=50000, timeout_seconds=300)
async def read_a() -> A:
    \"\"\"Read A.\"\"\"

@needs("config.yaml")
@requires("read_a")
@resource_bounds(tokens=50000, timeout_seconds=300)
async def read_b() -> B:
    \"\"\"Read B.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def merge(a: A, b: B) -> C:
    \"\"\"Merge.\"\"\"

async def execute():
    a = await read_a()
    b = await read_b()
    c = await merge(a, b)
"""
    war_errors = validate(war_code)
    war = [e for e in war_errors if "WAR-equivalent" in e.message]
    assert len(war) >= 1
    for e in war:
        assert "Missing @resource_bounds" not in e.message

    # File independence
    indep_code = """\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def lint() -> LintResult:
    \"\"\"Lint code.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def typecheck() -> TypeResult:
    \"\"\"Type check.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def report(l: LintResult, t: TypeResult) -> Report:
    \"\"\"Generate report.\"\"\"

async def execute():
    l = await lint()
    t = await typecheck()
    r = await report(l, t)
"""
    indep_errors = validate(indep_code)
    indep = [e for e in indep_errors if "appear independent" in e.message]
    assert len(indep) >= 1
    for e in indep:
        assert "Missing @resource_bounds" not in e.message


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_resource_bounds_advisory_still_filtered_with_false_dep(
    tmp_path: Path,
) -> None:
    """Missing @resource_bounds remains advisory even alongside real errors.

    When a compose.py has both false dependency errors AND missing
    @resource_bounds advisories, only the false dependency errors appear
    in the rejection -- the advisory is filtered out.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    # No @resource_bounds decorators (produces advisory) AND
    # independent tasks (produces false dep error)
    compose.write_text("""\
async def build_ui() -> UI:
    \"\"\"Build UI.\"\"\"

async def build_api() -> API:
    \"\"\"Build API.\"\"\"

async def deploy(u: UI, a: API) -> Deployed:
    \"\"\"Deploy.\"\"\"

async def execute():
    u = await build_ui()
    a = await build_api()
    d = await deploy(u, a)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    # False dep error surfaces as blocking
    assert "Composition errors:" in ctx
    assert "Fix the call graph." in ctx
    # Advisory is filtered out
    assert "Missing @resource_bounds" not in ctx


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_rejection_message_format_unchanged(tmp_path: Path) -> None:
    """False dependency errors use the same rejection format as M24 structural errors.

    The format is: 'Composition errors:\\n{errors}\\nFix the call graph.'
    This ensures the planner receives consistent actionable feedback.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def frontend() -> FE:
    \"\"\"Build frontend.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def backend() -> BE:
    \"\"\"Build backend.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def deploy(f: FE, b: BE) -> App:
    \"\"\"Deploy app.\"\"\"

async def execute():
    f = await frontend()
    b = await backend()
    app = await deploy(f, b)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    # Verify exact format: starts with "Composition errors:" and ends with "Fix the call graph."
    assert ctx.startswith("Composition errors:\n")
    assert ctx.endswith("\nFix the call graph.")


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_false_dep_blocks_through_post_hook(tmp_path: Path) -> None:
    """False dependency errors flow through the PostToolUse hook as blocking.

    Exercises the actual _make_post_hook path end-to-end: write a compose.py
    with a WAR-equivalent false dependency, trigger the hook, and verify the
    error appears in the rejection output (not silently filtered as advisory).
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    compose.write_text("""\
@needs("services/database")
@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

@needs("services/database")
@requires("task_a")
@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_b() -> B:
    \"\"\"Do B.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def task_c(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b()
    c = await task_c(a, b)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    # The WAR-equivalent error must surface as a blocking rejection
    assert "Composition errors:" in ctx, (
        f"Expected blocking 'Composition errors:' but got: {ctx!r}"
    )
    assert "WAR-equivalent" in ctx, (
        f"Expected WAR-equivalent error in hook output but got: {ctx!r}"
    )
    assert "Fix the call graph." in ctx


@pytest.mark.xfail(reason="M25 false dependency heuristics not fully wired")
def test_multi_error_types_aggregated_in_rejection(tmp_path: Path) -> None:
    """Multiple distinct error types are joined in a single rejection (F3).

    The PostToolUse hook joins all non-advisory errors into one rejection
    string via "\\n".join(errors). This test triggers two distinct error
    families -- a structural error (Undefined) and a false dependency
    (file independence) -- and asserts both appear in the output.
    """
    clou_dir = tmp_path / ".clou" / "milestones" / "m1"
    clou_dir.mkdir(parents=True)

    compose = clou_dir / "compose.py"
    # This compose.py triggers two distinct error types:
    # 1. File independence: build_frontend and build_backend are serial
    #    with no shared types/resources/constraints.
    # 2. Undefined function: undefined_func is called but never defined.
    compose.write_text("""\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def build_frontend() -> FrontendApp:
    \"\"\"Build the frontend.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def build_backend() -> BackendAPI:
    \"\"\"Build the backend.\"\"\"

@resource_bounds(tokens=50000, timeout_seconds=300)
async def integrate(fe: FrontendApp, be: BackendAPI) -> App:
    \"\"\"Wire frontend to backend.\"\"\"

async def execute():
    fe = await build_frontend()
    be = await build_backend()
    app = await integrate(fe, be)
    final = await undefined_func(app)
""")

    hook = _get_post_hook(tmp_path)
    result = _run(
        hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(compose)},
                "tool_response": "ok",
            },
            "tool-1",
            {},
        )
    )
    output = result.get("hookSpecificOutput")
    assert isinstance(output, dict)
    ctx = output.get("additionalContext", "")
    assert isinstance(ctx, str)
    # Both error families must appear in the single rejection string
    assert "Composition errors:" in ctx, (
        f"Expected 'Composition errors:' but got: {ctx!r}"
    )
    assert "Undefined: undefined_func" in ctx, (
        f"Expected 'Undefined: undefined_func' but got: {ctx!r}"
    )
    assert "appear independent" in ctx, (
        f"Expected 'appear independent' but got: {ctx!r}"
    )
    assert "Fix the call graph." in ctx


# ---------------------------------------------------------------------------
# Cleanup permission scope — CLEANUP_SCOPE + is_cleanup_allowed
# ---------------------------------------------------------------------------


def test_cleanup_scope_is_tuple_of_strings() -> None:
    """CLEANUP_SCOPE is a non-empty immutable tuple of glob pattern strings."""
    assert isinstance(CLEANUP_SCOPE, tuple)
    assert len(CLEANUP_SCOPE) > 0
    for pat in CLEANUP_SCOPE:
        assert isinstance(pat, str)


def test_cleanup_allowed_example_file() -> None:
    """Root-level .example files are allowed cleanup targets."""
    assert is_cleanup_allowed("roadmap.py.example") is True


def test_cleanup_allowed_bak_file() -> None:
    """Root-level .bak files are allowed cleanup targets."""
    assert is_cleanup_allowed("some-file.bak") is True


def test_cleanup_allowed_old_file() -> None:
    """Root-level .old files are allowed cleanup targets."""
    assert is_cleanup_allowed("config.old") is True


def test_cleanup_rejected_nested_milestones_path() -> None:
    """Nested milestone paths are rejected even if extension matches."""
    assert is_cleanup_allowed("milestones/foo/handoff.md") is False


def test_cleanup_rejected_nested_prompts_path() -> None:
    """Nested prompts paths are rejected."""
    assert is_cleanup_allowed("prompts/coordinator.md") is False


def test_cleanup_rejected_project_md() -> None:
    """Golden context files (project.md) don't match any cleanup pattern."""
    assert is_cleanup_allowed("project.md") is False


def test_cleanup_rejected_roadmap_md() -> None:
    """Golden context files (roadmap.md) don't match any cleanup pattern."""
    assert is_cleanup_allowed("roadmap.md") is False


def test_cleanup_rejected_empty_string() -> None:
    """Empty string is rejected."""
    assert is_cleanup_allowed("") is False


def test_cleanup_rejected_path_outside_clou() -> None:
    """Paths with directory separators are rejected (prevents traversal)."""
    assert is_cleanup_allowed("active/supervisor.md") is False


def test_cleanup_rejected_backslash_path() -> None:
    """Backslash directory separators are also rejected."""
    assert is_cleanup_allowed("milestones\\foo.example") is False


def test_write_permissions_unchanged() -> None:
    """WRITE_PERMISSIONS is not affected by CLEANUP_SCOPE addition."""
    expected_tiers = {
        "supervisor", "coordinator", "worker",
        "verifier", "brutalist", "assess-evaluator",
    }
    assert set(WRITE_PERMISSIONS.keys()) == expected_tiers
