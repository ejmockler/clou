"""Tests for the engine-side phase-acceptance gate integration
(M52 F32 / task #180).

The engine calls ``check_phase_acceptance`` once per ASSESS cycle and
persists the verdict into the checkpoint envelope before the LLM
ASSESS prompt fires.  Tests cover:

* ``parse_phase_deliverable_type`` — parser for the new typed phase.md
  ``## Deliverable\\ntype: <name>`` block, with graceful None for
  legacy phase.md (pre-M52).
* ``_run_phase_acceptance_gate`` — engine helper that wires the parser
  + gate + checkpoint write together; fail-soft on every input class.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.artifacts import compute_content_sha, parse_phase_deliverable_type
from clou.coordinator import _run_phase_acceptance_gate
from clou.golden_context import render_checkpoint
from clou.recovery_checkpoint import AcceptanceVerdict, parse_checkpoint


# ---------------------------------------------------------------------------
# parse_phase_deliverable_type
# ---------------------------------------------------------------------------


class TestParsePhaseDeliverableType:
    def test_extracts_type_from_canonical_section(self) -> None:
        body = (
            "# phase: p1\n\n"
            "## Purpose\n\nDo a thing.\n\n"
            "## Deliverable\n"
            "type: judgment_layer_spec\n"
            "acceptance: schema_pass\n\n"
            "## Hard constraints\n"
        )
        assert parse_phase_deliverable_type(body) == "judgment_layer_spec"

    def test_legacy_phase_md_returns_none(self) -> None:
        """Pre-M52 phase.md uses ``Single deliverable file: <path>`` and
        has no typed declaration.  The parser must return None so the
        engine can skip the gate gracefully."""
        body = (
            "# phase: p1\n\n"
            "## Purpose\n\nDo a thing.\n\n"
            "## Hard constraints\n"
            "- **Single deliverable file:** `phases/p1/spec.md`.\n"
        )
        assert parse_phase_deliverable_type(body) is None

    def test_deliverable_section_without_type_returns_none(self) -> None:
        body = (
            "## Deliverable\n\n"
            "A structured doc covering nine sections.\n\n"
            "## Tests\n"
        )
        assert parse_phase_deliverable_type(body) is None

    def test_only_first_deliverable_section_consulted(self) -> None:
        """If for some reason the file has two ``## Deliverable``
        headings, the first wins.  This stops a stray copy-paste from
        poisoning gate routing."""
        body = (
            "## Deliverable\n"
            "type: execution_summary\n\n"
            "## Notes\n\n"
            "## Deliverable\n"
            "type: judgment_layer_spec\n"
        )
        assert parse_phase_deliverable_type(body) == "execution_summary"

    def test_type_line_outside_deliverable_section_ignored(self) -> None:
        """A ``type:`` line in some other section must not be picked up."""
        body = (
            "## Purpose\n"
            "type: not_real\n\n"
            "## Deliverable\n"
            "acceptance: schema_pass\n"
        )
        assert parse_phase_deliverable_type(body) is None

    def test_empty_input_returns_none(self) -> None:
        assert parse_phase_deliverable_type("") is None


# ---------------------------------------------------------------------------
# _run_phase_acceptance_gate
# ---------------------------------------------------------------------------


def _make_phase_dir(
    tmp_path: Path,
    *,
    milestone: str,
    phase: str,
) -> Path:
    phase_dir = (
        tmp_path / ".clou" / "milestones" / milestone / "phases" / phase
    )
    phase_dir.mkdir(parents=True, exist_ok=True)
    return phase_dir


def _seed_checkpoint(
    tmp_path: Path,
    *,
    milestone: str,
    phase: str,
    last_acceptance_verdict: AcceptanceVerdict | None = None,
) -> Path:
    cp_path = (
        tmp_path / ".clou" / "milestones" / milestone
        / "active" / "coordinator.md"
    )
    cp_path.parent.mkdir(parents=True, exist_ok=True)
    cp_path.write_text(
        render_checkpoint(
            cycle=1,
            step="ASSESS",
            next_step="ASSESS",
            current_phase=phase,
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=last_acceptance_verdict,
        ),
        encoding="utf-8",
    )
    return cp_path


_VALID_EXEC_SUMMARY_BODY = (
    "status: completed\n"
    "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
    "failures: none\n"
    "blockers: none\n"
)


def _wrap_artifact(
    body: str,
    *,
    milestone: str,
    phase: str,
    type_name: str = "execution_summary",
) -> str:
    sha = compute_content_sha(body)
    return (
        f'````artifact milestone="{milestone}" phase="{phase}" '
        f'type="{type_name}" id="{sha}"\n'
        f"{body}\n"
        f"````\n"
    )


class TestRunPhaseAcceptanceGate:
    def test_advance_verdict_persisted_to_checkpoint(
        self, tmp_path: Path,
    ) -> None:
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Deliverable\n"
            "type: execution_summary\n"
            "acceptance: schema_pass\n",
            encoding="utf-8",
        )
        (phase_dir / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone=ms,
                phase=phase,
            ),
            encoding="utf-8",
        )
        cp_path = _seed_checkpoint(tmp_path, milestone=ms, phase=phase)

        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )

        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict is not None
        assert cp.last_acceptance_verdict.decision == "Advance"
        assert cp.last_acceptance_verdict.phase == phase
        assert cp.last_acceptance_verdict.content_sha == compute_content_sha(
            _VALID_EXEC_SUMMARY_BODY,
        )

    def test_gate_deadlock_verdict_persisted(self, tmp_path: Path) -> None:
        """Schema-failing execution.md → GateDeadlock written into the
        checkpoint envelope so the LLM's next advance attempt is
        refused by the verdict-gate validation."""
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Deliverable\n"
            "type: execution_summary\n"
            "acceptance: schema_pass\n",
            encoding="utf-8",
        )
        # Missing ``blockers:`` field → schema_mismatch.
        bad_body = (
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
        )
        (phase_dir / "execution.md").write_text(
            _wrap_artifact(bad_body, milestone=ms, phase=phase),
            encoding="utf-8",
        )
        cp_path = _seed_checkpoint(tmp_path, milestone=ms, phase=phase)

        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )

        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict is not None
        assert cp.last_acceptance_verdict.decision == "GateDeadlock"

    def test_legacy_phase_md_skips_gate(self, tmp_path: Path) -> None:
        """Pre-M52 phase.md: gate is a no-op.  The verdict in the
        checkpoint must remain unchanged (None in this case)."""
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Hard constraints\n"
            "- Single deliverable file: phases/p1/spec.md\n",
            encoding="utf-8",
        )
        (phase_dir / "execution.md").write_text(
            "## Summary\nstatus: completed\n",
            encoding="utf-8",
        )
        cp_path = _seed_checkpoint(tmp_path, milestone=ms, phase=phase)

        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )

        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        # Verdict unchanged — engine refused to write one.
        assert cp.last_acceptance_verdict is None

    def test_missing_phase_md_is_noop(self, tmp_path: Path) -> None:
        ms = "test-ms"
        phase = "p1"
        # Don't create phase.md.
        cp_path = _seed_checkpoint(tmp_path, milestone=ms, phase=phase)
        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict is None

    def test_missing_execution_md_is_noop(self, tmp_path: Path) -> None:
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Deliverable\n"
            "type: execution_summary\n",
            encoding="utf-8",
        )
        # Don't create execution.md.
        cp_path = _seed_checkpoint(tmp_path, milestone=ms, phase=phase)
        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict is None

    def test_missing_checkpoint_is_noop(self, tmp_path: Path) -> None:
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Deliverable\n"
            "type: execution_summary\n",
            encoding="utf-8",
        )
        (phase_dir / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone=ms,
                phase=phase,
            ),
            encoding="utf-8",
        )
        # Don't write a checkpoint.
        cp_path = (
            tmp_path / ".clou" / "milestones" / ms
            / "active" / "coordinator.md"
        )
        # Should not raise, should not create the file.
        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )
        assert not cp_path.exists()

    def test_gate_only_changes_verdict_field(self, tmp_path: Path) -> None:
        """The engine-side write must NOT bump phases_completed (F32
        single-writer protocol).  Only the verdict field changes."""
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Deliverable\n"
            "type: execution_summary\n",
            encoding="utf-8",
        )
        (phase_dir / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone=ms,
                phase=phase,
            ),
            encoding="utf-8",
        )
        cp_path = _seed_checkpoint(tmp_path, milestone=ms, phase=phase)
        before = parse_checkpoint(cp_path.read_text(encoding="utf-8"))

        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )

        after = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        # Only the verdict changed.
        assert after.cycle == before.cycle
        assert after.step == before.step
        assert after.next_step == before.next_step
        assert after.current_phase == before.current_phase
        assert after.phases_completed == before.phases_completed
        assert after.phases_total == before.phases_total
        # Verdict was None, now Advance.
        assert before.last_acceptance_verdict is None
        assert after.last_acceptance_verdict is not None
        assert after.last_acceptance_verdict.decision == "Advance"

    def test_gate_overwrites_stale_verdict(self, tmp_path: Path) -> None:
        """If the checkpoint already carries a verdict (from a prior
        cycle), the gate's fresh verdict overwrites it."""
        ms = "test-ms"
        phase = "p1"
        phase_dir = _make_phase_dir(tmp_path, milestone=ms, phase=phase)
        (phase_dir / "phase.md").write_text(
            "## Deliverable\n"
            "type: execution_summary\n",
            encoding="utf-8",
        )
        (phase_dir / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone=ms,
                phase=phase,
            ),
            encoding="utf-8",
        )
        stale = AcceptanceVerdict(
            phase="p_old",
            decision="GateDeadlock",
            content_sha="",
        )
        cp_path = _seed_checkpoint(
            tmp_path,
            milestone=ms,
            phase=phase,
            last_acceptance_verdict=stale,
        )
        _run_phase_acceptance_gate(
            project_dir=tmp_path,
            milestone=ms,
            phase=phase,
            checkpoint_path=cp_path,
        )
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict is not None
        assert cp.last_acceptance_verdict.phase == phase
        assert cp.last_acceptance_verdict.decision == "Advance"
