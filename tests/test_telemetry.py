"""Tests for clou.telemetry — structured span log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clou.telemetry import (
    SpanLog, init, read_log, span, event, write_milestone_summary, _fmt_duration,
    extract_task_data,
)
from clou import telemetry


class TestSpanLog:
    """SpanLog writes valid JSONL with correct structure."""

    def test_event_writes_jsonl(self, tmp_path: Path) -> None:
        log = SpanLog(tmp_path / "tel" / "test.jsonl")
        log.event("session.start", session_id="abc")

        lines = log.path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "session.start"
        assert record["session_id"] == "abc"
        assert "wall" in record
        assert "t_s" in record

    def test_span_records_duration(self, tmp_path: Path) -> None:
        log = SpanLog(tmp_path / "test.jsonl")
        with log.span("cycle", milestone="auth") as s:
            s["outcome"] = "ok"

        records = read_log(log.path)
        assert len(records) == 1
        r = records[0]
        assert r["span"] == "cycle"
        assert r["milestone"] == "auth"
        assert r["outcome"] == "ok"
        assert "duration_ms" in r
        assert r["duration_ms"] >= 0
        assert "t0_s" in r
        assert "wall" in r

    def test_span_emits_on_exception(self, tmp_path: Path) -> None:
        log = SpanLog(tmp_path / "test.jsonl")
        try:
            with log.span("cycle") as s:
                s["started"] = True
                raise ValueError("boom")
        except ValueError:
            pass

        records = read_log(log.path)
        assert len(records) == 1
        assert records[0]["started"] is True
        assert records[0]["duration_ms"] >= 0

    def test_multiple_records(self, tmp_path: Path) -> None:
        log = SpanLog(tmp_path / "test.jsonl")
        log.event("a")
        log.event("b")
        with log.span("c"):
            pass

        records = read_log(log.path)
        assert len(records) == 3

    def test_gitignore_created(self, tmp_path: Path) -> None:
        SpanLog(tmp_path / "tel" / "test.jsonl")
        gitignore = tmp_path / "tel" / ".gitignore"
        assert gitignore.exists()
        assert "*" in gitignore.read_text()

    def test_gitignore_not_overwritten(self, tmp_path: Path) -> None:
        tel_dir = tmp_path / "tel"
        tel_dir.mkdir()
        gitignore = tel_dir / ".gitignore"
        gitignore.write_text("custom\n")
        SpanLog(tel_dir / "test.jsonl")
        assert gitignore.read_text() == "custom\n"

    def test_emit_swallows_io_error(self, tmp_path: Path) -> None:
        """I/O failure in _emit must not propagate — telemetry is observability."""
        log = SpanLog(tmp_path / "test.jsonl")
        log.event("before")
        # Make the file unwritable.
        log.path.chmod(0o444)
        try:
            # Neither event nor span should raise.
            log.event("should_not_crash")
            with log.span("should_not_crash") as s:
                s["x"] = 1
        finally:
            log.path.chmod(0o644)
        # The "before" record survives; failed writes are silently dropped.
        records = read_log(log.path)
        assert len(records) == 1
        assert records[0]["event"] == "before"


class TestModuleLevelAPI:
    """Module-level span/event functions."""

    def test_noop_when_uninitialised(self) -> None:
        """No error when telemetry not initialised."""
        old = telemetry._log
        try:
            telemetry._log = None
            event("test")
            with span("test") as s:
                s["x"] = 1
            assert s == {"x": 1}  # Dict is usable but not persisted
        finally:
            telemetry._log = old

    def test_init_creates_log(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            log = init("sess123", tmp_path)
            assert log.path.exists()
            records = read_log(log.path)
            assert len(records) == 1
            assert records[0]["event"] == "session.start"
            assert records[0]["session_id"] == "sess123"
        finally:
            telemetry._log = old

    def test_span_and_event_after_init(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            log = init("sess456", tmp_path)
            event("milestone.start", milestone="auth")
            with span("cycle", milestone="auth", cycle_num=1) as s:
                s["outcome"] = "ASSESS"
            event("milestone.end", milestone="auth", outcome="completed")

            records = read_log(log.path)
            assert len(records) == 4  # session.start + 3
            assert records[1]["event"] == "milestone.start"
            assert records[2]["span"] == "cycle"
            assert records[2]["outcome"] == "ASSESS"
            assert records[3]["event"] == "milestone.end"
        finally:
            telemetry._log = old


class TestReadLog:
    """read_log handles edge cases."""

    def test_missing_file(self, tmp_path: Path) -> None:
        assert read_log(tmp_path / "nope.jsonl") == []

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert read_log(p) == []

    def test_corrupt_lines_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "mixed.jsonl"
        p.write_text('{"good": true}\nnot json\n{"also": "good"}\n')
        records = read_log(p)
        assert len(records) == 2

    def test_relative_times_increase(self, tmp_path: Path) -> None:
        log = SpanLog(tmp_path / "test.jsonl")
        log.event("a")
        log.event("b")
        records = read_log(log.path)
        assert records[1]["t_s"] >= records[0]["t_s"]


class TestFmtDuration:
    """Duration formatting helper."""

    def test_seconds(self) -> None:
        assert _fmt_duration(42_000) == "42s"

    def test_minutes(self) -> None:
        assert _fmt_duration(195_000) == "3m 15s"

    def test_zero(self) -> None:
        assert _fmt_duration(0) == "0s"


class TestWriteMilestoneSummary:
    """Layer 1 — golden context metrics summary."""

    def _populate_log(self, tmp_path: Path) -> None:
        """Seed the global log with a realistic milestone."""
        init("test-session", tmp_path)
        # milestone.start
        event("milestone.start", milestone="auth")
        # Cycle 1: PLAN
        with span("cycle", milestone="auth", cycle_num=1, cycle_type="PLAN") as c:
            c["outcome"] = "EXECUTE"
            c["input_tokens"] = 12000
            c["output_tokens"] = 3000
        # Cycle 2: EXECUTE with agents
        event("agent.start", milestone="auth", cycle_num=2,
              task_id="a1", description="implement login")
        event("agent.start", milestone="auth", cycle_num=2,
              task_id="a2", description="write tests")
        event("agent.end", milestone="auth", cycle_num=2,
              task_id="a1", status="completed", total_tokens=15000, tool_uses=12)
        event("agent.end", milestone="auth", cycle_num=2,
              task_id="a2", status="completed", total_tokens=8000, tool_uses=5)
        with span("cycle", milestone="auth", cycle_num=2, cycle_type="EXECUTE") as c:
            c["outcome"] = "ASSESS"
            c["input_tokens"] = 35000
            c["output_tokens"] = 6000
        # Cycle 3: ASSESS
        with span("cycle", milestone="auth", cycle_num=3, cycle_type="ASSESS") as c:
            c["outcome"] = "VERIFY"
            c["input_tokens"] = 20000
            c["output_tokens"] = 2000
        # Cycle 4: VERIFY
        with span("cycle", milestone="auth", cycle_num=4, cycle_type="VERIFY") as c:
            c["outcome"] = "COMPLETE"
            c["input_tokens"] = 6000
            c["output_tokens"] = 1000

    def test_writes_metrics_file(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            self._populate_log(tmp_path)
            write_milestone_summary(tmp_path, "auth", "completed")

            metrics = tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            assert metrics.exists()
            content = metrics.read_text()
            assert "# Metrics: auth" in content
            assert "outcome: completed" in content
            assert "cycles: 4" in content
        finally:
            telemetry._log = old

    def test_header_fields(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            self._populate_log(tmp_path)
            write_milestone_summary(tmp_path, "auth", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            ).read_text()
            assert "tokens_in: 73000" in content
            assert "tokens_out: 12000" in content
            assert "agents_spawned: 2" in content
            assert "agents_completed: 2" in content
            assert "agents_failed: 0" in content
            assert "crash_retries: 0" in content
        finally:
            telemetry._log = old

    def test_cycle_table(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            self._populate_log(tmp_path)
            write_milestone_summary(tmp_path, "auth", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            ).read_text()
            assert "## Cycles" in content
            assert "PLAN" in content
            assert "EXECUTE" in content
            assert "VERIFY" in content
        finally:
            telemetry._log = old

    def test_agent_table(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            self._populate_log(tmp_path)
            write_milestone_summary(tmp_path, "auth", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            ).read_text()
            assert "## Agents" in content
            assert "implement login" in content
            assert "write tests" in content
        finally:
            telemetry._log = old

    def test_incidents_section(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            init("test-inc", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "failed"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            event("crash", milestone="m1", cycle_num=1, attempt=1)
            with span("cycle", milestone="m1", cycle_num=2, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000

            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Incidents" in content
            assert "crash" in content
            assert "attempt 1" in content
            assert "crash_retries: 1" in content
        finally:
            telemetry._log = old

    def test_orphaned_agents(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            init("test-orphan", tmp_path)
            event("agent.start", milestone="m1", cycle_num=1,
                  task_id="x1", description="doomed agent")
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="EXECUTE") as c:
                c["outcome"] = "agent_team_crash"
                c["input_tokens"] = 5000
                c["output_tokens"] = 500

            write_milestone_summary(tmp_path, "m1", "escalated_agent_crash")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "orphaned" in content
            assert "doomed agent" in content
            assert "agents_failed: 1" in content
        finally:
            telemetry._log = old

    def test_noop_without_init(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            telemetry._log = None
            write_milestone_summary(tmp_path, "auth", "completed")
            # No file written, no error.
            assert not (
                tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            ).exists()
        finally:
            telemetry._log = old

    def test_ignores_other_milestones(self, tmp_path: Path) -> None:
        old = telemetry._log
        try:
            init("test-filter", tmp_path)
            with span("cycle", milestone="auth", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 10000
                c["output_tokens"] = 2000
            with span("cycle", milestone="billing", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 99000
                c["output_tokens"] = 99000

            write_milestone_summary(tmp_path, "auth", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            ).read_text()
            assert "cycles: 1" in content
            assert "tokens_in: 10000" in content
            # billing's tokens should not appear
            assert "99000" not in content
        finally:
            telemetry._log = old

    def test_quality_gate_section(self, tmp_path: Path) -> None:
        """DB-18: quality_gate.result events produce a Quality Gate table."""
        old = telemetry._log
        try:
            init("test-qg", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 1000
            event(
                "quality_gate.result", milestone="m1", cycle_num=3,
                tools_invoked=["roast"],
                tools_unavailable=["roast_cli_debate"],
                finding_count=7,
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Quality Gate" in content
            assert "roast" in content
            assert "roast_cli_debate" in content
            assert "7" in content
        finally:
            telemetry._log = old

    def test_quality_gate_section_converged(self, tmp_path: Path) -> None:
        """DB-18: converged status shows tools_suppressed, not tools_unavailable."""
        old = telemetry._log
        try:
            init("test-qg-conv", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 1000
            event(
                "quality_gate.result", milestone="m1", cycle_num=3,
                status="converged",
                tools_invoked=[],
                tools_unavailable=[],
                tools_suppressed=["mcp__brutalist__roast"],
                tools_invoked_count=0,
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Quality Gate" in content
            assert "converged" in content
            # Suppressed tools shown with (suppressed) label
            assert "mcp__brutalist__roast (suppressed)" in content
            # Gate Availability counts converged status
            assert "converged: 1" in content
            # Suppressed tools should NOT appear in per-tool unavailability
            # (tools_unavailable is empty, so no per-tool row for it)
            lines = content.split("\n")
            avail_section = False
            for line in lines:
                if "### Gate Availability" in line:
                    avail_section = True
                if avail_section and line.startswith("| mcp__brutalist__roast"):
                    # Should not appear in per-tool table since it was
                    # suppressed (not in tools_unavailable)
                    assert False, (
                        "Suppressed tool should not appear in per-tool "
                        "availability table"
                    )
                if avail_section and line.startswith("##") and "Gate" not in line:
                    break
        finally:
            telemetry._log = old

    def test_rework_section(self, tmp_path: Path) -> None:
        """DB-18: cycle.rework events produce a Rework table."""
        old = telemetry._log
        try:
            init("test-rw", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 1000
            event(
                "cycle.rework", milestone="m1", cycle_num=3,
                from_step="ASSESS", to_step="EXECUTE", phase="impl",
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Rework" in content
            assert "ASSESS" in content
            assert "impl" in content
        finally:
            telemetry._log = old

    def test_escalation_section(self, tmp_path: Path) -> None:
        """DB-18: escalation.created events produce an Escalations table."""
        old = telemetry._log
        try:
            init("test-esc", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 1000
            event(
                "escalation.created", milestone="m1", cycle_num=5,
                classification="validation_failure", severity="blocking",
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Escalations" in content
            assert "validation_failure" in content
            assert "blocking" in content
        finally:
            telemetry._log = old

    def test_no_db18_sections_when_no_events(self, tmp_path: Path) -> None:
        """DB-18 sections absent when no DB-18 events were emitted."""
        old = telemetry._log
        try:
            self._populate_log(tmp_path)
            write_milestone_summary(tmp_path, "auth", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "auth" / "metrics.md"
            ).read_text()
            assert "## Quality Gate" not in content
            assert "## Rework" not in content
            assert "## Escalations" not in content
        finally:
            telemetry._log = old


class TestExtractTaskData:
    """Per-task data extraction from telemetry records."""

    @staticmethod
    def _make_start(
        task_id: str, description: str, milestone: str, cycle_num: int, t_s: float,
    ) -> dict[str, Any]:
        return {
            "event": "agent.start",
            "milestone": milestone,
            "cycle_num": cycle_num,
            "task_id": task_id,
            "description": description,
            "wall": "2026-04-04T00:00:00+00:00",
            "t_s": t_s,
        }

    @staticmethod
    def _make_end(
        task_id: str, milestone: str, cycle_num: int, t_s: float,
        status: str = "completed", total_tokens: int = 1000, tool_uses: int = 5,
    ) -> dict[str, Any]:
        return {
            "event": "agent.end",
            "milestone": milestone,
            "cycle_num": cycle_num,
            "task_id": task_id,
            "status": status,
            "total_tokens": total_tokens,
            "tool_uses": tool_uses,
            "wall": "2026-04-04T00:01:00+00:00",
            "t_s": t_s,
        }

    def test_normal_completion_two_tasks(self) -> None:
        """Two tasks, both with start and end, produce correct data."""
        records = [
            self._make_start("a1", "implement login", "auth", 2, 10.0),
            self._make_start("a2", "write tests", "auth", 2, 10.5),
            self._make_end("a1", "auth", 2, 25.0, total_tokens=15000, tool_uses=12),
            self._make_end("a2", "auth", 2, 22.0, total_tokens=8000, tool_uses=5),
        ]
        result = extract_task_data(records, "auth")
        assert len(result) == 2
        # Sorted by (cycle_num, task_name): "implement login" < "write tests"
        assert result[0]["task_name"] == "implement login"
        assert result[0]["task_id"] == "a1"
        assert result[0]["cycle_num"] == 2
        assert result[0]["duration_s"] == 15.0
        assert result[0]["tokens"] == 15000
        assert result[0]["tool_uses"] == 12
        assert result[0]["status"] == "completed"

        assert result[1]["task_name"] == "write tests"
        assert result[1]["task_id"] == "a2"
        assert result[1]["duration_s"] == 11.5
        assert result[1]["tokens"] == 8000
        assert result[1]["tool_uses"] == 5
        assert result[1]["status"] == "completed"

    def test_orphaned_task(self) -> None:
        """Task with agent.start but no agent.end gets status='orphaned'."""
        records = [
            self._make_start("a1", "implement login", "auth", 1, 5.0),
            self._make_start("a2", "doomed agent", "auth", 1, 5.5),
            self._make_end("a1", "auth", 1, 20.0),
        ]
        result = extract_task_data(records, "auth")
        assert len(result) == 2
        orphan = [r for r in result if r["task_id"] == "a2"][0]
        assert orphan["status"] == "orphaned"
        assert orphan["duration_s"] == 0.0
        assert orphan["tokens"] == 0
        assert orphan["tool_uses"] == 0
        assert orphan["task_name"] == "doomed agent"

    def test_multi_cycle_tasks(self) -> None:
        """Same task name in different cycles treated as separate entries."""
        records = [
            self._make_start("c1-a", "build API", "m1", 1, 1.0),
            self._make_end("c1-a", "m1", 1, 10.0, total_tokens=5000, tool_uses=3),
            self._make_start("c2-a", "build API", "m1", 2, 20.0),
            self._make_end("c2-a", "m1", 2, 35.0, total_tokens=7000, tool_uses=8),
        ]
        result = extract_task_data(records, "m1")
        assert len(result) == 2
        # Sorted by cycle_num
        assert result[0]["cycle_num"] == 1
        assert result[0]["duration_s"] == 9.0
        assert result[0]["tokens"] == 5000
        assert result[1]["cycle_num"] == 2
        assert result[1]["duration_s"] == 15.0
        assert result[1]["tokens"] == 7000

    def test_empty_records(self) -> None:
        """Empty records list returns empty result."""
        assert extract_task_data([], "auth") == []

    def test_filters_by_milestone(self) -> None:
        """Records from a different milestone are excluded."""
        records = [
            self._make_start("a1", "implement login", "auth", 1, 1.0),
            self._make_end("a1", "auth", 1, 10.0, total_tokens=5000, tool_uses=3),
            self._make_start("b1", "billing task", "billing", 1, 2.0),
            self._make_end("b1", "billing", 1, 15.0, total_tokens=9000, tool_uses=7),
        ]
        result = extract_task_data(records, "auth")
        assert len(result) == 1
        assert result[0]["task_name"] == "implement login"
        assert result[0]["tokens"] == 5000

    def test_failed_task_status(self) -> None:
        """Task with status='failed' in agent.end is preserved."""
        records = [
            self._make_start("a1", "flaky task", "m1", 1, 1.0),
            self._make_end("a1", "m1", 1, 5.0, status="failed", total_tokens=2000),
        ]
        result = extract_task_data(records, "m1")
        assert len(result) == 1
        assert result[0]["status"] == "failed"
        assert result[0]["duration_s"] == 4.0
        assert result[0]["tokens"] == 2000

    def test_non_agent_records_ignored(self) -> None:
        """Records that are not agent.start/agent.end are ignored."""
        records = [
            {"event": "session.start", "session_id": "abc", "t_s": 0.0},
            {"span": "cycle", "milestone": "auth", "cycle_num": 1, "duration_ms": 5000},
            self._make_start("a1", "implement login", "auth", 1, 1.0),
            {"event": "crash", "milestone": "auth", "cycle_num": 1},
            self._make_end("a1", "auth", 1, 10.0),
        ]
        result = extract_task_data(records, "auth")
        assert len(result) == 1
        assert result[0]["task_name"] == "implement login"

    def test_sort_order_cycle_then_name(self) -> None:
        """Results sorted by cycle_num first, then task_name alphabetically."""
        records = [
            self._make_start("z1", "zebra task", "m1", 1, 1.0),
            self._make_end("z1", "m1", 1, 5.0),
            self._make_start("a1", "alpha task", "m1", 1, 2.0),
            self._make_end("a1", "m1", 1, 6.0),
            self._make_start("b2", "beta task", "m1", 2, 10.0),
            self._make_end("b2", "m1", 2, 15.0),
        ]
        result = extract_task_data(records, "m1")
        assert len(result) == 3
        assert result[0]["task_name"] == "alpha task"
        assert result[0]["cycle_num"] == 1
        assert result[1]["task_name"] == "zebra task"
        assert result[1]["cycle_num"] == 1
        assert result[2]["task_name"] == "beta task"
        assert result[2]["cycle_num"] == 2

    def test_end_without_start(self) -> None:
        """agent.end without agent.start handled gracefully (uses task_id as name)."""
        records = [
            self._make_end("orphan-end", "m1", 1, 10.0, total_tokens=3000, tool_uses=2),
        ]
        result = extract_task_data(records, "m1")
        assert len(result) == 1
        assert result[0]["task_name"] == "orphan-end"
        assert result[0]["tokens"] == 3000
        assert result[0]["duration_s"] == 0.0


# ---------------------------------------------------------------------------
# Topology and Per-Task Data sections in metrics.md
# ---------------------------------------------------------------------------

# A compose.py with a gather group (width=2) for testing topology sections.
_GATHER_COMPOSE = """\
class InfraResult: ...
class ModelResult: ...
class IntegrationResult: ...
class TestResult: ...

async def build_infrastructure() -> InfraResult:
    \"\"\"Build shard infrastructure.\"\"\"

async def extend_models() -> ModelResult:
    \"\"\"Extend data models.\"\"\"

async def implement_controls(
    infra: InfraResult, models: ModelResult,
) -> IntegrationResult:
    \"\"\"Implement runtime controls.\"\"\"

async def integrate_protocol(
    controls: IntegrationResult,
) -> TestResult:
    \"\"\"Integrate assess protocol.\"\"\"

async def execute():
    infra, models = await gather(
        build_infrastructure(),
        extend_models(),
    )
    controls = await implement_controls(infra, models)
    result = await integrate_protocol(controls)
"""

# A linear compose.py (width=1, no gather).
_LINEAR_COMPOSE = """\
class StepA: ...
class StepB: ...
class StepC: ...

async def do_first() -> StepA:
    \"\"\"First step.\"\"\"

async def do_second(a: StepA) -> StepB:
    \"\"\"Second step.\"\"\"

async def do_third(b: StepB) -> StepC:
    \"\"\"Third step.\"\"\"

async def execute():
    a = await do_first()
    b = await do_second(a)
    c = await do_third(b)
"""


class TestTopologySection:
    """## Topology section in metrics.md."""

    def _setup_compose(self, tmp_path: Path, milestone: str, source: str) -> None:
        compose_dir = tmp_path / ".clou" / "milestones" / milestone
        compose_dir.mkdir(parents=True, exist_ok=True)
        (compose_dir / "compose.py").write_text(source, encoding="utf-8")

    def test_topology_section_with_gather(self, tmp_path: Path) -> None:
        """Topology section emitted when compose.py exists with tasks."""
        old = telemetry._log
        try:
            init("test-topo", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 500
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Topology" in content
            assert "width: 2" in content
            assert "depth: 3" in content
            assert "layer_count: 3" in content
            assert "gather_groups: [2]" in content
            assert "layers:" in content
            # Layers should be a JSON list of lists.
            assert '["build_infrastructure", "extend_models"]' in content
        finally:
            telemetry._log = old

    def test_topology_section_linear(self, tmp_path: Path) -> None:
        """Topology section for a linear (no gather) compose.py."""
        old = telemetry._log
        try:
            init("test-topo-lin", tmp_path)
            self._setup_compose(tmp_path, "m1", _LINEAR_COMPOSE)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 500
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Topology" in content
            assert "width: 1" in content
            assert "depth: 3" in content
            assert "gather_groups: []" in content
        finally:
            telemetry._log = old

    def test_no_topology_without_compose(self, tmp_path: Path) -> None:
        """Topology section absent when no compose.py exists."""
        old = telemetry._log
        try:
            init("test-no-topo", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 500
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Topology" not in content
        finally:
            telemetry._log = old

    def test_topology_key_value_format(self, tmp_path: Path) -> None:
        """Topology section uses key: value format (grep-friendly)."""
        old = telemetry._log
        try:
            init("test-kv", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 500
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            # Each topology line should be parseable as "key: value"
            topo_start = content.index("## Topology")
            # Find the next section heading or end of file
            rest = content[topo_start:]
            topo_lines = []
            for line in rest.split("\n")[2:]:  # skip "## Topology" and blank
                if line.startswith("##") or line == "":
                    break
                topo_lines.append(line)
            assert len(topo_lines) == 5  # width, depth, layer_count, gather_groups, layers
            for line in topo_lines:
                assert ": " in line, f"Expected key: value format, got: {line}"
        finally:
            telemetry._log = old


class TestPerTaskDataSection:
    """## Per-Task Data section in metrics.md."""

    def _setup_compose(self, tmp_path: Path, milestone: str, source: str) -> None:
        compose_dir = tmp_path / ".clou" / "milestones" / milestone
        compose_dir.mkdir(parents=True, exist_ok=True)
        (compose_dir / "compose.py").write_text(source, encoding="utf-8")

    def test_per_task_table_with_layers(self, tmp_path: Path) -> None:
        """Per-Task Data table includes layer info from topology."""
        old = telemetry._log
        try:
            init("test-ptd", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            # Emit agents matching compose.py task names.
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a1", description="build_infrastructure", t_s=10.0)
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a2", description="extend_models", t_s=10.5)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a1", status="completed", total_tokens=12340, tool_uses=8, t_s=55.0)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a2", status="completed", total_tokens=10220, tool_uses=6, t_s=48.5)
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a3", description="implement_controls", t_s=56.0)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a3", status="completed", total_tokens=18450, tool_uses=12, t_s=118.0)
            with span("cycle", milestone="m1", cycle_num=2, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 50000
                c["output_tokens"] = 10000
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Per-Task Data" in content
            assert "| Layer | Task | Duration | Tokens | Tools | Status |" in content

            # Tasks in layer 0 should come before layer 1.
            topo_idx = content.index("## Per-Task Data")
            table_content = content[topo_idx:]
            bi_pos = table_content.index("build_infrastructure")
            em_pos = table_content.index("extend_models")
            ic_pos = table_content.index("implement_controls")
            # Layer 0 tasks before layer 1.
            assert bi_pos < ic_pos
            assert em_pos < ic_pos

            # Layer numbers present.
            assert "| 0 " in table_content
            assert "| 1 " in table_content
        finally:
            telemetry._log = old

    def test_per_task_table_sorted_by_layer_then_name(self, tmp_path: Path) -> None:
        """Per-Task Data sorted by (layer, task_name) -- deterministic."""
        old = telemetry._log
        try:
            init("test-sort", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            # Emit agents in reverse order.
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a2", description="extend_models", t_s=1.0)
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a1", description="build_infrastructure", t_s=2.0)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a2", status="completed", total_tokens=1000, tool_uses=3, t_s=10.0)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a1", status="completed", total_tokens=2000, tool_uses=5, t_s=12.0)
            with span("cycle", milestone="m1", cycle_num=2, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            ptd_start = content.index("## Per-Task Data")
            rest = content[ptd_start:]
            # Both are layer 0, alphabetical: build_infrastructure < extend_models
            bi_pos = rest.index("build_infrastructure")
            em_pos = rest.index("extend_models")
            assert bi_pos < em_pos
        finally:
            telemetry._log = old

    def test_per_task_without_topology(self, tmp_path: Path) -> None:
        """Per-Task Data with dash layer when no compose.py exists."""
        old = telemetry._log
        try:
            init("test-ptd-notopo", tmp_path)
            # No compose.py -- no topology.
            event("agent.start", milestone="m1", cycle_num=1,
                  task_id="a1", description="some task", t_s=1.0)
            event("agent.end", milestone="m1", cycle_num=1,
                  task_id="a1", status="completed", total_tokens=5000, tool_uses=3, t_s=10.0)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Per-Task Data" in content
            # Layer column should show em dash for unknown layer.
            assert "\u2014" in content  # em dash
        finally:
            telemetry._log = old

    def test_no_per_task_without_agents(self, tmp_path: Path) -> None:
        """Per-Task Data absent when no agent events exist."""
        old = telemetry._log
        try:
            init("test-no-ptd", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 500
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Per-Task Data" not in content
        finally:
            telemetry._log = old

    def test_duration_uses_fmt_duration(self, tmp_path: Path) -> None:
        """Per-task durations formatted via _fmt_duration (seconds -> human)."""
        old = telemetry._log
        try:
            init("test-dur", tmp_path)
            # Agent runs for 90 seconds (1m 30s).
            event("agent.start", milestone="m1", cycle_num=1,
                  task_id="a1", description="long task", t_s=10.0)
            event("agent.end", milestone="m1", cycle_num=1,
                  task_id="a1", status="completed", total_tokens=5000, tool_uses=3, t_s=100.0)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "1m 30s" in content
        finally:
            telemetry._log = old

    def test_tokens_formatted_with_commas(self, tmp_path: Path) -> None:
        """Token counts formatted with thousands separators."""
        old = telemetry._log
        try:
            init("test-tok", tmp_path)
            event("agent.start", milestone="m1", cycle_num=1,
                  task_id="a1", description="big task", t_s=1.0)
            event("agent.end", milestone="m1", cycle_num=1,
                  task_id="a1", status="completed", total_tokens=12340, tool_uses=8, t_s=10.0)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "12,340" in content
        finally:
            telemetry._log = old


class TestSectionOrdering:
    """New sections placed between Agents and Quality Gate."""

    def _setup_compose(self, tmp_path: Path, milestone: str, source: str) -> None:
        compose_dir = tmp_path / ".clou" / "milestones" / milestone
        compose_dir.mkdir(parents=True, exist_ok=True)
        (compose_dir / "compose.py").write_text(source, encoding="utf-8")

    def test_section_order_topology_before_quality_gate(self, tmp_path: Path) -> None:
        """Topology and Per-Task Data appear between Agents and Quality Gate."""
        old = telemetry._log
        try:
            init("test-order", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            # Agents
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a1", description="build_infrastructure", t_s=1.0)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a1", status="completed", total_tokens=5000, tool_uses=3, t_s=10.0)
            # Cycle
            with span("cycle", milestone="m1", cycle_num=2, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            # Quality gate
            event(
                "quality_gate.result", milestone="m1", cycle_num=3,
                tools_invoked=["roast"], tools_unavailable=[],
                finding_count=2,
            )
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            agents_pos = content.index("## Agents")
            topo_pos = content.index("## Topology")
            ptd_pos = content.index("## Per-Task Data")
            qg_pos = content.index("## Quality Gate")
            assert agents_pos < topo_pos < ptd_pos < qg_pos
        finally:
            telemetry._log = old

    def test_existing_sections_preserved(self, tmp_path: Path) -> None:
        """Adding topology does not remove existing sections."""
        old = telemetry._log
        try:
            init("test-preserve", tmp_path)
            self._setup_compose(tmp_path, "m1", _GATHER_COMPOSE)
            # Full set of events.
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a1", description="build_infrastructure", t_s=1.0)
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a1", status="completed", total_tokens=5000, tool_uses=3, t_s=10.0)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 1000
                c["output_tokens"] = 500
            with span("cycle", milestone="m1", cycle_num=2, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            event("crash", milestone="m1", cycle_num=1, attempt=1)
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            # All expected sections present.
            assert "# Metrics: m1" in content
            assert "## Cycles" in content
            assert "## Agents" in content
            assert "## Topology" in content
            assert "## Per-Task Data" in content
            assert "## Incidents" in content
        finally:
            telemetry._log = old


# ---------------------------------------------------------------------------
# Cognitive Load section in metrics.md (DB-20 Step 2)
# ---------------------------------------------------------------------------


class TestCognitiveLoadSection:
    """## Cognitive Load section renders from cognitive telemetry events."""

    def test_cognitive_load_section_renders_all_three_events(
        self, tmp_path: Path,
    ) -> None:
        """All three event types produce a complete Cognitive Load table row."""
        old = telemetry._log
        try:
            init("test-cog-full", tmp_path)
            with span("cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 20000
                c["output_tokens"] = 2000
            event(
                "read_set.composition", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", file_count=2,
                files=["assess_summary.md", "decisions.md"],
            )
            event(
                "read_set.reference_density", milestone="m1", cycle_num=3,
                density=0.85, referenced_count=2, total_count=2,
            )
            event(
                "cognitive.compositional_span", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", span=2,
                chain=["compose.py", "execution.md"], pre_composed=True,
            )
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Cognitive Load" in content
            assert "| Cycle | Type | Read Set Size | Ref Density | Comp Span | Pre-composed |" in content
            # Row for cycle 3
            assert "| 3 " in content
            assert "ASSESS" in content
            assert "| 2 " in content
            assert "| 0.85 " in content
            assert "| yes |" in content
        finally:
            telemetry._log = old

    def test_cognitive_load_missing_compositional_span(
        self, tmp_path: Path,
    ) -> None:
        """When compositional_span is missing, renders '-' for span columns."""
        old = telemetry._log
        try:
            init("test-cog-partial", tmp_path)
            with span("cycle", milestone="m1", cycle_num=5, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 15000
                c["output_tokens"] = 1500
            event(
                "read_set.composition", milestone="m1", cycle_num=5,
                cycle_type="ASSESS", file_count=4,
                files=["a.md", "b.md", "c.md", "d.md"],
            )
            event(
                "read_set.reference_density", milestone="m1", cycle_num=5,
                density=0.75, referenced_count=3, total_count=4,
            )
            # No cognitive.compositional_span event
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Cognitive Load" in content
            assert "| 5 " in content
            assert "| 4 " in content
            assert "| 0.75 " in content
            # Span and pre_composed should show dashes
            assert "| - |" in content
        finally:
            telemetry._log = old

    def test_cognitive_load_only_assess_cycles(
        self, tmp_path: Path,
    ) -> None:
        """Only ASSESS-cycle events appear; PLAN/EXECUTE events are excluded."""
        old = telemetry._log
        try:
            init("test-cog-assess-only", tmp_path)
            # PLAN cycle with composition event (should be excluded)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 10000
                c["output_tokens"] = 1000
            event(
                "read_set.composition", milestone="m1", cycle_num=1,
                cycle_type="PLAN", file_count=6,
                files=["a", "b", "c", "d", "e", "f"],
            )
            # ASSESS cycle (should be included)
            with span("cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 20000
                c["output_tokens"] = 2000
            event(
                "read_set.composition", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", file_count=2,
                files=["assess_summary.md", "decisions.md"],
            )
            event(
                "cognitive.compositional_span", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", span=2,
                chain=["compose.py", "execution.md"], pre_composed=True,
            )
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Cognitive Load" in content
            # Only cycle 3 should appear, not cycle 1
            cog_start = content.index("## Cognitive Load")
            # Find the next section or end
            cog_section = content[cog_start:]
            next_section = cog_section.find("\n## ", 1)
            if next_section > 0:
                cog_section = cog_section[:next_section]
            assert "| 3 " in cog_section
            assert "| 1 " not in cog_section
        finally:
            telemetry._log = old

    def test_cognitive_load_no_events_no_section(
        self, tmp_path: Path,
    ) -> None:
        """No cognitive events at all means no Cognitive Load section."""
        old = telemetry._log
        try:
            init("test-cog-none", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Cognitive Load" not in content
        finally:
            telemetry._log = old

    def test_existing_sections_unaffected(
        self, tmp_path: Path,
    ) -> None:
        """Adding cognitive load section does not break existing sections."""
        old = telemetry._log
        try:
            init("test-cog-noregress", tmp_path)
            # Full realistic scenario with agents and cognitive events
            event("agent.start", milestone="m1", cycle_num=2,
                  task_id="a1", description="implement login")
            event("agent.end", milestone="m1", cycle_num=2,
                  task_id="a1", status="completed", total_tokens=15000, tool_uses=12)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 12000
                c["output_tokens"] = 3000
            with span("cycle", milestone="m1", cycle_num=2, cycle_type="EXECUTE") as c:
                c["outcome"] = "ASSESS"
                c["input_tokens"] = 35000
                c["output_tokens"] = 6000
            with span("cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 20000
                c["output_tokens"] = 2000
            event(
                "read_set.composition", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", file_count=2,
                files=["assess_summary.md", "decisions.md"],
            )
            event(
                "quality_gate.result", milestone="m1", cycle_num=3,
                tools_invoked=["roast"], tools_unavailable=[],
                finding_count=2,
            )
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            # Existing sections still present
            assert "# Metrics: m1" in content
            assert "## Cycles" in content
            assert "## Agents" in content
            assert "## Quality Gate" in content
            # New section also present
            assert "## Cognitive Load" in content
        finally:
            telemetry._log = old

    def test_cognitive_load_multiple_assess_cycles(
        self, tmp_path: Path,
    ) -> None:
        """Multiple ASSESS cycles produce multiple rows sorted by cycle."""
        old = telemetry._log
        try:
            init("test-cog-multi", tmp_path)
            # Two ASSESS cycles
            with span("cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 20000
                c["output_tokens"] = 2000
            with span("cycle", milestone="m1", cycle_num=5, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 18000
                c["output_tokens"] = 1800
            event(
                "read_set.composition", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", file_count=4,
                files=["a.md", "b.md", "c.md", "d.md"],
            )
            event(
                "read_set.composition", milestone="m1", cycle_num=5,
                cycle_type="ASSESS", file_count=2,
                files=["assess_summary.md", "decisions.md"],
            )
            event(
                "cognitive.compositional_span", milestone="m1", cycle_num=3,
                cycle_type="ASSESS", span=4,
                chain=["a", "b", "c", "d"], pre_composed=False,
            )
            event(
                "cognitive.compositional_span", milestone="m1", cycle_num=5,
                cycle_type="ASSESS", span=2,
                chain=["assess_summary.md", "decisions.md"], pre_composed=True,
            )
            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Cognitive Load" in content
            # Both cycles present
            cog_start = content.index("## Cognitive Load")
            cog_section = content[cog_start:]
            next_section = cog_section.find("\n## ", 1)
            if next_section > 0:
                cog_section = cog_section[:next_section]
            assert "| 3 " in cog_section
            assert "| 5 " in cog_section
            # Cycle 3 row before cycle 5 row
            pos_3 = cog_section.index("| 3 ")
            pos_5 = cog_section.index("| 5 ")
            assert pos_3 < pos_5
            # Cycle 3 shows no pre-composed
            assert "| no |" in cog_section
            # Cycle 5 shows pre-composed
            assert "| yes |" in cog_section
        finally:
            telemetry._log = old


class TestPatternInfluenceSection:
    """M35: Pattern Influence section in metrics.md."""

    def test_section_renders_from_both_event_types(
        self, tmp_path: Path,
    ) -> None:
        """Full scenario: retrieval + influence events produce the section."""
        old = telemetry._log
        try:
            init("test-pi-full", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            event(
                "memory.patterns_retrieved", milestone="m1",
                cycle_num=1, cycle_type="PLAN", pattern_count=3,
                patterns=[
                    {"name": "decomposition-topology", "type": "decomposition", "description": "topology matters"},
                    {"name": "cycle-count-distribution", "type": "cost-calibration", "description": "cycle cost data"},
                    {"name": "validation-noise", "type": "debt", "description": "noisy validation"},
                ],
            )
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=1, cycle_type="PLAN",
                retrieved=["decomposition-topology", "cycle-count-distribution", "validation-noise"],
                referenced=["decomposition-topology", "cycle-count-distribution"],
                influence_ratio=0.67,
                match_details=[
                    {"pattern": "decomposition-topology", "match_type": "exact_name", "matched_phrase": "decomposition-topology"},
                    {"pattern": "cycle-count-distribution", "match_type": "key_phrase", "matched_phrase": "cycle cost data"},
                ],
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()

            # Section heading present
            assert "## Pattern Influence" in content

            # Per-cycle table
            assert "| Cycle | Type | Retrieved | Referenced | Influence |" in content
            assert "| 1 | PLAN | 3 | 2 | 0.67 |" in content

            # Patterns Retrieved sub-table
            assert "### Patterns Retrieved" in content
            assert "| decomposition-topology | decomposition | 1 |" in content
            assert "| cycle-count-distribution | cost-calibration | 1 |" in content
            assert "| validation-noise | debt | 1 |" in content

            # Patterns Referenced sub-table
            assert "### Patterns Referenced" in content
            assert "| decomposition-topology | 1 | exact_name |" in content
            assert "| cycle-count-distribution | 1 | key_phrase |" in content
        finally:
            telemetry._log = old

    def test_section_omitted_when_no_events(
        self, tmp_path: Path,
    ) -> None:
        """No retrieval/influence events -> no section at all."""
        old = telemetry._log
        try:
            init("test-pi-none", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Pattern Influence" not in content
            assert "### Patterns Retrieved" not in content
            assert "### Patterns Referenced" not in content
        finally:
            telemetry._log = old

    def test_multiple_cycles_ordered_by_cycle_num(
        self, tmp_path: Path,
    ) -> None:
        """Per-cycle rows are ordered by cycle number."""
        old = telemetry._log
        try:
            init("test-pi-multi", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            with span("cycle", milestone="m1", cycle_num=4, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            # Emit in reverse order to test sorting
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=4, cycle_type="ASSESS",
                retrieved=["pat-a", "pat-b"],
                referenced=["pat-a"],
                influence_ratio=0.50,
                match_details=[
                    {"pattern": "pat-a", "match_type": "exact_name", "matched_phrase": "pat-a"},
                ],
            )
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=1, cycle_type="PLAN",
                retrieved=["pat-a", "pat-b", "pat-c"],
                referenced=["pat-a", "pat-b"],
                influence_ratio=0.67,
                match_details=[
                    {"pattern": "pat-a", "match_type": "exact_name", "matched_phrase": "pat-a"},
                    {"pattern": "pat-b", "match_type": "key_phrase", "matched_phrase": "some phrase"},
                ],
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()

            # Find the Pattern Influence section
            pi_start = content.index("## Pattern Influence")
            pi_section = content[pi_start:]
            # Cycle 1 row appears before cycle 4 row
            pos_1 = pi_section.index("| 1 | PLAN")
            pos_4 = pi_section.index("| 4 | ASSESS")
            assert pos_1 < pos_4
        finally:
            telemetry._log = old

    def test_aggregate_patterns_across_cycles(
        self, tmp_path: Path,
    ) -> None:
        """Aggregate tables accumulate patterns across multiple cycles."""
        old = telemetry._log
        try:
            init("test-pi-agg", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            with span("cycle", milestone="m1", cycle_num=4, cycle_type="ASSESS") as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            # Retrieval events for two cycles
            event(
                "memory.patterns_retrieved", milestone="m1",
                cycle_num=1, cycle_type="PLAN", pattern_count=2,
                patterns=[
                    {"name": "pat-a", "type": "decomp", "description": "desc a"},
                    {"name": "pat-b", "type": "cost", "description": "desc b"},
                ],
            )
            event(
                "memory.patterns_retrieved", milestone="m1",
                cycle_num=4, cycle_type="ASSESS", pattern_count=2,
                patterns=[
                    {"name": "pat-a", "type": "decomp", "description": "desc a"},
                    {"name": "pat-c", "type": "debt", "description": "desc c"},
                ],
            )
            # Influence events
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=1, cycle_type="PLAN",
                retrieved=["pat-a", "pat-b"],
                referenced=["pat-a"],
                influence_ratio=0.50,
                match_details=[
                    {"pattern": "pat-a", "match_type": "exact_name", "matched_phrase": "pat-a"},
                ],
            )
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=4, cycle_type="ASSESS",
                retrieved=["pat-a", "pat-c"],
                referenced=["pat-a"],
                influence_ratio=0.50,
                match_details=[
                    {"pattern": "pat-a", "match_type": "key_phrase", "matched_phrase": "desc a"},
                ],
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()

            # Patterns Retrieved: pat-a appears in cycles 1, 4
            assert "| pat-a | decomp | 1, 4 |" in content
            # pat-b only in cycle 1
            assert "| pat-b | cost | 1 |" in content
            # pat-c only in cycle 4
            assert "| pat-c | debt | 4 |" in content

            # Patterns Referenced: pat-a referenced in both cycles with both match types
            assert "### Patterns Referenced" in content
            # pat-a in cycles 1, 4 with both match types
            assert "| pat-a | 1, 4 | exact_name, key_phrase |" in content
        finally:
            telemetry._log = old

    def test_no_json_dumps_in_output(
        self, tmp_path: Path,
    ) -> None:
        """Output is human-readable markdown, not raw JSON."""
        old = telemetry._log
        try:
            init("test-pi-readable", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            event(
                "memory.patterns_retrieved", milestone="m1",
                cycle_num=1, cycle_type="PLAN", pattern_count=1,
                patterns=[
                    {"name": "test-pat", "type": "test", "description": "a test pattern"},
                ],
            )
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=1, cycle_type="PLAN",
                retrieved=["test-pat"],
                referenced=["test-pat"],
                influence_ratio=1.0,
                match_details=[
                    {"pattern": "test-pat", "match_type": "exact_name", "matched_phrase": "test-pat"},
                ],
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()

            # Extract just the Pattern Influence section
            pi_start = content.index("## Pattern Influence")
            next_h2 = content.find("\n## ", pi_start + 1)
            if next_h2 > 0:
                pi_section = content[pi_start:next_h2]
            else:
                pi_section = content[pi_start:]

            # No JSON artifacts in the section
            assert "{" not in pi_section
            assert "}" not in pi_section
            assert "[" not in pi_section
            assert "]" not in pi_section
        finally:
            telemetry._log = old

    def test_retrieval_only_without_influence(
        self, tmp_path: Path,
    ) -> None:
        """Only retrieval events (no influence) still produces the section."""
        old = telemetry._log
        try:
            init("test-pi-ret-only", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            event(
                "memory.patterns_retrieved", milestone="m1",
                cycle_num=1, cycle_type="PLAN", pattern_count=1,
                patterns=[
                    {"name": "solo-pat", "type": "solo", "description": "solo"},
                ],
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()

            assert "## Pattern Influence" in content
            assert "### Patterns Retrieved" in content
            assert "| solo-pat | solo | 1 |" in content
            # No per-cycle influence table or referenced table
            assert "### Patterns Referenced" not in content
        finally:
            telemetry._log = old

    def test_pattern_names_with_pipes_and_newlines_are_sanitized(
        self, tmp_path: Path,
    ) -> None:
        """F11: pipe and newline chars in pattern fields do not corrupt tables."""
        old = telemetry._log
        try:
            init("test-pi-sanitize", tmp_path)
            with span("cycle", milestone="m1", cycle_num=1, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000
            event(
                "memory.patterns_retrieved", milestone="m1",
                cycle_num=1, cycle_type="PLAN", pattern_count=1,
                patterns=[
                    {
                        "name": "bad|name\nhere",
                        "type": "cost|type\nbroken",
                        "description": "desc with | and \n inside",
                    },
                ],
            )
            event(
                "memory.pattern_influence", milestone="m1",
                cycle_num=1, cycle_type="PLAN",
                retrieved=["bad|name\nhere"],
                referenced=["bad|name\nhere"],
                influence_ratio=1.0,
                match_details=[
                    {
                        "pattern": "bad|name\nhere",
                        "match_type": "exact|name\ntype",
                        "matched_phrase": "bad|name\nhere",
                    },
                ],
            )
            write_milestone_summary(tmp_path, "m1", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()

            # The section should render without corruption
            assert "## Pattern Influence" in content

            # Pipes and newlines must be escaped/stripped in table cells
            assert r"bad\|name here" in content
            assert r"cost\|type broken" in content

            # Verify table structure is not broken: each data row in the
            # Pattern Influence section should have exactly the expected
            # number of pipe-delimiters for its table format.
            pi_start = content.index("## Pattern Influence")
            pi_section = content[pi_start:]
            for line in pi_section.splitlines():
                if line.startswith("|") and not line.startswith("|--"):
                    # Count unescaped pipes (not preceded by backslash)
                    import re as _re
                    unescaped = _re.findall(r"(?<!\\)\|", line)
                    # Header/data rows have the same pipe count per table
                    # (minimum 4 for 3-column tables, minimum 6 for 5-column)
                    assert len(unescaped) >= 4, (
                        f"Table row has too few columns: {line!r}"
                    )
        finally:
            telemetry._log = old


class TestPatternInfluenceIntegration:
    """F14: Integration test using production _filter_memory_for_cycle path."""

    def test_production_retrieval_flows_through_to_metrics(
        self, tmp_path: Path,
    ) -> None:
        """Call _filter_memory_for_cycle with real memory, then verify metrics."""
        from clou.recovery_checkpoint import _filter_memory_for_cycle

        # Set up real memory.md with active patterns
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones"
        ms_dir.mkdir(parents=True)
        memory_path = clou_dir / "memory.md"
        memory_path.write_text(
            "# Operational Memory\n"
            "\n"
            "## Patterns\n"
            "\n"
            "### decomposition-topology\n"
            "type: decomposition\n"
            "observed: m01-setup\n"
            "reinforced: 2\n"
            "last_active: m02-auth\n"
            "status: active\n"
            "Topology-aware decomposition reduces cycle count.\n"
            "\n"
            "### cycle-cost-calibration\n"
            "type: cost-calibration\n"
            "observed: m01-setup\n"
            "reinforced: 1\n"
            "last_active: m01-setup\n"
            "status: active\n"
            "Track cycle costs for budget estimation.\n",
            encoding="utf-8",
        )

        old = telemetry._log
        try:
            init("test-pi-integration", tmp_path)

            # Emit a cycle span so write_milestone_summary has cycle data
            with span("cycle", milestone="test-ms", cycle_num=3, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000

            # Call production code path -- this emits the real
            # memory.patterns_retrieved event via telemetry.event()
            result = _filter_memory_for_cycle(
                memory_path, "PLAN", ms_dir,
                milestone="test-ms", cycle_num=3,
            )
            assert result is not None, "Expected patterns to pass filtering"

            # Now render metrics and verify the renderer consumed the event
            write_milestone_summary(tmp_path, "test-ms", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "test-ms" / "metrics.md"
            ).read_text()

            # Section should be present
            assert "## Pattern Influence" in content
            assert "### Patterns Retrieved" in content

            # Both patterns should appear with cycle_num 3
            assert "| decomposition-topology | decomposition | 3 |" in content
            assert "| cycle-cost-calibration | cost-calibration | 3 |" in content

            # Verify cycle_num appears in the Cycles Retrieved column
            pi_start = content.index("### Patterns Retrieved")
            pi_section = content[pi_start:]
            # Every data row should contain "3" as the cycle number
            for line in pi_section.splitlines():
                if line.startswith("| ") and not line.startswith("| Pattern") and not line.startswith("|--"):
                    assert "3" in line, (
                        f"cycle_num 3 missing from row: {line!r}"
                    )
        finally:
            telemetry._log = old

    def test_influence_pipeline_flows_through_to_metrics(
        self, tmp_path: Path,
    ) -> None:
        """RF5: retrieval -> scan_pattern_references -> metrics rendering."""
        from clou.recovery_checkpoint import _filter_memory_for_cycle
        from clou.recovery_compaction import scan_pattern_references

        # Set up real memory.md with active patterns
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones"
        ms_dir.mkdir(parents=True)
        memory_path = clou_dir / "memory.md"
        memory_path.write_text(
            "# Operational Memory\n"
            "\n"
            "## Patterns\n"
            "\n"
            "### decomposition-topology\n"
            "type: decomposition\n"
            "observed: m01-setup\n"
            "reinforced: 2\n"
            "last_active: m02-auth\n"
            "status: active\n"
            "Topology-aware decomposition reduces cycle count.\n"
            "\n"
            "### cycle-cost-calibration\n"
            "type: cost-calibration\n"
            "observed: m01-setup\n"
            "reinforced: 1\n"
            "last_active: m01-setup\n"
            "status: active\n"
            "Track cycle costs for budget estimation.\n",
            encoding="utf-8",
        )

        old = telemetry._log
        try:
            init("test-pi-influence", tmp_path)

            # Emit a cycle span so write_milestone_summary has cycle data
            with span("cycle", milestone="test-ms", cycle_num=3, cycle_type="PLAN") as c:
                c["outcome"] = "EXECUTE"
                c["input_tokens"] = 5000
                c["output_tokens"] = 1000

            # Step 1: Call production retrieval path (emits
            # memory.patterns_retrieved event).
            filtered = _filter_memory_for_cycle(
                memory_path, "PLAN", ms_dir,
                milestone="test-ms", cycle_num=3,
            )
            assert filtered is not None, "Expected patterns to pass filtering"

            # Write filtered memory to a temp file for the scanner
            filtered_path = tmp_path / "filtered_memory.md"
            filtered_path.write_text(filtered, encoding="utf-8")

            # Step 2: Write decisions.md that references the patterns.
            # Use exact name match for one pattern and a key phrase
            # from the description for the other.
            decisions_dir = ms_dir / "test-ms"
            decisions_dir.mkdir(parents=True, exist_ok=True)
            decisions_path = decisions_dir / "decisions.md"
            decisions_path.write_text(
                "# Decisions\n"
                "\n"
                "## Cycle 3\n"
                "\n"
                "Applied decomposition-topology to split the milestone "
                "into smaller phases. Also considered cycle costs for "
                "budget estimation when planning.\n",
                encoding="utf-8",
            )

            # Step 3: Call scan_pattern_references with production args.
            # This emits memory.pattern_influence telemetry event.
            influence = scan_pattern_references(
                filtered_path,
                decisions_path,
                milestone="test-ms",
                cycle_num=3,
                cycle_type="PLAN",
            )
            assert influence is not None, "Expected influence data"
            assert len(influence["referenced"]) > 0, "Expected at least one reference"

            # Step 4: Re-render metrics and verify Patterns Referenced table.
            write_milestone_summary(tmp_path, "test-ms", "completed")
            content = (
                tmp_path / ".clou" / "milestones" / "test-ms" / "metrics.md"
            ).read_text()

            # Both tables should be present
            assert "### Patterns Retrieved" in content
            assert "### Patterns Referenced" in content

            # Verify Patterns Referenced table contains matched patterns
            ref_start = content.index("### Patterns Referenced")
            ref_section = content[ref_start:]

            # decomposition-topology matched by exact name
            assert "decomposition-topology" in ref_section
            # cycle-cost-calibration matched by key phrase
            assert "cycle-cost-calibration" in ref_section

            # Verify table structure has expected columns
            assert "| Pattern | Cycles | Match Type |" in ref_section

            # Verify match types appear in the table rows
            ref_lines = [
                ln for ln in ref_section.splitlines()
                if ln.startswith("| ") and not ln.startswith("| Pattern") and not ln.startswith("|--")
            ]
            assert len(ref_lines) >= 2, (
                f"Expected at least 2 referenced pattern rows, got {len(ref_lines)}"
            )
            # Every data row should reference cycle 3
            for line in ref_lines:
                assert "3" in line, f"cycle 3 missing from referenced row: {line!r}"
        finally:
            telemetry._log = old
