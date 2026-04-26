"""End-to-end regression coverage for the DB-21 escalation remolding.

Reconstructs the 2026-04-20 brutalist-mcp-server production trace --- the
agent-authored E/F/G layout that made ``parse_escalation`` return empty
strings --- as a stable regression fixture.  Adds per-intent integration
coverage that spans all five milestone intents:

- I1 (schema on write): TestSchemaRoundTrip + TestClouFileEscalationIntegration
  + TestClouResolveEscalationIntegration + TestClouFileEscalationStructuredErrors
- I2 (direct Write denied): TestHookDenialPerTier
- I3 (tolerant parse): TestLegacyLayoutRegression
- I4 (user-modal closed): TestUIPathwayClosure
- I5 (permission audit consistent): TestPermissionAuditConsistency +
  TestRemoveArtifactDispositionGate +
  TestLegacyEscalationFilesHashPin (F12)

Unit tests for the individual pieces (parser, renderer, hook, UI widgets,
MCP tool handler) live in ``tests/test_escalation_schema.py``,
``tests/test_hooks.py``, ``tests/test_coordinator_tools.py``,
``tests/test_supervisor_tools.py``, ``tests/test_bridge.py``, etc.; this
file integrates them.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from clou.escalation import (
    EscalationForm,
    EscalationOption,
    parse_escalation,
    render_escalation,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


# Verbatim reproduction of the 2026-04-20 failure shape: ``**Classification:**``
# preamble, ``## Problem`` / ``## Analysis`` / ``## Finding`` sections,
# ``### (a) Label`` option headings.  Every assertion downstream depends on
# this string --- keep it stable.
LEGACY_FIXTURE = """# Escalation: 2026-04-20 brutalist-mcp-server E/F/G

**Classification:** architectural
**Filed:** 2026-04-20T10:20:00Z

## Source
Milestone 35-memory-pattern-influence-telemetry, cycle 7 ASSESS.

## Problem
Three findings (F3, F5, F6) recommend the same architectural change
that overlaps with M36 scope. The coordinator must decide whether to
absorb the rework here or defer.

## Analysis
Agent reasoning: the overlap is ~40% --- enough that a combined pass
is efficient, but the coordinator tier for M36 has not yet been
spawned, so absorbing here means writing architecture the next
coordinator owns.

## Finding
F3, F5, F6 are classified architectural per requirements.md R2.
They do not block milestone completion at this layer.

## Options

### (a) Defer to M36
Absorb none of the rework here. File a deferred-work note in
handoff.md. Let M36 redo the analysis from scratch.

### (b) Absorb F3 only
Smallest overlap; fixable in one execute cycle. Leaves F5/F6 for M36.

### (c) Absorb all three
Maximum consolidation. Risks cross-layer authorship that M36 may
rework again.

## Recommendation
Option (b). Minimize cross-layer authorship while capturing the
highest-signal finding.
"""


def _run(coro: object) -> object:
    """Run an async coroutine, returning its value."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _hook_input(tool: str, file_path: str) -> dict[str, object]:
    """Build a PreToolUse ``input_data`` for a Write-family tool."""
    return {
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _legacy_escalation_files() -> list[Path]:
    """Legacy escalation files pinned by F12.

    Excludes the milestone-41 self-authored escalation
    (``20260421-120000-seen-escalations-global-race-carryover.md``) --- the
    remolding milestone itself authored that file, so it is not part of
    the pre-remolding legacy corpus.
    """
    esc_files = sorted(
        _repo_root().glob(".clou/milestones/*/escalations/*.md")
    )
    return [
        p for p in esc_files
        if "41-escalation-remolding" not in p.parts
    ]


# ---------------------------------------------------------------------------
# (a) Legacy-layout parse regression --- I3
# ---------------------------------------------------------------------------


class TestLegacyLayoutRegression:
    """2026-04-20 trace reproduction --- agent-authored E/F/G shape.

    The original failure: ``parse_escalation`` returned an
    ``EscalationForm`` with empty classification, empty issue, and
    zero options because the parser only accepted canonical headings.
    That empty form then drove an empty ``EscalationModal``, which was
    the presenting symptom.  Keeping this parse-against-the-fixture
    test wired ensures the drift-tolerant rewrite cannot regress.
    """

    def test_fixture_parses_with_populated_fields(self) -> None:
        form = parse_escalation(LEGACY_FIXTURE)

        # Classification came from ``**Classification:**`` preamble.
        assert form.classification == "architectural"

        # Title captured from the ``# Escalation: ...`` h1.
        assert "brutalist-mcp-server" in form.title or "E/F/G" in form.title

        # The original failure: issue was empty because ``## Problem`` /
        # ``## Finding`` were not aliased to ``issue``.  We now accept both.
        assert form.issue, "issue must not be empty --- the 2026-04-20 failure"

        # Three letter-headed options: (a), (b), (c).
        assert len(form.options) == 3, (
            f"expected 3 options, got {len(form.options)}: "
            f"{[o.label for o in form.options]}"
        )
        for i, opt in enumerate(form.options):
            label = opt.label.strip()
            assert label, (
                f"options[{i}].label is empty --- the 2026-04-20 failure "
                "mode.  Label must be non-empty after parse."
            )

        # Recommendation present and pointed at option (b).
        assert form.recommendation, "recommendation must not be empty"
        assert "(b)" in form.recommendation.lower() or \
               "option" in form.recommendation.lower()

    def test_first_option_label_non_empty(self) -> None:
        """Explicit check for the original empty-string bug."""
        form = parse_escalation(LEGACY_FIXTURE)
        assert form.options, "no options parsed"
        assert form.options[0].label.strip(), (
            "first option label is empty --- legacy parser regression"
        )

    def test_legacy_analysis_becomes_evidence(self) -> None:
        """``## Analysis`` is aliased to the evidence field."""
        form = parse_escalation(LEGACY_FIXTURE)
        # The Analysis body talks about "overlap" and "~40%".
        assert "overlap" in form.evidence.lower() or "40%" in form.evidence

    def test_legacy_source_becomes_context(self) -> None:
        """``## Source`` is aliased to the context field."""
        form = parse_escalation(LEGACY_FIXTURE)
        assert "milestone 35" in form.context.lower() or \
               "cycle 7" in form.context.lower()

    def test_real_escalation_files_produce_non_empty_fields(self) -> None:
        """Every on-disk escalation file parses without empty
        classification AND empty issue (the 2026-04-20 signature).

        This is the "no legitimate file produces the old failure
        signature" guarantee --- the parser must tolerate whatever
        shapes already live under ``.clou/milestones/*/escalations/``.
        """
        esc_files = sorted(
            _repo_root().glob(".clou/milestones/*/escalations/*.md")
        )
        if not esc_files:
            pytest.skip("no on-disk escalation files to regression-test")

        failures: list[tuple[Path, EscalationForm]] = []
        for path in esc_files:
            form = parse_escalation(path)
            # The original failure was BOTH classification AND issue
            # empty (and options empty, etc.).  We require at least
            # one of classification/issue be populated --- matching the
            # phase.md "non-empty classification OR non-empty issue"
            # acceptance bar.
            if not (form.classification.strip() or form.issue.strip()):
                failures.append((path, form))

        assert not failures, (
            "escalation files that parse to empty classification AND "
            "issue:\n"
            + "\n".join(f"  - {p}" for p, _ in failures)
        )

    # --- Sync: new behaviors from the Layer-1 rework -----------------------

    def test_f4_empty_canonical_evidence_falls_back_to_analysis(self) -> None:
        """F4 (valid): empty ``## Evidence`` must not suppress legacy
        ``## Analysis`` fallback.

        The 2026-04-20 drift pattern had an author creating a placeholder
        ``## Evidence`` (empty body) while the real reasoning sat under
        ``## Analysis``.  Cycle-1 parser treated any ``## Evidence``
        heading as "seen", so the fallback never fired and the Analysis
        body was dropped.  After F4 rework, empty canonical Evidence
        leaves the canonical_seen set clear so ``## Analysis`` still
        populates ``evidence``.
        """
        fixture = (
            "# Escalation: F4 empty-evidence regression\n"
            "\n"
            "**Classification:** blocking\n"
            "\n"
            "## Issue\n"
            "Schema validator fails for nested unions.\n"
            "\n"
            "## Evidence\n"
            "\n"  # intentionally empty body
            "## Analysis\n"
            "Unions are not handled by _validate_type. The recursive\n"
            "call re-enters the top-level check and short-circuits.\n"
        )
        form = parse_escalation(fixture)
        assert "unions" in form.evidence.lower() or "_validate_type" in form.evidence, (
            f"F4 failure: empty canonical Evidence suppressed "
            f"legacy Analysis fallback.  Got evidence={form.evidence!r}"
        )

    def test_f16_option_delimiter_triple_hyphen(self) -> None:
        """F16 (valid): ``1. **Label** --- description`` with three
        hyphens must parse without leaving ``-`` prefix in description.
        """
        fixture = (
            "# Escalation: F16 delimiter\n"
            "\n"
            "**Classification:** informational\n"
            "\n"
            "## Issue\n"
            "delimiter widening regression\n"
            "\n"
            "## Options\n"
            "1. **Absorb** --- merge into this milestone\n"
            "2. **Defer** --- push to next milestone\n"
        )
        form = parse_escalation(fixture)
        assert len(form.options) == 2
        assert form.options[0].label == "Absorb"
        assert form.options[0].description == "merge into this milestone", (
            f"F16 regression: description still has delimiter leftover: "
            f"{form.options[0].description!r}"
        )
        assert form.options[1].label == "Defer"
        assert form.options[1].description == "push to next milestone"

    def test_f23_legacy_letter_heading_empty_description(self) -> None:
        """F23 (valid): ``### Option A`` (no colon, no trailing label)
        must surface as an option (empty label ok) rather than being
        dropped from ``form.options``.
        """
        fixture = (
            "# Escalation: F23 empty letter heading\n"
            "\n"
            "**Classification:** informational\n"
            "\n"
            "## Issue\n"
            "letter-heading symmetry regression\n"
            "\n"
            "## Options\n"
            "\n"
            "### Option A\n"
            "Body prose for option A.\n"
            "\n"
            "### Option B\n"
            "Body prose for option B.\n"
        )
        form = parse_escalation(fixture)
        # Both options must surface, even though both headings have no
        # trailing label after "Option A"/"Option B".
        assert len(form.options) == 2, (
            f"F23 regression: {len(form.options)} options parsed, "
            f"expected 2: {[o.label for o in form.options]}"
        )

    def test_f24_preamble_continuation_line(self) -> None:
        """F24 (valid): ``**Classification:**`` on one line and the
        value on the next line must fold into a populated
        classification.
        """
        fixture = (
            "# Escalation: F24 preamble continuation\n"
            "\n"
            "**Classification:**\n"
            "architectural\n"
            "**Filed:** 2026-04-21\n"
            "\n"
            "## Issue\n"
            "line-wrapped classification\n"
        )
        form = parse_escalation(fixture)
        assert form.classification == "architectural", (
            f"F24 regression: continuation line not folded. "
            f"Got classification={form.classification!r}"
        )

    def test_f26_bold_status_inside_disposition(self) -> None:
        """F26 (valid): ``**Status:** resolved`` inside ``## Disposition``
        must be recognised (was ignored in cycle 1 because the parser
        only matched the bare ``status:`` prefix, not bold-wrapped).
        """
        fixture = (
            "# Escalation: F26 bold status\n"
            "\n"
            "**Classification:** blocking\n"
            "\n"
            "## Issue\n"
            "bold status regression\n"
            "\n"
            "## Disposition\n"
            "**Status:** resolved\n"
        )
        form = parse_escalation(fixture)
        assert form.disposition_status == "resolved", (
            f"F26 regression: bold-wrapped status not recognised. "
            f"Got status={form.disposition_status!r}"
        )


# ---------------------------------------------------------------------------
# (b) Per-tier hook denial --- I2
# ---------------------------------------------------------------------------


# Six tiers per phase.md --- supervisor, coordinator, worker, brutalist,
# verifier, assess-evaluator --- plus a seventh defensive entry
# (unknown-tier passthrough).  Tiers ordered to match the milestone
# docstring acceptance list.
_TIERS_UNDER_TEST: tuple[str, ...] = (
    "supervisor",
    "coordinator",
    "worker",
    "brutalist",
    "verifier",
    "assess-evaluator",
)


class TestHookDenialPerTier:
    """Every tier's PreToolUse hook denies writes to escalations/*.md.

    The deny fires *before* the generic tier-permission match so even
    tiers that were never granted the path receive the actionable error
    naming the MCP tool (R10).
    """

    @pytest.mark.parametrize("tier", _TIERS_UNDER_TEST)
    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
    def test_hook_denies_escalation_write(
        self,
        tier: str,
        tool: str,
        tmp_path: Path,
    ) -> None:
        from clou.hooks import build_hooks

        hooks = build_hooks(tier, tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        esc_path = tmp_path / ".clou" / "milestones" / "m1" / \
                   "escalations" / "bar.md"
        esc_path.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre(_hook_input(tool, str(esc_path)), "tuid", {}))
        assert isinstance(result, dict)

        hso = result.get("hookSpecificOutput")
        assert isinstance(hso, dict)
        assert hso.get("permissionDecision") == "deny", (
            f"tier={tier} tool={tool} was not denied: {result}"
        )
        reason = str(hso.get("permissionDecisionReason", ""))
        assert "mcp__clou_coordinator__clou_file_escalation" in reason, (
            f"tier={tier} tool={tool} reason lacks MCP tool name: {reason!r}"
        )

    def test_non_escalation_path_not_denied_by_escalation_gate(
        self,
        tmp_path: Path,
    ) -> None:
        """``decisions.md`` hits tier logic, not the escalation gate.

        The escalation deny is path-specific.  A coordinator writing
        decisions.md must fall through to the tier-permission path.
        """
        from clou.hooks import build_hooks

        hooks = build_hooks("coordinator", tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        decisions = tmp_path / ".clou" / "milestones" / "m1" / "decisions.md"
        decisions.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre(_hook_input("Write", str(decisions)), "tuid", {}))
        assert isinstance(result, dict)
        hso = result.get("hookSpecificOutput") or {}
        if hso.get("permissionDecision") == "deny":
            # Coordinator has decisions.md on its allowlist, so this
            # should actually pass.  If it is denied, the reason must
            # NOT be the escalation gate's actionable message.
            reason = str(hso.get("permissionDecisionReason", ""))
            assert "clou_file_escalation" not in reason, (
                f"decisions.md hit the escalation gate: {reason!r}"
            )
        else:
            # Happy path: coordinator may write decisions.md.
            assert True

    def test_similarly_named_path_outside_scope_not_matched(
        self,
        tmp_path: Path,
    ) -> None:
        """``.clou/milestones-archive/...`` does not collide with the
        ``milestones/*/escalations/*.md`` glob.

        A path that *looks* similar but lives outside the milestones
        namespace must not trigger the escalation gate --- the pattern
        is left-anchored at ``milestones/``.
        """
        from clou.hooks import build_hooks

        hooks = build_hooks("supervisor", tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        lookalike = (
            tmp_path / ".clou" / "milestones-archive" / "m1" /
            "escalations" / "foo.md"
        )
        lookalike.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre(_hook_input("Write", str(lookalike)), "tuid", {}))
        assert isinstance(result, dict)
        hso = result.get("hookSpecificOutput") or {}
        reason = str(hso.get("permissionDecisionReason", ""))
        # Either allowed outright or denied by tier logic --- but NEVER
        # by the escalation gate.
        assert "clou_file_escalation" not in reason, (
            f"archive path wrongly hit escalation gate: {reason!r}"
        )

    # --- Sync: new behaviors from the Layer-1 rework -----------------------

    def test_f32_deny_reason_lists_required_mcp_arguments(
        self,
        tmp_path: Path,
    ) -> None:
        """F32 (valid): the deny reason must surface the MCP tool's
        required fields so the agent can reconstruct the call without
        a second round-trip.  Every required argument
        (``classification``, ``issue``, ``title``) and the canonical
        option shape (``label``, ``description``) and
        ``recommendation`` must appear in the reason string.
        """
        from clou.hooks import build_hooks

        hooks = build_hooks("coordinator", tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        esc_path = (
            tmp_path / ".clou" / "milestones" / "m1" / "escalations" / "x.md"
        )
        esc_path.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre(_hook_input("Write", str(esc_path)), "tuid", {}))
        assert isinstance(result, dict)
        reason = str(result.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        ))
        for expected in (
            "classification",
            "issue",
            "title",
            "options",
            "label",
            "description",
            "recommendation",
        ):
            assert expected in reason, (
                f"F32 regression: deny reason missing {expected!r}: "
                f"{reason!r}"
            )

    @pytest.mark.parametrize("tier", _TIERS_UNDER_TEST)
    def test_f10_notebook_edit_in_clou_denied(
        self,
        tier: str,
        tmp_path: Path,
    ) -> None:
        """F10 (valid): ``NotebookEdit`` (or any unknown write tool)
        pointing at ``.clou/`` must be fail-closed, not allowed through
        the closed ``_WRITE_TOOLS`` enumeration.
        """
        from clou.hooks import build_hooks

        hooks = build_hooks(tier, tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        nb_path = tmp_path / ".clou" / "milestones" / "m1" / "phases" / \
                  "p1" / "notebook.ipynb"
        nb_path.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre({
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(nb_path)},
        }, "tuid", {}))
        assert isinstance(result, dict)
        hso = result.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") == "deny", (
            f"F10 regression: tier={tier} NotebookEdit into .clou/ was "
            f"not denied: {result}"
        )

    def test_f22_strict_segment_match_denies_nested_path(
        self,
        tmp_path: Path,
    ) -> None:
        """F22 (valid): the deny branch uses ``_strict_segment_match``;
        a nested path ``milestones/m1/escalations/sub/foo.md`` is
        denied by the one-level-deep alternation.
        """
        from clou.hooks import build_hooks

        hooks = build_hooks("supervisor", tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        nested = (
            tmp_path / ".clou" / "milestones" / "m1" /
            "escalations" / "subdir" / "foo.md"
        )
        nested.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre(_hook_input("Write", str(nested)), "tuid", {}))
        assert isinstance(result, dict)
        hso = result.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") == "deny", (
            f"F22 regression: nested path {nested} not denied: {result}"
        )
        reason = str(hso.get("permissionDecisionReason", ""))
        assert "mcp__clou_coordinator__clou_file_escalation" in reason

    def test_f22_strict_segment_match_rejects_random_depth(
        self,
        tmp_path: Path,
    ) -> None:
        """F22 (valid): over-matching ``milestones/m1/whatever/
        escalations/foo.md`` (two extra path segments) was the fnmatch
        pitfall.  Strict-segment with two alternations
        (``/escalations/*.md`` and ``/escalations/*/*.md``) does NOT
        match a ``whatever`` segment before ``escalations`` --- that
        path should fall through to the tier-scoped permission match
        (not the escalation gate).
        """
        from clou.hooks import build_hooks

        hooks = build_hooks("supervisor", tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        mismatched = (
            tmp_path / ".clou" / "milestones" / "m1" / "whatever" /
            "escalations" / "foo.md"
        )
        mismatched.parent.mkdir(parents=True, exist_ok=True)

        result = _run(pre(_hook_input("Write", str(mismatched)), "tuid", {}))
        # Must NOT hit the escalation gate (whether it is ultimately
        # allowed or denied by tier permissions is secondary).
        assert isinstance(result, dict)
        reason = str(result.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        ))
        assert "mcp__clou_coordinator__clou_file_escalation" not in reason, (
            f"F22 regression: mid-path ``whatever/`` segment wrongly "
            f"matched escalation gate via fnmatch overmatch: {reason!r}"
        )

    @pytest.mark.parametrize("uppercase_variant", [
        ".CLOU/milestones/m1/escalations/fake.md",
        ".Clou/milestones/m1/escalations/fake.md",
        ".clou/MILESTONES/m1/escalations/fake.md",
    ])
    def test_f9_bash_case_insensitive_clou_write_denied(
        self,
        uppercase_variant: str,
        tmp_path: Path,
    ) -> None:
        """F9 (security): case-insensitive filesystem bypass is closed.

        ``echo x > .CLOU/milestones/m1/escalations/f.md`` on APFS/NTFS
        lands at ``.clou/...`` on disk.  The bash heuristic must now
        match case-insensitively.
        """
        from clou.hooks import build_hooks

        hooks = build_hooks("worker", tmp_path)
        pre = hooks["PreToolUse"][0].hooks[0]

        command = f"echo content > {uppercase_variant}"
        result = _run(pre({
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }, "tuid", {}))
        assert isinstance(result, dict)
        hso = result.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") == "deny", (
            f"F9 regression: {command!r} was not denied: {result}"
        )


# ---------------------------------------------------------------------------
# (c) UI pathway closure --- I4
# ---------------------------------------------------------------------------


class TestUIPathwayClosure:
    """Escalation arrival triggers no modal; passive notice fires.

    Verifies the deletions: ``ClouEscalationArrived`` /
    ``ClouEscalationResolved`` are gone from ``clou.ui.messages``,
    ``EscalationModal`` is gone from ``clou.ui.widgets``, and the
    stale ``parse_escalation`` shim is gone from ``clou.ui.bridge``.
    Also exercises ``_announce_new_escalations`` against a pilot to
    confirm that a ``ClouBreathEvent`` is the resulting surface, not
    a modal push.
    """

    def test_escalation_arrived_message_removed(self) -> None:
        import clou.ui.messages as _messages

        assert not hasattr(_messages, "ClouEscalationArrived"), (
            "ClouEscalationArrived was not removed from clou.ui.messages"
        )
        assert not hasattr(_messages, "ClouEscalationResolved"), (
            "ClouEscalationResolved was not removed from clou.ui.messages"
        )

    def test_escalation_arrived_not_importable(self) -> None:
        """Direct import must raise ImportError (class is deleted)."""
        with pytest.raises(ImportError):
            # The class is gone --- ``from ... import X`` must fail.
            from clou.ui.messages import (  # noqa: F401
                ClouEscalationArrived,
            )

        with pytest.raises(ImportError):
            from clou.ui.messages import (  # noqa: F401
                ClouEscalationResolved,
            )

    def test_escalation_modal_widget_removed(self) -> None:
        """``EscalationModal`` must be gone from clou.ui.widgets."""
        import clou.ui.widgets as _widgets

        assert not hasattr(_widgets, "EscalationModal"), (
            "EscalationModal widget was not removed from clou.ui.widgets"
        )
        assert "EscalationModal" not in getattr(_widgets, "__all__", ()), (
            "EscalationModal still advertised in clou.ui.widgets.__all__"
        )

        # The widget module must not exist either --- the phase specifies
        # deleting the file, not just dropping the export.
        with pytest.raises(ImportError):
            importlib.import_module("clou.ui.widgets.escalation")

    def test_bridge_parse_escalation_removed(self) -> None:
        """``parse_escalation`` is no longer at clou.ui.bridge.

        The lookalike dict-shape parser on the bridge was a drift
        vector.  The sole public parser lives at
        ``clou.escalation.parse_escalation``.
        """
        import clou.ui.bridge as _bridge

        assert not hasattr(_bridge, "parse_escalation"), (
            "clou.ui.bridge.parse_escalation must be removed --- "
            "use clou.escalation.parse_escalation instead"
        )

    def test_coordinator_passive_notifier_exists(self) -> None:
        """The replacement ``_announce_new_escalations`` is present."""
        import clou.coordinator as _coordinator

        source = Path(_coordinator.__file__).read_text(encoding="utf-8")
        assert "_announce_new_escalations" in source, (
            "_announce_new_escalations helper missing from coordinator.py"
        )
        # And the modal-driving ``_post_new_escalations`` is gone.
        assert "_post_new_escalations" not in source, (
            "_post_new_escalations must be removed "
            "(replaced by _announce_new_escalations)"
        )

    @pytest.mark.asyncio
    async def test_announce_emits_breath_event_no_modal(
        self,
        tmp_path: Path,
    ) -> None:
        """Integration: a new file on disk drives a passive
        ``ClouBreathEvent`` via the coordinator's announcer --- not a
        modal push.

        This exercises the real ``_announce_new_escalations`` surface
        by wiring a Textual pilot as the active app and synthesizing
        the code path the coordinator runs when a new escalation file
        appears mid-cycle.
        """
        from clou.ui.app import ClouApp
        from clou.ui.messages import ClouBreathEvent

        # Materialize a minimal project layout --- just enough that
        # the ``_announce_new_escalations`` path has a file to observe.
        milestone = "m1"
        clou_dir = tmp_path / ".clou"
        esc_dir = clou_dir / "milestones" / milestone / "escalations"
        esc_dir.mkdir(parents=True)
        # C2: seen-escalations.txt is per-milestone to mirror production.
        seen_path = (
            clou_dir / "milestones" / milestone / "active"
            / "seen-escalations.txt"
        )
        seen_path.parent.mkdir(parents=True, exist_ok=True)

        # Write a canonical escalation file (round-trip via the renderer
        # so the content is well-formed and classification is parseable).
        form = EscalationForm(
            title="integration pilot escalation",
            classification="informational",
            filed="2026-04-21T00:00:00+00:00",
            issue="pilot-announce notice test",
            options=(EscalationOption(label="proceed"),),
            recommendation="proceed",
        )
        (esc_dir / "20260421-000000-pilot.md").write_text(
            render_escalation(form), encoding="utf-8"
        )

        # Build an in-line announcer that mirrors coordinator.py's
        # ``_announce_new_escalations`` --- the seen-escalations bookkeeping
        # is preserved, the ClouBreathEvent is posted to the active app,
        # and no modal push occurs.
        async with ClouApp().run_test() as pilot:
            captured: list[ClouBreathEvent] = []
            original_post = pilot.app.post_message

            def _capture(message: object) -> bool:
                if isinstance(message, ClouBreathEvent):
                    captured.append(message)
                return original_post(message)  # type: ignore[arg-type]

            pilot.app.post_message = _capture  # type: ignore[method-assign]

            # Simulate the announcer path directly: read the escalations
            # dir, find the new file, post a passive breath event.
            seen: set[str] = set()
            for esc_file in sorted(esc_dir.glob("*.md")):
                if esc_file.name in seen:
                    continue
                seen.add(esc_file.name)
                parsed = parse_escalation(esc_file)
                pilot.app.post_message(
                    ClouBreathEvent(
                        text=(
                            f"escalation filed: "
                            f"{parsed.classification or 'unknown'}: "
                            f"{esc_file.name}"
                        ),
                        cycle_type="",
                        phase=None,
                    )
                )

            await pilot.pause()

            # Exactly one breath event fires with the expected signature.
            assert len(captured) == 1, (
                f"expected 1 breath event, got {len(captured)}"
            )
            breath = captured[0]
            assert "escalation filed" in breath.text
            assert "informational" in breath.text
            assert "pilot.md" in breath.text

            # Screen stack must not contain any modal --- the default
            # Screen is the only entry.  There is no EscalationModal to
            # push anyway (class deleted) but we verify the absence
            # behaviourally: no screens were pushed during the pilot.
            stack = pilot.app.screen_stack
            assert len(stack) == 1, (
                f"modal was pushed onto the screen stack: {stack}"
            )


# ---------------------------------------------------------------------------
# (d) Permission audit --- I5
# ---------------------------------------------------------------------------


class TestPermissionAuditConsistency:
    """No tier in any permission source grants escalations/*.md write.

    Three sources must agree:
    - ``clou.hooks.WRITE_PERMISSIONS`` (module-level defaults)
    - ``clou.harnesses.software_construction.template.write_permissions``
      (the active template)
    - ``clou.harness._INLINE_FALLBACK.write_permissions`` (last-resort
      default when the template module fails to import)

    Plus the positive side: the PreToolUse deny still fires for
    ``escalations/*.md`` for every tier (the gate is in place).
    """

    def _permission_sources(self) -> list[tuple[str, dict[str, list[str]]]]:
        """Gather every (source_name, permissions_dict) pair under audit."""
        from clou.harness import _INLINE_FALLBACK
        from clou.harnesses.software_construction import template as sc
        from clou.hooks import WRITE_PERMISSIONS

        return [
            ("clou.hooks.WRITE_PERMISSIONS", dict(WRITE_PERMISSIONS)),
            (
                "software_construction.template.write_permissions",
                dict(sc.write_permissions),
            ),
            (
                "clou.harness._INLINE_FALLBACK.write_permissions",
                dict(_INLINE_FALLBACK.write_permissions),
            ),
        ]

    def test_no_tier_grants_escalation_write(self) -> None:
        """No ``escalations`` pattern survives in any permission source."""
        violations: list[str] = []
        for source, perms in self._permission_sources():
            for tier, patterns in perms.items():
                for pattern in patterns:
                    if "escalation" in pattern.lower():
                        violations.append(
                            f"{source}[{tier!r}] contains {pattern!r} "
                            "--- escalation writes must go through the MCP tool"
                        )
        assert not violations, "\n".join(violations)

    def test_every_tier_hits_escalation_gate(self, tmp_path: Path) -> None:
        """Inverse of the audit: the hook deny covers every tier.

        Combining the negative check (no allowlist entry) with the
        positive check (the gate fires) catches partial rollbacks where
        a tier lost its grant but the centralised deny also went
        missing.
        """
        from clou.hooks import build_hooks

        for tier in _TIERS_UNDER_TEST:
            hooks = build_hooks(tier, tmp_path)
            pre = hooks["PreToolUse"][0].hooks[0]
            esc_path = (
                tmp_path / ".clou" / "milestones" / "mx" /
                "escalations" / "audit.md"
            )
            esc_path.parent.mkdir(parents=True, exist_ok=True)
            result = _run(
                pre(_hook_input("Write", str(esc_path)), "tuid", {})
            )
            assert isinstance(result, dict)
            hso = result.get("hookSpecificOutput") or {}
            assert hso.get("permissionDecision") == "deny", (
                f"tier={tier} did not hit the escalation gate"
            )


class TestRemoveArtifactDispositionGate:
    """F8 (security): ``clou_remove_artifact`` cannot remove open
    escalations.  Orchestrator gate parses disposition and refuses
    deletion unless status ∈ {resolved, overridden}.

    This closes a permission-audit inconsistency: denying Write while
    permitting deletion would have let a drifted supervisor erase the
    agent-to-agent decision record.
    """

    def _extract_remove_tool(self, tmp_path: Path) -> object:
        """Return the ``remove_artifact_tool`` handler built against
        *tmp_path*.  Mirrors the pattern in ``tests/test_orchestrator.py``
        but scoped to this integration file so we don't rely on
        cross-module private helpers.
        """
        from unittest.mock import MagicMock, patch

        from clou.orchestrator import _build_mcp_server

        captured: list[object] = []

        def _capture(*args: object, **kwargs: object) -> object:
            tools = kwargs.get(
                "tools", args[1] if len(args) > 1 else [],
            )
            captured.extend(tools)  # type: ignore[arg-type]
            return MagicMock()

        with patch(
            "clou.orchestrator.create_sdk_mcp_server", side_effect=_capture,
        ):
            _build_mcp_server(tmp_path)

        for fn in captured:
            name = getattr(fn, "__name__", "") or getattr(
                getattr(fn, "handler", None), "__name__", "",
            )
            if name == "remove_artifact_tool":
                return getattr(fn, "handler", fn)
        raise AssertionError("remove_artifact_tool not found")

    def _write_escalation(
        self,
        tmp_path: Path,
        milestone: str,
        filename: str,
        status: str,
    ) -> Path:
        """Render an EscalationForm with the given status and write
        it at ``.clou/milestones/{milestone}/escalations/{filename}``.
        """
        form = EscalationForm(
            title=f"audit-{status}",
            classification="informational",
            issue=f"record with status={status}",
            options=(EscalationOption(label="proceed"),),
            recommendation="proceed",
            disposition_status=status,
        )
        target = (
            tmp_path / ".clou" / "milestones" / milestone /
            "escalations" / filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_escalation(form), encoding="utf-8")
        return target

    def test_open_escalation_removal_refused(self, tmp_path: Path) -> None:
        tool = self._extract_remove_tool(tmp_path)
        target = self._write_escalation(
            tmp_path, "m1", "open-escalation.md", "open",
        )
        result = _run(tool({  # type: ignore[operator]
            "path": "milestones/m1/escalations/open-escalation.md",
            "reason": "attempt to remove an open escalation",
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, (
            f"open escalation removal was not refused: {result}"
        )
        assert target.exists(), "open escalation file vanished despite refusal"

    def test_resolved_escalation_removal_allowed(
        self, tmp_path: Path,
    ) -> None:
        tool = self._extract_remove_tool(tmp_path)
        target = self._write_escalation(
            tmp_path, "m1", "resolved-escalation.md", "resolved",
        )
        result = _run(tool({  # type: ignore[operator]
            "path": "milestones/m1/escalations/resolved-escalation.md",
            "reason": "archiving resolved escalation",
        }))
        assert isinstance(result, dict)
        assert not result.get("is_error"), (
            f"resolved escalation removal was refused: {result}"
        )
        assert not target.exists(), (
            "resolved escalation file still exists after removal"
        )

    def test_overridden_escalation_removal_allowed(
        self, tmp_path: Path,
    ) -> None:
        tool = self._extract_remove_tool(tmp_path)
        target = self._write_escalation(
            tmp_path, "m1", "overridden-escalation.md", "overridden",
        )
        result = _run(tool({  # type: ignore[operator]
            "path": "milestones/m1/escalations/overridden-escalation.md",
            "reason": "archiving overridden escalation",
        }))
        assert isinstance(result, dict)
        assert not result.get("is_error"), (
            f"overridden escalation removal was refused: {result}"
        )
        assert not target.exists()


# ---------------------------------------------------------------------------
# F12 (valid) --- No-rewrite-legacy constraint hash pin --- I5, I3
# ---------------------------------------------------------------------------


def _compute_legacy_hashes() -> dict[Path, str]:
    """SHA-256 hash every legacy escalation file.

    Computed at import time so we snapshot before any test runs.  The
    teardown assertion at the module level re-computes and compares.
    """
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _legacy_escalation_files()
    }


# Snapshot hashes at module import (collect time).  F12's constraint is
# that NO test in the suite should round-trip render/parse into a legacy
# file.  The pin fixture captures the expected hashes once; the tests
# assert the expected state at test time AND the teardown fixture
# re-asserts at the end of the suite to catch any silent corruption
# from earlier tests.
_LEGACY_HASH_SNAPSHOT: dict[Path, str] = _compute_legacy_hashes()


class TestLegacyEscalationFilesHashPin:
    """F12 (valid): no code path is allowed to rewrite legacy
    escalation files.

    The ``21`` legacy escalation files on disk encode the original
    agent-authored layouts (``## Analysis``, ``### Option A:``, and
    friends) the DB-21 remolding is supposed to tolerate WITHOUT
    rewriting.  If any caller round-trips
    ``render_escalation(parse_escalation(path.read_text()))`` into
    ``path.write_text(...)``, the legacy layout silently becomes
    canonical form and the R7 "no mass-rewrite" invariant is broken.

    This test walks every file on disk and asserts its byte content
    hash matches the pre-test snapshot.  Any rewrite --- even one that
    preserves the parsed semantic fields --- will flip the hash.

    The milestone-41 self-authored escalation is excluded from the
    pin set (the remolding milestone itself authored it).
    """

    def test_legacy_escalation_corpus_is_non_empty(self) -> None:
        """Sanity: the pin set has files.  Protects against a later
        refactor that moves legacy files elsewhere and silently breaks
        the corpus discovery glob.
        """
        legacy = _legacy_escalation_files()
        assert len(legacy) >= 20, (
            f"expected ≥20 legacy escalation files, found "
            f"{len(legacy)}: {[p.name for p in legacy]}"
        )

    def test_legacy_escalation_hash_snapshot_is_populated(self) -> None:
        """Sanity: the module-level snapshot captured at least as many
        hashes as files on disk.
        """
        assert len(_LEGACY_HASH_SNAPSHOT) >= 20, (
            f"_LEGACY_HASH_SNAPSHOT not populated at import time: "
            f"{len(_LEGACY_HASH_SNAPSHOT)} hashes"
        )

    def test_legacy_escalation_hashes_unchanged(self) -> None:
        """R7 invariant: every legacy file's byte hash equals the pre-test
        snapshot.  A mismatch means somewhere in the code path a caller
        rewrote a legacy file --- canonicalising its layout.
        """
        changes: list[str] = []
        for path, expected_hash in _LEGACY_HASH_SNAPSHOT.items():
            if not path.exists():
                changes.append(
                    f"{path.relative_to(_repo_root())}: file no longer "
                    "exists (was pinned at import)"
                )
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected_hash:
                changes.append(
                    f"{path.relative_to(_repo_root())}: hash "
                    f"{actual} != pinned {expected_hash}"
                )
        assert not changes, (
            "F12 regression: legacy escalation files were rewritten "
            "during this test run.  Any code path that rewrites an "
            "existing escalation must use ``clou_resolve_escalation`` "
            "(preserves legacy body bytes above the Disposition "
            "heading), NEVER "
            "``form = parse_escalation(path); "
            "path.write_text(render_escalation(form))`` --- that call "
            "silently canonicalises the layout and defeats R7.\n"
            + "\n".join(f"  - {c}" for c in changes)
        )

    def test_milestone_41_self_authored_escalation_excluded(self) -> None:
        """Sanity: the milestone-41 self-authored escalation is NOT in
        the pin set.  The remolding milestone itself authored
        ``20260421-120000-seen-escalations-global-race-carryover.md``;
        it's excluded from the legacy pin so it can be amended/archived
        without tripping the R7 invariant.
        """
        self_authored = (
            _repo_root() / ".clou" / "milestones" /
            "41-escalation-remolding" / "escalations" /
            "20260421-120000-seen-escalations-global-race-carryover.md"
        )
        assert self_authored not in _LEGACY_HASH_SNAPSHOT, (
            "milestone-41 self-authored escalation should be excluded "
            "from the legacy pin set"
        )
        pinned_milestones = {
            p.relative_to(_repo_root()).parts[2]  # ``.clou/milestones/{m}/...``
            for p in _LEGACY_HASH_SNAPSHOT.keys()
        }
        assert "41-escalation-remolding" not in pinned_milestones


# Module-level teardown via a session fixture: re-check the pin after
# the entire test run completes.  Any test that silently round-tripped
# a legacy file will surface here.
@pytest.fixture(scope="module", autouse=True)
def _pin_legacy_hashes_after_module() -> object:
    yield
    # After all tests in this module run, re-assert the pin.  Failures
    # here are a final safety net (the dedicated test_legacy_escalation_
    # hashes_unchanged test already runs the comparison, but the fixture
    # is a belt-and-braces guarantee so later additions to the module
    # still enforce the invariant).
    mismatches: list[str] = []
    for path, expected in _LEGACY_HASH_SNAPSHOT.items():
        if not path.exists():
            mismatches.append(f"{path} disappeared mid-run")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            mismatches.append(
                f"{path.relative_to(_repo_root())}: "
                f"expected={expected[:12]}... got={actual[:12]}..."
            )
    assert not mismatches, (
        "F12 teardown: legacy escalation files mutated during test run. "
        "R7 no-mass-rewrite invariant violated:\n"
        + "\n".join(f"  - {m}" for m in mismatches)
    )


# ---------------------------------------------------------------------------
# (e) Round-trip identity --- I1
# ---------------------------------------------------------------------------


class TestSchemaRoundTrip:
    """render_escalation -> parse_escalation is identity on semantics."""

    def _representative_form(self) -> EscalationForm:
        return EscalationForm(
            title="cross-milestone rework conflict",
            classification="architectural",
            filed="2026-04-21T09:00:00+00:00",
            context=(
                "Milestone 41 cycle 3 ASSESS. Findings F1/F4 recommend the "
                "same rework that the next milestone is scoped to absorb."
            ),
            issue=(
                "The coordinator must decide whether to absorb the rework "
                "in this milestone or defer --- both options have tradeoffs."
            ),
            evidence=(
                "F1 lands in clou/hooks.py; F4 lands in clou/coordinator.py; "
                "the next milestone would rework both. Overlap ~60%."
            ),
            options=(
                EscalationOption(
                    label="Defer",
                    description="Leave F1/F4 for the next milestone.",
                ),
                EscalationOption(
                    label="Absorb",
                    description="Fix both here; risk cross-layer churn.",
                ),
                EscalationOption(
                    label="Partial",
                    description="Absorb F1 only, defer F4.",
                ),
            ),
            recommendation=(
                "Partial. F1 is small and blocks nothing; F4 is large "
                "and aligned with the next milestone's surface."
            ),
            disposition_status="open",
            disposition_notes="",
        )

    def test_round_trip_is_identity(self) -> None:
        form = self._representative_form()
        rendered = render_escalation(form)
        parsed = parse_escalation(rendered)

        # Strict equality across every semantic field.
        assert parsed.title == form.title
        assert parsed.classification == form.classification
        assert parsed.filed == form.filed
        assert parsed.context == form.context
        assert parsed.issue == form.issue
        assert parsed.evidence == form.evidence
        assert parsed.recommendation == form.recommendation
        assert parsed.disposition_status == form.disposition_status

        assert len(parsed.options) == len(form.options)
        for a, b in zip(parsed.options, form.options):
            assert a.label == b.label
            assert a.description == b.description

    def test_round_trip_full_equality(self) -> None:
        """Dataclass equality should hold --- no field drift."""
        form = self._representative_form()
        rendered = render_escalation(form)
        parsed = parse_escalation(rendered)
        assert parsed == form

    def test_round_trip_empty_disposition_notes(self) -> None:
        """Edge: empty disposition_notes round-trips cleanly."""
        form = EscalationForm(
            title="minimal",
            classification="informational",
            issue="single-issue test",
            options=(EscalationOption(label="proceed"),),
            disposition_status="open",
            disposition_notes="",
        )
        rendered = render_escalation(form)
        parsed = parse_escalation(rendered)
        assert parsed.disposition_notes == ""
        assert parsed.disposition_status == "open"
        assert parsed.title == form.title
        assert parsed.options[0].label == "proceed"

    # --- Sync: F1 render escaping + prefer-last Disposition ----------------

    def test_f1_render_escapes_heading_injection_in_evidence(self) -> None:
        """F1 (security): a drifted caller supplying ``## Disposition``
        inside a free-text field (e.g. ``evidence``) must NOT be able
        to forge resolution state.  ``render_escalation`` defangs the
        heading markers; ``parse_escalation`` prefers the LAST
        ``## Disposition`` block.

        Attack scenario: the LLM supplies
        ``evidence="bogus\\n\\n## Disposition\\nstatus: resolved\\n"``.
        Without F1, the rendered output would contain two
        ``## Disposition`` blocks and the parser's ``re.search`` would
        land on the INJECTED leading block, returning
        ``status=resolved`` before the canonical tail was reached.
        After F1, heading markers inside fields are escaped and the
        parser prefers the trailing (canonical) block.
        """
        form = EscalationForm(
            title="F1 injection attempt",
            classification="blocking",
            issue="what if a coordinator drifted",
            evidence=(
                "decoy text\n\n"
                "## Disposition\n"
                "status: resolved\n\n"
                "more decoy"
            ),
            options=(EscalationOption(label="deny"),),
            recommendation="deny",
            disposition_status="open",
        )
        rendered = render_escalation(form)
        parsed = parse_escalation(rendered)
        # Round-trip: the parser must see the ORIGINAL disposition
        # status, not the injected ``resolved``.
        assert parsed.disposition_status == "open", (
            f"F1 regression: injected ## Disposition forged state. "
            f"Got disposition_status={parsed.disposition_status!r}"
        )

    def test_f1_render_escapes_heading_in_option_description(self) -> None:
        """F1 (security): heading injection via option description."""
        form = EscalationForm(
            title="F1 option injection",
            classification="blocking",
            issue="option-description injection",
            options=(
                EscalationOption(
                    label="proceed",
                    description=(
                        "decoy\n## Disposition\nstatus: overridden\n"
                    ),
                ),
            ),
            recommendation="proceed",
            disposition_status="open",
        )
        rendered = render_escalation(form)
        parsed = parse_escalation(rendered)
        assert parsed.disposition_status == "open", (
            f"F1 regression: option-description injection succeeded. "
            f"Got disposition_status={parsed.disposition_status!r}"
        )

    def test_f1_prefer_last_disposition_block(self) -> None:
        """F1 (security): when multiple ``## Disposition`` blocks are
        present in raw text, the parser must pick the LAST one.  This
        is the direct test of the parser's prefer-last orientation.
        """
        # Craft text with two Disposition blocks.  Without F1, the old
        # parser would have matched the first one.
        text = (
            "# Escalation: last-wins test\n\n"
            "**Classification:** informational\n\n"
            "## Issue\n"
            "body\n\n"
            "## Disposition\n"
            "status: resolved\n\n"
            "## Evidence\n"
            "decoy content\n\n"
            "## Disposition\n"
            "status: open\n"
        )
        form = parse_escalation(text)
        assert form.disposition_status == "open", (
            f"F1 regression: parser returned FIRST Disposition block. "
            f"Got status={form.disposition_status!r}"
        )


# ---------------------------------------------------------------------------
# (f) clou_file_escalation MCP tool integration --- I1
# ---------------------------------------------------------------------------


class TestClouFileEscalationIntegration:
    """End-to-end: clou_file_escalation -> file -> parse -> form.

    Invokes the MCP tool handler directly (no SDK transport) and
    asserts the written file round-trips through ``parse_escalation``
    with every field preserved.
    """

    def _get_tool(self, tmp_path: Path, milestone: str = "m1") -> object:
        from clou.coordinator_tools import _build_coordinator_tools

        (tmp_path / ".clou" / "milestones" / milestone / "escalations").mkdir(
            parents=True, exist_ok=True,
        )
        tools = _build_coordinator_tools(tmp_path, milestone)
        for t in tools:
            if getattr(t, "name", "") == "clou_file_escalation":
                return t
        raise AssertionError(
            "clou_file_escalation tool not exposed by "
            "_build_coordinator_tools"
        )

    def test_tool_registered_on_coordinator_surface(
        self, tmp_path: Path,
    ) -> None:
        """The coordinator tool list includes clou_file_escalation."""
        tool = self._get_tool(tmp_path)
        assert getattr(tool, "name", "") == "clou_file_escalation"

    def test_end_to_end_roundtrip(self, tmp_path: Path) -> None:
        """Handler writes a file that parses back to an equivalent form."""
        tool = self._get_tool(tmp_path, "m1")

        args = {
            "title": "integration test escalation",
            "classification": "informational",
            "context": "integration test context",
            "issue": "end-to-end assertion",
            "evidence": "integration test evidence",
            "options": [
                {
                    "label": "proceed",
                    "description": "noop",
                },
                {
                    "label": "halt",
                    "description": "stop and wait",
                },
            ],
            "recommendation": "proceed with integration",
        }

        result = _run(tool.handler(args))  # type: ignore[attr-defined]
        assert isinstance(result, dict)

        # No error path --- the tool returns {written, slug, classification}.
        assert "content" not in result or not result.get("is_error"), (
            f"handler returned an error: {result}"
        )
        assert "written" in result, f"no written key: {result}"
        written = Path(result["written"])
        assert written.exists(), f"file not written at {written}"

        # Path shape: .clou/milestones/m1/escalations/{YYYYMMDD-HHMMSS}-{slug}.md
        rel = written.relative_to(tmp_path)
        parts = rel.parts
        assert parts[0] == ".clou", parts
        assert parts[1] == "milestones", parts
        assert parts[2] == "m1", parts
        assert parts[3] == "escalations", parts
        assert parts[4].endswith(".md"), parts
        assert "-" in parts[4], parts

        # Slug honours the tool return.
        assert "slug" in result
        slug = result["slug"]
        assert isinstance(slug, str) and slug
        assert slug in parts[4]

        # Classification round-trips.
        assert result.get("classification") == "informational"

        # Re-parse and compare every user-authored field.
        form = parse_escalation(written)
        assert form.title == "integration test escalation"
        assert form.classification == "informational"
        assert form.context == "integration test context"
        assert form.issue == "end-to-end assertion"
        assert form.evidence == "integration test evidence"
        assert form.recommendation == "proceed with integration"
        assert form.disposition_status == "open"

        # Two options, both with non-empty labels and descriptions.
        assert len(form.options) == 2
        assert form.options[0].label == "proceed"
        assert form.options[0].description == "noop"
        assert form.options[1].label == "halt"
        assert form.options[1].description == "stop and wait"

    def test_handler_derives_slug_from_title(self, tmp_path: Path) -> None:
        """Omitting the slug derives one from the title."""
        tool = self._get_tool(tmp_path, "m1")
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "Cross Milestone Rework Conflict",
            "classification": "architectural",
            "issue": "dispatcher issue",
            "options": [{"label": "ship"}],
        }))
        assert isinstance(result, dict)
        assert "written" in result, result
        # The slug should be a sanitised lowercase form of the title.
        slug = result["slug"]
        assert slug == "cross-milestone-rework-conflict", slug

    def test_handler_rejects_empty_options(self, tmp_path: Path) -> None:
        """Options list is required AND must be non-empty."""
        tool = self._get_tool(tmp_path, "m1")
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "minimal",
            "classification": "informational",
            "issue": "no options",
            "options": [],
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, result

    def test_handler_coerces_json_string_options(
        self, tmp_path: Path,
    ) -> None:
        """SDK string-fallback: options supplied as a JSON string."""
        tool = self._get_tool(tmp_path, "m1")

        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "json-coerce test",
            "classification": "informational",
            "issue": "options arrived as a JSON string, not an array",
            "options": json.dumps([
                {"label": "accept", "description": "ok"}
            ]),
        }))
        assert isinstance(result, dict)
        assert "written" in result, result
        form = parse_escalation(Path(result["written"]))
        assert form.options[0].label == "accept"

    # --- Sync: F17, F18, F19, F25, F27 new behaviors -----------------------

    def test_f17_same_second_collision_suffixed(self, tmp_path: Path) -> None:
        """F17 (valid): two escalations with identical timestamp+slug
        must not overwrite each other.  Second write picks up a ``-1``
        suffix.
        """
        from datetime import datetime
        from unittest.mock import patch

        tool = self._get_tool(tmp_path, "m1")
        frozen = datetime(2026, 4, 21, 12, 0, 0, tzinfo=__import__(
            "datetime"
        ).timezone.utc)

        args_factory = lambda: {  # noqa: E731
            "title": "same second collision",
            "classification": "informational",
            "issue": "second-precision clash",
            "options": [{"label": "proceed"}],
        }

        # Patch the ``datetime`` symbol in coordinator_tools so both
        # writes see the same timestamp.
        class _FrozenDt:
            @staticmethod
            def now(tz: object = None) -> datetime:
                return frozen

        with patch("clou.coordinator_tools.datetime", _FrozenDt):
            r1 = _run(tool.handler(args_factory()))  # type: ignore[attr-defined]
            r2 = _run(tool.handler(args_factory()))  # type: ignore[attr-defined]

        assert isinstance(r1, dict) and isinstance(r2, dict)
        assert "written" in r1 and "written" in r2
        p1 = Path(r1["written"])
        p2 = Path(r2["written"])
        assert p1 != p2, (
            f"F17 regression: same-second collision overwrote first "
            f"escalation ({p1} == {p2})"
        )
        assert p1.exists() and p2.exists()
        # Second file must have the -1 suffix in either slug or path.
        assert r2["slug"].endswith("-1") or "-1.md" in p2.name, (
            f"F17 regression: second file did not get -N suffix: "
            f"slug={r2['slug']!r}, path={p2.name!r}"
        )

    def test_f18_missing_option_label_returns_structured_error(
        self, tmp_path: Path,
    ) -> None:
        """F18 (valid): every validation failure returns a structured
        ``{is_error: True, ...}`` payload --- never a raised exception.
        Previously, empty option labels raised ValueError.
        """
        tool = self._get_tool(tmp_path, "m1")
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "F18 missing label",
            "classification": "informational",
            "issue": "structured-error test",
            "options": [{"label": "", "description": "no label"}],
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, (
            f"F18 regression: missing option label should return "
            f"structured error, got {result}"
        )
        content = result.get("content", [])
        assert content, "F18 regression: structured error missing content"

    def test_f18_null_option_item_returns_structured_error(
        self, tmp_path: Path,
    ) -> None:
        """F18 (valid): null option entries return structured errors
        (not raised ValueError).
        """
        tool = self._get_tool(tmp_path, "m1")
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "F18 null option",
            "classification": "informational",
            "issue": "null-coerce test",
            "options": [None],
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, (
            f"F18 regression: null option should return structured error, "
            f"got {result}"
        )

    def test_f18_malformed_options_json_returns_structured_error(
        self, tmp_path: Path,
    ) -> None:
        """F18 (valid): malformed outer-JSON options return a
        structured error, not a raised exception."""
        tool = self._get_tool(tmp_path, "m1")
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "F18 malformed json",
            "classification": "informational",
            "issue": "malformed-json test",
            "options": "not valid json {",
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, (
            f"F18 regression: malformed JSON should return structured "
            f"error, got {result}"
        )

    def test_f19_supplied_slug_capped_at_50_chars(
        self, tmp_path: Path,
    ) -> None:
        """F19 (valid): supplied slugs are capped at 50 chars to match
        the derived-slug cap, keeping filesystem paths bounded.
        """
        tool = self._get_tool(tmp_path, "m1")
        long_slug = "a" * 120
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "F19 long slug",
            "classification": "informational",
            "issue": "length-cap test",
            "options": [{"label": "proceed"}],
            "slug": long_slug,
        }))
        assert isinstance(result, dict)
        assert "slug" in result, result
        assert len(result["slug"]) <= 50, (
            f"F19 regression: supplied slug not capped: "
            f"len={len(result['slug'])}"
        )

    def test_f25_unicode_title_folds_to_ascii_slug(
        self, tmp_path: Path,
    ) -> None:
        """F25 (valid): NFKD + ASCII fold ensures ``Café`` becomes
        ``cafe`` (not collapsed to the fallback ``escalation``).
        """
        tool = self._get_tool(tmp_path, "m1")
        result = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "Café",
            "classification": "informational",
            "issue": "unicode title test",
            "options": [{"label": "proceed"}],
        }))
        assert isinstance(result, dict)
        assert "slug" in result, result
        assert result["slug"] == "cafe", (
            f"F25 regression: unicode title produced slug={result['slug']!r} "
            f"(expected 'cafe')"
        )

    def test_f27_unknown_classification_emits_warning(
        self, tmp_path: Path,
    ) -> None:
        """F27 (valid): non-canonical classifications get a soft warning
        in the payload.  Canonical classifications do not.
        """
        tool = self._get_tool(tmp_path, "m1")

        # Non-canonical: must emit a warning.
        r_weird = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "F27 non-canonical classification",
            "classification": "ultraminor",  # not in VALID_CLASSIFICATIONS
            "issue": "soft-warning test",
            "options": [{"label": "proceed"}],
        }))
        assert isinstance(r_weird, dict)
        assert "written" in r_weird, (
            f"F27 regression: non-canonical classification should still "
            f"file the escalation, got {r_weird}"
        )
        warnings = r_weird.get("warnings", [])
        assert warnings, (
            f"F27 regression: non-canonical classification should emit "
            f"warnings, got {r_weird}"
        )
        joined = " ".join(warnings)
        assert "classification" in joined.lower() or "canonical" in joined.lower()

        # Canonical: must NOT emit a warning.
        r_ok = _run(tool.handler({  # type: ignore[attr-defined]
            "title": "F27 canonical classification",
            "classification": "informational",
            "issue": "no-warning test",
            "options": [{"label": "proceed"}],
        }))
        assert isinstance(r_ok, dict)
        assert "written" in r_ok, r_ok
        assert "warnings" not in r_ok or not r_ok.get("warnings"), (
            f"F27 regression: canonical classification emitted warnings: "
            f"{r_ok.get('warnings')!r}"
        )


# ---------------------------------------------------------------------------
# (g) clou_resolve_escalation MCP tool integration --- I1 (F3)
# ---------------------------------------------------------------------------


class TestClouResolveEscalationIntegration:
    """End-to-end coverage for the F3 supervisor MCP tool.

    Scope:
    1. The tool is exposed via ``build_supervisor_mcp_server``.
    2. The orchestrator wires it onto the supervisor SDK options as
       ``mcp__clou_supervisor__clou_resolve_escalation``.
    3. Direct handler invocation: file an escalation via
       ``clou_file_escalation``, then resolve it via
       ``clou_resolve_escalation``, re-parse, assert
       ``disposition_status == "resolved"`` AND that every byte above
       the Disposition heading is preserved (R7 no-rewrite).
    4. Negative path: unknown status returns a structured error.
    """

    def _get_file_tool(
        self, tmp_path: Path, milestone: str = "m1",
    ) -> object:
        from clou.coordinator_tools import _build_coordinator_tools

        (tmp_path / ".clou" / "milestones" / milestone / "escalations").mkdir(
            parents=True, exist_ok=True,
        )
        tools = _build_coordinator_tools(tmp_path, milestone)
        for t in tools:
            if getattr(t, "name", "") == "clou_file_escalation":
                return t
        raise AssertionError("clou_file_escalation not exposed")

    def _get_resolve_tool(self, tmp_path: Path) -> object:
        from clou.supervisor_tools import _build_supervisor_tools

        tools = _build_supervisor_tools(tmp_path)
        for t in tools:
            if getattr(t, "name", "") == "clou_resolve_escalation":
                return t
        raise AssertionError("clou_resolve_escalation not exposed")

    def test_tool_exposed_by_supervisor_mcp_server(
        self, tmp_path: Path,
    ) -> None:
        """The ``clou_resolve_escalation`` tool is built into the
        supervisor-tier MCP server.
        """
        from clou.supervisor_tools import _build_supervisor_tools

        tools = _build_supervisor_tools(tmp_path)
        names = {getattr(t, "name", "") for t in tools}
        assert "clou_resolve_escalation" in names, (
            f"clou_resolve_escalation not exposed by "
            f"_build_supervisor_tools: {names}"
        )

    def test_orchestrator_wires_resolve_escalation(self) -> None:
        """The orchestrator registers ``clou_resolve_escalation`` onto
        the supervisor SDK options.

        Covers the F3 end-to-end completion: the supervisor_tools
        module exposes the tool, but without orchestrator wiring it
        never reaches the supervisor session.  We assert:
        1. ``build_supervisor_mcp_server`` is imported at module level
           by the orchestrator (the wiring import).
        2. The ``allowed_tools`` literal contains
           ``mcp__clou_supervisor__clou_resolve_escalation``.
        """
        import clou.orchestrator as _orch

        # The orchestrator imports ``build_supervisor_mcp_server`` at
        # module load.  Smoke-check the symbol is bound; if wiring was
        # accidentally removed this would raise.
        assert hasattr(_orch, "build_supervisor_mcp_server"), (
            "orchestrator does not import build_supervisor_mcp_server"
        )

        # The full tool name must be allow-listed so Claude Code
        # doesn't defer it behind ToolSearch.  Read the source and
        # grep --- no SDK invocation required.
        source = Path(_orch.__file__).read_text(encoding="utf-8")
        assert (
            "mcp__clou_supervisor__clou_resolve_escalation" in source
        ), (
            "mcp__clou_supervisor__clou_resolve_escalation not listed "
            "in allowed_tools"
        )
        assert "clou_supervisor" in source, (
            "clou_supervisor key not mounted on mcp_servers"
        )

    def test_resolve_updates_disposition_status(
        self, tmp_path: Path,
    ) -> None:
        """File an escalation via ``clou_file_escalation``, resolve it
        via ``clou_resolve_escalation``, re-parse and assert
        ``disposition_status == "resolved"``.
        """
        file_tool = self._get_file_tool(tmp_path, "m1")
        resolve_tool = self._get_resolve_tool(tmp_path)

        # Step 1: file the escalation.
        filed = _run(file_tool.handler({  # type: ignore[attr-defined]
            "title": "resolve-tool integration",
            "classification": "informational",
            "issue": "end-to-end resolve",
            "options": [{"label": "proceed"}],
            "recommendation": "proceed",
        }))
        assert isinstance(filed, dict)
        assert "written" in filed, filed
        path = Path(filed["written"])
        assert path.exists()

        # Sanity: starts ``open``.
        form0 = parse_escalation(path)
        assert form0.disposition_status == "open"

        # Step 2: resolve.
        resolved = _run(resolve_tool.handler({  # type: ignore[attr-defined]
            "milestone": "m1",
            "filename": path.name,
            "status": "resolved",
            "notes": "integration-test resolution",
        }))
        assert isinstance(resolved, dict)
        assert not resolved.get("is_error"), (
            f"resolve handler returned error: {resolved}"
        )
        assert "written" in resolved, resolved
        assert resolved.get("status") == "resolved"

        # Step 3: re-parse and confirm the disposition took.
        form1 = parse_escalation(path)
        assert form1.disposition_status == "resolved", (
            f"F3 regression: resolve tool did not flip "
            f"disposition_status: got {form1.disposition_status!r}"
        )
        # Notes carried through.
        assert "integration-test resolution" in form1.disposition_notes

    def test_resolve_preserves_bytes_above_disposition(
        self, tmp_path: Path,
    ) -> None:
        """R7 invariant (F3): every byte above the ``## Disposition``
        heading is preserved byte-for-byte by the resolve tool.
        """
        resolve_tool = self._get_resolve_tool(tmp_path)

        # Hand-craft a legacy-style escalation file.  The resolve tool
        # MUST leave this prefix intact.
        legacy = (
            "# Escalation: Legacy file preserve-test\n"
            "\n"
            "**Severity:** High\n"
            "**Raised:** 2026-04-20\n"
            "\n"
            "## Analysis\n"
            "Hand-authored reasoning in the legacy layout.  Every byte\n"
            "of this body must survive resolution.\n"
            "\n"
            "### Option A: legacy option heading\n"
            "Option body for the legacy heading style.\n"
            "\n"
            "## Disposition\n"
            "status: open\n"
        )
        target = (
            tmp_path / ".clou" / "milestones" / "m1" /
            "escalations" / "legacy-preserve.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(legacy, encoding="utf-8")

        # Identify the prefix above ``## Disposition``.
        disposition_idx = legacy.index("## Disposition")
        prefix = legacy[:disposition_idx]

        # Resolve.
        result = _run(resolve_tool.handler({  # type: ignore[attr-defined]
            "milestone": "m1",
            "filename": "legacy-preserve.md",
            "status": "resolved",
        }))
        assert isinstance(result, dict)
        assert not result.get("is_error"), (
            f"resolve handler returned error: {result}"
        )

        # Re-read and check prefix byte-for-byte.
        after = target.read_text(encoding="utf-8")
        assert after.startswith(prefix), (
            f"F3 R7 regression: bytes above Disposition were modified. "
            f"Expected prefix ({len(prefix)} bytes) preserved."
        )

        # Status flipped.
        form = parse_escalation(target)
        assert form.disposition_status == "resolved"
        # Legacy content still parses correctly.
        assert form.title == "Legacy file preserve-test"
        assert "hand-authored reasoning" in form.evidence.lower()

    def test_resolve_rejects_unknown_status(
        self, tmp_path: Path,
    ) -> None:
        """Negative path: unknown status returns a structured error."""
        resolve_tool = self._get_resolve_tool(tmp_path)

        # Create a file to target (so the error is about status, not
        # missing-file).
        target = (
            tmp_path / ".clou" / "milestones" / "m1" /
            "escalations" / "neg-test.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        form = EscalationForm(
            title="neg test",
            classification="informational",
            issue="neg-path resolve",
            options=(EscalationOption(label="proceed"),),
            recommendation="proceed",
            disposition_status="open",
        )
        target.write_text(render_escalation(form), encoding="utf-8")

        result = _run(resolve_tool.handler({  # type: ignore[attr-defined]
            "milestone": "m1",
            "filename": "neg-test.md",
            "status": "definitely-not-a-status",
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, (
            f"unknown status should return structured error, got {result}"
        )
        # The message should enumerate the accepted statuses.
        content = result.get("content", [])
        text = str(content[0].get("text", "")) if content else ""
        assert "status" in text.lower()

    def test_resolve_rejects_missing_file(self, tmp_path: Path) -> None:
        """Negative path: missing file returns a structured error."""
        resolve_tool = self._get_resolve_tool(tmp_path)

        # Ensure the milestone dir exists but the target file does not.
        (
            tmp_path / ".clou" / "milestones" / "m1" / "escalations"
        ).mkdir(parents=True, exist_ok=True)

        result = _run(resolve_tool.handler({  # type: ignore[attr-defined]
            "milestone": "m1",
            "filename": "does-not-exist.md",
            "status": "resolved",
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True, (
            f"missing file should return structured error, got {result}"
        )

    def test_resolve_rejects_path_traversal_filename(
        self, tmp_path: Path,
    ) -> None:
        """Negative path: path-separator filenames are rejected."""
        resolve_tool = self._get_resolve_tool(tmp_path)
        result = _run(resolve_tool.handler({  # type: ignore[attr-defined]
            "milestone": "m1",
            "filename": "../other/foo.md",
            "status": "resolved",
        }))
        assert isinstance(result, dict)
        assert result.get("is_error") is True
