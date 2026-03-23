"""Golden context tree — .clou/ directory viewer.

Displays the ``.clou/`` directory structure as a Textual Tree with
semantic colour coding for milestones, phases, escalations, and files.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape as _escape_markup
from textual.widgets import Tree

if TYPE_CHECKING:
    from textual.widgets._tree import TreeNode

from clou.ui.theme import PALETTE

_log = logging.getLogger(__name__)

# Semantic hex colors.
_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_GREEN_DIM_HEX = PALETTE["accent-green"].dim().to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_BLUE_HEX = PALETTE["accent-blue"].to_hex()
_TEXT_DIM_HEX = PALETTE["text-dim"].to_hex()
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_TEXT_MUTED_HEX = PALETTE["text-muted"].to_hex()

#: Recently modified threshold (seconds).
_RECENT_THRESHOLD = 300  # 5 minutes

#: Milestone status keywords in state files.
_STATUS_KEYWORDS: dict[str, str] = {
    "completed": _GREEN_DIM_HEX,
    "incomplete": _TEAL_HEX,
    "active": _TEAL_HEX,
    "complete": _GREEN_DIM_HEX,
    "failed": _ROSE_HEX,
    "error": _ROSE_HEX,
}

#: Key files that get special styling.
_KEY_FILES = frozenset({"compose.py", "handoff.md", "decisions.md"})


def _is_recent(path: Path, now: float) -> bool:
    """Check if a file was modified within the recent threshold."""
    try:
        mtime = path.stat().st_mtime
        return (now - mtime) < _RECENT_THRESHOLD
    except OSError:
        return False


def _guess_milestone_status(milestone_dir: Path) -> str | None:
    """Try to determine milestone status from state files."""
    state_file = milestone_dir / "state.md"
    if not state_file.exists():
        state_file = milestone_dir / "phase_state.md"
    if not state_file.exists():
        return None

    try:
        content = state_file.read_text(encoding="utf-8").lower()
        for keyword in _STATUS_KEYWORDS:
            if re.search(r'\b' + keyword + r'\b', content):
                return keyword
    except OSError:
        _log.debug("Could not read state file %s", state_file, exc_info=True)
    return None


class ContextTreeWidget(Tree[str]):
    """Displays the ``.clou/`` directory structure with semantic styling.

    Extends Textual's Tree widget. Colour codes milestones by status,
    highlights recently modified files, and marks key files.
    """

    DEFAULT_CSS = f"""
    ContextTreeWidget {{
        background: {PALETTE["surface-deep"].to_hex()};
        color: {PALETTE["text"].to_hex()};
        padding: 1 2;
    }}
    """

    def __init__(
        self,
        clou_dir: Path | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        label = ".clou/"
        super().__init__(label, name=name, id=id, classes=classes, data="root")
        self._clou_dir = clou_dir

    def on_mount(self) -> None:
        """Build tree on mount."""
        if self._clou_dir is not None:
            self.refresh_tree(self._clou_dir)

    def refresh_tree(self, clou_dir: Path) -> None:
        """Rebuild the tree from the filesystem.

        Handles missing ``.clou/`` gracefully by showing a placeholder.
        """
        self._clou_dir = clou_dir
        self.clear()

        if not clou_dir.exists():
            self.root.add_leaf(
                f"[{_TEXT_MUTED_HEX}]No .clou/ directory found[/]",
                data="missing",
            )
            return

        now = time.time()
        self._build_tree(self.root, clou_dir, now, depth=0, max_depth=8)
        self.root.expand()

    def _build_tree(
        self,
        parent: TreeNode[str],
        directory: Path,
        now: float,
        depth: int,
        max_depth: int = 8,
    ) -> None:
        """Recursively build tree nodes from a directory."""
        if depth >= max_depth:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            _log.debug("Could not list directory %s", directory, exc_info=True)
            return

        for entry in entries:
            if entry.name.startswith("__"):
                continue
            if entry.is_symlink():
                continue

            if entry.is_dir():
                self._add_directory_node(parent, entry, now, depth, max_depth)
            else:
                self._add_file_node(parent, entry, now)

    def _add_directory_node(
        self,
        parent: TreeNode[str],
        directory: Path,
        now: float,
        depth: int,
        max_depth: int = 8,
    ) -> None:
        """Add a directory node with status-aware styling."""
        dir_name = directory.name

        # Check if this looks like a milestone directory.
        status = _guess_milestone_status(directory)
        if status and status in _STATUS_KEYWORDS:
            color = _STATUS_KEYWORDS[status]
            icon = {
                "active": "\u25c9",
                "complete": "\u2713",
                "completed": "\u2713",
                "failed": "\u2717",
                "error": "\u2717",
                "incomplete": "\u25cb",
            }.get(status, "\u25cb")
            label = f"[{color}]{icon} {_escape_markup(dir_name)}/[/]"
        else:
            label = f"[{_BLUE_HEX}]{_escape_markup(dir_name)}/[/]"

        node = parent.add(label, data=str(directory))
        self._build_tree(node, directory, now, depth + 1, max_depth=max_depth)

    def _add_file_node(
        self,
        parent: TreeNode[str],
        file_path: Path,
        now: float,
    ) -> None:
        """Add a file node with modification-aware styling."""
        file_name = file_path.name

        if _is_recent(file_path, now):
            color = _GOLD_HEX
            marker = " \u2022"  # bullet for recent
        elif file_name in _KEY_FILES:
            color = _TEXT_DIM_HEX
            marker = ""
        else:
            color = _TEXT_DIM_HEX
            marker = ""

        label = f"[{color}]{_escape_markup(file_name)}{marker}[/]"
        parent.add_leaf(label, data=str(file_path))
