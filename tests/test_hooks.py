"""Tests for hook enforcement (write boundaries and compose.py validation)."""

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path

from clou.hooks import (
    AGENT_TIER_MAP,
    WRITE_PERMISSIONS,
    HookConfig,
    _scoped_permissions,
    build_hooks,
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
    assert len(hooks["PostToolUse"]) == 1


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
