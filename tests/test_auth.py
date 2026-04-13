"""Tests for clou.auth — CLI discovery and auth status checking."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from clou.auth import AuthStatus, check_auth_status, find_claude_cli, run_auth_command


# ---------------------------------------------------------------------------
# find_claude_cli
# ---------------------------------------------------------------------------


def test_find_cli_via_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutil.which hit is returned immediately."""
    monkeypatch.setattr("clou.auth.shutil.which", lambda _: "/usr/bin/claude")
    assert find_claude_cli() == "/usr/bin/claude"


def test_find_cli_fallback_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falls back to well-known locations when which fails."""
    monkeypatch.setattr("clou.auth.shutil.which", lambda _: None)

    fake_cli = tmp_path / ".local/bin/claude"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.touch()

    monkeypatch.setattr("clou.auth.Path.home", lambda: tmp_path)
    assert find_claude_cli() == str(fake_cli)


def test_find_cli_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when no CLI is found anywhere."""
    monkeypatch.setattr("clou.auth.shutil.which", lambda _: None)
    monkeypatch.setattr("clou.auth.Path.home", lambda: Path("/nonexistent"))
    assert find_claude_cli() is None


# ---------------------------------------------------------------------------
# check_auth_status
# ---------------------------------------------------------------------------


def test_check_auth_logged_in() -> None:
    """Parses successful auth status JSON."""
    status_json = json.dumps(
        {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "email": "user@example.com",
            "subscriptionType": "max",
        }
    )
    result = subprocess.CompletedProcess(args=[], returncode=0, stdout=status_json)
    with patch("clou.auth.subprocess.run", return_value=result):
        status = check_auth_status("/usr/bin/claude")

    assert status.logged_in is True
    assert status.email == "user@example.com"
    assert status.auth_method == "claude.ai"
    assert status.subscription_type == "max"


def test_check_auth_not_logged_in() -> None:
    """Non-zero exit code means not logged in."""
    result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with patch("clou.auth.subprocess.run", return_value=result):
        status = check_auth_status("/usr/bin/claude")

    assert status.cli_found is True
    assert status.logged_in is False


def test_check_auth_timeout() -> None:
    """Timeout is handled gracefully."""
    with patch(
        "clou.auth.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)
    ):
        status = check_auth_status("/usr/bin/claude")

    assert status.cli_found is True
    assert status.logged_in is False


def test_check_auth_bad_json() -> None:
    """Malformed JSON is handled gracefully."""
    result = subprocess.CompletedProcess(args=[], returncode=0, stdout="not json")
    with patch("clou.auth.subprocess.run", return_value=result):
        status = check_auth_status("/usr/bin/claude")

    assert status.logged_in is False


# ---------------------------------------------------------------------------
# run_auth_command
# ---------------------------------------------------------------------------


def test_run_auth_cli_missing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prints install instructions and exits when CLI not found."""
    monkeypatch.setattr("clou.auth.find_claude_cli", lambda: None)
    with pytest.raises(SystemExit, match="1"):
        run_auth_command()

    out = capsys.readouterr().out
    assert "Claude CLI not found" in out
    assert "npm install" in out


def test_run_auth_not_logged_in(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prints login instructions when CLI found but not authenticated."""
    monkeypatch.setattr("clou.auth.find_claude_cli", lambda: "/usr/bin/claude")
    monkeypatch.setattr(
        "clou.auth.check_auth_status",
        lambda _: AuthStatus(
            cli_found=True,
            cli_path="/usr/bin/claude",
            logged_in=False,
            auth_method=None,
            email=None,
            subscription_type=None,
        ),
    )
    with pytest.raises(SystemExit, match="1"):
        run_auth_command()

    out = capsys.readouterr().out
    assert "not logged in" in out
    assert "clou auth login" in out


def test_run_auth_success(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prints status when authenticated."""
    monkeypatch.setattr("clou.auth.find_claude_cli", lambda: "/usr/bin/claude")
    monkeypatch.setattr(
        "clou.auth.check_auth_status",
        lambda _: AuthStatus(
            cli_found=True,
            cli_path="/usr/bin/claude",
            logged_in=True,
            auth_method="claude.ai",
            email="user@example.com",
            subscription_type="max",
        ),
    )
    run_auth_command()

    out = capsys.readouterr().out
    assert "authenticated" in out
    assert "user@example.com" in out
    assert "max" in out


# ---------------------------------------------------------------------------
# __main__ dispatch
# ---------------------------------------------------------------------------


def test_main_dispatches_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() dispatches to run_auth_command when argv[1] == 'auth'."""
    from clou.__main__ import main

    monkeypatch.setattr(sys, "argv", ["clou", "auth"])
    with patch("clou.auth.run_auth_command") as mock_auth:
        main()
    mock_auth.assert_called_once()
