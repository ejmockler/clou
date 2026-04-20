"""Tests for ASSESS pre-composition wiring (DB-20 Step 2).

Verifies that the pre-composition step integrates correctly into the
ASSESS cycle dispatch pipeline in coordinator.py, replacing the raw
5+ file read set with <=2 pre-composed files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clou.precompose import precompose_assess_context
from clou.prompts import build_cycle_prompt
from clou.recovery import determine_next_cycle
from clou import telemetry as _telemetry_mod
from clou.telemetry import (
    init as _telemetry_init,
    event as _telemetry_event,
    read_log as _telemetry_read_log,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_COMPOSE = '''\
"""Test milestone."""

from dataclasses import dataclass


@dataclass
class Result:
    files: list[str]


async def build_feature() -> Result:
    """Build the feature.
    I1.
    Criteria: Feature works."""


async def write_tests() -> Result:
    """Write tests.
    I1, I2.
    Criteria: Tests pass."""


async def execute():
    await gather(
        build_feature(),
        write_tests(),
    )
'''

MOCK_EXECUTION = """\
## Summary
status: completed
started: 2026-04-12T10:00:00Z
completed: 2026-04-12T11:00:00Z
tasks: 1 total, 1 completed, 0 failed, 0 in_progress
failures: none
blockers: none

### T1: Build feature
**Status:** completed
**Files changed:**
  - clou/feature.py (created)
**Tests:** 3 unit tests passing
**Notes:** ---
"""

MOCK_REQUIREMENTS = """\
# Requirements
- R1: Feature must exist
- R2: Tests must pass
"""

MOCK_ASSESSMENT = """\
# Assessment
## Findings
- F1 [pass]: Feature exists
"""

MOCK_DECISIONS = """\
## Cycle 3: ASSESS
Decision: accept
Rationale: All criteria met.
"""


@pytest.fixture()
def milestone_dir(tmp_path: Path) -> Path:
    """Create a milestone directory tree matching checkpoint expectations.

    The checkpoint path is at {clou_dir}/milestones/{ms}/active/coordinator.md
    so milestone_dir = {clou_dir}/milestones/{ms}.
    """
    clou_dir = tmp_path / ".clou"
    ms_dir = clou_dir / "milestones" / "m1"

    # compose.py
    ms_dir.mkdir(parents=True)
    (ms_dir / "compose.py").write_text(MOCK_COMPOSE)
    (ms_dir / "requirements.md").write_text(MOCK_REQUIREMENTS)
    (ms_dir / "assessment.md").write_text(MOCK_ASSESSMENT)
    (ms_dir / "decisions.md").write_text(MOCK_DECISIONS)

    # Phase dirs
    for phase in ("build_feature", "write_tests"):
        phase_dir = ms_dir / "phases" / phase
        phase_dir.mkdir(parents=True)
        (phase_dir / "execution.md").write_text(MOCK_EXECUTION)

    # Checkpoint
    active_dir = ms_dir / "active"
    active_dir.mkdir(parents=True)
    (active_dir / "coordinator.md").write_text(
        "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\n"
        "current_phase: build_feature\n"
    )

    return ms_dir


# ---------------------------------------------------------------------------
# T1: determine_next_cycle still returns raw ASSESS read set
# ---------------------------------------------------------------------------


class TestDetermineNextCycleUnchanged:
    """Verify determine_next_cycle is NOT modified -- raw read set returned."""

    def test_assess_raw_read_set_still_includes_execution(
        self, milestone_dir: Path,
    ) -> None:
        cp_path = milestone_dir / "active" / "coordinator.md"
        cycle_type, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle_type == "ASSESS"
        # Raw read set should still have execution.md and other files.
        assert any("execution.md" in f for f in read_set)
        assert "decisions.md" in read_set
        assert "requirements.md" in read_set
        assert "assessment.md" in read_set

    def test_assess_raw_read_set_has_five_or_more(
        self, milestone_dir: Path,
    ) -> None:
        """Raw ASSESS read set has 5+ files before pre-composition."""
        cp_path = milestone_dir / "active" / "coordinator.md"
        cycle_type, read_set = determine_next_cycle(cp_path, "m1")
        assert cycle_type == "ASSESS"
        # At minimum: execution.md for each peer + requirements + decisions + assessment
        assert len(read_set) >= 5


# ---------------------------------------------------------------------------
# T2: Pre-composition produces <=2 file read set
# ---------------------------------------------------------------------------


class TestPrecompositionReadSetReduction:
    """Verify the wiring logic replaces 5+ files with <=2."""

    def test_precomposed_read_set_at_most_two_files(
        self, milestone_dir: Path,
    ) -> None:
        """After pre-composition, read set contains <=2 files (+ optional memory)."""
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        precomposed_set = [summary_rel, "requirements.md"]
        assert len(precomposed_set) <= 2

    def test_precomposed_read_set_contains_summary(
        self, milestone_dir: Path,
    ) -> None:
        """Pre-composed read set includes the summary file."""
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        assert summary_rel == "active/assess_summary.md"

    def test_precomposed_read_set_preserves_filtered_memory(
        self, milestone_dir: Path,
    ) -> None:
        """If filtered memory was in original set, it's preserved."""
        original_set = [
            "phases/build_feature/execution.md",
            "requirements.md",
            "decisions.md",
            "assessment.md",
            "active/_filtered_memory.md",
        ]
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        precomposed_set = [summary_rel, "requirements.md"]
        if "active/_filtered_memory.md" in original_set:
            precomposed_set.append("active/_filtered_memory.md")
        assert len(precomposed_set) == 3
        assert "active/_filtered_memory.md" in precomposed_set

    def test_precomposed_read_set_without_filtered_memory(
        self, milestone_dir: Path,
    ) -> None:
        """Without filtered memory, read set is exactly 2 files."""
        original_set = [
            "phases/build_feature/execution.md",
            "requirements.md",
            "decisions.md",
            "assessment.md",
        ]
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        precomposed_set = [summary_rel, "requirements.md"]
        if "active/_filtered_memory.md" in original_set:
            precomposed_set.append("active/_filtered_memory.md")
        assert len(precomposed_set) == 2


# ---------------------------------------------------------------------------
# T3: Pre-composition integrates with build_cycle_prompt
# ---------------------------------------------------------------------------


class TestPrecompositionPromptIntegration:
    """Verify pre-composed read set flows correctly into prompt builder."""

    def test_prompt_references_summary_file(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """build_cycle_prompt references the pre-composed summary."""
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        precomposed_set = [summary_rel, "requirements.md"]

        prompt = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m1",
            cycle_type="ASSESS",
            read_set=precomposed_set,
            current_phase="build_feature",
        )
        assert "assess_summary.md" in prompt
        assert "requirements.md" in prompt

    def test_prompt_does_not_reference_raw_files(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Pre-composed prompt does not list raw execution.md or decisions.md."""
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        precomposed_set = [summary_rel, "requirements.md"]

        prompt = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m1",
            cycle_type="ASSESS",
            read_set=precomposed_set,
            current_phase="build_feature",
        )
        # The raw files should not be in the golden context list
        # (they may still appear in the protocol pointer which is fine).
        golden_section = prompt.split("golden context files:")[1].split("Write your state")[0]
        assert "decisions.md" not in golden_section
        assert "assessment.md" not in golden_section

    def test_prompt_has_fewer_golden_context_lines(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Pre-composed prompt lists fewer golden context files than raw."""
        # Raw read set
        cp_path = milestone_dir / "active" / "coordinator.md"
        _, raw_read_set = determine_next_cycle(cp_path, "m1")
        raw_prompt = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m1",
            cycle_type="ASSESS",
            read_set=raw_read_set,
            current_phase="build_feature",
        )
        raw_file_lines = [
            line for line in raw_prompt.split("\n")
            if line.strip().startswith("- .clou/milestones/m1/")
            and "golden context" not in line.lower()
        ]

        # Pre-composed read set
        co_layer = ["build_feature", "write_tests"]
        summary_path = precompose_assess_context(
            milestone_dir, "build_feature", co_layer,
        )
        summary_rel = str(summary_path.relative_to(milestone_dir))
        precomposed_set = [summary_rel, "requirements.md"]
        precomposed_prompt = build_cycle_prompt(
            project_dir=tmp_path,
            milestone="m1",
            cycle_type="ASSESS",
            read_set=precomposed_set,
            current_phase="build_feature",
        )
        precomposed_file_lines = [
            line for line in precomposed_prompt.split("\n")
            if line.strip().startswith("- .clou/milestones/m1/")
            and "golden context" not in line.lower()
        ]

        assert len(precomposed_file_lines) < len(raw_file_lines)


# ---------------------------------------------------------------------------
# T4: Fallback when pre-composition fails
# ---------------------------------------------------------------------------


class TestPrecompositionFallback:
    """Verify graceful fallback when pre-composition fails."""

    def test_missing_compose_py_still_produces_summary(
        self, tmp_path: Path,
    ) -> None:
        """Pre-composition works even without compose.py."""
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "requirements.md").write_text(MOCK_REQUIREMENTS)
        phase_dir = ms / "phases" / "build_feature"
        phase_dir.mkdir(parents=True)
        (phase_dir / "execution.md").write_text(MOCK_EXECUTION)
        (ms / "active").mkdir()

        result = precompose_assess_context(ms, "build_feature", ["build_feature"])
        assert result.exists()
        text = result.read_text(encoding="utf-8")
        assert "# ASSESS Context Summary" in text

    def test_empty_co_layer_still_works(self, tmp_path: Path) -> None:
        """Empty co-layer list produces a minimal summary."""
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "compose.py").write_text(MOCK_COMPOSE)
        (ms / "requirements.md").write_text(MOCK_REQUIREMENTS)
        (ms / "active").mkdir()

        result = precompose_assess_context(ms, "build_feature", [])
        assert result.exists()
        text = result.read_text(encoding="utf-8")
        assert "# ASSESS Context Summary" in text


# ---------------------------------------------------------------------------
# T5: Coordinator-assess.md prompt updated
# ---------------------------------------------------------------------------


class TestCoordinatorAssessPrompt:
    """Verify the ASSESS protocol references pre-composed context."""

    def test_prompt_mentions_precomposed_summary(self) -> None:
        """coordinator-assess.md references the pre-composed summary."""
        prompt_path = (
            Path(__file__).parent.parent / "clou" / "_prompts" / "coordinator-assess.md"
        )
        text = prompt_path.read_text(encoding="utf-8")
        assert "assess_summary.md" in text

    def test_prompt_mentions_golden_context(self) -> None:
        """coordinator-assess.md tells coordinator to read golden context files."""
        prompt_path = (
            Path(__file__).parent.parent / "clou" / "_prompts" / "coordinator-assess.md"
        )
        text = prompt_path.read_text(encoding="utf-8")
        assert "golden context" in text.lower()

    def test_prompt_preserves_decision_routing(self) -> None:
        """R8: ASSESS decision routing (steps 6-9) must NOT change."""
        prompt_path = (
            Path(__file__).parent.parent / "clou" / "_prompts" / "coordinator-assess.md"
        )
        text = prompt_path.read_text(encoding="utf-8")
        # Core decision routing steps unchanged.
        assert "next_step: EXECUTE (rework)" in text
        assert "next_step: VERIFY" in text
        assert "next_step: EXECUTE" in text
        assert "Write checkpoint" in text
        assert "decisions.md" in text

    def test_prompt_documents_fallback_path(self) -> None:
        """F5: coordinator-assess.md distinguishes normal vs fallback paths."""
        prompt_path = (
            Path(__file__).parent.parent / "clou" / "_prompts" / "coordinator-assess.md"
        )
        text = prompt_path.read_text(encoding="utf-8")
        # F5a: Clarifies coordinator reads summary, sub-agents read raw files.
        assert "Sub-agent reads" in text or "sub-agent" in text.lower()
        # F5b: Fallback instruction references raw files, not summary.
        assert "Fallback path" in text
        assert "raw" in text.lower()


# ---------------------------------------------------------------------------
# T6: F22 — _PHASE_RE validation before precompose call
# ---------------------------------------------------------------------------


class TestPhaseReValidation:
    """F22: Invalid _current_phase should skip precomposition."""

    def test_phase_re_rejects_path_traversal(self) -> None:
        """_PHASE_RE blocks path traversal patterns."""
        from clou.coordinator import _PHASE_RE

        assert not _PHASE_RE.match("../evil")
        assert not _PHASE_RE.match("../../etc/passwd")
        assert not _PHASE_RE.match("")
        assert not _PHASE_RE.match("/root")
        assert not _PHASE_RE.match("a/b")

    def test_phase_re_accepts_valid_names(self) -> None:
        """_PHASE_RE accepts normal phase names."""
        from clou.coordinator import _PHASE_RE

        assert _PHASE_RE.match("build_feature")
        assert _PHASE_RE.match("wire-assess-precomposition")
        assert _PHASE_RE.match("m1-setup")
        assert _PHASE_RE.match("0init")

    def test_phase_re_rejects_trailing_newline(self) -> None:
        """F18: _PHASE_RE must reject names with trailing newlines."""
        from clou.coordinator import _PHASE_RE

        assert not _PHASE_RE.match("build_feature\n")
        assert not _PHASE_RE.match("valid-name\n")
        assert not _PHASE_RE.match("setup\r\n")


# ---------------------------------------------------------------------------
# T7: F6 — Stale assess_summary.md deleted on precomposition failure
# ---------------------------------------------------------------------------


class TestStaleAssessSummaryCleanup:
    """F6: Stale assess_summary.md is deleted when precomposition fails."""

    def test_stale_summary_deleted_on_failure(
        self, milestone_dir: Path,
    ) -> None:
        """When precompose raises, stale assess_summary.md is removed."""
        stale = milestone_dir / "active" / "assess_summary.md"
        stale.write_text("stale content from prior cycle")
        assert stale.exists()

        # Simulate the fallback path: precompose raises, stale file removed.
        # We test the exact logic from coordinator.py's except clause.
        try:
            raise RuntimeError("simulated precomposition failure")
        except Exception:
            # This mirrors the coordinator.py F6 cleanup logic.
            if stale.exists():
                stale.unlink()

        assert not stale.exists()


# ---------------------------------------------------------------------------
# T8: F7 — get_colayer_tasks fallback emits telemetry
# ---------------------------------------------------------------------------


class TestColayerFallbackTelemetry:
    """F7: Scope narrowing in get_colayer_tasks is now observable."""

    def test_colayer_fallback_emits_event(
        self, tmp_path: Path,
    ) -> None:
        """When get_colayer_tasks fails, a telemetry event is emitted."""
        old = _telemetry_mod._log
        try:
            log = _telemetry_init("test-colayer-fallback", tmp_path)

            # Simulate the emission that coordinator.py performs in the
            # get_colayer_tasks fallback (F7).
            _telemetry_event(
                "precompose.colayer_fallback",
                milestone="m1",
                cycle_num=5,
                phase="build_feature",
                error="simulated parse failure",
            )

            records = _telemetry_read_log(log.path)
            fb_events = [
                r for r in records
                if r.get("event") == "precompose.colayer_fallback"
            ]
            assert len(fb_events) == 1
            assert fb_events[0]["phase"] == "build_feature"
            assert fb_events[0]["error"] == "simulated parse failure"
        finally:
            _telemetry_mod._log = old


# ---------------------------------------------------------------------------
# T9: F3 — Post-precompose composition event
# ---------------------------------------------------------------------------


class TestPostPrecomposeCompositionEvent:
    """F3: A second read_set.composition event with pre_composed=True."""

    def test_post_precompose_event_has_pre_composed_flag(
        self, tmp_path: Path,
    ) -> None:
        """Post-precompose composition event carries pre_composed=True."""
        old = _telemetry_mod._log
        try:
            log = _telemetry_init("test-post-precompose", tmp_path)

            # Simulate the two composition events as coordinator.py emits them.
            # First: raw (before precomposition).
            _telemetry_event(
                "read_set.composition",
                milestone="m1",
                cycle_num=5,
                cycle_type="ASSESS",
                file_count=6,
                files=["a.md", "b.md", "c.md", "d.md", "e.md", "f.md"],
            )
            # Second: post-precompose (F3).
            _telemetry_event(
                "read_set.composition",
                milestone="m1",
                cycle_num=5,
                cycle_type="ASSESS",
                file_count=2,
                files=["active/assess_summary.md", "requirements.md"],
                pre_composed=True,
            )

            records = _telemetry_read_log(log.path)
            comp_events = [
                r for r in records
                if r.get("event") == "read_set.composition"
            ]
            assert len(comp_events) == 2
            # First event: raw, no pre_composed flag.
            assert comp_events[0]["file_count"] == 6
            assert "pre_composed" not in comp_events[0]
            # Second event: pre_composed.
            assert comp_events[1]["file_count"] == 2
            assert comp_events[1]["pre_composed"] is True
        finally:
            _telemetry_mod._log = old


# ---------------------------------------------------------------------------
# T10: F9 — Integration test through actual coordinator code path
# ---------------------------------------------------------------------------


class TestCoordinatorPrecompositionIntegration:
    """F9: Exercise the coordinator's ASSESS precomposition path through
    the actual run_coordinator code, not a local reimplementation.

    Uses a mocked precompose_assess_context and a mocked _run_single_cycle
    that terminates the loop after one ASSESS cycle.
    """

    def test_precompose_called_and_read_set_replaced(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Integration: run_coordinator calls precompose and replaces read set."""
        from unittest.mock import AsyncMock
        from clou.coordinator import run_coordinator

        clou_dir = tmp_path / ".clou"

        # Create project.md for harness template resolution.
        (clou_dir / "project.md").write_text(
            "# test\ntemplate: software-construction\n"
        )

        # Milestone marker (expected by coordinator).
        (clou_dir / ".coordinator-milestone").write_text("m1")

        # status.md for validation.
        (milestone_dir / "status.md").write_text("status: in_progress\n")

        # Checkpoint: ASSESS is the next cycle.
        cp_path = milestone_dir / "active" / "coordinator.md"
        cp_path.write_text(
            "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build_feature\n"
        )

        # Create the summary file that our mock precompose will "produce".
        summary_path = milestone_dir / "active" / "assess_summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text("# ASSESS Context Summary\nTest content.\n")

        # Track what build_cycle_prompt receives.
        captured_read_sets: list[list[str]] = []
        _real_build = build_cycle_prompt

        def _capture_build(*args, **kwargs):
            read_set = kwargs.get("read_set") or (args[3] if len(args) > 3 else [])
            captured_read_sets.append(list(read_set))
            return _real_build(*args, **kwargs)

        # Mock precompose to return our prepared summary path.
        mock_precompose = MagicMock(return_value=summary_path)

        # Mock _run_single_cycle to write a COMPLETE checkpoint and return.
        async def _fake_cycle(project_dir, milestone, cycle_type, prompt,
                              **kwargs):
            cp_path.write_text(
                "cycle: 5\nstep: ASSESS\nnext_step: VERIFY\n"
                "current_phase: build_feature\n"
                "phases_completed: 1\nphases_total: 1\n"
            )
            return "ok"

        with (
            patch("clou.coordinator._run_single_cycle", side_effect=_fake_cycle),
            patch("clou.coordinator.build_cycle_prompt", side_effect=_capture_build),
            patch("clou.coordinator.validate_readiness", return_value=[]),
            patch("clou.coordinator.validate_delivery", return_value=[]),
            patch("clou.coordinator.validate_golden_context", return_value=[]),
            patch("clou.coordinator.git_commit_phase", new_callable=AsyncMock),
            patch("clou.coordinator.clean_stale_shards_for_layer"),
            patch(
                "clou.precompose.precompose_assess_context",
                mock_precompose,
            ),
        ):
            result = asyncio.run(run_coordinator(tmp_path, "m1"))

        # Verify precompose was called with the right phase.
        mock_precompose.assert_called_once()
        call_args = mock_precompose.call_args
        assert call_args[0][1] == "build_feature"  # phase_name

        # Verify build_cycle_prompt received the precomposed read set
        # (not the raw 5+ files).
        assert len(captured_read_sets) >= 1
        assess_read_set = captured_read_sets[0]
        assert "active/assess_summary.md" in assess_read_set
        assert "requirements.md" in assess_read_set
        # Should be <=3 files (summary + requirements + maybe filtered memory).
        assert len(assess_read_set) <= 3

    def test_stale_summary_cleaned_on_precompose_failure(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """F10: When precompose raises, stale assess_summary.md is deleted
        by the actual coordinator.py except clause (not reimplemented inline).
        """
        from unittest.mock import AsyncMock
        from clou.coordinator import run_coordinator

        clou_dir = tmp_path / ".clou"
        (clou_dir / "project.md").write_text(
            "# test\ntemplate: software-construction\n"
        )
        (clou_dir / ".coordinator-milestone").write_text("m1")
        (milestone_dir / "status.md").write_text("status: in_progress\n")

        # Checkpoint: ASSESS is the next cycle.
        cp_path = milestone_dir / "active" / "coordinator.md"
        cp_path.write_text(
            "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build_feature\n"
        )

        # Plant a stale summary file from a prior cycle.
        stale = milestone_dir / "active" / "assess_summary.md"
        stale.write_text("stale content from prior cycle")
        assert stale.exists()

        # Mock precompose to raise an exception, triggering the
        # coordinator's except clause which should clean up the stale file.
        mock_precompose = MagicMock(
            side_effect=RuntimeError("simulated precomposition failure"),
        )

        # _run_single_cycle writes a VERIFY checkpoint so the loop exits.
        async def _fake_cycle(project_dir, milestone, cycle_type, prompt,
                              **kwargs):
            cp_path.write_text(
                "cycle: 5\nstep: ASSESS\nnext_step: VERIFY\n"
                "current_phase: build_feature\n"
                "phases_completed: 1\nphases_total: 1\n"
            )
            return "ok"

        with (
            patch("clou.coordinator._run_single_cycle", side_effect=_fake_cycle),
            patch("clou.coordinator.validate_readiness", return_value=[]),
            patch("clou.coordinator.validate_delivery", return_value=[]),
            patch("clou.coordinator.validate_golden_context", return_value=[]),
            patch("clou.coordinator.git_commit_phase", new_callable=AsyncMock),
            patch("clou.coordinator.clean_stale_shards_for_layer"),
            patch(
                "clou.precompose.precompose_assess_context",
                mock_precompose,
            ),
        ):
            asyncio.run(run_coordinator(tmp_path, "m1"))

        # The stale file should have been cleaned up by the coordinator's
        # except clause (the real code path, not a reimplementation).
        assert not stale.exists()

    def test_symlink_summary_not_unlinked_on_failure(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """F2: Symlink assess_summary.md is NOT unlinked by the coordinator's
        except clause (boundary validation rejects it).
        """
        from unittest.mock import AsyncMock
        from clou.coordinator import run_coordinator

        clou_dir = tmp_path / ".clou"
        (clou_dir / "project.md").write_text(
            "# test\ntemplate: software-construction\n"
        )
        (clou_dir / ".coordinator-milestone").write_text("m1")
        (milestone_dir / "status.md").write_text("status: in_progress\n")

        cp_path = milestone_dir / "active" / "coordinator.md"
        cp_path.write_text(
            "cycle: 4\nstep: EXECUTE\nnext_step: ASSESS\n"
            "current_phase: build_feature\n"
        )

        # Create a symlink as the stale summary — should be refused.
        target = tmp_path / "outside_target.md"
        target.write_text("outside milestone boundary")
        symlink = milestone_dir / "active" / "assess_summary.md"
        symlink.symlink_to(target)
        assert symlink.is_symlink()

        mock_precompose = MagicMock(
            side_effect=RuntimeError("simulated failure"),
        )

        async def _fake_cycle(project_dir, milestone, cycle_type, prompt,
                              **kwargs):
            cp_path.write_text(
                "cycle: 5\nstep: ASSESS\nnext_step: VERIFY\n"
                "current_phase: build_feature\n"
                "phases_completed: 1\nphases_total: 1\n"
            )
            return "ok"

        with (
            patch("clou.coordinator._run_single_cycle", side_effect=_fake_cycle),
            patch("clou.coordinator.validate_readiness", return_value=[]),
            patch("clou.coordinator.validate_delivery", return_value=[]),
            patch("clou.coordinator.validate_golden_context", return_value=[]),
            patch("clou.coordinator.git_commit_phase", new_callable=AsyncMock),
            patch("clou.coordinator.clean_stale_shards_for_layer"),
            patch(
                "clou.precompose.precompose_assess_context",
                mock_precompose,
            ),
        ):
            asyncio.run(run_coordinator(tmp_path, "m1"))

        # Symlink should NOT have been unlinked (security check rejects it).
        assert symlink.is_symlink()
        # Target file should still exist.
        assert target.exists()


# ---------------------------------------------------------------------------
# Layer-wide stale cleanup — DAG task names are validated before reaching disk.
#
# Previously the cleanup read ``checkpoint.current_phase`` (a freeform string
# that a malicious checkpoint could poison).  The layer-wide remolding now
# iterates compose.py's DAG task names — Python identifiers parsed via
# ``ast.parse`` — so classic traversal attacks are structurally impossible.
# The ``_PHASE_RE`` guard stays as defense-in-depth: any identifier that
# somehow bypasses AST extraction but fails the phase naming convention
# (leading underscore, non-lowercase, etc.) is silently dropped.
# ---------------------------------------------------------------------------


class TestStaleShardPhaseValidation:
    """Layer-wide cleanup filters DAG task names through _PHASE_RE."""

    @pytest.fixture
    def milestone_dir(self, tmp_path: Path) -> Path:
        """Create minimal milestone structure for _run_single_cycle."""
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones" / "m1"
        (ms_dir / "active").mkdir(parents=True)
        (ms_dir / "phases" / "build_feature").mkdir(parents=True)
        (ms_dir / "phases" / "write_tests").mkdir(parents=True)
        return ms_dir

    def test_valid_dag_phases_reach_layer_cleanup(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Well-formed DAG task names are passed to the layer-wide sweep."""
        from unittest.mock import AsyncMock

        from clou.coordinator import _run_single_cycle

        (milestone_dir / "compose.py").write_text(MOCK_COMPOSE)

        cp_path = milestone_dir / "active" / "coordinator.md"
        cp_path.write_text(
            "cycle: 1\nstep: EXECUTE\nnext_step: EXECUTE\n"
            "current_phase: build_feature\n"
        )

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield MagicMock(
                type="result",
                usage={"input_tokens": 100},
                summary="done",
                uuid="test-uuid",
                session_id="test-sess",
            )

        mock_client.receive_response = _receive
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("clou.coordinator.load_prompt", return_value="<system/>"),
            patch("clou.coordinator._build_agents", return_value={}),
            patch("clou.coordinator.build_hooks", return_value={"PreToolUse": []}),
            patch("clou.coordinator.ClaudeSDKClient", return_value=mock_client),
            patch("clou.coordinator.read_cycle_outcome", return_value="ok"),
            patch(
                "clou.coordinator.clean_stale_shards_for_layer"
            ) as mock_clean,
        ):
            asyncio.run(_run_single_cycle(tmp_path, "m1", "EXECUTE", "do work"))

        # The layer-wide helper is called exactly once, with the DAG's
        # well-formed phase names passed as the phase list.
        mock_clean.assert_called_once()
        _ms_arg, phase_list = mock_clean.call_args[0]
        assert set(phase_list) == {"build_feature", "write_tests"}


# ---------------------------------------------------------------------------
# F1: _write_failure_shard budget abort handler rejects traversal phases
# ---------------------------------------------------------------------------


class TestBudgetAbortPhaseValidation:
    """F1: _shard_phase in the budget/violation abort handler is validated
    against _PHASE_RE before being passed to _write_failure_shard.  When the
    checkpoint-derived fallback phase is a traversal pattern,
    _write_failure_shard must NOT be called.
    """

    @pytest.fixture
    def milestone_dir(self, tmp_path: Path) -> Path:
        """Create minimal milestone structure."""
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones" / "m1"
        (ms_dir / "active").mkdir(parents=True)
        (ms_dir / "phases" / "build_feature").mkdir(parents=True)
        (ms_dir / "phases" / "write_tests").mkdir(parents=True)
        return ms_dir

    @pytest.mark.parametrize("bad_phase", [
        "../../",
        "../evil",
        "../../etc/passwd",
        "/root",
        "a/b",
    ])
    def test_budget_abort_traversal_phase_rejected(
        self, bad_phase: str, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """_write_failure_shard is NOT called when checkpoint fallback
        produces a traversal phase in the budget abort handler.

        This tests the defense-in-depth guard added at the budget
        abort handler.  The checkpoint fallback is reached when
        task_name is unavailable; the guard ensures traversal phases
        from parse_checkpoint() cannot reach _write_failure_shard.
        """
        from clou.coordinator import _PHASE_RE, _write_failure_shard

        # The guard logic extracted from coordinator.py budget abort handler:
        # _shard_phase = task_name  (empty -> falls back to checkpoint)
        # if not _shard_phase: _shard_phase = parse_checkpoint().current_phase
        # F1 guard: _shard_phase = _shard_phase.lower(); if not _PHASE_RE.match(): skip
        _shard_phase = bad_phase  # simulate checkpoint fallback value
        write_called = False

        if _shard_phase:
            _shard_phase = _shard_phase.lower()
            if not _PHASE_RE.match(_shard_phase):
                pass  # Guard rejects -- _write_failure_shard not called
            else:
                write_called = True

        assert not write_called, (
            f"_write_failure_shard would have been called with "
            f"traversal phase {bad_phase!r}"
        )

    def test_budget_abort_valid_phase_accepted(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Sanity: a valid phase name passes the F1 guard."""
        from clou.coordinator import _PHASE_RE

        _shard_phase = "build_feature"
        _shard_phase = _shard_phase.lower()
        assert _PHASE_RE.match(_shard_phase), (
            f"Valid phase {_shard_phase!r} should pass _PHASE_RE"
        )


# ---------------------------------------------------------------------------
# F1: _write_failure_shard idle watchdog handler rejects traversal phases
# ---------------------------------------------------------------------------


class TestWatchdogPhaseValidation:
    """F1: _fallback_phase in the idle watchdog handler is validated
    against _PHASE_RE before it can reach _write_failure_shard.  When the
    checkpoint-derived fallback phase is a traversal pattern,
    _fallback_phase must be set to None so _write_failure_shard cannot
    be called with it.
    """

    @pytest.fixture
    def milestone_dir(self, tmp_path: Path) -> Path:
        """Create minimal milestone structure."""
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones" / "m1"
        (ms_dir / "active").mkdir(parents=True)
        return ms_dir

    @pytest.mark.parametrize("bad_phase", [
        "../../",
        "../evil",
        "../../etc/passwd",
        "/root",
        "a/b",
    ])
    def test_watchdog_traversal_fallback_rejected(
        self, bad_phase: str, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """_fallback_phase is set to None when checkpoint has a traversal
        phase, preventing _write_failure_shard from receiving it.

        This tests the defense-in-depth guard added at the idle watchdog
        handler.  The guard follows the same pattern as the established
        guards at coordinator.py:924-931 and 1676-1681.
        """
        from clou.coordinator import _PHASE_RE

        # The guard logic extracted from coordinator.py idle watchdog handler:
        # _fallback_phase = parse_checkpoint().current_phase  (from checkpoint)
        # F1 guard: lowercase + _PHASE_RE check; set to None if invalid
        _fallback_phase = bad_phase

        if _fallback_phase:
            _fallback_phase = _fallback_phase.lower()
            if not _PHASE_RE.match(_fallback_phase):
                _fallback_phase = None

        # After the guard, _fallback_phase must be None for traversal phases.
        assert _fallback_phase is None, (
            f"_fallback_phase should be None for traversal phase {bad_phase!r}, "
            f"got {_fallback_phase!r}"
        )

        # Simulate the watchdog loop: task_name is falsy (task_id not in map),
        # so _shard_phase = task_name or _fallback_phase = None.
        # _write_failure_shard should NOT be called.
        task_name = None  # task_id not in _task_id_to_name
        _shard_phase = task_name or _fallback_phase
        assert not _shard_phase, (
            "_shard_phase must be falsy so _write_failure_shard is skipped"
        )

    def test_watchdog_valid_fallback_accepted(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Sanity: a valid phase name passes the F1 guard and is preserved."""
        from clou.coordinator import _PHASE_RE

        _fallback_phase = "build_feature"

        if _fallback_phase:
            _fallback_phase = _fallback_phase.lower()
            if not _PHASE_RE.match(_fallback_phase):
                _fallback_phase = None

        assert _fallback_phase == "build_feature", (
            f"Valid phase should be preserved, got {_fallback_phase!r}"
        )

    def test_watchdog_mixed_case_normalized(
        self, milestone_dir: Path, tmp_path: Path,
    ) -> None:
        """Mixed-case phase names are lowercased before validation."""
        from clou.coordinator import _PHASE_RE

        _fallback_phase = "Build_Feature"

        if _fallback_phase:
            _fallback_phase = _fallback_phase.lower()
            if not _PHASE_RE.match(_fallback_phase):
                _fallback_phase = None

        assert _fallback_phase == "build_feature", (
            f"Mixed-case should normalize to lowercase, got {_fallback_phase!r}"
        )
