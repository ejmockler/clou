"""Tests for the artifact-type registry, parser, and validators.

Covers:
    * Round-trip per seed type (emit → parse → validate).
    * Body canonicalization is sha-stable across whitespace and
      line-ending variants.
    * Negative cases: unbalanced fences, nested fences, malformed
      opening line, id/sha mismatch, body-size bound,
      artifact-count bound, cross-location forgery.
    * Validators reject missing required fields / headings.
    * Allowlist is data-derived from registered search_paths.
"""

from __future__ import annotations

import pytest

from clou.artifacts import (
    ARTIFACT_REGISTRY,
    MAX_ARTIFACTS_PER_FILE,
    MAX_BODY_BYTES,
    PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS,
    ArtifactParseError,
    LocationMismatch,
    ParsedArtifact,
    SchemaError,
    canonicalize_body,
    compute_content_sha,
    extract_artifacts,
    get_artifact_type,
    validate_artifact_location,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap(
    body: str,
    *,
    milestone: str = "m1",
    phase: str = "p1",
    type_name: str = "execution_summary",
    id_override: str | None = None,
) -> str:
    """Build a markdown string containing one well-formed artifact.

    By default the id is the body's correct sha (so the parser
    accepts it).  Override with ``id_override`` to produce a
    deliberately-mismatched block for negative tests.

    Uses 4-backtick artifact fences (so the body can freely embed
    standard 3-backtick code blocks).
    """
    sha = id_override if id_override is not None else compute_content_sha(body)
    return (
        f'````artifact milestone="{milestone}" phase="{phase}" '
        f'type="{type_name}" id="{sha}"\n'
        f"{body}\n"
        f"````\n"
    )


# ---------------------------------------------------------------------------
# Body canonicalization
# ---------------------------------------------------------------------------


class TestCanonicalization:
    """Sha must be stable across cosmetic whitespace variants."""

    def test_crlf_and_lf_produce_same_sha(self) -> None:
        body_lf = "hello\nworld\n"
        body_crlf = "hello\r\nworld\r\n"
        assert compute_content_sha(body_lf) == compute_content_sha(body_crlf)

    def test_trailing_whitespace_per_line_normalized(self) -> None:
        body_a = "hello\nworld\n"
        body_b = "hello   \nworld\t\t\n"
        assert compute_content_sha(body_a) == compute_content_sha(body_b)

    def test_interior_whitespace_preserved(self) -> None:
        body_a = "  hello\n  world\n"
        body_b = "hello\nworld\n"
        # Leading whitespace is content; sha must differ.
        assert compute_content_sha(body_a) != compute_content_sha(body_b)

    def test_canonical_form_idempotent(self) -> None:
        body = "hello   \nworld\t\n"
        once = canonicalize_body(body)
        twice = canonicalize_body(once)
        assert once == twice

    def test_blank_lines_preserved(self) -> None:
        body_a = "hello\n\nworld\n"
        body_b = "hello\nworld\n"
        # Blank line is content; sha must differ.
        assert compute_content_sha(body_a) != compute_content_sha(body_b)


# ---------------------------------------------------------------------------
# Round-trip per seed type
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Emit → parse → validate for each registered type."""

    def test_execution_summary_round_trip(self) -> None:
        # Schema aligned with golden_context.render_execution_summary:
        # status, tasks, failures, blockers (no started/completed).
        body = (
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
            "blockers: none\n"
            "deliverable: execution_summary embedded above\n"
        )
        md = _wrap(body, type_name="execution_summary")
        parsed = extract_artifacts(md)
        assert len(parsed) == 1
        artifact = parsed[0]
        assert artifact.type_name == "execution_summary"
        assert artifact.body_sha == artifact.declared_id

        artifact_type = get_artifact_type("execution_summary")
        assert artifact_type is not None
        errors = artifact_type.validator(artifact)
        assert errors == [], f"unexpected errors: {errors}"

    def test_judgment_layer_spec_round_trip(self) -> None:
        # Concrete schema (F34): nine markdown headings whose
        # normalized text matches the enumerated list.
        body = "\n".join([
            "## §1 Module identity",
            "",
            "Module owns judgment-layer spec semantics.",
            "",
            "## §2 Public interface",
            "",
            "decide_dispatch + decide_halt_routing.",
            "",
            "## §3 Contract",
            "",
            "Preconditions, postconditions, invariants.",
            "",
            "## §4 Failure modes",
            "",
            "Missing-judgment fallback, parse-failure tolerance.",
            "",
            "## §5 Migration partition",
            "",
            "p2 ranges vs p3 ranges.",
            "",
            "## §6 Halt-routing absorption",
            "",
            "M50 deletion markers.",
            "",
            "## §7 Tests-to-write",
            "",
            "Per-clause test list.",
            "",
            "## §8 LOC accounting",
            "",
            "Net reduction commitment.",
            "",
            "## §9 R5 brutalist summary",
            "",
            "Multi-CLI panel summary.",
            "",
        ])
        md = _wrap(body, type_name="judgment_layer_spec")
        parsed = extract_artifacts(md)
        assert len(parsed) == 1
        artifact = parsed[0]

        artifact_type = get_artifact_type("judgment_layer_spec")
        assert artifact_type is not None
        errors = artifact_type.validator(artifact)
        assert errors == [], f"unexpected errors: {errors}"

    def test_normalize_sha_bit_stable_property(self) -> None:
        """Property: arbitrary body text, after canonicalize +
        re-canonicalize, produces the same sha."""
        bodies = [
            "",
            "x",
            "x\n",
            "  x  \n  y  \n",
            "x\r\ny\r\nz\r\n",
            "## heading\n\nbody with **markdown**.\n",
            "no trailing newline",
        ]
        for body in bodies:
            once = canonicalize_body(body)
            twice = canonicalize_body(once)
            assert once == twice
            assert compute_content_sha(body) == compute_content_sha(once)


# ---------------------------------------------------------------------------
# Negative cases — parser rejection classes
# ---------------------------------------------------------------------------


class TestNegativeCases:
    """Each rejection class produces ArtifactParseError."""

    def test_unbalanced_fence_no_closing(self) -> None:
        sha = compute_content_sha("body")
        md = (
            f'````artifact milestone="m" phase="p" '
            f'type="execution_summary" id="{sha}"\n'
            "body\n"
            # no closing fence
        )
        with pytest.raises(ArtifactParseError, match="unbalanced"):
            extract_artifacts(md)

    def test_nested_artifact_fence_rejected(self) -> None:
        inner_sha = compute_content_sha("inner")
        outer_body = (
            f'````artifact milestone="m" phase="p" '
            f'type="execution_summary" id="{inner_sha}"\n'
            "inner\n"
            "````\n"
        )
        outer_sha = compute_content_sha(outer_body)
        md = (
            f'````artifact milestone="m" phase="p" '
            f'type="execution_summary" id="{outer_sha}"\n'
            f"{outer_body}\n"
            "````\n"
        )
        with pytest.raises(ArtifactParseError, match="nested"):
            extract_artifacts(md)

    def test_malformed_opening_missing_id_raises_diagnostic(self) -> None:
        # Two-pass parser: line starts with ````artifact but
        # doesn't match the strict regex — raise a diagnostic.
        # A worker that intended an artifact must see WHY it
        # failed rather than getting "no artifact found."
        md = (
            '````artifact milestone="m" phase="p" '
            'type="execution_summary"\n'
            "body\n"
            "````\n"
        )
        with pytest.raises(
            ArtifactParseError, match="malformed artifact opening",
        ):
            extract_artifacts(md)

    def test_malformed_opening_missing_attribute_value_raises(
        self,
    ) -> None:
        md = (
            '````artifact milestone="m" phase="p" '
            'type="execution_summary" id=\n'
            "body\n"
            "````\n"
        )
        with pytest.raises(
            ArtifactParseError, match="malformed artifact opening",
        ):
            extract_artifacts(md)

    def test_id_mismatch_rejected(self) -> None:
        body = "this body has its own sha"
        wrong_sha = "0" * 64
        md = _wrap(body, id_override=wrong_sha)
        with pytest.raises(
            ArtifactParseError,
            match="declared id .* does not match body sha",
        ):
            extract_artifacts(md)

    def test_body_size_bound_rejected(self) -> None:
        # Construct a body strictly larger than MAX_BODY_BYTES.
        oversized = "x" * (MAX_BODY_BYTES + 1)
        md = _wrap(oversized)
        with pytest.raises(ArtifactParseError, match="bytes; max is"):
            extract_artifacts(md)

    def test_artifact_count_bound_rejected(self) -> None:
        # Construct MAX_ARTIFACTS_PER_FILE + 1 artifacts.
        bodies = [
            f"body number {i}" for i in range(MAX_ARTIFACTS_PER_FILE + 1)
        ]
        md = "\n\n".join(_wrap(b) for b in bodies)
        with pytest.raises(ArtifactParseError, match="malformed"):
            extract_artifacts(md)


# ---------------------------------------------------------------------------
# Plain code blocks ignored
# ---------------------------------------------------------------------------


class TestNonArtifactFences:
    """Plain markdown code blocks must be ignored, not parsed
    as artifacts.  Otherwise legitimate code samples in prose
    would trip the parser."""

    def test_python_code_block_ignored(self) -> None:
        md = (
            "Some prose.\n"
            "```python\n"
            "def f(): pass\n"
            "```\n"
            "More prose.\n"
        )
        assert extract_artifacts(md) == []

    def test_unmarked_code_block_ignored(self) -> None:
        md = (
            "```\n"
            "raw code\n"
            "```\n"
        )
        assert extract_artifacts(md) == []

    def test_artifact_after_code_block(self) -> None:
        body = (
            "status: completed\ntasks: 0\nfailures: none\nblockers: none\n"
        )
        md = (
            "```python\n"
            "x = 1\n"
            "```\n"
            f"{_wrap(body)}\n"
        )
        artifacts = extract_artifacts(md)
        assert len(artifacts) == 1


# ---------------------------------------------------------------------------
# CRITICAL: Embedded code fences inside an artifact body
# ---------------------------------------------------------------------------


class TestArtifactBodyContainingCodeFences:
    """Worker output legitimately contains 3-backtick code blocks
    (tool transcripts, code samples).  The 4-backtick artifact
    fence must NOT be terminated by an inner 3-backtick close.

    These tests pin the regression class p1.G round 1 surfaced:
    bare ``` inside a body would silently truncate the artifact
    under the original 3-tick grammar."""

    def test_artifact_body_contains_python_code_block(self) -> None:
        body = (
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
            "blockers: none\n"
            "\n"
            "Sample output:\n"
            "```python\n"
            "def f():\n"
            "    return 42\n"
            "```\n"
            "deliverable: see code above\n"
        )
        md = _wrap(body)
        artifacts = extract_artifacts(md)
        assert len(artifacts) == 1
        assert "def f()" in artifacts[0].body
        assert "return 42" in artifacts[0].body

    def test_artifact_body_contains_unmarked_code_block(self) -> None:
        body = (
            "status: completed\n"
            "tasks: 0\n"
            "failures: none\n"
            "blockers: none\n"
            "```\n"
            "raw transcript\n"
            "more transcript\n"
            "```\n"
        )
        md = _wrap(body)
        artifacts = extract_artifacts(md)
        assert len(artifacts) == 1
        assert "raw transcript" in artifacts[0].body

    def test_artifact_body_contains_multiple_code_blocks(self) -> None:
        body = (
            "status: completed\n"
            "tasks: 0\n"
            "failures: none\n"
            "blockers: none\n"
            "\n"
            "Block 1:\n"
            "```bash\n"
            "echo hello\n"
            "```\n"
            "\n"
            "Block 2:\n"
            "```\n"
            "raw\n"
            "```\n"
        )
        md = _wrap(body)
        artifacts = extract_artifacts(md)
        assert len(artifacts) == 1
        assert "echo hello" in artifacts[0].body
        assert "Block 2" in artifacts[0].body


# ---------------------------------------------------------------------------
# Cross-location forgery
# ---------------------------------------------------------------------------


class TestLocationValidation:
    """An artifact whose declared milestone/phase doesn't match
    its filesystem location is forged and must be rejected."""

    def test_matching_location_passes(self) -> None:
        artifact = ParsedArtifact(
            milestone="m1",
            phase="p1",
            type_name="execution_summary",
            declared_id="x",
            body="body",
            body_sha="x",
        )
        result = validate_artifact_location(
            artifact, expected_milestone="m1", expected_phase="p1",
        )
        assert result is None

    def test_milestone_mismatch_returns_LocationMismatch(self) -> None:
        artifact = ParsedArtifact(
            milestone="other",
            phase="p1",
            type_name="execution_summary",
            declared_id="x",
            body="body",
            body_sha="x",
        )
        result = validate_artifact_location(
            artifact, expected_milestone="m1", expected_phase="p1",
        )
        assert isinstance(result, LocationMismatch)
        assert result.declared_milestone == "other"
        assert result.expected_milestone == "m1"
        assert "does not match" in result.message

    def test_phase_mismatch_returns_LocationMismatch(self) -> None:
        artifact = ParsedArtifact(
            milestone="m1",
            phase="other_phase",
            type_name="execution_summary",
            declared_id="x",
            body="body",
            body_sha="x",
        )
        result = validate_artifact_location(
            artifact, expected_milestone="m1", expected_phase="p1",
        )
        assert isinstance(result, LocationMismatch)
        assert result.declared_phase == "other_phase"


# ---------------------------------------------------------------------------
# Validator rejection cases
# ---------------------------------------------------------------------------


class TestExecutionSummaryValidator:
    """Required field absence produces SchemaError.  Schema
    aligned with golden_context.render_execution_summary."""

    def test_missing_status_field(self) -> None:
        body = (
            "tasks: 0\n"
            "failures: none\n"
            "blockers: none\n"
        )
        md = _wrap(body)
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(artifact)
        assert any(e.field_path == "status" for e in errors)

    def test_missing_tasks_field(self) -> None:
        body = (
            "status: completed\n"
            "failures: none\n"
            "blockers: none\n"
        )
        md = _wrap(body)
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(artifact)
        assert any(e.field_path == "tasks" for e in errors)

    def test_missing_failures_field(self) -> None:
        body = (
            "status: completed\n"
            "tasks: 0\n"
            "blockers: none\n"
        )
        md = _wrap(body)
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(artifact)
        assert any(e.field_path == "failures" for e in errors)

    def test_missing_blockers_field(self) -> None:
        body = (
            "status: completed\n"
            "tasks: 0\n"
            "failures: none\n"
        )
        md = _wrap(body)
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(artifact)
        assert any(e.field_path == "blockers" for e in errors)

    def test_field_with_blank_value_does_not_satisfy(self) -> None:
        body = (
            "status:\n"
            "tasks: 0\n"
            "failures: none\n"
            "blockers: none\n"
        )
        md = _wrap(body)
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(artifact)
        assert any(e.field_path == "status" for e in errors)

    def test_validates_canonical_writer_output(self) -> None:
        """The output of golden_context.render_execution_summary
        wrapped as an artifact must validate."""
        from clou.golden_context import render_execution_summary
        summary_md = render_execution_summary(
            status="completed",
            tasks_total=2,
            tasks_completed=2,
            tasks_failed=0,
            tasks_in_progress=0,
            failures="none",
            blockers="none",
        )
        # The render output starts with "## Summary\n"; strip the
        # heading so the body looks like raw labelled lines.
        body = summary_md.replace("## Summary\n", "", 1)
        md = _wrap(body)
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(artifact)
        assert errors == [], (
            f"canonical writer output failed validation: {errors}"
        )


class TestJudgmentLayerSpecValidator:
    """Heading presence is the gate; missing headings produce
    SchemaError."""

    def test_missing_one_heading(self) -> None:
        # Eight of the nine required heading prefixes.
        body = "\n".join([
            "## Module identity",
            "## Public interface",
            "## Contract",
            "## Failure modes",
            "## Migration partition",
            "## Halt-routing absorption",
            "## Tests-to-write",
            "## LOC accounting",
            # missing "R5 brutalist summary"
            "",
        ])
        md = _wrap(body, type_name="judgment_layer_spec")
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["judgment_layer_spec"].validator(
            artifact,
        )
        # Primary alias of the missing section is "r5 brutalist".
        assert any(
            e.field_path == "r5 brutalist" for e in errors
        )

    def test_no_headings_yields_nine_errors(self) -> None:
        body = "this body has no headings at all\n"
        md = _wrap(body, type_name="judgment_layer_spec")
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["judgment_layer_spec"].validator(
            artifact,
        )
        assert len(errors) == 9

    def test_section_marks_normalized(self) -> None:
        # Mix of section-mark variants normalize correctly.
        body = "\n".join([
            "##  §  1  Module identity",
            "## §2  Public interface",
            "##  3.  Contract",
            "## 4) Failure modes",
            "## (5) Migration partition",
            "## [6] Halt-routing absorption",
            "## §7 Tests-to-write",
            "## §8 LOC accounting",
            "## §9 R5 brutalist summary",
            "",
        ])
        md = _wrap(body, type_name="judgment_layer_spec")
        artifact = extract_artifacts(md)[0]
        errors = ARTIFACT_REGISTRY["judgment_layer_spec"].validator(
            artifact,
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


class TestRegistry:
    """Registry is frozen and seed types are present."""

    def test_seed_types_present(self) -> None:
        assert "execution_summary" in ARTIFACT_REGISTRY
        assert "judgment_layer_spec" in ARTIFACT_REGISTRY

    def test_registry_is_immutable(self) -> None:
        # MappingProxyType refuses item assignment.
        with pytest.raises(TypeError):
            ARTIFACT_REGISTRY["new_type"] = "anything"  # type: ignore[index]

    def test_get_artifact_type_returns_None_for_unknown(self) -> None:
        assert get_artifact_type("nonexistent") is None

    def test_artifact_type_dataclass_is_frozen(self) -> None:
        artifact_type = ARTIFACT_REGISTRY["execution_summary"]
        with pytest.raises(Exception):
            # frozen dataclass refuses field mutation
            artifact_type.name = "renamed"  # type: ignore[misc]


class TestPhaseAcceptanceAllowlist:
    """Allowlist is data-derived from search_paths."""

    def test_allowlist_contains_execution_md(self) -> None:
        assert "execution.md" in PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS

    def test_allowlist_is_frozenset(self) -> None:
        assert isinstance(
            PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS, frozenset,
        )

    def test_allowlist_only_contains_search_paths(self) -> None:
        # Every entry must come from some artifact type.
        all_search_paths: set[str] = set()
        for artifact_type in ARTIFACT_REGISTRY.values():
            all_search_paths.update(artifact_type.search_paths)
        assert (
            PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS == frozenset(all_search_paths)
        )


# ---------------------------------------------------------------------------
# End-to-end smoke
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Realistic execution.md content with embedded artifact."""

    def test_artifact_within_prose(self) -> None:
        body = (
            "status: completed\n"
            "tasks: 1 total, 1 completed, 0 failed, 0 in_progress\n"
            "failures: none\n"
            "blockers: none\n"
            "deliverable: see embedded execution_summary above\n"
        )
        md = (
            "# Phase execution log\n\n"
            "Some prose explaining what happened.\n\n"
            "Below is the typed deliverable:\n\n"
            f"{_wrap(body)}\n"
            "And some closing prose.\n"
        )
        artifacts = extract_artifacts(md)
        assert len(artifacts) == 1
        # Validates against schema:
        errors = ARTIFACT_REGISTRY["execution_summary"].validator(
            artifacts[0],
        )
        assert errors == []
        # And against location:
        loc = validate_artifact_location(
            artifacts[0],
            expected_milestone="m1",
            expected_phase="p1",
        )
        assert loc is None
