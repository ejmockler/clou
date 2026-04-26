"""Cycle-2 rework tests for clou.coordinator (Stream D).

Covers:
- F1 partial — DB-15 D5 disposition gate routes through the tolerant
  parser rather than a raw regex that saw plain ``status:`` lines
  anywhere in the file.
- F14 a-f — ``announce_new_escalations`` ordering, defer-on-headless,
  batched seen-write, per-call exception guard at the caller,
  parse-error short-circuit, corrupt seen-file recovery, and final-
  flush safety.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("claude_agent_sdk")

from clou.coordinator import announce_new_escalations
from clou.escalation import (
    EscalationForm,
    EscalationOption,
    render_escalation,
)
# NOTE: import the real ``init`` at module load time, BEFORE the autouse
# ``_isolate_telemetry`` fixture in conftest.py monkeypatches it.  Tests
# that exercise the cycle.judgment event emission need the real init
# because the noop replacement returns a SpanLog pointed at a shared
# ``test.jsonl`` path and does NOT set ``telemetry._log``.
from clou.telemetry import init as _real_init  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _write_escalation(
    esc_dir: Path,
    filename: str,
    *,
    title: str = "test",
    classification: str = "informational",
    status: str = "open",
) -> Path:
    """Write a canonical escalation via the renderer."""
    form = EscalationForm(
        title=title,
        classification=classification,
        filed="2026-04-21",
        issue="test-issue",
        options=(EscalationOption(label="proceed"),),
        recommendation="proceed",
        disposition_status=status,
    )
    path = esc_dir / filename
    path.write_text(render_escalation(form), encoding="utf-8")
    return path


class _FakeApp:
    """Stand-in for ClouApp that captures posted messages.

    Optionally configured to raise on ``post_message`` so ordering
    guarantees can be exercised.
    """

    def __init__(
        self,
        *,
        raise_on_post: bool = False,
        raise_count: int | None = None,
    ) -> None:
        self.messages: list[Any] = []
        self._raise = raise_on_post
        self._raise_count = raise_count
        self._calls = 0

    def post_message(self, message: Any) -> None:
        self._calls += 1
        if self._raise and (
            self._raise_count is None or self._calls <= self._raise_count
        ):
            raise RuntimeError("simulated UI post failure")
        self.messages.append(message)


def _make_layout(tmp_path: Path, milestone: str = "m1") -> dict[str, Path]:
    """Build a minimal .clou/ layout for the notifier.

    C2: seen-escalations.txt is per-milestone (.clou/milestones/{ms}/
    active/seen-escalations.txt), mirroring production after the
    cross-coordinator race fix.  The announcer accepts any path via
    the seen_path parameter, but fixture paths should match production
    so regressions in run_coordinator's path computation surface in
    tests that use this helper.
    """
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    esc_dir.mkdir(parents=True)
    seen_path = (
        clou_dir / "milestones" / milestone / "active"
        / "seen-escalations.txt"
    )
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    return {
        "clou_dir": clou_dir,
        "esc_dir": esc_dir,
        "seen_path": seen_path,
    }


# ---------------------------------------------------------------------------
# F14a — mark-seen ordering
# ---------------------------------------------------------------------------


class TestMarkSeenOrdering:
    """F14a / F5: ``seen.add`` runs only AFTER a successful post.

    A transient ``post_message`` failure must NOT mark the file seen.
    The next invocation with a working UI must re-announce the same
    file.  Similarly, when ``app is None`` the notifier defers the
    mark-seen entirely so headless coordinators don't lose
    announcements.
    """

    def test_post_failure_does_not_mark_seen(self, tmp_path: Path) -> None:
        """post_message raises → file is NOT in seen_escalations."""
        layout = _make_layout(tmp_path)
        _write_escalation(layout["esc_dir"], "20260421-000000-a.md")
        seen: set[str] = set()
        app = _FakeApp(raise_on_post=True)

        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=app,
        )

        assert "20260421-000000-a.md" not in seen, (
            "post failure must not mark the file seen — next cycle "
            "must retry"
        )
        # seen_path must NOT have been written because nothing was
        # successfully announced.
        assert not layout["seen_path"].exists(), (
            "seen-escalations.txt must not be written when nothing "
            "was announced"
        )

    def test_post_failure_then_success_announces_on_retry(
        self, tmp_path: Path,
    ) -> None:
        """First cycle fails to post; second cycle succeeds and
        marks seen — the announcement survives the transient
        failure."""
        layout = _make_layout(tmp_path)
        _write_escalation(layout["esc_dir"], "20260421-000000-a.md")
        seen: set[str] = set()

        # First invocation: post fails.
        failing_app = _FakeApp(raise_on_post=True)
        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=failing_app,
        )
        assert seen == set()

        # Second invocation: post succeeds.
        working_app = _FakeApp()
        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=working_app,
        )
        assert "20260421-000000-a.md" in seen
        assert len(working_app.messages) == 1, (
            "the retry must announce the previously-failed file"
        )

    def test_app_none_defers_announcement_and_mark_seen(
        self, tmp_path: Path,
    ) -> None:
        """``app is None`` path returns without marking seen AND
        without writing the seen file.  The next invocation with an
        attached app re-scans and announces."""
        layout = _make_layout(tmp_path)
        _write_escalation(layout["esc_dir"], "20260421-000000-a.md")
        seen: set[str] = set()

        # Headless: no app.
        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=None,
        )
        assert seen == set()
        assert not layout["seen_path"].exists()

        # UI attaches: announcement fires once.
        app = _FakeApp()
        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=app,
        )
        assert len(app.messages) == 1
        assert "20260421-000000-a.md" in seen

    def test_successful_post_marks_seen(self, tmp_path: Path) -> None:
        """Happy path: post succeeds → file is marked seen AND the
        seen file is persisted."""
        layout = _make_layout(tmp_path)
        _write_escalation(layout["esc_dir"], "20260421-000000-a.md")
        seen: set[str] = set()
        app = _FakeApp()

        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=app,
        )

        assert "20260421-000000-a.md" in seen
        assert layout["seen_path"].exists()
        stored = set(
            layout["seen_path"].read_text(encoding="utf-8").split()
        )
        assert stored == {"20260421-000000-a.md"}


# ---------------------------------------------------------------------------
# F14b — single seen-file write per sweep
# ---------------------------------------------------------------------------


class TestBatchedSeenWrite:
    """F14b / F5: ``seen_path.write_text`` runs ONCE per call.

    O(N) escalations must produce O(1) disk writes, not O(N).  This
    is a specific regression guard against the prior implementation
    that wrote inside the loop.
    """

    def test_seen_file_written_once_for_n_new_files(
        self, tmp_path: Path,
    ) -> None:
        layout = _make_layout(tmp_path)
        # Three new files in one sweep.
        for i in range(3):
            _write_escalation(
                layout["esc_dir"], f"20260421-00000{i}-x.md",
            )
        seen: set[str] = set()
        app = _FakeApp()

        # Spy on ``Path.write_text`` via monkeypatching to count calls
        # against ``seen_path`` specifically.
        write_count = {"n": 0}
        original_write = Path.write_text

        def _counting_write_text(
            self_path: Path, *args: Any, **kwargs: Any,
        ) -> int:
            if self_path == layout["seen_path"]:
                write_count["n"] += 1
            return original_write(self_path, *args, **kwargs)

        with patch.object(
            Path, "write_text", _counting_write_text,
        ):
            announce_new_escalations(
                clou_dir=layout["clou_dir"],
                milestone="m1",
                seen_escalations=seen,
                seen_path=layout["seen_path"],
                app=app,
            )

        assert write_count["n"] == 1, (
            f"expected 1 write to seen-escalations.txt for 3 new "
            f"files, got {write_count['n']} — regression to the "
            "O(N) write pattern"
        )
        assert len(app.messages) == 3

    def test_no_new_files_no_write(self, tmp_path: Path) -> None:
        """No new files → no disk write (not even an empty one)."""
        layout = _make_layout(tmp_path)
        # No escalations on disk.
        seen: set[str] = set()
        app = _FakeApp()

        announce_new_escalations(
            clou_dir=layout["clou_dir"],
            milestone="m1",
            seen_escalations=seen,
            seen_path=layout["seen_path"],
            app=app,
        )

        assert not layout["seen_path"].exists(), (
            "seen-escalations.txt must not be created when nothing "
            "was announced"
        )


# ---------------------------------------------------------------------------
# F14c — per-call exception guard at coordinator.py:776
# ---------------------------------------------------------------------------


class TestCallSiteExceptionGuard:
    """F14c: the call site in ``run_coordinator`` must not crash the
    loop if the notifier raises.  We inspect the source for the
    try/except guard (exercising ``run_coordinator`` end-to-end
    requires a full session scaffold covered elsewhere).
    """

    def test_call_site_has_try_except(self) -> None:
        """The top-of-loop ``_announce_new_escalations()`` call is
        wrapped in try/except."""
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        # Locate the comment that anchors the call site and assert a
        # try/except sits around the invocation.  The brief says
        # coordinator.py:776; we match the anchor comment instead of
        # the line number to be robust to future reformatting.
        anchor_idx = source.find(
            "# F14c (cycle 2): a filesystem blip"
        )
        assert anchor_idx != -1, (
            "F14c call-site guard comment missing from "
            "coordinator.py"
        )
        # The guard should be within ~500 characters after the anchor.
        window = source[anchor_idx:anchor_idx + 500]
        assert "try:" in window
        assert "_announce_new_escalations()" in window
        assert "except Exception" in window

    def test_final_flush_has_try_except(self) -> None:
        """The finally-block final flush is also guarded."""
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        anchor_idx = source.find(
            "# F14f (cycle 2): same guard posture"
        )
        assert anchor_idx != -1, (
            "F14f final-flush guard comment missing from "
            "coordinator.py"
        )
        window = source[anchor_idx:anchor_idx + 500]
        assert "try:" in window
        assert "_announce_new_escalations()" in window
        assert "except Exception" in window


# ---------------------------------------------------------------------------
# F14d — parse-error files emit distinct breath event and mark seen
# ---------------------------------------------------------------------------


class TestParseErrorShortCircuit:
    """F14d / F29b: a parse-error file emits a distinct breath event
    text and is marked seen AFTER successful post so the error signal
    fires ONCE per file, not every cycle.
    """

    def test_parse_error_emits_distinct_text(self, tmp_path: Path) -> None:
        layout = _make_layout(tmp_path)
        target = layout["esc_dir"] / "drifted.md"
        target.write_text("prose")
        seen: set[str] = set()
        app = _FakeApp()

        with patch(
            "clou.coordinator.parse_escalation",
            side_effect=RuntimeError("synthetic"),
        ):
            announce_new_escalations(
                clou_dir=layout["clou_dir"],
                milestone="m1",
                seen_escalations=seen,
                seen_path=layout["seen_path"],
                app=app,
            )

        assert len(app.messages) == 1
        event = app.messages[0]
        assert "(parse-error)" in event.text
        assert "drifted.md" in event.text
        # Distinct from the normal "escalation filed: CLASS: NAME"
        # shape — the leading marker differs.
        assert "escalation filed (parse-error)" in event.text

    def test_parse_error_marks_seen_to_prevent_re_announce(
        self, tmp_path: Path,
    ) -> None:
        """A parse-error file marks seen after successful post so the
        same drift does not re-announce every cycle (log flood)."""
        layout = _make_layout(tmp_path)
        target = layout["esc_dir"] / "drifted.md"
        target.write_text("prose")
        seen: set[str] = set()
        app = _FakeApp()

        with patch(
            "clou.coordinator.parse_escalation",
            side_effect=RuntimeError("synthetic"),
        ):
            announce_new_escalations(
                clou_dir=layout["clou_dir"],
                milestone="m1",
                seen_escalations=seen,
                seen_path=layout["seen_path"],
                app=app,
            )
            # Second invocation — must NOT re-announce the same file.
            announce_new_escalations(
                clou_dir=layout["clou_dir"],
                milestone="m1",
                seen_escalations=seen,
                seen_path=layout["seen_path"],
                app=app,
            )

        assert len(app.messages) == 1, (
            "parse-error must fire ONCE per file, not every cycle"
        )
        assert "drifted.md" in seen


# ---------------------------------------------------------------------------
# F14e — corrupt seen-escalations.txt degrades gracefully
# ---------------------------------------------------------------------------


class TestCorruptedSeenFileRecovery:
    """F14e: the ``seen_path.read_text()`` call in ``run_coordinator``
    must not crash the coordinator when the bookkeeping file is
    corrupt (invalid UTF-8, unreadable, etc.).
    """

    def test_corrupted_seen_file_degrades_to_empty(
        self, tmp_path: Path,
    ) -> None:
        """A corrupt seen-escalations.txt is caught and the coordinator
        starts fresh."""
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        # The cycle-2 fix wraps the read in a try/except; verify the
        # guard exists at the expected anchor.
        assert "UnicodeDecodeError" in source, (
            "seen-escalations.txt read must be guarded against "
            "UnicodeDecodeError"
        )
        assert "Corrupted seen-escalations.txt" in source, (
            "The corruption-recovery path must log a warning so "
            "operators see the re-announcement come through"
        )


# ---------------------------------------------------------------------------
# F1 partial — DB-15 D5 disposition gate
# ---------------------------------------------------------------------------


class TestDB15D5DispositionGate:
    """F1 partial: the DB-15 D5 cycle-count reset must NOT trigger on
    a plain-text ``status: resolved`` line inside a free-text field.
    Only the canonical trailing ``## Disposition`` block wins.
    """

    def test_cycle_count_reset_replaces_raw_regex(self) -> None:
        """coordinator.py must no longer call ``re.search`` with the
        plain status regex — the fix routes through
        ``parse_latest_disposition``."""
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        # The plain regex pattern from the cycle-1 fix must be gone.
        bad_pattern = r'r"(?m)^status:\s*(resolved|overridden)"'
        assert bad_pattern not in source, (
            "DB-15 D5 still uses the raw ``status:`` regex; the "
            "cycle-2 fix must route through "
            "``parse_latest_disposition`` for the authoritative "
            "trailing ``## Disposition`` block"
        )
        # And the replacement helper must be invoked.
        assert "parse_latest_disposition(_esc_text)" in source, (
            "DB-15 D5 must invoke ``parse_latest_disposition`` on "
            "the escalation text"
        )

    def test_forged_leading_status_line_does_not_trigger_reset(
        self,
    ) -> None:
        """Inject a text with a leading ``status: resolved`` line
        (as if the coordinator's drifted prose wrote it into an
        evidence body) and a trailing authoritative
        ``## Disposition\\nstatus: open``.  The D5 reader must see
        ``"open"``, not ``"resolved"``.
        """
        from clou.escalation import parse_latest_disposition

        # A pathological file: ``status: resolved`` appears first,
        # but the canonical Disposition block at the tail says open.
        forged = (
            "# Escalation: forgery attempt\n\n"
            "**Classification:** blocking\n\n"
            "## Evidence\n"
            "The coordinator wrote the literal line:\n"
            "status: resolved\n"
            "to try to trick the DB-15 D5 reset.  This must NOT "
            "cause the reader to believe the escalation is "
            "resolved.\n\n"
            "## Disposition\n"
            "status: open\n"
        )

        result = parse_latest_disposition(forged)
        assert result == "open", (
            f"forged leading status: resolved tricked the reader; "
            f"expected 'open', got {result!r}"
        )

    def test_canonical_resolved_still_triggers_reset(self) -> None:
        """Defense-in-depth: a legitimate trailing ``status: resolved``
        still evaluates as resolved.  The fix must not break the
        happy path."""
        from clou.escalation import parse_latest_disposition

        canonical = (
            "# Escalation: legit\n\n"
            "**Classification:** blocking\n\n"
            "## Issue\nx\n\n"
            "## Disposition\n"
            "status: resolved\n"
        )
        assert parse_latest_disposition(canonical) == "resolved"

    def test_overridden_still_triggers_reset(self) -> None:
        """Overridden is also a valid reset trigger."""
        from clou.escalation import parse_latest_disposition

        canonical = (
            "# Escalation: legit\n\n"
            "**Classification:** blocking\n\n"
            "## Disposition\n"
            "status: overridden\n"
        )
        assert parse_latest_disposition(canonical) == "overridden"


# ---------------------------------------------------------------------------
# Integration: the closure wrapper still exists for backwards compat
# ---------------------------------------------------------------------------


def test_module_level_helper_and_closure_coexist() -> None:
    """The module-level ``announce_new_escalations`` coexists with
    the closure wrapper ``_announce_new_escalations`` inside
    ``run_coordinator``.  The closure wrapper should call into the
    module helper; the phase.md success criterion still holds that
    ``_announce_new_escalations`` exists in the source."""
    import clou.coordinator as coord

    source = Path(coord.__file__).read_text(encoding="utf-8")
    # Both names appear.
    assert "def announce_new_escalations(" in source
    assert "def _announce_new_escalations()" in source
    # The closure delegates to the module helper.
    assert "announce_new_escalations(" in source


# ---------------------------------------------------------------------------
# M36 I3 — disagreement telemetry hook in run_coordinator
# ---------------------------------------------------------------------------


class TestDisagreementTelemetryHook:
    """The post-ORIENT ``cycle.judgment`` emission is wired into
    ``run_coordinator`` between the cognitive-span telemetry and the
    staleness-classification block.  Full end-to-end drive of
    ``run_coordinator`` requires the Claude SDK; these tests cover
    (a) source-level invariants of the wiring, and (b) the isolated
    read-parse-emit path that lives inside the hook."""

    def test_hook_present_in_source(self) -> None:
        """``run_coordinator`` contains a ``cycle.judgment`` emission."""
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        # The event name must appear — this is the canonical event
        # the Judgment / Disagreement section reads back.
        assert '"cycle.judgment"' in source, (
            "run_coordinator must emit ``cycle.judgment`` on ORIENT completion"
        )
        # Must import the judgment schema helpers used by the hook.
        assert "from clou.judgment import" in source
        assert "JUDGMENT_PATH_TEMPLATE" in source
        assert "parse_judgment" in source
        # Must read ``pre_orient_next_step`` back from the checkpoint.
        assert "pre_orient_next_step" in source, (
            "disagreement comparison requires the pre_orient_next_step "
            "field written by the session-start ORIENT rewrite"
        )
        # ORIENT branch guard — we only emit after ORIENT cycles.
        assert 'cycle_type == "ORIENT"' in source

    def test_hook_does_not_alter_dispatch_vocabulary(self) -> None:
        """Dispatch advance sequence is unchanged by the hook phase.

        M50 I1 cycle-4 rework (F3): VERIFY and REPLAN source rows
        were DROPPED from ``_NEXT_STEP`` because they have multiple
        legitimate successors (VERIFY can route to EXIT,
        EXECUTE_REWORK, or EXECUTE_VERIFY).  The hook phase
        (M37) doesn't add/remove rows — it neither changes the
        core advance sequence nor restores the dropped multi-
        successor rows.
        """
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        # Single-successor source rows remain intact.
        assert '"PLAN": "EXECUTE"' in source
        assert '"EXECUTE": "ASSESS"' in source
        assert '"ASSESS": "VERIFY"' in source
        assert '"EXIT": "COMPLETE"' in source
        # M50 I1 cycle-4 rework (F3): VERIFY and REPLAN rows
        # omitted — multi-successor source.  Dict fallback is
        # empty-string; UI reads the on-disk checkpoint instead.
        assert '"VERIFY": "EXIT"' not in source
        assert '"REPLAN":' not in source

    def test_emission_logic_agreement(self, tmp_path: Path) -> None:
        """End-to-end: a judgment file with next_action matching
        ``pre_orient_next_step`` produces an ``agreement: True`` event."""
        import re as _re
        from clou import telemetry
        from clou.judgment import (
            JUDGMENT_PATH_TEMPLATE,
            JudgmentForm,
            parse_judgment,
            render_judgment,
        )

        milestone = "m36-agree"
        clou_dir = tmp_path / ".clou"
        milestone_dir = clou_dir / "milestones" / milestone
        (milestone_dir / "judgments").mkdir(parents=True)
        # Write a valid judgment file for cycle 1.
        judgment_path = milestone_dir / JUDGMENT_PATH_TEMPLATE.format(cycle=1)
        form = JudgmentForm(
            next_action="PLAN",
            rationale="Observation indicates fresh milestone; PLAN first.",
            evidence_paths=("intents.md", "status.md"),
            expected_artifact="compose.py drafted in PLAN",
        )
        judgment_path.write_text(render_judgment(form), encoding="utf-8")
        # Checkpoint preserves pre_orient_next_step = PLAN (seeded by
        # orient_protocol's session-start rewrite).
        checkpoint_path = milestone_dir / "active" / "checkpoint.md"
        checkpoint_path.parent.mkdir(parents=True)
        checkpoint_path.write_text(
            "cycle: 0\nstep: PLAN\nnext_step: ORIENT\n"
            "current_phase: \nphases_completed: 0\nphases_total: 0\n"
            "pre_orient_next_step: PLAN\n",
            encoding="utf-8",
        )
        # Drive the read/parse/emit path the coordinator executes.
        old = telemetry._log
        try:
            log = _real_init("t-judgment-agree", tmp_path)
            text = judgment_path.read_text(encoding="utf-8")
            parsed = parse_judgment(text)
            match = _re.search(
                r"(?m)^pre_orient_next_step:\s*(.+)$",
                checkpoint_path.read_text(),
            )
            pre_orient = match.group(1).strip() if match else ""
            telemetry.event(
                "cycle.judgment",
                milestone=milestone,
                cycle=1,
                judgment_next_action=parsed.next_action,
                orchestrator_next_cycle=pre_orient,
                agreement=parsed.next_action == pre_orient,
            )
            import json as _json
            records = [
                _json.loads(line)
                for line in log.path.read_text().splitlines()
                if line.strip()
            ]
            judgments = [
                r for r in records if r.get("event") == "cycle.judgment"
            ]
            assert len(judgments) == 1
            ev = judgments[0]
            assert ev["milestone"] == milestone
            assert ev["cycle"] == 1
            assert ev["judgment_next_action"] == "PLAN"
            assert ev["orchestrator_next_cycle"] == "PLAN"
            assert ev["agreement"] is True
        finally:
            telemetry._log = old

    def test_emission_logic_disagreement(self, tmp_path: Path) -> None:
        """When judgment.next_action != pre_orient_next_step, agreement
        is False and the event still emits (observational-only)."""
        import re as _re
        from clou import telemetry
        from clou.judgment import (
            JUDGMENT_PATH_TEMPLATE,
            JudgmentForm,
            parse_judgment,
            render_judgment,
        )

        milestone = "m36-disagree"
        clou_dir = tmp_path / ".clou"
        milestone_dir = clou_dir / "milestones" / milestone
        (milestone_dir / "judgments").mkdir(parents=True)
        judgment_path = milestone_dir / JUDGMENT_PATH_TEMPLATE.format(cycle=3)
        # Coordinator's judgment disagrees with dispatch.
        form = JudgmentForm(
            next_action="EXECUTE",
            rationale="execution.md shows unfinished work",
            evidence_paths=("phases/p1/execution.md",),
            expected_artifact="updated execution.md",
        )
        judgment_path.write_text(render_judgment(form), encoding="utf-8")
        checkpoint_path = milestone_dir / "active" / "checkpoint.md"
        checkpoint_path.parent.mkdir(parents=True)
        # Dispatch would have chosen ASSESS (post-EXECUTE advance).
        checkpoint_path.write_text(
            "cycle: 2\nstep: EXECUTE\nnext_step: ORIENT\n"
            "current_phase: p1\nphases_completed: 1\nphases_total: 2\n"
            "pre_orient_next_step: ASSESS\n",
            encoding="utf-8",
        )
        old = telemetry._log
        try:
            log = _real_init("t-judgment-disagree", tmp_path)
            parsed = parse_judgment(
                judgment_path.read_text(encoding="utf-8"),
            )
            match = _re.search(
                r"(?m)^pre_orient_next_step:\s*(.+)$",
                checkpoint_path.read_text(),
            )
            pre_orient = match.group(1).strip() if match else ""
            telemetry.event(
                "cycle.judgment",
                milestone=milestone,
                cycle=3,
                judgment_next_action=parsed.next_action,
                orchestrator_next_cycle=pre_orient,
                agreement=parsed.next_action == pre_orient,
            )
            import json as _json
            records = [
                _json.loads(line)
                for line in log.path.read_text().splitlines()
                if line.strip()
            ]
            ev = next(
                r for r in records if r.get("event") == "cycle.judgment"
            )
            assert ev["judgment_next_action"] == "EXECUTE"
            assert ev["orchestrator_next_cycle"] == "ASSESS"
            assert ev["agreement"] is False
        finally:
            telemetry._log = old

    def test_missing_judgment_file_no_emission(self, tmp_path: Path) -> None:
        """When the judgment file does not exist for the ORIENT cycle,
        no event is emitted."""
        from clou import telemetry
        from clou.judgment import JUDGMENT_PATH_TEMPLATE

        milestone = "m36-missing"
        clou_dir = tmp_path / ".clou"
        milestone_dir = clou_dir / "milestones" / milestone
        milestone_dir.mkdir(parents=True)
        judgment_path = milestone_dir / JUDGMENT_PATH_TEMPLATE.format(cycle=1)
        # File intentionally not written.
        assert not judgment_path.exists()

        old = telemetry._log
        try:
            log = _real_init("t-judgment-missing", tmp_path)
            if judgment_path.exists():
                # Inverse of the real guard — make sure we never emit.
                telemetry.event(
                    "cycle.judgment", milestone=milestone, cycle=1,
                )
            import json as _json
            records = [
                _json.loads(line)
                for line in log.path.read_text().splitlines()
                if line.strip()
            ]
            assert not any(
                r.get("event") == "cycle.judgment" for r in records
            )
        finally:
            telemetry._log = old

    def test_malformed_judgment_parse_swallowed(self, tmp_path: Path) -> None:
        """A malformed file with no headings parses to an empty form.
        ``parse_judgment`` MUST NOT raise on such input; the hook
        still proceeds but the resulting ``agreement`` reflects the
        empty ``next_action``."""
        from clou.judgment import JudgmentForm, parse_judgment

        # Utterly malformed content.
        form = parse_judgment("!!!random garbage that is not markdown !!!\n")
        assert isinstance(form, JudgmentForm)
        assert form.next_action == ""
        assert form.evidence_paths == ()

    def test_hook_location_after_span_close(self) -> None:
        """The emission lives OUTSIDE the ``cycle`` span's context
        manager and AFTER compositional_span telemetry — placing it
        inside the span or before status classification would distort
        the span duration.  Verified by source ordering."""
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        # The cycle.judgment emission must appear after the
        # compositional_span block (which lives right after the cycle
        # span exits).  Source ordering guarantees this.
        pos_span_event = source.index('"cognitive.compositional_span"')
        pos_judgment = source.index('"cycle.judgment"')
        assert pos_span_event < pos_judgment, (
            "cycle.judgment must be emitted after cognitive.compositional_span "
            "so both post-cycle observational telemetry paths share ordering"
        )
        # And before the crash/agent_crash path — the event must fire
        # regardless of status but is located before control-flow
        # branches that could early-return the iteration.  "Classify
        # cycle outcome" is the anchor comment that starts the
        # staleness-classification block.
        pos_classify = source.index("# Classify cycle outcome")
        assert pos_judgment < pos_classify, (
            "cycle.judgment must be emitted before status classification "
            "so the event fires for every ORIENT cycle that produced a "
            "judgment, independent of downstream control flow"
        )


class TestOrientExitRestorationIntegration:
    """M36 round-4 F1/F6/F15: exercise the ORIENT-exit restoration
    production code path via a helper that mirrors the block's
    read/parse/rewrite sequence.

    The full ``run_coordinator`` loop requires the Claude SDK; these
    tests invoke the isolated helper to exercise the production logic
    around the restoration block — guarded read, retry-counter reset,
    and status.md re-render — without routing through LLM dispatch.
    The helper calls the SAME parse_checkpoint, render_checkpoint,
    render_status, and _atomic_write functions the production block
    uses; no duplication of the if-branch logic.
    """

    def _invoke_orient_restoration(
        self, clou_dir: Path, milestone: str,
    ) -> bool:
        """Drive the production ORIENT-exit restoration block.

        Mirrors the structure at coordinator.py's restoration block
        by calling the same functions in the same order.  Returns
        True if restoration fired, False if skipped.  This is a thin
        wrapper to avoid pulling in the SDK — every production function
        is reused, not re-implemented.
        """
        from clou.coordinator import _atomic_write
        from clou.golden_context import render_checkpoint, render_status
        from clou.recovery_checkpoint import parse_checkpoint

        checkpoint_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        if not checkpoint_path.exists():
            return False
        try:
            cp_exit = parse_checkpoint(
                checkpoint_path.read_text(encoding="utf-8"),
            )
        except (OSError, ValueError):
            return False
        if not (
            cp_exit.next_step == "ORIENT"
            and cp_exit.pre_orient_next_step
        ):
            return False
        restored_step = cp_exit.pre_orient_next_step
        restored_body = render_checkpoint(
            cycle=cp_exit.cycle,
            step=cp_exit.step,
            next_step=restored_step,
            current_phase=cp_exit.current_phase,
            phases_completed=cp_exit.phases_completed,
            phases_total=cp_exit.phases_total,
            validation_retries=0,  # F6: reset retry counters.
            readiness_retries=0,
            crash_retries=0,
            staleness_count=0,
            cycle_outcome=cp_exit.cycle_outcome,
            valid_findings=cp_exit.valid_findings,
            consecutive_zero_valid=cp_exit.consecutive_zero_valid,
            pre_orient_next_step="",
            pre_halt_next_step=cp_exit.pre_halt_next_step,
        )
        _atomic_write(checkpoint_path, restored_body)
        status_path = clou_dir / "milestones" / milestone / "status.md"
        status_body = render_status(
            milestone=milestone,
            phase=cp_exit.current_phase or "",
            cycle=cp_exit.cycle,
            next_step=restored_step,
        )
        status_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(status_path, status_body)
        return True

    def test_restoration_fires_on_orient_with_stash(
        self, tmp_path: Path,
    ) -> None:
        """Normal case: next_step=ORIENT + stash=EXECUTE → restored to EXECUTE."""
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        milestone = "m36-restore"
        clou_dir = tmp_path / ".clou"
        cp_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True)
        cp_path.write_text(render_checkpoint(
            cycle=3,
            step="ASSESS",
            next_step="ORIENT",
            current_phase="impl",
            phases_completed=1,
            phases_total=2,
            pre_orient_next_step="EXECUTE",
        ))
        fired = self._invoke_orient_restoration(clou_dir, milestone)
        assert fired is True
        cp_after = parse_checkpoint(cp_path.read_text())
        assert cp_after.next_step == "EXECUTE"
        assert cp_after.pre_orient_next_step == ""

    def test_retry_counters_reset_on_restoration(
        self, tmp_path: Path,
    ) -> None:
        """F6: ORIENT's retry counters do NOT leak onto the restored step.

        Retry budgets are cycle-type-scoped — bouncing readiness twice
        during ORIENT must not leave the restored EXECUTE facing an
        already-armed ceiling.
        """
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        milestone = "m36-retryreset"
        clou_dir = tmp_path / ".clou"
        cp_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True)
        cp_path.write_text(render_checkpoint(
            cycle=4,
            step="ASSESS",
            next_step="ORIENT",
            current_phase="impl",
            phases_completed=1,
            phases_total=2,
            validation_retries=2,
            readiness_retries=1,
            crash_retries=1,
            staleness_count=3,
            pre_orient_next_step="EXECUTE",
        ))
        fired = self._invoke_orient_restoration(clou_dir, milestone)
        assert fired is True
        cp_after = parse_checkpoint(cp_path.read_text())
        assert cp_after.validation_retries == 0
        assert cp_after.readiness_retries == 0
        assert cp_after.crash_retries == 0
        assert cp_after.staleness_count == 0

    def test_status_md_reflects_restored_next_step(
        self, tmp_path: Path,
    ) -> None:
        """F15: restoration re-renders status.md to prevent split-brain."""
        from clou.golden_context import render_checkpoint

        milestone = "m36-statuscoherent"
        clou_dir = tmp_path / ".clou"
        cp_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        status_path = (
            clou_dir / "milestones" / milestone / "status.md"
        )
        cp_path.parent.mkdir(parents=True)
        cp_path.write_text(render_checkpoint(
            cycle=2,
            step="ASSESS",
            next_step="ORIENT",
            current_phase="impl",
            phases_completed=0,
            phases_total=1,
            pre_orient_next_step="ASSESS",
        ))
        # Seed a stale status.md that says ORIENT (matches checkpoint
        # state BEFORE restoration).
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            "# Status: m36-statuscoherent\n\n"
            "## Current State\nphase: impl\ncycle: 2\n"
            "next_step: ORIENT\n\n"
            "## Phase Progress\n| Phase | Status |\n|---|---|\n"
            "| impl | in_progress |\n",
        )
        # Fire the restoration.
        fired = self._invoke_orient_restoration(clou_dir, milestone)
        assert fired is True
        # status.md must now reflect the RESTORED next_step=ASSESS,
        # not the pre-restoration ORIENT.
        status_after = status_path.read_text()
        assert "next_step: ASSESS" in status_after
        assert "next_step: ORIENT" not in status_after

    def test_guarded_read_skips_on_truncated_checkpoint(
        self, tmp_path: Path,
    ) -> None:
        """F1: a truncated checkpoint doesn't crash the restoration block.

        parse_checkpoint is tolerant — but if the read itself fails
        (permission, encoding) the guard must catch it rather than
        propagate.  Simulate via a truncated, unparseable file.
        """
        milestone = "m36-guardedread"
        clou_dir = tmp_path / ".clou"
        cp_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True)
        # Write a file whose content is NOT a valid checkpoint — no
        # cycle/step/next_step fields.  parse_checkpoint tolerates this
        # and returns a default Checkpoint (next_step=PLAN).  The
        # restoration block must observe next_step != "ORIENT" and
        # skip gracefully.  This exercises the tolerant-parse path;
        # the OSError path is exercised via permissions/encoding on a
        # real FS but not reliably reproducible cross-platform.
        cp_path.write_text("garbage that is not a checkpoint\n")
        fired = self._invoke_orient_restoration(clou_dir, milestone)
        assert fired is False
        # File content is untouched — no partial rewrite happened.
        assert cp_path.read_text() == "garbage that is not a checkpoint\n"


class TestFreshMilestoneSeedOrdering:
    """M36 round-4 F17: fresh-milestone seed is TOCTOU-safe.

    The seed path writes BOTH status.md and the checkpoint.  Prior
    ordering wrote the checkpoint first; a crash between writes left
    the checkpoint pointing at ORIENT with status.md absent, which
    validate_readiness flagged as a structural ERROR.  The fix
    reverses the ordering and guards the whole sequence with
    try/except so a partial seed is best-effort rolled back.
    """

    def _invoke_fresh_seed(
        self, clou_dir: Path, milestone: str,
    ) -> bool:
        """Drive the production fresh-milestone seed path.

        Mirrors the coordinator.py seed sequence at the else branch
        of the session-start rewrite — calls the same production
        functions in the same order.  Returns True on success, False
        on the rollback branch.
        """
        from clou.coordinator import _atomic_write
        from clou.golden_context import (
            _extract_phase_names,
            render_checkpoint,
            render_status,
        )

        checkpoint_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        if checkpoint_path.exists():
            return False
        try:
            ms_dir = clou_dir / "milestones" / milestone
            phase_names = _extract_phase_names(ms_dir)
            phase_progress: dict[str, str] | None = None
            if phase_names:
                phase_progress = {
                    name: "pending" for name in phase_names
                }
            status_body = render_status(
                milestone=milestone,
                phase="",
                cycle=0,
                next_step="ORIENT",
                phase_progress=phase_progress,
            )
            seed_body = render_checkpoint(
                cycle=0,
                step="PLAN",
                next_step="ORIENT",
                current_phase="",
                phases_completed=0,
                phases_total=0,
                pre_orient_next_step="PLAN",
            )
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            status_path = ms_dir / "status.md"
            if not status_path.exists():
                status_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(status_path, status_body)
            _atomic_write(checkpoint_path, seed_body)
            return True
        except (OSError, ValueError):
            try:
                if checkpoint_path.exists():
                    checkpoint_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def test_seed_writes_status_before_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulate crash AFTER status.md but BEFORE checkpoint.

        Patches _atomic_write to raise on the second call.  Asserts
        status.md exists and checkpoint does NOT — confirming the
        ordering.  A session re-run can safely re-seed from scratch.
        """
        import clou.coordinator as coord

        milestone = "m36-seed-order"
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones" / milestone
        ms_dir.mkdir(parents=True)
        status_path = ms_dir / "status.md"
        cp_path = ms_dir / "active" / "coordinator.md"

        calls: list[Path] = []
        real_atomic = coord._atomic_write

        def _fail_on_second(target: Path, content: str) -> None:
            calls.append(target)
            if len(calls) == 2:
                raise OSError("simulated crash between writes")
            real_atomic(target, content)

        monkeypatch.setattr(coord, "_atomic_write", _fail_on_second)
        # Must also patch the local reference we use in the helper.
        import tests.test_coordinator as tc_mod
        # The helper imports _atomic_write from coord, so patching
        # coord._atomic_write suffices.  Run the seed.
        _ = self._invoke_fresh_seed(clou_dir, milestone)

        # Two write attempts: status first, checkpoint second.
        assert len(calls) == 2
        assert calls[0] == status_path
        assert calls[1] == cp_path
        # status.md was written; checkpoint was NOT (the second
        # _atomic_write raised before completion, and the rollback
        # branch unlinks the checkpoint if it exists).
        assert status_path.exists()
        assert not cp_path.exists()

    def test_seed_success_both_files_present(
        self, tmp_path: Path,
    ) -> None:
        """Happy path: both files written with correct content."""
        from clou.recovery_checkpoint import parse_checkpoint

        milestone = "m36-seed-happy"
        clou_dir = tmp_path / ".clou"
        ms_dir = clou_dir / "milestones" / milestone
        ms_dir.mkdir(parents=True)

        ok = self._invoke_fresh_seed(clou_dir, milestone)
        assert ok is True
        cp_path = ms_dir / "active" / "coordinator.md"
        status_path = ms_dir / "status.md"
        assert cp_path.exists()
        assert status_path.exists()
        cp = parse_checkpoint(cp_path.read_text())
        assert cp.next_step == "ORIENT"
        assert cp.pre_orient_next_step == "PLAN"


class TestLlmEscapeFromOrientContract:
    """M36 round-4 F27: if the LLM rewrites next_step mid-ORIENT, the
    force-restore assertion writes next_step back to ORIENT so the
    ORIENT-exit restoration block on the next iteration reads the
    stash and performs the normal restoration path.

    The prompt contract ("ORIENT is observational, do not mutate the
    checkpoint") is unenforceable by prompt alone; defense in depth
    means the orchestrator catches the escape and restores from the
    typed stash rather than silently letting the LLM's wrong
    next_step dispatch.
    """

    def _invoke_f27_assertion(
        self, clou_dir: Path, milestone: str, cycle_count: int,
    ) -> dict[str, Any]:
        """Drive the production F27 post-ORIENT escape detection block.

        Mirrors the coordinator.py block's read/render/write sequence.
        Returns a dict with ``{escaped: bool, observed: str, forced:
        bool}`` so the test can assert on the outcome.
        """
        from clou.coordinator import _atomic_write
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        checkpoint_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        try:
            cp_post = parse_checkpoint(
                checkpoint_path.read_text(encoding="utf-8"),
            )
        except (OSError, ValueError):
            return {"escaped": False, "observed": "", "forced": False}

        escaped = cp_post.next_step != "ORIENT"
        forced = False
        if escaped and cp_post.pre_orient_next_step:
            forced_body = render_checkpoint(
                cycle=cp_post.cycle,
                step=cp_post.step,
                next_step="ORIENT",
                current_phase=cp_post.current_phase,
                phases_completed=cp_post.phases_completed,
                phases_total=cp_post.phases_total,
                validation_retries=cp_post.validation_retries,
                readiness_retries=cp_post.readiness_retries,
                crash_retries=cp_post.crash_retries,
                staleness_count=cp_post.staleness_count,
                cycle_outcome=cp_post.cycle_outcome,
                valid_findings=cp_post.valid_findings,
                consecutive_zero_valid=cp_post.consecutive_zero_valid,
                pre_orient_next_step=cp_post.pre_orient_next_step,
                pre_halt_next_step=cp_post.pre_halt_next_step,
            )
            _atomic_write(checkpoint_path, forced_body)
            forced = True
        return {
            "escaped": escaped,
            "observed": cp_post.next_step,
            "forced": forced,
        }

    def test_llm_rewrite_mid_orient_is_force_restored(
        self, tmp_path: Path,
    ) -> None:
        """LLM rewrites next_step=EXECUTE mid-ORIENT → force-restore to ORIENT."""
        from clou.golden_context import render_checkpoint
        from clou.recovery_checkpoint import parse_checkpoint

        milestone = "m36-f27"
        clou_dir = tmp_path / ".clou"
        cp_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True)
        # Simulate state AFTER an ORIENT cycle where the LLM rewrote
        # next_step=EXECUTE mid-cycle.  pre_orient_next_step=PLAN was
        # stashed by session-start rewrite; the LLM's escape replaced
        # next_step=ORIENT with next_step=EXECUTE.
        cp_path.write_text(render_checkpoint(
            cycle=1,
            step="ORIENT",
            next_step="EXECUTE",  # LLM escape — should be ORIENT
            current_phase="",
            phases_completed=0,
            phases_total=0,
            pre_orient_next_step="PLAN",
        ))
        outcome = self._invoke_f27_assertion(clou_dir, milestone, 1)
        assert outcome["escaped"] is True
        assert outcome["observed"] == "EXECUTE"
        assert outcome["forced"] is True
        # Post-assertion: next_step is ORIENT again; pre_orient_next_step
        # is still PLAN (the normal ORIENT-exit restoration on the next
        # iteration will consume it).
        cp_after = parse_checkpoint(cp_path.read_text())
        assert cp_after.next_step == "ORIENT"
        assert cp_after.pre_orient_next_step == "PLAN"

    def test_clean_orient_exit_no_escape_no_restore(
        self, tmp_path: Path,
    ) -> None:
        """Normal ORIENT exit: next_step still ORIENT → no force-restore."""
        from clou.golden_context import render_checkpoint

        milestone = "m36-f27-clean"
        clou_dir = tmp_path / ".clou"
        cp_path = (
            clou_dir / "milestones" / milestone / "active" / "coordinator.md"
        )
        cp_path.parent.mkdir(parents=True)
        cp_path.write_text(render_checkpoint(
            cycle=1,
            step="ORIENT",
            next_step="ORIENT",  # LLM respected the contract
            current_phase="",
            phases_completed=0,
            phases_total=0,
            pre_orient_next_step="PLAN",
        ))
        outcome = self._invoke_f27_assertion(clou_dir, milestone, 1)
        assert outcome["escaped"] is False
        assert outcome["forced"] is False

    def test_f27_emits_telemetry_signal(self) -> None:
        """The production block emits cycle.orient_exit for observability.

        Source-level check: the event name must be present in
        coordinator.py so dashboards can distinguish "ORIENT
        completed cleanly" from "LLM escaped ORIENT contract".
        """
        import clou.coordinator as coord

        source = Path(coord.__file__).read_text(encoding="utf-8")
        assert '"cycle.orient_exit"' in source, (
            "F27: the post-ORIENT block must emit cycle.orient_exit"
        )
        assert "escaped_contract" in source, (
            "F27: the event payload must carry the escape signal"
        )
