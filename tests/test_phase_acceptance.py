"""Tests for the phase-acceptance gate.

The gate is a pure function over pre-read execution.md text.
Tests cover:
    * Advance path (typed artifact present + valid + location matches)
    * Six GateDeadlock reasons:
      - missing_artifact_type (no artifact of declared type)
      - schema_mismatch (validator returns errors)
      - id_mismatch (sha rejection comes back as parse_error)
      - location_forgery (declared milestone/phase != file location)
      - parse_error (markdown structurally invalid)
      - unregistered_type (phase declares a type not in registry)
    * Idempotence (same inputs → same result)
    * Telemetry events fire with the expected verdict + reason
"""

from __future__ import annotations

import pytest

from clou.artifacts import compute_content_sha
from clou.phase_acceptance import (
    Advance,
    GateDeadlock,
    check_phase_acceptance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _wrap(
    body: str,
    *,
    milestone: str = "m1",
    phase: str = "p1",
    type_name: str = "execution_summary",
    id_override: str | None = None,
) -> str:
    sha = id_override if id_override is not None else compute_content_sha(body)
    return (
        f'````artifact milestone="{milestone}" phase="{phase}" '
        f'type="{type_name}" id="{sha}"\n'
        f"{body}\n"
        f"````\n"
    )


_VALID_EXEC_SUMMARY_BODY = (
    "status: completed\n"
    "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
    "failures: none\n"
    "blockers: none\n"
)


# ---------------------------------------------------------------------------
# Advance path
# ---------------------------------------------------------------------------


class TestAdvance:
    def test_valid_artifact_returns_advance(self) -> None:
        md = _wrap(_VALID_EXEC_SUMMARY_BODY)
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, Advance)
        assert result.phase == "p1"
        assert result.artifact_type == "execution_summary"
        # content_sha is the sha of the body, computed by the parser
        expected_sha = compute_content_sha(_VALID_EXEC_SUMMARY_BODY)
        assert result.content_sha == expected_sha

    def test_artifact_within_prose_returns_advance(self) -> None:
        md = (
            "# Phase log\n\nSome prose.\n\n"
            f"{_wrap(_VALID_EXEC_SUMMARY_BODY)}\n"
            "Closing prose.\n"
        )
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, Advance)

    def test_artifact_with_embedded_code_block_returns_advance(self) -> None:
        # 4-backtick artifact fence allows embedded 3-backtick
        # code blocks — regression test for the round-1 brutalist
        # finding (embedded fence collision).
        body = (
            "status: completed\n"
            "tasks: 0\nfailures: none\nblockers: none\n"
            "```python\nx = 1\n```\n"
        )
        md = _wrap(body)
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, Advance)


# ---------------------------------------------------------------------------
# GateDeadlock reasons
# ---------------------------------------------------------------------------


class TestGateDeadlockReasons:
    def test_empty_text_returns_missing_artifact_type(self) -> None:
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text="",
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "missing_artifact_type"

    def test_no_matching_type_returns_missing_artifact_type(self) -> None:
        # Has an artifact, but of a different type.
        md = _wrap(
            _VALID_EXEC_SUMMARY_BODY,
            type_name="execution_summary",
        )
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="judgment_layer_spec",
            execution_md_text=md,
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "missing_artifact_type"
        assert "judgment_layer_spec" in result.detail
        assert "execution_summary" in result.detail

    def test_schema_failure_returns_schema_mismatch(self) -> None:
        # Right type, missing required field.
        body = "status: completed\ntasks: 0\nfailures: none\n"  # no blockers
        md = _wrap(body)
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "schema_mismatch"
        assert "blockers" in result.detail

    def test_id_mismatch_returns_parse_error(self) -> None:
        # The parser raises ArtifactParseError for id mismatch;
        # the gate translates it to GateDeadlock(parse_error).
        wrong_sha = "0" * 64
        md = _wrap(_VALID_EXEC_SUMMARY_BODY, id_override=wrong_sha)
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "parse_error"
        assert "declared id" in result.detail.lower()

    def test_location_forgery_returns_location_forgery(self) -> None:
        # Artifact declares it's from a different milestone.
        md = _wrap(_VALID_EXEC_SUMMARY_BODY, milestone="other_milestone")
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "location_forgery"

    def test_phase_mismatch_returns_location_forgery(self) -> None:
        md = _wrap(_VALID_EXEC_SUMMARY_BODY, phase="other_phase")
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "location_forgery"

    def test_unbalanced_fence_returns_parse_error(self) -> None:
        sha = compute_content_sha("body")
        md = (
            f'````artifact milestone="m1" phase="p1" '
            f'type="execution_summary" id="{sha}"\n'
            "body\n"
            # no closing fence
        )
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "parse_error"

    def test_unregistered_type_returns_unregistered_type(self) -> None:
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="not_a_real_type",
            execution_md_text="",
        )
        assert isinstance(result, GateDeadlock)
        assert result.reason == "unregistered_type"
        assert "not_a_real_type" in result.detail


# ---------------------------------------------------------------------------
# Purity / idempotence
# ---------------------------------------------------------------------------


class TestPurity:
    def test_same_inputs_yield_same_result(self) -> None:
        md = _wrap(_VALID_EXEC_SUMMARY_BODY)
        r1 = check_phase_acceptance(
            milestone="m1", phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        r2 = check_phase_acceptance(
            milestone="m1", phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text=md,
        )
        assert r1 == r2  # frozen dataclass equality

    def test_same_failing_inputs_yield_same_failure(self) -> None:
        r1 = check_phase_acceptance(
            milestone="m1", phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text="",
        )
        r2 = check_phase_acceptance(
            milestone="m1", phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text="",
        )
        assert r1 == r2

    def test_no_filesystem_access(self, tmp_path) -> None:
        # The gate is pure over its arguments.  Nothing it does
        # should touch tmp_path or any other filesystem location.
        # We assert this by counting tmp_path contents before
        # and after.
        before = list(tmp_path.iterdir())
        check_phase_acceptance(
            milestone="m1", phase="p1",
            declared_deliverable_type="execution_summary",
            execution_md_text="anything",
        )
        after = list(tmp_path.iterdir())
        assert before == after


# ---------------------------------------------------------------------------
# Result types are frozen
# ---------------------------------------------------------------------------


class TestResultTypes:
    def test_advance_is_frozen(self) -> None:
        r = Advance(phase="p1", content_sha="x", artifact_type="t")
        with pytest.raises(Exception):
            r.phase = "other"  # type: ignore[misc]

    def test_gate_deadlock_is_frozen(self) -> None:
        r = GateDeadlock(
            phase="p1",
            reason="missing_artifact_type",
            detail="x",
        )
        with pytest.raises(Exception):
            r.phase = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# M51 self-test (synthetic fixture per F34, using real M51 spec content)
# ---------------------------------------------------------------------------


class TestM51RegressionFixture:
    """The bug-becomes-feature regression: M51's spec content,
    which was deadlocked under the old filename-coupled
    architecture, advances under the new substance-typed
    architecture.

    Uses a synthesised judgment_layer_spec body with the nine
    canonical headings.  The full-content fixture
    (``test_m51_real_content_advances`` below) wraps M51's
    actual 1193-line execution.md (preserved as research input
    under
    ``.clou/milestones/52-substance-and-capability/research/``)
    in artifact fences and verifies the gate accepts it.
    """

    def test_minimal_judgment_layer_spec_advances(self) -> None:
        body = "\n".join([
            "## Module identity",
            "",
            "Owns judgment-layer spec semantics.",
            "",
            "## Public interface",
            "## Contract",
            "## Failure modes",
            "## Migration partition",
            "## Halt-routing absorption",
            "## Tests-to-write",
            "## LOC accounting",
            "## R5 brutalist summary",
            "",
        ])
        md = _wrap(body, type_name="judgment_layer_spec")
        result = check_phase_acceptance(
            milestone="m1",
            phase="p1",
            declared_deliverable_type="judgment_layer_spec",
            execution_md_text=md,
        )
        assert isinstance(result, Advance)

    def test_m51_real_content_advances(self) -> None:
        """The bug-becomes-feature: M51's autonomous run produced
        1193 lines of substantive judgment-layer-spec content.
        Wrapping that content in artifact fences and running it
        through the new gate should return Advance — proving
        that the deadlocked content, properly typed, would have
        advanced under M52.

        This is the honest M51 regression: same content, new
        architecture, no deadlock.  No synthetic transformation
        beyond wrapping the bytes."""
        from pathlib import Path

        research_path = (
            Path(__file__).resolve().parent.parent
            / ".clou/milestones/52-substance-and-capability"
            / "research/m51-p1-judgment-layer-spec.md"
        )
        if not research_path.exists():
            pytest.skip(
                "M51 research input not present; skip the "
                "real-content regression",
            )
        m51_body = research_path.read_text(encoding="utf-8")
        md = _wrap(
            m51_body,
            milestone="51-orient-cycle-gating",
            phase="p1_judgment_layer_spec",
            type_name="judgment_layer_spec",
        )
        result = check_phase_acceptance(
            milestone="51-orient-cycle-gating",
            phase="p1_judgment_layer_spec",
            declared_deliverable_type="judgment_layer_spec",
            execution_md_text=md,
        )
        assert isinstance(result, Advance), (
            f"M51 real content failed gate: "
            f"{getattr(result, 'reason', '?')}: "
            f"{getattr(result, 'detail', '?')}"
        )
