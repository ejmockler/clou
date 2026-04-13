"""Tests for harness template wiring into orchestrator, hooks, and prompts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from clou.harness import (
    read_template_name,
    template_agent_tier_map,
    template_mcp_servers,
)
from clou.harnesses.software_construction import (
    template as software_template,
)
from clou.hooks import (
    AGENT_TIER_MAP,
    _scoped_permissions,
    build_hooks,
)
from clou.prompts import build_cycle_prompt

# ---------------------------------------------------------------------------
# read_template_name
# ---------------------------------------------------------------------------


def test_read_template_name_from_project_md(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "project.md").write_text(
        "# My Project\n\ntemplate: software-construction\n\n## Description\n"
    )
    assert read_template_name(tmp_path) == "software-construction"


def test_read_template_name_custom(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "project.md").write_text(
        "# My Project\n\ntemplate: custom-harness\n"
    )
    assert read_template_name(tmp_path) == "custom-harness"


def test_read_template_name_missing_file(tmp_path: Path) -> None:
    assert read_template_name(tmp_path) == "software-construction"


def test_read_template_name_missing_field(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "project.md").write_text("# My Project\n\n## Description\n")
    assert read_template_name(tmp_path) == "software-construction"


def test_read_template_name_empty_value(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "project.md").write_text("# My Project\n\ntemplate:\n")
    assert read_template_name(tmp_path) == "software-construction"


# ---------------------------------------------------------------------------
# template_mcp_servers
# ---------------------------------------------------------------------------


def test_template_mcp_servers_format() -> None:
    result = template_mcp_servers(software_template)
    assert "brutalist" in result
    assert result["brutalist"] == {
        "command": "npx",
        "args": ["-y", "@brutalist/mcp@latest"],
        "type": "stdio",
    }
    assert "cdp" in result
    assert result["cdp"] == {
        "command": "npx",
        "args": ["-y", "chrome-devtools-mcp@latest"],
        "type": "stdio",
    }


def test_supervisor_gets_only_quality_gate_servers() -> None:
    """Supervisor should get quality gate MCP servers, not all of them."""
    all_mcp = template_mcp_servers(software_template)
    gate_servers = {g.mcp_server for g in software_template.quality_gates}
    supervisor_mcp = {
        name: spec
        for name, spec in all_mcp.items()
        if name in gate_servers
    }
    # Should include brutalist (quality gate) but NOT cdp (verifier tool)
    assert "brutalist" in supervisor_mcp
    assert "cdp" not in supervisor_mcp


# ---------------------------------------------------------------------------
# template_agent_tier_map
# ---------------------------------------------------------------------------


def test_template_agent_tier_map_matches_hooks() -> None:
    assert template_agent_tier_map(software_template) == AGENT_TIER_MAP


# ---------------------------------------------------------------------------
# build_hooks with template
# ---------------------------------------------------------------------------


def _run(coro: object) -> dict[str, object]:
    result: object = asyncio.run(coro)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result


def test_build_hooks_with_template_allows_same_paths() -> None:
    """Template-driven hooks allow the same paths as default hooks."""
    project_dir = Path("/tmp/test-project")

    default_hooks = build_hooks(
        "coordinator", project_dir, milestone="ms1",
    )
    template_hooks = build_hooks(
        "coordinator", project_dir, milestone="ms1",
        template=software_template,
    )

    # Both should have PreToolUse and PostToolUse
    assert set(default_hooks.keys()) == set(template_hooks.keys())


def test_build_hooks_template_enforces_permissions() -> None:
    """Template-driven hooks enforce the template's write permissions."""
    project_dir = Path("/tmp/test-project")
    hooks = build_hooks(
        "coordinator", project_dir, milestone="ms1",
        template=software_template,
    )
    pre_hooks = hooks["PreToolUse"]
    assert len(pre_hooks) == 1

    # Write to coordinator-owned narrative path should be allowed
    result = _run(
        pre_hooks[0].hooks[0](
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(
                        project_dir / ".clou" / "milestones" / "ms1"
                        / "decisions.md"
                    ),
                },
            },
            None,
            {},
        )
    )
    hso = result.get("hookSpecificOutput", {})
    assert not (isinstance(hso, dict) and hso.get("permissionDecision") == "deny")


def test_build_hooks_template_supervisor_allows_understanding_md() -> None:
    """Template-driven hooks allow supervisor to write understanding.md.

    This tests the production code path: build_hooks with template= uses
    template.write_permissions (not the module-level WRITE_PERMISSIONS).
    """
    project_dir = Path("/tmp/test-project")
    hooks = build_hooks(
        "supervisor", project_dir,
        template=software_template,
    )
    pre_hooks = hooks["PreToolUse"]
    assert len(pre_hooks) == 1

    result = _run(
        pre_hooks[0].hooks[0](
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(
                        project_dir / ".clou" / "understanding.md"
                    ),
                },
            },
            None,
            {},
        )
    )
    hso = result.get("hookSpecificOutput", {})
    assert not (isinstance(hso, dict) and hso.get("permissionDecision") == "deny")


def test_build_hooks_template_worker_blocked_from_understanding_md() -> None:
    """Template-driven hooks block worker from writing understanding.md."""
    project_dir = Path("/tmp/test-project")
    hooks = build_hooks(
        "worker", project_dir,
        template=software_template,
    )
    pre_hooks = hooks["PreToolUse"]

    result = _run(
        pre_hooks[0].hooks[0](
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(
                        project_dir / ".clou" / "understanding.md"
                    ),
                },
            },
            None,
            {},
        )
    )
    hso = result.get("hookSpecificOutput", {})
    assert isinstance(hso, dict) and hso.get("permissionDecision") == "deny"


def test_build_hooks_template_blocks_unauthorized() -> None:
    """Template-driven hooks block unauthorized writes."""
    project_dir = Path("/tmp/test-project")
    hooks = build_hooks(
        "coordinator", project_dir, milestone="ms1",
        template=software_template,
    )
    pre_hooks = hooks["PreToolUse"]

    # Coordinator writing to project.md (supervisor-only) should be blocked
    result = _run(
        pre_hooks[0].hooks[0](
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(
                        project_dir / ".clou" / "project.md"
                    ),
                },
            },
            None,
            {},
        )
    )
    hso = result.get("hookSpecificOutput", {})
    assert isinstance(hso, dict) and hso.get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# _scoped_permissions with custom permissions dict
# ---------------------------------------------------------------------------


def test_scoped_permissions_with_custom_dict() -> None:
    custom = {"custom_tier": ["data/*.json", "milestones/*/output.md"]}
    result = _scoped_permissions("custom_tier", "ms1", custom)
    assert "milestones/ms1/output.md" in result
    assert "data/*.json" in result


def test_scoped_permissions_default_fallback() -> None:
    """Without custom dict, falls back to WRITE_PERMISSIONS."""
    result = _scoped_permissions("worker", "ms1")
    assert "milestones/ms1/phases/*/execution.md" in result


# ---------------------------------------------------------------------------
# build_cycle_prompt with template
# ---------------------------------------------------------------------------


def test_cycle_prompt_includes_harness_name() -> None:
    prompt = build_cycle_prompt(
        Path("/tmp"),
        "ms1",
        "EXECUTE",
        ["status.md"],
        template=software_template,
    )
    assert "software-construction" in prompt


def test_cycle_prompt_includes_quality_gates_for_assess() -> None:
    prompt = build_cycle_prompt(
        Path("/tmp"),
        "ms1",
        "ASSESS",
        ["status.md"],
        template=software_template,
    )
    assert "brutalist" in prompt
    assert "Quality gates:" in prompt


def test_cycle_prompt_includes_quality_gates_for_verify() -> None:
    prompt = build_cycle_prompt(
        Path("/tmp"),
        "ms1",
        "VERIFY",
        ["status.md"],
        template=software_template,
    )
    assert "Quality gates:" in prompt


def test_cycle_prompt_no_gates_for_execute() -> None:
    prompt = build_cycle_prompt(
        Path("/tmp"),
        "ms1",
        "EXECUTE",
        ["status.md"],
        template=software_template,
    )
    assert "Quality gates:" not in prompt


def test_cycle_prompt_without_template_no_harness_line() -> None:
    prompt = build_cycle_prompt(
        Path("/tmp"),
        "ms1",
        "ASSESS",
        ["status.md"],
    )
    assert "Active harness:" not in prompt


# ---------------------------------------------------------------------------
# Convergence detection with "Quality Gate" headers
# ---------------------------------------------------------------------------


def test_convergence_recognizes_quality_gate_header() -> None:
    from clou.recovery import assess_convergence

    decisions = (
        "## Cycle 3 — Quality Gate Assessment\n\n"
        "### Noise: some finding\n"
        "**Classification:** noise\n"
        "**Action:** Dismissed — no changes\n"
        "**Reasoning:** not needed\n\n"
        "## Cycle 2 — Quality Gate Assessment\n\n"
        "### Noise: another finding\n"
        "**Classification:** noise\n"
        "**Action:** Dismissed — no changes\n"
        "**Reasoning:** not needed\n\n"
        "## Cycle 1 — Quality Gate Assessment\n\n"
        "### Valid: first finding\n"
        "**Classification:** valid\n"
        "**Action:** fix it\n"
        "**Reasoning:** needed\n"
    )
    result = assess_convergence(None, decisions_content=decisions, threshold=2)
    assert result.total_assess_cycles == 3
    assert result.consecutive_zero_accepts == 2
    assert result.converged is True


def test_convergence_still_recognizes_brutalist_header() -> None:
    """Backward compatibility with existing decisions.md files."""
    from clou.recovery import assess_convergence

    decisions = (
        "## Cycle 2 — Brutalist Assessment\n\n"
        "### Noise: x\n"
        "**Classification:** noise\n"
        "**Action:** Dismissed — no changes\n"
        "**Reasoning:** z\n\n"
        "## Cycle 1 — Brutalist Assessment\n\n"
        "### Valid: first\n"
        "**Classification:** valid\n"
        "**Action:** fix\n"
        "**Reasoning:** b\n"
    )
    result = assess_convergence(None, decisions_content=decisions, threshold=1)
    assert result.total_assess_cycles == 2
    assert result.consecutive_zero_accepts == 1
    assert result.converged is True


# ---------------------------------------------------------------------------
# Validation accepts Quality Gate headers
# ---------------------------------------------------------------------------


def test_validation_accepts_quality_gate_cycle_header(
    tmp_path: Path,
) -> None:
    from clou.validation import _validate_decisions

    decisions_path = tmp_path / "decisions.md"
    decisions_path.write_text(
        "# Decisions\n\n"
        "## Cycle 1 — Quality Gate Assessment\n\n"
    )
    errors = _validate_decisions(decisions_path)
    # Zero-finding quality gate cycle is valid (convergence)
    assert not any("no " in e and "entry" in e for e in errors)
