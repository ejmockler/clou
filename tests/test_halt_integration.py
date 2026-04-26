"""M49b B8 — end-to-end halt → dispose → resume integration test.

This is the dog-food test that exercises the full trajectory-halt
arc across both tiers of the agent stack:

    Coordinator tier
      1. Calls ``clou_halt_trajectory`` MCP verb
      2. Escalation file materializes under ``escalations/``
      3. Coordinator calls ``clou_write_checkpoint`` with
         ``cycle_outcome=HALTED_PENDING_REVIEW`` / ``next_step=HALTED``
         (per the B3 prompt contract + post-halt hook)

    Engine
      4. On next cycle-boundary iteration, the halt gate
         (``_apply_halt_gate``) scans escalations, finds the open
         trajectory_halt, rewrites the checkpoint with
         ``pre_halt_next_step`` stashed, returns
         ``"halted_pending_review"``
      5. Orchestrator's ``_build_milestone_guidance`` produces a
         message naming ``clou_dispose_halt`` as the supervisor path
         (not ``clou_resolve_escalation``)

    Supervisor tier
      6. Supervisor calls ``clou_dispose_halt`` with one of three
         choices; escalation and checkpoint are rewritten atomically
         (checkpoint first for crash-safe replay — M49b D1)
      7. Escalation reaches ``disposition_status=resolved``;
         checkpoint has ``cycle_outcome=ADVANCED`` and the
         choice-specific ``next_step``; ``pre_halt_next_step`` stash
         cleared

    Re-entry
      8. Next coordinator session: halt gate finds no open halt,
         falls through cleanly; ``determine_next_cycle`` routes on
         the restored ``next_step``
      9. M36 ORIENT interposition: session-start dispatch stashes
         restored value into ``pre_orient_next_step`` (NOT
         ``pre_halt_next_step``), ORIENT runs, restoration preserves
         the choice-specific ``next_step``

The tests here don't actually spawn the SDK-backed run_coordinator
loop — that's infrastructure-heavy and the existing unit tests
cover each transition.  Instead we drive the STATE TRANSITIONS
directly through the seams (``_apply_halt_gate``, ``parse_checkpoint``,
``determine_next_cycle``, supervisor handlers) and assert the
engine-runnable invariant at every hop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")


def _find_tool(tools: list, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found")


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Minimal project root with a seeded milestone."""
    ms = tmp_path / ".clou" / "milestones" / "m1"
    (ms / "escalations").mkdir(parents=True, exist_ok=True)
    (ms / "active").mkdir(parents=True, exist_ok=True)
    (ms / "phases").mkdir(exist_ok=True)
    (ms / "milestone.md").write_text("# m1\n", encoding="utf-8")
    (ms / "intents.md").write_text("# Intents\n## I1\nfoo\n", encoding="utf-8")
    (ms / "requirements.md").write_text("# Reqs\n- r1\n", encoding="utf-8")
    (ms / "status.md").write_text("# Status\n", encoding="utf-8")
    return tmp_path


# =========================================================================
# Phase-by-phase drive of the halt arc.  Each test owns one transition;
# they share the `_drive_halt_to_checkpoint` helper so the fixture
# composition stays DRY.
# =========================================================================


def _drive_coordinator_files_halt(
    project_dir: Path,
    coord_tools,
    *,
    reason: str = "anti_convergence",
    cycle_num: int = 3,
) -> Path:
    """Phase 1-2: coordinator calls clou_halt_trajectory; escalation
    file materializes.  Returns the escalation path."""
    import asyncio

    # Seed an evidence file the halt tool will reference.
    ms = project_dir / ".clou" / "milestones" / "m1"
    (ms / "assessment.md").write_text(
        "# Assessment\n## Cycle 3\n- F28 meta-finding\n",
        encoding="utf-8",
    )

    handler = _find_tool(coord_tools, "clou_halt_trajectory").handler
    result = asyncio.run(handler({
        "reason": reason,
        "rationale": (
            "Findings re-surface across cycles; file mtimes confirm "
            "zero production change; three-model convergence on "
            "F28 meta-finding."
        ),
        "evidence_paths": ["assessment.md"],
        "cycle_num": cycle_num,
    }))
    assert not result.get("is_error"), result
    esc_path = Path(result["written"])
    assert esc_path.exists()
    assert "trajectory-halt" in esc_path.name
    return esc_path


def _seed_coordinator_checkpoint(
    project_dir: Path,
    *,
    cycle: int = 3,
    next_step: str = "ASSESS",
    current_phase: str = "phase_a",
) -> Path:
    """Seed a plausible mid-cycle checkpoint.  Mirrors what the
    coordinator would have written just before filing the halt."""
    from clou.golden_context import render_checkpoint

    cp = project_dir / ".clou" / "milestones" / "m1" / "active" / "coordinator.md"
    cp.write_text(
        render_checkpoint(
            cycle=cycle,
            step="ASSESS",
            next_step=next_step,
            current_phase=current_phase,
            phases_completed=2,
            phases_total=3,
        ),
        encoding="utf-8",
    )
    return cp


@pytest.fixture
def coord_tools(project_dir: Path):
    from clou.coordinator_tools import _build_coordinator_tools

    return _build_coordinator_tools(project_dir, "m1")


@pytest.fixture
def sup_tools(project_dir: Path):
    from clou.supervisor_tools import _build_supervisor_tools

    return _build_supervisor_tools(project_dir)


# =========================================================================
# Full arc: each disposition choice drives halt → dispose → runnable.
# =========================================================================


class TestHaltArcEndToEnd:

    def test_halt_and_continue_as_is_restores_pre_halt_step(
        self, project_dir, coord_tools, sup_tools,
    ) -> None:
        """Full arc: coordinator halts → engine gate fires →
        supervisor picks continue-as-is → checkpoint returns to the
        pre-halt next_step (ASSESS), halt fully cleared, ready for
        next cycle dispatch."""
        import asyncio

        from clou.coordinator import _apply_halt_gate
        from clou.escalation import (
            find_open_engine_gated_escalation,
            parse_escalation,
        )
        from clou.recovery_checkpoint import parse_checkpoint

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        esc_dir = ms_dir / "escalations"
        cp_path = ms_dir / "active" / "coordinator.md"

        # --- Phase 1: coordinator mid-ASSESS, files halt ------------
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        _seed_coordinator_checkpoint(
            project_dir, cycle=3, next_step="ASSESS",
            current_phase="phase_a",
        )
        esc_path = _drive_coordinator_files_halt(
            project_dir, coord_tools,
        )

        # --- Phase 2: engine gate fires on next cycle boundary ------
        outcome = _apply_halt_gate(
            esc_dir, cp_path, "m1", origin="cycle_start",
        )
        assert outcome == "halted_pending_review"
        cp_after_gate = parse_checkpoint(
            cp_path.read_text(encoding="utf-8"),
        )
        assert cp_after_gate.next_step == "HALTED"
        assert cp_after_gate.cycle_outcome == "HALTED_PENDING_REVIEW"
        assert cp_after_gate.pre_halt_next_step == "ASSESS"

        # --- Phase 3: orchestrator guidance names clou_dispose_halt -
        from clou.orchestrator import _build_milestone_guidance

        guidance = _build_milestone_guidance(
            project_dir, "m1", "halted_pending_review",
        )
        assert "clou_dispose_halt" in guidance
        assert str(esc_dir) in guidance

        # --- Phase 4: supervisor disposes via continue-as-is --------
        handler = _find_tool(sup_tools, "clou_dispose_halt").handler
        result = asyncio.run(handler({
            "milestone": "m1",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "Trajectory looks fine on closer inspection.",
        }))
        assert not result.get("is_error"), result
        assert result["next_step"] == "ASSESS"  # restored from stash

        # --- Phase 5: checkpoint is runnable; halt stash cleared ----
        cp_final = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp_final.next_step == "ASSESS"
        assert cp_final.cycle_outcome == "ADVANCED"
        assert cp_final.pre_halt_next_step == ""
        # Cycle counter + phase preserved across the full arc.
        assert cp_final.cycle == 3
        assert cp_final.current_phase == "phase_a"

        # --- Phase 6: escalation is resolved ------------------------
        form = parse_escalation(esc_path.read_text(encoding="utf-8"))
        assert form.disposition_status == "resolved"
        assert "Choice: continue-as-is" in form.disposition_notes

        # --- Phase 7: halt gate returns None on next iteration ------
        assert find_open_engine_gated_escalation(esc_dir) is None
        # And the gate helper agrees (no outcome → caller continues).
        outcome_after = _apply_halt_gate(
            esc_dir, cp_path, "m1", origin="cycle_start",
        )
        assert outcome_after is None

    def test_halt_and_re_scope_routes_to_plan(
        self, project_dir, coord_tools, sup_tools,
    ) -> None:
        """Same arc with re-scope → PLAN."""
        import asyncio

        from clou.coordinator import _apply_halt_gate
        from clou.recovery_checkpoint import parse_checkpoint

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        _seed_coordinator_checkpoint(
            project_dir, cycle=3, next_step="EXECUTE",
        )
        esc_path = _drive_coordinator_files_halt(
            project_dir, coord_tools,
        )
        _apply_halt_gate(
            ms_dir / "escalations",
            ms_dir / "active" / "coordinator.md",
            "m1", origin="cycle_start",
        )

        handler = _find_tool(sup_tools, "clou_dispose_halt").handler
        result = asyncio.run(handler({
            "milestone": "m1",
            "filename": esc_path.name,
            "choice": "re-scope",
            "notes": "Narrow scope to the orient_integration branch only.",
        }))
        assert not result.get("is_error"), result

        cp = parse_checkpoint(
            (ms_dir / "active" / "coordinator.md").read_text(
                encoding="utf-8",
            ),
        )
        assert cp.next_step == "PLAN"
        assert cp.cycle_outcome == "ADVANCED"
        assert cp.pre_halt_next_step == ""

    def test_halt_and_abandon_routes_to_exit(
        self, project_dir, coord_tools, sup_tools,
    ) -> None:
        """Same arc with abandon → EXIT."""
        import asyncio

        from clou.coordinator import _apply_halt_gate
        from clou.recovery_checkpoint import parse_checkpoint

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        _seed_coordinator_checkpoint(
            project_dir, cycle=3, next_step="ASSESS",
        )
        esc_path = _drive_coordinator_files_halt(
            project_dir, coord_tools,
        )
        _apply_halt_gate(
            ms_dir / "escalations",
            ms_dir / "active" / "coordinator.md",
            "m1", origin="cycle_start",
        )

        handler = _find_tool(sup_tools, "clou_dispose_halt").handler
        result = asyncio.run(handler({
            "milestone": "m1",
            "filename": esc_path.name,
            "choice": "abandon",
            "notes": "Milestone is out of scope; exit.",
        }))
        assert not result.get("is_error"), result

        cp = parse_checkpoint(
            (ms_dir / "active" / "coordinator.md").read_text(
                encoding="utf-8",
            ),
        )
        assert cp.next_step == "EXIT"


# =========================================================================
# M36 ORIENT regression: halt disposal must not interfere with the
# session-start ORIENT interposition or its exit-restoration.
# =========================================================================


class TestM36OrientRegressionAfterHaltDisposal:

    def test_continue_as_is_survives_orient_interposition(
        self, project_dir, coord_tools, sup_tools,
    ) -> None:
        """After continue-as-is restores next_step=ASSESS, the next
        coordinator session will see that checkpoint and interpose
        ORIENT (session-start dispatch), stashing ASSESS into
        pre_orient_next_step.  ORIENT-exit restoration must then
        restore ASSESS.  Verify:
          * pre_halt_next_step stays cleared across the interposition
          * pre_orient_next_step correctly stashes the restored value
          * ORIENT-exit restoration returns to ASSESS
        """
        import asyncio

        from clou.coordinator import _apply_halt_gate
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        cp_path = ms_dir / "active" / "coordinator.md"

        # --- Halt + dispose via continue-as-is ----------------------
        _seed_coordinator_checkpoint(
            project_dir, cycle=3, next_step="ASSESS",
        )
        esc_path = _drive_coordinator_files_halt(
            project_dir, coord_tools,
        )
        _apply_halt_gate(
            ms_dir / "escalations", cp_path, "m1",
            origin="cycle_start",
        )
        handler = _find_tool(sup_tools, "clou_dispose_halt").handler
        asyncio.run(handler({
            "milestone": "m1",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "resume",
        }))

        # --- Simulate session-start ORIENT interposition -----------
        # This mirrors coordinator.py:1341-1383 for a runnable
        # next_step (non-HALTED, non-ORIENT): stash next_step into
        # pre_orient_next_step, rewrite next_step=ORIENT.
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "ASSESS"
        assert cp.pre_halt_next_step == ""  # halt stash cleared
        rewritten = render_checkpoint(
            cycle=cp.cycle,
            step=cp.step,
            next_step="ORIENT",
            current_phase=cp.current_phase,
            phases_completed=cp.phases_completed,
            phases_total=cp.phases_total,
            validation_retries=cp.validation_retries,
            readiness_retries=cp.readiness_retries,
            crash_retries=cp.crash_retries,
            staleness_count=cp.staleness_count,
            cycle_outcome=cp.cycle_outcome,
            valid_findings=cp.valid_findings,
            consecutive_zero_valid=cp.consecutive_zero_valid,
            pre_orient_next_step=cp.next_step,  # "ASSESS"
            # M49b B6: ORIENT interposition preserves halt stash
            # (which is correctly empty after disposal).
            pre_halt_next_step=cp.pre_halt_next_step,
        )
        cp_path.write_text(rewritten, encoding="utf-8")

        cp_during_orient = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp_during_orient.next_step == "ORIENT"
        assert cp_during_orient.pre_orient_next_step == "ASSESS"
        assert cp_during_orient.pre_halt_next_step == ""

        # --- Simulate ORIENT-exit restoration -----------------------
        # Mirrors coordinator.py:1285-1331.
        cp_for_restore = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        restored_step = cp_for_restore.pre_orient_next_step
        restored_body = render_checkpoint(
            cycle=cp_for_restore.cycle,
            step=cp_for_restore.step,
            next_step=restored_step,
            current_phase=cp_for_restore.current_phase,
            phases_completed=cp_for_restore.phases_completed,
            phases_total=cp_for_restore.phases_total,
            validation_retries=cp_for_restore.validation_retries,
            readiness_retries=cp_for_restore.readiness_retries,
            crash_retries=cp_for_restore.crash_retries,
            staleness_count=cp_for_restore.staleness_count,
            cycle_outcome=cp_for_restore.cycle_outcome,
            valid_findings=cp_for_restore.valid_findings,
            consecutive_zero_valid=cp_for_restore.consecutive_zero_valid,
            # Clear pre_orient stash; preserve halt stash (still empty).
            pre_orient_next_step="",
            pre_halt_next_step=cp_for_restore.pre_halt_next_step,
        )
        cp_path.write_text(restored_body, encoding="utf-8")

        cp_final = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp_final.next_step == "ASSESS"  # restored
        assert cp_final.pre_orient_next_step == ""
        assert cp_final.pre_halt_next_step == ""

    def test_session_start_skips_halted_next_step(
        self, project_dir,
    ) -> None:
        """M49b D3 defense: if the session somehow starts with
        next_step=HALTED (contract violation), the ORIENT
        interposition MUST NOT stash HALTED (would loop via
        ORIENT-exit restoration).  This test simulates the check
        directly."""
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        cp_path = ms_dir / "active" / "coordinator.md"
        cp_path.write_text(
            render_checkpoint(
                cycle=3, step="ASSESS", next_step="HALTED",
                current_phase="phase_a",
                cycle_outcome="HALTED_PENDING_REVIEW",
                pre_halt_next_step="ASSESS",
            ),
            encoding="utf-8",
        )

        # The render guard must refuse stashing HALTED into pre_orient.
        with pytest.raises(
            ValueError, match="pre_orient_next_step.*HALTED",
        ):
            render_checkpoint(
                cycle=3, step="ASSESS", next_step="ORIENT",
                current_phase="phase_a",
                pre_orient_next_step="HALTED",
            )

        # Parse-side defensive drop: a corrupt file on disk must
        # not propagate HALTED as the restoration target.
        corrupted = (
            "cycle: 1\nstep: PLAN\nnext_step: PLAN\ncurrent_phase: \n"
            "phases_completed: 0\nphases_total: 0\n"
            "pre_orient_next_step: HALTED\n"
        )
        assert parse_checkpoint(corrupted).pre_orient_next_step == ""


# =========================================================================
# Negative-path integration: the supervisor who misroutes through
# clou_resolve_escalation (e.g. stale prompt cache) gets a clean error
# pointing to clou_dispose_halt — not a silent wedge.
# =========================================================================


class TestMisroutedSupervisorGetsHelpfulError:

    def test_clou_resolve_escalation_refuses_and_names_right_tool(
        self, project_dir, coord_tools, sup_tools,
    ) -> None:
        """A supervisor that calls clou_resolve_escalation on an
        engine-gated escalation (the stale prompt path) gets an
        explicit error naming clou_dispose_halt.  Without D2, this
        call would silently resolve the escalation but leave the
        checkpoint wedged in HALTED state."""
        import asyncio

        from clou.coordinator import _apply_halt_gate
        from clou.escalation import parse_escalation

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        _seed_coordinator_checkpoint(
            project_dir, cycle=3, next_step="ASSESS",
        )
        esc_path = _drive_coordinator_files_halt(
            project_dir, coord_tools,
        )
        _apply_halt_gate(
            ms_dir / "escalations",
            ms_dir / "active" / "coordinator.md",
            "m1", origin="cycle_start",
        )

        handler = _find_tool(
            sup_tools, "clou_resolve_escalation",
        ).handler
        result = asyncio.run(handler({
            "milestone": "m1",
            "filename": esc_path.name,
            "status": "resolved",
            "notes": "x",
        }))
        assert result.get("is_error"), result
        assert "clou_dispose_halt" in result["content"][0]["text"]

        # And the escalation file is UNCHANGED — the supervisor's
        # erroneous call didn't mutate state.
        form = parse_escalation(esc_path.read_text(encoding="utf-8"))
        assert form.disposition_status == "open"


# =========================================================================
# COMPLETE-path gate: a coordinator that races past a mid-cycle halt
# filing and writes COMPLETE gets caught by the pre-COMPLETE gate (C2).
# =========================================================================


class TestPreCompleteGateCatchesRacedHalt:

    def test_pre_complete_gate_fires_on_open_halt(
        self, project_dir, coord_tools,
    ) -> None:
        """Simulate the race window: coordinator files a halt
        (escalation materializes), but the cycle keeps running and
        writes next_step=COMPLETE before the next loop iteration
        would fire the cycle-top gate.  The pre-COMPLETE gate (C2)
        must catch this and halt the milestone."""
        from clou.coordinator import _apply_halt_gate
        from clou.recovery_checkpoint import parse_checkpoint

        ms_dir = project_dir / ".clou" / "milestones" / "m1"
        cp_path = ms_dir / "active" / "coordinator.md"

        # Coordinator files halt (escalation created).
        esc_path = _drive_coordinator_files_halt(
            project_dir, coord_tools,
        )

        # Race: cycle continued and wrote COMPLETE anyway.
        from clou.golden_context import render_checkpoint

        cp_path.write_text(
            render_checkpoint(
                cycle=4, step="ASSESS", next_step="COMPLETE",
                current_phase="phase_a",
                phases_completed=3, phases_total=3,
            ),
            encoding="utf-8",
        )

        # Pre-COMPLETE gate (same helper, origin=pre_complete).
        outcome = _apply_halt_gate(
            ms_dir / "escalations", cp_path, "m1",
            origin="pre_complete",
        )
        assert outcome == "halted_pending_review"

        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "HALTED"
        assert cp.cycle_outcome == "HALTED_PENDING_REVIEW"
        # The COMPLETE transition is blocked; halt takes priority.
        assert esc_path.exists()
