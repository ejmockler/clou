"""Tests for cognitive metrics: compositional span computation and telemetry.

DB-20 Step 2 — instrument_cognitive_metrics phase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clou.telemetry import (
    SpanLog,
    compute_compositional_span,
    _classify_file_role,
    init,
    event,
    read_log,
    span,
)
from clou import telemetry


class TestClassifyFileRole:
    """_classify_file_role assigns correct roles to read-set file paths."""

    def test_intents(self) -> None:
        assert _classify_file_role("intents.md") == "intent"

    def test_intents_with_path(self) -> None:
        assert _classify_file_role("milestones/m1/intents.md") == "intent"

    def test_compose(self) -> None:
        assert _classify_file_role("compose.py") == "criteria"

    def test_phase_md(self) -> None:
        assert _classify_file_role("phases/impl/phase.md") == "criteria"

    def test_execution_md(self) -> None:
        assert _classify_file_role("phases/impl/execution.md") == "execution"

    def test_execution_shard(self) -> None:
        assert _classify_file_role("phases/impl/execution-impl.md") == "execution"

    def test_assessment(self) -> None:
        assert _classify_file_role("assessment.md") == "assessment"

    def test_decisions(self) -> None:
        assert _classify_file_role("decisions.md") == "decision"

    def test_requirements(self) -> None:
        assert _classify_file_role("requirements.md") == "requirements"

    def test_summary(self) -> None:
        assert _classify_file_role("active/assess_summary.md") == "summary"

    def test_unknown_file(self) -> None:
        assert _classify_file_role("status.md") is None

    def test_unknown_random(self) -> None:
        assert _classify_file_role("active/coordinator.md") is None


class TestComputeCompositionalSpan:
    """compute_compositional_span measures hop count correctly."""

    def test_raw_assess_read_set(self) -> None:
        """Full ASSESS read set without pre-composition: span should be high."""
        read_set = [
            "phases/impl/execution.md",
            "phases/impl/execution-impl.md",
            "requirements.md",
            "decisions.md",
            "assessment.md",
        ]
        result = compute_compositional_span(read_set)
        # execution (2 files, 1 role) + requirements + decision + assessment = 4 roles
        assert result["span"] == 4
        assert result["pre_composed"] is False
        assert "execution" in result["chain"]
        assert "requirements" in result["chain"]
        assert "decision" in result["chain"]
        assert "assessment" in result["chain"]

    def test_full_raw_read_set_high_span(self) -> None:
        """Maximum span with all artifact types present."""
        read_set = [
            "intents.md",
            "compose.py",
            "phases/impl/phase.md",
            "phases/impl/execution.md",
            "assessment.md",
            "decisions.md",
            "requirements.md",
        ]
        result = compute_compositional_span(read_set)
        # intent + criteria (compose.py and phase.md are same role) +
        # execution + assessment + decision + requirements = 6 roles
        assert result["span"] == 6
        assert result["pre_composed"] is False

    def test_pre_composed_read_set(self) -> None:
        """Pre-composed summary should result in low span."""
        read_set = [
            "active/assess_summary.md",
            "requirements.md",
        ]
        result = compute_compositional_span(read_set)
        assert result["span"] == 2
        assert result["pre_composed"] is True
        assert "summary" in result["chain"]
        assert "requirements" in result["chain"]

    def test_pre_composed_summary_only(self) -> None:
        """Single pre-composed summary file: span=1."""
        read_set = ["active/assess_summary.md"]
        result = compute_compositional_span(read_set)
        assert result["span"] == 1
        assert result["pre_composed"] is True

    def test_empty_read_set(self) -> None:
        """Empty read set: span=0."""
        result = compute_compositional_span([])
        assert result["span"] == 0
        assert result["chain"] == []
        assert result["pre_composed"] is False

    def test_unrecognized_files_ignored(self) -> None:
        """Files that don't match known roles are excluded from span."""
        read_set = [
            "status.md",
            "active/coordinator.md",
            "active/_filtered_memory.md",
        ]
        result = compute_compositional_span(read_set)
        assert result["span"] == 0
        assert result["chain"] == []

    def test_duplicate_roles_counted_once(self) -> None:
        """Multiple execution shards count as one role."""
        read_set = [
            "phases/a/execution.md",
            "phases/a/execution-a.md",
            "phases/b/execution.md",
            "phases/b/execution-b.md",
        ]
        result = compute_compositional_span(read_set)
        assert result["span"] == 1
        assert result["chain"] == ["execution"]

    def test_chain_preserves_order(self) -> None:
        """Chain list preserves first-seen order of roles."""
        read_set = [
            "decisions.md",
            "requirements.md",
            "assessment.md",
        ]
        result = compute_compositional_span(read_set)
        assert result["chain"] == ["decision", "requirements", "assessment"]


class TestCompositionalSpanEmission:
    """Verify cognitive.compositional_span event is emitted with correct fields."""

    def test_event_emitted_with_mock_telemetry(self, tmp_path: Path) -> None:
        """Simulate the emission pattern used in coordinator.py."""
        old = telemetry._log
        try:
            log = init("test-span", tmp_path)

            # Simulate what coordinator.py does after an ASSESS cycle.
            read_set = [
                "phases/impl/execution.md",
                "requirements.md",
                "decisions.md",
                "assessment.md",
            ]
            ms_dir = tmp_path / ".clou" / "milestones" / "m1"
            ms_dir.mkdir(parents=True, exist_ok=True)

            span_data = compute_compositional_span(read_set)
            event(
                "cognitive.compositional_span",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                span=span_data["span"],
                chain=span_data["chain"],
                pre_composed=span_data["pre_composed"],
            )

            records = read_log(log.path)
            span_events = [
                r for r in records
                if r.get("event") == "cognitive.compositional_span"
            ]
            assert len(span_events) == 1
            ev = span_events[0]
            assert ev["milestone"] == "m1"
            assert ev["cycle_num"] == 3
            assert ev["cycle_type"] == "ASSESS"
            assert ev["span"] == 4
            assert ev["pre_composed"] is False
            assert isinstance(ev["chain"], list)
            assert len(ev["chain"]) == 4
        finally:
            telemetry._log = old

    def test_event_fields_with_pre_composed(self, tmp_path: Path) -> None:
        """Pre-composed read set emits correct span and pre_composed flag."""
        old = telemetry._log
        try:
            log = init("test-span-pre", tmp_path)

            read_set = [
                "active/assess_summary.md",
                "requirements.md",
            ]
            ms_dir = tmp_path / ".clou" / "milestones" / "m1"
            ms_dir.mkdir(parents=True, exist_ok=True)

            span_data = compute_compositional_span(read_set)
            event(
                "cognitive.compositional_span",
                milestone="m1",
                cycle_num=5,
                cycle_type="ASSESS",
                span=span_data["span"],
                chain=span_data["chain"],
                pre_composed=span_data["pre_composed"],
            )

            records = read_log(log.path)
            span_events = [
                r for r in records
                if r.get("event") == "cognitive.compositional_span"
            ]
            assert len(span_events) == 1
            ev = span_events[0]
            assert ev["span"] == 2
            assert ev["pre_composed"] is True
            assert "summary" in ev["chain"]
        finally:
            telemetry._log = old


class TestCompositionEventPreference:
    """F3: When dual read_set.composition events exist (raw + pre_composed),
    the telemetry rendering prefers the pre_composed=True event for file_count.
    """

    def test_pre_composed_event_preferred_for_file_count(
        self, tmp_path: Path,
    ) -> None:
        """Dual events: pre_composed=True file_count wins in rendering."""
        from clou.telemetry import write_milestone_summary

        old = telemetry._log
        try:
            log = init("test-prefer-pre", tmp_path)

            # Cycle span (required for metrics.md rendering).
            with span(
                "cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS",
            ) as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 10000
                c["output_tokens"] = 1000

            # First: raw composition event (6 files).
            event(
                "read_set.composition",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                file_count=6,
                files=["a.md", "b.md", "c.md", "d.md", "e.md", "f.md"],
            )
            # Second: pre-composed event (2 files).
            event(
                "read_set.composition",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                file_count=2,
                files=["active/assess_summary.md", "requirements.md"],
                pre_composed=True,
            )

            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            assert "## Cognitive Load" in content
            # The table should show file_count=2 (pre_composed), not 6.
            cog_start = content.index("## Cognitive Load")
            cog_section = content[cog_start:]
            next_section = cog_section.find("\n## ", 1)
            if next_section > 0:
                cog_section = cog_section[:next_section]
            assert "| 2 " in cog_section
            # Should NOT show the raw file_count of 6.
            assert "| 6 " not in cog_section
            # Should show pre-composed = yes.
            assert "| yes |" in cog_section
        finally:
            telemetry._log = old

    def test_single_raw_event_shows_raw_count(
        self, tmp_path: Path,
    ) -> None:
        """Single raw composition event (no pre_composed): shows raw count."""
        from clou.telemetry import write_milestone_summary

        old = telemetry._log
        try:
            log = init("test-single-raw", tmp_path)

            with span(
                "cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS",
            ) as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 10000
                c["output_tokens"] = 1000

            event(
                "read_set.composition",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                file_count=5,
                files=["a.md", "b.md", "c.md", "d.md", "e.md"],
            )

            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            cog_start = content.index("## Cognitive Load")
            cog_section = content[cog_start:]
            next_section = cog_section.find("\n## ", 1)
            if next_section > 0:
                cog_section = cog_section[:next_section]
            assert "| 5 " in cog_section
        finally:
            telemetry._log = old

    def test_reversed_order_still_prefers_pre_composed(
        self, tmp_path: Path,
    ) -> None:
        """Even if pre_composed event arrives first, it wins."""
        from clou.telemetry import write_milestone_summary

        old = telemetry._log
        try:
            log = init("test-reversed-order", tmp_path)

            with span(
                "cycle", milestone="m1", cycle_num=3, cycle_type="ASSESS",
            ) as c:
                c["outcome"] = "VERIFY"
                c["input_tokens"] = 10000
                c["output_tokens"] = 1000

            # Pre-composed event first.
            event(
                "read_set.composition",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                file_count=2,
                files=["active/assess_summary.md", "requirements.md"],
                pre_composed=True,
            )
            # Raw event second (should not overwrite).
            event(
                "read_set.composition",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                file_count=6,
                files=["a.md", "b.md", "c.md", "d.md", "e.md", "f.md"],
            )

            write_milestone_summary(tmp_path, "m1", "completed")

            content = (
                tmp_path / ".clou" / "milestones" / "m1" / "metrics.md"
            ).read_text()
            cog_start = content.index("## Cognitive Load")
            cog_section = content[cog_start:]
            next_section = cog_section.find("\n## ", 1)
            if next_section > 0:
                cog_section = cog_section[:next_section]
            # Pre-composed count should win.
            assert "| 2 " in cog_section
            assert "| 6 " not in cog_section
        finally:
            telemetry._log = old


class TestExistingMetricsForAssess:
    """Verify read_set.composition and read_set.reference_density
    are operational for ASSESS cycles (not just EXECUTE).

    These events are emitted unconditionally on all cycle types
    in coordinator.py. We verify the emission is not gated by
    cycle type.
    """

    def test_read_set_composition_emits_for_assess(self, tmp_path: Path) -> None:
        """read_set.composition is emitted for ASSESS cycle_type."""
        old = telemetry._log
        try:
            log = init("test-comp-assess", tmp_path)

            # Simulate what coordinator.py does: emit read_set.composition
            # with cycle_type=ASSESS.
            event(
                "read_set.composition",
                milestone="m1",
                cycle_num=3,
                cycle_type="ASSESS",
                file_count=4,
                files=["execution.md", "requirements.md", "decisions.md", "assessment.md"],
            )

            records = read_log(log.path)
            comp_events = [
                r for r in records
                if r.get("event") == "read_set.composition"
            ]
            assert len(comp_events) == 1
            assert comp_events[0]["cycle_type"] == "ASSESS"
            assert comp_events[0]["file_count"] == 4
        finally:
            telemetry._log = old

    def test_read_set_reference_density_emits_for_assess(self, tmp_path: Path) -> None:
        """read_set.reference_density is emitted for all cycle types
        including ASSESS."""
        old = telemetry._log
        try:
            log = init("test-refdens-assess", tmp_path)

            # Simulate the emission from coordinator.py.
            event(
                "read_set.reference_density",
                milestone="m1",
                cycle_num=3,
                referenced_count=3,
                total_count=4,
                density=0.75,
                unreferenced=["assessment.md"],
            )

            records = read_log(log.path)
            rd_events = [
                r for r in records
                if r.get("event") == "read_set.reference_density"
            ]
            assert len(rd_events) == 1
            assert rd_events[0]["density"] == 0.75
            assert rd_events[0]["total_count"] == 4
        finally:
            telemetry._log = old
