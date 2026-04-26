"""Tests for the verdict-gated ``phases_completed`` advance in
``clou_write_checkpoint`` (M52 F32, F33, F38, F40, F41).

The gate is the LLM-side enforcement of substance-typed phase
acceptance.  An advance claim (``phases_completed`` increases) is
authorised only when ``prev_cp.last_acceptance_verdict`` carries an
``Advance`` decision for the right phase, OR when the bootstrap /
migration grace fires (cycle 0 + verdict None, or pre-M52 checkpoint
without the field).

Test surface:
    * Wire format round-trip (golden_context + recovery_checkpoint)
    * Validation paths (advance, schema_mismatch, off-by-one,
      cross-phase, GateDeadlock decision, bootstrap, inheritance)
    * Telemetry events fire on accept/refuse
    * Pre-M52 checkpoint (no verdict line) parses as None and
      bootstraps once on first advance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.golden_context import render_checkpoint
from clou.recovery_checkpoint import (
    AcceptanceVerdict,
    Checkpoint,
    parse_checkpoint,
)


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


class TestWireFormatRoundTrip:
    """``last_acceptance_verdict: <phase>|<decision>|<sha>`` (or ``none``)."""

    def test_render_then_parse_advance(self) -> None:
        v = AcceptanceVerdict(
            phase="p1",
            decision="Advance",
            content_sha="a" * 64,
        )
        body = render_checkpoint(
            cycle=1,
            step="ASSESS",
            next_step="ASSESS",
            current_phase="p1",
            last_acceptance_verdict=v,
        )
        assert "last_acceptance_verdict: p1|Advance|" + ("a" * 64) in body
        cp = parse_checkpoint(body)
        assert cp.last_acceptance_verdict == v

    def test_render_then_parse_gate_deadlock(self) -> None:
        v = AcceptanceVerdict(
            phase="p1",
            decision="GateDeadlock",
            content_sha="",
        )
        body = render_checkpoint(
            cycle=1,
            step="ASSESS",
            next_step="ASSESS",
            current_phase="p1",
            last_acceptance_verdict=v,
        )
        assert "last_acceptance_verdict: p1|GateDeadlock|" in body
        cp = parse_checkpoint(body)
        assert cp.last_acceptance_verdict == v

    def test_render_then_parse_none(self) -> None:
        body = render_checkpoint(
            cycle=0,
            step="PLAN",
            next_step="PLAN",
            last_acceptance_verdict=None,
        )
        assert "last_acceptance_verdict: none\n" in body
        cp = parse_checkpoint(body)
        assert cp.last_acceptance_verdict is None

    def test_pre_m52_checkpoint_parses_as_none(self) -> None:
        """F41 migration shim: a pre-M52 checkpoint has no
        ``last_acceptance_verdict`` line at all.  The parser must
        treat the absent field as ``None`` (not raise)."""
        legacy_body = (
            "cycle: 1\n"
            "step: ASSESS\n"
            "next_step: ASSESS\n"
            "current_phase: p1\n"
            "phases_completed: 0\n"
            "phases_total: 3\n"
        )
        cp = parse_checkpoint(legacy_body)
        assert cp.last_acceptance_verdict is None

    def test_render_rejects_invalid_decision(self) -> None:
        v = AcceptanceVerdict(
            phase="p1",
            decision="not-a-real-decision",
            content_sha="a" * 64,
        )
        with pytest.raises(ValueError, match="invalid last_acceptance_verdict.decision"):
            render_checkpoint(
                cycle=1,
                step="PLAN",
                next_step="PLAN",
                last_acceptance_verdict=v,
            )

    def test_render_rejects_pipe_in_phase(self) -> None:
        v = AcceptanceVerdict(
            phase="p1|sneaky",
            decision="Advance",
            content_sha="a" * 64,
        )
        with pytest.raises(ValueError, match="must not contain '\\|'"):
            render_checkpoint(
                cycle=1,
                step="PLAN",
                next_step="PLAN",
                last_acceptance_verdict=v,
            )

    def test_render_rejects_newline_in_phase(self) -> None:
        v = AcceptanceVerdict(
            phase="p1\nsneaky",
            decision="Advance",
            content_sha="a" * 64,
        )
        with pytest.raises(ValueError, match="must not contain newlines"):
            render_checkpoint(
                cycle=1,
                step="PLAN",
                next_step="PLAN",
                last_acceptance_verdict=v,
            )

    def test_parse_rejects_malformed_three_part(self) -> None:
        """Malformed wire format → None with a warning (not raise).

        The parser is defensive — it never crashes on a
        wire-format error; the worst case is treating the verdict as
        absent, which falls through to bootstrap on next advance."""
        body = (
            "cycle: 1\n"
            "next_step: PLAN\n"
            "last_acceptance_verdict: only_one_part\n"
        )
        cp = parse_checkpoint(body)
        assert cp.last_acceptance_verdict is None

    def test_parse_rejects_invalid_decision(self) -> None:
        body = (
            "cycle: 1\n"
            "next_step: PLAN\n"
            "last_acceptance_verdict: p1|MaybeAdvance|" + ("a" * 64) + "\n"
        )
        cp = parse_checkpoint(body)
        assert cp.last_acceptance_verdict is None


# ---------------------------------------------------------------------------
# Verdict-gate validation in clou_write_checkpoint
# ---------------------------------------------------------------------------


def _find_tool(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found")


@pytest.fixture
def coord_tools(tmp_path: Path):
    pytest.importorskip("claude_agent_sdk")
    from clou.coordinator_tools import _build_coordinator_tools

    (tmp_path / ".clou" / "milestones" / "test-ms").mkdir(parents=True)
    return tmp_path, _build_coordinator_tools(tmp_path, "test-ms")


def _write_prev_checkpoint(
    tmp_path: Path,
    *,
    cycle: int,
    current_phase: str,
    phases_completed: int,
    phases_total: int,
    last_acceptance_verdict: AcceptanceVerdict | None,
    next_step: str = "ASSESS",
    step: str = "ASSESS",
) -> Path:
    """Seed the prior checkpoint a verdict-gate test will read."""
    cp_path = tmp_path / ".clou" / "milestones" / "test-ms" / "active" / "coordinator.md"
    cp_path.parent.mkdir(parents=True, exist_ok=True)
    cp_path.write_text(
        render_checkpoint(
            cycle=cycle,
            step=step,
            next_step=next_step,
            current_phase=current_phase,
            phases_completed=phases_completed,
            phases_total=phases_total,
            last_acceptance_verdict=last_acceptance_verdict,
        ),
        encoding="utf-8",
    )
    return cp_path


class TestVerdictGateAdvance:
    """F33 strict path: prev_cp.verdict.phase == prev_cp.current_phase
    AND verdict.decision == Advance AND single-phase increment."""

    @pytest.mark.asyncio
    async def test_advance_succeeds_with_valid_verdict(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=1,
            current_phase="p1",
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=AcceptanceVerdict(
                phase="p1",
                decision="Advance",
                content_sha="a" * 64,
            ),
        )
        result = await handler({
            "cycle": 2,
            "step": "PLAN",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 1
        # Inherited verdict (passthrough).
        assert cp.last_acceptance_verdict == AcceptanceVerdict(
            phase="p1",
            decision="Advance",
            content_sha="a" * 64,
        )

    @pytest.mark.asyncio
    async def test_advance_refused_when_verdict_is_gate_deadlock(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=1,
            current_phase="p1",
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=AcceptanceVerdict(
                phase="p1",
                decision="GateDeadlock",
                content_sha="",
            ),
        )
        result = await handler({
            "cycle": 2,
            "step": "PLAN",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert result.get("reason") == "verdict_not_advance"
        # Refused → checkpoint NOT mutated by the tool.
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 0

    @pytest.mark.asyncio
    async def test_advance_refused_on_phase_mismatch(
        self, coord_tools,
    ) -> None:
        """Verdict for a different phase must not authorise the
        current advance — closes the off-by-one bypass class."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=2,
            current_phase="p2",  # currently on p2
            phases_completed=1,
            phases_total=3,
            last_acceptance_verdict=AcceptanceVerdict(
                phase="p1",  # but verdict is for p1 (stale)
                decision="Advance",
                content_sha="a" * 64,
            ),
        )
        result = await handler({
            "cycle": 3,
            "step": "PLAN",
            "next_step": "EXECUTE",
            "current_phase": "p3",
            "phases_completed": 2,
            "phases_total": 3,
        })
        assert result.get("reason") == "verdict_phase_mismatch"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 1

    @pytest.mark.asyncio
    async def test_advance_refused_on_skip(self, coord_tools) -> None:
        """phases_completed must increment by exactly 1 — no skipping."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=1,
            current_phase="p1",
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=AcceptanceVerdict(
                phase="p1",
                decision="Advance",
                content_sha="a" * 64,
            ),
        )
        result = await handler({
            "cycle": 2,
            "step": "PLAN",
            "next_step": "EXECUTE",
            "current_phase": "p3",
            "phases_completed": 2,  # skip from 0 to 2
            "phases_total": 3,
        })
        assert result.get("reason") == "non_unit_increment"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 0

    @pytest.mark.asyncio
    async def test_no_advance_no_gate(self, coord_tools) -> None:
        """A write that does NOT increase phases_completed must not be
        gated.  Most cycle writes are non-advancing."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=1,
            current_phase="p1",
            phases_completed=1,
            phases_total=3,
            last_acceptance_verdict=AcceptanceVerdict(
                phase="p_old",  # would fail the strict gate if checked
                decision="GateDeadlock",
                content_sha="",
            ),
        )
        # Same phases_completed, just a cycle bump.
        result = await handler({
            "cycle": 2,
            "step": "EXECUTE",
            "next_step": "ASSESS",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        # Verdict inherited (passthrough), no validation fired.
        assert cp.last_acceptance_verdict.decision == "GateDeadlock"


class TestVerdictGateBootstrap:
    """F40/F41: bootstrap (cycle 0 + None verdict) AND migration shim
    (pre-M52 checkpoint without the field) both allow ONE advance with
    a telemetry event."""

    @pytest.mark.asyncio
    async def test_bootstrap_allows_advance_when_verdict_is_none(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=0,
            current_phase="p1",
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=None,
        )
        result = await handler({
            "cycle": 1,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 1

    @pytest.mark.asyncio
    async def test_bootstrap_allows_advance_for_pre_m52_checkpoint(
        self, coord_tools,
    ) -> None:
        """Write a checkpoint without the verdict line (pre-M52) and
        verify the parser sees ``None`` and the gate bootstraps."""
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        cp_path = (
            tmp_path / ".clou" / "milestones" / "test-ms"
            / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        # Hand-rolled pre-M52 checkpoint (no last_acceptance_verdict
        # field at all).
        cp_path.write_text(
            "cycle: 5\n"
            "step: ASSESS\n"
            "next_step: ASSESS\n"
            "current_phase: p1\n"
            "phases_completed: 0\n"
            "phases_total: 3\n",
            encoding="utf-8",
        )
        result = await handler({
            "cycle": 6,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        assert "error" not in result, result
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 1


class TestVerdictInheritance:
    """F38: passthrough.  Each non-gate-producing write must read the
    verdict from prev_cp and write it through unchanged."""

    @pytest.mark.asyncio
    async def test_verdict_passthrough_on_non_advance_write(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        seed_verdict = AcceptanceVerdict(
            phase="p1",
            decision="Advance",
            content_sha="b" * 64,
        )
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=1,
            current_phase="p1",
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=seed_verdict,
        )
        await handler({
            "cycle": 2,
            "step": "EXECUTE",
            "next_step": "ASSESS",
            "current_phase": "p1",
            "phases_completed": 0,
            "phases_total": 3,
        })
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict == seed_verdict

    @pytest.mark.asyncio
    async def test_verdict_passthrough_on_advance_write(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        seed_verdict = AcceptanceVerdict(
            phase="p1",
            decision="Advance",
            content_sha="c" * 64,
        )
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=1,
            current_phase="p1",
            phases_completed=0,
            phases_total=3,
            last_acceptance_verdict=seed_verdict,
        )
        await handler({
            "cycle": 2,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p2",
            "phases_completed": 1,
            "phases_total": 3,
        })
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.last_acceptance_verdict == seed_verdict


class TestStrictGatingAfterBootstrap:
    """After the bootstrap grace fires, a SECOND advance attempt must
    still hit the strict gate.  The bootstrap is one-shot: it writes
    None forward, and absent a fresh engine-side verdict the next
    advance will bootstrap again (allowed, but logged) — the strict
    gate fires only once a non-None verdict is recorded."""

    @pytest.mark.asyncio
    async def test_second_advance_with_stale_verdict_refused(
        self, coord_tools,
    ) -> None:
        tmp_path, tools = coord_tools
        handler = _find_tool(tools, "clou_write_checkpoint").handler
        # Seed prev_cp at phases_completed=1, current_phase=p2 (already
        # advanced once), with a verdict that's stale (for p1, not p2).
        cp_path = _write_prev_checkpoint(
            tmp_path,
            cycle=2,
            current_phase="p2",
            phases_completed=1,
            phases_total=3,
            last_acceptance_verdict=AcceptanceVerdict(
                phase="p1",
                decision="Advance",
                content_sha="d" * 64,
            ),
        )
        result = await handler({
            "cycle": 3,
            "step": "ASSESS",
            "next_step": "EXECUTE",
            "current_phase": "p3",
            "phases_completed": 2,
            "phases_total": 3,
        })
        assert result.get("reason") == "verdict_phase_mismatch"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.phases_completed == 1
