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


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class TestCoerceJsonObject:
    """_coerce_json_object bridges SDK string-fallback schema advertising."""

    def test_dict_passthrough(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        assert _coerce_json_object({"a": "b"}, param="x") == {"a": "b"}

    def test_none_returns_none(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        assert _coerce_json_object(None, param="x") is None

    def test_empty_string_returns_none(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        assert _coerce_json_object("", param="x") is None

    def test_json_string_parsed(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        result = _coerce_json_object('{"impl": "in_progress"}', param="x")
        assert result == {"impl": "in_progress"}

    def test_unparseable_string_raises(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        with pytest.raises(ValueError, match="param_a"):
            _coerce_json_object("not json!", param="param_a")

    def test_json_array_raises(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        with pytest.raises(ValueError, match="got list"):
            _coerce_json_object("[1, 2, 3]", param="x")

    def test_wrong_python_type_raises(self) -> None:
        from clou.coordinator_tools import _coerce_json_object

        with pytest.raises(ValueError, match="got int"):
            _coerce_json_object(42, param="x")


class TestCoerceJsonArray:
    """_coerce_json_array mirrors object coercion for list-typed params."""

    def test_list_passthrough(self) -> None:
        from clou.coordinator_tools import _coerce_json_array

        assert _coerce_json_array([1, 2, 3], param="x") == [1, 2, 3]

    def test_none_returns_empty_list(self) -> None:
        from clou.coordinator_tools import _coerce_json_array

        assert _coerce_json_array(None, param="x") == []

    def test_empty_string_returns_empty_list(self) -> None:
        from clou.coordinator_tools import _coerce_json_array

        assert _coerce_json_array("", param="x") == []

    def test_json_string_parsed(self) -> None:
        from clou.coordinator_tools import _coerce_json_array

        assert _coerce_json_array('[{"a": 1}]', param="x") == [{"a": 1}]

    def test_unparseable_string_raises(self) -> None:
        from clou.coordinator_tools import _coerce_json_array

        with pytest.raises(ValueError, match="unparseable"):
            _coerce_json_array("[bad", param="x")

    def test_json_object_raises(self) -> None:
        from clou.coordinator_tools import _coerce_json_array

        with pytest.raises(ValueError, match="got dict"):
            _coerce_json_array('{"a": 1}', param="x")


# ---------------------------------------------------------------------------
# Tool handler invocation — exercises the schema+coercion boundary that
# crashed in production when the SDK advertised dict/list as "string".
# ---------------------------------------------------------------------------


def _find_tool(tools: list, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found")


@pytest.fixture
def coord_tools(tmp_path: Path):
    pytest.importorskip("claude_agent_sdk")
    from clou.coordinator_tools import _build_coordinator_tools

    (tmp_path / ".clou" / "milestones" / "test-ms").mkdir(parents=True)
    return tmp_path, _build_coordinator_tools(tmp_path, "test-ms")


class TestUpdateStatusToolSchema:
    """JSON Schema advertised to the LLM for clou_update_status."""

    def test_phase_progress_advertised_as_object(self, coord_tools) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_update_status")
        schema = t.input_schema
        assert schema["type"] == "object"
        assert schema["properties"]["phase_progress"]["type"] == "object"
        assert schema["properties"]["phase_progress"][
            "additionalProperties"
        ]["type"] == "string"


class TestUpdateStatusToolHandler:
    """Regression: handler accepts dict AND JSON-string phase_progress."""

    @pytest.mark.asyncio
    async def test_accepts_dict_phase_progress(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_update_status").handler
        result = await handler({
            "phase": "impl",
            "cycle": 1,
            "next_step": "EXECUTE",
            "phase_progress": {"impl": "in_progress"},
            "notes": "",
        })
        status_md = (tmp_path / ".clou" / "milestones" / "test-ms" / "status.md").read_text()
        assert "impl" in status_md
        assert "in_progress" in status_md
        assert result["written"].endswith("status.md")

    @pytest.mark.asyncio
    async def test_accepts_stringified_phase_progress(self, coord_tools) -> None:
        """The original crash: phase_progress arrived as a JSON string.

        Assert on the rendered markdown table rows — substring checks would
        pass even if the dict was collapsed to garbage.  The parsed dict
        must produce two distinct table rows.
        """
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_update_status").handler
        await handler({
            "phase": "impl",
            "cycle": 1,
            "next_step": "EXECUTE",
            "phase_progress": '{"impl": "in_progress", "api": "pending"}',
            "notes": "",
        })
        status_md = (tmp_path / ".clou" / "milestones" / "test-ms" / "status.md").read_text()
        # Proper dict → two table rows with the pipe-separator syntax.
        assert "| impl | in_progress |" in status_md
        assert "| api | pending |" in status_md

    @pytest.mark.asyncio
    async def test_empty_phase_progress_is_ok(self, coord_tools) -> None:
        """Empty string → None → render_status uses default table row."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_update_status").handler
        await handler({
            "phase": "impl",
            "cycle": 1,
            "next_step": "EXECUTE",
            "phase_progress": "",
            "notes": "",
        })
        status_md = (tmp_path / ".clou" / "milestones" / "test-ms" / "status.md").read_text()
        # When phase_progress is empty, render_status synthesises a
        # single default row for the current phase.
        assert "| impl | in_progress |" in status_md

    @pytest.mark.asyncio
    async def test_malformed_string_raises(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_update_status").handler
        with pytest.raises(ValueError, match="phase_progress"):
            await handler({
                "phase": "impl",
                "cycle": 1,
                "next_step": "EXECUTE",
                "phase_progress": "not json",
                "notes": "",
            })


class TestWriteExecutionToolSchema:
    """JSON Schema advertised to the LLM for clou_write_execution."""

    def test_tasks_advertised_as_array_of_objects(self, coord_tools) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_write_execution")
        schema = t.input_schema
        tasks_schema = schema["properties"]["tasks"]
        assert tasks_schema["type"] == "array"
        assert tasks_schema["items"]["type"] == "object"
        assert tasks_schema["items"]["properties"]["files_changed"]["type"] == "array"

    def test_notes_not_advertised(self, coord_tools) -> None:
        """notes was dead input at the top level — removed per brutalist review."""
        _, tools = coord_tools
        t = _find_tool(tools, "clou_write_execution")
        assert "notes" not in t.input_schema["properties"]
        assert "notes" not in t.input_schema["required"]


class TestWriteExecutionToolHandler:
    """Regression: handler accepts list AND JSON-string tasks + nested."""

    @pytest.mark.asyncio
    async def test_accepts_list_of_dicts(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        result = await handler({
            "phase": "impl",
            "status": "completed",
            "tasks": [
                {"name": "Build shard", "status": "completed",
                 "files_changed": ["a.py"]},
                {"name": "Write tests", "status": "completed"},
            ],
            "failures": "none",
            "blockers": "none",
        })
        assert result["task_count"] == 2
        execution_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "phases" / "impl"
            / "execution.md"
        ).read_text()
        assert "Build shard" in execution_md
        assert "a.py" in execution_md

    @pytest.mark.asyncio
    async def test_accepts_stringified_tasks_array(self, coord_tools) -> None:
        """The LLM may send tasks as a JSON string if the schema drifts.

        Assert on the rendered task heading and Summary count — substring
        checks would pass on garbage.  A task entry must produce
        ``### T1:`` and a Summary line showing 1 completed.
        """
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        result = await handler({
            "phase": "impl",
            "status": "in_progress",
            "tasks": '[{"name": "T1", "status": "completed"}]',
            "failures": "none",
            "blockers": "none",
        })
        assert result["task_count"] == 1
        execution_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "phases" / "impl"
            / "execution.md"
        ).read_text()
        assert "### T1: T1" in execution_md
        assert "**Status:** completed" in execution_md
        assert "1 total, 1 completed" in execution_md

    @pytest.mark.asyncio
    async def test_accepts_list_with_stringified_items(self, coord_tools) -> None:
        """Native list where each element is a JSON string.

        The realistic LLM drift mode: outer array is a proper list but
        individual items come through stringified.  Each item must be
        parsed and rendered as a distinct task.
        """
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        result = await handler({
            "phase": "impl",
            "status": "in_progress",
            "tasks": [
                '{"name": "T1", "status": "completed"}',
                '{"name": "T2", "status": "in_progress"}',
            ],
            "failures": "none",
            "blockers": "none",
        })
        assert result["task_count"] == 2
        execution_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "phases" / "impl"
            / "execution.md"
        ).read_text()
        assert "### T1: T1" in execution_md
        assert "### T2: T2" in execution_md
        assert "2 total, 1 completed" in execution_md
        assert "1 in_progress" in execution_md

    @pytest.mark.asyncio
    async def test_malformed_tasks_string_raises(self, coord_tools) -> None:
        """Top-level tasks as unparseable string → ValueError at boundary."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        with pytest.raises(ValueError, match="tasks"):
            await handler({
                "phase": "impl",
                "status": "in_progress",
                "tasks": "not json at all",
                "failures": "none",
                "blockers": "none",
            })

    @pytest.mark.asyncio
    async def test_malformed_files_changed_raises(self, coord_tools) -> None:
        """Nested files_changed as unparseable string → ValueError with path."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        with pytest.raises(ValueError, match=r"tasks\[0\]\.files_changed"):
            await handler({
                "phase": "impl",
                "status": "in_progress",
                "tasks": [{
                    "name": "T1", "status": "completed",
                    "files_changed": "not json either",
                }],
                "failures": "none",
                "blockers": "none",
            })

    @pytest.mark.asyncio
    async def test_invalid_phase_rejected_at_handler(self, coord_tools) -> None:
        """Path traversal in `phase` must be rejected before any filesystem write."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        with pytest.raises(ValueError, match="invalid phase"):
            await handler({
                "phase": "../../etc",
                "status": "in_progress",
                "tasks": [],
                "failures": "none",
                "blockers": "none",
            })
        # Ensure no execution.md was written anywhere for the bad phase.
        ms_root = tmp_path / ".clou" / "milestones" / "test-ms"
        assert not (ms_root / "phases" / "..").exists() or \
            not list((ms_root / "phases").rglob("execution.md"))

    @pytest.mark.asyncio
    async def test_accepts_stringified_files_changed(self, coord_tools) -> None:
        """Inner files_changed list may also arrive as a JSON string."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        await handler({
            "phase": "impl",
            "status": "completed",
            "tasks": [{
                "name": "T1", "status": "completed",
                "files_changed": '["x.py", "y.py"]',
            }],
            "failures": "none",
            "blockers": "none",
        })
        execution_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "phases" / "impl"
            / "execution.md"
        ).read_text()
        assert "x.py" in execution_md
        assert "y.py" in execution_md

    @pytest.mark.asyncio
    async def test_null_task_item_raises(self, coord_tools) -> None:
        """Null/empty task items must fail loudly (no silent data loss)."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        with pytest.raises(ValueError, match=r"tasks\[1\]"):
            await handler({
                "phase": "impl",
                "status": "in_progress",
                "tasks": [{"name": "T1", "status": "completed"}, None],
                "failures": "none",
                "blockers": "none",
            })

    @pytest.mark.asyncio
    async def test_empty_string_task_item_raises(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        with pytest.raises(ValueError, match=r"tasks\[0\]"):
            await handler({
                "phase": "impl",
                "status": "in_progress",
                "tasks": [""],
                "failures": "none",
                "blockers": "none",
            })

    @pytest.mark.asyncio
    async def test_empty_tasks_array_ok(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_execution").handler
        result = await handler({
            "phase": "impl",
            "status": "in_progress",
            "tasks": [],
            "failures": "none",
            "blockers": "none",
        })
        assert result["task_count"] == 0


class TestCheckpointToolHandler:
    """clou_write_checkpoint now also side-effects status.md."""

    @pytest.mark.asyncio
    async def test_writes_checkpoint(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 2,
            "step": "EXECUTE",
            "next_step": "ASSESS",
            "current_phase": "impl",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert result["next_step"] == "ASSESS"
        checkpoint_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active" / "coordinator.md"
        ).read_text()
        assert "cycle: 2" in checkpoint_md
        assert "step: EXECUTE" in checkpoint_md

    @pytest.mark.asyncio
    async def test_checkpoint_side_effects_status(self, coord_tools) -> None:
        """Writing checkpoint also writes status.md as a derived view."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE (rework)",
            "current_phase": "api",
            "phases_completed": 1,
            "phases_total": 2,
        })
        assert "status_written" in result
        status_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "status.md"
        ).read_text()
        assert "# Status: test-ms" in status_md
        assert "phase: api" in status_md
        assert "cycle: 3" in status_md

    @pytest.mark.asyncio
    async def test_checkpoint_status_validates(self, coord_tools) -> None:
        """Side-effected status.md passes validation."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        await handler({
            "cycle": 1,
            "step": "PLAN",
            "next_step": "EXECUTE",
            "current_phase": "setup",
            "phases_completed": 0,
            "phases_total": 2,
        })
        status_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "status.md"
        ).read_text()
        findings = validate_status_checkpoint(status_md)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []


class TestUpdateStatusFromCheckpoint:
    """clou_update_status now reads checkpoint and re-renders."""

    @pytest.mark.asyncio
    async def test_reads_checkpoint_when_available(self, coord_tools) -> None:
        """When a checkpoint exists, status.md is derived from it."""
        tmp_path, tools = coord_tools
        # First write a checkpoint.
        cp_handler = _find_tool(tools, "clou_write_checkpoint").handler
        await cp_handler({
            "cycle": 5,
            "step": "ASSESS",
            "next_step": "VERIFY",
            "current_phase": "api",
            "phases_completed": 2,
            "phases_total": 3,
        })
        # Now call update_status -- it should derive from checkpoint.
        status_handler = _find_tool(tools, "clou_update_status").handler
        result = await status_handler({
            "phase": "ignored",
            "cycle": 999,
            "next_step": "ignored",
            "phase_progress": {"ignored": "pending"},
            "notes": "",
        })
        status_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "status.md"
        ).read_text()
        # Should reflect checkpoint values, not the args.
        assert "cycle: 5" in status_md
        assert "phase: api" in status_md
        assert "cycle: 999" not in status_md

    @pytest.mark.asyncio
    async def test_falls_back_without_checkpoint(self, coord_tools) -> None:
        """Without a checkpoint, falls back to direct render from args."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_update_status").handler
        await handler({
            "phase": "init",
            "cycle": 1,
            "next_step": "EXECUTE",
            "phase_progress": {"init": "in_progress"},
            "notes": "first plan",
        })
        status_md = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "status.md"
        ).read_text()
        assert "phase: init" in status_md
        assert "cycle: 1" in status_md
        assert "| init | in_progress |" in status_md
