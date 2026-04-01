"""Tests for harness template schema, loader, and validator."""

from __future__ import annotations

from unittest.mock import patch

from clou.harness import (
    _INLINE_FALLBACK,
    AgentSpec,
    ComposeConventions,
    HarnessTemplate,
    MCPServerSpec,
    QualityGateSpec,
    load_template,
    validate_template,
)
from clou.hooks import (
    AGENT_TIER_MAP,
    WRITE_PERMISSIONS,
)

# ---------------------------------------------------------------------------
# Schema basics
# ---------------------------------------------------------------------------


def test_agent_spec_frozen() -> None:
    spec = AgentSpec(
        description="d", prompt_ref="worker", tier="worker", tools=["Read"],
    )
    assert spec.description == "d"
    assert spec.tier == "worker"


def test_mcp_server_spec_defaults() -> None:
    spec = MCPServerSpec(command="npx", args=["-y", "pkg"])
    assert spec.type == "stdio"


def test_compose_conventions_defaults() -> None:
    cc = ComposeConventions()
    assert cc.require_verify is True
    assert cc.phase_comments is True
    assert cc.validators == ["graph.validate"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _minimal_template(**overrides: object) -> HarnessTemplate:
    """Build a minimal valid template with optional overrides."""
    defaults: dict[str, object] = {
        "name": "test",
        "description": "test template",
        "agents": {
            "worker": AgentSpec(
                description="d",
                prompt_ref="worker",
                tier="worker",
                tools=["Read"],
            ),
        },
        "quality_gates": [],
        "verification_modalities": ["Shell"],
        "mcp_servers": {},
        "write_permissions": {"worker": ["*.md"]},
    }
    defaults.update(overrides)
    return HarnessTemplate(**defaults)  # type: ignore[arg-type]


def test_validate_valid_template() -> None:
    tmpl = _minimal_template()
    assert validate_template(tmpl) == []


def test_validate_empty_name() -> None:
    tmpl = _minimal_template(name="")
    errors = validate_template(tmpl)
    assert any("name is empty" in e for e in errors)


def test_validate_no_agents() -> None:
    tmpl = _minimal_template(agents={})
    errors = validate_template(tmpl)
    assert any("no agents" in e for e in errors)


def test_validate_tier_not_in_write_permissions() -> None:
    tmpl = _minimal_template(write_permissions={})
    errors = validate_template(tmpl)
    assert any("tier 'worker'" in e and "write_permissions" in e for e in errors)


def test_validate_gate_references_missing_agent() -> None:
    tmpl = _minimal_template(
        quality_gates=[
            QualityGateSpec(
                mcp_server="gate",
                assess_agent="nonexistent",
                verify_agent="worker",
                required=True,
            ),
        ],
        mcp_servers={"gate": MCPServerSpec(command="echo", args=[])},
    )
    errors = validate_template(tmpl)
    assert any("assess_agent 'nonexistent'" in e for e in errors)


def test_validate_gate_references_missing_mcp() -> None:
    tmpl = _minimal_template(
        quality_gates=[
            QualityGateSpec(
                mcp_server="missing",
                assess_agent="worker",
                verify_agent="worker",
                required=True,
            ),
        ],
    )
    errors = validate_template(tmpl)
    assert any("mcp_server 'missing'" in e for e in errors)


# ---------------------------------------------------------------------------
# Loader — name validation
# ---------------------------------------------------------------------------


def test_load_template_invalid_chars_falls_back() -> None:
    """Names with dots, slashes, or underscores are rejected."""
    tmpl = load_template("../../os")
    assert tmpl.name == "software-construction"


def test_load_template_dots_rejected() -> None:
    tmpl = load_template("foo.bar")
    assert tmpl.name == "software-construction"


def test_load_template_empty_rejected() -> None:
    tmpl = load_template("")
    assert tmpl.name == "software-construction"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_load_template_valid() -> None:
    tmpl = load_template("software-construction")
    assert tmpl.name == "software-construction"
    assert "implementer" in tmpl.agents
    assert "brutalist" in tmpl.agents
    assert "assess-evaluator" in tmpl.agents
    assert "verifier" in tmpl.agents


def test_load_template_invalid_name_falls_back() -> None:
    tmpl = load_template("nonexistent-harness")
    assert tmpl.name == "software-construction"


def test_load_template_import_error_falls_back() -> None:
    with patch("importlib.import_module", side_effect=ImportError("boom")):
        tmpl = load_template("software-construction")
    assert tmpl.name == "software-construction"


def test_load_template_validation_failure_falls_back() -> None:
    """A module that passes import but fails validation triggers fallback."""
    bad_template = HarnessTemplate(
        name="bad",
        description="bad",
        agents={
            "x": AgentSpec(
                description="d",
                prompt_ref="worker",
                tier="missing_tier",
                tools=["Read"],
            ),
        },
        quality_gates=[],
        verification_modalities=[],
        mcp_servers={},
        write_permissions={},
    )

    class FakeModule:
        template = bad_template

    with patch("importlib.import_module", return_value=FakeModule()):
        tmpl = load_template("bad-template")
    assert tmpl.name == "software-construction"


# ---------------------------------------------------------------------------
# Software template matches current hardcoded behavior
# ---------------------------------------------------------------------------


def test_software_template_tools_match_orchestrator() -> None:
    """Template agent tools match orchestrator._build_agents() exactly."""
    from clou.harnesses.software_construction import template

    # These are the exact tool lists from the template.
    expected_tools = {
        "implementer": [
            "Read", "Write", "Edit", "MultiEdit",
            "Bash", "Grep", "Glob",
            "WebSearch", "WebFetch",
        ],
        "brutalist": [
            "Read", "Write", "Grep", "Glob",
            "mcp__brutalist__roast_codebase",
            "mcp__brutalist__roast_architecture",
            "mcp__brutalist__roast_security",
            "mcp__brutalist__roast_product",
            "mcp__brutalist__roast_infrastructure",
            "mcp__brutalist__roast_file_structure",
            "mcp__brutalist__roast_dependencies",
            "mcp__brutalist__roast_test_coverage",
            "mcp__brutalist__roast_cli_debate",
            "mcp__brutalist__brutalist_discover",
        ],
        "assess-evaluator": [
            "Read", "Write", "Grep", "Glob",
        ],
        "verifier": [
            "Read", "Write", "Bash", "Grep", "Glob",
            "WebSearch", "WebFetch",
            "mcp__cdp__navigate",
            "mcp__cdp__screenshot",
            "mcp__cdp__accessibility_snapshot",
            "mcp__cdp__evaluate_javascript",
            "mcp__cdp__click",
            "mcp__cdp__type",
            "mcp__cdp__network_get_response_body",
            "mcp__cdp__console_messages",
        ],
    }

    for agent_name, expected in expected_tools.items():
        assert template.agents[agent_name].tools == expected, (
            f"{agent_name} tools mismatch"
        )


def test_software_template_models_match_orchestrator() -> None:
    from clou.harnesses.software_construction import template

    for agent_name in ("implementer", "brutalist", "assess-evaluator", "verifier"):
        assert template.agents[agent_name].model == "opus", (
            f"{agent_name} model mismatch"
        )


def test_software_template_write_permissions_match_hooks() -> None:
    """Template write_permissions match hooks.py:WRITE_PERMISSIONS exactly."""
    from clou.harnesses.software_construction import template

    assert template.write_permissions == WRITE_PERMISSIONS


def test_software_template_tier_map_matches_hooks() -> None:
    """Template-derived tier map matches hooks.py:AGENT_TIER_MAP."""
    from clou.harnesses.software_construction import template

    derived = {name: spec.tier for name, spec in template.agents.items()}
    assert derived == AGENT_TIER_MAP


def test_software_template_mcp_servers() -> None:
    from clou.harnesses.software_construction import template

    assert "brutalist" in template.mcp_servers
    b = template.mcp_servers["brutalist"]
    assert b.command == "npx"
    assert b.args == ["-y", "@brutalist/mcp@latest"]

    assert "cdp" in template.mcp_servers
    c = template.mcp_servers["cdp"]
    assert c.command == "npx"
    assert c.args == ["-y", "chrome-devtools-mcp@latest"]


def test_software_template_validates() -> None:
    from clou.harnesses.software_construction import template

    assert validate_template(template) == []


# ---------------------------------------------------------------------------
# Inline fallback matches template module
# ---------------------------------------------------------------------------


def test_inline_fallback_matches_template_module() -> None:
    """_INLINE_FALLBACK must match software_construction.template exactly.

    This prevents drift between the last-resort fallback and the template
    module (DB-11 D9 defense-in-depth).
    """
    from clou.harnesses.software_construction import template

    assert template == _INLINE_FALLBACK


def test_inline_fallback_validates() -> None:
    assert validate_template(_INLINE_FALLBACK) == []
