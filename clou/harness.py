"""Harness template schema, loader, and validator.

A harness template is a capability profile — the set of tools, quality
gates, verification mechanisms, and write permissions available to each
agent tier.  The supervisor selects a template; the orchestrator reads
it and configures agent definitions, hooks, and MCP servers accordingly.

Public API:
    HarnessTemplate, AgentSpec, QualityGateSpec, MCPServerSpec,
    ComposeConventions — schema dataclasses
    load_template(name) -> HarnessTemplate — load by name with fallback
    validate_template(template) -> list[str] — structural validation
    read_template_name(project_dir) -> str — read from project.md
    template_mcp_servers(template) -> dict — SDK-compatible MCP config
    template_agent_tier_map(template) -> dict — agent-name-to-tier map
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("clou")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """MCP server configuration."""

    command: str
    args: list[str]
    type: str = "stdio"


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Agent definition within a harness template.

    Each agent maps to an AgentDefinition at runtime.  The ``tier``
    field links the agent to its write permission group in the template's
    ``write_permissions`` dict — required because agent names (e.g.
    "implementer") may differ from tier names (e.g. "worker").
    """

    description: str
    prompt_ref: str
    tier: str
    tools: list[str]
    model: str = "opus"


@dataclass(frozen=True, slots=True)
class QualityGateSpec:
    """Quality gate configuration.

    The gate spec references agents, not tool lists.  The agent's tool
    list is the single source of truth for available gate tools.
    """

    mcp_server: str
    assess_agent: str
    verify_agent: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ComposeConventions:
    """Constraints on compose.py structure."""

    require_verify: bool = True
    phase_comments: bool = True
    validators: list[str] = field(default_factory=lambda: ["graph.validate"])


@dataclass(frozen=True, slots=True)
class HarnessTemplate:
    """Complete harness template — a capability profile for a domain."""

    name: str
    description: str
    agents: dict[str, AgentSpec]
    quality_gates: list[QualityGateSpec]
    verification_modalities: list[str]
    mcp_servers: dict[str, MCPServerSpec]
    write_permissions: dict[str, list[str]]
    compose_conventions: ComposeConventions = field(
        default_factory=ComposeConventions,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_template(template: HarnessTemplate) -> list[str]:
    """Validate a harness template for structural correctness.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not template.name:
        errors.append("Template name is empty")

    if not template.agents:
        errors.append("Template has no agents")

    # Every agent tier must have a corresponding write_permissions entry.
    for agent_name, spec in template.agents.items():
        if spec.tier not in template.write_permissions:
            errors.append(
                f"Agent '{agent_name}' has tier '{spec.tier}' but "
                f"write_permissions has no entry for that tier"
            )

    # Quality gate agents must exist in the agents dict.
    for gate in template.quality_gates:
        if gate.assess_agent not in template.agents:
            errors.append(
                f"Quality gate references assess_agent '{gate.assess_agent}' "
                f"which is not in agents"
            )
        if gate.verify_agent not in template.agents:
            errors.append(
                f"Quality gate references verify_agent '{gate.verify_agent}' "
                f"which is not in agents"
            )
        # Gate's MCP server must be in mcp_servers.
        if gate.mcp_server not in template.mcp_servers:
            errors.append(
                f"Quality gate references mcp_server '{gate.mcp_server}' "
                f"which is not in mcp_servers"
            )

    return errors


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def load_template(name: str) -> HarnessTemplate:
    """Load a harness template by name.

    Attempts to import ``clou.harnesses.<name>`` (hyphens converted to
    underscores) and read its ``template`` attribute.  On any failure,
    falls back to the hardcoded software-construction default.

    Per DB-11 D9: template loading failures are never fatal.
    """
    if not _TEMPLATE_NAME_RE.match(name):
        log.warning(
            "Invalid template name %r. "
            "Falling back to default software-construction.",
            name,
        )
        return _default_template()

    module_name = name.replace("-", "_")

    try:
        mod = importlib.import_module(f"clou.harnesses.{module_name}")
        tmpl = mod.template
        if not isinstance(tmpl, HarnessTemplate):
            raise TypeError(
                f"clou.harnesses.{module_name}.template is "
                f"{type(tmpl).__name__}, expected HarnessTemplate"
            )
        errors = validate_template(tmpl)
        if errors:
            log.warning(
                "Template '%s' has validation errors: %s. "
                "Falling back to default.",
                name,
                errors,
            )
            return _default_template()
        return tmpl
    except Exception:
        log.warning(
            "Failed to load template '%s'. "
            "Falling back to default software-construction.",
            name,
            exc_info=True,
        )
        return _default_template()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_template_name(project_dir: Path) -> str:
    """Read the template name from project.md.

    Looks for a ``template: <name>`` line.  Returns
    ``"software-construction"`` if not found or malformed.
    """
    project_md = project_dir / ".clou" / "project.md"
    if not project_md.exists():
        return "software-construction"
    try:
        content = project_md.read_text()
    except OSError:
        return "software-construction"
    for line in content.splitlines():
        if line.startswith("template:"):
            name = line.split(":", 1)[1].strip()
            if name:
                return name
    return "software-construction"


def template_mcp_servers(template: HarnessTemplate) -> dict[str, Any]:
    """Convert template MCP server specs to SDK-compatible dicts."""
    return {
        name: {
            "command": spec.command,
            "args": spec.args,
            "type": spec.type,
        }
        for name, spec in template.mcp_servers.items()
    }


def template_agent_tier_map(template: HarnessTemplate) -> dict[str, str]:
    """Derive agent-name-to-tier mapping from template agents."""
    return {name: spec.tier for name, spec in template.agents.items()}


# ---------------------------------------------------------------------------
# Default fallback
# ---------------------------------------------------------------------------


def _default_template() -> HarnessTemplate:
    """Return the hardcoded software-construction fallback.

    This reproduces the exact configuration from orchestrator.py's
    _build_agents(), _BRUTALIST_MCP, _CDP_MCP, and hooks.py's
    WRITE_PERMISSIONS — the configuration that existed before harness
    templates were introduced.
    """
    try:
        from clou.harnesses.software_construction import template

        return template
    except Exception:
        log.error(
            "Cannot load software_construction template module. "
            "Returning inline fallback.",
            exc_info=True,
        )
        return _INLINE_FALLBACK


# Absolute last-resort fallback — only used if the software_construction
# module itself cannot be imported (package corruption).
_INLINE_FALLBACK = HarnessTemplate(
    name="software-construction",
    description="Build, test, and deploy software systems",
    agents={
        "implementer": AgentSpec(
            description=(
                "Implement code changes for assigned tasks. "
                "Read compose.py for your function signature, phase.md "
                "for context. Write results to execution.md."
            ),
            prompt_ref="worker",
            tier="worker",
            tools=[
                "Read", "Write", "Edit", "MultiEdit",
                "Bash", "Grep", "Glob",
                "WebSearch", "WebFetch",
            ],
        ),
        "assessor": AgentSpec(
            description=(
                "Invoke quality gate tools on changed code and "
                "structure findings into assessment.md. Does not "
                "evaluate findings — captures only."
            ),
            prompt_ref="assessor",
            tier="assessor",
            tools=[
                "Read", "Write", "Bash", "Grep", "Glob",
                "mcp__brutalist__roast_codebase",
                "mcp__brutalist__roast_architecture",
                "mcp__brutalist__roast_security",
                "mcp__brutalist__roast_product",
                "mcp__brutalist__roast_infrastructure",
                "mcp__brutalist__roast_file_structure",
                "mcp__brutalist__roast_dependencies",
                "mcp__brutalist__roast_test_coverage",
            ],
        ),
        "verifier": AgentSpec(
            description=(
                "Verify milestone completion by perceiving the "
                "output as a user would. Materialize the environment, "
                "walk golden paths, explore adversarially, "
                "prepare handoff.md."
            ),
            prompt_ref="verifier",
            tier="verifier",
            tools=[
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
        ),
    },
    quality_gates=[
        QualityGateSpec(
            mcp_server="brutalist",
            assess_agent="assessor",
            verify_agent="verifier",
            required=True,
        ),
    ],
    verification_modalities=["Browser", "HTTP", "Shell", "Code"],
    mcp_servers={
        "brutalist": MCPServerSpec(
            command="npx",
            args=["-y", "@brutalist/mcp@latest"],
        ),
        "cdp": MCPServerSpec(
            command="npx",
            args=["-y", "chrome-devtools-mcp@latest"],
        ),
    },
    write_permissions={
        "supervisor": [
            "project.md",
            "roadmap.md",
            "requests.md",
            "understanding.md",
            "milestones/*/milestone.md",
            "milestones/*/requirements.md",
            "milestones/*/escalations/*.md",
            "active/supervisor.md",
        ],
        "coordinator": [
            "milestones/*/compose.py",
            "milestones/*/status.md",
            "milestones/*/decisions.md",
            "milestones/*/escalations/*.md",
            "milestones/*/phases/*/phase.md",
            "active/coordinator.md",
        ],
        "worker": [
            "milestones/*/phases/*/execution.md",
        ],
        "verifier": [
            "milestones/*/phases/verification/execution.md",
            "milestones/*/phases/verification/artifacts/*",
            "milestones/*/handoff.md",
        ],
        "assessor": [
            "milestones/*/assessment.md",
        ],
    },
    compose_conventions=ComposeConventions(
        require_verify=True,
        phase_comments=True,
        validators=["graph.validate"],
    ),
)
