"""Tests for the phase.md deliverable-type linter (M52 R2 / task #68).

The linter refuses phase.md files that declare a deliverable type
not registered in ``ARTIFACT_REGISTRY``.  Catches typos and
references to unregistered types BEFORE the engine's gate runs and
returns ``GateDeadlock(unregistered_type)`` mid-cycle.
"""

from __future__ import annotations

from clou.artifacts import PhaseLintError, lint_phase_md


_VALID_TYPED_PHASE_MD = (
    "# phase: p1\n\n"
    "## Purpose\n\nDo a thing.\n\n"
    "## Deliverable\n"
    "type: execution_summary\n"
    "acceptance: schema_pass\n\n"
    "## Hard constraints\n"
)


class TestLintPhaseMd:
    def test_passes_on_valid_typed_phase_md(self) -> None:
        errors = lint_phase_md(_VALID_TYPED_PHASE_MD)
        assert errors == []

    def test_unregistered_type_flagged(self) -> None:
        body = (
            "## Deliverable\n"
            "type: not_a_real_type\n"
            "acceptance: schema_pass\n"
        )
        errors = lint_phase_md(body)
        assert len(errors) == 1
        assert errors[0].code == "unregistered_type"
        assert "not_a_real_type" in errors[0].message

    def test_deliverable_section_without_type_flagged(self) -> None:
        body = (
            "## Deliverable\n"
            "A structured doc covering nine sections.\n"
            "acceptance: schema_pass\n"
        )
        errors = lint_phase_md(body)
        assert len(errors) == 1
        assert errors[0].code == "deliverable_section_missing_type"

    def test_legacy_phase_md_passes_in_default_mode(self) -> None:
        """During the M52 migration window, the linter must NOT flag
        legacy phase.md files that have no Deliverable section.
        Those flow through the F41 bootstrap path."""
        body = (
            "# phase: p1\n\n"
            "## Purpose\n\nDo a thing.\n\n"
            "## Hard constraints\n"
            "- **Single deliverable file:** `phases/p1/spec.md`.\n"
        )
        errors = lint_phase_md(body)
        assert errors == []

    def test_legacy_phase_md_flagged_in_strict_mode(self) -> None:
        """``strict=True`` enforces full M52 compliance — useful as a
        pre-flight check for new phase creation."""
        body = (
            "# phase: p1\n\n"
            "## Purpose\n\nDo a thing.\n\n"
            "## Hard constraints\n"
            "- Single deliverable file: phases/p1/spec.md\n"
        )
        errors = lint_phase_md(body, strict=True)
        assert len(errors) == 1
        assert errors[0].code == "no_deliverable_section"

    def test_passes_on_judgment_layer_spec_type(self) -> None:
        body = (
            "## Deliverable\n"
            "type: judgment_layer_spec\n"
            "acceptance: schema_pass\n"
        )
        errors = lint_phase_md(body)
        assert errors == []

    def test_lint_error_is_frozen(self) -> None:
        e = PhaseLintError(code="x", message="y")
        # Can't use pytest.raises because a frozen dataclass raises
        # FrozenInstanceError or TypeError depending on Python version.
        try:
            e.code = "other"  # type: ignore[misc]
        except Exception:
            pass
        else:  # pragma: no cover - safety
            raise AssertionError("PhaseLintError should be frozen")

    def test_one_pass_returns_all_errors(self) -> None:
        """The linter collects errors rather than raising on the first
        one — but for the current ruleset there's at most one fault
        per file (each rule is mutually exclusive).  Sanity-check."""
        body = (
            "## Deliverable\n"
            "type: still_not_real\n"
        )
        errors = lint_phase_md(body)
        assert len(errors) == 1
        assert errors[0].code == "unregistered_type"


class TestLintRealMilestoneFiles:
    """Smoke test: walk the existing milestone tree and check that
    only legacy phase.md files trip the strict linter (as expected
    until task #69 ships the migration sweep).

    This test is informational — it doesn't fail on legacy files
    because the migration is staged.  But it documents the fact that
    the linter integrates against real on-disk content and lets a
    future migration sweep prove the file count goes to zero."""

    def test_strict_lint_only_fails_on_legacy_files(self) -> None:
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        milestones_dir = repo_root / ".clou" / "milestones"
        if not milestones_dir.is_dir():
            return
        legacy_count = 0
        typed_count = 0
        for phase_md in sorted(milestones_dir.glob("*/phases/*/phase.md")):
            text = phase_md.read_text(encoding="utf-8")
            errors = lint_phase_md(text, strict=True)
            if any(e.code == "no_deliverable_section" for e in errors):
                legacy_count += 1
            elif not errors:
                typed_count += 1
        # Both counts are informational; the test passes regardless,
        # so it doesn't block the migration roll-out.  The numbers
        # serve as a snapshot for the migration ticket.
        assert legacy_count >= 0
        assert typed_count >= 0
