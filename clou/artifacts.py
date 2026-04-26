"""Artifact-type registry for substance-typed phase deliverables.

M52 Move 1. Phase deliverables are content-typed artifacts, not
filename-coupled paths.  This module owns the registry of artifact
types, the fenced-block grammar, and the validator pipeline.

The phase-acceptance gate (``clou/phase_acceptance.py``) reads typed
artifacts via ``extract_artifacts`` and routes per the validator
verdict.  Workers emit typed artifacts as fenced blocks inside the
file their tier is permitted to write (``execution.md`` for worker
tier).

Identity is a 4-tuple: ``(milestone, phase, type, content_sha)``.
The content_sha alone is the body fingerprint (dedup signal); the
4-tuple is cross-location identity.  The fenced block declares all
four attributes::

    ```artifact milestone="<m>" phase="<p>" type="<name>" id="<sha>"
    ...body...
    ```

See ``.clou/milestones/52-substance-and-capability/`` for the full
spec.

Public API:
    ArtifactType                   --- registered type (frozen)
    ParsedArtifact                 --- one extracted block
    SchemaError                    --- validator failure detail
    ARTIFACT_REGISTRY              --- name -> ArtifactType (immutable)
    extract_artifacts(text)        --- parse fenced blocks
    validate_artifact_location()   --- reject cross-location forgery
    canonicalize_body(text)        --- whitespace-normalize for sha
    compute_content_sha(text)      --- sha256 of canonical body
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from clou import telemetry

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parser bounds (R1 — F10, F18)
# ---------------------------------------------------------------------------
#
# Hard limits (memory-deterministic; refusal):
MAX_BODY_BYTES: int = 1 * 1024 * 1024          # 1 MiB per artifact
MAX_ARTIFACTS_PER_FILE: int = 32                # pathological input

# Advisory limits (observability; emit telemetry, do not refuse).
ADVISORY_PARSE_MS_PER_ARTIFACT: int = 1000
ADVISORY_PARSE_MS_PER_FILE: int = 30_000

# Note: nested artifact fences (artifact-in-artifact) are rejected
# unconditionally.  There is no MAX_FENCE_DEPTH knob — depth is
# fixed at 1.  This is enforced by ``_OPENING_LINE_RE`` matching
# inside the body walk and raising ``ArtifactParseError``.


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaError:
    """One validation failure.  Multiple may surface per artifact."""

    field_path: str
    """Dotted path into the artifact body, or empty for top-level."""

    message: str
    """Human-readable explanation."""


@dataclass(frozen=True)
class ParsedArtifact:
    """One extracted ``artifact`` fenced block.

    The four declared attributes (milestone, phase, type, id) are
    parsed from the opening fence line.  ``body`` is the raw text
    between the opening and closing fences.  ``body_sha`` is the
    sha256 of the canonicalized body — populated by the parser so
    callers don't recompute it.

    A ``ParsedArtifact`` is "extracted" but not yet "validated".
    ``validate_artifact_location`` checks the milestone/phase
    declared here against the file's filesystem location;
    per-type validators check the body shape.
    """

    milestone: str
    phase: str
    type_name: str
    declared_id: str
    body: str
    body_sha: str


@dataclass(frozen=True)
class ArtifactType:
    """A registered artifact type.

    ``search_paths`` are paths relative to ``phase_dir/`` where the
    phase-acceptance gate looks for this type's deliverable.  Today
    every seed type uses ``("execution.md",)`` (worker tier).
    M52b will introduce per-tier search paths as the capability
    registry lands.

    ``validator`` is a pure function over a ``ParsedArtifact``,
    returning a list of ``SchemaError``.  Empty list = valid.
    """

    name: str
    search_paths: tuple[str, ...]
    validator: Callable[[ParsedArtifact], list[SchemaError]]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ArtifactType.name must be non-empty")
        if not self.search_paths:
            raise ValueError(
                f"ArtifactType.search_paths must be non-empty "
                f"(type {self.name!r})",
            )
        for path in self.search_paths:
            if not path or not path.strip():
                raise ValueError(
                    f"ArtifactType.search_paths contains empty "
                    f"path (type {self.name!r})",
                )


# ---------------------------------------------------------------------------
# Body canonicalization + sha
# ---------------------------------------------------------------------------


def canonicalize_body(body: str) -> str:
    """Canonical form for sha-stable identity.

    - CRLF / lone CR → LF.
    - All trailing Unicode whitespace stripped per line — including
      NBSP (U+00A0), zero-width space, vertical tab, form feed, and
      ASCII space/tab.  Bare ``str.rstrip()`` covers the unicode
      whitespace class plus the ASCII set.
    - Final newline preserved (or absent) as in the input.
    - No interior changes — the body's shape, including blank lines
      and indentation, is preserved.

    Two artifacts with the same logical body but different line
    endings or trailing whitespace (ASCII or unicode) produce the
    same canonical form and therefore the same sha.
    """
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = [line.rstrip() for line in lines]
    return "\n".join(cleaned)


def compute_content_sha(body: str) -> str:
    """sha256 hex digest of ``canonicalize_body(body)``."""
    canonical = canonicalize_body(body).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Fenced-block grammar
# ---------------------------------------------------------------------------
#
# Artifact fences use FOUR backticks so that workers can freely
# embed standard 3-backtick code blocks inside artifact bodies.
# (Standard markdown rule: a fence must be at least as long as the
# opening; a 4-tick artifact fence cannot be terminated by a
# 3-tick code-block close.)
#
# Opening fence (load-bearing — parser brittleness lives here):
#
#   ````artifact milestone="<m>" phase="<p>" type="<name>" id="<sha>"
#
# Attributes are required, order-fixed (milestone, phase, type, id),
# double-quoted, separated by single spaces.  No trailing whitespace
# on the opening line.  Empty values for milestone, phase, or type
# are rejected at parse time.
#
# Closing fence (also 4 backticks; matches only artifact closes,
# never standard 3-backtick code blocks):
#
#   ````
#
# Near-match diagnostics: a line that STARTS with `````artifact`
# but doesn't match the strict opening regex raises
# ``ArtifactParseError`` with a specific deviation message rather
# than silently skipping.

_OPENING_LINE_STRICT_RE = re.compile(
    r'^````artifact'
    r' milestone="(?P<milestone>[^"]+)"'
    r' phase="(?P<phase>[^"]+)"'
    r' type="(?P<type>[^"]+)"'
    r' id="(?P<id>[^"]+)"'
    r'\s*$'
)

# Loose match: any line beginning with the 4-tick + "artifact"
# token.  Used to detect "intended an artifact but got the syntax
# wrong" cases for friendly diagnostics.
_OPENING_LINE_LOOSE_RE = re.compile(r'^````artifact\b')

# Closes match exactly four backticks (with optional trailing
# whitespace).  3-tick fences inside the body are ignored.
_CLOSING_LINE_RE = re.compile(r'^````\s*$')


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ArtifactParseError(ValueError):
    """Raised by ``extract_artifacts`` for structurally invalid input.

    Includes:
    - unbalanced fences (opening without closing)
    - nested artifact fences (artifact-in-artifact, depth > 1)
    - malformed opening line (any of the four attributes
      missing or malformed)
    - id mismatch (body sha != declared id)
    - body size > MAX_BODY_BYTES
    - artifact count > MAX_ARTIFACTS_PER_FILE
    """


def extract_artifacts(markdown_text: str) -> list[ParsedArtifact]:
    """Extract all ``artifact`` fenced blocks from markdown text.

    Returns a list of :class:`ParsedArtifact` instances, one per
    well-formed artifact block.  Raises :class:`ArtifactParseError`
    for structurally invalid input (including any block whose
    declared id mismatches its body sha).

    Plain markdown code blocks (` ```python `, etc.) are ignored —
    only `` ```artifact `` opens an artifact block.

    Bounds:
    - Refuses input producing > ``MAX_ARTIFACTS_PER_FILE`` blocks.
    - Refuses any block whose body exceeds ``MAX_BODY_BYTES``.
    - Refuses nested artifact fences (depth > 1).
    """
    if not markdown_text:
        return []

    lines = markdown_text.splitlines(keepends=False)
    artifacts: list[ParsedArtifact] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        opening_match = _OPENING_LINE_STRICT_RE.match(line)
        if opening_match is None:
            # Two-pass: if the line LOOKS like it intended to be
            # an artifact opening but didn't pass strict parsing,
            # raise a diagnostic rather than silently skipping.
            # Workers iterating on the format need to see that the
            # syntax is wrong, not that the artifact "wasn't found."
            if _OPENING_LINE_LOOSE_RE.match(line) is not None:
                telemetry.event(
                    "artifact.rejected",
                    reason="malformed_opening",
                    line=i + 1,
                )
                msg = (
                    f"malformed artifact opening at line {i + 1}: "
                    f"expected exactly "
                    f'`````artifact milestone="<m>" phase="<p>" '
                    f'type="<name>" id="<sha>"`. '
                    f"got: {line!r}"
                )
                raise ArtifactParseError(msg)
            i += 1
            continue

        # Found a strict artifact opening.  Walk forward to the
        # closing 4-tick fence.  Reject any nested artifact
        # opening (depth > 1).
        opening_lineno = i
        body_start = i + 1
        body_end = -1
        for j in range(body_start, n):
            inner = lines[j]
            if _OPENING_LINE_STRICT_RE.match(inner) is not None:
                telemetry.event(
                    "artifact.rejected",
                    reason="nested_fence",
                    outer_line=opening_lineno + 1,
                    inner_line=j + 1,
                )
                msg = (
                    f"nested artifact fence at line {j + 1} "
                    f"(opened at line {opening_lineno + 1}); "
                    f"artifact bodies cannot contain other "
                    f"artifacts"
                )
                raise ArtifactParseError(msg)
            if _CLOSING_LINE_RE.match(inner) is not None:
                body_end = j
                break

        if body_end < 0:
            telemetry.event(
                "artifact.rejected",
                reason="unbalanced_fence",
                opening_line=opening_lineno + 1,
            )
            msg = (
                f"unbalanced artifact fence at line "
                f"{opening_lineno + 1}: no closing ```` found"
            )
            raise ArtifactParseError(msg)

        body = "\n".join(lines[body_start:body_end])
        try:
            body_bytes_count = len(body.encode("utf-8"))
        except UnicodeEncodeError as exc:
            telemetry.event(
                "artifact.rejected",
                reason="malformed_utf8",
                opening_line=opening_lineno + 1,
            )
            msg = (
                f"artifact body at line {opening_lineno + 1} "
                f"contains malformed UTF-8: {exc}"
            )
            raise ArtifactParseError(msg) from None
        if body_bytes_count > MAX_BODY_BYTES:
            telemetry.event(
                "artifact.rejected",
                reason="body_size",
                value=body_bytes_count,
                limit=MAX_BODY_BYTES,
            )
            telemetry.event(
                "artifact.parse_bound_hit",
                bound="body_size",
                value=body_bytes_count,
                limit=MAX_BODY_BYTES,
            )
            msg = (
                f"artifact body at line {opening_lineno + 1} is "
                f"{body_bytes_count} bytes; max is {MAX_BODY_BYTES}"
            )
            raise ArtifactParseError(msg)

        # Pre-append count check (CLAUDE M1): incrementing the
        # accepted count past the limit must NOT also emit a
        # spurious ``artifact.parsed`` event for an artifact that
        # is about to be rejected.
        if len(artifacts) >= MAX_ARTIFACTS_PER_FILE:
            telemetry.event(
                "artifact.rejected",
                reason="artifact_count",
                value=len(artifacts) + 1,
                limit=MAX_ARTIFACTS_PER_FILE,
            )
            telemetry.event(
                "artifact.parse_bound_hit",
                bound="artifact_count",
                value=len(artifacts) + 1,
                limit=MAX_ARTIFACTS_PER_FILE,
            )
            msg = (
                f"file contains > {MAX_ARTIFACTS_PER_FILE} "
                f"artifact blocks; rejecting as malformed"
            )
            raise ArtifactParseError(msg)

        body_sha = compute_content_sha(body)
        declared_id = opening_match.group("id")
        if body_sha != declared_id:
            telemetry.event(
                "artifact.rejected",
                reason="id_mismatch",
                type=opening_match.group("type"),
                declared_id=declared_id,
                computed_sha=body_sha,
            )
            msg = (
                f"artifact at line {opening_lineno + 1}: "
                f"declared id {declared_id!r} does not match "
                f"body sha {body_sha!r}"
            )
            raise ArtifactParseError(msg)

        parsed = ParsedArtifact(
            milestone=opening_match.group("milestone"),
            phase=opening_match.group("phase"),
            type_name=opening_match.group("type"),
            declared_id=declared_id,
            body=body,
            body_sha=body_sha,
        )
        artifacts.append(parsed)
        telemetry.event(
            "artifact.parsed",
            type=parsed.type_name,
            id=parsed.declared_id,
            milestone=parsed.milestone,
            phase=parsed.phase,
            body_bytes=body_bytes_count,
        )

        i = body_end + 1

    return artifacts


# ---------------------------------------------------------------------------
# Cross-location forgery rejection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocationMismatch:
    """One cross-location forgery detail."""

    expected_milestone: str
    expected_phase: str
    declared_milestone: str
    declared_phase: str

    @property
    def message(self) -> str:
        return (
            f"declared milestone={self.declared_milestone!r} "
            f"phase={self.declared_phase!r} does not match file "
            f"location milestone={self.expected_milestone!r} "
            f"phase={self.expected_phase!r}"
        )


def validate_artifact_location(
    parsed: ParsedArtifact,
    *,
    expected_milestone: str,
    expected_phase: str,
) -> LocationMismatch | None:
    """Reject artifacts whose declared (milestone, phase) doesn't
    match the file's filesystem location.

    Returns ``None`` if the artifact's declaration matches the
    expected location, otherwise a :class:`LocationMismatch`
    describing the discrepancy.

    This catches paste-and-forge: an artifact body produced under
    milestone A that someone (or a confused agent) drops into
    milestone B's execution.md should not validate.  The
    content-sha alone cannot detect this; the 4-tuple identity
    requires location agreement.
    """
    if (
        parsed.milestone == expected_milestone
        and parsed.phase == expected_phase
    ):
        return None
    return LocationMismatch(
        expected_milestone=expected_milestone,
        expected_phase=expected_phase,
        declared_milestone=parsed.milestone,
        declared_phase=parsed.phase,
    )


# ---------------------------------------------------------------------------
# Seed validators
# ---------------------------------------------------------------------------
#
# Two seed types ship with M52 (R1).  Other types are added in
# future milestones as phases need them.


def _validate_execution_summary(
    parsed: ParsedArtifact,
) -> list[SchemaError]:
    """Generic worker-output schema.

    Aligned with ``clou.golden_context.render_execution_summary``
    so that artifacts emitted by the canonical writer validate
    cleanly.  Required fields:

    - ``status``    — one of ``in_progress | completed | failed | ...``
                      (per ``TASK_STATUSES`` in golden_context)
    - ``tasks``     — count summary, e.g. ``"3 total, 2 completed, ..."``
    - ``failures``  — ``"none"`` or per-failure detail
    - ``blockers``  — ``"none"`` or per-blocker detail

    Optional fields:

    - ``deliverable`` — references the typed output if present

    The validator is a presence check: each required field must
    appear as ``label: <non-empty>`` on at least one line.  Header-
    or bullet-prefixed lines (``## status: ...``, ``- status: ...``)
    are tolerated.  The validator is NOT semantic — it accepts any
    non-empty value for ``status``; deeper validation is the
    responsibility of a future schema iteration when the registry
    tier system lands (M52b).
    """
    errors: list[SchemaError] = []
    body = parsed.body

    required = ("status", "tasks", "failures", "blockers")
    for field_name in required:
        pattern = re.compile(
            rf"(?m)^(?:#+ |\* |- )?{re.escape(field_name)}:[ \t]+\S",
        )
        if pattern.search(body) is None:
            errors.append(
                SchemaError(
                    field_path=field_name,
                    message=(
                        f"required field {field_name!r} "
                        f"not found in body"
                    ),
                )
            )

    return errors


# Concrete schema for the M51 fixture (F34).  The validator
# passes iff the body contains markdown headings whose normalized
# text PREFIX-matches each canonical name.  Prefix matching is
# the right tolerance: M51's actual headings are
# "Public interface — both halves", "Migration path from
# coordinator.py — partitioned p2 vs p3", etc.  The canonical
# names are unambiguous prefixes — no two canonicals are prefixes
# of each other, so the prefix match is unambiguous.
#
# "migration partition" is a special case: M51 wrote "migration
# path", which doesn't prefix-match "migration partition".  We
# accept either prefix as canonical.  The intent is "the section
# that partitions migration scope between phases" — both names
# express it.
_JUDGMENT_LAYER_SPEC_HEADINGS: tuple[tuple[str, ...], ...] = (
    ("module identity",),
    ("public interface",),
    ("contract",),
    ("failure modes",),
    ("migration partition", "migration path"),
    ("halt-routing absorption",),
    ("tests-to-write",),
    ("loc accounting",),
    ("r5 brutalist", "brutalist multi-cli", "brutalist summary"),
)


def _normalize_heading(heading: str) -> str:
    """Lowercase + collapse whitespace + strip section numbers.

    Maps "##  §1 Module identity " -> "module identity".
    Tolerant to several section-mark variants:

    - ``§1``, ``§ 1``, ``§``      — section glyph + optional number
    - ``1.``, ``1)``              — bare number with separator
    - ``(1)``, ``[1]``            — bracketed number
    - ``1 -``, ``1:``             — number with dash/colon separator
    """
    text = heading.strip().lstrip("#").strip()
    # Strip leading section-mark + number.  Recognise:
    #   §<digits>[.]    section glyph + number
    #   §               bare section glyph
    #   (<digits>)      parenthesized
    #   [<digits>]      bracketed
    #   <digits>[.)]    bare number with optional separator
    text = re.sub(
        r"^(?:"
        r"§\s*\d+\.?|"          # § 1 or §1.
        r"§|"                    # bare §
        r"\(\d+\)|"              # (1)
        r"\[\d+\]|"              # [1]
        r"\d+[\.\):\-]?"        # 1, 1., 1), 1:, 1-
        r")\s*",
        "",
        text,
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def _validate_judgment_layer_spec(
    parsed: ParsedArtifact,
) -> list[SchemaError]:
    """M51 fixture schema (F34).

    Validator passes iff the body contains a markdown heading
    that PREFIX-matches each canonical heading name (or one of
    its accepted aliases).  Headings may be at any level
    (``#``, ``##``, ``###``, ...) and may carry section marks
    (``§1`` / ``1.`` / etc.) which are stripped during
    normalization.

    The prefix-match tolerance accommodates M51's actual heading
    style ("Public interface — both halves",
    "Migration path from coordinator.py", etc.) where the
    canonical name is the leading topic and the suffix is
    descriptive.
    """
    errors: list[SchemaError] = []
    headings_in_body: list[str] = []
    for line in parsed.body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings_in_body.append(_normalize_heading(stripped))

    for required_alternatives in _JUDGMENT_LAYER_SPEC_HEADINGS:
        # Accept the section if ANY canonical name (or alias) is
        # a prefix of ANY heading in the body.
        matched = any(
            heading.startswith(canonical)
            for canonical in required_alternatives
            for heading in headings_in_body
        )
        if not matched:
            primary = required_alternatives[0]
            errors.append(
                SchemaError(
                    field_path=primary,
                    message=(
                        f"required heading prefix {primary!r} "
                        f"(or aliases {list(required_alternatives[1:])!r}) "
                        f"not found in body"
                    ),
                )
            )

    return errors


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _build_registry() -> Mapping[str, ArtifactType]:
    """Construct the seed registry.

    Frozen at module-import time.  Mutation requires a code change
    to this function (and a corresponding test update).  No
    runtime registration API.
    """
    seeds: dict[str, ArtifactType] = {
        "execution_summary": ArtifactType(
            name="execution_summary",
            search_paths=("execution.md",),
            validator=_validate_execution_summary,
            description=(
                "Generic worker output: status, timestamps, "
                "tasks, failures, blockers, deliverable."
            ),
        ),
        "judgment_layer_spec": ArtifactType(
            name="judgment_layer_spec",
            search_paths=("execution.md",),
            validator=_validate_judgment_layer_spec,
            description=(
                "M51 synthetic fixture: nine-section spec body, "
                "validated by heading presence."
            ),
        ),
    }
    return MappingProxyType(seeds)


ARTIFACT_REGISTRY: Mapping[str, ArtifactType] = _build_registry()


def get_artifact_type(name: str) -> ArtifactType | None:
    """Look up a registered type by name.

    Returns ``None`` for unregistered names.  Callers that want a
    hard-failure should check membership first.
    """
    return ARTIFACT_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Phase-acceptance allowlist (F36)
# ---------------------------------------------------------------------------
#
# Data-derived: union of ``search_paths`` across all registered
# types.  ``clou/phase_acceptance.py`` may use any path in this
# allowlist as a literal; other paths are forbidden by the AST
# check (``tests/test_no_filename_completion.py``).
PHASE_ACCEPTANCE_DELIVERABLE_LOCATIONS: frozenset[str] = frozenset(
    path
    for artifact_type in ARTIFACT_REGISTRY.values()
    for path in artifact_type.search_paths
)


# ---------------------------------------------------------------------------
# Phase.md deliverable-type extraction (M52 R2)
# ---------------------------------------------------------------------------


#: Regex for the ``## Deliverable`` section's ``type:`` line.  The
#: section is the canonical declaration of what artifact type a phase
#: produces; the gate reads this to know which type to look for in
#: ``execution.md``.  Format::
#:
#:     ## Deliverable
#:     type: <ArtifactTypeName>
#:     acceptance: schema_pass
#:
#: Legacy phase.md files (pre-M52) use ``Single deliverable file:
#: <path>`` and have no typed declaration — the parser returns ``None``
#: for those, allowing the engine to skip the gate gracefully.
_DELIVERABLE_HEADING_RE = re.compile(
    r"(?ms)^##\s*Deliverable\s*$(?P<body>.*?)(?=^##\s|\Z)",
)
_DELIVERABLE_TYPE_RE = re.compile(
    r"(?m)^type:\s*(\S+)\s*$",
)


def parse_phase_deliverable_type(phase_md_text: str) -> str | None:
    """Extract the typed deliverable from a ``phase.md`` body.

    Returns the ``type:`` value declared under the ``## Deliverable``
    heading (e.g. ``"execution_summary"``, ``"judgment_layer_spec"``)
    or ``None`` when no typed declaration is present (legacy phase.md
    that uses the pre-M52 ``Single deliverable file:`` line).

    The match is restricted to the first ``## Deliverable`` section so
    a stray ``type:`` line elsewhere in the file (e.g. in a code block
    or inside another section) does not poison the lookup.
    """
    match = _DELIVERABLE_HEADING_RE.search(phase_md_text)
    if match is None:
        return None
    body = match.group("body")
    type_match = _DELIVERABLE_TYPE_RE.search(body)
    if type_match is None:
        return None
    return type_match.group(1).strip()


# ---------------------------------------------------------------------------
# Phase.md linter (M52 R2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseLintError:
    """A single lint failure for a phase.md file.

    ``code`` is a stable identifier (``unregistered_type``,
    ``deliverable_section_missing_type``, ``no_deliverable_section``)
    so callers can route remediation logic; ``message`` is the
    human-readable error.  Returned as a list rather than raised so
    the linter can collect every fault in one pass.
    """

    code: str
    message: str


def lint_phase_md(phase_md_text: str, *, strict: bool = False) -> list[PhaseLintError]:
    """Lint a phase.md body's deliverable declaration.

    Returns one ``PhaseLintError`` per fault found; an empty list
    means the file passes.  This is the per-phase enforcement that
    R2 mandates: refuse creation of a phase whose ``deliverable``
    type is unregistered in ``ARTIFACT_REGISTRY``.

    Args:
        phase_md_text: The full text of a ``phase.md`` file.
        strict: When True, the absence of a ``## Deliverable``
            section is an error (legacy phase.md fails).  When False
            (the default during the M52 migration window), legacy
            phase.md without a deliverable section is OK — it falls
            through to the F41 migration shim.  Tests and the
            phase-creation entry point should use ``strict=True``;
            sweeps over the existing tree should use the default to
            avoid flagging known-legacy files.

    Faults:
        ``no_deliverable_section`` (strict only): no ``## Deliverable``
            heading found.
        ``deliverable_section_missing_type``: section present but no
            ``type:`` line.
        ``unregistered_type``: ``type:`` value is not a key in
            ``ARTIFACT_REGISTRY``.
    """
    errors: list[PhaseLintError] = []
    match = _DELIVERABLE_HEADING_RE.search(phase_md_text)
    if match is None:
        if strict:
            errors.append(
                PhaseLintError(
                    code="no_deliverable_section",
                    message=(
                        "phase.md must declare a typed deliverable under "
                        "a '## Deliverable' heading"
                    ),
                ),
            )
        return errors
    body = match.group("body")
    type_match = _DELIVERABLE_TYPE_RE.search(body)
    if type_match is None:
        errors.append(
            PhaseLintError(
                code="deliverable_section_missing_type",
                message=(
                    "'## Deliverable' section is present but no "
                    "'type: <ArtifactTypeName>' line was found"
                ),
            ),
        )
        return errors
    declared_type = type_match.group(1).strip()
    if declared_type not in ARTIFACT_REGISTRY:
        registered = sorted(ARTIFACT_REGISTRY.keys())
        errors.append(
            PhaseLintError(
                code="unregistered_type",
                message=(
                    f"declared deliverable type {declared_type!r} is "
                    f"not registered in ARTIFACT_REGISTRY "
                    f"(registered types: {registered!r})"
                ),
            ),
        )
    return errors
