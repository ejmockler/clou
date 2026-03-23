"""Tests for clou.__main__ — CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from clou.__main__ import _run_init, main


def test_run_init_creates_clou_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_init creates .clou/ in the current directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["clou", "init"])
    _run_init()

    assert (tmp_path / ".clou").is_dir()
    assert (tmp_path / ".clou" / "milestones").is_dir()
    assert (tmp_path / ".clou" / "project.md").is_file()


def test_run_init_with_project_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_init uses argv[2] as project name."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["clou", "init", "my-project"])
    _run_init()

    content = (tmp_path / ".clou" / "project.md").read_text()
    assert "# my-project" in content


def test_run_init_defaults_to_dir_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_init uses cwd name when no project name given."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["clou", "init"])
    _run_init()

    content = (tmp_path / ".clou" / "project.md").read_text()
    assert f"# {tmp_path.name}" in content


def test_main_dispatches_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() dispatches to _run_init when argv[1] == 'init'."""
    monkeypatch.setattr(sys, "argv", ["clou", "init"])
    with patch("clou.__main__._run_init") as mock_init:
        main()
    mock_init.assert_called_once()


def test_main_without_init_launches_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() without 'init' calls resolve_project_dir_or_exit and ClouApp."""
    monkeypatch.setattr(sys, "argv", ["clou"])
    with (
        patch(
            "clou.project.resolve_project_dir_or_exit",
            return_value=Path("/tmp/project"),
        ) as mock_resolve,
        patch("clou.ui.app.ClouApp") as mock_app_cls,
    ):
        main()

    mock_resolve.assert_called_once()
    mock_app_cls.assert_called_once()
    mock_app_cls.return_value.run.assert_called_once()


# ---------------------------------------------------------------------------
# --continue / --resume flags
# ---------------------------------------------------------------------------


def test_parse_resume_flag_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No flags returns None."""
    from clou.__main__ import _parse_resume_flag

    monkeypatch.setattr(sys, "argv", ["clou"])
    assert _parse_resume_flag() is None


def test_parse_resume_flag_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    """--resume SESSION_ID returns the session ID."""
    from clou.__main__ import _parse_resume_flag

    monkeypatch.setattr(sys, "argv", ["clou", "--resume", "abc123"])
    assert _parse_resume_flag() == "abc123"


def test_parse_resume_flag_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--continue returns the most recent session ID."""
    from clou.__main__ import _parse_resume_flag
    from clou.session import Session

    (tmp_path / ".clou").mkdir()
    Session(tmp_path, session_id="latest")
    monkeypatch.setattr(sys, "argv", ["clou", "--continue"])
    with patch(
        "clou.project.resolve_project_dir_or_exit",
        return_value=tmp_path,
    ):
        sid = _parse_resume_flag()
    assert sid == "latest"


def test_parse_resume_flag_continue_no_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--continue with no sessions exits."""
    from clou.__main__ import _parse_resume_flag

    (tmp_path / ".clou").mkdir()
    monkeypatch.setattr(sys, "argv", ["clou", "--continue"])
    with (
        patch(
            "clou.project.resolve_project_dir_or_exit",
            return_value=tmp_path,
        ),
        pytest.raises(SystemExit),
    ):
        _parse_resume_flag()


def test_main_with_resume_passes_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() passes resume_session_id to ClouApp."""
    monkeypatch.setattr(sys, "argv", ["clou", "--resume", "test-id"])
    with (
        patch(
            "clou.project.resolve_project_dir_or_exit",
            return_value=Path("/tmp/project"),
        ),
        patch("clou.ui.app.ClouApp") as mock_app_cls,
    ):
        main()

    call_kwargs = mock_app_cls.call_args
    assert call_kwargs[1]["resume_session_id"] == "test-id"
