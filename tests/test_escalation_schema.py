"""Tests for clou/escalation.py --- schema + render + parse round-trip.

Covers intents I1 (schema on write) and I3 (tolerant parse across
legacy layouts).  Pair of the DB-21 drift-class remolding application
to ``escalations/*.md``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clou.escalation import (
    EscalationForm,
    EscalationOption,
    VALID_CLASSIFICATIONS,
    VALID_DISPOSITION_STATUSES,
    parse_escalation,
    render_escalation,
)
from clou.recovery_escalation import (
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_staleness_escalation,
    write_validation_escalation,
)


# ---------------------------------------------------------------------------
# Canonical render
# ---------------------------------------------------------------------------


def test_render_minimal_form_has_disposition() -> None:
    """A minimal form always emits the Disposition anchor."""
    form = EscalationForm(title="Test", classification="blocking")
    out = render_escalation(form)
    assert "# Escalation: Test" in out
    assert "**Classification:** blocking" in out
    assert "## Disposition" in out
    assert "status: open" in out


def test_render_empty_filed_omits_filed_line() -> None:
    """When filed is empty, no **Filed:** preamble is emitted."""
    form = EscalationForm(title="T", classification="blocking", filed="")
    out = render_escalation(form)
    assert "**Filed:**" not in out


def test_render_filled_filed_emits_line() -> None:
    form = EscalationForm(
        title="T",
        classification="blocking",
        filed="2026-04-21T00:00:00+00:00",
    )
    out = render_escalation(form)
    assert "**Filed:** 2026-04-21T00:00:00+00:00" in out


def test_render_empty_sections_omitted() -> None:
    """Empty optional sections (Context, Issue, Evidence, Options,
    Recommendation) are NOT emitted to avoid blank headings."""
    form = EscalationForm(
        title="T",
        classification="blocking",
        filed="2026-04-21T00:00:00+00:00",
    )
    out = render_escalation(form)
    assert "## Context" not in out
    assert "## Issue" not in out
    assert "## Evidence" not in out
    assert "## Options" not in out
    assert "## Recommendation" not in out
    # Disposition is the only anchor that MUST be present.
    assert "## Disposition" in out


def test_render_options_with_description_use_bold_em_dash() -> None:
    form = EscalationForm(
        title="T",
        classification="blocking",
        options=(
            EscalationOption(label="Migrate now", description="Full rewrite"),
            EscalationOption(label="Defer", description="Ship v1 first"),
        ),
    )
    out = render_escalation(form)
    assert "1. **Migrate now** \u2014 Full rewrite" in out
    assert "2. **Defer** \u2014 Ship v1 first" in out


def test_render_options_without_description_use_plain_number() -> None:
    form = EscalationForm(
        title="T",
        classification="blocking",
        options=(
            EscalationOption(label="Retry"),
            EscalationOption(label="Abort"),
        ),
    )
    out = render_escalation(form)
    assert "1. Retry" in out
    assert "2. Abort" in out
    # No bold markers when description is absent.
    assert "**Retry**" not in out


def test_render_disposition_with_notes() -> None:
    form = EscalationForm(
        title="T",
        classification="blocking",
        disposition_status="resolved",
        disposition_notes="Option A applied by supervisor.",
    )
    out = render_escalation(form)
    assert "status: resolved" in out
    assert "Option A applied by supervisor." in out


# ---------------------------------------------------------------------------
# Round-trip identity
# ---------------------------------------------------------------------------


def _representative_form() -> EscalationForm:
    return EscalationForm(
        title="Representative Case",
        classification="blocking",
        filed="2026-04-21T00:00:00+00:00",
        context="Some context about the situation.",
        issue="The concrete problem.",
        evidence="Errors (blocking):\n- alpha\n- beta",
        options=(
            EscalationOption(label="Retry", description="Try again"),
            EscalationOption(label="Abort", description="Give up"),
        ),
        recommendation="Retry once, then escalate.",
        disposition_status="open",
    )


def test_round_trip_identity_on_representative_form() -> None:
    form = _representative_form()
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed == form


def test_round_trip_with_notes_and_resolved_status() -> None:
    form = EscalationForm(
        title="Resolved case",
        classification="degraded",
        filed="2026-04-21T00:00:00+00:00",
        context="ctx",
        issue="iss",
        evidence="ev",
        options=(EscalationOption(label="Only option"),),
        recommendation="rec",
        disposition_status="resolved",
        disposition_notes="Option Only applied.",
    )
    assert parse_escalation(render_escalation(form)) == form


# ---------------------------------------------------------------------------
# Legacy-layout tolerance
# ---------------------------------------------------------------------------


COORDINATOR_PARALLEL_SAFETY = """# Escalation: coordinator.py parallel-safety prerequisites

## Source
Milestone: 31-cross-milestone-parallel-dispatch
Phase: parallel_dispatch (ASSESS cycle 1)

## Problem
The parallel dispatch tool in orchestrator.py is correctly implemented, but coordinator.py contains five pre-existing mechanisms that assume serial execution.

## Analysis
These are not bugs in the parallel dispatch tool -- they are pre-existing serial-execution assumptions in coordinator.py that predate this milestone.

## Options

### Option A: Per-coordinator isolation (recommended)
- Pass app reference through coordinator loop instead of global singleton
- Make marker file milestone-scoped.

### Option B: Git worktree isolation
- Each parallel coordinator gets its own git worktree.

### Option C: Document serial-only and defer
- Document that parallel dispatch is experimental and not production-ready.

## Recommendation
Option A. The fixes are localized to coordinator.py and recovery_consolidation.py.

## Severity
Non-blocking for milestone 31.
"""


def test_parse_coordinator_parallel_safety_layout() -> None:
    """Prose-authored legacy layout: ``## Problem``/``## Analysis``/
    ``### Option A:`` headings with ``## Severity`` classification."""
    form = parse_escalation(COORDINATOR_PARALLEL_SAFETY)
    assert form.title == "coordinator.py parallel-safety prerequisites"
    # Severity fell into classification (no ``**Classification:**`` preamble).
    assert form.classification.lower().startswith("non-blocking")
    # Problem became issue.
    assert "parallel dispatch tool" in form.issue.lower()
    # Analysis folded into evidence.
    assert "serial-execution assumptions" in form.evidence.lower()
    # Source folded into context.
    assert "31-cross-milestone-parallel-dispatch" in form.context
    # Three ``### Option A/B/C`` entries parsed with non-empty labels.
    assert len(form.options) >= 3
    for opt in form.options:
        assert opt.label.strip()
    # Option A recommended label captured.
    assert any(
        "per-coordinator isolation" in o.label.lower()
        for o in form.options
    )
    assert "Option A" in form.recommendation


CROSS_MILESTONE_HANDOFF = """# Escalation: Cross-milestone write access for M31 handoff.md

## Problem
The sole remaining rework item (F2) requires editing handoff.md.

## Analysis
All M32 code is complete and verified.

## Options

### Option A: User makes the one-line edit (recommended)
**Action:** User edits handoff.md line 47.

### Option B: Grant temporary cross-milestone write access
**Action:** Expand coordinator tier permissions.

### Option C: Accept milestone as-is, defer the data fix
**Action:** Close M32 with the code complete but the real-world exercise deferred.

## Recommendation
**Option A.** The edit is trivial, verified, and directly addresses the acceptance criterion.
"""


def test_parse_cross_milestone_handoff_layout() -> None:
    form = parse_escalation(CROSS_MILESTONE_HANDOFF)
    assert "Cross-milestone write access" in form.title
    assert "handoff.md" in form.issue.lower()
    assert len(form.options) >= 3
    assert all(o.label.strip() for o in form.options)


UNDERSTANDING_SKELETON = """# Escalation: understanding.md skeleton requires supervisor-tier write

**Raised:** 2026-03-29
**Severity:** low --- does not block milestone completion
**Status:** open

## Analysis

The understanding.md skeleton file needs its sections updated.

## Options

### Option A: Supervisor applies the change on next session start
The supervisor reads understanding.md at orient.

### Option B: User applies the change manually
The user runs: replace the content of .clou/understanding.md.

### Option C: Add understanding.md to coordinator write permissions
Expand coordinator tier permissions.

## Recommendation

**Option A.** The supervisor will naturally update the skeleton.

## Disposition
status: resolved
resolution: Option A applied.

## Target content

```markdown
# Understanding
```
"""


def test_parse_understanding_skeleton_layout() -> None:
    form = parse_escalation(UNDERSTANDING_SKELETON)
    # ``**Raised:**`` folds into filed.
    assert form.filed == "2026-03-29"
    # ``**Severity:**`` preamble (with bold key) folds into classification.
    assert form.classification.lower().startswith("low")
    # ``## Analysis`` lands in evidence.
    assert "understanding.md skeleton" in form.evidence.lower()
    # Options parsed from ``### Option A/B/C`` headings.
    assert len(form.options) >= 3
    # Disposition status read from ``## Disposition`` block.
    assert form.disposition_status == "resolved"
    assert "Option A applied" in form.disposition_notes


QUALITY_GATE_BLOCKED = """# Escalation: Quality Gate Blocked --- Brutalist MCP Unavailable

**Classification:** degraded
**Filed:** 2026-03-29T23:30:00Z

## Context
During ASSESS cycle for milestone 16-width-aware-planning, the assessor attempted to invoke three Brutalist quality gate tools.

## Issue
The quality gate cannot be invoked.

## Analysis

This milestone modified only markdown files.

## Options

1. **Block until Brutalist MCP is restored.** Wait for npm registry access to recover, then re-run ASSESS.
   - Pro: Follows strict quality gate requirement.

2. **Proceed to VERIFY with degraded classification.** Accept that the quality gate was unavailable.
   - Pro: Milestone is not blocked.

3. **Perform manual coordinator assessment in lieu of quality gate.** The coordinator reviews the actual changed files.
   - Pro: Provides assessment coverage without external tool dependency.

## Recommendation

**Option 2: Proceed to VERIFY with degraded classification.**

## Disposition
Pending supervisor review.
"""


def test_parse_quality_gate_blocked_layout() -> None:
    """Layout closest to canonical with ``1. **Label...**`` options."""
    form = parse_escalation(QUALITY_GATE_BLOCKED)
    assert form.classification == "degraded"
    assert form.filed == "2026-03-29T23:30:00Z"
    assert form.context.startswith("During ASSESS cycle")
    assert form.issue == "The quality gate cannot be invoked."
    # Analysis folds into evidence alongside canonical (no canonical evidence here).
    assert "markdown files" in form.evidence.lower()
    # Three bold-numbered options parsed with description content.
    assert len(form.options) == 3
    assert all(o.label.strip() for o in form.options)
    assert all(o.description for o in form.options)
    # Recommendation populated from ``**Option 2: ...**`` text.
    assert "Option 2" in form.recommendation


STATUS_MD_MCP_BUG = """# Escalation: clou_update_status MCP tool bug blocks status.md updates

## Issue
The `clou_update_status` MCP tool crashes.

## Impact
- status.md has not been updated since the initial write.

## Occurrences
- First reported: Cycle 1.

## Fix
In `coordinator_tools.py:render_status()`: add `if isinstance(phase_progress, str): phase_progress = json.loads(phase_progress)`.

## Recommendation
Fix the MCP tool.

## Workaround
Checkpoint is the authoritative state file.
"""


def test_parse_status_md_mcp_bug_layout() -> None:
    """Layout with ``## Impact`` / ``## Occurrences`` / ``## Fix``."""
    form = parse_escalation(STATUS_MD_MCP_BUG)
    # Issue survived intact.
    assert "clou_update_status" in form.issue
    # Impact + Occurrences + Workaround folded into evidence.
    assert "status.md has not been updated" in form.evidence
    assert "Cycle 1" in form.evidence
    assert "Checkpoint is the authoritative" in form.evidence
    # ``## Fix`` became recommendation fallback, but canonical
    # Recommendation is explicitly present, so Recommendation wins.
    assert form.recommendation == "Fix the MCP tool."


# ---------------------------------------------------------------------------
# Every real file on disk parses to populated content.
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _disk_escalations() -> list[Path]:
    return sorted((_REPO_ROOT / ".clou" / "milestones").glob(
        "*/escalations/*.md",
    ))


@pytest.mark.parametrize("path", _disk_escalations(), ids=lambda p: p.name)
def test_disk_escalation_parses_with_non_empty_core(path: Path) -> None:
    """Every escalation file on disk must parse to an EscalationForm with
    non-empty classification OR non-empty issue.  The goal of DB-21 I3:
    no legitimate file produces empty strings for its core semantic
    content.
    """
    form = parse_escalation(path)
    # Either the classification is populated OR the issue is --- every
    # legacy file has at least one.
    assert form.classification.strip() or form.issue.strip(), (
        f"{path} produced empty classification AND empty issue"
    )


# ---------------------------------------------------------------------------
# Missing-field defaults
# ---------------------------------------------------------------------------


def test_parse_empty_string_returns_defaults_not_raises() -> None:
    """Malformed input must not raise."""
    form = parse_escalation("")
    assert form.title == ""
    assert form.classification == ""
    assert form.issue == ""
    assert form.options == ()
    assert form.disposition_status == "open"


def test_parse_title_only() -> None:
    """Even a bare title should parse without raising."""
    form = parse_escalation("# Escalation: Lonely\n")
    assert form.title == "Lonely"
    assert form.disposition_status == "open"


def test_parse_malformed_options_does_not_raise() -> None:
    text = """# Escalation: Malformed

**Classification:** blocking

## Options
random prose without numbered items.

## Disposition
status: open
"""
    form = parse_escalation(text)
    assert form.classification == "blocking"
    # No options parsed, but no exception either.
    assert form.options == ()


# ---------------------------------------------------------------------------
# recovery_escalation.py emits canonical form that re-parses
# ---------------------------------------------------------------------------


def test_recovery_cycle_limit_escalation_round_trips(tmp_path: Path) -> None:
    asyncio.run(write_cycle_limit_escalation(tmp_path, "m1", 20))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    form = parse_escalation(files[0])
    assert form.title == "Cycle Limit Reached"
    assert form.classification == "blocking"
    assert form.filed  # ISO timestamp set
    assert form.issue
    assert form.recommendation
    # Options came in as plain strings --- they should be plain-number
    # entries with empty description and non-empty label.
    assert len(form.options) == 3
    for opt in form.options:
        assert opt.label.strip()
        assert opt.description == ""
    # Disposition defaults to open.
    assert form.disposition_status == "open"


def test_recovery_agent_crash_escalation_round_trips(tmp_path: Path) -> None:
    asyncio.run(write_agent_crash_escalation(
        tmp_path, "m1", error_detail="segfault at 0xdead",
    ))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    form = parse_escalation(files[0])
    assert form.title == "Agent Crash"
    assert form.classification == "blocking"
    assert "segfault at 0xdead" in form.evidence
    assert len(form.options) == 3


def test_recovery_validation_escalation_round_trips(tmp_path: Path) -> None:
    asyncio.run(write_validation_escalation(
        tmp_path, "m1", ["error alpha", "error beta"],
    ))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    form = parse_escalation(files[0])
    assert form.classification == "blocking"
    assert "error alpha" in form.evidence
    assert "error beta" in form.evidence


def test_recovery_staleness_escalation_round_trips(tmp_path: Path) -> None:
    asyncio.run(write_staleness_escalation(
        tmp_path, "m1",
        cycle_type="EXECUTE",
        consecutive_count=3,
        phases_completed=1,
        next_step="EXECUTE",
    ))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    form = parse_escalation(files[0])
    assert form.title == "Staleness Detected"
    assert form.classification == "blocking"
    assert "cycle_type: EXECUTE" in form.evidence
    assert "consecutive_count: 3" in form.evidence
    assert "phases_completed: 1" in form.evidence
    assert "next_step: EXECUTE" in form.evidence


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_valid_disposition_statuses_contain_core_values() -> None:
    assert "open" in VALID_DISPOSITION_STATUSES
    assert "resolved" in VALID_DISPOSITION_STATUSES
    assert "overridden" in VALID_DISPOSITION_STATUSES


def test_valid_classifications_contain_known_values() -> None:
    assert "blocking" in VALID_CLASSIFICATIONS
    assert "degraded" in VALID_CLASSIFICATIONS
    assert "informational" in VALID_CLASSIFICATIONS
    assert "architectural" in VALID_CLASSIFICATIONS


# ---------------------------------------------------------------------------
# Rework-cycle regression coverage (ASSESS cycle 1 findings)
# ---------------------------------------------------------------------------
#
# Each test below corresponds 1:1 to a finding the evaluator classified as
# valid-or-security against this phase.  Keep them grouped by finding ID so
# future ASSESS rounds can trace provenance.


# --- F1: Markdown-section injection via render_escalation ------------------


def test_f1_render_escapes_heading_injection_in_evidence() -> None:
    """A drifted caller supplying ``## Disposition`` inside evidence must
    NOT forge resolution state when the file is re-parsed."""
    malicious = (
        "bogus\n\n"
        "## Disposition\n"
        "status: resolved\n"
    )
    form = EscalationForm(
        title="Attack",
        classification="blocking",
        evidence=malicious,
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    # The rendered output must not contain a second, non-canonical
    # ``## Disposition`` heading that the parser recognises.
    # The only accepted disposition must be the canonical tail block.
    assert parsed.disposition_status == "open", (
        f"injected heading forged disposition to {parsed.disposition_status!r}"
    )
    # The evidence text is preserved (with a backslash-escaped heading).
    assert "Disposition" in parsed.evidence
    assert "status: resolved" in parsed.evidence


def test_f1_render_escapes_heading_injection_in_options() -> None:
    """Options label/description must be sanitised too."""
    form = EscalationForm(
        title="Attack",
        classification="blocking",
        options=(
            EscalationOption(
                label="benign label",
                description=(
                    "innocent-looking\n\n## Disposition\nstatus: overridden"
                ),
            ),
        ),
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed.disposition_status == "open"


def test_f1_render_escapes_newline_in_classification() -> None:
    """Embedded newlines in single-line preamble fields are collapsed
    so an attacker cannot hoist an h2 into the preamble region."""
    form = EscalationForm(
        title="Attack",
        classification=(
            "blocking\n\n## Disposition\nstatus: resolved"
        ),
        issue="legit issue",
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    # Disposition must not have been forged.
    assert parsed.disposition_status == "open"
    # The preamble must contain a single line for Classification
    # (no newline-introduced h2 after it).
    preamble_lines = rendered.splitlines()
    for line in preamble_lines:
        if line.startswith("**Classification:**"):
            assert "\n" not in line
            break
    else:
        raise AssertionError("no **Classification:** line in rendered output")


def test_f1_render_escapes_heading_injection_in_title() -> None:
    """A malicious title that starts with ``##`` cannot inject a
    section boundary into the preamble region."""
    form = EscalationForm(
        title="## Disposition\nstatus: resolved",
        classification="blocking",
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed.disposition_status == "open"


def test_f1_render_escapes_heading_injection_in_filed() -> None:
    """Same protection for the filed preamble field."""
    form = EscalationForm(
        title="T",
        classification="blocking",
        filed="2026-04-21\n\n## Disposition\nstatus: resolved",
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed.disposition_status == "open"


def test_f1_parser_prefers_last_disposition_block() -> None:
    """Even if the file on disk has an injected leading Disposition
    block (written through a compromised path), the parser must read
    the LAST block, which :func:`render_escalation` always places at
    the tail."""
    forged = (
        "# Escalation: Forged\n"
        "\n"
        "**Classification:** blocking\n"
        "\n"
        "## Disposition\n"
        "status: resolved\n"
        "\n"
        "## Context\n"
        "distractor\n"
        "\n"
        "## Disposition\n"
        "status: open\n"
    )
    form = parse_escalation(forged)
    # The terminal block wins.
    assert form.disposition_status == "open"


def test_f1_rendered_output_is_idempotent_under_reparse() -> None:
    """Round-trip under adversarial input: rendering a malicious form,
    re-parsing, and re-rendering yields identical semantic content."""
    form = EscalationForm(
        title="Attack",
        classification="blocking",
        evidence="## Disposition\nstatus: resolved",
    )
    first = render_escalation(form)
    reparsed = parse_escalation(first)
    second = render_escalation(reparsed)
    # No new sections introduced by the round-trip.
    assert first.count("## Disposition") == second.count("## Disposition")
    assert reparsed.disposition_status == "open"


# --- F4: Empty canonical Evidence suppresses Analysis fallback -------------


def test_f4_empty_canonical_evidence_lets_analysis_fall_back() -> None:
    """When ``## Evidence`` has no body, the legacy ``## Analysis``
    block must populate evidence.  This is the 2026-04-20 drift pattern
    the remolding was meant to rescue."""
    text = """# Escalation: Empty canonical evidence

**Classification:** blocking

## Issue
Something is wrong.

## Evidence

## Analysis
The real reasoning lives here.
"""
    form = parse_escalation(text)
    assert "real reasoning" in form.evidence, (
        "empty ## Evidence swallowed ## Analysis fallback"
    )


def test_f4_empty_evidence_with_impact_legacy_fallback() -> None:
    """Same behavior for ``## Impact`` as a legacy evidence source."""
    text = """# Escalation: T

**Classification:** blocking

## Evidence

## Impact
Impact description.
"""
    form = parse_escalation(text)
    assert "Impact description" in form.evidence


def test_f4_nonempty_canonical_evidence_still_wins() -> None:
    """Guard against regression: a populated ``## Evidence`` must still
    suppress the legacy ``## Analysis`` fallback (canonical wins)."""
    text = """# Escalation: T

**Classification:** blocking

## Evidence
Canonical evidence body.

## Analysis
Legacy body that should be suppressed.
"""
    form = parse_escalation(text)
    assert "Canonical evidence body" in form.evidence
    assert "Legacy body" not in form.evidence


# --- F14: Classification field has variable semantics ----------------------


def test_f14_classification_documented_as_open_set() -> None:
    """Module docstring must explicitly warn callers about the open-set
    contract so downstream ``== 'blocking'`` routing is not invited."""
    import clou.escalation as mod
    doc = mod.__doc__ or ""
    # Look for explicit open-set language in the module docstring.
    assert "open-set" in doc.lower()
    # Warn about equality-based routing.
    assert "MUST NOT" in doc or "must not" in doc.lower()


def test_f14_parser_preserves_unknown_classification_verbatim() -> None:
    """Open-set contract: any string value survives a round-trip."""
    form = EscalationForm(
        title="T",
        classification="some-brand-new-classification",
    )
    parsed = parse_escalation(render_escalation(form))
    assert parsed.classification == "some-brand-new-classification"


# --- F16: Option delimiter parsing supports ``---`` and dashes -------------


def test_f16_option_bold_parses_double_hyphen() -> None:
    """``1. **Label** -- description`` (two hyphens) parses correctly
    without leaving ``-`` characters at the start of description."""
    text = """# Escalation: T

**Classification:** blocking

## Options
1. **Alpha** -- first option
2. **Beta** -- second option

## Disposition
status: open
"""
    form = parse_escalation(text)
    assert len(form.options) == 2
    for opt in form.options:
        assert not opt.description.startswith("-"), (
            f"description has leftover dashes: {opt.description!r}"
        )
    assert form.options[0].description == "first option"
    assert form.options[1].description == "second option"


def test_f16_option_bold_parses_triple_hyphen() -> None:
    """``1. **Label** --- description`` (triple hyphen) parses correctly."""
    text = """# Escalation: T

**Classification:** blocking

## Options
1. **Gamma** --- third option
2. **Delta** --- fourth option

## Disposition
status: open
"""
    form = parse_escalation(text)
    assert len(form.options) == 2
    assert form.options[0].description == "third option"
    assert form.options[1].description == "fourth option"


def test_f16_option_bold_parses_em_dash() -> None:
    """U+2014 em-dash (as emitted by :func:`render_escalation`)."""
    text = """# Escalation: T

**Classification:** blocking

## Options
1. **Epsilon** \u2014 em-dashed
2. **Zeta** \u2013 en-dashed

## Disposition
status: open
"""
    form = parse_escalation(text)
    assert len(form.options) == 2
    assert form.options[0].description == "em-dashed"
    assert form.options[1].description == "en-dashed"


def test_f16_option_letter_heading_parses_triple_hyphen() -> None:
    """``### Option A --- Label`` (triple hyphen) in legacy prose shape."""
    text = """# Escalation: T

**Classification:** blocking

### Option A --- First label
Body for A.

### Option B --- Second label
Body for B.
"""
    form = parse_escalation(text)
    assert len(form.options) == 2
    # Labels should not have leftover dashes.
    assert form.options[0].label == "First label"
    assert form.options[1].label == "Second label"


# --- F21: Recovery writer encoding -----------------------------------------


def test_f21_recovery_writer_uses_utf8_encoding(tmp_path: Path) -> None:
    """Recovery writer must encode files as UTF-8 to match the sibling
    coordinator_tools path and to avoid UnicodeEncodeError on non-UTF-8
    locales (em-dashes appear in options)."""
    asyncio.run(write_cycle_limit_escalation(tmp_path, "m1", 20))
    esc_dir = tmp_path / ".clou" / "milestones" / "m1" / "escalations"
    files = list(esc_dir.iterdir())
    assert len(files) == 1
    # Re-read with UTF-8 explicitly and confirm the bytes round-trip.
    raw = files[0].read_bytes()
    decoded = raw.decode("utf-8")
    # The canonical renderer uses an em-dash between label and description
    # when description is present.  The recovery writer passes options as
    # plain strings (empty description), so em-dashes won't appear in
    # this fixture, but *any* future em-dash must round-trip without
    # encoding errors.  Check the file has no replacement/malformed bytes.
    assert "\ufffd" not in decoded  # no decoding-fallback replacement char


def test_f21_recovery_writer_encoding_round_trips_em_dash(tmp_path: Path) -> None:
    """Manual round-trip: call the internal writer with content that
    contains em-dashes in the evidence body and assert UTF-8 encoding."""
    from clou.recovery_escalation import _write_escalation
    path = _write_escalation(
        project_dir=tmp_path,
        milestone="m1",
        slug="unicode-test",
        title="Title \u2014 with em-dash",
        classification="blocking",
        context="context",
        issue="issue with \u2014 em-dash",
        evidence="evidence \u2014 body",
        options=["one", "two"],
        recommendation="rec",
    )
    # Must read back without error as UTF-8.
    decoded = path.read_text(encoding="utf-8")
    assert "\u2014" in decoded
    # Re-parse round-trips semantically.
    form = parse_escalation(path)
    assert "\u2014" in form.issue
    assert "\u2014" in form.evidence


# --- F23: Legacy option heading drops empty-description options -----------


def test_f23_legacy_option_without_trailing_label_is_preserved() -> None:
    """``### Option A`` with no trailing label/separator must still
    surface as an option (with empty label) rather than being dropped."""
    text = """# Escalation: T

**Classification:** blocking

### Option A
First option body.

### Option B: Explicit label
Second option body.
"""
    form = parse_escalation(text)
    # Both options must be present.
    assert len(form.options) == 2
    # F23 symmetry with ``_OPTION_BOLD_RE``: missing trailing label
    # yields an empty string, not a dropped option.
    labels = [o.label for o in form.options]
    assert "Explicit label" in labels


def test_f23_legacy_option_with_only_separator_is_preserved() -> None:
    """``### Option A:`` (colon but no label) is also preserved."""
    text = """# Escalation: T

**Classification:** blocking

### Option A:
Body A.

### Option B:
Body B.
"""
    form = parse_escalation(text)
    # Both options present with descriptions populated.
    assert len(form.options) == 2


# --- F24: Preamble regex ignores continuation lines ------------------------


def test_f24_preamble_classification_with_continuation_line() -> None:
    """``**Classification:**\\narchitectural`` (author wrapped value onto
    next line) must parse classification="architectural", not empty."""
    text = """# Escalation: T

**Classification:**
architectural

**Filed:** 2026-04-21

## Issue
The issue.
"""
    form = parse_escalation(text)
    assert form.classification == "architectural", (
        f"continuation line was dropped; classification={form.classification!r}"
    )


def test_f24_preamble_continuation_stops_at_next_bold_key() -> None:
    """Continuation must NOT consume a following ``**Filed:**`` line."""
    text = """# Escalation: T

**Classification:**
**Filed:** 2026-04-21

## Issue
Test.
"""
    form = parse_escalation(text)
    # Empty value with no non-bold continuation stays empty.
    assert form.classification == ""
    # Filed is still parsed from its own bold line.
    assert form.filed == "2026-04-21"


def test_f24_preamble_continuation_with_blank_line_before_value() -> None:
    """A single blank line between key and continuation is tolerated."""
    text = """# Escalation: T

**Classification:**

architectural

## Issue
test.
"""
    form = parse_escalation(text)
    assert form.classification == "architectural"


# --- F26: ``**Status:**`` inside Disposition --------------------------------


def test_f26_parse_disposition_bold_status_line() -> None:
    """``**Status:** resolved`` inside ``## Disposition`` must be
    recognised despite the bold wrappers."""
    text = """# Escalation: T

**Classification:** blocking

## Disposition
**Status:** resolved
Notes about the resolution.
"""
    form = parse_escalation(text)
    assert form.disposition_status == "resolved"
    assert "Notes about" in form.disposition_notes


def test_f26_parse_disposition_bold_status_overridden() -> None:
    """Same coverage for ``overridden`` status."""
    text = """# Escalation: T

**Classification:** blocking

## Disposition
**Status:** overridden
"""
    form = parse_escalation(text)
    assert form.disposition_status == "overridden"


def test_f26_parse_disposition_mixed_bold_and_plain() -> None:
    """Plain ``status:`` still works after adding bold-tolerance."""
    text = """# Escalation: T

**Classification:** blocking

## Disposition
status: resolved
"""
    form = parse_escalation(text)
    assert form.disposition_status == "resolved"


# ---------------------------------------------------------------------------
# Cycle 2 rework coverage (ASSESS cycle 2 findings)
# ---------------------------------------------------------------------------


# --- F1 / F29: CR carriage-return bypass of heading-escape -----------------


def test_cycle2_f1_escape_field_normalizes_crlf_before_heading_escape() -> None:
    """``\\r\\n`` in a field body collapses to ``\\n`` BEFORE the heading
    regex fires, so a ``\\r## Disposition`` sequence is escaped as a
    heading after the on-disk CRLF is translated to LF.
    """
    from clou.escalation import _escape_field

    # A body that would, after universal-newline read, look like:
    # ``bogus\n## Disposition\nstatus: resolved``.
    attack = "bogus\r\n## Disposition\r\nstatus: resolved"
    escaped = _escape_field(attack)
    # Heading must be backslash-escaped so the parser doesn't read it.
    assert "\\##" in escaped
    # No bare ``^## `` at the start of a line in the escaped output.
    for line in escaped.splitlines():
        assert not line.startswith("## "), (
            f"unescaped heading survived: {line!r}"
        )


def test_cycle2_f1_escape_field_normalizes_bare_cr() -> None:
    """A lone ``\\r`` (no LF) also normalizes to ``\\n`` before the regex.

    Python's universal-newline read converts bare ``\\r`` to ``\\n``, so
    the on-disk form of a ``\\r``-prefixed heading becomes a real heading
    after round-trip.  The escape must fire BEFORE that translation.
    """
    from clou.escalation import _escape_field

    attack = "prose\r## Disposition\rstatus: resolved"
    escaped = _escape_field(attack)
    assert "\\##" in escaped
    # No surviving bare CR in the output (normalized to LF).
    assert "\r" not in escaped


def test_cycle2_f1_render_defends_cr_injection_in_evidence() -> None:
    """End-to-end: CR-prefixed heading in evidence cannot forge
    disposition state after a file is written and re-parsed.
    """
    import tempfile as _tempfile

    form = EscalationForm(
        title="CR attack",
        classification="blocking",
        evidence="bogus\r\n## Disposition\r\nstatus: resolved",
    )
    rendered = render_escalation(form)
    # Write and re-read via Path to exercise the universal-newline
    # translation that the old code path was vulnerable to.
    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    ) as fh:
        fh.write(rendered)
        fh.flush()
        tmp_path = Path(fh.name)
    try:
        parsed = parse_escalation(tmp_path)
    finally:
        tmp_path.unlink()
    assert parsed.disposition_status == "open", (
        "CR-prefixed heading forged disposition after disk round-trip"
    )


def test_cycle2_f1_render_rejects_cr_in_classification() -> None:
    """Embedded ``\\r`` in classification collapses to a space so it
    cannot carry a heading into the preamble region."""
    form = EscalationForm(
        title="T",
        classification="blocking\r## Disposition\rstatus: resolved",
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    # Disposition must not have been forged through classification.
    assert parsed.disposition_status == "open"
    # Classification is on a single line in the rendered preamble.
    for line in rendered.splitlines():
        if line.startswith("**Classification:**"):
            # No CR remaining in the preamble line.
            assert "\r" not in line
            break
    else:
        raise AssertionError("no **Classification:** line in output")


def test_cycle2_f1_render_rejects_cr_in_title() -> None:
    """``\\r`` in title collapses to a space."""
    form = EscalationForm(
        title="Attack\r## Disposition\rstatus: resolved",
        classification="blocking",
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed.disposition_status == "open"


def test_cycle2_f1_render_rejects_cr_in_filed() -> None:
    """``\\r`` in filed collapses to a space."""
    form = EscalationForm(
        title="T",
        classification="blocking",
        filed="2026-04-21\r## Disposition\rstatus: resolved",
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed.disposition_status == "open"


# --- F7: single-source canonical enum --------------------------------------


def test_cycle2_f7_valid_disposition_statuses_contains_union() -> None:
    """The canonical enum carries the union of all five statuses."""
    from clou.escalation import VALID_DISPOSITION_STATUSES

    for expected in (
        "open", "investigating", "deferred", "resolved", "overridden",
    ):
        assert expected in VALID_DISPOSITION_STATUSES, (
            f"{expected!r} missing from VALID_DISPOSITION_STATUSES"
        )


def test_cycle2_f7_open_disposition_statuses_is_intermediate_subset() -> None:
    """The ``OPEN`` subset is the supervisor's in-flight set."""
    from clou.escalation import (
        OPEN_DISPOSITION_STATUSES,
        VALID_DISPOSITION_STATUSES,
    )

    assert set(OPEN_DISPOSITION_STATUSES) == {
        "open", "investigating", "deferred",
    }
    # Every OPEN status is valid.
    for s in OPEN_DISPOSITION_STATUSES:
        assert s in VALID_DISPOSITION_STATUSES


def test_cycle2_f7_resolved_disposition_statuses_is_terminal_subset() -> None:
    """The ``RESOLVED`` subset is the terminal/closed set."""
    from clou.escalation import (
        RESOLVED_DISPOSITION_STATUSES,
        VALID_DISPOSITION_STATUSES,
    )

    assert set(RESOLVED_DISPOSITION_STATUSES) == {"resolved", "overridden"}
    for s in RESOLVED_DISPOSITION_STATUSES:
        assert s in VALID_DISPOSITION_STATUSES


def test_cycle2_f7_supervisor_tools_resolution_statuses_is_canonical() -> None:
    """The supervisor module's ``RESOLUTION_STATUSES`` now aliases the
    canonical enum (F7 single-source consolidation)."""
    pytest.importorskip("claude_agent_sdk")
    from clou.escalation import VALID_DISPOSITION_STATUSES
    from clou.supervisor_tools import RESOLUTION_STATUSES

    assert set(RESOLUTION_STATUSES) == set(VALID_DISPOSITION_STATUSES)


# --- F27: parse_latest_disposition helper ----------------------------------


def test_cycle2_f27_parse_latest_disposition_last_block_wins() -> None:
    """When multiple ``## Disposition`` blocks exist, the helper returns
    the status from the LAST block."""
    from clou.escalation import parse_latest_disposition

    text = (
        "# Escalation: T\n"
        "\n"
        "## Disposition\n"
        "status: resolved\n"
        "\n"
        "## Context\n"
        "distractor\n"
        "\n"
        "## Disposition\n"
        "status: open\n"
    )
    assert parse_latest_disposition(text) == "open"


def test_cycle2_f27_parse_latest_disposition_returns_open_without_block() -> None:
    """No Disposition block at all → ``"open"`` default."""
    from clou.escalation import parse_latest_disposition

    assert parse_latest_disposition("# Title only") == "open"


def test_cycle2_f27_find_last_disposition_span_returns_last_match() -> None:
    """The span helper anchors on the LAST ``## Disposition`` heading."""
    from clou.escalation import find_last_disposition_span

    text = (
        "# T\n"
        "## Disposition\n"
        "status: resolved\n"
        "## Context\n"
        "..\n"
        "## Disposition\n"
        "status: open\n"
    )
    span = find_last_disposition_span(text)
    assert span is not None
    start, end = span
    # The span should cover the TAIL ``## Disposition`` block, not the
    # leading one.
    assert text[start:].startswith("## Disposition\nstatus: open")
    # End at the end of the text.
    assert end == len(text)


def test_cycle2_f27_find_last_disposition_span_none_when_absent() -> None:
    from clou.escalation import find_last_disposition_span

    assert find_last_disposition_span("# just title") is None


def test_cycle2_f27_find_last_disposition_span_single_block() -> None:
    """Single block → span covers that block."""
    from clou.escalation import find_last_disposition_span

    text = "# T\n\n## Disposition\nstatus: open\n"
    span = find_last_disposition_span(text)
    assert span is not None
    start, _ = span
    assert text[start:].startswith("## Disposition")


# --- F21: unknown disposition status fallback ------------------------------


def test_cycle2_f21_unknown_status_falls_back_to_open() -> None:
    """A file with ``status: closed`` (not in the valid set) parses
    ``disposition_status="open"`` while preserving the raw token."""
    text = (
        "# Escalation: T\n"
        "\n"
        "**Classification:** blocking\n"
        "\n"
        "## Disposition\n"
        "status: closed\n"
    )
    form = parse_escalation(text)
    assert form.disposition_status == "open"
    assert form.disposition_status_raw == "closed"


def test_cycle2_f21_known_status_raw_equals_canonical() -> None:
    """When status IS in the valid set, raw and canonical agree."""
    text = (
        "# Escalation: T\n"
        "\n"
        "**Classification:** blocking\n"
        "\n"
        "## Disposition\n"
        "status: resolved\n"
    )
    form = parse_escalation(text)
    assert form.disposition_status == "resolved"
    assert form.disposition_status_raw == "resolved"


def test_cycle2_f21_investigating_status_survives() -> None:
    """``investigating`` is part of the canonical union (F7 + F21)."""
    text = (
        "# Escalation: T\n"
        "\n"
        "**Classification:** blocking\n"
        "\n"
        "## Disposition\n"
        "status: investigating\n"
    )
    form = parse_escalation(text)
    assert form.disposition_status == "investigating"
    assert form.disposition_status_raw == "investigating"


def test_cycle2_f21_deferred_status_survives() -> None:
    """``deferred`` is part of the canonical union (F7 + F21)."""
    text = (
        "# Escalation: T\n"
        "\n"
        "**Classification:** blocking\n"
        "\n"
        "## Disposition\n"
        "status: deferred\n"
    )
    form = parse_escalation(text)
    assert form.disposition_status == "deferred"


def test_cycle2_f21_parse_latest_disposition_raw_exposes_drift() -> None:
    """The raw-accessor helper returns the on-disk token even when the
    canonical status was coerced to ``"open"``."""
    from clou.escalation import parse_latest_disposition_raw

    text = (
        "# T\n\n"
        "**Classification:** blocking\n\n"
        "## Disposition\n"
        "status: obsolete\n"
    )
    assert parse_latest_disposition_raw(text) == "obsolete"


# --- Round-trip stability after anchor-selector unification -----------------


def test_cycle2_round_trip_stable_after_anchor_unification() -> None:
    """Render → parse → render is stable under the consolidated LAST-match
    anchor semantics (F2 / F27 writer+reader symmetry).  Even a form
    carrying adversarial content round-trips without the Disposition
    selector flipping between first and last.
    """
    form = EscalationForm(
        title="stability",
        classification="blocking",
        context="ctx",
        issue="iss",
        evidence="## Disposition\nstatus: resolved",  # injection attempt
        options=(EscalationOption(label="A", description="a"),),
        recommendation="rec",
        disposition_status="open",
    )
    first = render_escalation(form)
    second = render_escalation(parse_escalation(first))
    assert first == second


# --- Embedded-CR rejection across single-line fields -----------------------


def test_cycle2_classification_rejects_embedded_cr() -> None:
    """Single-line fields collapse ``\\r`` so an embedded CR cannot
    hoist a second preamble key or heading into the output."""
    form = EscalationForm(
        title="T",
        classification="blocking\r\n**Status:** resolved",
    )
    rendered = render_escalation(form)
    # Classification appears on one line in the output with no CR.
    for line in rendered.splitlines():
        if line.startswith("**Classification:**"):
            assert "\r" not in line
            break


def test_cycle2_title_rejects_embedded_cr() -> None:
    form = EscalationForm(
        title="T1\rT2",
        classification="blocking",
    )
    rendered = render_escalation(form)
    assert "\r" not in rendered


def test_cycle2_filed_rejects_embedded_cr() -> None:
    form = EscalationForm(
        title="T",
        classification="blocking",
        filed="2026-04-21\r2026-04-22",
    )
    rendered = render_escalation(form)
    assert "\r" not in rendered


# ---------------------------------------------------------------------------
# M49a: trajectory_halt classification
# ---------------------------------------------------------------------------
#
# ``trajectory_halt`` is added to ``VALID_CLASSIFICATIONS``; no other
# schema changes.  The engine dispatch-gate reads escalations and halts
# on open+trajectory_halt per M49a.  These tests pin the
# classification's presence in the advisory tuple and the round-trip
# through render/parse so downstream consumers (engine check,
# supervisor disposition) can rely on canonical shape.


def test_trajectory_halt_in_valid_classifications() -> None:
    """The advisory tuple must list trajectory_halt so authors see it
    in docs + IDE completions.  The parser itself accepts any string
    value --- this is an authorship hint, not a validator."""
    assert "trajectory_halt" in VALID_CLASSIFICATIONS


def test_trajectory_halt_round_trip() -> None:
    """Render -> parse -> render must be byte-stable for a
    trajectory_halt escalation carrying the three canonical Options
    (continue-as-is / re-scope / abandon)."""
    form = EscalationForm(
        title="Anti-convergence detected in orient_integration",
        classification="trajectory_halt",
        filed="2026-04-22",
        context="Cycle 3 ASSESS on orient_integration phase.",
        issue=(
            "Findings re-surface with zero production change. "
            "58 -> 33 -> 28 trajectory confirmed by file mtimes."
        ),
        evidence=(
            "assessment.md:277-288 F28 meta-finding; "
            "three-model convergence across all critic families."
        ),
        options=(
            EscalationOption(
                label="continue-as-is",
                description="re-dispatch with current scope",
            ),
            EscalationOption(
                label="re-scope",
                description="route to PLAN with revised scope",
            ),
            EscalationOption(
                label="abandon",
                description=(
                    "route to EXIT; milestone outcome "
                    "escalated_trajectory"
                ),
            ),
        ),
        recommendation="re-scope --- orient_protocol owns the findings",
    )

    rendered1 = render_escalation(form)
    parsed = parse_escalation(rendered1)
    rendered2 = render_escalation(parsed)
    assert rendered1 == rendered2, "render->parse->render not byte-stable"

    assert parsed.classification == "trajectory_halt"
    assert [o.label for o in parsed.options] == [
        "continue-as-is", "re-scope", "abandon",
    ]
    assert parsed.recommendation.startswith("re-scope")
    assert parsed.disposition_status == "open"


def test_trajectory_halt_disposition_resolution_round_trip() -> None:
    """After supervisor disposition, status=resolved must round-trip."""
    form = EscalationForm(
        title="Anti-convergence",
        classification="trajectory_halt",
        options=(
            EscalationOption(label="continue-as-is"),
            EscalationOption(label="re-scope"),
            EscalationOption(label="abandon"),
        ),
        disposition_status="resolved",
        disposition_notes=(
            "resolved_by: supervisor\n"
            "resolution: user chose re-scope --- routing PLAN with "
            "'focus on orient_protocol production code'"
        ),
    )
    rendered = render_escalation(form)
    parsed = parse_escalation(rendered)
    assert parsed.classification == "trajectory_halt"
    assert parsed.disposition_status == "resolved"
    assert "re-scope" in parsed.disposition_notes


def test_trajectory_halt_options_preserved_across_drifted_parse() -> None:
    """Legacy ``### Option A:`` layout still parses into the options
    tuple --- the halt verb must tolerate any supervisor-era drift in
    the escalations directory."""
    drifted = (
        "# Escalation: Anti-convergence\n\n"
        "**Classification:** trajectory_halt\n\n"
        "## Issue\n"
        "Findings re-surface without production change.\n\n"
        "## Options\n"
        "### Option A: continue-as-is\n"
        "keep going\n\n"
        "### Option B: re-scope\n"
        "restart with new scope\n\n"
        "### Option C: abandon\n"
        "mark escalated_trajectory\n\n"
        "## Disposition\n"
        "status: open\n"
    )
    parsed = parse_escalation(drifted)
    assert parsed.classification == "trajectory_halt"
    assert len(parsed.options) == 3
    labels = [o.label for o in parsed.options]
    assert "continue-as-is" in labels
    assert "re-scope" in labels
    assert "abandon" in labels


def test_trajectory_halt_open_status_remains_the_engine_signal() -> None:
    """Engine halt gate must distinguish open vs resolved --- pin both
    shapes round-trip correctly so the gate can branch reliably."""
    base_fields = {
        "title": "Halt",
        "classification": "trajectory_halt",
        "options": (EscalationOption(label="continue-as-is"),),
    }
    open_form = EscalationForm(**base_fields, disposition_status="open")
    resolved_form = EscalationForm(
        **base_fields, disposition_status="resolved",
    )

    open_parsed = parse_escalation(render_escalation(open_form))
    resolved_parsed = parse_escalation(render_escalation(resolved_form))

    assert open_parsed.disposition_status == "open"
    assert resolved_parsed.disposition_status == "resolved"


# ---------------------------------------------------------------------------
# M49b B2: ENGINE_GATED_CLASSIFICATIONS contract exception
# ---------------------------------------------------------------------------
#
# The module docstring states callers MUST NOT branch on classification
# equality for routing, but M49b's halt gate does exactly that for
# `trajectory_halt`.  The `ENGINE_GATED_CLASSIFICATIONS` frozenset
# whitelists classifications where the contract exception applies: they
# are code-written (hardcoded in MCP tool handlers), immune to the
# LLM drift the general contract guards against.


def test_engine_gated_classifications_is_exported() -> None:
    """Module exports the whitelist as a frozenset constant."""
    from clou.escalation import ENGINE_GATED_CLASSIFICATIONS

    assert isinstance(ENGINE_GATED_CLASSIFICATIONS, frozenset)


def test_engine_gated_classifications_includes_trajectory_halt() -> None:
    """trajectory_halt is the first (currently only) engine-gated
    classification.  Pin membership so a future refactor doesn't
    silently drop it."""
    from clou.escalation import ENGINE_GATED_CLASSIFICATIONS

    assert "trajectory_halt" in ENGINE_GATED_CLASSIFICATIONS


def test_engine_gated_classifications_is_subset_of_valid() -> None:
    """Engine-gated values MUST also appear in VALID_CLASSIFICATIONS
    (the advisory tuple); otherwise documentation + IDE autocompletion
    won't surface them as legal classifications for human authors
    writing halt-tool invocations in prompts or examples."""
    from clou.escalation import (
        ENGINE_GATED_CLASSIFICATIONS,
        VALID_CLASSIFICATIONS,
    )

    assert set(ENGINE_GATED_CLASSIFICATIONS).issubset(
        set(VALID_CLASSIFICATIONS)
    )


def test_engine_gated_classifications_is_immutable() -> None:
    """frozenset guarantee: callers can cache the constant without
    fear of mutation by a downstream module (Python doesn't enforce
    module-level constant-ness otherwise)."""
    import pytest

    from clou.escalation import ENGINE_GATED_CLASSIFICATIONS

    with pytest.raises(AttributeError):
        ENGINE_GATED_CLASSIFICATIONS.add("spurious")  # type: ignore[attr-defined]


def test_classification_contract_doc_exception_present() -> None:
    """The module docstring must name ENGINE_GATED_CLASSIFICATIONS as
    the code-written exception to the 'don't branch on classification'
    rule.  Pin the doc state so future refactors that loosen or
    tighten the exception must update the docstring first."""
    import clou.escalation as escalation_module

    doc = escalation_module.__doc__ or ""
    assert "ENGINE_GATED_CLASSIFICATIONS" in doc
    assert "code-written" in doc.lower()


# ---------------------------------------------------------------------------
# M49b B4: find_open_engine_gated_escalation — the halt gate's scanner
# ---------------------------------------------------------------------------
#
# Tests exercise the scan helper directly; the integration test that
# run_coordinator's pre-dispatch gate actually invokes this helper
# is SDK-gated (tested in the main run_coordinator integration path).


def _write_escalation(path: Path, form: EscalationForm) -> None:
    """Helper: render and write an escalation file to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_escalation(form), encoding="utf-8")


def test_find_open_halt_returns_none_for_missing_dir(
    tmp_path: Path,
) -> None:
    """Non-existent esc_dir should return None, not raise."""
    from clou.escalation import find_open_engine_gated_escalation

    result = find_open_engine_gated_escalation(tmp_path / "no-such-dir")
    assert result is None


def test_find_open_halt_returns_none_for_empty_dir(
    tmp_path: Path,
) -> None:
    """Empty directory should return None."""
    from clou.escalation import find_open_engine_gated_escalation

    (tmp_path / "escalations").mkdir()
    result = find_open_engine_gated_escalation(tmp_path / "escalations")
    assert result is None


def test_find_open_halt_returns_none_when_no_halt_escalation(
    tmp_path: Path,
) -> None:
    """Directory with non-halt escalations returns None."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    _write_escalation(
        esc_dir / "staleness.md",
        EscalationForm(
            title="Staleness",
            classification="blocking",  # NOT engine-gated
            issue="staleness",
            options=(EscalationOption(label="resolve"),),
            disposition_status="open",
        ),
    )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is None


def test_find_open_halt_matches_on_trajectory_halt_classification(
    tmp_path: Path,
) -> None:
    """Open trajectory_halt escalation should be matched."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    _write_escalation(
        esc_dir / "halt.md",
        EscalationForm(
            title="Trajectory halt: anti_convergence (cycle 3)",
            classification="trajectory_halt",
            issue="findings re-surface",
            options=(
                EscalationOption(label="continue-as-is"),
                EscalationOption(label="re-scope"),
                EscalationOption(label="abandon"),
            ),
            disposition_status="open",
        ),
    )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is not None
    path, form = result
    assert path.name == "halt.md"
    assert form.classification == "trajectory_halt"


def test_find_open_halt_skips_resolved_halts(tmp_path: Path) -> None:
    """Resolved trajectory_halt should NOT match (engine must resume
    once the user dispositioned the halt)."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    _write_escalation(
        esc_dir / "halt.md",
        EscalationForm(
            title="Trajectory halt",
            classification="trajectory_halt",
            issue="x",
            options=(EscalationOption(label="continue-as-is"),),
            disposition_status="resolved",
        ),
    )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is None


def test_find_open_halt_matches_deferred_disposition(
    tmp_path: Path,
) -> None:
    """Brutalist Issue D fix: ``deferred`` is in
    OPEN_DISPOSITION_STATUSES, so a deferred halt still gates
    dispatch.  The original spec's narrower ``{open, investigating}``
    set would have let a deferred halt silently resume."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    _write_escalation(
        esc_dir / "halt.md",
        EscalationForm(
            title="Trajectory halt",
            classification="trajectory_halt",
            issue="x",
            options=(EscalationOption(label="continue-as-is"),),
            disposition_status="deferred",
        ),
    )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is not None


def test_find_open_halt_matches_investigating_disposition(
    tmp_path: Path,
) -> None:
    """Investigating is non-terminal — the supervisor is in the
    middle of deciding.  Gate must still fire; dispatch must not
    resume until the decision is made (status=resolved)."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    _write_escalation(
        esc_dir / "halt.md",
        EscalationForm(
            title="Trajectory halt",
            classification="trajectory_halt",
            issue="x",
            options=(EscalationOption(label="continue-as-is"),),
            disposition_status="investigating",
        ),
    )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is not None


def test_find_open_halt_skips_unparseable_file(
    tmp_path: Path,
) -> None:
    """Fail-open on corruption: a malformed escalation file logs and
    is skipped.  Other valid halts in the same directory still match.
    The operator sees the WARNING and cleans up the bad file; the
    engine does not wedge."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    esc_dir.mkdir()
    # A file that parse_escalation can't make sense of.  parse
    # is tolerant (never raises), so to force a skip we write
    # bytes that will fail read_text's utf-8 decoding.
    bad_file = esc_dir / "corrupt.md"
    bad_file.write_bytes(b"\xff\xfe\x00\x00 invalid utf-8 \xc3\x28")
    # And a valid halt file alongside.
    _write_escalation(
        esc_dir / "halt.md",
        EscalationForm(
            title="Trajectory halt",
            classification="trajectory_halt",
            issue="x",
            options=(EscalationOption(label="continue-as-is"),),
            disposition_status="open",
        ),
    )
    result = find_open_engine_gated_escalation(esc_dir)
    # Valid halt wins; corrupt file skipped.
    assert result is not None
    path, _form = result
    assert path.name == "halt.md"


def test_find_open_halt_iterates_in_sorted_order(
    tmp_path: Path,
) -> None:
    """Timestamp-prefixed filenames give deterministic oldest-first
    iteration; the first open match wins.  Pin the order so
    same-second collisions (if they ever happen) resolve
    deterministically rather than by filesystem enumeration quirks."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    for name in ("20260422-093000-halt.md", "20260422-094500-halt.md"):
        _write_escalation(
            esc_dir / name,
            EscalationForm(
                title=f"Trajectory halt {name}",
                classification="trajectory_halt",
                issue="x",
                options=(EscalationOption(label="continue-as-is"),),
                disposition_status="open",
            ),
        )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is not None
    path, _form = result
    # Earlier timestamp wins.
    assert path.name == "20260422-093000-halt.md"


def test_find_open_halt_ignores_non_md_files(
    tmp_path: Path,
) -> None:
    """Only *.md files are scanned; stray .txt / .bak / .json files
    in the escalations directory are ignored (even if they happen to
    contain the classification keyword as payload)."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    esc_dir.mkdir()
    (esc_dir / "stale.txt").write_text(
        "classification: trajectory_halt\nstatus: open\n"
    )
    (esc_dir / "halt.md.bak").write_text(
        "**Classification:** trajectory_halt\n"
    )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is None


def test_find_open_halt_respects_classification_membership(
    tmp_path: Path,
) -> None:
    """An escalation with a made-up "halt-like" classification
    (e.g. ``"trajectoryhalt"`` without the underscore,
    ``"halt"`` alone, ``"Trajectory_Halt"`` capitalised) must NOT
    match.  Only exact membership in ENGINE_GATED_CLASSIFICATIONS
    gates dispatch — this is the contract fix from Issue F."""
    from clou.escalation import find_open_engine_gated_escalation

    esc_dir = tmp_path / "escalations"
    for bad_class in ("trajectoryhalt", "halt", "Trajectory_Halt"):
        _write_escalation(
            esc_dir / f"{bad_class}.md",
            EscalationForm(
                title="fake halt",
                classification=bad_class,
                issue="x",
                options=(EscalationOption(label="continue-as-is"),),
                disposition_status="open",
            ),
        )
    result = find_open_engine_gated_escalation(esc_dir)
    assert result is None
