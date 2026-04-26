"""Targeted unit tests for the telemetry-fix work (F1/F4/F5/F6/F8/F10/F11).

Deterministic coverage for the helpers exposed at module level.  The
integration-level behaviors (synthetic agent.end on stop_task, new gate
status branches firing in a real cycle, warnings delta across milestone
iterations) are covered by live-run verification — see the roadmap note
on C2 and the in-repo telemetry fixture milestone.
"""

from __future__ import annotations

import re

from clou.coordinator import infer_agent_tier
from clou.recovery_compaction import _extract_cycle_section


# ---------------------------------------------------------------------------
# F4b: infer_agent_tier
# ---------------------------------------------------------------------------


class TestInferAgentTier:
    """Tier inference over runtime agent descriptions."""

    VOCAB = {"brutalist", "worker", "verifier", "assess-evaluator"}

    def test_sdk_task_type_is_authoritative(self) -> None:
        # When the SDK provides task_type, it wins regardless of description.
        assert infer_agent_tier(
            "arbitrary description",
            self.VOCAB,
            task_type="brutalist",
        ) == "brutalist"
        assert infer_agent_tier(
            "Brutalist quality gate",
            self.VOCAB,
            task_type="worker",
        ) == "worker"

    def test_task_type_outside_vocabulary_falls_back(self) -> None:
        # Unknown task_type is ignored (defensive against SDK drift).
        assert infer_agent_tier(
            "Classify findings", self.VOCAB, task_type="unknown-tier",
        ) == "assess-evaluator"

    def test_brutalist_noun_matches_anywhere(self) -> None:
        assert infer_agent_tier(
            "Brutalist quality gate assessment", self.VOCAB,
        ) == "brutalist"
        assert infer_agent_tier(
            "Dispatch the brutalist panel now", self.VOCAB,
        ) == "brutalist"
        assert infer_agent_tier(
            "Run a roast on the output", self.VOCAB,
        ) == "brutalist"

    def test_classify_and_verify_match_as_first_word(self) -> None:
        assert infer_agent_tier(
            "Classify assessment findings", self.VOCAB,
        ) == "assess-evaluator"
        assert infer_agent_tier(
            "Verify milestone implementation", self.VOCAB,
        ) == "verifier"

    def test_verb_in_middle_does_not_match(self) -> None:
        # Historical false-positive: "Implement verification harness"
        # used to classify as verifier because substring-match matched
        # "verify".  New logic requires first-word verb match.
        assert infer_agent_tier(
            "Implement verification harness", self.VOCAB,
        ) == "worker"
        assert infer_agent_tier(
            "Improve the verification pipeline for evaluator output", self.VOCAB,
        ) == "worker"
        assert infer_agent_tier(
            "Add a classifier for the inputs", self.VOCAB,
        ) == "worker"

    def test_unknown_description_defaults_to_worker(self) -> None:
        assert infer_agent_tier(
            "Run the full test suite", self.VOCAB,
        ) == "worker"
        assert infer_agent_tier(
            "Full test suite summary", self.VOCAB,
        ) == "worker"
        assert infer_agent_tier(
            "Fix F2 unguarded unlink", self.VOCAB,
        ) == "worker"

    def test_punctuation_stripped(self) -> None:
        # Description may have trailing punctuation; it should not
        # prevent keyword matching.
        assert infer_agent_tier(
            "Verify, then summarize.", self.VOCAB,
        ) == "verifier"
        assert infer_agent_tier(
            "Classify: each finding.", self.VOCAB,
        ) == "assess-evaluator"

    def test_case_insensitive_matching(self) -> None:
        assert infer_agent_tier(
            "BRUTALIST REVIEW", self.VOCAB,
        ) == "brutalist"
        assert infer_agent_tier(
            "verify something", self.VOCAB,
        ) == "verifier"

    def test_tier_not_in_vocabulary_falls_back(self) -> None:
        # If the harness template doesn't declare a tier, don't emit
        # that tier even on strong keyword match.  The template is
        # the source of truth for available tiers.
        limited = {"worker"}
        assert infer_agent_tier(
            "Brutalist quality gate", limited,
        ) == "worker"
        assert infer_agent_tier(
            "Classify findings", limited,
        ) == "worker"


# ---------------------------------------------------------------------------
# F10: _extract_cycle_section robustness to LLM formatting drift
# ---------------------------------------------------------------------------


class TestExtractCycleSection:
    """Verify the convergence-detection helper tolerates LLM formatting drift."""

    def test_canonical_heading(self) -> None:
        text = "## Cycle 1\nbody 1\n## Cycle 2\nbody 2\n"
        assert _extract_cycle_section(text, 1) == "## Cycle 1\nbody 1\n"
        assert _extract_cycle_section(text, 2) == "## Cycle 2\nbody 2\n"

    def test_missing_cycle_returns_none(self) -> None:
        text = "## Cycle 1\nbody 1\n## Cycle 2\nbody 2\n"
        assert _extract_cycle_section(text, 99) is None

    def test_empty_string(self) -> None:
        assert _extract_cycle_section("", 1) is None

    def test_tolerates_trailing_heading_text(self) -> None:
        # Coordinator LLM often decorates headings: "## Cycle 3 — Assessment"
        text = "## Cycle 3 — Assessment\ncontent\n## Cycle 4 — rework\nother\n"
        section = _extract_cycle_section(text, 3)
        assert section is not None
        assert "Assessment" in section
        assert "rework" not in section

    def test_tolerates_case_drift(self) -> None:
        # Coordinator might typo "## cycle 3" in lowercase.
        text = "## cycle 3\nbody\n"
        section = _extract_cycle_section(text, 3)
        assert section is not None
        assert "body" in section

    def test_tolerates_extra_whitespace(self) -> None:
        # "##  Cycle  3" (double space) used to fail silently in the old
        # single-space split regex.
        text = "##  Cycle  3\nbody\n"
        section = _extract_cycle_section(text, 3)
        assert section is not None
        assert "body" in section

    def test_tolerates_tab_whitespace(self) -> None:
        text = "##\tCycle\t3\nbody\n"
        section = _extract_cycle_section(text, 3)
        assert section is not None

    def test_convergence_entry_detection(self) -> None:
        # The canonical use case: detect whether a cycle's section
        # contains a ``### Convergence:`` declaration.
        text = (
            "## Cycle 3\n"
            "### Valid: bug found\n"
            "## Cycle 4\n"
            "### Convergence: trajectory met\n"
            "**Reasoning:** bugs -> edge cases -> test gaps -> stop\n"
        )
        section_3 = _extract_cycle_section(text, 3)
        section_4 = _extract_cycle_section(text, 4)
        assert section_3 is not None and "### Convergence:" not in section_3
        assert section_4 is not None and "### Convergence:" in section_4

    def test_does_not_bleed_across_cycles(self) -> None:
        # Entries from a later cycle should not appear in the returned
        # section of an earlier cycle.
        text = (
            "## Cycle 1\nearly\n"
            "## Cycle 2\n### Convergence: later\n"
        )
        section = _extract_cycle_section(text, 1)
        assert section is not None
        assert "### Convergence:" not in section

    def test_numeric_boundary(self) -> None:
        # ``## Cycle 10`` should not match when asked for cycle 1.
        text = "## Cycle 10\nbody\n"
        assert _extract_cycle_section(text, 1) is None
        assert _extract_cycle_section(text, 10) is not None
