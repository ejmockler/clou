"""Custom MCP tool definitions for the Clou orchestrator.

Public API:
    clou_spawn_coordinator(project_dir, milestone) -> str
    clou_status(project_dir) -> str
    clou_init(project_dir, project_name, description) -> str
"""

from __future__ import annotations

from pathlib import Path


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
    return (
        f"Coordinator for '{milestone}' requested. "
        f"Read {project_dir / '.clou' / 'milestones' / milestone / 'status.md'} for results."
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


async def clou_init(project_dir: Path, project_name: str, description: str) -> str:
    """Initialize the .clou/ directory structure for a new project.

    Idempotent — safe to run on an existing .clou/ directory.  Missing
    directories and files are created; existing files are never overwritten.
    Prompts are global (bundled with clou), not copied per-project.
    """
    clou_dir = project_dir / ".clou"

    # Create directory tree (exist_ok for idempotency).
    # Golden context lives here; prompts are global.
    (clou_dir / "milestones").mkdir(parents=True, exist_ok=True)
    (clou_dir / "active").mkdir(exist_ok=True)

    # Write structural files if missing
    _write_if_missing(
        clou_dir / "project.md",
        f"# {project_name}\n\n{description}\n",
    )
    _write_if_missing(
        clou_dir / "roadmap.md",
        "# Roadmap\n\n## Milestones\n",
    )
    _write_if_missing(
        clou_dir / "requests.md",
        "# Requests\n",
    )

    return f"Initialized .clou/ for '{project_name}'"
