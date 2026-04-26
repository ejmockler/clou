"""Tests for clou/judgment.py --- schema + render + parse + validate.

Covers intent I2 (typed judgment artifact per DB-14 ArtifactForm).
Mirrors the escalation-remolding test suite pattern from M41:
round-trip identity, drift-tolerant parse, validator rejection, and
vocabulary coupling with ``recovery_checkpoint._VALID_NEXT_STEPS``.
"""

from __future__ import annotations

import pytest

from clou.judgment import (
    JUDGMENT_PATH_TEMPLATE,
    VALID_NEXT_ACTIONS,
    JudgmentForm,
    parse_judgment,
    render_judgment,
    validate_judgment_fields,
)
from clou.recovery_checkpoint import _VALID_NEXT_STEPS


# ---------------------------------------------------------------------------
# Canonical render
# ---------------------------------------------------------------------------


def test_render_minimal_form_has_all_sections() -> None:
    """A fully-populated form renders every canonical section."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="Fresh milestone — start with decomposition.",
        evidence_paths=("intents.md", "status.md"),
        expected_artifact="phases/*/phase.md for each named task",
    )
    out = render_judgment(form)
    assert out.startswith("# Judgment\n")
    assert "**Next action:** PLAN" in out
    assert "## Rationale" in out
    assert "Fresh milestone — start with decomposition." in out
    assert "## Evidence" in out
    assert "- intents.md" in out
    assert "- status.md" in out
    assert "## Expected artifact" in out
    assert "phases/*/phase.md for each named task" in out
    # Trailing newline discipline --- matches escalation.py / assessment.py.
    assert out.endswith("\n")


def test_render_empty_evidence_emits_none_placeholder() -> None:
    """Empty evidence_paths renders ``(none)`` --- the renderer is forgiving.

    validate_judgment_fields rejects this upstream; the renderer does
    NOT, so a parser fed the rendered output round-trips to an empty
    tuple without choking on a blank evidence section.
    """
    form = JudgmentForm(
        next_action="EXIT",
        rationale="Nothing left to do.",
        evidence_paths=(),
        expected_artifact="milestone closed",
    )
    out = render_judgment(form)
    assert "## Evidence\n(none)" in out


def test_render_preserves_path_order() -> None:
    """Evidence bullets are emitted in supplied order."""
    form = JudgmentForm(
        next_action="EXECUTE",
        rationale="Two phases ready.",
        evidence_paths=("phases/a/phase.md", "phases/b/phase.md"),
        expected_artifact="phases/a/execution.md and phases/b/execution.md",
    )
    out = render_judgment(form)
    a_idx = out.index("- phases/a/phase.md")
    b_idx = out.index("- phases/b/phase.md")
    assert a_idx < b_idx


# ---------------------------------------------------------------------------
# Round-trip identity
# ---------------------------------------------------------------------------


def test_round_trip_identity_full_form() -> None:
    """render(parse(render(form))) == render(form) for a populated form."""
    form = JudgmentForm(
        next_action="ASSESS",
        rationale="Three execution.md files present; ready to evaluate.",
        evidence_paths=(
            "phases/one/execution.md",
            "phases/two/execution.md",
            "phases/three/execution.md",
        ),
        expected_artifact="assessment.md with F-numbered findings",
    )
    rendered = render_judgment(form)
    parsed = parse_judgment(rendered)
    assert parsed == form


def test_round_trip_empty_evidence() -> None:
    """Round-trip handles the empty-evidence edge case via ``(none)``."""
    form = JudgmentForm(
        next_action="EXIT",
        rationale="Milestone converged.",
        evidence_paths=(),
        expected_artifact="handoff.md",
    )
    rendered = render_judgment(form)
    parsed = parse_judgment(rendered)
    assert parsed == form


# ---------------------------------------------------------------------------
# Tolerant parse
# ---------------------------------------------------------------------------


def test_parse_reordered_sections() -> None:
    """Sections in non-canonical order still parse cleanly."""
    text = (
        "# Judgment\n"
        "\n"
        "**Next action:** PLAN\n"
        "\n"
        "## Expected artifact\n"
        "phase.md files\n"
        "\n"
        "## Evidence\n"
        "- intents.md\n"
        "- status.md\n"
        "\n"
        "## Rationale\n"
        "Fresh milestone.\n"
    )
    form = parse_judgment(text)
    assert form.next_action == "PLAN"
    assert form.rationale == "Fresh milestone."
    assert form.evidence_paths == ("intents.md", "status.md")
    assert form.expected_artifact == "phase.md files"


def test_parse_missing_all_sections_gives_empty_defaults() -> None:
    """A file with only the preamble yields empty defaults --- parser NEVER raises."""
    text = "# Judgment\n\n**Next action:** PLAN\n"
    form = parse_judgment(text)
    assert form.next_action == "PLAN"
    assert form.rationale == ""
    assert form.evidence_paths == ()
    assert form.expected_artifact == ""


def test_parse_missing_preamble_gives_empty_next_action() -> None:
    """A judgment with no ``**Next action:**`` line parses with empty next_action."""
    text = (
        "# Judgment\n"
        "\n"
        "## Rationale\n"
        "Something happened.\n"
        "\n"
        "## Evidence\n"
        "- foo.md\n"
    )
    form = parse_judgment(text)
    assert form.next_action == ""
    assert form.rationale == "Something happened."
    assert form.evidence_paths == ("foo.md",)


def test_parse_evidence_bullet_list() -> None:
    """Three bullet entries become a 3-tuple in order."""
    text = (
        "# Judgment\n\n**Next action:** EXECUTE\n\n"
        "## Rationale\nGo.\n\n"
        "## Evidence\n- path/one.md\n- path/two.md\n- path/three.md\n\n"
        "## Expected artifact\nx\n"
    )
    form = parse_judgment(text)
    assert form.evidence_paths == (
        "path/one.md",
        "path/two.md",
        "path/three.md",
    )


def test_parse_evidence_none_literal() -> None:
    """``(none)`` body produces empty tuple --- round-trips empty evidence."""
    text = (
        "# Judgment\n\n**Next action:** EXIT\n\n"
        "## Rationale\nStop.\n\n"
        "## Evidence\n(none)\n\n"
        "## Expected artifact\nclosed\n"
    )
    form = parse_judgment(text)
    assert form.evidence_paths == ()


def test_parse_evidence_ignores_prose_between_bullets() -> None:
    """Non-bullet lines inside Evidence are skipped, not absorbed as paths."""
    text = (
        "# Judgment\n\n**Next action:** EXECUTE\n\n"
        "## Rationale\nGo.\n\n"
        "## Evidence\n"
        "Some prose about the evidence.\n"
        "- real/path.md\n"
        "More prose.\n"
        "- real/other.md\n"
        "\n"
        "## Expected artifact\nx\n"
    )
    form = parse_judgment(text)
    assert form.evidence_paths == ("real/path.md", "real/other.md")


def test_parse_case_insensitive_headings() -> None:
    """Heading matching is case-insensitive (``## rationale`` works)."""
    text = (
        "# Judgment\n\n**next action:** PLAN\n\n"
        "## rationale\nLowercase heading.\n\n"
        "## EVIDENCE\n- a.md\n\n"
        "## Expected Artifact\nupper.\n"
    )
    form = parse_judgment(text)
    assert form.next_action == "PLAN"
    assert form.rationale == "Lowercase heading."
    assert form.evidence_paths == ("a.md",)
    assert form.expected_artifact == "upper."


def test_parse_never_raises_on_empty_string() -> None:
    """Empty input is a valid (if useless) judgment."""
    form = parse_judgment("")
    assert form.next_action == ""
    assert form.rationale == ""
    assert form.evidence_paths == ()
    assert form.expected_artifact == ""


def test_parse_never_raises_on_garbage() -> None:
    """Completely off-schema text returns empty defaults without raising."""
    form = parse_judgment("this is not markdown at all\njust lines of text\n")
    assert form.next_action == ""
    assert form.rationale == ""
    assert form.evidence_paths == ()
    assert form.expected_artifact == ""


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def test_validator_accepts_canonical_form() -> None:
    """A valid form passes validation silently."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="Decomposition required.",
        evidence_paths=("intents.md",),
        expected_artifact="compose.py",
    )
    # Does NOT raise.
    validate_judgment_fields(form)


def test_validator_rejects_unknown_next_action() -> None:
    """``next_action`` outside the vocabulary raises with actionable message."""
    form = JudgmentForm(
        next_action="bogus",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    with pytest.raises(ValueError, match="not in cycle-type vocabulary"):
        validate_judgment_fields(form)


def test_validator_rejects_empty_evidence_paths() -> None:
    """Empty ``evidence_paths`` raises with the non-empty contract message."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="r",
        evidence_paths=(),
        expected_artifact="y",
    )
    with pytest.raises(ValueError, match="evidence_paths must be non-empty"):
        validate_judgment_fields(form)


def test_validator_rejects_empty_rationale() -> None:
    """Empty rationale raises."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    with pytest.raises(ValueError, match="rationale must be non-empty"):
        validate_judgment_fields(form)


def test_validator_rejects_whitespace_only_rationale() -> None:
    """Whitespace-only rationale counts as empty."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="   \n\t  ",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    with pytest.raises(ValueError, match="rationale must be non-empty"):
        validate_judgment_fields(form)


def test_validator_rejects_empty_expected_artifact() -> None:
    """Empty expected_artifact raises."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="",
    )
    with pytest.raises(ValueError, match="expected_artifact must be non-empty"):
        validate_judgment_fields(form)


def test_validator_rejects_whitespace_only_expected_artifact() -> None:
    """Whitespace-only expected_artifact counts as empty."""
    form = JudgmentForm(
        next_action="PLAN",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="  \t  ",
    )
    with pytest.raises(ValueError, match="expected_artifact must be non-empty"):
        validate_judgment_fields(form)


def test_validator_error_names_invalid_token() -> None:
    """The unknown-next_action message includes the offending token."""
    form = JudgmentForm(
        next_action="DANCE",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    with pytest.raises(ValueError, match="'DANCE'"):
        validate_judgment_fields(form)


# ---------------------------------------------------------------------------
# M50 I1: legacy cycle-type tokens rejected; structured tokens accepted
# ---------------------------------------------------------------------------


def test_validate_judgment_rejects_legacy_rework_next_action() -> None:
    """M50 I1: ``next_action=EXECUTE (rework)`` raises with a message
    that includes the offending punctuated token.

    The judgment vocabulary couples to ``_VALID_NEXT_STEPS`` via
    ``VALID_NEXT_ACTIONS`` -- the canonicalisation of the dispatch
    frozenset transitively drops the legacy form from accepted
    judgments.  This guards against a coordinator paraphrase that
    would otherwise round-trip a legacy token through the judgment
    file and reach the LLM dispatch surface in M37.
    """
    form = JudgmentForm(
        next_action="EXECUTE (rework)",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    with pytest.raises(ValueError, match=r"EXECUTE \(rework\)"):
        validate_judgment_fields(form)


def test_validate_judgment_rejects_legacy_verify_next_action() -> None:
    """M50 I1: ``next_action=EXECUTE (additional verification)`` raises."""
    form = JudgmentForm(
        next_action="EXECUTE (additional verification)",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    with pytest.raises(
        ValueError, match=r"EXECUTE \(additional verification\)",
    ):
        validate_judgment_fields(form)


def test_validate_judgment_accepts_structured_rework_next_action() -> None:
    """M50 I1: ``next_action=EXECUTE_REWORK`` is accepted (no raise).

    The structured token is the canonical replacement for the legacy
    ``EXECUTE (rework)`` form across the whole dispatch surface.
    """
    form = JudgmentForm(
        next_action="EXECUTE_REWORK",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    validate_judgment_fields(form)  # must not raise


def test_validate_judgment_accepts_structured_verify_next_action() -> None:
    """M50 I1: ``next_action=EXECUTE_VERIFY`` is accepted (no raise)."""
    form = JudgmentForm(
        next_action="EXECUTE_VERIFY",
        rationale="r",
        evidence_paths=("x.md",),
        expected_artifact="y",
    )
    validate_judgment_fields(form)  # must not raise


# ---------------------------------------------------------------------------
# Vocabulary coupling and path template
# ---------------------------------------------------------------------------


def test_valid_next_actions_is_dispatch_vocabulary_minus_none() -> None:
    """``VALID_NEXT_ACTIONS`` is ``_VALID_NEXT_STEPS`` minus ``none``.

    M50 I1 cycle-3 rework (F4/F15): ``none`` was removed from
    ``_VALID_NEXT_STEPS`` (it is now parse-only tolerance, never
    accepted on render).  The ``- {"none"}`` subtraction here stays
    as a belt-and-suspenders guard so a regression re-introducing
    ``none`` into the source set is still rejected for judgments.

    Judgments are ORIENT-cycle artifacts: their ``next_action`` must
    name a real dispatch target, never the EXIT-protocol "no further
    dispatch" sentinel.  :func:`validate_judgment_fields` rejects
    ``none`` via ``VALID_NEXT_ACTIONS`` membership regardless.

    Guards against vocabulary drift: when a new cycle type is added
    to the dispatch frozenset, judgments accept it automatically
    (subject to the single ``none`` exclusion).
    """
    # Post-F4/F15: none no longer lives in the source dispatch set.
    assert "none" not in _VALID_NEXT_STEPS
    assert "none" not in VALID_NEXT_ACTIONS
    # Sorted re-export of the source set (now identical to the source).
    assert set(VALID_NEXT_ACTIONS) == set(_VALID_NEXT_STEPS) - {"none"}
    assert list(VALID_NEXT_ACTIONS) == sorted(_VALID_NEXT_STEPS - {"none"})


def test_valid_next_actions_accepted_by_validator() -> None:
    """Every value in ``VALID_NEXT_ACTIONS`` passes the validator."""
    for action in VALID_NEXT_ACTIONS:
        form = JudgmentForm(
            next_action=action,
            rationale="r",
            evidence_paths=("x.md",),
            expected_artifact="y",
        )
        validate_judgment_fields(form)  # no raise


def test_judgment_path_template_zero_pads_cycle_one() -> None:
    """``.format(cycle=1)`` produces ``judgments/cycle-01-judgment.md``."""
    assert (
        JUDGMENT_PATH_TEMPLATE.format(cycle=1)
        == "judgments/cycle-01-judgment.md"
    )


def test_judgment_path_template_two_digit_cycle() -> None:
    """Two-digit cycles render without additional padding."""
    assert (
        JUDGMENT_PATH_TEMPLATE.format(cycle=42)
        == "judgments/cycle-42-judgment.md"
    )


def test_judgment_path_template_three_digit_cycle_overflows_pad() -> None:
    """Cycles >= 100 overflow the pad but remain well-formed strings.

    Sanity check --- the format string uses ``:02d`` which pads TO
    two digits but does not truncate larger values.
    """
    assert (
        JUDGMENT_PATH_TEMPLATE.format(cycle=123)
        == "judgments/cycle-123-judgment.md"
    )


# ---------------------------------------------------------------------------
# Cross-checks with the render layer
# ---------------------------------------------------------------------------


def test_render_output_validates_cleanly() -> None:
    """A validated form's rendered output parses back into a valid form."""
    form = JudgmentForm(
        next_action="VERIFY",
        rationale="Ready for the final check.",
        evidence_paths=("status.md", "intents.md"),
        expected_artifact="final verdict in verification summary",
    )
    validate_judgment_fields(form)
    text = render_judgment(form)
    parsed = parse_judgment(text)
    validate_judgment_fields(parsed)
    assert parsed == form
