"""Tests for P1 (Stream C continuation): rework-vs-stall classification
via artifact evidence.

Under the zero-escalations principle, the staleness detector must not
fire when a cycle produced real work that happens not to increment
phases_completed (the rework case).  These tests exercise
``snapshot_milestone_artifacts`` and the new ``cycle_produced_evidence``
reset rule in ``update_staleness``.

The telemetry incident that motivated P1: M41 cycle 3 EXECUTE rework
produced 88k tokens, 4 implementer agents, 23 valid findings -- but
phases_completed stayed at 5/5 (by design of rework), the detector
counted 3 consecutive EXECUTE cycles, and escalated_staleness fired.
A single human-visible escalation that the system should have closed
itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.coordinator import (
    StalenessState,
    snapshot_milestone_artifacts,
    update_staleness,
)


class TestSnapshotMilestoneArtifacts:
    """Cheap (size, mtime) snapshot of relevant milestone artifacts."""

    def test_empty_directory_returns_empty_dict(self, tmp_path: Path) -> None:
        assert snapshot_milestone_artifacts(tmp_path / "ms") == {}

    def test_captures_decisions_md(self, tmp_path: Path) -> None:
        """Snapshot stores a sha256 content hash per tracked file."""
        import hashlib
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "decisions.md").write_text("initial\n")
        snap = snapshot_milestone_artifacts(ms)
        assert "decisions.md" in snap
        assert snap["decisions.md"] == hashlib.sha256(b"initial\n").hexdigest()

    def test_captures_phase_execution_files(self, tmp_path: Path) -> None:
        ms = tmp_path / "ms"
        (ms / "phases" / "p1").mkdir(parents=True)
        (ms / "phases" / "p2").mkdir(parents=True)
        (ms / "phases" / "p1" / "execution.md").write_text("p1 work")
        (ms / "phases" / "p2" / "execution.md").write_text("p2 work")
        snap = snapshot_milestone_artifacts(ms)
        assert "phases/p1/execution.md" in snap
        assert "phases/p2/execution.md" in snap

    def test_captures_assessment_and_compose(self, tmp_path: Path) -> None:
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "assessment.md").write_text("findings")
        (ms / "compose.py").write_text("async def execute(): pass")
        snap = snapshot_milestone_artifacts(ms)
        assert "assessment.md" in snap
        assert "compose.py" in snap

    def test_excludes_active_checkpoint(self, tmp_path: Path) -> None:
        """Active checkpoint files mutate every cycle by design; if
        they counted, every cycle would look like progress and the
        detector would never fire.
        """
        ms = tmp_path / "ms"
        (ms / "active").mkdir(parents=True)
        (ms / "active" / "coordinator.md").write_text("checkpoint")
        snap = snapshot_milestone_artifacts(ms)
        assert not any("active" in k for k in snap), snap

    def test_excludes_status_md(self, tmp_path: Path) -> None:
        """status.md is a render-only view of the checkpoint (M21 R1);
        counting it would inherit the checkpoint's always-mutating
        behavior.
        """
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "status.md").write_text("status")
        snap = snapshot_milestone_artifacts(ms)
        assert "status.md" not in snap

    def test_size_change_surfaces(self, tmp_path: Path) -> None:
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "decisions.md").write_text("one line\n")
        s1 = snapshot_milestone_artifacts(ms)
        (ms / "decisions.md").write_text("one line\ntwo lines\n")
        s2 = snapshot_milestone_artifacts(ms)
        assert s1 != s2

    def test_mtime_only_change_is_not_evidence(self, tmp_path: Path) -> None:
        """P1-review brutalist 3-of-3 finding: `git checkout` and hook
        re-saves update mtime without changing content.  Content-hash
        snapshot must be immune to these false-evidence sources.
        """
        import os
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "decisions.md").write_text("same content\n")
        s1 = snapshot_milestone_artifacts(ms)
        # Touch the file: update mtime but keep content identical.
        # Use a future timestamp to ensure mtime difference even at
        # second granularity.
        import time as _time
        future = _time.time() + 3600
        os.utime(ms / "decisions.md", (future, future))
        s2 = snapshot_milestone_artifacts(ms)
        assert s1 == s2, (
            "content-hash snapshot must ignore mtime-only changes"
        )

    def test_same_size_different_content_surfaces(self, tmp_path: Path) -> None:
        """Same byte-count but different content must register as
        evidence.  (size, mtime) would have missed this on fast
        filesystems where mtime resolution is coarser than write
        timing.)
        """
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "decisions.md").write_text("AAAAAA")
        s1 = snapshot_milestone_artifacts(ms)
        (ms / "decisions.md").write_text("BBBBBB")  # same length
        s2 = snapshot_milestone_artifacts(ms)
        assert s1 != s2

    def test_symlink_escape_is_not_hashed(self, tmp_path: Path) -> None:
        """P1-review brutalist finding: a malicious phase directory
        symlink pointing outside the milestone dir would let an
        attacker generate false progress evidence by changing an
        external file (e.g., a system log).  The snapshot must skip
        such symlinks.
        """
        import os
        ms = tmp_path / "ms"
        (ms / "phases" / "evil").mkdir(parents=True)
        # External file outside the milestone dir.
        external = tmp_path / "outside.md"
        external.write_text("external content")
        # execution.md in the phase points outside.
        link_target = ms / "phases" / "evil" / "execution.md"
        os.symlink(external, link_target)
        snap = snapshot_milestone_artifacts(ms)
        # The escaping symlink must not appear in the snapshot.
        assert "phases/evil/execution.md" not in snap

    def test_new_file_surfaces(self, tmp_path: Path) -> None:
        ms = tmp_path / "ms"
        ms.mkdir()
        s1 = snapshot_milestone_artifacts(ms)
        (ms / "decisions.md").write_text("new")
        s2 = snapshot_milestone_artifacts(ms)
        assert s1 != s2

    def test_unrelated_file_ignored(self, tmp_path: Path) -> None:
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "something-else.txt").write_text("not tracked")
        snap = snapshot_milestone_artifacts(ms)
        # Only tracked patterns appear.
        assert "something-else.txt" not in snap


class TestUpdateStalenessEvidence:
    """The zero-escalations reset rule: cycle_produced_evidence resets
    the staleness counter regardless of cycle_type or phases_completed.
    """

    def test_evidence_resets_counter_to_one(self) -> None:
        """The M41 scenario: 2 prior EXECUTE cycles built up count=2,
        and this cycle is also EXECUTE with no phase advancement --
        but it produced rework evidence.  Counter must reset.
        """
        state = StalenessState(
            count=2,
            prev_cycle_type="EXECUTE",
            prev_phases_completed=5,
            saw_type_change=False,
            last_cycle_outcome="ADVANCED",
        )
        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=True,
        )
        assert state.count == 1

    def test_no_evidence_preserves_legacy_behavior(self) -> None:
        """When cycle_produced_evidence is False, the detector behaves
        exactly as before: same type + same phases = counter increments.
        """
        state = StalenessState(
            count=2,
            prev_cycle_type="EXECUTE",
            prev_phases_completed=5,
            saw_type_change=False,
            last_cycle_outcome="ADVANCED",
        )
        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=False,
        )
        assert state.count == 3

    def test_inconclusive_wins_over_evidence(self) -> None:
        """INCONCLUSIVE / INTERRUPTED outcomes reset to 0 (not stuck)
        and the evidence flag doesn't override that.  Rule order
        matters: outcome check is first.
        """
        state = StalenessState(
            count=2, last_cycle_outcome="INCONCLUSIVE",
        )
        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=True,
        )
        # INCONCLUSIVE resets to 0, not 1.  (Both are non-escalating
        # outcomes, but the semantics differ: "no cycle ran" vs
        # "cycle ran with progress".)
        assert state.count == 0

    def test_repeated_stall_still_escalates_without_evidence(self) -> None:
        """Regression guard: a truly stalled run (same type, no
        phase advancement, no evidence) must still trip the detector.
        P1 must not over-correct -- the pre-existing saw_type_change
        reset-once rule means count=3 is reached on the 4th
        consecutive same-type no-evidence cycle, so iterate 4 times.
        """
        state = StalenessState()
        for _ in range(4):
            update_staleness(
                state, cycle_type="EXECUTE", phases_completed=0,
                cycle_produced_evidence=False,
            )
        assert state.count >= 3

    def test_rework_cycle_sequence_never_escalates(self) -> None:
        """End-to-end: a milestone that runs 10 EXECUTE rework cycles
        with artifact evidence each time does NOT escalate.  Models
        a plausible complex milestone with many rework rounds.
        """
        state = StalenessState()
        for _ in range(10):
            update_staleness(
                state, cycle_type="EXECUTE", phases_completed=5,
                cycle_produced_evidence=True,
            )
        # Count never accumulates past 1 because every cycle resets.
        assert state.count == 1

    def test_mixed_evidence_and_no_evidence_counts_only_no_evidence(
        self,
    ) -> None:
        """A cycle with evidence followed by cycles without evidence:
        the counter restarts from the evidence cycle and counts only
        subsequent evidence-free cycles.
        """
        state = StalenessState()
        # Cycle 1: evidence (counter → 1)
        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=True,
        )
        assert state.count == 1
        # Cycle 2: no evidence, same type, same phases (counter → 2)
        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=False,
        )
        assert state.count == 2
        # Cycle 3: no evidence (counter → 3, threshold hit)
        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=False,
        )
        assert state.count == 3


class TestIntegrationSnapshotPlusUpdate:
    """The full producer->consumer chain: snapshot captures state;
    comparing two snapshots produces the cycle_produced_evidence
    boolean; update_staleness uses it to classify rework correctly.
    """

    def test_rework_cycle_scenario_end_to_end(self, tmp_path: Path) -> None:
        """Reconstruct M41 cycle-3 scenario and verify the detector
        now classifies it as rework, not stall.
        """
        ms = tmp_path / "ms"
        (ms / "phases" / "p1").mkdir(parents=True)
        (ms / "phases" / "p1" / "execution.md").write_text("initial")
        (ms / "decisions.md").write_text("## Cycle 1\n")
        (ms / "assessment.md").write_text("findings v1")

        # Baseline before cycle 3 (all 5 phases already marked complete).
        before = snapshot_milestone_artifacts(ms)
        state = StalenessState(
            count=2,  # 2 prior EXECUTE cycles under the old detector
            prev_cycle_type="EXECUTE",
            prev_phases_completed=5,
        )

        # Cycle 3 runs: rework workers edit assessment + decisions +
        # a phase execution.md.  Phases_completed stays at 5.
        (ms / "assessment.md").write_text("findings v1 + cycle-3 classifications")
        (ms / "decisions.md").write_text("## Cycle 3\n## Cycle 1\n")
        (ms / "phases" / "p1" / "execution.md").write_text(
            "initial\n## Cycle 3 rework\n"
        )

        after = snapshot_milestone_artifacts(ms)
        assert after != before

        update_staleness(
            state, cycle_type="EXECUTE", phases_completed=5,
            cycle_produced_evidence=(after != before),
        )
        # Under the old detector this would have been count=3 and
        # escalated.  Under P1, it resets to 1.
        assert state.count == 1

    def test_stalled_cycle_scenario_end_to_end(self, tmp_path: Path) -> None:
        """Opposite case: cycles run without ANY artifact changes.
        This is a real stall and should still escalate (P1 must not
        over-correct and hide legitimate stalls).
        """
        ms = tmp_path / "ms"
        ms.mkdir()
        (ms / "decisions.md").write_text("frozen\n")

        state = StalenessState()
        for _ in range(4):
            before = snapshot_milestone_artifacts(ms)
            # Cycle runs but produces no artifact changes.
            after = snapshot_milestone_artifacts(ms)
            update_staleness(
                state, cycle_type="EXECUTE", phases_completed=0,
                cycle_produced_evidence=(after != before),
            )
        assert state.count >= 3  # Threshold hit; would escalate.


