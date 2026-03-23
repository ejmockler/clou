"""Prompt loading and construction for Clou's orchestrator.

Public API:
    load_prompt(tier, project_dir, **kwargs) -> str
    build_cycle_prompt(project_dir, milestone, cycle_type, read_set, ...) -> str
"""

from __future__ import annotations

from pathlib import Path

#: Bundled prompt templates shipped with the package — global, not per-project.
_BUNDLED_PROMPTS = Path(__file__).parent / "_prompts"


def load_prompt(tier: str, project_dir: Path, **kwargs: str) -> str:
    """Load and parameterize a tier's system prompt (identity + invariants only).

    Prompts are global — loaded from the bundled ``_prompts/`` directory,
    not from the project's ``.clou/prompts/``.  The full protocol is in
    separate files the agent reads during execution.
    """
    prompt_path = _BUNDLED_PROMPTS / f"{tier}-system.xml"
    prompt = prompt_path.read_text()

    # Inject instance-specific context ({{milestone}}, {{phase}}, etc.)
    for key, value in kwargs.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    return prompt


def build_cycle_prompt(
    project_dir: Path,
    milestone: str,
    cycle_type: str,
    read_set: list[str],
    validation_errors: list[str] | None = None,
) -> str:
    """Construct targeted prompt for a single cycle.

    The system_prompt contains identity + invariants only (~800-1,200 tokens).
    This initial query provides: cycle type, protocol file pointer, and
    golden context file pointers. The coordinator reads the protocol file
    as its first action, then the golden context files.
    """
    milestone_prefix = f".clou/milestones/{milestone}"
    root_prefixes = ("project.md", "active/")
    file_list = "\n".join(
        f"- .clou/{f}" if f.startswith(root_prefixes) else f"- {milestone_prefix}/{f}"
        for f in read_set
    )
    protocol_file = str(_BUNDLED_PROMPTS / f"coordinator-{cycle_type.lower()}.md")

    prompt = (
        f"This cycle: {cycle_type}.\n\n"
        f"Read your protocol file first:\n- {protocol_file}\n\n"
        f"Then read these golden context files:\n{file_list}\n\n"
        f"Execute the {cycle_type} protocol. "
        f"Write all state to golden context before exiting."
    )

    if validation_errors:
        error_list = "\n".join(f"  - {e}" for e in validation_errors)
        prompt += (
            f"\n\nWARNING: Previous cycle produced malformed golden context. "
            f"Specific errors:\n{error_list}\n"
            f"Ensure all golden context writes conform to schema."
        )

    return prompt
