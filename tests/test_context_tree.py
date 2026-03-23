"""Tests for _is_recent and _guess_milestone_status in clou.ui.widgets.context_tree."""

from __future__ import annotations

import os
from pathlib import Path

from clou.ui.widgets.context_tree import (
    ContextTreeWidget,
    _RECENT_THRESHOLD,
    _guess_milestone_status,
    _is_recent,
)


def test_recent_file_returns_true(tmp_path: Path) -> None:
    """A file modified 60s ago should be considered recent."""
    f = tmp_path / "recent.txt"
    f.write_text("x")
    now = 1_000_000.0
    os.utime(f, (now - 60, now - 60))
    assert _is_recent(f, now) is True


def test_stale_file_returns_false(tmp_path: Path) -> None:
    """A file modified 600s ago should NOT be considered recent."""
    f = tmp_path / "stale.txt"
    f.write_text("x")
    now = 1_000_000.0
    os.utime(f, (now - 600, now - 600))
    assert _is_recent(f, now) is False


def test_nonexistent_path_returns_false(tmp_path: Path) -> None:
    """A path that doesn't exist should return False (OSError)."""
    missing = tmp_path / "no_such_file.txt"
    assert _is_recent(missing, 1_000_000.0) is False


def test_exactly_at_threshold_returns_false(tmp_path: Path) -> None:
    """A file whose age equals _RECENT_THRESHOLD is NOT recent (strict <)."""
    f = tmp_path / "boundary.txt"
    f.write_text("x")
    now = 1_000_000.0
    os.utime(f, (now - _RECENT_THRESHOLD, now - _RECENT_THRESHOLD))
    assert _is_recent(f, now) is False


def test_just_inside_threshold_returns_true(tmp_path: Path) -> None:
    """A file 1s inside the threshold should be recent."""
    f = tmp_path / "almost.txt"
    f.write_text("x")
    now = 1_000_000.0
    os.utime(f, (now - _RECENT_THRESHOLD + 1, now - _RECENT_THRESHOLD + 1))
    assert _is_recent(f, now) is True


# --- _guess_milestone_status tests ---


def test_milestone_no_state_files_returns_none(tmp_path: Path) -> None:
    assert _guess_milestone_status(tmp_path) is None


def test_milestone_state_md_with_matching_keyword(tmp_path: Path) -> None:
    (tmp_path / "state.md").write_text("Status: Completed\n", encoding="utf-8")
    assert _guess_milestone_status(tmp_path) == "completed"


def test_milestone_state_md_no_matching_keyword(tmp_path: Path) -> None:
    (tmp_path / "state.md").write_text("Status: pending\n", encoding="utf-8")
    assert _guess_milestone_status(tmp_path) is None


def test_milestone_fallback_to_phase_state_md(tmp_path: Path) -> None:
    (tmp_path / "phase_state.md").write_text("This milestone is active.\n", encoding="utf-8")
    assert _guess_milestone_status(tmp_path) == "active"


def test_milestone_oserror_on_read_returns_none(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "state.md"
    state_file.write_text("completed", encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert _guess_milestone_status(tmp_path) is None


# --- _build_tree OSError tests ---


def test_build_tree_skips_unreadable_directory(tmp_path: Path, monkeypatch) -> None:
    """_build_tree catches OSError from iterdir and skips the directory."""
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    readable = clou_dir / "milestone-ok"
    readable.mkdir()
    (readable / "compose.py").write_text("# ok")
    unreadable = clou_dir / "milestone-bad"
    unreadable.mkdir()

    original_iterdir = Path.iterdir

    def _patched_iterdir(self):
        if self == unreadable:
            raise OSError("Permission denied")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _patched_iterdir)

    w = ContextTreeWidget()
    # Should not raise — the OSError from the unreadable dir is caught.
    w.refresh_tree(clou_dir)

    # The readable milestone's content should still appear in the tree.
    all_data: list[str] = []

    def _collect(node: object) -> None:
        all_data.append(str(getattr(node, "data", "")))
        for child in getattr(node, "children", []):
            _collect(child)

    _collect(w.root)
    assert any("milestone-ok" in d for d in all_data)
