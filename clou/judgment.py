"""Structured schema + render + parse for ORIENT-cycle judgment markdown.

Part of the DB-14 ArtifactForm / DB-21 drift-class remolding recipe
applied to per-cycle ``judgments/cycle-{NN}-judgment.md`` files: one
schema on write, one renderer, one tolerant parser on read --- LLM owns
content, code owns format.

The parser is deliberately drift-tolerant: it accepts the canonical
``# Judgment`` / ``**Next action:**`` / ``## Rationale`` / ``## Evidence``
/ ``## Expected artifact`` shape produced by :func:`render_judgment`
and degrades gracefully when sections are missing or reordered.  Missing
sections default to empty strings / empty tuples; the parser MUST NOT
raise.  Field-level validation lives in :func:`validate_judgment_fields`
so readers can still inspect malformed files.

The ``next_action`` vocabulary is coupled to dispatch via
:data:`VALID_NEXT_ACTIONS`, which re-exports
:data:`clou.recovery_checkpoint._VALID_NEXT_STEPS` --- when M37 promotes
judgment from telemetry to gating, the vocabularies must already agree.

Public API:
    JudgmentForm                 --- frozen dataclass
    VALID_NEXT_ACTIONS           --- tuple (sorted) from _VALID_NEXT_STEPS
    JUDGMENT_PATH_TEMPLATE       --- write-path format string
    render_judgment(form)        --- canonical markdown
    parse_judgment(text)         --- tolerant text -> JudgmentForm
    validate_judgment_fields(f)  --- raises ValueError on malformed input
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clou.recovery_checkpoint import _VALID_NEXT_STEPS

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Accepted ``next_action`` vocabulary.  Sourced directly from the
#: coordinator's dispatch vocabulary so the judgment schema and the
#: orchestrator's cycle-type router stay coupled.  When a new cycle
#: type is added to ``recovery_checkpoint._VALID_NEXT_STEPS`` it becomes
#: available to judgments automatically.
#:
#: M50 I1 cycle-3 rework (F4/F15): ``'none'`` was removed from
#: ``_VALID_NEXT_STEPS`` to eliminate the asymmetric render/parse
#: round-trip; the ``- {"none"}`` subtraction here stays as a belt-and-
#: suspenders guard so if ``'none'`` ever re-enters the source set
#: (e.g., a regression during a future unification), judgment
#: validation still rejects the EXIT-protocol sentinel.  Judgments are
#: ORIENT-cycle artifacts; their ``next_action`` must name a real
#: dispatch target, not "no further dispatch".
VALID_NEXT_ACTIONS: tuple[str, ...] = tuple(sorted(_VALID_NEXT_STEPS - {"none"}))

#: Write-path template for judgment files, relative to the milestone
#: directory.  ``.format(cycle=N)`` produces zero-padded two-digit
#: cycle numbers (``cycle-01-judgment.md``, ``cycle-42-judgment.md``).
#: Consumed by the MCP writer tool and by the disagreement telemetry
#: reader in later phases of this milestone.
JUDGMENT_PATH_TEMPLATE: str = "judgments/cycle-{cycle:02d}-judgment.md"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgmentForm:
    """Full structured form of a cycle-judgment markdown file.

    All four fields are required by the schema: the parser returns
    empty-string / empty-tuple defaults for missing sections; the
    validator enforces non-emptiness as the terminal gate.  Callers
    decide whether to raise on malformed input (MCP tool converts
    ``ValueError`` into a structured ``is_error`` payload).
    """

    next_action: str
    rationale: str
    evidence_paths: tuple[str, ...]
    expected_artifact: str


# ---------------------------------------------------------------------------
# Render --- canonical markdown from structured form
# ---------------------------------------------------------------------------


def render_judgment(form: JudgmentForm) -> str:
    """Render a :class:`JudgmentForm` to canonical markdown.

    Layout:
        ``# Judgment`` (no title --- the cycle number lives in the
        filename via :data:`JUDGMENT_PATH_TEMPLATE`).
        ``**Next action:** {next_action}``
        ``## Rationale`` -> body
        ``## Evidence`` -> ``- path`` bullet list, or ``(none)`` when
        ``evidence_paths`` is empty (the renderer is forgiving;
        :func:`validate_judgment_fields` rejects empty evidence upstream).
        ``## Expected artifact`` -> body

    One blank line between sections.  Trailing newline.
    """
    lines: list[str] = ["# Judgment", ""]

    lines.append(f"**Next action:** {form.next_action}")
    lines.append("")

    lines.append("## Rationale")
    lines.append(form.rationale)
    lines.append("")

    lines.append("## Evidence")
    if form.evidence_paths:
        for path in form.evidence_paths:
            lines.append(f"- {path}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Expected artifact")
    lines.append(form.expected_artifact)

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Parse --- tolerant markdown to structured form
# ---------------------------------------------------------------------------


_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# ``**Next action:** value`` preamble --- accepts colon-inside or
# colon-outside the bold span (``**Next action:** x`` and
# ``**Next action**: x`` both appear in wild markdown).  Capture group
# constrained to non-newline characters so an empty-valued preamble
# does not accidentally consume a following heading.
_NEXT_ACTION_RE = re.compile(
    r"^[ \t]*\*\*Next\s+action:?\*\*[ \t]*:?[ \t]*([^\n]*?)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Return ``(start_after_heading, end_before_next_h1_or_h2)`` or None.

    Case-insensitive match on ``heading``.  Local to this module by
    design --- we do NOT import section helpers from
    :mod:`clou.escalation` or :mod:`clou.assessment` so each
    ArtifactForm schema stays independently testable and revisable.
    """
    escaped = re.escape(heading)
    match = re.search(rf"(?im)^##\s+{escaped}\s*$", text)
    if not match:
        return None
    start = match.end()
    next_h = re.search(r"(?m)^#{1,2}\s+", text[start:])
    end = start + next_h.start() if next_h else len(text)
    return start, end


def _strip_section(body: str) -> str:
    """Strip leading / trailing whitespace from a section body."""
    return body.strip()


def _parse_next_action(text: str) -> str:
    """Extract the ``**Next action:**`` preamble value before the first h2.

    Scans only the region preceding the first ``## Heading`` so a
    ``**Next action:**`` line inside a section body does not leak into
    the preamble.  Returns empty string when no preamble line is
    present.
    """
    first_h2 = _H2_RE.search(text)
    end = first_h2.start() if first_h2 else len(text)
    preamble = text[:end]
    m = _NEXT_ACTION_RE.search(preamble)
    return m.group(1).strip() if m else ""


def _parse_evidence(body: str) -> tuple[str, ...]:
    """Parse the Evidence section body into a tuple of path strings.

    Each non-empty line beginning with ``- `` is treated as a path
    entry.  Indented ``  - `` bullets are also accepted.  The literal
    ``(none)`` token (case-insensitive, possibly surrounded by
    whitespace) signals explicit emptiness and returns the empty tuple.
    Other non-bullet lines are ignored so free-form prose interleaved
    with bullets does not corrupt the path list.
    """
    stripped = body.strip()
    if not stripped:
        return ()
    # Explicit "(none)" marker --- render emits this when evidence is
    # empty, so the parser must recognise it as empty-tuple rather
    # than treating ``(none)`` as a path.
    if stripped.lower() == "(none)":
        return ()

    paths: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("- "):
            value = s[2:].strip()
            if value:
                paths.append(value)
    return tuple(paths)


def parse_judgment(text: str) -> JudgmentForm:
    """Parse judgment markdown (possibly drifted) into a structured form.

    Accepts the canonical layout produced by :func:`render_judgment`
    and degrades gracefully when sections are missing or reordered:
    each section is located by its ``## Heading`` span independently,
    so a file that emits ``## Expected artifact`` before
    ``## Rationale`` still parses cleanly.

    Missing sections default to empty string (``rationale``,
    ``expected_artifact``) or empty tuple (``evidence_paths``).  A
    missing ``**Next action:**`` preamble yields ``next_action = ""``.

    The parser MUST NOT raise on malformed input.  Field-level
    rejection is the separate responsibility of
    :func:`validate_judgment_fields`, which callers invoke when they
    need to gate a write.  Readers that want to inspect or triage a
    broken judgment file can consume the form directly without having
    to catch exceptions.
    """
    next_action = _parse_next_action(text)

    rationale = ""
    rspan = _section_span(text, "Rationale")
    if rspan is not None:
        rationale = _strip_section(text[rspan[0]:rspan[1]])

    evidence_paths: tuple[str, ...] = ()
    espan = _section_span(text, "Evidence")
    if espan is not None:
        evidence_paths = _parse_evidence(text[espan[0]:espan[1]])

    expected_artifact = ""
    aspan = _section_span(text, "Expected artifact")
    if aspan is not None:
        expected_artifact = _strip_section(text[aspan[0]:aspan[1]])

    return JudgmentForm(
        next_action=next_action,
        rationale=rationale,
        evidence_paths=evidence_paths,
        expected_artifact=expected_artifact,
    )


# ---------------------------------------------------------------------------
# Validate --- reject malformed forms with actionable errors
# ---------------------------------------------------------------------------


def validate_judgment_fields(form: JudgmentForm) -> None:
    """Raise :class:`ValueError` when *form* violates the schema invariants.

    Invariants:
        - ``next_action`` must be a value in :data:`VALID_NEXT_ACTIONS`
          (coupled to dispatch via ``_VALID_NEXT_STEPS``).
        - ``evidence_paths`` must be non-empty --- a judgment without
          evidence is not a judgment.
        - ``rationale`` must contain non-whitespace content.
        - ``expected_artifact`` must contain non-whitespace content.

    Error messages are actionable: they name the offending field and
    either the valid vocabulary (``next_action``) or the non-empty
    contract.  The MCP writer tool in a later phase converts
    ``ValueError`` into a structured ``is_error`` payload so the
    coordinator LLM can self-correct without having to catch Python
    exceptions.
    """
    # M50 I1 cycle-2 rework (F26): judgments use the judgment-specific
    # vocabulary (``VALID_NEXT_ACTIONS``) which excludes ``none`` — a
    # judgment must name a real dispatch target.
    if form.next_action not in VALID_NEXT_ACTIONS:
        raise ValueError(
            f"next_action {form.next_action!r} not in cycle-type "
            f"vocabulary: {list(VALID_NEXT_ACTIONS)}"
        )
    if not form.evidence_paths:
        raise ValueError("evidence_paths must be non-empty")
    if not form.rationale.strip():
        raise ValueError("rationale must be non-empty")
    if not form.expected_artifact.strip():
        raise ValueError("expected_artifact must be non-empty")
