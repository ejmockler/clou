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
        # M50 I1 cycle-4 rework (F20): explicit next_step required.
        content = render_status(
            milestone="m1", phase="impl", cycle=1, next_step="PLAN",
        )
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
            "next_step": "EXECUTE_REWORK",
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


class TestCheckpointToolRetryCounterPreservation:
    """M36 F5 (round-4): clou_write_checkpoint MUST round-trip retry counters.

    Every coordinator-initiated checkpoint write previously omitted
    validation_retries, readiness_retries, crash_retries, and
    staleness_count from the render call — so every write zeroed the
    retry ceilings.  The MCP tool schema does not expose these counters
    to the LLM; they are orchestrator-owned state that must survive
    LLM-driven rewrites.  Same wipe-class as F2 (pre_orient_next_step)
    and F4 (cycle_outcome/valid_findings/consecutive_zero_valid) —
    read from prev_cp and pass through unchanged.
    """

    @pytest.mark.asyncio
    async def test_validation_retries_preserved(self, coord_tools) -> None:
        """Pre-existing validation_retries survive an MCP write."""
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        cp_path = (
            tmp_path / ".clou" / "milestones" / "test-ms"
            / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(render_checkpoint(
            cycle=2,
            step="EXECUTE",
            next_step="ASSESS",
            current_phase="impl",
            phases_completed=0,
            phases_total=2,
            validation_retries=2,
            readiness_retries=1,
            crash_retries=1,
            staleness_count=3,
        ))
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "impl",
            "phases_completed": 1,
            "phases_total": 2,
        })
        cp_after = parse_checkpoint(cp_path.read_text())
        assert cp_after.validation_retries == 2
        assert cp_after.readiness_retries == 1
        assert cp_after.crash_retries == 1
        assert cp_after.staleness_count == 3

    @pytest.mark.asyncio
    async def test_fresh_checkpoint_defaults_to_zero(
        self, coord_tools,
    ) -> None:
        """When no prior checkpoint, retry counters remain at 0."""
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        cp_path = (
            tmp_path / ".clou" / "milestones" / "test-ms"
            / "active" / "coordinator.md"
        )
        assert not cp_path.exists()
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        await handler({
            "cycle": 1,
            "step": "PLAN",
            "next_step": "EXECUTE",
            "current_phase": "setup",
            "phases_completed": 0,
            "phases_total": 1,
        })
        cp_after = parse_checkpoint(cp_path.read_text())
        assert cp_after.validation_retries == 0
        assert cp_after.readiness_retries == 0
        assert cp_after.crash_retries == 0
        assert cp_after.staleness_count == 0

    @pytest.mark.asyncio
    async def test_retry_counters_survive_multiple_writes(
        self, coord_tools,
    ) -> None:
        """Counters persist across multiple consecutive MCP writes."""
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        cp_path = (
            tmp_path / ".clou" / "milestones" / "test-ms"
            / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(render_checkpoint(
            cycle=1,
            step="PLAN",
            next_step="EXECUTE",
            current_phase="impl",
            phases_completed=0,
            phases_total=2,
            validation_retries=1,
            readiness_retries=2,
            crash_retries=0,
            staleness_count=1,
        ))
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        # Three back-to-back writes — retry counters must NOT decay.
        for i in range(3):
            await handler({
                "cycle": 2 + i,
                "step": "EXECUTE",
                "next_step": "ASSESS",
                "current_phase": "impl",
                "phases_completed": 0,
                "phases_total": 2,
            })
        cp_after = parse_checkpoint(cp_path.read_text())
        assert cp_after.validation_retries == 1
        assert cp_after.readiness_retries == 2
        assert cp_after.staleness_count == 1


class TestCheckpointToolHaltTransitionStash:
    """M49b E1 (closes B9/F1): write_checkpoint_tool MUST stash the
    prior next_step into pre_halt_next_step when the LLM writes
    next_step=HALTED per the coordinator-assess prompt contract.
    Without this stash, the two-writer seam (LLM + engine halt gate)
    produces silent stash loss — continue-as-is silently falls back
    to ORIENT instead of restoring the actual pre-halt phase."""

    @pytest.mark.asyncio
    async def test_halted_transition_stashes_prior_next_step(
        self, coord_tools,
    ) -> None:
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler

        # Prior state: mid-ASSESS cycle.
        await handler({
            "cycle": 3, "step": "ASSESS", "next_step": "ASSESS",
            "current_phase": "phase_a",
            "phases_completed": 2, "phases_total": 3,
        })
        # LLM now files halt + per prompt contract writes HALTED.
        await handler({
            "cycle": 3, "step": "ASSESS", "next_step": "HALTED",
            "current_phase": "phase_a",
            "phases_completed": 2, "phases_total": 3,
            "cycle_outcome": "HALTED_PENDING_REVIEW",
        })

        cp = parse_checkpoint(
            (tmp_path / ".clou" / "milestones" / "test-ms"
             / "active" / "coordinator.md").read_text(encoding="utf-8"),
        )
        assert cp.next_step == "HALTED"
        # THE LOAD-BEARING ASSERTION: the LLM's HALTED write must
        # stash the prior next_step so clou_dispose_halt can restore it.
        assert cp.pre_halt_next_step == "ASSESS", (
            "E1 stash missing — continue-as-is would fall back to "
            "ORIENT instead of restoring the pre-halt step"
        )

    @pytest.mark.asyncio
    async def test_halted_transition_from_execute(
        self, coord_tools,
    ) -> None:
        """Same invariant for mid-EXECUTE halts (the common case when
        F28 meta-findings surface during verification-class cycles)."""
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler

        await handler({
            "cycle": 5, "step": "EXECUTE", "next_step": "EXECUTE",
            "current_phase": "phase_b",
            "phases_completed": 1, "phases_total": 2,
        })
        await handler({
            "cycle": 5, "step": "EXECUTE", "next_step": "HALTED",
            "current_phase": "phase_b",
            "phases_completed": 1, "phases_total": 2,
            "cycle_outcome": "HALTED_PENDING_REVIEW",
        })

        cp = parse_checkpoint(
            (tmp_path / ".clou" / "milestones" / "test-ms"
             / "active" / "coordinator.md").read_text(encoding="utf-8"),
        )
        assert cp.pre_halt_next_step == "EXECUTE"

    @pytest.mark.asyncio
    async def test_halted_on_halted_preserves_existing_stash(
        self, coord_tools,
    ) -> None:
        """Idempotent re-write of HALTED on a checkpoint that already
        says HALTED must NOT attempt to stash HALTED (which would
        raise from the render-side guard) and must NOT overwrite an
        existing stash (older context wins — matches _apply_halt_gate's
        preserve rule)."""
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler

        # First HALTED write from ASSESS → stash=ASSESS.
        await handler({
            "cycle": 3, "step": "ASSESS", "next_step": "ASSESS",
            "current_phase": "phase_a",
            "phases_completed": 2, "phases_total": 3,
        })
        await handler({
            "cycle": 3, "step": "ASSESS", "next_step": "HALTED",
            "current_phase": "phase_a",
            "phases_completed": 2, "phases_total": 3,
            "cycle_outcome": "HALTED_PENDING_REVIEW",
        })
        # Re-write HALTED (idempotent) — must not error and must
        # preserve the ASSESS stash.
        await handler({
            "cycle": 3, "step": "ASSESS", "next_step": "HALTED",
            "current_phase": "phase_a",
            "phases_completed": 2, "phases_total": 3,
            "cycle_outcome": "HALTED_PENDING_REVIEW",
        })

        cp = parse_checkpoint(
            (tmp_path / ".clou" / "milestones" / "test-ms"
             / "active" / "coordinator.md").read_text(encoding="utf-8"),
        )
        assert cp.pre_halt_next_step == "ASSESS"

    @pytest.mark.asyncio
    async def test_non_halted_write_leaves_stash_empty(
        self, coord_tools,
    ) -> None:
        """Non-HALTED writes must NOT populate pre_halt_next_step
        (stash is M49b-specific — no spurious populations)."""
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler

        await handler({
            "cycle": 2, "step": "EXECUTE", "next_step": "ASSESS",
            "current_phase": "foo",
            "phases_completed": 1, "phases_total": 2,
        })
        cp = parse_checkpoint(
            (tmp_path / ".clou" / "milestones" / "test-ms"
             / "active" / "coordinator.md").read_text(encoding="utf-8"),
        )
        assert cp.pre_halt_next_step == ""


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


# ---------------------------------------------------------------------------
# clou_brief_worker — canonical worker briefing construction
# ---------------------------------------------------------------------------


class TestBriefWorkerTool:
    """Code-owned briefing construction: no LLM-owned path derivation."""

    @pytest.mark.asyncio
    async def test_briefing_embeds_canonical_execution_path(
        self, coord_tools,
    ) -> None:
        """The returned briefing contains the exact canonical execution.md path."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({"function_name": "extend_logger"})
        assert not result.get("is_error")
        briefing = result["content"][0]["text"]

        # Deterministic path is baked in — no {task_slug} placeholder.
        assert (
            ".clou/milestones/test-ms/phases/extend_logger/execution.md"
            in briefing
        )
        assert "extend_logger" in briefing
        # No slugged shard syntax anywhere.
        assert "execution-" not in briefing

    @pytest.mark.asyncio
    async def test_briefing_includes_protocol_file_and_project_md(
        self, coord_tools,
    ) -> None:
        """Standard reads are always present."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({"function_name": "build_feature"})
        briefing = result["content"][0]["text"]

        assert ".clou/prompts/worker.md" in briefing
        assert ".clou/milestones/test-ms/compose.py" in briefing
        assert ".clou/milestones/test-ms/phases/build_feature/phase.md" in briefing
        assert ".clou/project.md" in briefing

    @pytest.mark.asyncio
    async def test_briefing_appends_intent_block(self, coord_tools) -> None:
        """Intent IDs append structuring guidance to the briefing."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({
            "function_name": "extend_logger",
            "intent_ids": ["I1", "I3"],
        })
        briefing = result["content"][0]["text"]

        assert "Your task addresses these intents: I1, I3" in briefing
        assert "per-intent sections" in briefing

    @pytest.mark.asyncio
    async def test_briefing_omits_intent_block_when_absent(
        self, coord_tools,
    ) -> None:
        """No intent IDs → no intent block in the output."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({"function_name": "extend_logger"})
        briefing = result["content"][0]["text"]

        assert "intents:" not in briefing
        assert "per-intent sections" not in briefing

    @pytest.mark.asyncio
    async def test_briefing_appends_extra_reads(self, coord_tools) -> None:
        """extra_reads entries appear alongside the standard read list."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({
            "function_name": "extend_logger",
            "extra_reads": ["src/logger.ts", "docs/LOGGING.md"],
        })
        briefing = result["content"][0]["text"]

        assert "- src/logger.ts" in briefing
        assert "- docs/LOGGING.md" in briefing

    @pytest.mark.asyncio
    async def test_briefing_rejects_invalid_function_name(
        self, coord_tools,
    ) -> None:
        """Function names that fail sanitize_phase are rejected."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        # sanitize_phase requires alphanumeric with underscores/hyphens,
        # no path separators or traversal.
        for bad in ["../escape", "has/slash", "has space", ""]:
            result = await handler({"function_name": bad})
            assert result.get("is_error"), f"should reject {bad!r}"

    @pytest.mark.asyncio
    async def test_briefing_rejects_non_string_function_name(
        self, coord_tools,
    ) -> None:
        """Missing or non-string function_name is rejected, not silently coerced."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({})
        assert result.get("is_error")

        result = await handler({"function_name": 123})
        assert result.get("is_error")

    @pytest.mark.asyncio
    async def test_briefing_accepts_json_string_arrays(
        self, coord_tools,
    ) -> None:
        """SDK may advertise array params as strings; JSON-string forms are accepted."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_brief_worker").handler

        result = await handler({
            "function_name": "extend_logger",
            "intent_ids": '["I1", "I3"]',
            "extra_reads": '["src/logger.ts"]',
        })
        briefing = result["content"][0]["text"]
        assert "I1, I3" in briefing
        assert "- src/logger.ts" in briefing


# ---------------------------------------------------------------------------
# clou_write_assessment + clou_append_classifications — code-owned assessment
# ---------------------------------------------------------------------------


class TestWriteAssessmentTool:
    """The brutalist's initial-write pathway for assessment.md."""

    @pytest.mark.asyncio
    async def test_writes_canonical_structure(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        result = await handler({
            "phase_name": "impl",
            "summary": {
                "status": "completed",
                "tools_invoked": 1,
                "findings_total": 2,
                "findings_critical": 0,
                "findings_major": 1,
                "findings_minor": 1,
                "phase_evaluated": "impl",
            },
            "tools": [
                {"tool": "roast", "domain": "codebase", "status": "invoked"},
            ],
            "findings": [
                {
                    "number": 1, "title": "Leak",
                    "severity": "major", "source_tool": "roast",
                    "affected_files": ["src/a.ts"],
                    "finding_text": '"leak at line 42"',
                },
                {
                    "number": 2, "title": "Nit",
                    "severity": "minor", "source_tool": "roast",
                },
            ],
        })
        assert not result.get("is_error")
        assert result["finding_count"] == 2

        path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        )
        content = path.read_text(encoding="utf-8")
        assert "# Assessment: impl" in content
        assert "## Summary" in content
        assert "status: completed" in content
        assert "## Findings" in content
        assert "### F1: Leak" in content
        assert "**Severity:** major" in content

    @pytest.mark.asyncio
    async def test_rejects_invalid_status(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        result = await handler({
            "phase_name": "impl",
            "summary": {"status": "bogus"},
        })
        assert result.get("is_error")
        assert "status" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_finding_severity(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        import pytest as _pt
        with _pt.raises(ValueError, match="severity"):
            await handler({
                "phase_name": "impl",
                "summary": {"status": "completed"},
                "findings": [
                    {"number": 1, "title": "x", "severity": "nope"},
                ],
            })

    @pytest.mark.asyncio
    async def test_rejects_missing_phase_name(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        result = await handler({
            "phase_name": "",
            "summary": {"status": "completed"},
        })
        assert result.get("is_error")
        assert "phase_name" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_rejects_missing_summary(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        result = await handler({"phase_name": "impl"})
        assert result.get("is_error")
        assert "summary" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_degraded_mode_passes_through(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        result = await handler({
            "phase_name": "impl",
            "summary": {
                "status": "degraded",
                "tools_invoked": 0,
                "findings_total": 0,
                "phase_evaluated": "impl",
                "internal_reviewers": 2,
                "gate_error": "quota exhausted",
            },
        })
        assert not result.get("is_error")
        content = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        ).read_text(encoding="utf-8")
        assert "status: degraded" in content
        assert "internal_reviewers: 2" in content
        assert "gate_error: quota exhausted" in content

    @pytest.mark.asyncio
    async def test_stringified_json_arrays_are_accepted(
        self, coord_tools,
    ) -> None:
        """SDK may advertise arrays as strings; JSON strings must work."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_assessment").handler

        result = await handler({
            "phase_name": "impl",
            "summary": {
                "status": "completed",
                "tools_invoked": 1,
                "findings_total": 1,
                "findings_major": 1,
                "phase_evaluated": "impl",
            },
            "findings": (
                '[{"number": 1, "title": "x", "severity": "major"}]'
            ),
        })
        assert not result.get("is_error")
        assert result["finding_count"] == 1


class TestAppendClassificationsTool:
    """Evaluator amendment pathway — parse existing, merge, re-render."""

    @pytest.mark.asyncio
    async def test_appends_to_canonical_assessment(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools

        # Seed with a canonical assessment.
        writer = _find_tool(tools, "clou_write_assessment").handler
        await writer({
            "phase_name": "impl",
            "summary": {
                "status": "completed",
                "tools_invoked": 1,
                "findings_total": 1,
                "findings_major": 1,
                "phase_evaluated": "impl",
            },
            "findings": [
                {"number": 1, "title": "x", "severity": "major"},
            ],
        })

        appender = _find_tool(tools, "clou_append_classifications").handler
        result = await appender({
            "classifications": [
                {
                    "finding_number": 1,
                    "classification": "valid",
                    "action": "Fix x",
                    "reasoning": "breaks invariant",
                },
            ],
        })
        assert not result.get("is_error")
        assert result["classification_count"] == 1

        content = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        ).read_text(encoding="utf-8")
        assert "## Classifications" in content
        assert "**Classification:** valid" in content
        assert "**Action:** Fix x" in content

    @pytest.mark.asyncio
    async def test_appends_to_drifted_phase_organized_assessment(
        self, coord_tools,
    ) -> None:
        """Drift-tolerant: existing phase-organized file is parsed + canonicalized."""
        tmp_path, tools = coord_tools
        path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("""\
# Assessment: Layer 1

## Summary
status: completed
tools_invoked: 1
findings: 2 total, 0 critical, 2 major, 0 minor
phase_evaluated: phase_a, phase_b

## Phase: phase_a

### F1: Alpha thing
**Severity:** major
**Source tool:** roast

## Phase: phase_b

### F2: Beta thing
**Severity:** major
**Source tool:** roast
""", encoding="utf-8")

        appender = _find_tool(tools, "clou_append_classifications").handler
        result = await appender({
            "classifications": [
                {
                    "finding_number": 1,
                    "classification": "valid",
                    "action": "fix alpha",
                },
                {
                    "finding_number": 2,
                    "classification": "noise",
                    "reasoning": "style only",
                },
            ],
        })
        assert not result.get("is_error")

        content = path.read_text(encoding="utf-8")
        # Canonicalized: ## Findings replaces ## Phase: sections.
        assert "## Findings" in content
        assert "## Phase: phase_a" not in content
        assert "## Phase: phase_b" not in content
        # Phase tag preserved per-finding.
        assert "**Phase:** phase_a" in content
        assert "**Phase:** phase_b" in content
        # Classifications merged.
        assert "## Classifications" in content
        assert "**Classification:** valid" in content
        assert "**Classification:** noise" in content

    @pytest.mark.asyncio
    async def test_rejects_without_existing_assessment(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        appender = _find_tool(tools, "clou_append_classifications").handler

        result = await appender({
            "classifications": [
                {"finding_number": 1, "classification": "valid"},
            ],
        })
        assert result.get("is_error")
        assert "does not exist" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_rejects_unknown_classification_kind(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Assessment: x\n\n## Summary\nstatus: completed\n",
            encoding="utf-8",
        )

        appender = _find_tool(tools, "clou_append_classifications").handler
        import pytest as _pt
        with _pt.raises(ValueError, match="classification"):
            await appender({
                "classifications": [
                    {"finding_number": 1, "classification": "invalid-kind"},
                ],
            })

    @pytest.mark.asyncio
    async def test_rejects_empty_classifications(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Assessment: x\n\n## Summary\nstatus: completed\n",
            encoding="utf-8",
        )

        appender = _find_tool(tools, "clou_append_classifications").handler
        result = await appender({"classifications": []})
        assert result.get("is_error")

    @pytest.mark.asyncio
    async def test_end_to_end_brutalist_then_evaluator_flow(
        self, coord_tools,
    ) -> None:
        """Integration test: brutalist writes, evaluator classifies,
        validator accepts, and no drift is possible at any point.

        This is the architectural "never again" invariant for the
        assessment-drift class of bug:

        1. Brutalist calls clou_write_assessment with structured
           findings across TWO phases (the exact scope that drove the
           brutalist-mcp-server LLM to invent '## Phase: X' sections).
        2. Evaluator calls clou_append_classifications to classify each.
        3. Validator runs on the resulting file and produces zero
           errors.

        At no point does freeform Write touch assessment.md — the
        hook enforces this, and these tests pin the behavior.
        """
        from clou.assessment import parse_assessment
        from clou.validation import validate_golden_context

        tmp_path, tools = coord_tools

        writer = _find_tool(tools, "clou_write_assessment").handler
        appender = _find_tool(tools, "clou_append_classifications").handler

        # (1) Brutalist writes assessment for a multi-phase layer.
        await writer({
            "phase_name": "Layer 1 Cycle 2",
            "summary": {
                "status": "completed",
                "tools_invoked": 2,
                "findings_total": 3,
                "findings_critical": 0,
                "findings_major": 2,
                "findings_minor": 1,
                "phase_evaluated": "instrument_cli_spawn, instrument_debate_module",
            },
            "tools": [
                {"tool": "roast", "domain": "codebase", "status": "invoked"},
                {"tool": "roast", "domain": "security", "status": "invoked"},
            ],
            "findings": [
                {
                    "number": 1, "title": "Flag meaning",
                    "severity": "major", "source_tool": "roast",
                    "source_models": ["CODEX"],
                    "affected_files": ["src/cli/spawn.ts"],
                    "finding_text": '"spawned means called spawnAsync"',
                    "phase": "instrument_cli_spawn",
                },
                {
                    "number": 2, "title": "Label escape order",
                    "severity": "major", "source_tool": "roast",
                    "affected_files": ["src/metrics/index.ts"],
                    "phase": "instrument_cli_spawn",
                },
                {
                    "number": 3, "title": "Debug-level proposition",
                    "severity": "minor", "source_tool": "roast",
                    "affected_files": ["src/debate/round.ts"],
                    "phase": "instrument_debate_module",
                },
            ],
        })

        # (2) Evaluator classifies.
        await appender({
            "classifications": [
                {
                    "finding_number": 1, "classification": "valid",
                    "action": "Rename or reposition",
                    "reasoning": "semantic drift",
                },
                {
                    "finding_number": 2, "classification": "valid",
                    "action": "Fix escape order", "reasoning": "",
                },
                {
                    "finding_number": 3, "classification": "security",
                    "action": "Downgrade to trace",
                    "reasoning": "log-level leakage",
                },
            ],
        })

        # (3) Validator accepts — zero errors and zero drift warnings.
        findings = validate_golden_context(tmp_path, "test-ms")
        from clou.validation import Severity
        errors = [f for f in findings if f.severity == Severity.ERROR]
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        assert errors == [], f"unexpected errors: {errors}"
        # No drift warnings — canonical form produced by the writer.
        assert not any(
            "drifted" in f.message.lower() for f in warnings
        ), f"unexpected drift warning: {warnings}"

        # The round-trip preserved phase tags per-finding.
        content = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        ).read_text(encoding="utf-8")
        assert "## Findings" in content
        assert "**Phase:** instrument_cli_spawn" in content
        assert "**Phase:** instrument_debate_module" in content
        # Drifted section names NEVER appear.
        assert "## Phase: " not in content
        assert "## Classification Summary" not in content
        assert "## Rework Signal Roll-up" not in content

        # Parsed form carries the evaluator's classifications.
        form = parse_assessment(content)
        assert len(form.classifications) == 3
        assert {c.finding_number for c in form.classifications} == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_last_writer_wins_on_reclassification(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools

        writer = _find_tool(tools, "clou_write_assessment").handler
        await writer({
            "phase_name": "impl",
            "summary": {
                "status": "completed",
                "tools_invoked": 1,
                "findings_total": 1,
                "findings_major": 1,
                "phase_evaluated": "impl",
            },
            "findings": [
                {"number": 1, "title": "x", "severity": "major"},
            ],
        })

        appender = _find_tool(tools, "clou_append_classifications").handler
        await appender({
            "classifications": [
                {
                    "finding_number": 1,
                    "classification": "noise",
                    "reasoning": "first pass",
                },
            ],
        })
        await appender({
            "classifications": [
                {
                    "finding_number": 1,
                    "classification": "valid",
                    "reasoning": "revised",
                },
            ],
        })

        content = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "assessment.md"
        ).read_text(encoding="utf-8")
        # Second classification wins.
        assert "**Classification:** valid" in content
        assert "revised" in content
        # First is gone (replaced).
        assert "first pass" not in content


# ---------------------------------------------------------------------------
# clou_file_escalation — MCP tool for agent-authored escalation files
# ---------------------------------------------------------------------------


class TestFileEscalationToolSchema:
    """JSON Schema advertised to the LLM for clou_file_escalation."""

    def test_tool_is_exposed(self, coord_tools) -> None:
        _, tools = coord_tools
        # C3 added clou_propose_milestone bringing the bundle to 8 tools.
        # Assert by membership rather than count so later tool additions
        # don't require updating this test.
        tool_names = {getattr(t, "name", "") for t in tools}
        assert "clou_file_escalation" in tool_names
        t = _find_tool(tools, "clou_file_escalation")
        assert t is not None

    def test_schema_advertises_object_with_options_array(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_file_escalation")
        schema = t.input_schema
        assert schema["type"] == "object"
        # Required keys
        required = set(schema["required"])
        assert required == {"title", "classification", "issue", "options"}
        # Options advertised as an array of {label, description} objects.
        options_schema = schema["properties"]["options"]
        assert options_schema["type"] == "array"
        assert options_schema["items"]["type"] == "object"
        assert "label" in options_schema["items"]["properties"]
        assert "description" in options_schema["items"]["properties"]


class TestFileEscalationToolHandler:
    """Round-trip + edge-case coverage for clou_file_escalation."""

    @pytest.mark.asyncio
    async def test_happy_path_roundtrips_every_field(self, coord_tools) -> None:
        """Full payload → file on disk → parse_escalation → identical form."""
        from clou.escalation import parse_escalation

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Supervisor Decision Needed",
            "classification": "blocking",
            "context": "Cycle 4 produced conflicting assessments.",
            "issue": "Two subagents disagree on the impl direction.",
            "evidence": "assessment.md shows major finding F2 disputed.",
            "options": [
                {
                    "label": "Option A",
                    "description": "Merge both implementations.",
                },
                {
                    "label": "Option B",
                    "description": "Pick the simpler one.",
                },
            ],
            "recommendation": "Prefer Option B — lower risk.",
        })
        assert not result.get("is_error")
        assert "written" in result
        assert result["slug"] == "supervisor-decision-needed"
        assert result["classification"] == "blocking"

        written = Path(result["written"])
        # Path lives under the milestone's escalations/ directory.
        ms_esc_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        )
        assert written.parent == ms_esc_dir
        assert written.exists()
        # Filename: YYYYMMDD-HHMMSS-{slug}.md
        stem = written.stem
        assert stem.endswith("-supervisor-decision-needed")

        # Re-parse and check every input field round-trips.
        form = parse_escalation(written.read_text(encoding="utf-8"))
        assert form.title == "Supervisor Decision Needed"
        assert form.classification == "blocking"
        assert form.context == "Cycle 4 produced conflicting assessments."
        assert form.issue == "Two subagents disagree on the impl direction."
        assert form.evidence == (
            "assessment.md shows major finding F2 disputed."
        )
        assert form.recommendation == "Prefer Option B — lower risk."
        assert len(form.options) == 2
        assert form.options[0].label == "Option A"
        assert form.options[0].description == "Merge both implementations."
        assert form.options[1].label == "Option B"
        assert form.options[1].description == "Pick the simpler one."
        assert form.disposition_status == "open"

    @pytest.mark.asyncio
    async def test_missing_title_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "",
            "classification": "blocking",
            "issue": "x",
            "options": [{"label": "A"}],
        })
        assert result.get("is_error")
        assert "title" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_classification_returns_error(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert result.get("is_error")
        assert "classification" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_issue_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "",
            "options": [{"label": "A"}],
        })
        assert result.get("is_error")
        assert "issue" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_empty_options_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "y",
            "options": [],
        })
        assert result.get("is_error")
        assert "options" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_option_missing_label_returns_error(
        self, coord_tools,
    ) -> None:
        """F18: label failure returns structured is_error (not raised)."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": ""}],
        })
        assert result.get("is_error")
        assert "options[0].label" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_stringified_options_accepted(self, coord_tools) -> None:
        """SDK may advertise arrays as strings — JSON-string options must work."""
        from clou.escalation import parse_escalation

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "JSON Shorthand Case",
            "classification": "informational",
            "issue": "LLM sent options as a JSON string.",
            "options": (
                '[{"label": "A", "description": "first"}, '
                '{"label": "B", "description": "second"}]'
            ),
        })
        assert not result.get("is_error")
        written = Path(result["written"])
        form = parse_escalation(written.read_text(encoding="utf-8"))
        assert len(form.options) == 2
        assert form.options[0].label == "A"
        assert form.options[0].description == "first"
        assert form.options[1].label == "B"
        assert form.options[1].description == "second"

    @pytest.mark.asyncio
    async def test_stringified_option_items_accepted(
        self, coord_tools,
    ) -> None:
        """Outer list proper, inner items stringified — the realistic drift mode."""
        from clou.escalation import parse_escalation

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Mixed Shorthand",
            "classification": "degraded",
            "issue": "Inner items are JSON strings.",
            "options": [
                '{"label": "A", "description": "first"}',
                '{"label": "B", "description": "second"}',
            ],
        })
        assert not result.get("is_error")
        form = parse_escalation(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        assert [o.label for o in form.options] == ["A", "B"]

    @pytest.mark.asyncio
    async def test_slug_derived_from_title(self, coord_tools) -> None:
        """No slug supplied → derived slug appears in path and matches [a-z0-9-]+."""
        import re as _re

        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "  Weird Title, With! Punctuation & Symbols  ",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        slug = result["slug"]
        assert _re.fullmatch(r"[a-z0-9-]+", slug) is not None
        # The derived slug must appear in the written filename.
        assert f"-{slug}.md" in result["written"]
        # Sanity: known transformation.
        assert slug.startswith("weird-title")

    @pytest.mark.asyncio
    async def test_supplied_slug_overrides_derivation(
        self, coord_tools,
    ) -> None:
        """User-provided slug wins and appears in the path verbatim."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Something Else Entirely",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
            "slug": "my-custom-slug",
        })
        assert not result.get("is_error")
        assert result["slug"] == "my-custom-slug"
        assert "-my-custom-slug.md" in result["written"]

    @pytest.mark.asyncio
    async def test_supplied_slug_with_path_traversal_rejected(
        self, coord_tools,
    ) -> None:
        """sanitize_phase blocks ``../escape`` in supplied slugs."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
            "slug": "../escape",
        })
        assert result.get("is_error")
        assert "slug" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_writes_under_milestone_escalations_dir(
        self, coord_tools,
    ) -> None:
        """Path must be under ``.clou/milestones/{ms}/escalations/`` — no traversal."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Path Sanity",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        written = Path(result["written"])
        ms_esc = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        )
        # resolve()-compared prefix guards against symlink/traversal escapes.
        assert str(written.resolve()).startswith(str(ms_esc.resolve()))
        assert written.exists()

    @pytest.mark.asyncio
    async def test_filed_timestamp_is_populated(self, coord_tools) -> None:
        """The file's `**Filed:**` preamble reflects the write time."""
        from clou.escalation import parse_escalation

        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Filed Timestamp",
            "classification": "informational",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        form = parse_escalation(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        # ISO-8601 with tz offset from datetime.now(UTC).isoformat().
        assert form.filed
        # Year component is a sanity signal that we wrote "now", not "".
        assert form.filed.startswith("20")

    # ---- F17: exclusive-create + suffix-retry on collision ---------------

    @pytest.mark.asyncio
    async def test_same_second_collision_gets_suffix(
        self, coord_tools, monkeypatch,
    ) -> None:
        """F17: Two filings in the same second with the same slug →
        second file gets ``-1`` suffix and the first is not overwritten.
        """
        import clou.coordinator_tools as ct

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler

        # Freeze datetime.now(UTC) so both calls land on the same second
        # AND the same slug (derived from identical titles).  The -1
        # suffix must come from the exclusive-create retry, not from
        # the timestamp moving.
        from datetime import datetime as _dt, UTC as _UTC

        class _Frozen:
            @staticmethod
            def now(tz=None):
                return _dt(2026, 4, 21, 12, 0, 0, tzinfo=_UTC)

        monkeypatch.setattr(ct, "datetime", _Frozen)

        payload = {
            "title": "Same Slug Twice",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        }
        first = await handler(dict(payload))
        second = await handler(dict(payload))
        assert not first.get("is_error")
        assert not second.get("is_error")
        assert first["written"] != second["written"]
        assert Path(first["written"]).exists()
        assert Path(second["written"]).exists()
        assert second["slug"].endswith("-1")
        assert second["written"].endswith("-1.md")

    # ---- F16 (cycle 2): reserved-slug canonical-first policy -----------

    @pytest.mark.asyncio
    async def test_reserved_slug_canonical_when_free(
        self, coord_tools,
    ) -> None:
        """F16 (cycle 2): reserved slug written at the canonical path
        when no collision exists on disk.

        The cycle-1 behaviour pre-suffixed every reserved-slug write even
        when ``cycle-limit.md`` was free, producing an adjacent
        ``cycle-limit-1.md`` with no causal relationship to the later
        recovery-filed ``cycle-limit.md``.  F16 switches to
        canonical-first: the exclusive-create contract already prevents
        stomping, so there is no reason to pre-suffix when the canonical
        filename is available.
        """
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler

        ms_esc_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        )
        # Precondition: the canonical ``cycle-limit.md`` (no suffix) does
        # NOT exist on disk yet.  The tool should therefore be free to
        # write at the canonical path.
        assert not any(
            p.name.endswith("-cycle-limit.md")
            for p in (ms_esc_dir.glob("*.md") if ms_esc_dir.exists() else [])
        )

        result = await handler({
            "title": "Cycle Limit",  # derives to slug "cycle-limit"
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        # F16: slug is the canonical ``cycle-limit`` — NOT ``cycle-limit-1``.
        assert result["slug"] == "cycle-limit"
        assert result["written"].endswith("-cycle-limit.md")
        # And no ``-1`` sibling was materialised ahead of time.
        assert not result["written"].endswith("-cycle-limit-1.md")

    @pytest.mark.asyncio
    async def test_reserved_slug_falls_back_to_suffix_on_collision(
        self, coord_tools, monkeypatch,
    ) -> None:
        """F16 (cycle 2): reserved slug collision → fall back to
        ``-1`` suffix, preserving the pre-existing canonical file.
        """
        import clou.coordinator_tools as ct

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler

        # Freeze datetime so the two calls share the same timestamp
        # component.  The collision will be on the canonical filename.
        from datetime import datetime as _dt, UTC as _UTC

        class _Frozen:
            @staticmethod
            def now(tz=None):
                return _dt(2026, 4, 21, 12, 0, 0, tzinfo=_UTC)

        monkeypatch.setattr(ct, "datetime", _Frozen)

        ms_esc_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        )
        ms_esc_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create the canonical ``20260421-120000-cycle-limit.md`` so
        # the tool has to suffix on the next attempt.
        canonical = ms_esc_dir / "20260421-120000-cycle-limit.md"
        canonical.write_text("pre-existing", encoding="utf-8")

        result = await handler({
            "title": "Cycle Limit",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        # F16: canonical is taken, so the tool moves to ``-1``.
        assert result["slug"] == "cycle-limit-1"
        assert result["written"].endswith("-cycle-limit-1.md")
        # The pre-existing canonical file is untouched.
        assert canonical.read_text(encoding="utf-8") == "pre-existing"

    def test_recovery_writer_still_works_after_coordinator_claims_canonical(
        self, tmp_path, monkeypatch,
    ) -> None:
        """F16 (cycle 2): When the coordinator tool has written the
        canonical ``{ts}-cycle-limit.md`` file, the in-process recovery
        writer still functions correctly --- it writes a ``-1`` suffix
        file rather than failing or stomping the coordinator-filed one.

        Sanity: the two writers share an exclusive-create contract, so
        the operational outcome (two adjacent files) is the same
        whether the coordinator or recovery wins the canonical name.
        """
        from clou.recovery_escalation import _write_escalation

        # Freeze datetime so the coordinator-file (simulated below) and
        # the recovery call share a second-level timestamp.
        import clou.recovery_escalation as re_mod
        from datetime import datetime as _dt, UTC as _UTC

        class _Frozen:
            @staticmethod
            def now(tz=None):
                return _dt(2026, 4, 21, 12, 0, 0, tzinfo=_UTC)

        monkeypatch.setattr(re_mod, "datetime", _Frozen)

        project_dir = tmp_path
        ms_name = "recovery-test-ms"
        ms_esc_dir = (
            project_dir / ".clou" / "milestones" / ms_name / "escalations"
        )
        ms_esc_dir.mkdir(parents=True, exist_ok=True)

        # Simulate the coordinator having already claimed the canonical
        # filename (e.g. because it ran F16-style and wrote canonical).
        canonical = ms_esc_dir / "20260421-120000-cycle-limit.md"
        canonical.write_text("coordinator-filed", encoding="utf-8")

        # Now fire the recovery writer for the same slug at the same
        # timestamp.  It must still succeed --- and must NOT stomp the
        # coordinator's file.
        path = _write_escalation(
            project_dir=project_dir,
            milestone=ms_name,
            slug="cycle-limit",
            title="Cycle Limit Reached",
            classification="blocking",
            context="x",
            issue="y",
            evidence="z",
            options=["Option 1"],
            recommendation="r",
        )
        assert path.exists()
        assert path.name == "20260421-120000-cycle-limit-1.md"
        # The coordinator-filed canonical is untouched.
        assert canonical.read_text(encoding="utf-8") == "coordinator-filed"

    # ---- F22 (cycle 2): suffix space exhaustion returns structured error

    @pytest.mark.asyncio
    async def test_suffix_exhaustion_returns_structured_error(
        self, coord_tools, monkeypatch,
    ) -> None:
        """F22 (cycle 2): When the suffix loop exhausts (1..999 all
        taken), the handler returns a structured ``is_error`` payload
        rather than leaking ``RuntimeError`` / ``_SlugSuffixExhausted``
        through the MCP transport.

        Patches ``_exclusive_write`` to always raise the internal
        exhaustion sentinel; verifies the handler catches it and
        returns ``is_error: True`` with an actionable hint.
        """
        import clou.coordinator_tools as ct

        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler

        def _always_exhausts(*args, **kwargs):
            raise ct._SlugSuffixExhausted(
                "exhausted slug suffix range for slug 'x' (tried 1..999)"
            )

        monkeypatch.setattr(ct, "_exclusive_write", _always_exhausts)

        result = await handler({
            "title": "Unrelenting Collisions",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        # F22: structured error, NOT a raised exception reaching the
        # caller.  The content message should mention the exhaustion
        # and include an actionable hint.
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "exhausted" in text.lower()
        # The N>999 hint is the actionable self-correction signal.
        assert "999" in text

    @pytest.mark.asyncio
    async def test_suffix_exhaustion_at_forced_filesystem_level(
        self, coord_tools, monkeypatch,
    ) -> None:
        """F22 (cycle 2): End-to-end exhaustion proof — patch the
        module-local ``open`` binding in ``clou.coordinator_tools`` so
        every ``mode='x'`` attempt raises ``FileExistsError``, drive the
        handler, and assert the structured error bubble-up chain.

        This test exercises the full ``_exclusive_write`` loop (1..999
        retries all failing) rather than short-circuiting via a patched
        ``_exclusive_write``, confirming the real exhaustion path
        surfaces a structured payload.
        """
        import builtins
        import clou.coordinator_tools as ct

        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler

        real_open = builtins.open

        def _always_exists(*args, **kwargs):
            mode = kwargs.get("mode")
            if mode is None and len(args) > 1:
                mode = args[1]
            if isinstance(mode, str) and "x" in mode:
                raise FileExistsError("forced collision (test)")
            return real_open(*args, **kwargs)

        # Inject a module-local ``open`` binding that overrides the
        # builtin for ``_exclusive_write``'s lookup.  ``raising=False``
        # is required because the module doesn't normally shadow ``open``.
        monkeypatch.setattr(ct, "open", _always_exists, raising=False)

        result = await handler({
            "title": "Unrelenting Collisions E2E",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "exhausted" in text.lower()
        assert "999" in text

    # ---- F18: every validation failure is structured, not raised --------

    @pytest.mark.asyncio
    async def test_options_malformed_json_returns_error(
        self, coord_tools,
    ) -> None:
        """F18: Malformed JSON in options string → structured is_error."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "y",
            "options": "not valid json",
        })
        assert result.get("is_error")
        assert "options" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_null_option_item_returns_error(
        self, coord_tools,
    ) -> None:
        """F18: A null entry inside the options list → structured is_error."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "y",
            "options": [None],
        })
        assert result.get("is_error")
        assert "options[0]" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_malformed_inner_option_string_returns_error(
        self, coord_tools,
    ) -> None:
        """F18: Inner item that is a malformed JSON string → is_error."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "x",
            "classification": "blocking",
            "issue": "y",
            "options": ["{bad json"],
        })
        assert result.get("is_error")
        assert "options[0]" in result["content"][0]["text"]

    # ---- F19: supplied slug capped to 50 chars --------------------------

    @pytest.mark.asyncio
    async def test_supplied_slug_capped_at_50_chars(
        self, coord_tools,
    ) -> None:
        """F19: Long supplied slugs are capped to _SLUG_MAX_LEN (50)."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler

        long_slug = "x" * 120  # sanitize_phase accepts; the cap kicks in.
        result = await handler({
            "title": "anything",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
            "slug": long_slug,
        })
        assert not result.get("is_error")
        # Cap applied: slug length ≤ 50.
        assert len(result["slug"]) <= 50
        assert result["slug"] == "x" * 50

    # ---- F25: Unicode slug round-trip via NFKD + ASCII fold -------------

    @pytest.mark.asyncio
    async def test_unicode_title_folds_to_ascii_slug(
        self, coord_tools,
    ) -> None:
        """F25: Non-ASCII titles normalise via NFKD + ASCII fold.

        Without the fold, ``Café`` collapses to the fallback
        ``escalation`` slug and every such title filed in the same
        second collides.  With the fold, ``Café`` → ``cafe``.
        """
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Café",
            "classification": "informational",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        assert result["slug"] == "cafe"
        assert "-cafe.md" in result["written"]

    @pytest.mark.asyncio
    async def test_unicode_accents_fold_not_dropped(
        self, coord_tools,
    ) -> None:
        """F25: ``élève`` preserves letter shape after fold → ``eleve``."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "élève",
            "classification": "informational",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        assert result["slug"] == "eleve"

    # ---- F27: non-canonical classification emits soft warning -----------

    @pytest.mark.asyncio
    async def test_unknown_classification_emits_warning(
        self, coord_tools,
    ) -> None:
        """F27: ``classification`` outside VALID_CLASSIFICATIONS →
        write succeeds, payload carries ``warnings`` list naming the
        canonical values.  Open-set contract is preserved (value is
        written verbatim).
        """
        from clou.escalation import parse_escalation, VALID_CLASSIFICATIONS

        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Drift Case",
            "classification": "urgent",  # NOT in VALID_CLASSIFICATIONS
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        assert "warnings" in result
        assert any("urgent" in w for w in result["warnings"])
        # Canonical values are named so the LLM can self-correct.
        canonical_mentioned = any(
            c in w
            for w in result["warnings"]
            for c in VALID_CLASSIFICATIONS
        )
        assert canonical_mentioned
        # Open-set contract: the value was still written.
        form = parse_escalation(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        assert form.classification == "urgent"

    @pytest.mark.asyncio
    async def test_canonical_classification_no_warning(
        self, coord_tools,
    ) -> None:
        """F27 inverse: canonical classification → no ``warnings`` key."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_file_escalation").handler
        result = await handler({
            "title": "Normal Case",
            "classification": "blocking",
            "issue": "y",
            "options": [{"label": "A"}],
        })
        assert not result.get("is_error")
        # No warnings emitted on the happy path.
        assert "warnings" not in result


# ---------------------------------------------------------------------------
# clou_write_judgment — MCP tool for ORIENT-cycle judgment files
# ---------------------------------------------------------------------------


class TestWriteJudgmentToolSchema:
    """JSON Schema advertised to the LLM for clou_write_judgment."""

    def test_tool_is_exposed(self, coord_tools) -> None:
        _, tools = coord_tools
        tool_names = {getattr(t, "name", "") for t in tools}
        assert "clou_write_judgment" in tool_names
        t = _find_tool(tools, "clou_write_judgment")
        assert t is not None

    def test_schema_required_fields(self, coord_tools) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_write_judgment")
        schema = t.input_schema
        assert schema["type"] == "object"
        required = set(schema["required"])
        assert required == {
            "next_action", "rationale", "evidence_paths",
            "expected_artifact", "cycle",
        }

    def test_schema_evidence_paths_is_array_of_strings(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_write_judgment")
        schema = t.input_schema
        evidence_schema = schema["properties"]["evidence_paths"]
        assert evidence_schema["type"] == "array"
        assert evidence_schema["items"]["type"] == "string"

    def test_schema_cycle_is_integer(self, coord_tools) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_write_judgment")
        schema = t.input_schema
        assert schema["properties"]["cycle"]["type"] == "integer"


class TestWriteJudgmentToolHandler:
    """Round-trip + edge-case coverage for clou_write_judgment."""

    @pytest.mark.asyncio
    async def test_happy_path_roundtrips_every_field(
        self, coord_tools,
    ) -> None:
        from clou.judgment import parse_judgment

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": (
                "Phase execution.md artefacts show all three tracks "
                "have a valid compose.py interface ready."
            ),
            "evidence_paths": [
                ".clou/milestones/36-orient-cycle-prefix/intents.md",
                ".clou/milestones/36-orient-cycle-prefix/status.md",
            ],
            "expected_artifact": (
                "phases/judgment_schema/execution.md updated with the "
                "new module path and test count"
            ),
            "cycle": 1,
        })
        assert not result.get("is_error")
        assert "written" in result
        assert result["next_action"] == "EXECUTE"
        assert result["evidence_path_count"] == 2

        written = Path(result["written"])
        ms_judgments = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "judgments"
        )
        assert written.parent == ms_judgments
        assert written.name == "cycle-01-judgment.md"
        assert written.exists()

        # Re-parse and verify every field round-trips.
        form = parse_judgment(written.read_text(encoding="utf-8"))
        assert form.next_action == "EXECUTE"
        assert (
            "Phase execution.md artefacts" in form.rationale
            and "interface ready" in form.rationale
        )
        assert form.evidence_paths == (
            ".clou/milestones/36-orient-cycle-prefix/intents.md",
            ".clou/milestones/36-orient-cycle-prefix/status.md",
        )
        assert "execution.md updated" in form.expected_artifact

    @pytest.mark.asyncio
    async def test_cycle_zero_padding(self, coord_tools) -> None:
        """cycle=5 → ``cycle-05-judgment.md`` (two-digit zero-pad)."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "ASSESS",
            "rationale": "Cycle 5 is when we assess.",
            "evidence_paths": ["status.md"],
            "expected_artifact": "assessment.md with structured findings",
            "cycle": 5,
        })
        assert not result.get("is_error")
        written = Path(result["written"])
        assert written.name == "cycle-05-judgment.md"

    @pytest.mark.asyncio
    async def test_cycle_above_nine_uses_two_digits(
        self, coord_tools,
    ) -> None:
        """cycle=12 → ``cycle-12-judgment.md`` (no truncation)."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "VERIFY",
            "rationale": "Cycle 12 verifies convergence.",
            "evidence_paths": ["metrics.md"],
            "expected_artifact": "verification log",
            "cycle": 12,
        })
        assert not result.get("is_error")
        written = Path(result["written"])
        assert written.name == "cycle-12-judgment.md"

    @pytest.mark.asyncio
    async def test_stringified_evidence_paths_accepted(
        self, coord_tools,
    ) -> None:
        """SDK may send arrays as JSON strings (shorthand coercion)."""
        from clou.judgment import parse_judgment

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "LLM sent evidence_paths as JSON string.",
            "evidence_paths": '["intents.md", "status.md"]',
            "expected_artifact": "execution.md",
            "cycle": 2,
        })
        assert not result.get("is_error")
        assert result["evidence_path_count"] == 2
        form = parse_judgment(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        assert form.evidence_paths == ("intents.md", "status.md")

    @pytest.mark.asyncio
    async def test_empty_evidence_paths_returns_error(
        self, coord_tools,
    ) -> None:
        """validate_judgment_fields rejects empty evidence_paths."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "But I have no evidence.",
            "evidence_paths": [],
            "expected_artifact": "something",
            "cycle": 1,
        })
        assert result.get("is_error")
        assert "evidence_paths" in result["content"][0]["text"]
        assert "non-empty" in result["content"][0]["text"]
        # No file written.
        judgments_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "judgments"
        )
        assert not (judgments_dir / "cycle-01-judgment.md").exists()

    @pytest.mark.asyncio
    async def test_unknown_next_action_returns_error(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "bogus",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"]
        assert "next_action" in msg
        assert "cycle-type vocabulary" in msg
        # No file written.
        judgments_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "judgments"
        )
        assert not (judgments_dir / "cycle-01-judgment.md").exists()

    @pytest.mark.asyncio
    async def test_missing_rationale_returns_error(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            # rationale omitted -> defaults to empty string
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert result.get("is_error")
        assert "rationale" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_expected_artifact_returns_error(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            # expected_artifact omitted
            "cycle": 1,
        })
        assert result.get("is_error")
        assert "expected_artifact" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_cycle_zero_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": 0,
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"]
        assert "cycle" in msg
        assert "positive integer" in msg

    @pytest.mark.asyncio
    async def test_cycle_negative_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": -3,
        })
        assert result.get("is_error")
        assert "positive integer" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_cycle_non_integer_returns_error(
        self, coord_tools,
    ) -> None:
        """cycle='1' (string) must not be silently coerced."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": "1",
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"]
        assert "cycle" in msg
        assert "positive integer" in msg

    @pytest.mark.asyncio
    async def test_cycle_bool_rejected(self, coord_tools) -> None:
        """cycle=True is bool (subclass of int) but must be rejected."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": True,
        })
        assert result.get("is_error")
        assert "positive integer" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_cycle_missing_returns_error(self, coord_tools) -> None:
        """cycle omitted entirely defaults to None and is rejected."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            # cycle omitted
        })
        assert result.get("is_error")
        assert "cycle" in result["content"][0]["text"]
        assert "positive integer" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_malformed_evidence_paths_json_returns_error(
        self, coord_tools,
    ) -> None:
        """Unparseable JSON string in evidence_paths is a structured error."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": "not-a-json-array",
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert result.get("is_error")
        assert "evidence_paths" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_whitespace_only_rationale_returns_error(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "   \t\n   ",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert result.get("is_error")
        assert "rationale" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_parent_directory_created_if_missing(
        self, coord_tools,
    ) -> None:
        """judgments/ directory is created even when it does not pre-exist."""
        tmp_path, tools = coord_tools
        judgments_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "judgments"
        )
        assert not judgments_dir.exists()
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md"],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert not result.get("is_error")
        assert judgments_dir.is_dir()
        assert (judgments_dir / "cycle-01-judgment.md").exists()

    @pytest.mark.asyncio
    async def test_evidence_paths_stripped(self, coord_tools) -> None:
        """Whitespace-only entries are discarded and count reflects real paths."""
        from clou.judgment import parse_judgment

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md", "   ", "b.md"],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert not result.get("is_error")
        assert result["evidence_path_count"] == 2
        form = parse_judgment(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        assert form.evidence_paths == ("a.md", "b.md")

    @pytest.mark.asyncio
    async def test_evidence_paths_non_string_item_rejected(
        self, coord_tools,
    ) -> None:
        """M52 Task #26: items must be strings, not silently
        coerced via ``str(p)``.  A dict element renders as
        ``"{'path': 'x'}"`` under coercion — garbage for the
        supervisor's evidence list.  Same validation pattern as
        ``clou_halt_trajectory``."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_judgment").handler

        # dict element
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["valid.md", {"path": "x"}, "ok.md"],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "evidence_paths[1]" in text
        assert "must be a string" in text
        assert "dict" in text

        # int element
        result = await handler({
            "next_action": "EXECUTE",
            "rationale": "r",
            "evidence_paths": ["a.md", 42],
            "expected_artifact": "x",
            "cycle": 1,
        })
        assert result.get("is_error") is True
        assert "evidence_paths[1]" in result["content"][0]["text"]
        assert "int" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# F13 lint: escalation writes must be routed through sanctioned writers
# ---------------------------------------------------------------------------


class TestEscalationWriteLint:
    """F13: No module outside the sanctioned writers may write to
    ``escalations/``.

    The three sanctioned writers are:

    - ``clou/coordinator_tools.py`` (``clou_file_escalation`` tool)
    - ``clou/supervisor_tools.py`` (``clou_resolve_escalation`` tool)
    - ``clou/recovery_escalation.py`` (in-process recovery writers)

    Any other module that performs ``write_text``/``open(..., mode='x')``
    on a path under ``escalations/`` bypasses the DB-21 single-write-path
    contract and must be refactored to go through one of the above.

    This is a lint-style check: it scans the ``clou/`` source tree for
    lines containing ``escalations/`` alongside a writer verb, and
    flags any occurrence outside the sanctioned set.  The scan is
    conservative; false positives (e.g. a line that mentions
    ``escalations/`` in a comment adjacent to an unrelated ``write_text``)
    can be worked around by reshaping the code, not by loosening the
    lint.
    """

    SANCTIONED = frozenset({
        "coordinator_tools.py",
        "supervisor_tools.py",
        "recovery_escalation.py",
    })

    def test_no_direct_escalation_writes_outside_sanctioned(self) -> None:
        import re

        repo_root = Path(__file__).resolve().parent.parent
        clou_dir = repo_root / "clou"
        assert clou_dir.is_dir(), f"expected {clou_dir} to exist"

        # Looks for the token ``escalations/`` in any line that also
        # contains a write verb.  This catches constructions like
        # ``(ms_dir / "escalations" / name).write_text(...)`` on a
        # single line; multiline constructions that split the path
        # across several statements are caught by a secondary scan
        # below (lines that build an ``escalations`` path adjacent to
        # a write call in the same module).
        write_verb = re.compile(
            r"\b(write_text|write_bytes|open\s*\([^)]*mode\s*=\s*['\"][wax])"
        )
        path_token = re.compile(r'escalations[/"]')

        offenders: list[tuple[str, int, str]] = []
        for py in clou_dir.rglob("*.py"):
            if py.name in self.SANCTIONED:
                continue
            for i, line in enumerate(
                py.read_text(encoding="utf-8").splitlines(), 1,
            ):
                stripped = line.strip()
                # Skip comments, docstrings, test suppressions.
                if stripped.startswith("#"):
                    continue
                if not path_token.search(line):
                    continue
                if write_verb.search(line):
                    offenders.append((str(py), i, line.rstrip()))

        assert offenders == [], (
            "Direct write to escalations/ detected outside sanctioned "
            "writers. Route through clou_file_escalation (coordinator) "
            "or clou_resolve_escalation (supervisor). Offenders:\n"
            + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
        )

    def test_sanctioned_writers_exist(self) -> None:
        """Sanity: the three sanctioned files actually exist.

        Guards against a rename silently invalidating the lint's
        allow-list.
        """
        repo_root = Path(__file__).resolve().parent.parent
        clou_dir = repo_root / "clou"
        for name in self.SANCTIONED:
            assert (clou_dir / name).is_file(), (
                f"sanctioned writer {name} missing --- the lint's "
                "allow-list must be updated to match the new layout"
            )


# ---------------------------------------------------------------------------
# M49a: clou_halt_trajectory --- coordinator verb for trajectory-breakdown halt
# ---------------------------------------------------------------------------


class TestHaltTrajectoryToolSchema:
    """JSON Schema advertised to the LLM for clou_halt_trajectory."""

    def test_tool_is_exposed(self, coord_tools) -> None:
        _, tools = coord_tools
        tool_names = {getattr(t, "name", "") for t in tools}
        assert "clou_halt_trajectory" in tool_names

    def test_schema_requires_reason_rationale_evidence_cycle(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_halt_trajectory")
        schema = t.input_schema
        assert schema["type"] == "object"
        assert set(schema["required"]) == {
            "reason", "rationale", "evidence_paths", "cycle_num",
        }

    def test_schema_advertises_evidence_paths_array(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_halt_trajectory")
        schema = t.input_schema
        assert schema["properties"]["evidence_paths"]["type"] == "array"
        assert schema["properties"]["evidence_paths"]["items"]["type"] == "string"

    def test_schema_proposal_ref_is_optional(self, coord_tools) -> None:
        _, tools = coord_tools
        t = _find_tool(tools, "clou_halt_trajectory")
        schema = t.input_schema
        assert "proposal_ref" in schema["properties"]
        assert "proposal_ref" not in schema["required"]


class TestHaltTrajectoryToolHandler:
    """Round-trip + edge cases for clou_halt_trajectory."""

    @pytest.mark.asyncio
    async def test_happy_path_writes_canonical_escalation(
        self, coord_tools,
    ) -> None:
        from clou.escalation import parse_escalation

        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "anti_convergence",
            "rationale": (
                "Findings re-surface with zero production change; "
                "58->33->28 trajectory; three-model convergence."
            ),
            "evidence_paths": [
                "milestones/test-ms/assessment.md:277-288",
                "telemetry/8d4c7878a56c.jsonl#cycle=3",
            ],
            "proposal_ref": "proposals/phase-owner-affinity.md",
            "cycle_num": 3,
        })
        assert not result.get("is_error")
        assert result["classification"] == "trajectory_halt"
        assert result["slug"] == "trajectory-halt"

        written = Path(result["written"])
        ms_esc_dir = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        )
        assert written.parent == ms_esc_dir
        assert written.exists()
        assert written.stem.endswith("-trajectory-halt")

        form = parse_escalation(written.read_text(encoding="utf-8"))
        assert form.classification == "trajectory_halt"
        # Title carries reason + cycle.
        assert "anti_convergence" in form.title
        assert "cycle 3" in form.title
        # Issue carries the rationale.
        assert "re-surface" in form.issue
        # Evidence carries the bullet-list of paths.
        assert "assessment.md:277-288" in form.evidence
        assert "telemetry" in form.evidence
        # Options pre-populated with the three canonical choices.
        labels = [o.label for o in form.options]
        assert labels == ["continue-as-is", "re-scope", "abandon"]
        # Recommendation defers to supervisor and links proposal_ref.
        assert "supervisor" in form.recommendation.lower()
        assert "phase-owner-affinity.md" in form.recommendation
        # Disposition starts open so the engine halt gate fires.
        assert form.disposition_status == "open"

    @pytest.mark.asyncio
    async def test_missing_reason_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "",
            "rationale": "x",
            "evidence_paths": ["a.md"],
            "cycle_num": 1,
        })
        assert result.get("is_error")
        assert "reason" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_rationale_returns_error(self, coord_tools) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "anti_convergence",
            "rationale": "",
            "evidence_paths": ["a.md"],
            "cycle_num": 1,
        })
        assert result.get("is_error")
        assert "rationale" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_empty_evidence_paths_returns_error(
        self, coord_tools,
    ) -> None:
        """Halt without evidence is not routable for supervisor review."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "anti_convergence",
            "rationale": "x",
            "evidence_paths": [],
            "cycle_num": 1,
        })
        assert result.get("is_error")
        assert "evidence_paths" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_non_integer_cycle_num_returns_error(
        self, coord_tools,
    ) -> None:
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "anti_convergence",
            "rationale": "x",
            "evidence_paths": ["a.md"],
            "cycle_num": "3",  # string, not int
        })
        assert result.get("is_error")
        assert "cycle_num" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_boolean_cycle_num_rejected(self, coord_tools) -> None:
        """bool is a subclass of int --- reject it explicitly."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "anti_convergence",
            "rationale": "x",
            "evidence_paths": ["a.md"],
            "cycle_num": True,
        })
        assert result.get("is_error")

    @pytest.mark.asyncio
    async def test_stringified_json_evidence_paths_coerced(
        self, coord_tools,
    ) -> None:
        """SDK schema-fallback case: array arrives as JSON string."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "scope_mismatch",
            "rationale": "x",
            "evidence_paths": '["a.md", "b.md"]',
            "cycle_num": 2,
        })
        assert not result.get("is_error")
        written = Path(result["written"])
        assert written.exists()
        text = written.read_text(encoding="utf-8")
        assert "a.md" in text and "b.md" in text

    @pytest.mark.asyncio
    async def test_proposal_ref_optional(self, coord_tools) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "irreducible_blocker",
            "rationale": "x",
            "evidence_paths": ["a.md"],
            "cycle_num": 1,
        })
        assert not result.get("is_error")
        from clou.escalation import parse_escalation
        form = parse_escalation(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        # When proposal_ref is absent, the "Related proposal:" link
        # stanza must not appear in the rendered recommendation.  The
        # general word "proposal" may still appear elsewhere (e.g.
        # "clou_propose_milestone" referenced as guidance), so the
        # sharp check is on the exact link-introducing substring.
        assert "Related proposal:" not in form.recommendation

    @pytest.mark.asyncio
    async def test_proposal_ref_present_emits_link(
        self, coord_tools,
    ) -> None:
        """Paired positive case for test_proposal_ref_optional: when
        proposal_ref IS supplied, the link stanza MUST appear.  Pins
        the conditional rendering so a future refactor can't silently
        drop the proposal-linking behaviour."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        result = await handler({
            "reason": "anti_convergence",
            "rationale": "x",
            "evidence_paths": ["a.md"],
            "proposal_ref": "proposals/phase-owner-affinity.md",
            "cycle_num": 1,
        })
        assert not result.get("is_error")
        from clou.escalation import parse_escalation
        form = parse_escalation(
            Path(result["written"]).read_text(encoding="utf-8"),
        )
        assert "Related proposal:" in form.recommendation
        assert "phase-owner-affinity.md" in form.recommendation

    @pytest.mark.asyncio
    async def test_evidence_paths_non_string_item_rejected(
        self, coord_tools,
    ) -> None:
        """Brutalist CODEX #6: evidence_paths schema advertises strings
        but the handler previously coerced dicts/numbers via ``str()``.
        Reject non-strings with a structured error so supervisors
        never receive evidence like ``{'path': 'x'}`` rendered as a
        bullet."""
        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        for bad in (
            [{"path": "a.md"}],          # dict item
            ["a.md", 42],                 # number item in otherwise-valid list
            ["a.md", True],               # bool item
            ["a.md", None],               # null item
        ):
            result = await handler({
                "reason": "anti_convergence",
                "rationale": "x",
                "evidence_paths": bad,
                "cycle_num": 1,
            })
            assert result.get("is_error"), (
                f"handler must reject non-string evidence_paths item: {bad!r}"
            )
            assert "evidence_paths" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_two_halts_in_same_second_suffix_cleanly(
        self, coord_tools,
    ) -> None:
        """Reserved-slug auto-suffix must apply so two rapid halts
        don't overwrite each other."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        payload = {
            "reason": "anti_convergence",
            "rationale": "x",
            "evidence_paths": ["a.md"],
            "cycle_num": 1,
        }
        r1 = await handler(payload)
        r2 = await handler(payload)
        assert not r1.get("is_error")
        assert not r2.get("is_error")
        # Both files exist with distinct paths.
        assert r1["written"] != r2["written"]
        assert Path(r1["written"]).exists()
        assert Path(r2["written"]).exists()

    @pytest.mark.asyncio
    async def test_telemetry_event_fires(self, coord_tools, monkeypatch) -> None:
        """clou_halt_trajectory must emit trajectory_halt.filed with
        milestone + reason + evidence_path_count + cycle_num."""
        from clou import telemetry

        events: list[tuple[str, dict[str, Any]]] = []

        def _capture(name: str, **attrs: Any) -> None:
            events.append((name, attrs))

        monkeypatch.setattr(telemetry, "event", _capture)

        _, tools = coord_tools
        handler = _find_tool(tools, "clou_halt_trajectory").handler
        await handler({
            "reason": "anti_convergence",
            "rationale": "x",
            "evidence_paths": ["a.md", "b.md"],
            "cycle_num": 7,
        })
        trajectory_events = [
            (n, a) for n, a in events if n == "trajectory_halt.filed"
        ]
        assert len(trajectory_events) == 1
        _, attrs = trajectory_events[0]
        assert attrs["milestone"] == "test-ms"
        assert attrs["reason"] == "anti_convergence"
        assert attrs["evidence_path_count"] == 2
        assert attrs["cycle_num"] == 7
