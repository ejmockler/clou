"""Project root discovery for Clou.

Walks up the directory tree from cwd (or a given start) looking for the
``.clou/`` marker directory — analogous to how ``git`` finds ``.git/``.
"""

from __future__ import annotations

import sys
from pathlib import Path


class ProjectNotFoundError(Exception):
    """No ``.clou/`` directory found in any ancestor."""


def find_project_root(start: Path | None = None) -> Path | None:
    """Locate the nearest ancestor containing a ``.clou/`` directory.

    Walks up from *start* (default: cwd) to the filesystem root.
    Returns the first directory that contains a ``.clou/`` child,
    or ``None`` if not found.
    """
    current = (start or Path.cwd()).resolve()
    for ancestor in (current, *current.parents):
        if (ancestor / ".clou").is_dir():
            return ancestor
    return None


def resolve_project_dir(argv: list[str] | None = None) -> Path:
    """Resolve project directory from CLI args or directory walking.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).
            Accepts an optional first positional argument as an explicit
            project path.

    Returns:
        Path to the project root (directory containing ``.clou/``).

    Raises:
        ProjectNotFoundError: If no ``.clou/`` directory is found.
    """
    args = argv if argv is not None else sys.argv[1:]

    if args and not args[0].startswith("-"):
        explicit = Path(args[0]).resolve()
        if not explicit.is_dir():
            print(
                f"Warning: '{args[0]}' is not a directory, ignoring.",
                file=sys.stderr,
            )
        elif not (explicit / ".clou").is_dir():
            print(
                f"Warning: '{explicit}' has no .clou/ directory, ignoring.",
                file=sys.stderr,
            )
        else:
            return explicit

    root = find_project_root()
    if root is None:
        raise ProjectNotFoundError(
            "No .clou/ directory found in current directory or any parent."
        )
    return root


def global_project_dir() -> Path:
    """Return the global fallback workspace directory (``~/.config/clou/``)."""
    return Path.home() / ".config" / "clou"


def _ensure_global_workspace(workspace: Path) -> None:
    """Initialize the global workspace synchronously.

    ``clou_init`` is async but performs zero async I/O, so we replicate
    the essential scaffolding here to avoid ``asyncio.run()`` which would
    crash when an event loop is already running (e.g. orchestrator entry).
    Prompts are global (bundled with clou), not copied per-workspace.
    """
    clou_dir = workspace / ".clou"
    (clou_dir / "milestones").mkdir(parents=True, exist_ok=True)
    (clou_dir / "active").mkdir(exist_ok=True)

    project_md = clou_dir / "project.md"
    if not project_md.exists():
        project_md.write_text("# global\n\n")

    roadmap = clou_dir / "roadmap.md"
    if not roadmap.exists():
        roadmap.write_text("# Roadmap\n\n## Milestones\n")

    requests = clou_dir / "requests.md"
    if not requests.exists():
        requests.write_text("# Requests\n")


def resolve_project_dir_or_exit(argv: list[str] | None = None) -> Path:
    """CLI wrapper around :func:`resolve_project_dir` with global fallback.

    If no local ``.clou/`` project is found, falls back to
    ``~/.config/clou/`` as an isolated global workspace so ``clou``
    can run from any directory.
    """
    try:
        return resolve_project_dir(argv)
    except ProjectNotFoundError:
        workspace = global_project_dir()
        if not (workspace / ".clou").is_dir():
            _ensure_global_workspace(workspace)
        return workspace
