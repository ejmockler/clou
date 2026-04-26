"""End-to-end integration test for the substance-typed phase
acceptance flow (M52 task #152).

The test exercises the full pipeline that closes M51's deadlock class:

    worker writes execution.md
    engine calls _run_phase_acceptance_gate (writes verdict)
    LLM calls clou_write_checkpoint (advances phases_completed)
    tool validates against prev_cp.last_acceptance_verdict

The contract: **the LLM cannot advance phases_completed without an
``Advance`` verdict for the right phase.**  This is the primary
regression: the M51 deadlock disappears when execution.md carries a
typed artifact that the gate accepts; the same content without a typed
artifact (legacy phase.md path) still routes through the migration
shim, but a *failing* typed artifact is *refused* — the LLM cannot
self-judge past the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.artifacts import compute_content_sha
from clou.coordinator import _run_phase_acceptance_gate
from clou.golden_context import render_checkpoint
from clou.recovery_checkpoint import AcceptanceVerdict, parse_checkpoint


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


def _find_tool(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found")


@pytest.fixture
def integration_setup(tmp_path: Path):
    """Build the coordinator's MCP tool set + a phase scaffold."""
    pytest.importorskip("claude_agent_sdk")
    from clou.coordinator_tools import _build_coordinator_tools

    ms = "test-ms"
    phase = "p1"
    ms_dir = tmp_path / ".clou" / "milestones" / ms
    phase_dir = ms_dir / "phases" / phase
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "phase.md").write_text(
        "## Purpose\n\nDo p1.\n\n"
        "## Deliverable\n"
        "type: execution_summary\n"
        "acceptance: schema_pass\n",
        encoding="utf-8",
    )
    tools = _build_coordinator_tools(tmp_path, ms)
    return {
        "tmp_path": tmp_path,
        "ms": ms,
        "phase": phase,
        "phase_dir": phase_dir,
        "tools": tools,
        "checkpoint_path": ms_dir / "active" / "coordinator.md",
    }


def _seed_pre_gate_checkpoint(setup: dict, *, current_phase: str = "p1") -> None:
    """Engine state at the start of an ASSESS cycle, before the gate
    runs.  No verdict yet.  ``phases_completed`` is one less than the
    phase about to be claimed."""
    setup["checkpoint_path"].parent.mkdir(parents=True, exist_ok=True)
    setup["checkpoint_path"].write_text(
        render_checkpoint(
            cycle=2,
            step="ASSESS",
            next_step="ASSESS",
            current_phase=current_phase,
            phases_completed=0,
            phases_total=3,
        ),
        encoding="utf-8",
    )


class TestEndToEndAdvance:
    """The bug-becomes-feature regression: an execution.md that the
    gate accepts authorises a single-phase advance."""

    @pytest.mark.asyncio
    async def test_gate_advance_then_llm_advance_succeeds(
        self, integration_setup,
    ) -> None:
        setup = integration_setup
        _seed_pre_gate_checkpoint(setup, current_phase=setup["phase"])
        # Worker output: a typed artifact that validates.
        (setup["phase_dir"] / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone=setup["ms"],
                phase=setup["phase"],
            ),
            encoding="utf-8",
        )

        # Engine path: gate runs, writes Advance verdict.
        _run_phase_acceptance_gate(
            project_dir=setup["tmp_path"],
            milestone=setup["ms"],
            phase=setup["phase"],
            checkpoint_path=setup["checkpoint_path"],
        )
        cp_after_gate = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp_after_gate.last_acceptance_verdict is not None
        assert cp_after_gate.last_acceptance_verdict.decision == "Advance"

        # LLM path: claim advance via clou_write_checkpoint.
        handler = _find_tool(setup["tools"], "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result
        cp_after_advance = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp_after_advance.phases_completed == 1


class TestEndToEndRefusal:
    """The deadlock-class regression: a failing typed artifact stops
    the LLM from advancing.  This is the *new* invariant — pre-M52,
    the LLM could self-judge advance regardless of execution content."""

    @pytest.mark.asyncio
    async def test_gate_deadlock_refuses_advance(
        self, integration_setup,
    ) -> None:
        setup = integration_setup
        _seed_pre_gate_checkpoint(setup, current_phase=setup["phase"])
        # Worker output: typed artifact missing required ``blockers:``.
        bad_body = (
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
        )
        (setup["phase_dir"] / "execution.md").write_text(
            _wrap_artifact(
                bad_body,
                milestone=setup["ms"],
                phase=setup["phase"],
            ),
            encoding="utf-8",
        )

        # Engine path: gate runs, writes GateDeadlock.
        _run_phase_acceptance_gate(
            project_dir=setup["tmp_path"],
            milestone=setup["ms"],
            phase=setup["phase"],
            checkpoint_path=setup["checkpoint_path"],
        )
        cp_after_gate = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp_after_gate.last_acceptance_verdict is not None
        assert cp_after_gate.last_acceptance_verdict.decision == "GateDeadlock"

        # LLM path: tries to advance anyway.  REFUSED.
        handler = _find_tool(setup["tools"], "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert result.get("reason") == "verdict_not_advance", result
        cp_unchanged = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        # phases_completed must NOT have advanced.
        assert cp_unchanged.phases_completed == 0

    @pytest.mark.asyncio
    async def test_phase_mismatch_refuses_advance(
        self, integration_setup,
    ) -> None:
        """The artifact declares a different phase than the file's
        location → ``GateDeadlock(location_forgery)`` → LLM cannot
        advance.  This closes the C3 cross-phase bypass class."""
        setup = integration_setup
        _seed_pre_gate_checkpoint(setup, current_phase=setup["phase"])
        # Artifact body claims it's from a different milestone/phase.
        (setup["phase_dir"] / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone="different-ms",
                phase=setup["phase"],
            ),
            encoding="utf-8",
        )
        _run_phase_acceptance_gate(
            project_dir=setup["tmp_path"],
            milestone=setup["ms"],
            phase=setup["phase"],
            checkpoint_path=setup["checkpoint_path"],
        )
        cp_after_gate = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp_after_gate.last_acceptance_verdict.decision == "GateDeadlock"

        handler = _find_tool(setup["tools"], "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert result.get("reason") == "verdict_not_advance", result


class TestEndToEndRework:
    """Re-emit during rework: changing execution.md produces a fresh
    sha → fresh gate evaluation.  The verdict tracks content_sha
    (F35), so a stale verdict for an old body cannot authorise an
    advance against a new body."""

    @pytest.mark.asyncio
    async def test_rework_refreshes_verdict(self, integration_setup) -> None:
        setup = integration_setup
        _seed_pre_gate_checkpoint(setup, current_phase=setup["phase"])
        # First gate run: bad body → GateDeadlock.
        bad_body = (
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
        )
        (setup["phase_dir"] / "execution.md").write_text(
            _wrap_artifact(
                bad_body,
                milestone=setup["ms"],
                phase=setup["phase"],
            ),
            encoding="utf-8",
        )
        _run_phase_acceptance_gate(
            project_dir=setup["tmp_path"],
            milestone=setup["ms"],
            phase=setup["phase"],
            checkpoint_path=setup["checkpoint_path"],
        )
        cp1 = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp1.last_acceptance_verdict.decision == "GateDeadlock"

        # Worker reworks — execution.md is replaced with a valid body.
        (setup["phase_dir"] / "execution.md").write_text(
            _wrap_artifact(
                _VALID_EXEC_SUMMARY_BODY,
                milestone=setup["ms"],
                phase=setup["phase"],
            ),
            encoding="utf-8",
        )
        # Second gate run: fresh sha → Advance overwrites the prior
        # GateDeadlock.
        _run_phase_acceptance_gate(
            project_dir=setup["tmp_path"],
            milestone=setup["ms"],
            phase=setup["phase"],
            checkpoint_path=setup["checkpoint_path"],
        )
        cp2 = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp2.last_acceptance_verdict.decision == "Advance"
        assert cp2.last_acceptance_verdict.content_sha == compute_content_sha(
            _VALID_EXEC_SUMMARY_BODY,
        )

        # LLM advance now succeeds against the fresh verdict.
        handler = _find_tool(setup["tools"], "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result


class TestLegacyPhaseMdMigration:
    """F41 migration shim: a milestone whose phase.md has no typed
    deliverable does not get gated by the engine — the LLM's first
    advance is allowed via the bootstrap path with telemetry."""

    @pytest.mark.asyncio
    async def test_legacy_phase_advance_bootstraps(
        self, integration_setup,
    ) -> None:
        setup = integration_setup
        # Replace phase.md with the legacy format (no typed deliverable).
        (setup["phase_dir"] / "phase.md").write_text(
            "## Purpose\n\nDo p1.\n\n"
            "## Hard constraints\n"
            "- **Single deliverable file:** `phases/p1/spec.md`.\n",
            encoding="utf-8",
        )
        _seed_pre_gate_checkpoint(setup, current_phase=setup["phase"])
        # Worker writes a body but it's not a typed artifact.
        (setup["phase_dir"] / "execution.md").write_text(
            "## Summary\nstatus: completed\n",
            encoding="utf-8",
        )

        # Engine: gate detects legacy phase.md and skips (no verdict
        # written).
        _run_phase_acceptance_gate(
            project_dir=setup["tmp_path"],
            milestone=setup["ms"],
            phase=setup["phase"],
            checkpoint_path=setup["checkpoint_path"],
        )
        cp_after_gate = parse_checkpoint(
            setup["checkpoint_path"].read_text(encoding="utf-8"),
        )
        assert cp_after_gate.last_acceptance_verdict is None

        # LLM advance: bootstraps via the F41 grace.
        handler = _find_tool(setup["tools"], "clou_write_checkpoint").handler
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result
