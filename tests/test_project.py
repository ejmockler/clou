"""Tests for project root discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.project import (
    ProjectNotFoundError,
    find_project_root,
    global_project_dir,
    resolve_project_dir,
    resolve_project_dir_or_exit,
)


# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------


class TestFindProjectRoot:
    """find_project_root walks up looking for .clou/."""

    def test_finds_clou_in_cwd(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        assert find_project_root(tmp_path) == tmp_path

    def test_finds_clou_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        assert find_project_root(child) == tmp_path

    def test_finds_clou_in_grandparent(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert find_project_root(deep) == tmp_path

    def test_returns_none_when_no_clou(self, tmp_path: Path) -> None:
        """No .clou/ anywhere — returns None."""
        child = tmp_path / "empty"
        child.mkdir()
        assert find_project_root(child) is None

    def test_prefers_nearest_ancestor(self, tmp_path: Path) -> None:
        """If .clou/ exists at multiple levels, pick the nearest."""
        (tmp_path / ".clou").mkdir()
        nested = tmp_path / "workspace"
        nested.mkdir()
        (nested / ".clou").mkdir()
        assert find_project_root(nested) == nested

    def test_defaults_to_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """With no argument, uses cwd."""
        (tmp_path / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))
        assert find_project_root() == tmp_path


# ---------------------------------------------------------------------------
# resolve_project_dir
# ---------------------------------------------------------------------------


class TestResolveProjectDir:
    """resolve_project_dir: CLI arg handling + walk-up discovery."""

    def test_explicit_valid_project(self, tmp_path: Path) -> None:
        """Explicit path with .clou/ is accepted."""
        (tmp_path / ".clou").mkdir()
        result = resolve_project_dir([str(tmp_path)])
        assert result == tmp_path

    def test_explicit_dir_without_clou_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit dir without .clou/ prints warning, falls through to walk."""
        # Create a project root for walk-up to find.
        project = tmp_path / "project"
        project.mkdir()
        (project / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: project))

        # Pass a dir that exists but has no .clou/.
        no_clou = tmp_path / "other"
        no_clou.mkdir()

        result = resolve_project_dir([str(no_clou)])
        assert result == project  # fell through to walk-up
        assert "no .clou/ directory" in capsys.readouterr().err.lower()

    def test_explicit_nonexistent_path_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-directory explicit path prints warning, falls through."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: project))

        result = resolve_project_dir([str(tmp_path / "nonexistent")])
        assert result == project
        assert "not a directory" in capsys.readouterr().err.lower()

    def test_explicit_file_path_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """File (not dir) as explicit path prints warning, falls through."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: project))

        a_file = tmp_path / "somefile.txt"
        a_file.write_text("hi")

        result = resolve_project_dir([str(a_file)])
        assert result == project
        assert "not a directory" in capsys.readouterr().err.lower()

    def test_flag_argument_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Arguments starting with - are not treated as paths."""
        (tmp_path / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))
        result = resolve_project_dir(["--verbose"])
        assert result == tmp_path

    def test_walk_up_from_subdirectory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No CLI arg — discovers project root by walking up from cwd."""
        (tmp_path / ".clou").mkdir()
        deep = tmp_path / "src" / "pkg"
        deep.mkdir(parents=True)
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: deep))
        result = resolve_project_dir([])
        assert result == tmp_path

    def test_no_project_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Raises ProjectNotFoundError when no .clou/ exists anywhere."""
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: empty))
        with pytest.raises(ProjectNotFoundError):
            resolve_project_dir([])

    def test_empty_argv_uses_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty argv list triggers walk-up discovery."""
        (tmp_path / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))
        assert resolve_project_dir([]) == tmp_path

    def test_defaults_to_sys_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When argv=None, reads from sys.argv."""
        (tmp_path / ".clou").mkdir()
        monkeypatch.setattr("sys.argv", ["clou", str(tmp_path)])
        result = resolve_project_dir()
        assert result == tmp_path


# ---------------------------------------------------------------------------
# resolve_project_dir_or_exit — global fallback
# ---------------------------------------------------------------------------


class TestResolveProjectDirOrExit:
    """resolve_project_dir_or_exit falls back to ~/.clou/ when no local project."""

    def test_returns_local_project_when_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a local .clou/ exists, returns it (no fallback)."""
        (tmp_path / ".clou").mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))
        result = resolve_project_dir_or_exit([])
        assert result == tmp_path

    def test_falls_back_to_global_workspace_when_no_local_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no local .clou/ exists, falls back to ~/.config/clou/."""
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: empty))
        # Point home to a temp dir so we don't touch real home.
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        result = resolve_project_dir_or_exit([])
        workspace = fake_home / ".config" / "clou"
        assert result == workspace
        # Should have auto-initialized .clou/ in the workspace.
        assert (workspace / ".clou").is_dir()
        assert (workspace / ".clou" / "project.md").is_file()
        assert (workspace / ".clou" / "milestones").is_dir()

    def test_fallback_reuses_existing_global_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ~/.config/clou/.clou/ already exists, reuses without re-init."""
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: empty))
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        workspace = fake_home / ".config" / "clou"
        workspace.mkdir(parents=True)
        (workspace / ".clou").mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        result = resolve_project_dir_or_exit([])
        assert result == workspace

    def test_global_project_dir_returns_config_path(self) -> None:
        """global_project_dir returns ~/.config/clou/."""
        result = global_project_dir()
        assert result == Path.home() / ".config" / "clou"
