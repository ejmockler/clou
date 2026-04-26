"""Phase-acceptance gate for substance-typed deliverables.

M52 Move 1.  This module is the authoritative completion classifier
for phase deliverables.  The LLM-driven ASSESS protocol no longer
decides whether a phase is complete; it reads the gate's verdict
from the cycle context and routes accordingly.  ``phases_completed``
is incremented by ``clou_write_checkpoint`` only when the verdict
gate (in ``coordinator_tools.py``) finds an ``Advance`` verdict for
the right phase in ``prev_cp.last_acceptance_verdict``.

The gate is a **pure function** over pre-read text.  It does not
read the filesystem itself — the caller (``coordinator.py``) reads
``execution.md`` via a typed reader and passes the bytes here.
This keeps the gate's correctness independent of filename
construction (F39): a future caller could read from any source
without breaking the gate.

The gate is **idempotent** — same inputs always yield the same
verdict.  Side-effects are limited to telemetry emission; no
filesystem writes, no checkpoint mutation, no MCP roundtrips.

Public API:
    AcceptanceResult                --- frozen result type
    Advance                          --- gate accepted the deliverable
    GateDeadlock                     --- gate rejected; reason given
    GateDeadlockReason               --- string enum of rejection causes
    check_phase_acceptance(...)      --- the gate function

See ``.clou/milestones/52-substance-and-capability/`` for the spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Union

from clou import telemetry
from clou.artifacts import (
    ArtifactParseError,
    LocationMismatch,
    SchemaError,
    extract_artifacts,
    get_artifact_type,
    validate_artifact_location,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


GateDeadlockReason = Literal[
    "missing_artifact_type",   # No deliverable found
    "schema_mismatch",         # Found but failed validator
    "id_mismatch",             # Body sha != declared id
    "location_forgery",        # milestone/phase header doesn't match file
    "parse_error",             # Markdown grammar error
    "unregistered_type",       # phase declared a type not in registry
]


@dataclass(frozen=True)
class Advance:
    """Gate accepted the deliverable.

    Carries the matched artifact's content_sha so the
    verdict-gate validator in ``clou_write_checkpoint`` can
    bind the advance to a specific execution.md content.
    """

    phase: str
    content_sha: str
    artifact_type: str


@dataclass(frozen=True)
class GateDeadlock:
    """Gate rejected the deliverable.

    Carries enough detail for the supervisor to triage:
    which phase, what the gate looked for, what went wrong.
    """

    phase: str
    reason: GateDeadlockReason
    detail: str


AcceptanceResult = Union[Advance, GateDeadlock]


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def check_phase_acceptance(
    milestone: str,
    phase: str,
    declared_deliverable_type: str,
    execution_md_text: str,
) -> AcceptanceResult:
    """Evaluate phase acceptance against pre-read execution.md text.

    The caller is responsible for reading ``execution.md`` and
    passing the bytes here.  The gate does not touch the
    filesystem — keeping it pure means the structural defense
    (no filenames in phase-acceptance code) lives in the type
    signature, not in an AST check (F39).

    Args:
        milestone: The milestone the phase belongs to.  Used to
            validate the artifact's declared location.
        phase: The phase name.  Same purpose.
        declared_deliverable_type: The artifact-type name from
            ``phase.md``'s deliverable declaration.  Looked up in
            the artifact registry; if absent → ``GateDeadlock``.
        execution_md_text: The raw text of the phase's
            ``execution.md`` file.  May be empty.

    Returns:
        ``Advance`` if a typed artifact of the declared type is
        present, validates against location, and passes its
        per-type schema validator.  ``GateDeadlock`` otherwise.
        Both are frozen dataclasses (idempotent over inputs).

    Side effects:
        Emits ``phase_acceptance.checked`` telemetry with the
        verdict and reason.  No filesystem writes, no checkpoint
        mutation.
    """
    artifact_type = get_artifact_type(declared_deliverable_type)
    if artifact_type is None:
        result: AcceptanceResult = GateDeadlock(
            phase=phase,
            reason="unregistered_type",
            detail=(
                f"phase declares deliverable type "
                f"{declared_deliverable_type!r}, which is not "
                f"registered in clou.artifacts.ARTIFACT_REGISTRY"
            ),
        )
        _emit_telemetry(milestone, phase, result)
        return result

    # Parse the markdown.  ArtifactParseError is a structural
    # rejection (id mismatch, unbalanced fence, body too large,
    # etc.); convert to GateDeadlock so the supervisor sees it
    # as a contract failure, not an unhandled exception.
    try:
        parsed_list = extract_artifacts(execution_md_text)
    except ArtifactParseError as exc:
        result = GateDeadlock(
            phase=phase,
            reason="parse_error",
            detail=str(exc),
        )
        _emit_telemetry(milestone, phase, result)
        return result

    # Find the first artifact whose declared type matches.
    matching = [a for a in parsed_list if a.type_name == declared_deliverable_type]
    if not matching:
        result = GateDeadlock(
            phase=phase,
            reason="missing_artifact_type",
            detail=(
                f"no artifact of type {declared_deliverable_type!r} "
                f"found in execution.md "
                f"(saw types: {sorted({a.type_name for a in parsed_list})!r})"
                if parsed_list
                else (
                    f"no artifact of type {declared_deliverable_type!r} "
                    f"found in execution.md (no artifacts present)"
                )
            ),
        )
        _emit_telemetry(milestone, phase, result)
        return result

    # First match wins — phases declare exactly one deliverable
    # type, so multiple artifacts of the same type would be a
    # worker mistake.  We pick the first to be deterministic and
    # don't surface duplicates as a separate error class (the
    # validator might catch them; if not, that's a future
    # extension).
    parsed = matching[0]

    # Validate location: the artifact's declared milestone/phase
    # must match the file's filesystem location.  Forgery here
    # would mean a paste-and-cite from another milestone slipping
    # through.
    location_mismatch = validate_artifact_location(
        parsed,
        expected_milestone=milestone,
        expected_phase=phase,
    )
    if location_mismatch is not None:
        result = GateDeadlock(
            phase=phase,
            reason="location_forgery",
            detail=location_mismatch.message,
        )
        _emit_telemetry(milestone, phase, result)
        return result

    # Validate against the per-type schema.
    schema_errors = artifact_type.validator(parsed)
    if schema_errors:
        result = GateDeadlock(
            phase=phase,
            reason="schema_mismatch",
            detail=_format_schema_errors(schema_errors),
        )
        _emit_telemetry(milestone, phase, result)
        return result

    # All checks passed.
    result = Advance(
        phase=phase,
        content_sha=parsed.body_sha,
        artifact_type=parsed.type_name,
    )
    _emit_telemetry(milestone, phase, result)
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _emit_telemetry(
    milestone: str,
    phase: str,
    result: AcceptanceResult,
) -> None:
    """Emit ``phase_acceptance.checked`` event.

    Telemetry is the only side-effect of the gate.  The event
    fires on every invocation; the verdict (and reason if
    GateDeadlock) is the payload.
    """
    if isinstance(result, Advance):
        telemetry.event(
            "phase_acceptance.checked",
            milestone=milestone,
            phase=phase,
            verdict="Advance",
            content_sha=result.content_sha,
            artifact_type=result.artifact_type,
        )
    else:
        telemetry.event(
            "phase_acceptance.checked",
            milestone=milestone,
            phase=phase,
            verdict="GateDeadlock",
            reason=result.reason,
        )


def _format_schema_errors(errors: list[SchemaError]) -> str:
    """One-line per error, joined with semicolons."""
    if not errors:
        return ""
    parts = [
        f"{e.field_path or '<root>'}: {e.message}"
        for e in errors
    ]
    return "; ".join(parts)
