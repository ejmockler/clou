"""Tests for clou.telemetry — structured span log."""

from __future__ import annotations

import json
from pathlib import Path

from clou.telemetry import (
    SpanLog, init, read_log, span, event, write_milestone_summary, _fmt_duration,
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
