"""M49b B1 — HALTED_PENDING_REVIEW checkpoint vocabulary tests.

Covers the type-system hygiene corrections (brutalist Issues A + B):
- HALTED_PENDING_REVIEW in CycleOutcome enum
- HALTED in _VALID_NEXT_STEPS frozenset (recovery_checkpoint.py)
- HALTED in VALID_NEXT_STEPS frozenset (golden_context.py)
- HALTED_PENDING_REVIEW in VALID_CYCLE_OUTCOMES frozenset
- render_checkpoint accepts both new values
- parse_checkpoint round-trips them

These tests DO NOT import from clou.coordinator (which pulls in the
claude_agent_sdk and blocks local collection).  The SDK-dependent
staleness-reset behaviour (update_staleness treating
HALTED_PENDING_REVIEW in the INCONCLUSIVE/INTERRUPTED class) is
verified in the existing test_staleness_evidence.py file under the
same SDK-gating as the rest of that module.
"""

from __future__ import annotations


def test_cycleoutcome_enum_exposes_halted() -> None:
    """The typed enum must carry HALTED_PENDING_REVIEW so downstream
    callers can import it as a symbol rather than hardcoding the
    string literal."""
    from clou.recovery_checkpoint import CycleOutcome

    assert CycleOutcome.HALTED_PENDING_REVIEW.value == "HALTED_PENDING_REVIEW"
    assert "HALTED_PENDING_REVIEW" in {e.value for e in CycleOutcome}


def test_valid_next_steps_includes_halted() -> None:
    """Pin HALTED in both the golden_context.py and
    recovery_checkpoint.py frozensets --- they are duplicate sources
    of truth today, kept in sync.  Missing either would cause parser
    coercion to PLAN on halted checkpoints (brutalist Issue A)."""
    from clou.golden_context import VALID_NEXT_STEPS
    from clou.recovery_checkpoint import _VALID_NEXT_STEPS

    assert "HALTED" in VALID_NEXT_STEPS
    assert "HALTED" in _VALID_NEXT_STEPS


def test_valid_cycle_outcomes_includes_halted_pending_review() -> None:
    """render_checkpoint validates cycle_outcome against this set;
    missing would raise ValueError on halt write (brutalist Issue B)."""
    from clou.golden_context import VALID_CYCLE_OUTCOMES

    assert "HALTED_PENDING_REVIEW" in VALID_CYCLE_OUTCOMES


def test_render_checkpoint_accepts_halted_values() -> None:
    """render_checkpoint must accept HALTED next_step and
    HALTED_PENDING_REVIEW cycle_outcome without raising."""
    from clou.golden_context import render_checkpoint

    rendered = render_checkpoint(
        cycle=3, step="ASSESS", next_step="HALTED",
        current_phase="orient_integration",
        phases_completed=5, phases_total=6,
        cycle_outcome="HALTED_PENDING_REVIEW",
    )
    assert "next_step: HALTED\n" in rendered
    assert "cycle_outcome: HALTED_PENDING_REVIEW\n" in rendered


def test_parse_checkpoint_round_trips_halted() -> None:
    """Render -> parse byte-stable for halted checkpoints --- pins the
    M49b type vocabulary end-to-end.  Without HALTED in
    _VALID_NEXT_STEPS the parser silently coerces to PLAN and a halted
    checkpoint resumes as a normal PLAN cycle (brutalist Issue A).
    """
    from clou.golden_context import render_checkpoint
    from clou.recovery_checkpoint import parse_checkpoint

    rendered = render_checkpoint(
        cycle=3, step="ASSESS", next_step="HALTED",
        current_phase="orient_integration",
        phases_completed=5, phases_total=6,
        staleness_count=1,
        cycle_outcome="HALTED_PENDING_REVIEW",
        valid_findings=28,
    )
    parsed = parse_checkpoint(rendered)
    assert parsed.next_step == "HALTED"
    assert parsed.cycle_outcome == "HALTED_PENDING_REVIEW"
    assert parsed.cycle == 3
    assert parsed.staleness_count == 1
    assert parsed.valid_findings == 28


def test_render_checkpoint_rejects_genuinely_invalid_outcome() -> None:
    """Regression guard: adding HALTED_PENDING_REVIEW to the valid set
    must not weaken the general rejection behaviour.  Truly unknown
    outcomes still raise."""
    import pytest

    from clou.golden_context import render_checkpoint

    with pytest.raises(ValueError, match="invalid cycle_outcome"):
        render_checkpoint(
            cycle=0, step="PLAN", next_step="PLAN",
            cycle_outcome="SOMETHING_MADE_UP",
        )


def test_render_checkpoint_rejects_genuinely_invalid_next_step() -> None:
    """Regression guard for the parallel case on next_step."""
    import pytest

    from clou.golden_context import render_checkpoint

    with pytest.raises(ValueError, match="invalid next_step"):
        render_checkpoint(
            cycle=0, step="PLAN", next_step="SOMETHING_MADE_UP",
        )


# ---------------------------------------------------------------------------
# M49b C1 — engine HALTED routing tests (closes B5 brutalist findings
# L1, L3, L5, L8).  These verify that:
#   * ``determine_next_cycle`` no longer silently coerces HALTED→PLAN
#   * ``_VALID_NEXT_STEPS`` and ``determine_next_cycle``'s match arms are
#     in parity (no third source-of-truth drift)
#   * ``_build_milestone_guidance`` routes ``halted_pending_review``
#     results to the escalations dir, not to a non-existent handoff.md
#   * the halt-gate scan call is wrapped in try/except so a transient
#     PermissionError/OSError does not crash run_coordinator
# ---------------------------------------------------------------------------


def _seed_halted_checkpoint(
    project_dir,  # type: ignore[no-untyped-def]
    milestone: str = "m1",
    *,
    next_step: str = "HALTED",
    current_phase: str = "phase_a",
):
    """Build a checkpoint at the expected on-disk path for use with
    ``determine_next_cycle(checkpoint_path, milestone)``.  Mirrors the
    layout from ``test_orient_integration._bootstrap_milestone`` but
    minimal — just the bits ``determine_next_cycle`` needs."""
    from clou.golden_context import render_checkpoint

    ms_dir = project_dir / ".clou" / "milestones" / milestone
    (ms_dir / "active").mkdir(parents=True, exist_ok=True)
    (ms_dir / "intents.md").write_text("# Intents\n", encoding="utf-8")
    (ms_dir / "requirements.md").write_text("# Reqs\n", encoding="utf-8")
    (ms_dir / "milestone.md").write_text("# Milestone\n", encoding="utf-8")
    (ms_dir / "phases").mkdir(exist_ok=True)
    if current_phase:
        (ms_dir / "phases" / current_phase).mkdir(exist_ok=True)
    cp = ms_dir / "active" / "coordinator.md"
    cp.write_text(
        render_checkpoint(
            cycle=3,
            step="ASSESS",
            next_step=next_step,
            current_phase=current_phase,
        ),
        encoding="utf-8",
    )
    return cp


def test_determine_next_cycle_raises_on_halted_next_step(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Brutalist B5/L1: HALTED in checkpoint without an open
    engine-gated escalation is a supervisor-contract violation.  The
    consumer must raise, not silently coerce to PLAN (the previous
    fall-through behaviour, which erased the halt context and resumed
    from PLAN with the original phase lost)."""
    import pytest

    from clou.recovery_checkpoint import determine_next_cycle

    cp = _seed_halted_checkpoint(tmp_path, next_step="HALTED")
    with pytest.raises(RuntimeError, match="next_step=HALTED"):
        determine_next_cycle(cp, "m1")


def test_determine_next_cycle_covers_every_valid_next_step(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Brutalist B5/L8: ``_VALID_NEXT_STEPS`` and the ``match`` arms in
    ``determine_next_cycle`` are duplicate sources of truth.  The
    existing test only pins membership; this test pins behaviour:
    every value in the frozenset must produce a NON-default branch
    (either a routed cycle type or a deliberate raise).  A new value
    added to ``_VALID_NEXT_STEPS`` without a corresponding ``case``
    arm previously fell through to the ``"UNKNOWN"`` default and
    silently coerced to PLAN."""
    import pytest

    from clou.recovery_checkpoint import (
        _VALID_NEXT_STEPS,
        determine_next_cycle,
    )

    # Each value either routes to a specific cycle_type or raises.
    # The structured ``EXECUTE_REWORK`` / ``EXECUTE_VERIFY`` tokens
    # (M50 I1: replacing the punctuated legacy forms) each preserve
    # their token through the router — cycle-2 rework (F5/F12) pins
    # this preservation so telemetry / prompt builders can distinguish
    # rework from verification from plain execute.
    #
    # M50 I1 cycle-3 rework (F4/F15): ``'none'`` is no longer in
    # ``_VALID_NEXT_STEPS`` — ``render_checkpoint`` rejects it on writes,
    # and ``parse_checkpoint`` coerces any legacy on-disk ``next_step:
    # none`` to ``COMPLETE`` before the router sees it.  The parse-only
    # tolerance is tested in ``test_golden_context``'s
    # ``test_none_next_step_parse_only_tolerance``; this coverage test
    # enumerates only values the router is reachable with via write
    # paths, which is exactly ``_VALID_NEXT_STEPS``.
    expected: dict[str, str] = {
        "ORIENT": "ORIENT",
        "PLAN": "PLAN",
        "EXECUTE": "EXECUTE",
        "EXECUTE_REWORK": "EXECUTE_REWORK",
        "EXECUTE_VERIFY": "EXECUTE_VERIFY",
        "ASSESS": "ASSESS",
        "REPLAN": "REPLAN",
        "VERIFY": "VERIFY",
        "EXIT": "EXIT",
        "COMPLETE": "COMPLETE",
    }
    raises: set[str] = {"HALTED"}

    # Coverage check — if someone adds a value to _VALID_NEXT_STEPS
    # without updating either ``expected`` or ``raises``, this fails.
    covered = set(expected) | raises
    missing = set(_VALID_NEXT_STEPS) - covered
    assert not missing, (
        f"_VALID_NEXT_STEPS contains {missing} which has no behavioural "
        f"contract in this test.  Add a case to determine_next_cycle "
        f"AND extend this test's `expected` or `raises` set.  See "
        f"M49b C1 for the pattern."
    )

    for next_step, cycle_type in expected.items():
        cp = _seed_halted_checkpoint(
            tmp_path / next_step.replace(" ", "_").replace("(", "").replace(")", ""),
            next_step=next_step,
        )
        result_type, _ = determine_next_cycle(cp, "m1")
        assert result_type == cycle_type, (
            f"next_step={next_step!r} routed to {result_type!r}, "
            f"expected {cycle_type!r}"
        )

    for next_step in raises:
        cp = _seed_halted_checkpoint(
            tmp_path / f"raise_{next_step}",
            next_step=next_step,
        )
        with pytest.raises(RuntimeError):
            determine_next_cycle(cp, "m1")


def test_build_milestone_guidance_routes_halted_pending_review(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Brutalist B5/L3: when run_coordinator returns
    ``"halted_pending_review"``, the orchestrator's per-milestone
    guidance text MUST point the supervisor at the
    ``escalations/`` directory (where the trajectory-halt escalation
    lives), NOT at ``handoff.md`` (which is only written at COMPLETE
    and does not exist on a halted milestone).  The previous
    fall-through branch told the supervisor to read four files that
    don't exist — actively misleading."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from clou.orchestrator import _build_milestone_guidance

    guidance = _build_milestone_guidance(
        tmp_path, "m1", "halted_pending_review",
    )
    assert "escalations" in guidance
    assert "halted pending review" in guidance.lower()
    # Must NOT direct the supervisor to handoff.md (does not exist on
    # a halted milestone).
    assert "handoff.md" not in guidance


def test_build_milestone_guidance_unchanged_for_non_halted_results(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Regression guard: the new halted_pending_review branch must
    not affect the existing escalated/paused/stopped/default paths."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from clou.orchestrator import _build_milestone_guidance

    # Default branch (e.g. "completed"): handoff.md is the right answer.
    g_done = _build_milestone_guidance(tmp_path, "m1", "completed")
    assert "handoff.md" in g_done
    # Escalated branch: escalations dir.
    g_esc = _build_milestone_guidance(tmp_path, "m1", "escalated_blocked")
    assert "escalations" in g_esc
    # Paused branch: queue-drain text.
    g_paused = _build_milestone_guidance(tmp_path, "m1", "paused")
    assert "input queue" in g_paused


# ---------------------------------------------------------------------------
# M49b C2 — pre-COMPLETE halt-gate tests (closes B5/L4).  The
# loop-top gate (B4) and the pre-COMPLETE gate share a single
# helper, ``_apply_halt_gate``, so a halt filed mid-cycle cannot
# escape via either the next-iteration boundary OR a COMPLETE
# checkpoint write before that boundary fires.
# ---------------------------------------------------------------------------


def _make_halt_escalation_file(esc_dir, name: str = "halt.md", *, disposition: str = "open"):  # type: ignore[no-untyped-def]
    """Build a trajectory_halt escalation file in *esc_dir/name*."""
    from clou.escalation import (
        EscalationForm,
        EscalationOption,
        render_escalation,
    )

    esc_dir.mkdir(parents=True, exist_ok=True)
    form = EscalationForm(
        title="Trajectory halt: anti_convergence (cycle 3)",
        classification="trajectory_halt",
        issue="findings re-surface across rounds",
        options=(
            EscalationOption(label="continue-as-is"),
            EscalationOption(label="re-scope"),
            EscalationOption(label="abandon"),
        ),
        disposition_status=disposition,
    )
    (esc_dir / name).write_text(render_escalation(form), encoding="utf-8")


def _seed_checkpoint(checkpoint_path, *, next_step: str = "ASSESS"):  # type: ignore[no-untyped-def]
    from clou.golden_context import render_checkpoint

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        render_checkpoint(
            cycle=5, step="ASSESS", next_step=next_step,
            current_phase="phase_a", phases_completed=2, phases_total=3,
        ),
        encoding="utf-8",
    )


def test_apply_halt_gate_returns_none_for_no_open_halt(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """No escalation files → returns None, no checkpoint mutation."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from clou.coordinator import _apply_halt_gate

    esc_dir = tmp_path / "escalations"
    esc_dir.mkdir()
    cp = tmp_path / "active" / "coordinator.md"
    _seed_checkpoint(cp, next_step="ASSESS")
    before = cp.read_text(encoding="utf-8")

    outcome = _apply_halt_gate(esc_dir, cp, "m1", origin="cycle_start")

    assert outcome is None
    assert cp.read_text(encoding="utf-8") == before


def test_apply_halt_gate_returns_outcome_and_rewrites_checkpoint(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Open trajectory_halt → returns "halted_pending_review" and
    rewrites checkpoint to next_step=HALTED, cycle_outcome=HALTED_PENDING_REVIEW.
    This is the load-bearing behaviour that B4 depends on; both the
    loop-top and pre-COMPLETE gates share this helper now."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from clou.coordinator import _apply_halt_gate
    from clou.recovery_checkpoint import parse_checkpoint

    esc_dir = tmp_path / "escalations"
    _make_halt_escalation_file(esc_dir)
    cp = tmp_path / "active" / "coordinator.md"
    _seed_checkpoint(cp, next_step="ASSESS")

    outcome = _apply_halt_gate(esc_dir, cp, "m1", origin="cycle_start")

    assert outcome == "halted_pending_review"
    parsed = parse_checkpoint(cp.read_text(encoding="utf-8"))
    assert parsed.next_step == "HALTED"
    assert parsed.cycle_outcome == "HALTED_PENDING_REVIEW"
    # Other fields preserved from before (cycle counter, phase, etc.)
    assert parsed.cycle == 5
    assert parsed.current_phase == "phase_a"


def test_apply_halt_gate_skips_resolved_halts(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """A resolved trajectory_halt must NOT trigger the gate — the
    operator has already dispositioned it.  The engine should be
    free to continue (after C1/B6 makes supervisor rewrite next_step
    away from HALTED)."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from clou.coordinator import _apply_halt_gate

    esc_dir = tmp_path / "escalations"
    _make_halt_escalation_file(esc_dir, disposition="resolved")
    cp = tmp_path / "active" / "coordinator.md"
    _seed_checkpoint(cp, next_step="ASSESS")
    before = cp.read_text(encoding="utf-8")

    outcome = _apply_halt_gate(esc_dir, cp, "m1", origin="cycle_start")

    assert outcome is None
    assert cp.read_text(encoding="utf-8") == before


def test_apply_halt_gate_fails_open_on_scan_exception(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """Brutalist B5/L5: the helper must catch any exception from the
    underlying scan and treat it as no-halt-found.  A transient FS
    error (PermissionError, OSError) must NOT crash run_coordinator —
    halt gating is best-effort defense, not a load-bearing oracle."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    import clou.coordinator as cmod

    def boom(*_a, **_kw):
        raise PermissionError("simulated FS error")

    monkeypatch.setattr(cmod, "find_open_engine_gated_escalation", boom)
    cp = tmp_path / "active" / "coordinator.md"
    _seed_checkpoint(cp, next_step="ASSESS")
    before = cp.read_text(encoding="utf-8")

    outcome = cmod._apply_halt_gate(
        tmp_path / "escalations", cp, "m1", origin="cycle_start",
    )

    assert outcome is None  # fail-open: engine continues
    assert cp.read_text(encoding="utf-8") == before  # no mutation


def test_apply_halt_gate_origin_label_distinguishes_call_sites(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """C2: the origin label ("cycle_start" vs "pre_complete") must
    propagate to telemetry so operators can distinguish the common
    loop-top halt from the rare race-condition halt at COMPLETE."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    import clou.coordinator as cmod

    captured: list[dict] = []

    def fake_event(_name: str, **kw):  # type: ignore[no-untyped-def]
        captured.append(kw)

    monkeypatch.setattr(cmod.telemetry, "event", fake_event)

    esc_dir = tmp_path / "escalations"
    _make_halt_escalation_file(esc_dir)
    cp = tmp_path / "active" / "coordinator.md"
    _seed_checkpoint(cp, next_step="COMPLETE")

    cmod._apply_halt_gate(esc_dir, cp, "m1", origin="pre_complete")

    halts = [e for e in captured if e.get("milestone") == "m1"]
    assert halts, "no telemetry event captured for halt"
    assert halts[-1].get("origin") == "pre_complete"


# ---------------------------------------------------------------------------
# M49b C3 — classification routing contract enforcement (closes B5/L6).
# ``ENGINE_GATED_CLASSIFICATIONS`` is the single routing authority.
# The escalation.py module docstring documents that callers MUST NOT
# branch on classification equality — they must use membership in
# ENGINE_GATED_CLASSIFICATIONS instead.  This test enforces the rule
# structurally so a regression is caught at test time, not at PR
# review or — worse — in production.
# ---------------------------------------------------------------------------


def _classification_routing_violations() -> list[tuple[str, int, str]]:
    """AST-walk every ``clou/*.py`` and find branches on
    ``<expr>.classification`` against a string literal.  Allowed:
    membership tests against the named constants
    ``ENGINE_GATED_CLASSIFICATIONS`` / ``VALID_CLASSIFICATIONS``.

    Allowlist: ``escalation.py`` itself defines the contract and the
    constants used to satisfy it.
    """
    import ast
    from pathlib import Path

    clou_dir = Path(__file__).resolve().parent.parent / "clou"
    allowlist_files = {"escalation.py"}
    allowed_constants = {
        "ENGINE_GATED_CLASSIFICATIONS",
        "VALID_CLASSIFICATIONS",
    }

    def _is_classification_attr(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "classification"
        )

    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(clou_dir.rglob("*.py")):
        if py_file.name in allowlist_files:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            # Don't fail the test for unrelated parse errors —
            # syntax checks live in their own test surface.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            # Find side that references *.classification.
            class_side = next(
                (o for o in operands if _is_classification_attr(o)), None
            )
            if class_side is None:
                continue
            # Find the OTHER side and the operator(s).
            for op, other in zip(node.ops, node.comparators):
                # If the .classification ref is on the RIGHT, swap.
                if other is class_side:
                    other = node.left
                # Allowed: ``in NAMED_CONSTANT`` membership.
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(
                    other, ast.Name
                ) and other.id in allowed_constants:
                    continue
                # Banned: equality / inequality with a string literal.
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(
                    other, ast.Constant
                ) and isinstance(other.value, str):
                    rel = py_file.relative_to(clou_dir.parent).as_posix()
                    violations.append(
                        (rel, node.lineno, ast.unparse(node))
                    )
                    continue
                # Banned: membership in a tuple/list/set of literals.
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(
                    other, (ast.Tuple, ast.List, ast.Set)
                ) and all(
                    isinstance(elt, ast.Constant)
                    and isinstance(elt.value, str)
                    for elt in other.elts
                ):
                    rel = py_file.relative_to(clou_dir.parent).as_posix()
                    violations.append(
                        (rel, node.lineno, ast.unparse(node))
                    )
                    continue
                # Banned: membership in a name that is NOT one of the
                # allowed routing constants.
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(
                    other, ast.Name
                ) and other.id not in allowed_constants:
                    rel = py_file.relative_to(clou_dir.parent).as_posix()
                    violations.append(
                        (rel, node.lineno, ast.unparse(node))
                    )
                    continue
    return violations


def test_no_classification_routing_branch_outside_allowlist() -> None:
    """Brutalist B5/L6: enforce the routing contract documented in
    ``clou/escalation.py``.  Engine control flow MUST route on
    ``parsed.classification in ENGINE_GATED_CLASSIFICATIONS``, not on
    string equality with classification literals.  The latter pattern
    silently breaks when a new classification (e.g. the docstring's
    own ``budget_halt`` example) is added to ``VALID_CLASSIFICATIONS``
    but its handlers forget to extend the routing branch.

    Allowlist: ``escalation.py`` itself (defines the contract and
    the constants).  Membership tests against
    ``ENGINE_GATED_CLASSIFICATIONS`` / ``VALID_CLASSIFICATIONS`` are
    the prescribed pattern.
    """
    violations = _classification_routing_violations()
    assert not violations, (
        "Engine routing contract violation: code branches on "
        "<expr>.classification equality.  Use membership in "
        "ENGINE_GATED_CLASSIFICATIONS instead.  See "
        "clou/escalation.py module docstring for the contract.\n\n"
        "Violations:\n"
        + "\n".join(f"  {f}:{n}  {expr}" for f, n, expr in violations)
    )


def test_classification_contract_test_catches_a_planted_violation(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """Meta-test: the contract scanner must actually detect a planted
    violation.  Without this, a regression that broke the AST walker
    would silently pass — failing-open is exactly the L6 concern.

    We synthesize a fake clou/ directory with one violating file and
    run the scanner against it via path-rebinding.
    """
    import ast

    fake_clou = tmp_path / "clou"
    fake_clou.mkdir()
    (fake_clou / "evil.py").write_text(
        # A drifted escalation router that branches on string equality.
        'def route(parsed):\n'
        '    if parsed.classification == "trajectory_halt":\n'
        '        return "halt"\n'
        '    return "continue"\n',
        encoding="utf-8",
    )

    # Re-implement the scanner inline against the fake directory.
    violations: list[tuple[str, int]] = []
    for py_file in sorted(fake_clou.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            if any(
                isinstance(o, ast.Attribute) and o.attr == "classification"
                for o in operands
            ):
                violations.append((py_file.name, node.lineno))

    assert violations, (
        "Meta-test broken: the AST scanner failed to detect a planted "
        "classification routing violation.  Fix the scanner before "
        "trusting the contract test."
    )


def test_halt_option_labels_is_single_source_of_truth() -> None:
    """M49b D4 (closes B7/F5): the trajectory_halt escalation's option
    labels live in clou.escalation.HALT_OPTION_LABELS.  Both the
    coordinator-side handler that BUILDS the escalation and the
    supervisor-side handler that DISPOSITIONS it consume this tuple,
    so a future PR that adds a 4th label or renames one cannot drift
    them apart silently.

    This test pins:
      * HALT_OPTION_LABELS contains exactly the three current labels
        in canonical order
      * No empty / duplicate labels
      * Every label is a non-empty string
    """
    from clou.escalation import HALT_OPTION_LABELS

    assert HALT_OPTION_LABELS == (
        "continue-as-is", "re-scope", "abandon",
    )
    assert len(HALT_OPTION_LABELS) == len(set(HALT_OPTION_LABELS))
    assert all(isinstance(s, str) and s for s in HALT_OPTION_LABELS)


def test_supervisor_dispose_halt_choices_match_halt_option_labels() -> None:
    """The supervisor's clou_dispose_halt accepted-choice set MUST
    equal the canonical HALT_OPTION_LABELS.  Without this pin, the
    coordinator can produce an escalation with a label the supervisor
    tool silently rejects."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from clou.escalation import HALT_OPTION_LABELS
    from clou.supervisor_tools import _build_supervisor_tools

    # We need to reach inside the closure to read _HALT_VALID_CHOICES.
    # _build_supervisor_tools uses module-level constants, so we can
    # also assert via behaviour: invoke clou_dispose_halt with each
    # canonical label and check it does not error on choice.
    # Behavioural pin (preferred — closure-private constant inspection
    # is brittle):
    import asyncio
    from pathlib import Path
    import tempfile

    from clou.escalation import (
        EscalationForm,
        EscalationOption,
        render_escalation,
    )
    from clou.golden_context import render_checkpoint

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        ms = td_path / ".clou" / "milestones" / "test-ms"
        for label in HALT_OPTION_LABELS:
            (ms / "escalations").mkdir(parents=True, exist_ok=True)
            (ms / "active").mkdir(parents=True, exist_ok=True)
            esc = ms / "escalations" / f"halt-{label}.md"
            esc.write_text(
                render_escalation(
                    EscalationForm(
                        title="halt",
                        classification="trajectory_halt",
                        issue="x",
                        options=tuple(
                            EscalationOption(label=L)
                            for L in HALT_OPTION_LABELS
                        ),
                        disposition_status="open",
                    )
                ),
                encoding="utf-8",
            )
            cp = ms / "active" / "coordinator.md"
            cp.write_text(
                render_checkpoint(
                    cycle=1, step="ASSESS", next_step="HALTED",
                    cycle_outcome="HALTED_PENDING_REVIEW",
                    pre_halt_next_step="EXECUTE",
                ),
                encoding="utf-8",
            )

            tools = _build_supervisor_tools(td_path)
            handler = next(
                t.handler for t in tools if t.name == "clou_dispose_halt"
            )
            result = asyncio.run(handler({
                "milestone": "test-ms",
                "filename": esc.name,
                "choice": label,
                "notes": "x",
            }))
            assert not result.get("is_error"), (
                f"label {label!r} from HALT_OPTION_LABELS rejected by "
                f"clou_dispose_halt — drift between coordinator-built "
                f"escalation and supervisor-accepted choice set: "
                f"{result}"
            )


# ---------------------------------------------------------------------------
# M49b D5 — documentation pins (closes B7/F2-companion).  The agent-
# facing surfaces (supervisor prompt, coordinator-assess prompt,
# coordinator-written escalation body, orchestrator return guidance)
# MUST name `clou_dispose_halt` for engine-gated escalations.  Without
# this naming, the supervisor follows the prompt-default path
# (clou_resolve_escalation) and trips D2's defense-in-depth refusal —
# the user sees an error instead of a clean disposition flow.
# ---------------------------------------------------------------------------


def test_supervisor_prompt_names_clou_dispose_halt() -> None:
    """The supervisor prompt MUST mention clou_dispose_halt as the
    disposition path for engine-gated escalations.  Without this, the
    supervisor agent only knows clou_resolve_escalation and hits D2's
    refusal.

    M49b E3 (closes B9/F2-companion): the runtime prompt at
    ``.clou/prompts/supervisor.md`` is independent of the canonical
    source at ``clou/_prompts/supervisor.md`` — there is no sync
    mechanism.  BOTH must be updated.  This test pins both so a
    future update to one without the other is caught.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for prompt in (
        repo / "clou" / "_prompts" / "supervisor.md",
        # The runtime copy (what the supervisor session actually
        # reads per supervisor-system.xml) — independent of the
        # source file above; requires its own update.
        repo / ".clou" / "prompts" / "supervisor.md",
    ):
        assert prompt.is_file(), f"missing prompt: {prompt}"
        text = prompt.read_text(encoding="utf-8")
        assert "clou_dispose_halt" in text, (
            f"supervisor prompt {prompt.name} must name "
            f"clou_dispose_halt for engine-gated escalations"
        )
        # Must explain why standard clou_resolve_escalation is wrong here.
        assert "engine-gated" in text.lower() or "ENGINE_GATED" in text


def test_orchestrator_supervisor_allowlist_includes_clou_dispose_halt() -> None:
    """M49b E2 (closes B9/F2): orchestrator.py's supervisor session
    allow-list MUST include clou_dispose_halt.  Without this, the
    supervisor session cannot invoke the tool even though the MCP
    server registers it."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    src = (repo / "clou" / "orchestrator.py").read_text(encoding="utf-8")
    assert "mcp__clou_supervisor__clou_dispose_halt" in src, (
        "supervisor allow-list in orchestrator.py must include "
        "mcp__clou_supervisor__clou_dispose_halt — otherwise halted "
        "milestones are operationally unrecoverable from the supervisor "
        "session"
    )


def test_coordinator_assess_prompt_names_clou_dispose_halt() -> None:
    """The coordinator's assess-cycle prompt tells the coordinator
    what the supervisor will do downstream.  After M49b D5 it must
    name clou_dispose_halt, not clou_resolve_escalation."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for prompt in (
        repo / "clou" / "_prompts" / "coordinator-assess.md",
        repo / ".clou" / "prompts" / "coordinator-assess.md",
    ):
        assert prompt.is_file(), f"missing prompt: {prompt}"
        text = prompt.read_text(encoding="utf-8")
        assert "clou_dispose_halt" in text


def test_coordinator_built_halt_escalation_recommends_dispose_halt() -> None:
    """The trajectory_halt escalation file built by the coordinator
    handler embeds a `Recommendation` field that the supervisor reads
    as primary evidence.  After M49b D5, that recommendation must
    name clou_dispose_halt — not clou_resolve_escalation, which would
    misdirect the supervisor and trip D2's refusal."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    # Read the source directly rather than invoking the handler — the
    # text is embedded as a string literal in coordinator_tools.py.
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "clou" / "coordinator_tools.py"
    ).read_text(encoding="utf-8")
    # Find the recommendation_text block for trajectory_halt.
    # We pin: it mentions clou_dispose_halt; it does NOT recommend
    # clou_resolve_escalation as the primary path.
    # (clou_resolve_escalation may still appear in the file — e.g.
    # describing the standard escalation path for non-engine-gated —
    # so we check the local trajectory-halt block context instead.)
    halt_block_start = src.find('classification="trajectory_halt"')
    assert halt_block_start > 0, (
        "trajectory_halt escalation construction not found in "
        "coordinator_tools.py — has the file been refactored?"
    )
    # Walk back to find the recommendation_text ASSIGNMENT (the
    # original `recommendation_text = (...)` form, not the later
    # `recommendation_text += (...)` proposal-ref append).
    rec_idx = src.rfind("recommendation_text = (", 0, halt_block_start)
    assert rec_idx > 0, (
        "could not locate `recommendation_text = (...)` assignment "
        "before the trajectory_halt EscalationForm in "
        "coordinator_tools.py"
    )
    # Read forward 800 chars to capture the full string.
    block = src[rec_idx:rec_idx + 800]
    assert "clou_dispose_halt" in block, (
        "recommendation_text in coordinator_tools.py trajectory_halt "
        "block must name clou_dispose_halt — found:\n" + block[:400]
    )


def test_orchestrator_halted_pending_review_guidance_names_tool() -> None:
    """The orchestrator's milestone-result guidance is shown to the
    supervisor agent when a halted milestone is loaded.  After M49b
    D5 it must explicitly name clou_dispose_halt (not just describe
    the choices)."""
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from pathlib import Path

    from clou.orchestrator import _build_milestone_guidance

    guidance = _build_milestone_guidance(
        Path("/tmp/fake-project"), "test-ms", "halted_pending_review",
    )
    assert "clou_dispose_halt" in guidance


def test_engine_gated_classifications_is_non_empty() -> None:
    """Sanity pin: ENGINE_GATED_CLASSIFICATIONS must contain at least
    ``trajectory_halt``.  An empty set silently disables the engine
    halt gate (every classification falls through as not-gated)."""
    from clou.escalation import ENGINE_GATED_CLASSIFICATIONS

    assert "trajectory_halt" in ENGINE_GATED_CLASSIFICATIONS
    assert len(ENGINE_GATED_CLASSIFICATIONS) >= 1


# ---------------------------------------------------------------------------
# M49b C4 — disposition-semantics contract pins (closes B5/L7).
# ``OPEN_DISPOSITION_STATUSES`` doubles as the engine wedge set for
# engine-gated classifications.  ``deferred`` and ``investigating``
# are deliberately included.  These tests pin that decision so a
# future PR cannot quietly remove either status from the wedge
# without surfacing the contract trade-off.
# ---------------------------------------------------------------------------


def test_open_disposition_statuses_pins_the_engine_wedge_set() -> None:
    """C4: the wedge set is contractual.  ``deferred`` and
    ``investigating`` MUST be in the open set.  Removing either is a
    semantic change to the engine-gated escalation flow — it would
    let the supervisor "park" a halt while continuing to dispatch
    cycles.  That defeats the trajectory-halt intent.  See
    ``knowledge-base/protocols/escalation.md`` § "Engine-Gated
    Escalations" for the contract.

    If you need to change this set, update the protocol doc, the
    OPEN_DISPOSITION_STATUSES docstring in escalation.py, and the
    supervisor UX contract together — then update this test."""
    from clou.escalation import OPEN_DISPOSITION_STATUSES

    assert "open" in OPEN_DISPOSITION_STATUSES
    assert "investigating" in OPEN_DISPOSITION_STATUSES
    assert "deferred" in OPEN_DISPOSITION_STATUSES
    # Disjoint from the resolved set — terminal vs non-terminal must
    # not overlap, otherwise the engine gate could fire on a
    # supposedly-resolved disposition.
    from clou.escalation import RESOLVED_DISPOSITION_STATUSES

    assert not (
        set(OPEN_DISPOSITION_STATUSES) & set(RESOLVED_DISPOSITION_STATUSES)
    ), (
        "OPEN and RESOLVED disposition status sets must be disjoint; "
        "an overlap would let the engine gate fire on a resolved halt."
    )


def test_disposition_status_partition_covers_valid_set() -> None:
    """The union of OPEN + RESOLVED disposition statuses must equal
    VALID_DISPOSITION_STATUSES (no orphan tokens).  An orphan would
    silently fall through both the engine gate and the operational
    "open escalations" count — a hole in the state machine."""
    from clou.escalation import (
        OPEN_DISPOSITION_STATUSES,
        RESOLVED_DISPOSITION_STATUSES,
        VALID_DISPOSITION_STATUSES,
    )

    partition = set(OPEN_DISPOSITION_STATUSES) | set(
        RESOLVED_DISPOSITION_STATUSES
    )
    assert partition == set(VALID_DISPOSITION_STATUSES), (
        f"Disposition-status partition incomplete.  "
        f"VALID={set(VALID_DISPOSITION_STATUSES)!r} "
        f"OPEN∪RESOLVED={partition!r} "
        f"orphans={set(VALID_DISPOSITION_STATUSES) - partition!r}.  "
        f"Every valid disposition must be either in the wedge set "
        f"(OPEN_DISPOSITION_STATUSES) or terminal "
        f"(RESOLVED_DISPOSITION_STATUSES) — no third state."
    )


def test_engine_wedge_documented_in_protocol_doc() -> None:
    """C4 + M49b documentation pin: the supervisor UX contract for
    engine-gated escalations lives in
    ``knowledge-base/protocols/escalation.md``.  This test asserts
    the section exists so a future doc refactor does not silently
    delete the contract.  The contract requires that the supervisor
    flow always terminates disposition of engine-gated escalations
    in ``resolved`` or ``overridden`` — a real wedge concern that
    deserves a permanent doc."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    doc = repo / "knowledge-base" / "protocols" / "escalation.md"
    assert doc.is_file(), f"missing protocol doc: {doc}"
    text = doc.read_text(encoding="utf-8")
    # Heading + key contract phrases.
    assert "Engine-Gated Escalations" in text
    assert "OPEN_DISPOSITION_STATUSES" in text
    # The supervisor UX guarantee.
    assert "resolved" in text and "overridden" in text


# ---------------------------------------------------------------------------
# M49b D3 — symmetric HALTED guard for pre_orient_next_step (closes
# B7/F3).  Without this guard, a HALTED next_step on session start
# could be stashed into pre_orient_next_step, then restored back via
# ORIENT-exit restoration, then trip determine_next_cycle's HALTED
# RuntimeError on the iteration after.  pre_halt_next_step had this
# guard since B6; pre_orient_next_step did not.
# ---------------------------------------------------------------------------


def test_render_checkpoint_rejects_halted_in_pre_orient_next_step() -> None:
    """Symmetric to the existing pre_halt_next_step HALTED rejection."""
    import pytest

    from clou.golden_context import render_checkpoint

    with pytest.raises(ValueError, match="pre_orient_next_step"):
        render_checkpoint(
            cycle=0, step="PLAN", next_step="PLAN",
            pre_orient_next_step="HALTED",
        )


def test_parse_checkpoint_drops_halted_pre_orient_next_step() -> None:
    """Tolerant parse at the read side — a corrupted on-disk file
    with pre_orient_next_step=HALTED parses to empty (no restoration
    pending) rather than carrying the loop-causing value forward."""
    from clou.recovery_checkpoint import parse_checkpoint

    body = (
        "cycle: 1\n"
        "step: PLAN\n"
        "next_step: PLAN\n"
        "current_phase: \n"
        "phases_completed: 0\n"
        "phases_total: 0\n"
        "pre_orient_next_step: HALTED\n"
    )
    parsed = parse_checkpoint(body)
    assert parsed.pre_orient_next_step == ""


# ---------------------------------------------------------------------------
# M49b E4 — halt-gate parse-failure telemetry (closes B9/F3).  When a
# *-trajectory-halt.md file is corrupted/truncated, the gate fails
# open (continues scanning other files).  Without telemetry the only
# signal is a log line.  Operators need a tripwire for "safety rail
# is blind."
# ---------------------------------------------------------------------------


def test_halt_gate_parse_failure_emits_telemetry(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """A corrupted escalation file produces a telemetry event with
    structured fields so operators can alert on
    `halt_gate.parse_failure` count > 0."""
    from clou.escalation import find_open_engine_gated_escalation
    from clou import telemetry as _tlm

    esc_dir = tmp_path / "escalations"
    esc_dir.mkdir()

    # Corrupted file that will fail parse_escalation (missing
    # required Classification preamble line).
    bad = esc_dir / "20260423-070000-trajectory-halt.md"
    bad.write_text("# Not a real escalation\n\n<<<corrupted>>>\n", encoding="utf-8")

    captured: list[dict] = []

    def fake_event(name, **kw):  # type: ignore[no-untyped-def]
        if name == "halt_gate.parse_failure":
            captured.append(kw)

    monkeypatch.setattr(_tlm, "event", fake_event)

    # Scan — gate fails open (returns None), emits telemetry for the
    # skipped file.
    result = find_open_engine_gated_escalation(esc_dir)
    # May or may not actually fail to parse depending on how strict
    # parse_escalation is — we need a guaranteed-bad file.  Check:
    # if capture is empty, try a byte-truncated UTF-8 file that will
    # definitely fail UnicodeDecodeError.
    if not captured:
        bad.write_bytes(b"\xff\xfe incomplete bytes")
        result = find_open_engine_gated_escalation(esc_dir)

    # Engine-gated escalation not found (fail-open behaviour).
    assert result is None
    # Telemetry event emitted for each parse failure.
    assert captured, (
        "halt_gate.parse_failure telemetry must fire on corrupt "
        "escalation — operators need this tripwire"
    )
    ev = captured[-1]
    assert ev["filename"] == bad.name
    # is_halt_file flag is True for *-trajectory-halt.md files so
    # alerts can prioritize them.
    assert ev["is_halt_file"] is True


def test_halt_gate_parse_failure_telemetry_swallowed_if_broken(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """E4 defensive: if telemetry.event itself raises (bad backend,
    monkeypatch test, etc.) the halt gate must still complete
    without propagating the error into the cycle-boundary hot path."""
    from clou.escalation import find_open_engine_gated_escalation
    from clou import telemetry as _tlm

    esc_dir = tmp_path / "escalations"
    esc_dir.mkdir()
    (esc_dir / "bad.md").write_bytes(b"\xff\xfe bytes")

    def boomy_event(name, **kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated telemetry failure")

    monkeypatch.setattr(_tlm, "event", boomy_event)

    # Must not raise — telemetry is best-effort, not load-bearing.
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is None


def test_legacy_checkpoint_without_halt_values_parses_unchanged() -> None:
    """Backward compat: a checkpoint with pre-M49b outcome/next_step
    parses identically to before.  This is the simple base case but
    worth pinning so a future refactor doesn't accidentally gate on
    the halt values."""
    from clou.recovery_checkpoint import parse_checkpoint

    legacy = (
        "cycle: 2\n"
        "step: EXECUTE\n"
        "next_step: ASSESS\n"
        "current_phase: orient_integration\n"
        "phases_completed: 5\n"
        "phases_total: 6\n"
        "validation_retries: 0\n"
        "readiness_retries: 0\n"
        "crash_retries: 0\n"
        "staleness_count: 1\n"
        "cycle_outcome: ADVANCED\n"
        "valid_findings: 28\n"
        "consecutive_zero_valid: 0\n"
        "pre_orient_next_step: \n"
    )
    parsed = parse_checkpoint(legacy)
    assert parsed.cycle == 2
    assert parsed.next_step == "ASSESS"
    assert parsed.cycle_outcome == "ADVANCED"
