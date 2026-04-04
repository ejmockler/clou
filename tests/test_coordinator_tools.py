"""Tests for coordinator protocol artifact tools.

The serializer→file→validation round-trip is tested without the SDK.
Server construction tests require the SDK and are skipped otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.golden_context import (
    assemble_execution,
    render_checkpoint,
    render_execution_summary,
    render_execution_task,
    render_status,
)
from clou.validation import (
    Severity,
    _validate_execution,
    validate_checkpoint,
    validate_status_checkpoint,
)


@pytest.fixture
def ms_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".clou" / "milestones" / "test-ms"
    d.mkdir(parents=True)
    return d


class TestCheckpointRoundTrip:
    """render_checkpoint → write → validate_checkpoint = 0 errors."""

    def test_writes_valid_file(self, ms_dir: Path) -> None:
        content = render_checkpoint(
            cycle=2, step="EXECUTE", next_step="ASSESS",
            current_phase="impl", phases_completed=1, phases_total=3,
        )
        path = ms_dir / "active" / "coordinator.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        findings = validate_checkpoint(path.read_text())
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []

    def test_zero_warnings_when_fully_specified(self) -> None:
        content = render_checkpoint(
            cycle=1, step="PLAN", next_step="EXECUTE",
            current_phase="impl", phases_completed=0, phases_total=2,
        )
        assert validate_checkpoint(content) == []


class TestStatusRoundTrip:
    """render_status → write → validate_status_checkpoint = 0 errors."""

    def test_writes_valid_file(self, ms_dir: Path) -> None:
        content = render_status(
            milestone="test-ms", phase="impl", cycle=1,
            next_step="EXECUTE",
            phase_progress={"impl": "in_progress", "api": "pending"},
        )
        path = ms_dir / "status.md"
        path.write_text(content, encoding="utf-8")

        findings = validate_status_checkpoint(path.read_text())
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []

    def test_minimal_passes(self) -> None:
        content = render_status(milestone="m1", phase="impl", cycle=1)
        findings = validate_status_checkpoint(content)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []


class TestExecutionRoundTrip:
    """assemble_execution → write → _validate_execution = 0 errors."""

    def test_writes_valid_file(self, ms_dir: Path) -> None:
        summary = render_execution_summary(
            status="completed", tasks_total=2, tasks_completed=2,
        )
        tasks = [
            render_execution_task(1, "Build shard", status="completed",
                                  files_changed=["clou/shard.py"]),
            render_execution_task(2, "Write tests", status="completed"),
        ]
        content = assemble_execution(summary, tasks)

        path = ms_dir / "phases" / "impl" / "execution.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        findings = _validate_execution(path)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == [], f"errors: {errors}"

    def test_has_required_sections(self) -> None:
        summary = render_execution_summary(status="in_progress", tasks_total=1)
        tasks = [render_execution_task(1, "Do work")]
        content = assemble_execution(summary, tasks)

        assert "## Summary" in content
        assert "## Tasks" in content
        assert "### T1:" in content


class TestServerConstruction:
    """build_coordinator_mcp_server requires the SDK."""

    def test_rejects_invalid_milestone(self, tmp_path: Path) -> None:
        pytest.importorskip("claude_agent_sdk")
        from clou.coordinator_tools import build_coordinator_mcp_server

        with pytest.raises(ValueError):
            build_coordinator_mcp_server(tmp_path, "../escape")

    def test_creates_server(self, tmp_path: Path) -> None:
        pytest.importorskip("claude_agent_sdk")
        from clou.coordinator_tools import build_coordinator_mcp_server

        (tmp_path / ".clou" / "milestones" / "valid-ms").mkdir(parents=True)
        server = build_coordinator_mcp_server(tmp_path, "valid-ms")
        assert server is not None
