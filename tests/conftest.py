"""Shared test fixtures.

Provides auth detection for integration tests without coupling to any
specific authentication method. The ``claude auth status`` command is
method-agnostic — it works for API keys, OAuth, or whatever Claude Code
adds next.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Auth detection — method-agnostic
# ---------------------------------------------------------------------------


def _claude_cli_available() -> bool:
    """Check if the ``claude`` CLI binary is on PATH."""
    return shutil.which("claude") is not None


def _claude_authenticated() -> bool:
    """Check if ``claude`` has valid auth, regardless of method."""
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        status = json.loads(result.stdout)
        return bool(status.get("loggedIn", False))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return False


_HAS_CLI = _claude_cli_available()
_HAS_AUTH = _HAS_CLI and _claude_authenticated()


def _skip_reason() -> str:
    if not _HAS_CLI:
        return "claude CLI not found on PATH"
    return "claude CLI not authenticated (run: claude auth login)"


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

requires_claude_auth = pytest.mark.skipif(
    not _HAS_AUTH,
    reason=_skip_reason(),
)


# ---------------------------------------------------------------------------
# Supervisor isolation for UI tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_supervisor(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent the supervisor from connecting to the SDK in unit tests.

    ``ClouApp.on_mount()`` calls ``run_supervisor_worker()`` which lazily
    imports and calls ``run_supervisor()``.  In unit tests the SDK is
    either unavailable or unwanted.  This fixture replaces it with a
    no-op, keeping the worker lifecycle intact while avoiding real
    connections.

    Skipped for tests marked ``integration``.
    """
    if any(m.name == "integration" for m in request.node.iter_markers()):
        return

    async def _noop(*_args: object, **_kwargs: object) -> None:
        pass

    try:
        monkeypatch.setattr("clou.orchestrator.run_supervisor", _noop)
    except (ImportError, ModuleNotFoundError):
        pass  # SDK not installed — nothing to stub
