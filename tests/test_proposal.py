"""Tests for clou.proposal -- milestone proposal schema + render + parse.

Implements DB-21 per-artifact coverage: round-trip, drift tolerance,
validation failures, slug derivation, directory path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.proposal import (
    VALID_SCOPES,
    VALID_STATUSES,
    MilestoneProposalForm,
    parse_proposal,
    proposals_dir,
    render_proposal,
    slugify_title,
)


class TestMilestoneProposalForm:
    """Schema-level invariants."""

    def test_frozen(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
        )
        with pytest.raises(AttributeError):
            form.title = "y"  # type: ignore[misc]

    def test_slots(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
        )
        # Frozen+slots raises FrozenInstanceError (a TypeError subclass)
        # when any attribute write is attempted, whether the name is
        # an existing field or a new one.
        with pytest.raises((AttributeError, TypeError)):
            form.new_attr = "nope"  # type: ignore[attr-defined]

    def test_defaults(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
        )
        assert form.estimated_scope == "day"
        assert form.depends_on == ()
        assert form.independent_of == ()
        assert form.status == "open"

    def test_valid_enum_sets_are_tuples(self) -> None:
        """Export invariant: writer, parser, renderer, validator all
        reference the same tuples.
        """
        assert isinstance(VALID_SCOPES, tuple)
        assert isinstance(VALID_STATUSES, tuple)
        assert "day" in VALID_SCOPES
        assert "open" in VALID_STATUSES


class TestRender:
    """Canonical markdown emission."""

    def test_canonical_form_contains_expected_sections(self) -> None:
        form = MilestoneProposalForm(
            title="Close perception-layer gaps",
            filed_by_milestone="41-escalation-remolding",
            filed_by_cycle=3,
            rationale="Three failures today trace to one root: perception.",
            cross_cutting_evidence="See telemetry at t=15167s, t=23303s.",
            estimated_scope="multi-day",
            depends_on=("36-orient-cycle-prefix",),
            recommendation="Fold into ORIENT arc acceptance criteria.",
        )
        out = render_proposal(form)
        assert "# Proposal: Close perception-layer gaps" in out
        assert "**Filed by:** coordinator for milestone `41-escalation-remolding`, cycle 3" in out
        assert "**Estimated scope:** multi-day" in out
        assert "**Depends on:** 36-orient-cycle-prefix" in out
        assert "## Rationale" in out
        assert "Three failures today" in out
        assert "## Cross-Cutting Evidence" in out
        assert "## Recommendation" in out
        assert "## Disposition" in out
        assert "status: open" in out

    def test_render_deterministic(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
        )
        assert render_proposal(form) == render_proposal(form)

    def test_render_rejects_invalid_scope(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
            estimated_scope="eternity",  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="invalid estimated_scope"):
            render_proposal(form)

    def test_render_rejects_invalid_status(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
            status="deferred",  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="invalid status"):
            render_proposal(form)

    def test_optional_sections_omitted_when_empty(self) -> None:
        form = MilestoneProposalForm(
            title="x", filed_by_milestone="ms", filed_by_cycle=1,
            rationale="r", cross_cutting_evidence="e",
        )
        out = render_proposal(form)
        assert "## Recommendation" not in out
        assert "**Depends on:**" not in out
        assert "**Independent of:**" not in out


class TestParse:
    """Drift-tolerant parse."""

    def test_round_trip_byte_stable(self) -> None:
        form = MilestoneProposalForm(
            title="Round-trip check",
            filed_by_milestone="test-ms",
            filed_by_cycle=2,
            rationale="R.",
            cross_cutting_evidence="E.",
            estimated_scope="afternoon",
            depends_on=("a", "b"),
            recommendation="Rec.",
        )
        text = render_proposal(form)
        reparsed = parse_proposal(text)
        assert reparsed == form
        # Byte-stable: render(parse(canonical)) == canonical.
        assert render_proposal(reparsed) == text

    def test_drifted_title_separator(self) -> None:
        """LLMs occasionally use em-dash or en-dash after the title."""
        text = "# Proposal — Drift tolerance\n\n## Rationale\n\nx\n"
        form = parse_proposal(text)
        assert form.title == "Drift tolerance"

    def test_missing_filed_by_defaults(self) -> None:
        text = "# Proposal: no metadata\n\n## Rationale\n\nr\n"
        form = parse_proposal(text)
        assert form.filed_by_milestone == ""
        assert form.filed_by_cycle == 0

    def test_drifted_scope_coerces_to_default(self) -> None:
        text = (
            "# Proposal: x\n\n"
            "**Estimated scope:** forever-and-a-day\n\n"
            "## Rationale\n\nr\n"
        )
        form = parse_proposal(text)
        assert form.estimated_scope == "day"  # canonical default

    def test_parses_depends_on_csv(self) -> None:
        text = (
            "# Proposal: x\n\n"
            "**Filed by:** coordinator for milestone `m1`, cycle 1\n"
            "**Depends on:** a, b, c\n\n"
            "## Rationale\n\nr\n"
        )
        form = parse_proposal(text)
        assert form.depends_on == ("a", "b", "c")

    def test_parses_independent_of_csv(self) -> None:
        text = (
            "# Proposal: x\n\n"
            "**Independent of:** a,b\n\n"
            "## Rationale\n\nr\n"
        )
        form = parse_proposal(text)
        assert form.independent_of == ("a", "b")

    def test_parses_status_from_disposition(self) -> None:
        text = (
            "# Proposal: x\n\n"
            "## Rationale\n\nr\n\n"
            "## Disposition\n\nstatus: accepted\n"
        )
        form = parse_proposal(text)
        assert form.status == "accepted"

    def test_unknown_status_coerced_to_open(self) -> None:
        text = (
            "# Proposal: x\n\n"
            "## Rationale\n\nr\n\n"
            "## Disposition\n\nstatus: reticulated\n"
        )
        form = parse_proposal(text)
        assert form.status == "open"


class TestSlugify:
    def test_normal(self) -> None:
        assert slugify_title("Close Perception Gaps") == "close-perception-gaps"

    def test_strips_non_ascii(self) -> None:
        assert slugify_title("Déjà vu") == "dj-vu"

    def test_collapses_multiple_hyphens(self) -> None:
        assert slugify_title("a -- b -- c") == "a-b-c"

    def test_capped_at_max_len(self) -> None:
        assert len(slugify_title("x" * 100, max_len=20)) == 20

    def test_deterministic(self) -> None:
        assert slugify_title("X") == slugify_title("X")


class TestProposalsDir:
    def test_project_scoped_not_milestone_scoped(self, tmp_path: Path) -> None:
        """Proposals are ABOUT future milestones that don't exist yet,
        so the directory lives at .clou/proposals/, not under any
        milestone.
        """
        clou_dir = tmp_path / ".clou"
        assert proposals_dir(clou_dir) == clou_dir / "proposals"
        assert "milestones" not in proposals_dir(clou_dir).parts
