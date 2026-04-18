"""Tests for scan_pattern_references() and _extract_key_phrases() — M35 I2."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clou.recovery_compaction import (
    MemoryPattern,
    _extract_cycle_section,
    _extract_key_phrases,
    _render_memory,
    scan_pattern_references,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_filtered_memory(path: Path, patterns: list[MemoryPattern]) -> None:
    """Write patterns to a filtered memory file using the renderer."""
    path.write_text(_render_memory(patterns), encoding="utf-8")


def _make_pattern(
    name: str = "decomposition-topology",
    type_: str = "decomposition",
    description: str = "4 phases, parallel gather execution",
    status: str = "active",
) -> MemoryPattern:
    return MemoryPattern(
        name=name,
        type=type_,
        observed=["m01"],
        reinforced=1,
        last_active="m01",
        status=status,
        description=description,
    )


# ---------------------------------------------------------------------------
# _extract_key_phrases
# ---------------------------------------------------------------------------


class TestExtractKeyPhrases:
    def test_basic_3_word_phrases(self) -> None:
        phrases = _extract_key_phrases("one two three four")
        assert phrases == ["one two three", "two three four"]

    def test_less_than_min_words(self) -> None:
        assert _extract_key_phrases("one two") == []

    def test_exactly_min_words(self) -> None:
        assert _extract_key_phrases("one two three") == ["one two three"]

    def test_lowercased(self) -> None:
        phrases = _extract_key_phrases("Hello World Foo")
        assert phrases == ["hello world foo"]

    def test_empty_string(self) -> None:
        assert _extract_key_phrases("") == []

    def test_custom_min_words(self) -> None:
        phrases = _extract_key_phrases("a b c d", min_words=4)
        assert phrases == ["a b c d"]


# ---------------------------------------------------------------------------
# scan_pattern_references — exact name match
# ---------------------------------------------------------------------------


class TestExactNameMatch:
    def test_hyphenated_name_found(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="decomposition-topology")
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 1\nUsed decomposition-topology for planning.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == ["decomposition-topology"]
        assert result["influence_ratio"] == 1.0
        assert result["match_details"][0]["match_type"] == "exact_name"

    def test_space_separated_name_found(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="decomposition-topology")
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 1\nApplied decomposition topology approach.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == ["decomposition-topology"]

    def test_case_insensitive(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="cost-calibration")
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 1\nReviewed COST-CALIBRATION data.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == ["cost-calibration"]


# ---------------------------------------------------------------------------
# scan_pattern_references — key phrase overlap
# ---------------------------------------------------------------------------


class TestKeyPhraseOverlap:
    def test_phrase_from_description_found(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(
            name="gather-topology",
            description="parallel gather execution for tasks",
        )
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 1\nWe use parallel gather execution in this plan.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == ["gather-topology"]
        assert result["match_details"][0]["match_type"] == "key_phrase"
        assert result["match_details"][0]["matched_phrase"] == "parallel gather execution"

    def test_no_phrase_match(self, tmp_path: Path) -> None:
        """Neither name nor phrases match."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(
            name="some-obscure-pattern",
            description="totally unrelated phrasing here used",
        )
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 1\nNothing relevant in this decisions doc.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == []
        assert result["influence_ratio"] == 0.0
        assert result["match_details"] == []


# ---------------------------------------------------------------------------
# scan_pattern_references — influence ratio
# ---------------------------------------------------------------------------


class TestInfluenceRatio:
    def test_ratio_computation(self, tmp_path: Path) -> None:
        """2 retrieved, 1 referenced -> 0.5."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p1 = _make_pattern(name="pattern-a", description="word1 word2 word3")
        p2 = _make_pattern(name="pattern-b", description="unmatched desc phrase")
        _write_filtered_memory(filtered, [p1, p2])
        decisions.write_text(
            "## Cycle 1\nReferences pattern-a in text.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="ASSESS",
        )

        assert result is not None
        assert len(result["retrieved"]) == 2
        assert len(result["referenced"]) == 1
        assert result["influence_ratio"] == pytest.approx(0.5)

    def test_all_matched_ratio_one(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p1 = _make_pattern(name="pattern-a")
        p2 = _make_pattern(name="pattern-b")
        _write_filtered_memory(filtered, [p1, p2])
        decisions.write_text(
            "## Cycle 1\nUsed pattern-a and pattern-b for decisions.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["influence_ratio"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# scan_pattern_references — empty/edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_filtered_memory_file(self, tmp_path: Path) -> None:
        """Returns None when _filtered_memory.md does not exist."""
        filtered = tmp_path / "_filtered_memory.md"  # does not exist
        decisions = tmp_path / "decisions.md"
        decisions.write_text("## Cycle 1\nSome decisions.\n", encoding="utf-8")

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is None

    def test_no_decisions_file(self, tmp_path: Path) -> None:
        """Returns None when decisions.md does not exist."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"  # does not exist

        p = _make_pattern()
        _write_filtered_memory(filtered, [p])

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is None

    def test_empty_patterns(self, tmp_path: Path) -> None:
        """Returns None when memory file has no patterns."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        filtered.write_text("# Operational Memory\n\n## Patterns\n\n", encoding="utf-8")
        decisions.write_text("## Cycle 1\nSome decisions.\n", encoding="utf-8")

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is None

    def test_decisions_not_modified(self, tmp_path: Path) -> None:
        """R7: decisions.md content must not be modified."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="test-pattern")
        _write_filtered_memory(filtered, [p])
        original_content = "## Cycle 1\nMentions test-pattern here.\n"
        decisions.write_text(original_content, encoding="utf-8")

        scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert decisions.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# Telemetry event structure
# ---------------------------------------------------------------------------


class TestTelemetryEvent:
    def test_event_emitted_with_correct_structure(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="decomp-pattern", description="parallel gather execution")
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 3\nUsed parallel gather execution approach.\n",
            encoding="utf-8",
        )

        captured: list[dict[str, Any]] = []

        def fake_event(name: str, **attrs: Any) -> None:
            captured.append({"name": name, **attrs})

        with patch("clou.telemetry.event", side_effect=fake_event):
            result = scan_pattern_references(
                filtered, decisions,
                milestone="test-ms", cycle_num=3, cycle_type="ASSESS",
            )

        assert result is not None
        assert len(captured) == 1
        evt = captured[0]
        assert evt["name"] == "memory.pattern_influence"
        assert evt["milestone"] == "test-ms"
        assert evt["cycle_num"] == 3
        assert evt["cycle_type"] == "ASSESS"
        assert evt["retrieved"] == ["decomp-pattern"]
        assert evt["referenced"] == ["decomp-pattern"]
        assert evt["influence_ratio"] == pytest.approx(1.0)
        assert len(evt["match_details"]) == 1
        detail = evt["match_details"][0]
        assert detail["pattern"] == "decomp-pattern"
        assert detail["match_type"] == "key_phrase"

    def test_no_event_when_no_patterns(self, tmp_path: Path) -> None:
        """No telemetry event when filtered memory has no patterns."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        filtered.write_text("# Operational Memory\n\n## Patterns\n\n", encoding="utf-8")
        decisions.write_text("## Cycle 1\nSome text.\n", encoding="utf-8")

        mock_event = MagicMock()
        with patch("clou.telemetry.event", mock_event):
            result = scan_pattern_references(
                filtered, decisions,
                milestone="test-ms", cycle_num=1, cycle_type="PLAN",
            )

        assert result is None
        mock_event.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: result dict schema completeness
# ---------------------------------------------------------------------------


class TestResultSchema:
    def test_all_required_fields_present(self, tmp_path: Path) -> None:
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern()
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 5\nSome unrelated text.\n", encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=5, cycle_type="ASSESS",
        )

        assert result is not None
        required_keys = {
            "milestone", "cycle_num", "cycle_type",
            "retrieved", "referenced", "influence_ratio",
            "match_details",
        }
        assert required_keys <= set(result.keys())
        assert isinstance(result["retrieved"], list)
        assert isinstance(result["referenced"], list)
        assert isinstance(result["influence_ratio"], float)
        assert isinstance(result["match_details"], list)


# ---------------------------------------------------------------------------
# F4: _extract_cycle_section and multi-cycle scoping
# ---------------------------------------------------------------------------


class TestExtractCycleSection:
    """Verify _extract_cycle_section extracts only the matching heading."""

    def test_extracts_matching_cycle(self) -> None:
        content = (
            "## Cycle 1\nFirst cycle content.\n\n"
            "## Cycle 2\nSecond cycle content.\n"
        )
        section = _extract_cycle_section(content, 2)
        assert section is not None
        assert "Second cycle content" in section
        assert "First cycle content" not in section

    def test_returns_none_for_missing_cycle(self) -> None:
        content = "## Cycle 1\nOnly one cycle.\n"
        assert _extract_cycle_section(content, 5) is None

    def test_first_cycle_extraction(self) -> None:
        content = (
            "## Cycle 1\nContent A.\n\n"
            "## Cycle 2\nContent B.\n"
        )
        section = _extract_cycle_section(content, 1)
        assert section is not None
        assert "Content A" in section
        assert "Content B" not in section

    def test_preamble_ignored(self) -> None:
        """Content before the first ## Cycle heading is not matched."""
        content = (
            "# Decisions\n\nSome preamble.\n\n"
            "## Cycle 1\nActual cycle.\n"
        )
        section = _extract_cycle_section(content, 1)
        assert section is not None
        assert "preamble" not in section
        assert "Actual cycle" in section


class TestMultiCycleScoping:
    """F4: scan_pattern_references scans only the current cycle section."""

    def test_pattern_in_old_cycle_not_credited(self, tmp_path: Path) -> None:
        """A pattern mentioned in cycle 1 must not be credited to cycle 2."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="decomposition-topology")
        _write_filtered_memory(filtered, [p])

        decisions.write_text(
            "## Cycle 1\nUsed decomposition-topology for planning.\n\n"
            "## Cycle 2\nNo patterns referenced here.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=2, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == []
        assert result["influence_ratio"] == 0.0

    def test_pattern_in_current_cycle_credited(self, tmp_path: Path) -> None:
        """A pattern in the current cycle section IS credited."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="decomposition-topology")
        _write_filtered_memory(filtered, [p])

        decisions.write_text(
            "## Cycle 1\nNo patterns here.\n\n"
            "## Cycle 2\nUsed decomposition-topology approach.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=2, cycle_type="PLAN",
        )

        assert result is not None
        assert result["referenced"] == ["decomposition-topology"]
        assert result["influence_ratio"] == pytest.approx(1.0)

    def test_returns_none_when_cycle_section_missing(self, tmp_path: Path) -> None:
        """Returns None when the requested cycle section does not exist."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="decomposition-topology")
        _write_filtered_memory(filtered, [p])

        decisions.write_text(
            "## Cycle 1\nOnly one cycle.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered, decisions,
            milestone="test-ms", cycle_num=5, cycle_type="PLAN",
        )

        assert result is None


# ---------------------------------------------------------------------------
# F8: Warning-level logging on scanner failure
# ---------------------------------------------------------------------------


class TestScannerFailureLogging:
    """F8: Telemetry emission failure is logged at warning level."""

    def test_telemetry_failure_logged_as_warning(self, tmp_path: Path) -> None:
        """When telemetry.event() raises, _log.warning is called."""
        filtered = tmp_path / "_filtered_memory.md"
        decisions = tmp_path / "decisions.md"

        p = _make_pattern(name="test-pattern")
        _write_filtered_memory(filtered, [p])
        decisions.write_text(
            "## Cycle 1\nMentions test-pattern here.\n", encoding="utf-8",
        )

        with patch("clou.telemetry.event", side_effect=RuntimeError("boom")), \
             patch("clou.recovery_compaction._log") as mock_log:
            result = scan_pattern_references(
                filtered, decisions,
                milestone="test-ms", cycle_num=1, cycle_type="PLAN",
            )

        # The function should still return the result (telemetry failure
        # does not prevent returning influence data).
        assert result is not None
        assert result["referenced"] == ["test-pattern"]
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# F10+F20: Integration tests — coordinator wiring and full pipeline
# ---------------------------------------------------------------------------

_SKIP_NO_SDK = pytest.mark.skipif(
    not importlib.util.find_spec("claude_agent_sdk"),
    reason="requires claude_agent_sdk",
)

_PC = "clou.coordinator"  # coordinator-resident names


def _coordinator_project(tmp_path: Path) -> Path:
    """Create minimal .clou structure for coordinator tests."""
    active = tmp_path / ".clou" / "active"
    active.mkdir(parents=True)
    prompts = tmp_path / ".clou" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "coordinator-system.xml").write_text("<system/>")
    return tmp_path


@_SKIP_NO_SDK
class TestCoordinatorScannerWiring:
    """F10: Verify the coordinator's post-cycle block calls scan_pattern_references.

    These tests patch scan_pattern_references at the coordinator's import site
    (clou.recovery.scan_pattern_references) and run run_coordinator to verify
    the wiring: cycle type gating, file existence guards, argument passing,
    and exception handling.
    """

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        return _coordinator_project(tmp_path)

    @staticmethod
    def _run_coordinator():
        """Deferred import of run_coordinator (requires claude_agent_sdk)."""
        from clou.coordinator import run_coordinator
        return run_coordinator

    def _milestone_dirs(self, project_dir: Path, milestone: str = "auth") -> tuple[Path, Path, Path]:
        """Create milestone dirs and return (active_dir, decisions_path, filtered_path)."""
        ms_dir = project_dir / ".clou" / "milestones" / milestone
        active_dir = ms_dir / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        decisions_path = ms_dir / "decisions.md"
        filtered_path = active_dir / "_filtered_memory.md"
        return active_dir, decisions_path, filtered_path

    @pytest.mark.asyncio
    async def test_scan_called_for_plan_with_correct_args(self, project_dir: Path) -> None:
        """PLAN cycle triggers scanner with cycle_num=cycle_count+1."""
        _active, decisions_path, filtered_path = self._milestone_dirs(project_dir)

        # Create the files the coordinator checks before calling.
        p = _make_pattern(name="test-pattern")
        _write_filtered_memory(filtered_path, [p])
        decisions_path.write_text("## Cycle 4\nUsed test-pattern.\n", encoding="utf-8")

        mock_scanner = MagicMock(return_value=None)

        with (
            patch(f"{_PC}.determine_next_cycle", side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])]),
            patch(f"{_PC}.read_cycle_count", return_value=3),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", new_callable=AsyncMock, return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch("clou.recovery.scan_pattern_references", mock_scanner),
        ):
            result = await self._run_coordinator()(project_dir, "auth")

        assert result == "completed"
        mock_scanner.assert_called_once_with(
            filtered_path,
            decisions_path,
            milestone="auth",
            cycle_num=4,  # cycle_count(3) + 1
            cycle_type="PLAN",
        )

    @pytest.mark.asyncio
    async def test_scan_called_for_assess_cycle(self, project_dir: Path) -> None:
        """ASSESS cycle also triggers scanner."""
        _active, decisions_path, filtered_path = self._milestone_dirs(project_dir)

        p = _make_pattern(name="quality-pattern")
        _write_filtered_memory(filtered_path, [p])
        decisions_path.write_text("## Cycle 2\nApplied quality-pattern.\n", encoding="utf-8")

        mock_scanner = MagicMock(return_value=None)

        with (
            patch(f"{_PC}.determine_next_cycle", side_effect=[("ASSESS", ["status.md"]), ("COMPLETE", [])]),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", new_callable=AsyncMock, return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch("clou.recovery.scan_pattern_references", mock_scanner),
        ):
            result = await self._run_coordinator()(project_dir, "auth")

        assert result == "completed"
        mock_scanner.assert_called_once()
        call_kwargs = mock_scanner.call_args
        assert call_kwargs[1]["cycle_type"] == "ASSESS"

    @pytest.mark.asyncio
    async def test_scan_skipped_for_execute_cycle(self, project_dir: Path) -> None:
        """EXECUTE cycle type does not trigger scanner."""
        _active, decisions_path, filtered_path = self._milestone_dirs(project_dir)

        p = _make_pattern(name="test-pattern")
        _write_filtered_memory(filtered_path, [p])
        decisions_path.write_text("## Cycle 1\nSome text.\n", encoding="utf-8")

        mock_scanner = MagicMock(return_value=None)

        with (
            patch(f"{_PC}.determine_next_cycle", side_effect=[("EXECUTE", ["status.md"]), ("COMPLETE", [])]),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", new_callable=AsyncMock, return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch("clou.recovery.scan_pattern_references", mock_scanner),
        ):
            result = await self._run_coordinator()(project_dir, "auth")

        assert result == "completed"
        mock_scanner.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_skipped_when_filtered_memory_missing(self, project_dir: Path) -> None:
        """Scanner not called when _filtered_memory.md does not exist."""
        _active, decisions_path, _filtered_path = self._milestone_dirs(project_dir)

        # Only create decisions.md, not _filtered_memory.md.
        decisions_path.write_text("## Cycle 2\nSome text.\n", encoding="utf-8")

        mock_scanner = MagicMock(return_value=None)

        with (
            patch(f"{_PC}.determine_next_cycle", side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])]),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", new_callable=AsyncMock, return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch("clou.recovery.scan_pattern_references", mock_scanner),
        ):
            result = await self._run_coordinator()(project_dir, "auth")

        assert result == "completed"
        mock_scanner.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_skipped_when_decisions_missing(self, project_dir: Path) -> None:
        """Scanner not called when decisions.md does not exist."""
        _active, _decisions_path, filtered_path = self._milestone_dirs(project_dir)

        # Only create _filtered_memory.md, not decisions.md.
        p = _make_pattern(name="test-pattern")
        _write_filtered_memory(filtered_path, [p])

        mock_scanner = MagicMock(return_value=None)

        with (
            patch(f"{_PC}.determine_next_cycle", side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])]),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", new_callable=AsyncMock, return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch("clou.recovery.scan_pattern_references", mock_scanner),
        ):
            result = await self._run_coordinator()(project_dir, "auth")

        assert result == "completed"
        mock_scanner.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_error_logged_as_warning(self, project_dir: Path) -> None:
        """When scanner raises, coordinator logs warning and continues."""
        _active, decisions_path, filtered_path = self._milestone_dirs(project_dir)

        p = _make_pattern(name="test-pattern")
        _write_filtered_memory(filtered_path, [p])
        decisions_path.write_text("## Cycle 2\nSome text.\n", encoding="utf-8")

        mock_scanner = MagicMock(side_effect=RuntimeError("scanner boom"))

        with (
            patch(f"{_PC}.determine_next_cycle", side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])]),
            patch(f"{_PC}.read_cycle_count", return_value=1),
            patch(f"{_PC}.validate_readiness", return_value=[]),
            patch(f"{_PC}._run_single_cycle", new_callable=AsyncMock, return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.validate_delivery", return_value=[]),
            patch("clou.recovery.scan_pattern_references", mock_scanner),
            patch(f"{_PC}.log") as mock_log,
        ):
            result = await self._run_coordinator()(project_dir, "auth")

        assert result == "completed"
        mock_scanner.assert_called_once()
        # Verify warning was logged with the milestone name.
        mock_log.warning.assert_any_call(
            "Pattern reference scan failed for %r",
            "auth", exc_info=True,
        )


class TestPipelineIntegration:
    """F20: End-to-end pipeline from retrieval filtering through scanning to rendering."""

    def test_filter_scan_render_pipeline(self, tmp_path: Path) -> None:
        """Run _filter_memory_for_cycle -> scan_pattern_references -> verify
        the scanner correctly identifies patterns that survive filtering."""
        from clou.recovery_checkpoint import _filter_memory_for_cycle

        # 1. Create a memory.md with mixed pattern types.
        memory_path = tmp_path / "memory.md"
        milestones_dir = tmp_path / "milestones"
        milestones_dir.mkdir()

        decomp = _make_pattern(
            name="decomposition-topology",
            type_="decomposition",
            description="4 phases parallel gather execution",
        )
        qg = _make_pattern(
            name="quality-gate-convergence",
            type_="quality-gate",
            description="consecutive zero valid findings threshold",
        )
        # This one should NOT survive PLAN filtering (wrong type).
        escalation = _make_pattern(
            name="crash-retry-escalation",
            type_="escalation",
            description="escalate after three crash retries",
        )

        memory_path.write_text(
            _render_memory([decomp, qg, escalation]), encoding="utf-8",
        )

        # 2. Filter for a PLAN cycle (only decomposition + cost-calibration + debt types).
        filtered_content = _filter_memory_for_cycle(
            memory_path, "PLAN", milestones_dir,
        )

        assert filtered_content is not None
        # Write it to the filtered path.
        filtered_path = tmp_path / "_filtered_memory.md"
        filtered_path.write_text(filtered_content, encoding="utf-8")

        # Only decomposition-topology should survive PLAN filtering.
        from clou.recovery_compaction import _parse_memory

        surviving = _parse_memory(filtered_content)
        surviving_names = [p.name for p in surviving]
        assert "decomposition-topology" in surviving_names
        # quality-gate and escalation types are not in PLAN filter.
        assert "quality-gate-convergence" not in surviving_names
        assert "crash-retry-escalation" not in surviving_names

        # 3. Create decisions.md referencing the surviving pattern.
        decisions_path = tmp_path / "decisions.md"
        decisions_path.write_text(
            "## Cycle 1\n"
            "Applied decomposition-topology to structure the milestone.\n"
            "Using 4 phases parallel gather execution for tasks.\n",
            encoding="utf-8",
        )

        # 4. Scan for references.
        result = scan_pattern_references(
            filtered_path, decisions_path,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["retrieved"] == ["decomposition-topology"]
        assert result["referenced"] == ["decomposition-topology"]
        assert result["influence_ratio"] == pytest.approx(1.0)
        assert len(result["match_details"]) >= 1
        # Should be exact_name since the hyphenated name appears directly.
        assert result["match_details"][0]["match_type"] == "exact_name"

    def test_filter_scan_no_influence(self, tmp_path: Path) -> None:
        """Pipeline where patterns survive filtering but are not referenced."""
        from clou.recovery_checkpoint import _filter_memory_for_cycle

        memory_path = tmp_path / "memory.md"
        milestones_dir = tmp_path / "milestones"
        milestones_dir.mkdir()

        decomp = _make_pattern(
            name="decomposition-topology",
            type_="decomposition",
            description="4 phases parallel gather execution",
        )
        memory_path.write_text(
            _render_memory([decomp]), encoding="utf-8",
        )

        filtered_content = _filter_memory_for_cycle(
            memory_path, "PLAN", milestones_dir,
        )
        assert filtered_content is not None

        filtered_path = tmp_path / "_filtered_memory.md"
        filtered_path.write_text(filtered_content, encoding="utf-8")

        decisions_path = tmp_path / "decisions.md"
        decisions_path.write_text(
            "## Cycle 1\n"
            "Decided to skip planning and go straight to implementation.\n",
            encoding="utf-8",
        )

        result = scan_pattern_references(
            filtered_path, decisions_path,
            milestone="test-ms", cycle_num=1, cycle_type="PLAN",
        )

        assert result is not None
        assert result["retrieved"] == ["decomposition-topology"]
        assert result["referenced"] == []
        assert result["influence_ratio"] == 0.0
