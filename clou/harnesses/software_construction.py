"""Software construction harness template.

Configures Clou for building, testing, and deploying software systems.
This is the default template — extracted from the original hardcoded
configuration in orchestrator.py.

The values here must match the current runtime behavior exactly.
Any divergence means the template extraction introduced a regression.
"""

from clou.harness import (
    AgentSpec,
    ArtifactForm,
    ComposeConventions,
    HarnessTemplate,
    MCPServerSpec,
    QualityGateSpec,
)

template = HarnessTemplate(
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
                "Read",
                "Write",
                "Edit",
                "MultiEdit",
                "Bash",
                "Grep",
                "Glob",
                "WebSearch",
                "WebFetch",
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
                "Read",
                "Write",
                "Bash",
                "Grep",
                "Glob",
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
                "Read",
                "Write",
                "Bash",
                "Grep",
                "Glob",
                "WebSearch",
                "WebFetch",
                # CDP browser verification tools
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
            "milestones/*/intents.md",
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
            "milestones/*/active/coordinator.md",
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
    artifact_forms={
        "intents": ArtifactForm(
            criterion_template="When {trigger}, {observable_outcome}",
            anti_patterns=(
                "file paths or module names as criterion subject",
                "implementation verbs (extract, refactor, build) as criterion",
                "criteria verifiable by file inspection alone",
            ),
        ),
    },
)
