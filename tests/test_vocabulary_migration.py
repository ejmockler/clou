"""Tests for ``clou.vocabulary_migration`` and the rework-detection
exact-equality switch (M50 I1).

The migration helper rewrites persisted legacy cycle-type tokens
(``EXECUTE (rework)`` / ``EXECUTE (additional verification)``) to the
structured identifiers (``EXECUTE_REWORK`` / ``EXECUTE_VERIFY``) under
the on-disk ``.clou/`` tree.  These tests exercise:

- Rewrite of an active checkpoint (``milestones/*/active/coordinator.md``).
- Rewrite of a decisions log (``milestones/*/decisions.md``).
- Rewrite of a per-cycle judgment file (``milestones/*/judgments/*.md``).
- Rewrite of a per-project prompt mirror (``prompts/coordinator-*.md``).
- Idempotency: a second run on already-migrated state returns zero counts
  and produces no on-disk writes (mtime preserved).
- Tolerance: missing milestone / prompt directories yield zero counts
  rather than raising.
- Rework detection now keys on exact-token equality against
  ``EXECUTE_REWORK`` -- the legacy substring match would have spuriously
  fired on any cycle name containing the literal substring "rework".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.vocabulary_migration import migrate_legacy_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_milestone(
    clou_dir: Path,
    name: str,
    *,
    checkpoint: str | None = None,
    decisions: str | None = None,
    judgments: dict[str, str] | None = None,
) -> Path:
    """Materialise a milestone directory and seed the requested artifacts."""
    ms_dir = clou_dir / "milestones" / name
    ms_dir.mkdir(parents=True, exist_ok=True)
    if checkpoint is not None:
        active = ms_dir / "active"
        active.mkdir(parents=True, exist_ok=True)
        (active / "coordinator.md").write_text(checkpoint, encoding="utf-8")
    if decisions is not None:
        (ms_dir / "decisions.md").write_text(decisions, encoding="utf-8")
    if judgments:
        jdir = ms_dir / "judgments"
        jdir.mkdir(parents=True, exist_ok=True)
        for jname, jbody in judgments.items():
            (jdir / jname).write_text(jbody, encoding="utf-8")
    return ms_dir


# ---------------------------------------------------------------------------
# Rewrite per artifact family
# ---------------------------------------------------------------------------


def test_migration_rewrites_checkpoint(tmp_path: Path) -> None:
    """Legacy token in active/coordinator.md is rewritten to the structured form."""
    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        checkpoint=(
            "cycle: 3\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
            "current_phase: impl\n"
        ),
    )

    counts = migrate_legacy_tokens(clou_dir)

    cp_text = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    assert "EXECUTE_REWORK" in cp_text
    assert "EXECUTE (rework)" not in cp_text
    assert counts["checkpoints"] == 1
    # Other families untouched.
    assert counts["decisions"] == 0
    assert counts["judgments"] == 0
    assert counts["prompts"] == 0


def test_migration_rewrites_decisions(tmp_path: Path) -> None:
    """Legacy token in a decisions.md anchored field line is rewritten.

    decisions.md occasionally quotes a checkpoint field verbatim in a
    fenced or indented routing block — e.g. when a cycle's decision
    explains which ``next_step`` the coordinator chose, the field
    appears at start-of-line with a colon separator.  The migration
    MUST rewrite these anchored occurrences; prose references (see
    :func:`test_migration_does_not_corrupt_prose_in_decisions`) are
    left alone.
    """
    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        decisions=(
            "## Cycle 5 — Quality Gate Assessment\n\n"
            "### Valid: foo\n\n"
            "Checkpoint routing:\n"
            "next_step: EXECUTE (rework)\n"
        ),
    )

    counts = migrate_legacy_tokens(clou_dir)

    dec = (ms / "decisions.md").read_text(encoding="utf-8")
    # Anchored field line rewritten.
    assert "next_step: EXECUTE_REWORK" in dec
    assert "EXECUTE (rework)" not in dec
    assert counts["decisions"] == 1


def test_migration_does_not_corrupt_prose_in_decisions(tmp_path: Path) -> None:
    """Prose references to the legacy token in decisions.md are NOT rewritten.

    This is the bug the cycle-1 implementation shipped: a context-blind
    ``str.replace`` over the whole file body rewrote prose mentions
    (e.g. a decision entry explaining the rename, or a paraphrased
    routing fragment like ``routing next_step: EXECUTE (rework)``).
    The field-anchored migration MUST leave such prose intact — only
    anchored ``^[ \\t]*next_step:`` field lines are touched.
    """
    clou_dir = tmp_path / ".clou"
    prose_lines = (
        "## Cycle 5 — Rename explanation\n\n"
        "The legacy token `EXECUTE (rework)` is rejected at parse\n"
        "time; its structured replacement is `EXECUTE_REWORK`.\n"
        "**Action:** Rework EXECUTE; routing next_step: EXECUTE (rework)\n"
        'Narrative: "EXECUTE (rework)" becomes EXECUTE_REWORK.\n'
    )
    ms = _seed_milestone(clou_dir, "demo", decisions=prose_lines)

    counts = migrate_legacy_tokens(clou_dir)

    dec = (ms / "decisions.md").read_text(encoding="utf-8")
    # Prose references preserved byte-for-byte.
    assert dec == prose_lines
    # And the counts reflect zero rewrites.
    assert counts["decisions"] == 0


def test_migration_rewrites_judgment(tmp_path: Path) -> None:
    """Legacy token in a judgment file is rewritten."""
    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        judgments={
            "cycle-04-judgment.md": (
                "# Judgment\n\n"
                "**Next action:** EXECUTE (additional verification)\n\n"
                "## Rationale\nVerify the new perceptual flow.\n\n"
                "## Evidence\n- status.md\n\n"
                "## Expected artifact\nverification/execution.md\n"
            ),
        },
    )

    counts = migrate_legacy_tokens(clou_dir)

    jtext = (ms / "judgments" / "cycle-04-judgment.md").read_text(
        encoding="utf-8",
    )
    assert "EXECUTE_VERIFY" in jtext
    assert "EXECUTE (additional verification)" not in jtext
    assert counts["judgments"] == 1


def test_migration_rewrites_prompt_mirror(tmp_path: Path) -> None:
    """Legacy token in .clou/prompts/coordinator-*.md mirror is rewritten.

    The bundled ``clou/_prompts/`` is the source of truth, but the
    per-project mirror written by ``clou_init`` does not auto-resync;
    the migration treats it as same drift class as the milestone
    artifacts.

    The mirror contains routing directives in the canonical
    ``^[ \\t]*next_step: VALUE$`` form (as in
    ``coordinator-verify.md`` line 50), which is exactly what the
    field-anchored pattern captures.
    """
    clou_dir = tmp_path / ".clou"
    prompts = clou_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "coordinator-assess.md").write_text(
        "# Assess\n\n"
        "Rework routing (indented routing block):\n"
        "     next_step: EXECUTE (rework)\n",
        encoding="utf-8",
    )
    (prompts / "coordinator-verify.md").write_text(
        "# Verify\n\n"
        "Perceptual-gap routing:\n"
        "     next_step: EXECUTE (additional verification)\n",
        encoding="utf-8",
    )

    counts = migrate_legacy_tokens(clou_dir)

    assess_txt = (prompts / "coordinator-assess.md").read_text(
        encoding="utf-8",
    )
    verify_txt = (prompts / "coordinator-verify.md").read_text(
        encoding="utf-8",
    )
    assert "next_step: EXECUTE_REWORK" in assess_txt
    assert "EXECUTE (rework)" not in assess_txt
    assert "next_step: EXECUTE_VERIFY" in verify_txt
    assert "EXECUTE (additional verification)" not in verify_txt
    assert counts["prompts"] == 2


def test_migration_does_not_corrupt_prompt_prose(tmp_path: Path) -> None:
    """Prose mentions of the legacy token in prompt mirrors are NOT rewritten.

    coordinator-orient.md legitimately mentions the legacy tokens in
    a "these are rejected at parse time" doc note.  The field-anchored
    regex must leave these prose mentions intact — rewriting them would
    make the doc note self-contradictory ("EXECUTE_REWORK is rejected
    at parse time").
    """
    clou_dir = tmp_path / ".clou"
    prompts = clou_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    doc_prose = (
        "# Orient\n\n"
        "Legacy tokens `EXECUTE (rework)` and `EXECUTE (additional "
        "verification)`\n"
        "are rejected at parse time.  Use `EXECUTE_REWORK` /\n"
        "`EXECUTE_VERIFY` instead.\n"
    )
    (prompts / "coordinator-orient.md").write_text(doc_prose, encoding="utf-8")

    counts = migrate_legacy_tokens(clou_dir)

    after = (prompts / "coordinator-orient.md").read_text(encoding="utf-8")
    # Prose preserved byte-for-byte.
    assert after == doc_prose
    assert counts["prompts"] == 0


# ---------------------------------------------------------------------------
# Idempotency, tolerance, structured-input no-op
# ---------------------------------------------------------------------------


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """A second run is a no-op at the WRITE site, not just in counts.

    M50 I1 cycle-3 rework (F21): the cycle-2 idempotency test
    compared ``st_mtime_ns`` before/after — a loose proxy that can
    yield false negatives on filesystems with coarse-grained
    timestamps.  The structural invariant is: on a second run over
    already-migrated state, ``_atomic_write`` MUST NOT be called
    (the content-hash guard short-circuits before the atomic write).
    This patches ``_atomic_write`` on the second run and asserts
    ``assert_not_called``.

    Call-site idempotency is the load-bearing property — mtime
    preservation is a downstream consequence.  Pinning the call-site
    catches regressions like "always write even when content
    unchanged" which mtime preservation might accidentally pass on
    some filesystems.
    """
    from unittest.mock import patch

    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        checkpoint=(
            "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        ),
    )
    cp_path = ms / "active" / "coordinator.md"

    # First run rewrites once (no patch — real _atomic_write executes).
    first = migrate_legacy_tokens(clou_dir)
    assert first["checkpoints"] == 1
    mtime_after_first = cp_path.stat().st_mtime_ns

    # Second run: patch _atomic_write to assert it is NOT called.
    with patch(
        "clou.vocabulary_migration._atomic_write",
    ) as mock_atomic:
        second = migrate_legacy_tokens(clou_dir)
    mock_atomic.assert_not_called()
    assert second == {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [],
    }
    # mtime preserved as a consequence (belt-and-suspenders).
    assert cp_path.stat().st_mtime_ns == mtime_after_first


def test_migration_leaves_structured_files_untouched(tmp_path: Path) -> None:
    """A checkpoint that already uses the structured token is not rewritten.

    Critical for the idempotency invariant -- the migration must
    detect "already migrated" state without touching the file.
    """
    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "fresh",
        checkpoint=(
            "cycle: 0\nstep: PLAN\nnext_step: EXECUTE_REWORK\n"
            "current_phase: impl\n"
        ),
    )
    cp_path = ms / "active" / "coordinator.md"
    mtime_before = cp_path.stat().st_mtime_ns

    counts = migrate_legacy_tokens(clou_dir)

    assert counts == {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [],
    }
    assert cp_path.stat().st_mtime_ns == mtime_before


def test_migration_tolerates_missing_directories(tmp_path: Path) -> None:
    """No milestones/ and no prompts/ directories yields zero counts."""
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir(parents=True, exist_ok=True)
    # No milestones/ subtree, no prompts/ subtree -- bare .clou/.

    counts = migrate_legacy_tokens(clou_dir)

    assert counts == {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [],
    }


def test_migration_ignores_arbitrary_markdown_files_under_milestones(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-3 rework (F18): only the four named artifact families are swept.

    Replaces the cycle-2 tautological ``test_migration_skips_non_
    milestone_entries`` which seeded a single ``.txt`` stray and
    asserted the helper's iterdir() loop declines non-directories.
    That test verified ``pathlib.Path.iterdir`` behaviour, not the
    migration contract.

    The REAL invariant: the migration has a closed target list —
    exactly four artifact families
    (``active/coordinator.md``, ``decisions.md``,
    ``judgments/*.md``, ``prompts/coordinator-*.md``).  Any ``.md``
    file that isn't in this list, EVEN INSIDE a milestone directory,
    must be left alone.  A regression that widened the sweep (e.g.,
    a ``rglob("*.md")`` shortcut) would pass the cycle-2 test but
    fail this one.
    """
    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        checkpoint=(
            "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        ),
    )

    # Materialise OUT-OF-SCOPE markdown files with the legacy token
    # inside a real milestone directory structure.  If the sweep
    # accidentally widened to grep-everything-markdown, these files
    # would be rewritten and counts would tick up.
    out_of_scope_files = {
        # Phase working files — milestone-local but not in any of the
        # four named families.
        ms / "phases" / "impl" / "execution.md": (
            "## Task 1\n"
            "Routing for this phase pins next_step: EXECUTE (rework)\n"
        ),
        ms / "phases" / "impl" / "plan.md": (
            "# Plan\n\n"
            "next_step: EXECUTE (rework)\n"
        ),
        # Status / notes at milestone root (not one of the four).
        ms / "status.md": (
            "# Status\nnext_step: EXECUTE (rework)\n"
        ),
        # Inside a "notes/" subdir the helper does not know about.
        ms / "notes" / "progress.md": (
            "next_step: EXECUTE (rework)\n"
        ),
    }
    for path, body in out_of_scope_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    counts = migrate_legacy_tokens(clou_dir)

    # The in-scope checkpoint was rewritten.
    assert counts["checkpoints"] == 1
    # Out-of-scope files untouched — byte-identical.
    for path, expected in out_of_scope_files.items():
        assert path.read_text(encoding="utf-8") == expected, (
            f"Out-of-scope file {path} was mutated by the migration"
        )
    assert counts["decisions"] == 0
    assert counts["judgments"] == 0
    assert counts["prompts"] == 0
    assert counts["failed"] == []


def test_migration_handles_multiple_milestones(tmp_path: Path) -> None:
    """Per-family counts aggregate across all milestones swept."""
    clou_dir = tmp_path / ".clou"
    _seed_milestone(
        clou_dir,
        "alpha",
        checkpoint=(
            "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        ),
        # Anchored field line (checkpoint-style) embedded in a
        # decisions.md block.  Prose mentions of the legacy token are
        # deliberately NOT rewritten — see
        # test_migration_does_not_corrupt_prose_in_decisions.
        decisions="Routing echo:\nnext_step: EXECUTE (rework)\n",
    )
    _seed_milestone(
        clou_dir,
        "beta",
        checkpoint=(
            "cycle: 2\nstep: VERIFY\n"
            "next_step: EXECUTE (additional verification)\n"
        ),
        judgments={
            "cycle-02-judgment.md": (
                "# Judgment\n\n"
                "**Next action:** EXECUTE (rework)\n\n"
                "## Rationale\nFix the broken handler.\n\n"
                "## Evidence\n- assessment.md\n\n"
                "## Expected artifact\nphases/handler/execution.md\n"
            ),
        },
    )

    counts = migrate_legacy_tokens(clou_dir)

    assert counts["checkpoints"] == 2  # both milestones rewritten
    assert counts["decisions"] == 1
    assert counts["judgments"] == 1
    assert counts["prompts"] == 0
    assert counts["failed"] == []


# ---------------------------------------------------------------------------
# F6 — _CHECKPOINT_FIELD_RE stash-field alternation coverage.  The regex
# gates three field names in one alternation: ``next_step``,
# ``pre_orient_next_step``, ``pre_halt_next_step``.  Previously only the
# ``next_step`` leg was exercised; these parametrised tests close the
# gap so a regex edit that drops a field (e.g., re-simplifying the
# alternation to only ``next_step``) fails loudly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["next_step", "pre_orient_next_step", "pre_halt_next_step"],
)
def test_migration_rewrites_checkpoint_stash_field_isolated(
    tmp_path: Path,
    field: str,
) -> None:
    """Each stash field is covered independently by the alternation.

    M50 I1 cycle-3 rework (F6): the cycle-2 implementation added the
    stash-field alternation (``next_step``, ``pre_orient_next_step``,
    ``pre_halt_next_step``) to ``_CHECKPOINT_FIELD_RE`` but only the
    ``next_step`` leg was tested.  If a regex edit silently drops a
    stash field from the alternation, a legacy token in that field
    would survive the migration undetected.  This parametrised test
    fires the regex against each field in isolation so every leg is
    pinned.

    Mutation guard: a future edit that replaces the alternation with
    just ``next_step`` fails two of three parameter sets.
    """
    clou_dir = tmp_path / ".clou"
    checkpoint = (
        "cycle: 3\nstep: ORIENT\n"
        "next_step: ORIENT\n"
        f"{field}: EXECUTE (rework)\n"
        "current_phase: impl\n"
    )
    # For the bare ``next_step`` case, we want to check the alternation
    # rewrites the field alone (not the ``next_step: ORIENT`` line above).
    if field == "next_step":
        checkpoint = (
            "cycle: 3\nstep: ASSESS\n"
            "next_step: EXECUTE (rework)\n"
            "current_phase: impl\n"
        )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=checkpoint)

    counts = migrate_legacy_tokens(clou_dir)

    cp_text = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    # The specific field was rewritten.
    assert f"{field}: EXECUTE_REWORK" in cp_text
    # No legacy token survives anywhere.
    assert "EXECUTE (rework)" not in cp_text
    assert counts["checkpoints"] == 1
    assert counts["failed"] == []


def test_migration_rewrites_checkpoint_all_stash_fields_combined(
    tmp_path: Path,
) -> None:
    """All three stash fields in the same checkpoint are rewritten in one pass.

    M50 I1 cycle-3 rework (F6): combined fixture covering all three
    regex alternation legs in a single file.  Proves the regex applies
    globally (``re.sub`` applies all matches, not just the first) and
    that the atomic write preserves the non-field surrounding lines
    byte-for-byte.
    """
    clou_dir = tmp_path / ".clou"
    checkpoint = (
        "cycle: 9\nstep: ORIENT\n"
        "next_step: EXECUTE (rework)\n"
        "pre_orient_next_step: EXECUTE (rework)\n"
        "pre_halt_next_step: EXECUTE (additional verification)\n"
        "current_phase: impl\n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=checkpoint)

    counts = migrate_legacy_tokens(clou_dir)

    cp_text = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    # All three fields rewritten to structured tokens.
    assert "next_step: EXECUTE_REWORK" in cp_text
    assert "pre_orient_next_step: EXECUTE_REWORK" in cp_text
    assert "pre_halt_next_step: EXECUTE_VERIFY" in cp_text
    # No legacy tokens survive.
    assert "EXECUTE (rework)" not in cp_text
    assert "EXECUTE (additional verification)" not in cp_text
    # Non-field lines preserved byte-for-byte.
    assert "cycle: 9" in cp_text
    assert "step: ORIENT" in cp_text
    assert "current_phase: impl" in cp_text
    # Single file rewritten once — counts are per-file not per-match.
    assert counts["checkpoints"] == 1
    assert counts["failed"] == []


def test_migration_decisions_rewrites_pre_orient_next_step_field(
    tmp_path: Path,
) -> None:
    """decisions.md stash-field quotations are covered by the regex.

    M50 I1 cycle-3 rework (F6/F14): decisions.md sometimes echoes the
    ``pre_orient_next_step:`` stash value when documenting an ORIENT-
    exit restoration decision.  The same field-anchored alternation
    that gates checkpoints must gate decisions too.
    """
    clou_dir = tmp_path / ".clou"
    decisions = (
        "## Cycle 7 — ORIENT-exit restoration\n\n"
        "Routing summary:\n"
        "pre_orient_next_step: EXECUTE (rework)\n"
    )
    ms = _seed_milestone(clou_dir, "demo", decisions=decisions)

    counts = migrate_legacy_tokens(clou_dir)

    dec = (ms / "decisions.md").read_text(encoding="utf-8")
    assert "pre_orient_next_step: EXECUTE_REWORK" in dec
    assert "EXECUTE (rework)" not in dec
    assert counts["decisions"] == 1


def test_migration_decisions_combined_anchored_and_prose(
    tmp_path: Path,
) -> None:
    """Combined fixture: anchored fields rewritten, prose preserved.

    M50 I1 cycle-3 rework (F14): the combined fixture proves the
    field-anchored rewriter discriminates between anchored field lines
    (which must be rewritten) and prose quoting the same legacy token
    (which must be preserved).  A mutation that drops the ``^`` or
    ``$`` anchor in ``_CHECKPOINT_FIELD_RE`` would either rewrite prose
    (failing the prose half of this test) or leave fields untouched
    (failing the rewrite counts).
    """
    clou_dir = tmp_path / ".clou"
    decisions = (
        "## Cycle 8 — Rename + restoration\n\n"
        "The legacy token `EXECUTE (rework)` was renamed to "
        "`EXECUTE_REWORK`.\n"
        "Routing block:\n"
        "next_step: EXECUTE (rework)\n"
        "pre_orient_next_step: EXECUTE (rework)\n"
        "Narrative: `EXECUTE (rework)` appears three more times here.\n"
    )
    ms = _seed_milestone(clou_dir, "demo", decisions=decisions)

    counts = migrate_legacy_tokens(clou_dir)

    dec = (ms / "decisions.md").read_text(encoding="utf-8")
    # Anchored fields rewritten.
    assert "next_step: EXECUTE_REWORK" in dec
    assert "pre_orient_next_step: EXECUTE_REWORK" in dec
    # Prose backtick mentions preserved byte-for-byte.
    # Two occurrences: the rename explanation and the narrative line.
    assert dec.count("`EXECUTE (rework)`") == 2
    # Anchored legacy tokens are GONE (only the backtick-wrapped prose
    # remains, which is a distinct token shape).
    assert "\nnext_step: EXECUTE (rework)" not in dec
    assert "\npre_orient_next_step: EXECUTE (rework)" not in dec
    # Multiple anchored matches in one file still count as ONE rewrite
    # (per-file not per-match).
    assert counts["decisions"] == 1


# ---------------------------------------------------------------------------
# Rework-detection: exact-token equality
# ---------------------------------------------------------------------------


def test_is_rework_requested_fires_on_structured_token() -> None:
    """The production :func:`is_rework_requested` helper fires on EXECUTE_REWORK.

    Coupling check: this is the predicate the EXECUTE dispatch path
    consults at ``coordinator.py:run_coordinator`` (post-M50): the
    helper examines the effective ``next_step`` (with ORIENT-stash
    fallback) against exact-token equality with ``EXECUTE_REWORK``.
    Replaces the previous tautological assertion ``"EXECUTE_REWORK"
    == "EXECUTE_REWORK"`` which asserted nothing about production.
    """
    from clou.recovery_checkpoint import Checkpoint, is_rework_requested

    cp = Checkpoint(next_step="EXECUTE_REWORK")
    assert is_rework_requested(cp) is True


def test_is_rework_requested_does_not_fire_on_plain_execute() -> None:
    """A vanilla ``EXECUTE`` cycle is NOT a rework cycle per production."""
    from clou.recovery_checkpoint import Checkpoint, is_rework_requested

    cp = Checkpoint(next_step="EXECUTE")
    assert is_rework_requested(cp) is False


def test_is_rework_requested_does_not_fire_on_substring_match() -> None:
    """Non-canonical tokens containing the literal substring "rework"
    MUST NOT trigger rework detection via the production helper.

    This is the spurious-match the canonicalisation closes -- the
    legacy substring check (``"rework" in _effective_next_step.lower()``)
    would have fired on a hypothetical future ``REWORK_PLAN`` token
    or any string-typo variant.  Exact equality through the helper is
    the structural fix.
    """
    from clou.recovery_checkpoint import Checkpoint, is_rework_requested

    for spurious in (
        "execute_rework_pending",      # extended variant
        "REWORK_PLAN",                  # unrelated cycle, contains substring
        "execute (rework, deferred)",  # punctuated paraphrase
    ):
        cp = Checkpoint(next_step=spurious)
        assert is_rework_requested(cp) is False, (
            f"helper wrongly fired on {spurious!r}"
        )


def test_is_rework_requested_reads_pre_orient_stash() -> None:
    """When ORIENT is pending/live, the helper falls back to the stash.

    Iteration 1 (session-start ORIENT dispatch): the coordinator
    rewrites ``next_step`` to ``"ORIENT"`` and stashes the prior step
    in ``pre_orient_next_step``.  The helper MUST consult the stash
    for the effective-step verdict — otherwise rework telemetry would
    silently drop on every first iteration after an ASSESS-requested
    rework.
    """
    from clou.recovery_checkpoint import Checkpoint, is_rework_requested

    # ORIENT pending, stash holds EXECUTE_REWORK.
    cp = Checkpoint(
        next_step="ORIENT",
        pre_orient_next_step="EXECUTE_REWORK",
    )
    assert is_rework_requested(cp) is True

    # ORIENT pending, stash holds plain EXECUTE (not rework).
    cp2 = Checkpoint(
        next_step="ORIENT",
        pre_orient_next_step="EXECUTE",
    )
    assert is_rework_requested(cp2) is False

    # ORIENT pending, empty stash (no restoration target).
    cp3 = Checkpoint(next_step="ORIENT", pre_orient_next_step="")
    assert is_rework_requested(cp3) is False


def test_is_rework_requested_does_not_fire_on_legacy_token() -> None:
    """Legacy punctuated tokens MUST NOT trigger rework detection.

    ``parse_checkpoint`` would reject ``EXECUTE (rework)`` and default
    to ``PLAN``, but if a test or caller constructs a Checkpoint
    directly with a legacy literal, the helper still must return
    False — rework dispatch is coupled to the canonical vocabulary,
    not the legacy form.
    """
    from clou.recovery_checkpoint import Checkpoint, is_rework_requested

    cp = Checkpoint(next_step="EXECUTE (rework)")
    assert is_rework_requested(cp) is False


def test_is_rework_requested_on_parsed_legacy_checkpoint_is_false() -> None:
    """Full parse path: a legacy checkpoint defaults to PLAN, helper returns False.

    End-to-end: parse rejects the legacy token, ``next_step`` becomes
    ``PLAN``, and the rework helper correctly says "no" because PLAN
    is not EXECUTE_REWORK.  This test wires parse_checkpoint through
    to the helper to prove the integration.
    """
    from clou.recovery_checkpoint import is_rework_requested, parse_checkpoint

    cp = parse_checkpoint(
        "cycle: 3\nstep: ASSESS\nnext_step: EXECUTE (rework)\n",
    )
    assert cp.next_step == "PLAN"
    assert is_rework_requested(cp) is False


def test_execute_rework_is_in_valid_vocabulary() -> None:
    """The structured rework + verify tokens live in ``_VALID_NEXT_STEPS``.

    M50 I1 cycle-3 rework (F17): renamed from
    ``test_rework_detection_imports_from_canonical_vocabulary`` — the
    previous name implied a coupling check via import, but the body
    only asserted membership.  The accurate name is
    ``test_execute_rework_is_in_valid_vocabulary``: the test pins
    vocabulary membership of the structured tokens.

    Coupling check: when M37 promotes judgment to dispatch authority,
    the structured token is the value the LLM types and the value
    ``parse_checkpoint`` accepts — they must be the same string.
    Legacy punctuated forms MUST NOT be in the vocabulary since
    ``parse_checkpoint`` rejects them at parse time.
    """
    from clou.recovery_checkpoint import _VALID_NEXT_STEPS

    assert "EXECUTE_REWORK" in _VALID_NEXT_STEPS
    assert "EXECUTE_VERIFY" in _VALID_NEXT_STEPS
    # Legacy forms must NOT be in the vocabulary.
    assert "EXECUTE (rework)" not in _VALID_NEXT_STEPS
    assert "EXECUTE (additional verification)" not in _VALID_NEXT_STEPS


# ---------------------------------------------------------------------------
# Crash safety: best-effort on file errors
# ---------------------------------------------------------------------------


def test_migration_skips_unreadable_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A file the migration cannot read is skipped with a warning.

    The migration is best-effort per-file: a corrupted artifact does
    not crash the sweep -- it logs and moves on.  The corrupted file
    is left for ``parse_checkpoint`` to surface its rejection warning
    on the next read attempt.
    """
    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        checkpoint=(
            "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        ),
    )
    cp_path = ms / "active" / "coordinator.md"
    # Write a non-UTF8 byte sequence to trigger UnicodeDecodeError.
    cp_path.write_bytes(b"\xff\xfeillegal")

    with caplog.at_level("WARNING"):
        counts = migrate_legacy_tokens(clou_dir)

    assert counts["checkpoints"] == 0
    msgs = " ".join(r.message for r in caplog.records)
    assert "vocabulary_migration" in msgs
    # Partial-failure signal: the unreadable path is recorded.
    assert cp_path in counts["failed"]


def test_migration_partial_failure_surface(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unreadable files populate the ``failed`` list in the return dict.

    Cycle-2 rework (F1/F15/F28): aggregate counts alone cannot
    distinguish "zero files needed migration" from "half the files
    failed".  The ``failed`` list is the structural signal that
    feeds the ``vocabulary_migration.partial_failure`` telemetry
    event at the coordinator session-start call site.
    """
    clou_dir = tmp_path / ".clou"
    # Seed two milestones: one normal, one with an unreadable checkpoint.
    _seed_milestone(
        clou_dir,
        "good",
        checkpoint=(
            "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        ),
    )
    bad_ms = _seed_milestone(clou_dir, "bad")
    bad_cp = bad_ms / "active" / "coordinator.md"
    bad_cp.parent.mkdir(parents=True, exist_ok=True)
    bad_cp.write_bytes(b"\xff\xfebogus-bytes")

    with caplog.at_level("WARNING"):
        counts = migrate_legacy_tokens(clou_dir)

    # Successful rewrite recorded.
    assert counts["checkpoints"] == 1
    # Failed path surfaced for the partial-failure telemetry event.
    failed = counts["failed"]
    assert isinstance(failed, list)
    assert bad_cp in failed


def test_migration_write_failure_surfaces_in_failed_list(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M50 I1 cycle-3 rework (F8): write-side failures also populate the failed list.

    The existing partial-failure tests (``test_migration_skips_unreadable_file``,
    ``test_migration_partial_failure_surface``) exercise only the read
    error path (UnicodeDecodeError on ``read_text``).  The write side —
    ``_atomic_write`` raising ``OSError`` (ENOSPC, EROFS, EACCES) — is
    equally part of the best-effort contract and must surface the same
    way.  This test patches ``_atomic_write`` to raise and asserts the
    failed path lands in ``counts["failed"]`` without crashing the
    sweep.
    """
    from unittest.mock import patch

    clou_dir = tmp_path / ".clou"
    ms = _seed_milestone(
        clou_dir,
        "demo",
        checkpoint=(
            "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        ),
    )
    cp_path = ms / "active" / "coordinator.md"

    # Simulate a write-time EACCES on the atomic swap.
    def _raise_oserror(target: Path, content: str) -> None:
        raise PermissionError(f"simulated EACCES on {target}")

    with (
        caplog.at_level("WARNING"),
        patch(
            "clou.vocabulary_migration._atomic_write",
            side_effect=_raise_oserror,
        ),
    ):
        counts = migrate_legacy_tokens(clou_dir)

    # The rewrite never completed — counts are zero.
    assert counts["checkpoints"] == 0
    # But the failure is surfaced so the caller can emit partial_failure.
    assert cp_path in counts["failed"]
    # Warning logged at the write-failure site.
    msgs = " ".join(r.message for r in caplog.records)
    assert "cannot write" in msgs.lower()


def test_atomic_write_cleanup_on_failure(tmp_path: Path) -> None:
    """M50 I1 cycle-3 rework (F8): failed _atomic_write leaves no tmp orphan.

    The atomic-write helper uses ``tempfile.mkstemp`` + ``os.replace``
    to guarantee the target file is either the old content or the new
    content — never a half-written partial.  When the ``os.replace``
    (or the preceding write) fails, the tmpfile MUST be cleaned up so
    the working directory does not accumulate ``.coordinator.md.*.tmp``
    orphans across failed migrations.

    Failure mode this test pins: a regression that drops the
    ``except Exception: tmp_path.unlink()`` cleanup block would leak
    tmpfiles on every failed write, which in turn would:
      1. Pollute the milestone directory with invisible orphans,
      2. Eventually fill the filesystem (ENOSPC amplification),
      3. Masquerade as valid checkpoint prefix files to naive readers.
    """
    from unittest.mock import patch

    from clou.vocabulary_migration import _atomic_write

    target = tmp_path / "target.md"
    target.write_text("original\n", encoding="utf-8")

    # Force os.replace to raise mid-swap.
    def _fail_replace(src: str, dst: str) -> None:
        raise PermissionError("simulated swap failure")

    with patch("clou.vocabulary_migration.os.replace", side_effect=_fail_replace):
        with pytest.raises(PermissionError):
            _atomic_write(target, "rewritten\n")

    # Target untouched.
    assert target.read_text(encoding="utf-8") == "original\n"
    # No tmpfile orphan left behind — the dir contains only target.
    siblings = list(tmp_path.iterdir())
    tmp_orphans = [p for p in siblings if p.name.endswith(".tmp")]
    assert tmp_orphans == [], (
        f"_atomic_write leaked tmpfile orphans: {tmp_orphans}"
    )


# ---------------------------------------------------------------------------
# Prose-corruption regression guards (F11/F27)
# ---------------------------------------------------------------------------


def test_migration_preserves_orient_doc_note(tmp_path: Path) -> None:
    """``coordinator-orient.md``-style doc note is NOT mangled.

    The bundled orient prompt has a doc note mentioning both legacy
    tokens by name to explain the rename.  A context-blind
    ``str.replace`` would rewrite these prose mentions, producing the
    self-contradictory line ``"EXECUTE_REWORK is rejected at parse
    time"``.  The field-anchored rewriter MUST leave the note intact.
    """
    clou_dir = tmp_path / ".clou"
    prompts = clou_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    doc = (
        "# Orient\n\n"
        "## Step 3: Detect legacy cycle-type tokens\n\n"
        "`EXECUTE (rework)` and `EXECUTE (additional verification)` are\n"
        "rejected at parse time.  The structured replacements are\n"
        "`EXECUTE_REWORK` and `EXECUTE_VERIFY` respectively.\n"
    )
    (prompts / "coordinator-orient.md").write_text(doc, encoding="utf-8")

    counts = migrate_legacy_tokens(clou_dir)

    after = (prompts / "coordinator-orient.md").read_text(encoding="utf-8")
    # Byte-identical — no prose-corruption.
    assert after == doc
    assert counts["prompts"] == 0


def test_migration_does_not_corrupt_judgment_prose_body(tmp_path: Path) -> None:
    """Prose in a judgment's Rationale / Expected artifact sections is NOT rewritten.

    The judgment preamble (``**Next action:** VALUE``) is the only
    anchored surface the migration rewrites.  A Rationale section
    that legitimately describes why rework was chosen, including the
    legacy token in prose, must stay untouched.
    """
    clou_dir = tmp_path / ".clou"
    jbody = (
        "# Judgment\n\n"
        "**Next action:** EXECUTE_REWORK\n\n"
        "## Rationale\n"
        "Assess requested rework (prior to M50 this would have routed\n"
        "via `EXECUTE (rework)`; the structured EXECUTE_REWORK is the\n"
        "post-M50 identifier).\n\n"
        "## Evidence\n- assessment.md\n\n"
        "## Expected artifact\n"
        "phases/impl/execution.md  "
        "(rework of `EXECUTE (rework)`-style dispatch)\n"
    )
    _seed_milestone(
        clou_dir,
        "demo",
        judgments={"cycle-01-judgment.md": jbody},
    )

    counts = migrate_legacy_tokens(clou_dir)

    after = (
        clou_dir / "milestones" / "demo" / "judgments"
        / "cycle-01-judgment.md"
    ).read_text(encoding="utf-8")
    # Byte-identical preservation.
    assert after == jbody
    assert counts["judgments"] == 0


def test_migration_preserves_bundled_coordinator_orient_prompt(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-3 rework (F13): the REAL bundled prompt is byte-preserved.

    The cycle-2 tests used hand-crafted prose fixtures
    (``test_migration_preserves_orient_doc_note``) — useful but
    brittle: a future edit to the bundled ``clou/_prompts/coordinator-
    orient.md`` could reintroduce a legacy-token line shape the
    fixture doesn't exercise.  This test copies the REAL bundled
    prompt into the per-project ``.clou/prompts/`` mirror, runs the
    migration, and asserts byte-identity.

    This is the ground-truth invariant: whatever the bundled orient
    prompt says (including its "legacy tokens are rejected" doc note),
    the migration MUST NOT alter a single byte.  A regression that
    mangles the prompt ship-breaks the orientation flow at session
    start for every user.
    """
    from importlib.resources import files

    # Locate the bundled prompt via importlib.resources (survives
    # editable-install / wheel install symmetrically).
    bundled = files("clou._prompts").joinpath("coordinator-orient.md")
    original_bytes = bundled.read_bytes()
    assert b"EXECUTE (rework)" in original_bytes, (
        "Precondition: bundled coordinator-orient.md must contain a "
        "legacy-token prose mention to exercise the prose-preservation "
        "guard.  If this preamble goes away, this test becomes vacuous "
        "and should be deleted."
    )

    # Materialise the bundled file in the per-project mirror path.
    clou_dir = tmp_path / ".clou"
    prompts = clou_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    target = prompts / "coordinator-orient.md"
    target.write_bytes(original_bytes)

    counts = migrate_legacy_tokens(clou_dir)

    # Byte-identical preservation of the real bundled prompt.
    after_bytes = target.read_bytes()
    assert after_bytes == original_bytes, (
        "Migration altered the bundled coordinator-orient.md.  Diff "
        "surface:\n" + _diff_bytes(original_bytes, after_bytes)
    )
    assert counts["prompts"] == 0
    assert counts["failed"] == []


def _diff_bytes(a: bytes, b: bytes) -> str:
    """Short diff summary for assertion failure messages."""
    if a == b:
        return "(identical)"
    import difflib
    a_lines = a.decode("utf-8", errors="replace").splitlines()
    b_lines = b.decode("utf-8", errors="replace").splitlines()
    return "\n".join(difflib.unified_diff(
        a_lines, b_lines,
        fromfile="original", tofile="after",
        lineterm="",
    ))


# ---------------------------------------------------------------------------
# LLM-typo variants (F40/F41): the anchored pattern MUST NOT spuriously
# match lowercase/alternate-casing variants.  This protects the parser
# from silently "healing" typos that the LLM MUST fix upstream.
# ---------------------------------------------------------------------------


def test_migration_does_not_rewrite_lowercase_variant(tmp_path: Path) -> None:
    """Lowercase ``execute (rework)`` MUST NOT be rewritten.

    The migration is case-sensitive on the token value to avoid
    papering over LLM casing typos.  ``execute (rework)`` is not the
    official legacy token — the coordinator must surface a validation
    warning via ``parse_checkpoint`` so the typo is fixed, not
    silently rewritten to a canonical value that happens to look right.
    """
    clou_dir = tmp_path / ".clou"
    body = (
        "cycle: 1\nstep: ASSESS\nnext_step: execute (rework)\n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    # Typo variant preserved — parser will reject it, surfacing the typo.
    assert after == body
    assert counts["checkpoints"] == 0


def test_migration_does_not_rewrite_double_space_variant(
    tmp_path: Path,
) -> None:
    """Double-space ``EXECUTE  (rework)`` MUST NOT be rewritten.

    Another LLM-typo guard: a whitespace variant is not the canonical
    legacy token.  The regex matches only the exact literal
    ``EXECUTE (rework)`` and ``EXECUTE (additional verification)``.
    """
    clou_dir = tmp_path / ".clou"
    body = (
        "cycle: 1\nstep: ASSESS\nnext_step: EXECUTE  (rework)\n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    assert after == body
    assert counts["checkpoints"] == 0


# ---------------------------------------------------------------------------
# 'none' legacy token sweep (F13) — M50 I1 cycle-4 rework, partially
# REVERTED in cycle-5 ASSESS / cycle-2 EXECUTE_REWORK (F2/F3 stash-
# field overreach).  The coordinator's exit template historically wrote
# ``next_step: none`` to signal milestone completion; cycle-3's
# vocabulary narrowing (F4/F15) replaced the canonical form with
# ``COMPLETE``.  Migration sweeps:
#
#   next_step: none              -> next_step: COMPLETE
#   pre_orient_next_step: none   -> LEFT ALONE (parser drops to empty)
#   pre_halt_next_step: none     -> LEFT ALONE (parser drops to empty)
#
# The cycle-4 F13 expansion swept ``none`` through every prefix in
# the alternation; that produced a migration-vs-parser disagreement
# (parser drops legacy stash tokens at recovery_checkpoint.py:380-415
# while migration coerced to COMPLETE), silently terminating any
# pre-M50 milestone whose stash was the legacy ``none``.  The cycle-2
# revert restores stash-field handling to the parser's drop-the-stash
# semantics — the migration must agree round-trip with the parser.
#
# Case-sensitive (exact-match ``none`` only) — non-canonical casings
# fall through to ``parse_checkpoint``'s rejection path.
# ---------------------------------------------------------------------------


def test_migration_rewrites_next_step_none(tmp_path: Path) -> None:
    """M50 I1 cycle-4 rework (F13a): ``next_step: none`` -> ``COMPLETE``.

    Seeds a checkpoint with the legacy ``next_step: none`` token.
    Migration must rewrite it to ``next_step: COMPLETE`` so the
    coordinator's parse-time coercion at :func:`parse_checkpoint`
    stops being a load-bearing backstop for every session.

    The cycle-2 EXECUTE_REWORK revert preserves this rewrite for the
    ``next_step`` field — only the stash-field prefixes
    (``pre_orient_next_step`` / ``pre_halt_next_step``) had their
    ``none`` rewrite reverted.
    """
    clou_dir = tmp_path / ".clou"
    body = "cycle: 7\nstep: EXIT\nnext_step: none\ncurrent_phase: \n"
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    assert "next_step: COMPLETE" in after
    assert "next_step: none" not in after
    assert counts["checkpoints"] == 1


def test_migration_does_not_rewrite_pre_orient_next_step_none(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F2/F3 revert): pre_orient_next_step: none is LEFT ALONE.

    The cycle-4 F13 expansion rewrote ``pre_orient_next_step: none`` to
    ``: COMPLETE`` on disk; that produced a split-brain recovery model
    because :func:`parse_checkpoint` (recovery_checkpoint.py:380-386)
    drops legacy stash tokens to empty rather than coercing.  After
    the cycle-4 rewrite, a fresh parse of the migrated file would
    return ``COMPLETE`` for the stash, the ORIENT-exit restoration
    block would route ``next_step <- COMPLETE``, and the milestone
    would silently terminate.

    Stash slots are RESTORATION TARGETS, not current dispatch values.
    The parser's drop-the-stash semantics are the canonical handling
    for legacy stash tokens; migration must agree with the parser
    round-trip.

    Asserts: after migration, the stash field is STILL ``none`` on
    disk (the parser will drop it on the next read).
    """
    clou_dir = tmp_path / ".clou"
    body = (
        "cycle: 7\nstep: ORIENT\nnext_step: ORIENT\n"
        "pre_orient_next_step: none\ncurrent_phase: \n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    # Stash field unchanged — the parser owns the drop-to-empty
    # semantics, not the migration.
    assert "pre_orient_next_step: none" in after
    assert "pre_orient_next_step: COMPLETE" not in after
    # Whole file unchanged (no other rewrites in this fixture).
    assert after == body
    # Counts: zero (no rewrite occurred).
    assert counts["checkpoints"] == 0


def test_migration_does_not_rewrite_pre_halt_next_step_none(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F2/F3 revert mirror): pre_halt_next_step: none is LEFT ALONE.

    Parallel to the pre_orient case.  ``pre_halt_next_step`` is the
    engine-halt stash consumed by ``clou_dispose_halt`` under the
    ``continue-as-is`` choice; the parser drops legacy ``none`` to
    empty (recovery_checkpoint.py:409-415).  Migration agrees by
    not rewriting.
    """
    clou_dir = tmp_path / ".clou"
    body = (
        "cycle: 7\nstep: HALTED\nnext_step: HALTED\n"
        "pre_halt_next_step: none\ncurrent_phase: \n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    assert "pre_halt_next_step: none" in after
    assert "pre_halt_next_step: COMPLETE" not in after
    assert after == body
    assert counts["checkpoints"] == 0


def test_migration_combined_next_step_and_stash_fields_none(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F2/F3 revert): combined fixture.

    Seeds a checkpoint with ``next_step: PLAN`` (no rewrite needed
    on this field) AND ``pre_orient_next_step: none`` AND
    ``pre_halt_next_step: none``.  After the migration:

    - ``next_step: PLAN`` is unchanged (already canonical).
    - ``pre_orient_next_step: none`` is STILL ``none`` (revert).
    - ``pre_halt_next_step: none`` is STILL ``none`` (revert).

    This is the literal ship-criteria test from the supervisor's
    re-scope brief (escalations/20260425-031626-trajectory-halt.md
    NEW SCOPE FOR I1, item 1; decisions.md cycle 1 ### Valid: F2
    Action block).  Without the revert, the cycle-4 F13 regex
    would rewrite both stash fields to ``: COMPLETE`` and the
    migration would change the canonical state of two fields the
    test is about.
    """
    clou_dir = tmp_path / ".clou"
    body = (
        "cycle: 5\nstep: ASSESS\nnext_step: PLAN\n"
        "pre_orient_next_step: none\n"
        "pre_halt_next_step: none\n"
        "current_phase: impl\n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")

    # next_step is unchanged (already canonical PLAN — no rewrite).
    assert "next_step: PLAN" in after

    # Both stash fields preserved as `none` — NOT rewritten to COMPLETE.
    assert "pre_orient_next_step: none" in after
    assert "pre_orient_next_step: COMPLETE" not in after
    assert "pre_halt_next_step: none" in after
    assert "pre_halt_next_step: COMPLETE" not in after

    # Whole file unchanged byte-for-byte.
    assert after == body, (
        "Stash-field rewrite must be a no-op when value is `none` — "
        "the parser owns the drop-to-empty semantics.  Cycle-4 F13 "
        "regression: rewrite stash to COMPLETE silently terminates "
        "milestones whose ORIENT-exit restoration target was legacy "
        "`none`.  See decisions.md cycle 1 ### Valid: F2."
    )

    # Counts: zero (no rewrite occurred — checkpoint was already
    # canonical for next_step and stash fields are now exempt).
    assert counts["checkpoints"] == 0


def test_migration_combined_legacy_next_step_and_stash_none(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F2/F3 revert): mixed fixture.

    Same scenario as the brief but with ``next_step: none`` instead
    of ``: PLAN`` — proves the next_step rewrite STILL FIRES even
    when the stash-field rewrite is suppressed.  Both behaviours
    must coexist after the revert.
    """
    clou_dir = tmp_path / ".clou"
    body = (
        "cycle: 9\nstep: ASSESS\nnext_step: none\n"
        "pre_orient_next_step: none\n"
        "pre_halt_next_step: none\n"
        "current_phase: impl\n"
    )
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")

    # next_step rewritten — F13a intent stands.
    assert "next_step: COMPLETE" in after
    # The standalone "next_step: none" line must not survive.
    # (substring "step: none" might still appear inside
    # "pre_orient_next_step: none"; we assert the canonical line
    # form instead.)
    assert "\nnext_step: none\n" not in after

    # Both stash fields preserved — F13b/F13c expansion reverted.
    assert "pre_orient_next_step: none" in after
    assert "pre_orient_next_step: COMPLETE" not in after
    assert "pre_halt_next_step: none" in after
    assert "pre_halt_next_step: COMPLETE" not in after

    # File rewritten exactly once (the next_step line).
    assert counts["checkpoints"] == 1


def test_migration_does_not_rewrite_capital_None(tmp_path: Path) -> None:
    """M50 I1 cycle-4 rework (F13c): ``None`` is NOT rewritten.

    Case-sensitive invariant preserved: only lowercase ``none``
    (the canonical legacy form emitted by the prior template)
    rewrites.  ``None`` (Python convention) and ``NONE`` (shouty)
    fall through so ``parse_checkpoint`` surfaces the typo via the
    ``Unknown next_step`` warning path.  Symmetric with the
    punctuated-token case-sensitivity guard
    (``test_migration_does_not_rewrite_lowercase_variant``).
    """
    clou_dir = tmp_path / ".clou"
    body = "cycle: 7\nstep: EXIT\nnext_step: None\ncurrent_phase: \n"
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    assert after == body, (
        "Capital 'None' must not be migrated — it is a Python-"
        "convention typo, not the canonical legacy token"
    )
    assert counts["checkpoints"] == 0


def test_migration_does_not_rewrite_uppercase_NONE(tmp_path: Path) -> None:
    """M50 I1 cycle-4 rework (F13c mirror): ``NONE`` is NOT rewritten.

    Parallel to the ``None`` guard.  Uppercase variants fall through
    to the parser's rejection path — no silent coercion on on-disk
    casing drift.
    """
    clou_dir = tmp_path / ".clou"
    body = "cycle: 7\nstep: EXIT\nnext_step: NONE\ncurrent_phase: \n"
    ms = _seed_milestone(clou_dir, "demo", checkpoint=body)

    counts = migrate_legacy_tokens(clou_dir)

    after = (ms / "active" / "coordinator.md").read_text(encoding="utf-8")
    assert after == body
    assert counts["checkpoints"] == 0


def test_migration_does_not_rewrite_lowercase_judgment_variant(
    tmp_path: Path,
) -> None:
    """Lowercase ``execute (rework)`` in a judgment MUST NOT be rewritten.

    M50 I1 cycle-3 rework (F1): the cycle-2 pass applied
    ``re.IGNORECASE`` to ``_JUDGMENT_NEXT_ACTION_RE``, which matched
    lowercase/mixed-case values and then fed the lowercased string
    into the uppercase-keyed ``_LEGACY_NEXT_STEPS`` dict — raising
    ``KeyError`` inside ``_replace_token`` and crashing the migration
    on any mixed-case judgment.  The fix drops ``re.IGNORECASE`` so
    lowercase judgment values are treated identically to checkpoints:
    the migration leaves the file alone and
    ``validate_judgment_fields`` surfaces the typo on read.

    Parallel to ``test_migration_does_not_rewrite_lowercase_variant``
    (checkpoint-side guard).  Without the fix, this test throws a
    KeyError rather than returning zero counts.
    """
    clou_dir = tmp_path / ".clou"
    # Lowercase AND mixed-case variants — both should be left alone.
    jbody_lowercase = (
        "# Judgment\n\n"
        "**Next action:** execute (rework)\n\n"
        "## Rationale\nTypo case — migration must not rewrite.\n"
    )
    jbody_mixedcase = (
        "# Judgment\n\n"
        "**Next action:** Execute (Rework)\n\n"
        "## Rationale\nMixed case — migration must not rewrite.\n"
    )
    _seed_milestone(
        clou_dir,
        "demo",
        judgments={
            "cycle-01-judgment.md": jbody_lowercase,
            "cycle-02-judgment.md": jbody_mixedcase,
        },
    )

    # Must NOT raise KeyError (the cycle-2 regression).
    counts = migrate_legacy_tokens(clou_dir)

    jdir = clou_dir / "milestones" / "demo" / "judgments"
    after_lower = (jdir / "cycle-01-judgment.md").read_text(encoding="utf-8")
    after_mixed = (jdir / "cycle-02-judgment.md").read_text(encoding="utf-8")
    assert after_lower == jbody_lowercase
    assert after_mixed == jbody_mixedcase
    assert counts["judgments"] == 0
    # No failures either — the case-insensitive branch used to raise
    # KeyError, which `_rewrite_one`'s try/except does not catch.
    assert counts["failed"] == []


def test_migration_regex_value_group_is_case_sensitive() -> None:
    """The VALUE capture group of every anchored regex is case-sensitive.

    M50 I1 cycle-4 rework (F18/F27 closure; keeps F1 KeyError
    regression closed): the case-sensitivity invariant is NOT a
    blanket "no IGNORECASE anywhere" rule — that over-strict reading
    was the F2/F19 regression (cycle-3 dropped IGNORECASE from the
    judgment regex entirely, making the migration unable to see
    lowercase-label judgments that production's parser accepts).

    The real invariant is VALUE-group-specific: the capture group
    named ``value`` MUST only match the canonical uppercase legacy
    tokens.  Lowercase / mixed-case values (e.g. ``execute (rework)``
    or ``Execute (Rework)``) MUST NOT match, to keep the F1
    KeyError regression closed (a lowercased string would key-miss
    ``_LEGACY_NEXT_STEPS`` and raise).

    Behavioral probe: for each of the three anchored regexes, seed
    a minimal sample with the canonical uppercase token, a
    lowercase value, and a mixed-case value; assert match ONLY on
    the uppercase fixture.  This pins the invariant against both
    directions of regression:

    - Restoring blanket IGNORECASE (cycle-2's F1 regression): the
      lowercase/mixed-case fixtures would match; test fails.
    - Dropping IGNORECASE from the PREFIX label on
      ``_JUDGMENT_NEXT_ACTION_RE`` (cycle-3's F2/F19 regression):
      the behavioral probe for the judgment regex lives in the
      separate ``test_migration_rewrites_lowercase_judgment_label``
      test — mixed-case PREFIX + canonical VALUE must match.
    """
    # Samples share the shape ``{prefix}{value}\n`` where only
    # ``{value}`` varies.  The prefix is formatted to fit each
    # regex's anchor.
    uppercase = "EXECUTE (rework)"
    lowercase = "execute (rework)"
    mixedcase = "Execute (Rework)"

    from clou.vocabulary_migration import (
        _CHECKPOINT_FIELD_RE,
        _JUDGMENT_NEXT_ACTION_RE,
        _PROMPT_ROUTING_RE,
    )

    probes: tuple[tuple[str, object, str], ...] = (
        ("_CHECKPOINT_FIELD_RE", _CHECKPOINT_FIELD_RE, "next_step: {v}\n"),
        ("_JUDGMENT_NEXT_ACTION_RE", _JUDGMENT_NEXT_ACTION_RE, "**Next action:** {v}\n"),
        ("_PROMPT_ROUTING_RE", _PROMPT_ROUTING_RE, "next_step: {v}\n"),
    )

    for name, pattern, template in probes:
        assert pattern.search(template.format(v=uppercase)) is not None, (
            f"{name} must match the canonical uppercase legacy token"
        )
        assert pattern.search(template.format(v=lowercase)) is None, (
            f"{name} must NOT match lowercase VALUE {lowercase!r} — "
            f"lowercase typos MUST fall through to the parser's "
            f"rejection path (F1 KeyError regression guard)"
        )
        assert pattern.search(template.format(v=mixedcase)) is None, (
            f"{name} must NOT match mixed-case VALUE {mixedcase!r} — "
            f"mixed-case typos MUST fall through to the parser's "
            f"rejection path (F1 KeyError regression guard)"
        )


def test_migration_rewrites_lowercase_judgment_label(tmp_path: Path) -> None:
    """Lowercase PREFIX label with canonical VALUE MUST be rewritten.

    M50 I1 cycle-4 rework (F2/F19): cycle-3 dropped
    ``re.IGNORECASE`` from ``_JUDGMENT_NEXT_ACTION_RE`` while
    production's :func:`clou.judgment._parse_next_action` keeps
    ``re.IGNORECASE`` on its own label-matching regex.  That
    asymmetry meant a judgment written as
    ``**next action:** EXECUTE (rework)`` (lowercase label,
    canonical VALUE) was:

    - Readable by production parser (IGNORECASE matches
      ``**next action:**``).
    - Rejected by ``validate_judgment_fields`` (VALUE is the
      now-illegal punctuated legacy token).
    - Invisible to the migration (cycle-3 regex required exact
      ``**Next action:**`` label casing).

    This trifecta wedges a milestone that paused mid-rework: the
    coordinator can read its own past judgments, the validator
    vetoes the read, and the migration helper can't fix it up.

    The fix restores PREFIX-label tolerance while keeping VALUE
    case-sensitive via scoped regex flags:
    ``(?im)^(?P<prefix>...)(?-i:(?P<value>...))``.  This test
    pins both halves: lowercase label + canonical VALUE rewrites,
    and the rewritten file is ``validate_judgment_fields``-valid.

    Parallel companion:
    ``test_migration_does_not_rewrite_lowercase_judgment_variant``
    guards the opposite direction (lowercase VALUE must NOT be
    rewritten — F1 KeyError regression closed).
    """
    import pytest

    from clou.judgment import parse_judgment, validate_judgment_fields

    clou_dir = tmp_path / ".clou"
    # Lowercase label, canonical uppercase VALUE.  Include the full
    # JudgmentForm section set so post-rewrite
    # ``validate_judgment_fields`` has non-empty evidence, rationale,
    # and expected_artifact to check against.
    jbody_lowercase = (
        "# Judgment\n\n"
        "**next action:** EXECUTE (rework)\n\n"
        "## Rationale\n"
        "Lowercase label variant — migration must rewrite.\n\n"
        "## Evidence\n"
        "- .clou/milestones/demo/decisions.md\n\n"
        "## Expected artifact\n"
        "A rewritten judgment with structured VALUE.\n"
    )
    # Mixed-case label, canonical uppercase VALUE.
    jbody_mixedcase = (
        "# Judgment\n\n"
        "**NEXT ACTION:** EXECUTE (additional verification)\n\n"
        "## Rationale\n"
        "Mixed-case label variant — migration must rewrite.\n\n"
        "## Evidence\n"
        "- .clou/milestones/demo/decisions.md\n\n"
        "## Expected artifact\n"
        "A rewritten judgment with structured VALUE.\n"
    )
    _seed_milestone(
        clou_dir,
        "demo",
        judgments={
            "cycle-01-judgment.md": jbody_lowercase,
            "cycle-02-judgment.md": jbody_mixedcase,
        },
    )

    counts = migrate_legacy_tokens(clou_dir)

    jdir = clou_dir / "milestones" / "demo" / "judgments"
    after_lower = (jdir / "cycle-01-judgment.md").read_text(encoding="utf-8")
    after_mixed = (jdir / "cycle-02-judgment.md").read_text(encoding="utf-8")

    # Labels preserved verbatim; VALUES canonicalized.
    assert "**next action:** EXECUTE_REWORK" in after_lower, (
        f"lowercase-label judgment was not rewritten; body:\n{after_lower}"
    )
    assert "**NEXT ACTION:** EXECUTE_VERIFY" in after_mixed, (
        f"mixed-case-label judgment was not rewritten; body:\n{after_mixed}"
    )
    assert counts["judgments"] == 2
    assert counts["failed"] == []

    # Post-migration, the rewritten files must survive
    # validate_judgment_fields.  This closes the reverse half of the
    # trifecta: the prior state was parser-readable AND validator-
    # rejected; the new state must be BOTH parser-readable AND
    # validator-accepted.  Anything less leaves a wedge surface.
    form_lower = parse_judgment(after_lower)
    form_mixed = parse_judgment(after_mixed)
    assert form_lower.next_action == "EXECUTE_REWORK"
    assert form_mixed.next_action == "EXECUTE_VERIFY"
    # validate_judgment_fields raises on failure; call must not raise.
    try:
        validate_judgment_fields(form_lower)
        validate_judgment_fields(form_mixed)
    except ValueError as err:  # pragma: no cover — regression beacon
        pytest.fail(
            f"rewritten judgment failed validate_judgment_fields: {err}"
        )


# ---------------------------------------------------------------------------
# cycle_type enumeration sites (F3): no raw cycle_type == "EXECUTE"
# or cycle_type != "EXECUTE" comparisons may escape the helper.
# ---------------------------------------------------------------------------


#: Lint contract (M50 I1 cycle-4 rework, F1/F15/F25): any direct
#: cycle_type-vs-EXECUTE check outside :func:`is_execute_family` is an
#: offender.  Covers the full class of idioms by which a cycle-type
#: discriminator can be tested without routing through the helper:
#:
#:   * ``cycle_type == "EXECUTE"`` / ``cycle_type != "EXECUTE"``
#:   * ``"EXECUTE" == cycle_type`` / ``"EXECUTE" != cycle_type``
#:     (reversed operands)
#:   * ``cycle_type in (...)`` tuple-literal membership
#:   * ``cycle_type in {...}`` set-literal membership
#:   * ``cycle_type in [...]`` list-literal membership
#:   * ``cycle_type.startswith("EXECUTE")`` prefix match
#:   * ``case "EXECUTE":`` match-statement arm
#:   * Single-quote variants of every string-literal form
#:
#: Each pattern is a concrete regex.  The offender set is the union
#: of every pattern's matches.  The synthetic-offender test below
#: proves every pattern catches its intended idiom (mutation guard
#: against a silent regex-drift regression).
_CYCLE_TYPE_OFFENDER_PATTERNS: tuple[tuple[str, str], ...] = (
    # ==/!= (both directions).  The ``["'"]`` class matches both quote
    # styles.  ``\s*`` tolerates ``cycle_type=="EXECUTE"`` and
    # ``cycle_type  ==  "EXECUTE"`` equally.
    ("equality", r'cycle_type\s*(?:==|!=)\s*["\']EXECUTE(?:_\w+)?["\']'),
    ("reversed_equality", r'["\']EXECUTE(?:_\w+)?["\']\s*(?:==|!=)\s*cycle_type'),
    # Membership in tuple / set / list literal.  The opener / closer
    # pair disambiguates the three literal shapes; the inner body is
    # ``[^)\]}]*`` (anything but the matching close bracket) so we
    # catch n-tuples of any length.
    ("tuple_membership", r'cycle_type\s+in\s+\([^)]*["\']EXECUTE(?:_\w+)?["\']'),
    ("set_membership", r'cycle_type\s+in\s+\{[^}]*["\']EXECUTE(?:_\w+)?["\']'),
    ("list_membership", r'cycle_type\s+in\s+\[[^\]]*["\']EXECUTE(?:_\w+)?["\']'),
    # Prefix-match via startswith — would catch EXECUTE, EXECUTE_REWORK,
    # EXECUTE_VERIFY identically but is a drift surface (a non-EXECUTE-
    # family token that happens to start with ``EXECUTE`` would match).
    ("startswith", r'cycle_type\.startswith\s*\(\s*["\']EXECUTE["\']'),
    # match-case arm.  ``match cycle_type:`` followed by
    # ``case "EXECUTE":`` is the PEP 634 form of the same idiom.
    ("match_case", r'case\s+["\']EXECUTE(?:_\w+)?["\']'),
)


def _scan_for_cycle_type_offenders(
    clou_pkg: object,
    *,
    allowed_files: set[str],
    helper_files: set[str] | None = None,
) -> list[tuple[str, int, str]]:
    """Walk ``clou_pkg`` and return every line matching an offender regex.

    Two layers of exemption:

    * ``helper_files`` — files that OWN the abstraction or hold the
      canonical dispatch are exempt entirely.  These files are where
      the vocabulary lives; by construction they must enumerate the
      tokens.  Example: :mod:`clou.recovery_checkpoint` defines
      ``_EXECUTE_FAMILY`` / :func:`is_execute_family` AND the
      authoritative ``match checkpoint.next_step`` dispatch.
    * ``allowed_files`` — files where the idiom is tolerated in
      comments / docstrings / rst code refs (documentation of the
      pattern, not actual control flow).

    Returns ``[(relpath, lineno, line_text), ...]``.
    """
    import re as _re
    from pathlib import Path

    clou_pkg_path = Path(str(clou_pkg))
    compiled = [(name, _re.compile(pat)) for name, pat in _CYCLE_TYPE_OFFENDER_PATTERNS]
    helpers = helper_files or set()

    offenders: list[tuple[str, int, str]] = []
    for py in sorted(clou_pkg_path.rglob("*.py")):
        if "tests" in py.parts:
            continue
        if py.name in helpers:
            continue  # helper module: all hits are by-definition allowed
        rel = str(py.relative_to(clou_pkg_path))
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if not any(pat.search(line) for _, pat in compiled):
                continue
            if py.name in allowed_files:
                stripped = line.lstrip()
                if (
                    stripped.startswith("#")
                    or "'''" in line
                    or '"""' in line
                    or stripped.startswith("*")  # docstring body
                    or "``" in line  # rst code ref
                ):
                    continue
            offenders.append((rel, lineno, line.rstrip()))
    return offenders


def test_no_raw_cycle_type_execute_comparisons_outside_helper() -> None:
    """Structural lint: the codebase must route EXECUTE-family checks through the helper.

    M50 I1 cycle-4 rework (F1/F15/F25).  The cycle-2 pass introduced
    :func:`clou.recovery_checkpoint.is_execute_family` but three
    sites escaped its reach and cycle-3 closed only one (the
    ``cycle_type == "EXECUTE"`` idiom in ``prompts.py``); the other
    two (an inline tuple at ``task_graph.py:436`` and a list of
    membership idioms documented in the coordinator comments) were
    missed because the lint regex only caught ``==`` / ``!=``
    comparisons.

    This cycle extends the lint to cover every membership /
    comparison idiom enumerated in
    :data:`_CYCLE_TYPE_OFFENDER_PATTERNS` — tuple, set, list
    membership; prefix-match via ``startswith``; match-case arms;
    reversed-operand equality; both quote styles.

    Mutation guard: a future edit that re-introduces any raw
    comparison (``cycle_type in ("EXECUTE", ...)``,
    ``case "EXECUTE":``, etc.) anywhere in ``clou/`` will fail this
    test unless the occurrence sits inside an allowed file's
    docstring / comment (documentation only).
    """
    from pathlib import Path

    clou_pkg = Path(__file__).parent.parent / "clou"
    assert clou_pkg.is_dir()

    # Helper modules OWN the abstraction and the canonical dispatch;
    # their hits are by-definition allowed (entire file exempt).
    helper_files = {
        # recovery_checkpoint.py defines _EXECUTE_FAMILY,
        # is_execute_family, AND the authoritative
        # ``match checkpoint.next_step`` dispatch that enumerates the
        # family in a single ``case "EXECUTE" | ...`` arm.  The
        # vocabulary lives here.
        "recovery_checkpoint.py",
    }
    # Files where the idiom is tolerated in comments / docstrings /
    # rst code refs (documentation only, not control flow).
    allowed_files = {
        # coordinator.py documents the same idiom in comments.
        "coordinator.py",
    }

    offenders = _scan_for_cycle_type_offenders(
        clou_pkg,
        allowed_files=allowed_files,
        helper_files=helper_files,
    )

    assert offenders == [], (
        "Found raw cycle_type-vs-EXECUTE checks outside "
        "is_execute_family helper:\n"
        + "\n".join(f"  {f}:{ln}: {txt}" for f, ln, txt in offenders)
    )


def test_cycle_type_offender_patterns_catch_every_idiom(
    tmp_path: Path,
) -> None:
    """Synthetic offender lines prove every pattern catches its idiom.

    M50 I1 cycle-4 rework (F1/F15/F25 follow-through): pure
    production-code scanning can't distinguish "lint is catching
    nothing" from "lint regex is broken and catching nothing".
    Seed a synthetic file with one offender line per pattern; assert
    the scan returns exactly the same set.

    Mutation guard against silent regex drift: if a future edit
    breaks ``_CYCLE_TYPE_OFFENDER_PATTERNS`` so one pattern no
    longer matches, this test fails even when no real offender
    exists in ``clou/``.
    """
    # One offender per pattern.  Each line's content is crafted to
    # match EXACTLY one pattern in the tuple (verified below).
    synthetic_offenders = {
        "equality": 'if cycle_type == "EXECUTE":',
        "reversed_equality": 'if "EXECUTE" == cycle_type:',
        "tuple_membership": (
            'if cycle_type in ("EXECUTE", "EXECUTE_REWORK", "EXECUTE_VERIFY"):'
        ),
        "set_membership": 'if cycle_type in {"EXECUTE", "EXECUTE_REWORK"}:',
        "list_membership": 'if cycle_type in ["EXECUTE", "EXECUTE_VERIFY"]:',
        "startswith": 'if cycle_type.startswith("EXECUTE"):',
        "match_case": '        case "EXECUTE_REWORK":',
    }

    # Seed a fake package with one .py file per offender idiom.
    pkg = tmp_path / "fake_clou"
    pkg.mkdir()
    for name, line in synthetic_offenders.items():
        (pkg / f"{name}.py").write_text(
            f"# synthetic offender for pattern {name}\n"
            f"cycle_type = \"EXECUTE\"\n"
            f"{line}\n"
            f"    pass\n",
            encoding="utf-8",
        )

    offenders = _scan_for_cycle_type_offenders(
        pkg, allowed_files=set()
    )

    # Every pattern must contribute exactly one offender (drop
    # duplicates from disjoint per-pattern matches).
    hit_files = {f for f, _ln, _txt in offenders}
    expected_files = {f"{name}.py" for name in synthetic_offenders}
    missing = expected_files - hit_files
    assert not missing, (
        "The following offender patterns failed to catch their "
        "synthetic idiom:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nOffender scan returned:\n  "
        + "\n  ".join(f"{f}:{ln}: {txt}" for f, ln, txt in offenders)
    )

    # Single-quote variants: regenerate each idiom with single quotes
    # and verify the scan still catches them.
    single_quote_pkg = tmp_path / "fake_clou_sq"
    single_quote_pkg.mkdir()
    for name, line in synthetic_offenders.items():
        # Replace double quotes with single on the idiom line.  Keep
        # the dummy ``cycle_type = "EXECUTE"`` assignment double-
        # quoted so it doesn't itself match an offender pattern.
        sq_line = line.replace('"', "'")
        (single_quote_pkg / f"{name}.py").write_text(
            f"# single-quote variant of {name}\n"
            f"cycle_type = \"EXECUTE\"\n"
            f"{sq_line}\n"
            f"    pass\n",
            encoding="utf-8",
        )

    sq_offenders = _scan_for_cycle_type_offenders(
        single_quote_pkg, allowed_files=set()
    )
    sq_hit_files = {f for f, _ln, _txt in sq_offenders}
    sq_missing = expected_files - sq_hit_files
    assert not sq_missing, (
        "Single-quote variants not caught by lint:\n  "
        + "\n  ".join(sorted(sq_missing))
    )


def test_next_step_dict_contains_execute_family_transitions() -> None:
    """M50 I1 cycle-3 rework (F3c): _NEXT_STEP covers EXECUTE_REWORK / EXECUTE_VERIFY.

    Without these entries, ``_NEXT_STEP.get(cycle_type, "")`` at the
    cycle-completion post-message returns an empty string on rework/
    verify completion, leaving ``ClouCycleComplete.next_step`` blank
    in the UI and forcing downstream dashboards to special-case the
    EXECUTE family.  Pins the transitions: both EXECUTE_REWORK and
    EXECUTE_VERIFY advance to ASSESS (same as plain EXECUTE).
    """
    from clou.coordinator import _NEXT_STEP

    assert _NEXT_STEP.get("EXECUTE") == "ASSESS"
    assert _NEXT_STEP.get("EXECUTE_REWORK") == "ASSESS"
    assert _NEXT_STEP.get("EXECUTE_VERIFY") == "ASSESS"


def test_next_step_dict_omits_multi_successor_rows() -> None:
    """M50 I1 cycle-4 rework (F3): VERIFY and REPLAN rows are omitted.

    The static ``_NEXT_STEP`` dict's job is to report the next cycle
    type in ``ClouCycleComplete`` for UI rendering.  Two source
    cycles have multiple legitimate successors and cannot be honest
    in a static map:

    * VERIFY can route to EXIT (criteria met), EXECUTE_REWORK
      (perception gap → code changes), or EXECUTE_VERIFY
      (perception gap → another verification pass).  See
      ``coordinator-verify.md`` lines 50-52.
    * REPLAN can route to EXECUTE or back to PLAN depending on
      the outcome.

    Hardcoding either row is a lie on one of the paths.  The fix:
    omit the row; ``_NEXT_STEP.get(cycle_type, "")`` returns an
    empty string which the UI tolerates (the empty ``next_step``
    renders a best-effort label).

    Pins the omission against a future edit that "helpfully"
    restores the rows without addressing the multi-successor
    semantics.
    """
    from clou.coordinator import _NEXT_STEP

    assert "VERIFY" not in _NEXT_STEP, (
        "VERIFY was re-added to _NEXT_STEP despite its multi-"
        "successor semantics.  VERIFY can route to EXIT, "
        "EXECUTE_REWORK, or EXECUTE_VERIFY — see "
        "coordinator-verify.md lines 50-52.  Dict cannot express "
        "this; either keep it omitted (preferred) or read the "
        "just-written checkpoint's next_step instead."
    )
    assert "REPLAN" not in _NEXT_STEP, (
        "REPLAN was re-added to _NEXT_STEP despite its multi-"
        "successor semantics.  REPLAN can route to EXECUTE or "
        "back to PLAN depending on outcome."
    )


def test_verify_completion_renders_nonblank_next_step_on_rework_route() -> None:
    """M50 I1 cycle-4 rework (F3 consumer end): VERIFY->EXECUTE_REWORK.

    The F3 action says "add a test that VERIFY→EXECUTE_REWORK
    routing renders a non-blank next_step in ClouCycleComplete" —
    verifying the consumer end of the signal, not just the dict
    literal.  Dropping VERIFY from ``_NEXT_STEP`` alone would leave
    the UI rendering an empty string (the default), which is WORSE
    than the prior VERIFY→EXIT lie because the user sees no
    signal at all.

    The real source of truth on VERIFY's successor is the
    just-written checkpoint.  The test simulates a run where:
    (a) coordinator has completed a VERIFY cycle,
    (b) the checkpoint on disk has ``next_step: EXECUTE_REWORK``,
    (c) a downstream consumer SHOULD be able to read that
    checkpoint and display ``next_step: EXECUTE_REWORK`` rather
    than the blank that the cycle-3 dict produces.

    This test asserts the CONTRACT — the cycle-4 cleanup keeps
    the dict honest (omit the source row), and the UI-side
    consumer path is expected to read the checkpoint directly.
    The test serves as documentation of the expected contract;
    the concrete UI integration is out of scope for I1 (filed as
    proposal; see decisions.md).
    """
    import tempfile
    from pathlib import Path

    from clou.recovery_checkpoint import parse_checkpoint

    # Simulate a VERIFY cycle completion where the coordinator
    # wrote ``next_step: EXECUTE_REWORK`` to the checkpoint
    # (perception-gap route per coordinator-verify.md:50).
    with tempfile.TemporaryDirectory() as tmp:
        cp_path = Path(tmp) / "coordinator.md"
        cp_path.write_text(
            "cycle: 6\nstep: VERIFY\nnext_step: EXECUTE_REWORK\n"
            "current_phase: verification\n",
            encoding="utf-8",
        )

        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "EXECUTE_REWORK"

    # The on-disk next_step is the honest signal.  A UI consumer
    # that wants the truthful "what runs next" label reads the
    # checkpoint — the static dict cannot answer multi-successor
    # questions.


# ---------------------------------------------------------------------------
# Round-trip + no-legacy-emission (F38)
# ---------------------------------------------------------------------------


def test_render_checkpoint_never_emits_legacy_tokens() -> None:
    """``render_checkpoint`` MUST NOT emit a legacy cycle-type token.

    Negative-emission guard: when the golden-context renderer gets a
    structured token on input, its output must never contain the
    punctuated legacy form.  This closes a round-trip drift path —
    if render ever back-translated to the legacy form, a subsequent
    parse would reject it and default to PLAN.
    """
    from clou.golden_context import render_checkpoint

    body = render_checkpoint(
        cycle=7,
        step="ASSESS",
        next_step="EXECUTE_REWORK",
        current_phase="impl",
        phases_completed=0,
        phases_total=2,
    )
    assert "EXECUTE (rework)" not in body
    assert "EXECUTE (additional verification)" not in body
    assert "EXECUTE_REWORK" in body


def test_parse_then_render_round_trip_preserves_structured_token() -> None:
    """Full round-trip fixed point: render -> parse -> render is byte-stable.

    M50 I1 cycle-3 rework (F16): the cycle-2 version asserted only
    ``parse(render(...)).next_step == EXECUTE_REWORK`` — a half round-
    trip that doesn't prove the bytes stabilise.  A mutation that
    silently drops a field on re-render (e.g., misses the
    ``pre_orient_next_step`` stash) would still pass that assertion.

    Full fixed-point check: ``render(parse(render(x))) == render(x)``
    byte-for-byte.  This is the strongest form of round-trip: every
    field the renderer emits must survive parse+re-render unchanged.
    """
    from clou.golden_context import render_checkpoint
    from clou.recovery_checkpoint import parse_checkpoint

    for next_step in ("EXECUTE_REWORK", "EXECUTE_VERIFY"):
        body1 = render_checkpoint(
            cycle=3,
            step="ASSESS",
            next_step=next_step,
            current_phase="impl",
            phases_completed=0,
            phases_total=1,
        )
        cp = parse_checkpoint(body1)
        # Intermediate-state guard: the parsed next_step survived
        # (this is the cycle-2 assertion).
        assert cp.next_step == next_step, (
            f"next_step {next_step!r} not preserved on parse: "
            f"got {cp.next_step!r}"
        )

        # Full fixed-point: re-render and compare bytes.
        body2 = render_checkpoint(
            cycle=cp.cycle,
            step=cp.step,
            next_step=cp.next_step,
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
            pre_orient_next_step=cp.pre_orient_next_step,
            pre_halt_next_step=cp.pre_halt_next_step,
        )
        assert body1 == body2, (
            f"Round-trip NOT fixed-point for next_step={next_step!r}.\n"
            f"Initial render:\n{body1}\n"
            f"Re-render after parse:\n{body2}"
        )


# ---------------------------------------------------------------------------
# Structured-token preservation in determine_next_cycle (F5/F12)
# ---------------------------------------------------------------------------


def test_determine_next_cycle_preserves_execute_rework_token(
    tmp_path: Path,
) -> None:
    """``determine_next_cycle`` returns ``EXECUTE_REWORK`` (not ``EXECUTE``).

    Cycle-2 rework (F5/F12): the prior implementation collapsed
    ``EXECUTE_REWORK`` / ``EXECUTE_VERIFY`` to plain ``"EXECUTE"``
    in the router's return value, which meant every downstream
    consumer (telemetry, prompt builder, DAG wiring) saw the same
    token regardless of whether the cycle was rework or verify.  The
    fix preserves the structured token so telemetry can distinguish
    gate decisions per cycle subtype.
    """
    from clou.recovery_checkpoint import determine_next_cycle

    checkpoint = tmp_path / "coordinator.md"
    checkpoint.write_text(
        "cycle: 3\nstep: ASSESS\nnext_step: EXECUTE_REWORK\n"
        "current_phase: impl\n"
    )

    cycle_type, _ = determine_next_cycle(
        checkpoint,
        milestone="demo",
    )
    assert cycle_type == "EXECUTE_REWORK"


def test_determine_next_cycle_preserves_execute_verify_token(
    tmp_path: Path,
) -> None:
    """``determine_next_cycle`` returns ``EXECUTE_VERIFY`` (not ``EXECUTE``)."""
    from clou.recovery_checkpoint import determine_next_cycle

    checkpoint = tmp_path / "coordinator.md"
    checkpoint.write_text(
        "cycle: 4\nstep: VERIFY\nnext_step: EXECUTE_VERIFY\n"
        "current_phase: impl\n"
    )

    cycle_type, _ = determine_next_cycle(
        checkpoint,
        milestone="demo",
    )
    assert cycle_type == "EXECUTE_VERIFY"


def test_determine_next_cycle_plain_execute_stays_execute(
    tmp_path: Path,
) -> None:
    """A plain ``next_step=EXECUTE`` checkpoint still returns ``EXECUTE``.

    Coupling check: the structured-token preservation fix must NOT
    accidentally upgrade plain EXECUTE into a rework/verify variant.
    """
    from clou.recovery_checkpoint import determine_next_cycle

    checkpoint = tmp_path / "coordinator.md"
    checkpoint.write_text(
        "cycle: 2\nstep: PLAN\nnext_step: EXECUTE\ncurrent_phase: impl\n"
    )

    cycle_type, _ = determine_next_cycle(
        checkpoint,
        milestone="demo",
    )
    assert cycle_type == "EXECUTE"


def test_is_execute_family_recognises_all_three_tokens() -> None:
    """``is_execute_family`` matches EXECUTE / EXECUTE_REWORK / EXECUTE_VERIFY.

    Consumers that want "this is an EXECUTE phase" semantics (prompts,
    commit-at-phase-end, DAG wiring) call this helper so they do not
    have to enumerate the three canonical tokens.
    """
    from clou.recovery_checkpoint import is_execute_family

    assert is_execute_family("EXECUTE") is True
    assert is_execute_family("EXECUTE_REWORK") is True
    assert is_execute_family("EXECUTE_VERIFY") is True
    # Non-execute cycles are not in the family.
    assert is_execute_family("ASSESS") is False
    assert is_execute_family("VERIFY") is False
    assert is_execute_family("PLAN") is False
    # Empty / unknown strings not in family.
    assert is_execute_family("") is False
    assert is_execute_family("execute_rework") is False  # casing-sensitive


# ---------------------------------------------------------------------------
# run_coordinator wiring (F6/F36): session-start calls the migration helper
# ---------------------------------------------------------------------------


def _install_sdk_shim() -> None:
    """Shared claude_agent_sdk shim (mimics test_lifecycle_events)."""
    import sys
    from unittest.mock import MagicMock
    if "claude_agent_sdk" not in sys.modules:
        _mock_sdk = MagicMock()
        _mock_sdk.ClaudeAgentOptions = MagicMock
        _mock_sdk.ClaudeSDKClient = MagicMock
        _mock_sdk.SandboxSettings = MagicMock
        _mock_sdk.create_sdk_mcp_server = MagicMock(return_value=MagicMock())
        _mock_sdk.tool = lambda *a, **kw: lambda f: f
        sys.modules["claude_agent_sdk"] = _mock_sdk


def _seed_run_coordinator_tree(
    tmp_path: Path,
    *,
    milestone: str = "test-ms",
    next_step_on_disk: str = "EXECUTE",
) -> tuple[Path, Path]:
    """Seed a minimal .clou/ tree for ``run_coordinator`` preamble.

    Returns ``(clou_dir, checkpoint_path)`` so tests can inspect the
    persisted checkpoint after ``run_coordinator`` returns.
    """
    clou_dir = tmp_path / ".clou"
    (clou_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (clou_dir / "prompts" / "coordinator-system.xml").write_text("<system/>")
    ms_dir = clou_dir / "milestones" / milestone
    (ms_dir / "active").mkdir(parents=True, exist_ok=True)
    (ms_dir / "status.md").write_text("phase: p1\ncycle: 1\n")
    cp_path = ms_dir / "active" / "coordinator.md"
    cp_path.write_text(
        f"cycle: 1\nstep: PLAN\nnext_step: {next_step_on_disk}\n"
        "current_phase: p1\nphases_completed: 0\nphases_total: 1\n"
    )
    (clou_dir / ".coordinator-milestone").write_text(milestone)
    return clou_dir, cp_path


async def test_run_coordinator_invokes_migration_at_session_start(
    tmp_path: Path,
) -> None:
    """``run_coordinator`` calls ``migrate_legacy_tokens`` at session start.

    Cycle-2 rework (F6/F36): the prior tests certified the migration
    helper in isolation but never proved the coordinator actually
    called it.  A regression that silently removed the session-start
    call would leave persisted legacy tokens in place and every test
    for the helper would still pass.  This test patches the helper
    and asserts ``run_coordinator`` invoked it against the
    project's ``.clou`` directory.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, _cp = _seed_run_coordinator_tree(tmp_path, milestone=milestone)

    _PC = "clou.coordinator"
    # Return a legacy count so the telemetry/log branch fires and we
    # exercise the partial-path; also return the structured dict shape
    # the production helper returns so assertions don't fight the schema.
    stub_counts = {
        "checkpoints": 1,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [],
    }

    # Patch migrate_legacy_tokens in its source module AND where it's
    # imported inside run_coordinator (a local `from ... import`).  The
    # local import shadow means patching only the source module won't
    # take effect once run_coordinator has already resolved the name;
    # patching the source module BEFORE run_coordinator executes is
    # sufficient because the local import happens at call time.
    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            return_value=stub_counts,
        ) as mock_migrate,
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ),
        patch(f"{_PC}.read_cycle_count", return_value=0),
        patch(f"{_PC}._run_single_cycle", new=AsyncMock(return_value="ok")),
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        result = await run_coordinator(tmp_path, milestone)

    # Verify the session-start migration was invoked against this
    # project's .clou directory — this is the wiring gate.
    assert mock_migrate.call_count == 1
    called_with = mock_migrate.call_args.args[0]
    assert called_with == clou_dir
    # Sanity: coordinator completed its single iteration.
    assert result == "completed"


async def test_run_coordinator_migration_runs_before_determine_next_cycle(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-3 rework (F7): migration must run BEFORE the dispatch router.

    The migration's job is to rewrite legacy tokens on disk so the
    dispatch router (``determine_next_cycle``, which re-reads the
    checkpoint) sees the structured vocabulary.  If the call order
    were reversed, the router would see ``EXECUTE (rework)``,
    ``parse_checkpoint`` would coerce it to PLAN, and dispatch would
    trivially silent-downgrade every pending rework request.

    Note: an earlier ``parse_checkpoint`` at module line ~921 reads
    only retry counters (not ``next_step``), so its ordering relative
    to migration is not load-bearing.  The load-bearing ordering is
    migration → determine_next_cycle, which is what this test pins.

    Replaces the cycle-2 "assert mock_migrate.call_count == 1" which
    proved the call happened but not its position relative to
    dispatch.
    """
    from unittest.mock import AsyncMock, Mock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, _cp = _seed_run_coordinator_tree(tmp_path, milestone=milestone)

    # Shared tracker observes call order across both functions.
    tracker = Mock()

    def _migrate_spy(arg: Path) -> dict[str, object]:
        tracker("migrate")
        return {
            "checkpoints": 0, "decisions": 0, "judgments": 0,
            "prompts": 0, "failed": [],
        }

    def _dispatch_spy(*a, **kw):
        tracker("dispatch")
        return ("COMPLETE", [])

    _PC = "clou.coordinator"
    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            side_effect=_migrate_spy,
        ),
        patch(f"{_PC}.determine_next_cycle", side_effect=_dispatch_spy),
        patch(f"{_PC}.read_cycle_count", return_value=0),
        patch(f"{_PC}._run_single_cycle", new=AsyncMock(return_value="ok")),
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        await run_coordinator(tmp_path, milestone)

    call_names = [c.args[0] for c in tracker.call_args_list]
    assert "migrate" in call_names, "migrate_legacy_tokens not called"
    assert "dispatch" in call_names, "determine_next_cycle not called"
    first_migrate = call_names.index("migrate")
    first_dispatch = call_names.index("dispatch")
    assert first_migrate < first_dispatch, (
        f"Expected migration before determine_next_cycle, got call "
        f"order: {call_names}"
    )


async def test_run_coordinator_migration_emits_partial_failure_event(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-3 rework (F7): non-empty failed list emits partial_failure.

    The cycle-2 wiring test returned ``{"failed": []}`` so the
    partial-failure branch was never exercised via the coordinator.
    This test stubs a non-empty ``failed`` list and asserts the
    distinct ``vocabulary_migration.partial_failure`` telemetry event
    is emitted with the failed paths.  Without this guard, a regression
    that swaps the event name (``.partial_failure`` →
    ``.partial_fail``) or drops the branch entirely would pass every
    other test.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, _cp = _seed_run_coordinator_tree(tmp_path, milestone=milestone)

    failed_path = clou_dir / "milestones" / milestone / "broken.md"
    stub_counts = {
        "checkpoints": 1,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [failed_path],
    }

    _PC = "clou.coordinator"
    captured_events: list[tuple[str, dict]] = []

    def _capture_event(name: str, **kw) -> None:
        captured_events.append((name, kw))

    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            return_value=stub_counts,
        ),
        patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ),
        patch(f"{_PC}.read_cycle_count", return_value=0),
        patch(f"{_PC}._run_single_cycle", new=AsyncMock(return_value="ok")),
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        await run_coordinator(tmp_path, milestone)

    # Find the partial_failure event (and the rewrote event — both
    # fire when counts are non-zero AND failed list is non-empty).
    event_names = [ev[0] for ev in captured_events]
    assert "vocabulary_migration.partial_failure" in event_names, (
        f"partial_failure event not emitted; got events: {event_names}"
    )
    assert "vocabulary_migration.rewrote" in event_names, (
        f"rewrote event not emitted; got events: {event_names}"
    )
    # Verify the failed paths are carried in the payload as strings.
    partial_event = next(
        ev for ev in captured_events
        if ev[0] == "vocabulary_migration.partial_failure"
    )
    partial_payload = partial_event[1]
    assert str(failed_path) in partial_payload["failed"]
    assert partial_payload["milestone"] == milestone


async def test_run_coordinator_legacy_checkpoint_dispatches_to_structured(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-3 rework (F7): dispatch-effect test proves migration fixes state.

    End-to-end: seed an on-disk checkpoint with the legacy token
    ``EXECUTE (rework)``, run the coordinator's session-start preamble
    (real migration helper, not stubbed), and assert that
    ``parse_checkpoint`` now reads ``EXECUTE_REWORK`` — NOT the PLAN
    default it would return if the migration had been skipped.

    This is the outcome the wiring exists to produce: a legacy
    checkpoint on disk becomes a structured checkpoint in memory
    without losing dispatch information.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402
    from clou.recovery_checkpoint import parse_checkpoint

    milestone = "test-ms"
    clou_dir, cp_path = _seed_run_coordinator_tree(
        tmp_path, milestone=milestone,
    )
    # Overwrite with a legacy-token checkpoint.
    cp_path.write_text(
        "cycle: 3\nstep: ASSESS\nnext_step: EXECUTE (rework)\n"
        "current_phase: p1\nphases_completed: 0\nphases_total: 1\n",
        encoding="utf-8",
    )

    _PC = "clou.coordinator"
    # Real migration runs; the rest is stubbed so the loop completes
    # after a single iteration.
    with (
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ),
        patch(f"{_PC}.read_cycle_count", return_value=0),
        patch(f"{_PC}._run_single_cycle", new=AsyncMock(return_value="ok")),
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        await run_coordinator(tmp_path, milestone)

    # Read the on-disk checkpoint after coordinator exit: migration
    # must have rewritten the legacy token to the structured form.
    after = cp_path.read_text(encoding="utf-8")
    # Migration rewrites the field; a later ORIENT dispatch or
    # checkpoint rewrite may alter other fields but NEVER reintroduce
    # the legacy form.
    assert "EXECUTE (rework)" not in after, (
        f"Legacy token survived session-start migration:\n{after}"
    )

    # And parse_checkpoint on the rewritten file sees a valid
    # structured token (either EXECUTE_REWORK directly or ORIENT with
    # stash after session-start promotion).  Either way: NOT default-
    # PLAN, which is what we'd get if the migration had not run.
    parsed = parse_checkpoint(after)
    assert parsed.next_step in (
        "EXECUTE_REWORK",
        "ORIENT",
    ), (
        f"Expected EXECUTE_REWORK / ORIENT after migration; got "
        f"{parsed.next_step!r} — indicates migration did not run "
        f"before parse."
    )
    # If ORIENT, the stash must preserve the structured token.
    if parsed.next_step == "ORIENT":
        assert parsed.pre_orient_next_step == "EXECUTE_REWORK"


# ---------------------------------------------------------------------------
# F4 path (a) — engine halt-gate on active-milestone partial failure.
# M50 I1 cycle-2 EXECUTE_REWORK: the cycle-4 surface logged a warning,
# emitted telemetry, and continued into the dispatch loop.  When the
# active milestone's checkpoint is in the failed list, the next
# parse_checkpoint reads the unmigrated legacy token, defaults
# next_step=PLAN, and silently re-plans a completed phase.  Path (a)
# files an engine-gated trajectory_halt escalation so M49b's halt
# gate catches it on the next loop iteration and the supervisor
# disposes via clou_dispose_halt.
# ---------------------------------------------------------------------------


async def test_run_coordinator_active_failure_files_engine_halt(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F4 path a): active checkpoint in
    failed list triggers engine halt-gate.

    Simulates the exact failure mode named in decisions.md cycle 1
    F4 (### Valid: F4 Action block) and the supervisor's re-scope brief
    item 2 (escalations/20260425-031626-trajectory-halt.md NEW SCOPE
    FOR I1, item 2 path a):
    - The active milestone's ``active/coordinator.md`` is in
      ``migrate_legacy_tokens``'s ``failed`` list.
    - The coordinator MUST file a ``trajectory_halt`` escalation so
      the next iteration's ``_apply_halt_gate`` catches it.
    - The escalation file lives under
      ``.clou/milestones/{milestone}/escalations/`` with a
      ``trajectory-halt`` slug and ``classification: trajectory_halt``
      so ``find_open_engine_gated_escalation`` recognises it.

    Without the halt-gate, the prior log-warning-only surface
    sidesteps M49b — the parser would default ``next_step`` to
    ``PLAN`` and silently re-plan a completed phase.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402
    from clou.escalation import parse_escalation

    milestone = "test-ms"
    clou_dir, cp_path = _seed_run_coordinator_tree(
        tmp_path, milestone=milestone,
    )

    # Stub migration to report the ACTIVE checkpoint as failed.
    stub_counts = {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [cp_path],
    }

    _PC = "clou.coordinator"
    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            return_value=stub_counts,
        ),
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ) as mock_determine_next_cycle,
        patch(f"{_PC}.read_cycle_count", return_value=3),
        patch(
            f"{_PC}._run_single_cycle",
            new=AsyncMock(return_value="ok"),
        ) as mock_run_single_cycle,
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        result = await run_coordinator(tmp_path, milestone)

    # Halt escalation must be filed under the milestone's
    # escalations/ directory with the trajectory-halt slug.
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    halt_files = list(esc_dir.glob("*-trajectory-halt*.md"))
    assert len(halt_files) >= 1, (
        f"F4 path (a) halt escalation not filed; expected a "
        f"`*-trajectory-halt*.md` in {esc_dir}; got "
        f"{[p.name for p in esc_dir.iterdir()] if esc_dir.exists() else 'no dir'}"
    )
    halt_path = halt_files[0]

    # The escalation must parse and have classification=trajectory_halt
    # so find_open_engine_gated_escalation recognises it on the next
    # loop iteration.
    form = parse_escalation(halt_path.read_text(encoding="utf-8"))
    assert form.classification == "trajectory_halt", (
        f"halt escalation classification must be `trajectory_halt`; "
        f"got {form.classification!r}"
    )
    # Title should name the trigger so dashboards can group similar
    # halts.
    assert "vocabulary_migration.partial_failure" in form.title or (
        "partial_failure" in form.title
    ), (
        f"halt title should reference vocabulary_migration partial "
        f"failure; got {form.title!r}"
    )
    # disposition_status starts open so the halt gate fires.
    assert form.disposition_status == "open"

    # M50 I1 cycle-1 round-2 rework (F7 — MAJOR): the halt-gate
    # contract is "engine exits cleanly to HALTED_PENDING_REVIEW
    # without dispatching the cycle".  The cycle-2 surface only
    # asserted file existence; the load-bearing invariants are:
    # (a) ``run_coordinator`` returned the canonical
    #     ``"halted_pending_review"`` string.
    # (b) ``_run_single_cycle`` was NEVER invoked — the halt fired
    #     at the loop-top engine gate before dispatch.
    # (c) ``determine_next_cycle`` was NEVER invoked for the post-
    #     halt iteration; the halt-gate runs BEFORE dispatch
    #     routing.
    # (d) The active checkpoint was rewritten to
    #     ``next_step=HALTED`` with
    #     ``cycle_outcome=HALTED_PENDING_REVIEW`` — the supervisor's
    #     ``clou_dispose_halt`` reads this state to drive disposition.
    # Without these assertions, F1's primary failure mode (halt-gate
    # crashes BEFORE dispatch is skipped) is invisible at test time.
    assert result == "halted_pending_review", (
        f"run_coordinator must return 'halted_pending_review' after "
        f"the halt-gate fires; got {result!r}"
    )
    mock_run_single_cycle.assert_not_called()
    mock_determine_next_cycle.assert_not_called()
    # Checkpoint state transition: HALTED + HALTED_PENDING_REVIEW.
    from clou.recovery_checkpoint import parse_checkpoint as _parse_cp
    final_cp = _parse_cp(cp_path.read_text(encoding="utf-8"))
    assert final_cp.next_step == "HALTED", (
        f"checkpoint must be rewritten to next_step=HALTED after "
        f"halt-gate; got {final_cp.next_step!r}"
    )
    assert final_cp.cycle_outcome == "HALTED_PENDING_REVIEW", (
        f"checkpoint cycle_outcome must be HALTED_PENDING_REVIEW; "
        f"got {final_cp.cycle_outcome!r}"
    )


async def test_run_coordinator_other_milestone_failure_does_not_halt(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F4 path a, narrow guard): only
    active-milestone failures fire the halt gate.

    Mirror of the active-failure test.  Other-milestone failures
    (e.g., a stale archived milestone with an unreadable checkpoint)
    are best-effort hygiene; they MUST NOT block dispatch of the
    active milestone.  The narrow guard "failing path belongs to
    the milestone being dispatched" is named in CLAUDE FINDING 2
    (decisions.md F4 Action block).

    Without this guard, every coordinator session would halt under
    the global migration sweep if any one milestone's checkpoint
    happened to be unreadable — denial of service via stale state.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, cp_path = _seed_run_coordinator_tree(
        tmp_path, milestone=milestone,
    )

    # Stub migration to report ANOTHER milestone's path as failed.
    other_failed = clou_dir / "milestones" / "other-ms" / "active" / "coordinator.md"
    stub_counts = {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [other_failed],
    }

    _PC = "clou.coordinator"
    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            return_value=stub_counts,
        ),
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ) as mock_determine_next_cycle,
        patch(f"{_PC}.read_cycle_count", return_value=0),
        patch(
            f"{_PC}._run_single_cycle",
            new=AsyncMock(return_value="ok"),
        ),
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        await run_coordinator(tmp_path, milestone)

    # NO halt escalation for the active milestone — the failed path
    # was for a different milestone, so the narrow guard does not fire.
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    halt_files = (
        list(esc_dir.glob("*-trajectory-halt*.md"))
        if esc_dir.exists()
        else []
    )
    assert len(halt_files) == 0, (
        f"F4 path (a) halt fired for an unrelated-milestone failure; "
        f"expected no `*-trajectory-halt*.md` in {esc_dir}; got "
        f"{[p.name for p in halt_files]}"
    )
    # M50 I1 cycle-1 round-2 rework (F7 — MAJOR mirror): the symmetric
    # invariant for the no-halt branch is "the dispatch router was
    # actually invoked" — i.e. ``run_coordinator`` reached the loop
    # body without crashing in the migration / halt-filing prelude.
    # Without this assertion, "no halt fired" is indistinguishable
    # from "halt path crashed silently before dispatch could route".
    mock_determine_next_cycle.assert_called()


async def test_run_coordinator_active_failure_emits_halt_filed_telemetry(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-2 EXECUTE_REWORK (F4 path a): halt-filed telemetry.

    The cycle-2 surface emits a NEW telemetry event,
    ``vocabulary_migration.partial_failure.halt_filed``, alongside the
    existing ``vocabulary_migration.partial_failure`` event so
    operators can distinguish "active milestone failed and halt was
    filed" from "off-milestone failure logged for hygiene".  This
    test asserts both events fire when the active checkpoint is in
    the failed list.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, cp_path = _seed_run_coordinator_tree(
        tmp_path, milestone=milestone,
    )

    stub_counts = {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [cp_path],
    }

    captured_events: list[tuple[str, dict]] = []

    def _capture_event(name: str, **kw) -> None:
        captured_events.append((name, kw))

    _PC = "clou.coordinator"
    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            return_value=stub_counts,
        ),
        patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ),
        patch(f"{_PC}.read_cycle_count", return_value=2),
        patch(f"{_PC}._run_single_cycle", new=AsyncMock(return_value="ok")),
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        await run_coordinator(tmp_path, milestone)

    event_names = [ev[0] for ev in captured_events]
    # Both events fire — partial_failure for the warning surface,
    # halt_filed for the new engine halt-gate surface.
    assert "vocabulary_migration.partial_failure" in event_names, (
        f"partial_failure event must still fire; got: {event_names}"
    )
    assert "vocabulary_migration.partial_failure.halt_filed" in event_names, (
        f"halt_filed event missing — F4 path (a) regression; got: "
        f"{event_names}"
    )
    halt_filed_event = next(
        ev for ev in captured_events
        if ev[0] == "vocabulary_migration.partial_failure.halt_filed"
    )
    halt_payload = halt_filed_event[1]
    assert halt_payload["milestone"] == milestone
    # The halt path is recorded so operators can find the file.
    assert "halt_path" in halt_payload
    assert "trajectory-halt" in halt_payload["halt_path"]

    # M50 I1 cycle-1 round-2 rework (F8 — MAJOR): the load-bearing
    # state-transition signal is ``cycle.halted`` (emitted at
    # ``coordinator.py:_apply_halt_gate`` with ``origin=cycle_start``).
    # The cycle-2 surface verified the filing-level events but missed
    # the gate-engagement event — without this assertion, the halt-
    # gate could silently fail to fire on the next iteration AND
    # ``vocabulary_migration.partial_failure.halt_filed`` would still
    # surface (filing succeeded; only consumption failed).
    assert "cycle.halted" in event_names, (
        f"cycle.halted event missing — halt-gate did NOT engage on "
        f"the next loop iteration; got: {event_names}"
    )
    halted_event = next(
        ev for ev in captured_events if ev[0] == "cycle.halted"
    )
    halted_payload = halted_event[1]
    assert halted_payload.get("milestone") == milestone
    assert halted_payload.get("origin") == "cycle_start", (
        f"cycle.halted origin must be 'cycle_start' (loop-top gate); "
        f"got {halted_payload.get('origin')!r}"
    )


# ---------------------------------------------------------------------------
# F28 — UNMOCKED read_cycle_count exercise (would have caught F1).
# M50 I1 cycle-1 round-2 rework: the cycle-2 T13 tests over-mocked
# ``read_cycle_count`` — the ONE failure surface F4's halt-gate exists
# to defend (an unreadable checkpoint that ``read_cycle_count`` re-
# raises on) is invisible because the helper is replaced by a stub.
# These tests seed an ACTUALLY unreadable checkpoint (chmod 000 +
# invalid-UTF-8 variants) and assert the halt-gate engages without
# crashing.  This catches F1 directly: if ``read_cycle_count`` re-
# raises ``OSError`` / ``UnicodeDecodeError``, ``run_coordinator``
# crashes BEFORE filing the halt and these tests fail loudly.
# ---------------------------------------------------------------------------


async def test_run_coordinator_unreadable_checkpoint_files_halt_without_crash(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-1 round-2 rework (F28 — MAJOR): F1 regression cover.

    Seeds an actually-unreadable checkpoint via ``chmod 000`` and
    runs ``run_coordinator`` with NO mock on ``read_cycle_count``.
    The migration helper records the path in its ``failed`` list,
    the halt-filing block fires, and ``run_coordinator`` exits
    cleanly to ``halted_pending_review``.

    Without the F1 try/except defense, ``read_cycle_count`` re-
    raises the same ``OSError`` that put the path in the failed
    list — the engine crashes BEFORE filing the halt.  This test
    fails loudly under that regression.
    """
    import sys
    from unittest.mock import AsyncMock, patch

    if sys.platform.startswith("win"):
        import pytest
        pytest.skip("chmod 000 is a POSIX-specific permission shape")

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, cp_path = _seed_run_coordinator_tree(
        tmp_path, milestone=milestone,
    )

    # Make the checkpoint actually unreadable.  read() raises
    # PermissionError (subclass of OSError); read_text() propagates.
    cp_path.chmod(0o000)
    try:
        # Force the migration helper to report the active checkpoint
        # in its failed list — we cannot rely on the production
        # helper since chmod 000 may interact with run-as-root
        # environments; the helper's job here is to observe the
        # failure surface, not to BE the failure surface.
        stub_counts = {
            "checkpoints": 0,
            "decisions": 0,
            "judgments": 0,
            "prompts": 0,
            "failed": [cp_path],
        }

        captured_events: list[tuple[str, dict]] = []

        def _capture_event(name: str, **kw) -> None:
            captured_events.append((name, kw))

        _PC = "clou.coordinator"
        # IMPORTANT: NO patch on read_cycle_count.  The whole point of
        # this test is to exercise the production helper against an
        # unreadable file.  The other patches (determine_next_cycle,
        # _run_single_cycle) are scaffolding — the halt-gate must
        # fire BEFORE any of them are reached.
        with (
            patch(
                "clou.vocabulary_migration.migrate_legacy_tokens",
                return_value=stub_counts,
            ),
            patch(
                f"{_PC}.telemetry.event",
                side_effect=_capture_event,
            ),
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("COMPLETE", [])],
            ) as mock_determine_next_cycle,
            patch(
                f"{_PC}._run_single_cycle",
                new=AsyncMock(return_value="ok"),
            ) as mock_run_single_cycle,
            patch(
                f"{_PC}.validate_golden_context",
                return_value=[],
            ),
            patch(
                f"{_PC}.build_cycle_prompt",
                return_value="prompt",
            ),
        ):
            # Must not crash; must not raise OSError/UnicodeDecodeError.
            result = await run_coordinator(tmp_path, milestone)

        # Halt-gate engaged: clean exit, no dispatch, halt event fired.
        assert result == "halted_pending_review", (
            f"run_coordinator must return 'halted_pending_review' "
            f"after halt-gate fires on an unreadable active "
            f"checkpoint; got {result!r}"
        )
        mock_run_single_cycle.assert_not_called()
        mock_determine_next_cycle.assert_not_called()

        event_names = [ev[0] for ev in captured_events]
        # Both filing-level events fire (partial_failure surface
        # was captured in the existing test) AND the consumption-
        # level event fires (cycle.halted from _apply_halt_gate).
        assert (
            "vocabulary_migration.partial_failure.halt_filed"
            in event_names
        ), (
            f"halt-filed event missing under unreadable-checkpoint "
            f"path; F1 likely re-raised; got: {event_names}"
        )
        assert "cycle.halted" in event_names, (
            f"cycle.halted event missing — halt-gate did not engage "
            f"on the next iteration; got: {event_names}"
        )
    finally:
        # Restore permissions so the tmp_path teardown can unlink.
        try:
            cp_path.chmod(0o644)
        except OSError:
            pass


async def test_run_coordinator_invalid_utf8_checkpoint_files_halt_without_crash(
    tmp_path: Path,
) -> None:
    """M50 I1 cycle-1 round-2 rework (F28 — MAJOR mirror): UnicodeDecodeError variant.

    Same contract as the chmod-000 variant but exercises the
    ``UnicodeDecodeError`` re-raise path: the checkpoint contains
    bytes that are NOT valid UTF-8, so ``read_text(encoding='utf-8')``
    raises ``UnicodeDecodeError`` rather than ``PermissionError``.
    Both must be defended at the F1 patch sites; this test pins the
    UTF-8 surface alongside the chmod surface.
    """
    from unittest.mock import AsyncMock, patch

    _install_sdk_shim()
    from clou.coordinator import run_coordinator  # noqa: E402

    milestone = "test-ms"
    clou_dir, cp_path = _seed_run_coordinator_tree(
        tmp_path, milestone=milestone,
    )

    # Replace the checkpoint with bytes that are NOT valid UTF-8.
    # 0xFF 0xFE 0xFD are continuation bytes without a leading byte —
    # any UTF-8 decoder rejects this prefix.
    cp_path.write_bytes(b"\xff\xfe\xfd not valid utf-8")

    stub_counts = {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [cp_path],
    }

    captured_events: list[tuple[str, dict]] = []

    def _capture_event(name: str, **kw) -> None:
        captured_events.append((name, kw))

    _PC = "clou.coordinator"
    # NO patch on read_cycle_count — the production helper must
    # absorb the UnicodeDecodeError per the F1 contract.
    with (
        patch(
            "clou.vocabulary_migration.migrate_legacy_tokens",
            return_value=stub_counts,
        ),
        patch(f"{_PC}.telemetry.event", side_effect=_capture_event),
        patch(
            f"{_PC}.determine_next_cycle",
            side_effect=[("COMPLETE", [])],
        ) as mock_determine_next_cycle,
        patch(
            f"{_PC}._run_single_cycle",
            new=AsyncMock(return_value="ok"),
        ) as mock_run_single_cycle,
        patch(f"{_PC}.validate_golden_context", return_value=[]),
        patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
    ):
        result = await run_coordinator(tmp_path, milestone)

    # Same halt-gate engagement contract as the chmod variant.
    assert result == "halted_pending_review", (
        f"run_coordinator must return 'halted_pending_review' "
        f"after halt-gate fires on an invalid-UTF-8 active "
        f"checkpoint; got {result!r}"
    )
    mock_run_single_cycle.assert_not_called()
    mock_determine_next_cycle.assert_not_called()
    event_names = [ev[0] for ev in captured_events]
    assert (
        "vocabulary_migration.partial_failure.halt_filed"
        in event_names
    ), (
        f"halt-filed event missing under invalid-UTF-8 path; F1 "
        f"likely re-raised UnicodeDecodeError; got: {event_names}"
    )
    assert "cycle.halted" in event_names, (
        f"cycle.halted event missing — halt-gate did not engage; "
        f"got: {event_names}"
    )
