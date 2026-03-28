"""Custom MCP tool definitions for the Clou orchestrator.

Public API:
    clou_spawn_coordinator(project_dir, milestone) -> str
    clou_create_milestone(project_dir, milestone, milestone_content, requirements_content) -> str
    clou_status(project_dir) -> str
    clou_init(project_dir, project_name, description) -> str
"""

from __future__ import annotations

from pathlib import Path

from clou.prompts import _BUNDLED_PROMPTS


def _write_if_missing(path: Path, content: str) -> bool:
    """Write *content* to *path* only if the file does not already exist.

    Returns True if written, False if skipped.
    """
    if path.exists():
        return False
    path.write_text(content)
    return True


async def clou_spawn_coordinator(project_dir: Path, milestone: str) -> str:
    """Spawn a coordinator session for a milestone.

    Validates the milestone directory exists, then returns a status message.
    The orchestrator intercepts the return value to actually run the coordinator.
    """
    milestone_md = project_dir / ".clou" / "milestones" / milestone / "milestone.md"
    if not milestone_md.exists():
        msg = f"Milestone file not found: {milestone_md}"
        raise ValueError(msg)
    status = project_dir / ".clou" / "milestones" / milestone / "status.md"
    return f"Coordinator for '{milestone}' requested. Read {status} for results."


async def clou_create_milestone(
    project_dir: Path,
    milestone: str,
    milestone_content: str,
    requirements_content: str,
) -> str:
    """Create a new milestone directory with milestone.md and requirements.md.

    The supervisor calls this after converging with the user — the milestone
    name and content come from the convergence dialogue.

    Raises ValueError if the milestone directory already exists.
    """
    ms_dir = project_dir / ".clou" / "milestones" / milestone
    if ms_dir.exists():
        msg = f"Milestone '{milestone}' already exists"
        raise ValueError(msg)
    ms_dir.mkdir(parents=True)
    (ms_dir / "milestone.md").write_text(milestone_content)
    (ms_dir / "requirements.md").write_text(requirements_content)
    return (
        f"Created milestone '{milestone}' with "
        f"{ms_dir / 'milestone.md'} and {ms_dir / 'requirements.md'}"
    )


async def clou_status(project_dir: Path) -> str:
    """Read current Clou status: active milestones, open escalations."""
    clou_dir = project_dir / ".clou"
    if not clou_dir.is_dir():
        return "No .clou/ directory found. Run clou_init first."

    sections: list[str] = []

    # Roadmap
    roadmap = clou_dir / "roadmap.md"
    if roadmap.exists():
        sections.append(roadmap.read_text())

    # Open escalations
    milestones_dir = clou_dir / "milestones"
    if milestones_dir.is_dir():
        escalations: list[str] = []
        for ms_dir in sorted(milestones_dir.iterdir()):
            esc_dir = ms_dir / "escalations"
            if not esc_dir.is_dir():
                continue
            for esc_file in sorted(esc_dir.iterdir()):
                if esc_file.is_file():
                    escalations.append(f"- {ms_dir.name}/{esc_file.name}")
        if escalations:
            sections.append("## Open Escalations\n" + "\n".join(escalations))

    if not sections:
        return "No status information available."

    return "\n\n".join(sections)


async def clou_init(
    project_dir: Path,
    project_name: str,
    description: str = "",
) -> str:
    """Initialize the .clou/ directory structure for a new project.

    Idempotent — safe to run on an existing .clou/ directory.  Missing
    directories and files are created; existing files are never overwritten.
    Bundled prompt files are copied to .clou/prompts/ so agents can read
    their protocols at project-relative paths.

    The supervisor calls this AFTER converging with the user — project_name
    and description come from the convergence dialogue, not from upfront
    parameters.  Description is optional for the initial scaffold; the
    supervisor writes the full project.md content separately.
    """
    clou_dir = project_dir / ".clou"

    (clou_dir / "milestones").mkdir(parents=True, exist_ok=True)
    (clou_dir / "active").mkdir(exist_ok=True)
    (clou_dir / "prompts").mkdir(exist_ok=True)

    # Copy bundled prompt files so agents can read protocols at
    # .clou/prompts/<file>.  Uses _write_if_missing so per-project
    # customizations survive re-init.
    for src in sorted(_BUNDLED_PROMPTS.iterdir()):
        if src.is_file() and src.name not in ("__init__.py",):
            _write_if_missing(clou_dir / "prompts" / src.name, src.read_text())

    # Write structural files if missing.  The supervisor will overwrite
    # project.md with full content after convergence — this just ensures
    # the file exists for the template: field.
    project_content = f"# {project_name}\n"
    if description:
        project_content += f"\n{description}\n"
    _write_if_missing(clou_dir / "project.md", project_content)
    _write_if_missing(
        clou_dir / "roadmap.md",
        "# Roadmap\n\n## Milestones\n",
    )
    _write_if_missing(
        clou_dir / "requests.md",
        "# Requests\n",
    )

    return f"Initialized .clou/ for '{project_name}'"
