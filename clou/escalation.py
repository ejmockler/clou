"""Structured schema + render + parse for escalation markdown.

Part of the DB-21 drift-class remolding recipe applied to
``escalations/*.md``: one schema on write, one renderer, one tolerant
parser on read --- LLM owns content, code owns format.

The parser is deliberately drift-tolerant: it accepts the canonical
``# Escalation: Title`` / ``**Classification:**`` / ``## Context`` /
``## Issue`` / ``## Evidence`` / ``## Options`` / ``## Recommendation`` /
``## Disposition`` shape produced by :func:`render_escalation` AND
every legacy layout found on disk under
``.clou/milestones/*/escalations/*.md`` (``## Analysis``, ``## Problem``,
``## Finding``, ``## Severity``, ``## Fix``, ``## Target content`` and
the ``### Option A:`` / ``### (a)`` option headings used by
prose-authored escalations).

Classification is an **open-set string**, not an enum.  The
:data:`VALID_CLASSIFICATIONS` tuple lists the classifications this
codebase currently produces, but the parser accepts arbitrary values
(legacy ``## Severity`` bodies, new agent-authored strings, localised
vocabulary).  Callers MUST NOT branch on classification equality for
routing decisions --- treat it as human-authored free text and prefer
explicit status fields (``disposition_status``) for control flow.  See
F14 in ``.clou/milestones/41-escalation-remolding/assessment.md``.

**Narrow exception (M49b):** classifications listed in
:data:`ENGINE_GATED_CLASSIFICATIONS` ARE permitted as engine
control-flow signals.  The "don't-branch-on-classification"
contract above protects against LLM-authored drift; the values in
this set are code-written (hardcoded in MCP tool handlers like
``clou_halt_trajectory``) and therefore immune to drift.  Engine
callers that read this constant are making an explicit whitelisted
exception; new engine-gated classifications join the frozenset
rather than requiring parallel typed fields on :class:`EscalationForm`.

Public API:
    EscalationOption              --- one option {label, description}
    EscalationForm                --- full structured form
    render_escalation(form)       --- canonical markdown
    parse_escalation(text)        --- tolerant text -> EscalationForm
    parse_latest_disposition(t)   --- str (LAST ## Disposition block status)
    find_last_disposition_span(t) --- (start, end) or None (LAST block)
    VALID_DISPOSITION_STATUSES    --- union enum (F7)
    OPEN_DISPOSITION_STATUSES     --- {open, investigating, deferred}
    RESOLVED_DISPOSITION_STATUSES --- {resolved, overridden}
    VALID_CLASSIFICATIONS         --- advisory list; parser does NOT reject
                                      unknown values
    ENGINE_GATED_CLASSIFICATIONS  --- M49b: code-written classifications
                                      that engine control flow IS
                                      permitted to branch on
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------
#
# F7 (cycle 2): canonicalise the disposition status enum in the schema
# module.  Prior to this cycle the repository carried three independent
# definitions (``clou.escalation.VALID_DISPOSITION_STATUSES``,
# ``clou.supervisor_tools.RESOLUTION_STATUSES``,
# ``clou.tools._OPEN_ESCALATION_STATUSES``) which disagreed about which
# tokens are valid.  The schema module owns the single source of truth:
# the supervisor resolution tool and ``clou_status`` import from here.
#
# The supervisor can express intermediate workflow statuses
# (``investigating``, ``deferred``) while a decision is still in flight,
# so the valid set is the UNION of the resolver's intermediate states
# plus the terminal states the parser round-trips (``resolved``,
# ``overridden``), plus the ``open`` default.

VALID_DISPOSITION_STATUSES: tuple[str, ...] = (
    "open",
    "investigating",
    "deferred",
    "resolved",
    "overridden",
)

# Statuses that should keep an escalation visible in operational status
# output (``clou_status``, "open escalations" counts, etc.).  These are
# the non-terminal dispositions that still require supervisor attention.
#
# M49b C4 (closes B5/L7): for ENGINE_GATED_CLASSIFICATIONS, this same
# tuple controls the engine halt gate — a halt with disposition in any
# of these three states wedges the milestone (the engine refuses to
# dispatch).  ``deferred`` and ``investigating`` are deliberately
# included: bypassing either would let the engine dispatch a halt the
# supervisor explicitly parked.  There is no "park-and-continue"
# affordance for engine-gated halts; the supervisor UX MUST terminate
# disposition in ``resolved`` or ``overridden`` before exiting the
# disposition loop for these classifications.  See
# ``knowledge-base/protocols/escalation.md`` § "Engine-Gated
# Escalations" for the supervisor UX contract.
OPEN_DISPOSITION_STATUSES: tuple[str, ...] = (
    "open",
    "investigating",
    "deferred",
)

# Terminal dispositions --- the escalation is closed.  Consumed by the
# DB-15 D5 cycle-count-reset logic (via ``parse_latest_disposition``)
# and by ``clou_remove_artifact`` as the deletion authorization gate.
RESOLVED_DISPOSITION_STATUSES: tuple[str, ...] = (
    "resolved",
    "overridden",
)


# Classifications observed in-tree; ``parse_escalation`` does NOT reject
# unknown values --- this tuple is a hint for authors and a fallback
# default list for docs.  New classifications land at the LLM surface
# without a code change.
#
# M49a: ``trajectory_halt`` is the coordinator's verb for "this
# milestone's trajectory is broken, pause pending supervisor review"
# (see ``.clou/milestones/49a-trajectory-halt-bridging/`` and roadmap
# M49).  Filed via ``clou_halt_trajectory`` (thin wrapper over the
# escalation pathway); dispatched on by the engine's pre-cycle halt
# check when any open escalation carries this classification.
VALID_CLASSIFICATIONS: tuple[str, ...] = (
    "blocking", "degraded", "informational", "architectural",
    "trajectory_halt",
)


# M49b: classifications that engine control flow IS permitted to
# branch on.  Narrow exception to the module-level contract
# "callers MUST NOT branch on classification equality."  The
# contract protects against LLM-authored drift; values listed here
# are code-written in MCP tool handlers (``clou_halt_trajectory``
# hardcodes ``classification="trajectory_halt"`` in
# ``coordinator_tools.py``) and therefore immune to the drift the
# contract guards against.
#
# Engine callers use this constant via
# ``parsed.classification in ENGINE_GATED_CLASSIFICATIONS`` rather
# than a hardcoded string literal, so future engine-gated
# classifications (e.g. a hypothetical ``budget_halt``) join the
# set with one edit rather than sprinkling literals through the
# dispatch loop.
#
# The trade-off chosen: this constant + docstring exception is the
# light-touch alternative to extending :class:`EscalationForm` with
# a typed ``engine_action`` field.  A typed field would require
# render/parse/drift-parser changes to every escalation writer ---
# disproportionate cost for one engine-gated classification.  If
# the list grows beyond 2-3 entries or acquires non-boolean
# semantics (severity levels, typed parameters), revisit in favour
# of the typed-field approach.
ENGINE_GATED_CLASSIFICATIONS: frozenset[str] = frozenset({
    "trajectory_halt",
})


# M49b D4 (closes B7/F5): single source of truth for the trajectory_halt
# escalation's option labels.  Both the coordinator-side handler that
# BUILDS the escalation (clou_halt_trajectory in coordinator_tools.py)
# and the supervisor-side handler that DISPOSITIONS it (clou_dispose_halt
# in supervisor_tools.py) consume this tuple — so a future PR that adds
# a 4th label or renames one cannot drift them apart silently.
#
# Order matters for UX (presented to the supervisor in this order):
#   continue-as-is — re-dispatch with current scope
#   re-scope        — route to PLAN with new scope
#   abandon         — route to EXIT
#
# tests/test_supervisor_tools.py pins clou_dispose_halt's accepted set
# against this tuple AND pins the produced escalation's option labels
# against this tuple — both directions guarded.
HALT_OPTION_LABELS: tuple[str, ...] = (
    "continue-as-is",
    "re-scope",
    "abandon",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EscalationOption:
    """One option under ``## Options``."""

    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class EscalationForm:
    """Full structured form of an escalation.md file.

    Fields map 1:1 onto the canonical render layout; legacy layouts are
    folded into these fields by :func:`parse_escalation` (e.g.
    ``## Analysis`` becomes evidence, ``## Problem`` becomes issue).

    F21 (cycle 2): ``disposition_status`` is always constrained to a
    value in :data:`VALID_DISPOSITION_STATUSES`; unknown tokens parsed
    from disk are canonicalised to ``"open"`` at read time.  The raw
    value survives in :attr:`disposition_status_raw` so
    operational surfaces (``clou_status``) can distinguish a drifted
    ``status: closed`` file from a true ``status: open`` file and
    surface "unknown: closed" rather than silently hiding the record.
    """

    title: str
    classification: str
    filed: str = ""
    context: str = ""
    issue: str = ""
    evidence: str = ""
    options: tuple[EscalationOption, ...] = ()
    recommendation: str = ""
    disposition_status: str = "open"
    disposition_notes: str = ""
    # F21: the raw token (pre-canonicalisation) parsed from the file.
    # Equals ``disposition_status`` when the value was in
    # :data:`VALID_DISPOSITION_STATUSES`; otherwise carries the
    # unrecognised token (e.g. ``"closed"``) while
    # ``disposition_status`` falls back to ``"open"``.  Default ``""``
    # means "inherit from ``disposition_status``" --- set explicitly by
    # :func:`parse_escalation` when the on-disk token diverges from the
    # canonical value.
    disposition_status_raw: str = ""

    def __post_init__(self) -> None:
        # Frozen-dataclass idiom for computed defaults: use
        # ``object.__setattr__`` to mutate a field that's normally
        # locked.  When the caller didn't supply a raw token (the
        # common case), we inherit the canonical status so round-trip
        # equality holds without every caller having to specify both
        # fields.
        if not self.disposition_status_raw:
            object.__setattr__(
                self,
                "disposition_status_raw",
                self.disposition_status,
            )


# ---------------------------------------------------------------------------
# Render --- canonical markdown from structured form
# ---------------------------------------------------------------------------


# F1 (security): any line that starts with a markdown heading marker
# (``#``, ``##``, ..., ``######``) inside a rendered field would be read
# by the parser as a section boundary --- a drifted coordinator could
# inject ``## Disposition\nstatus: resolved`` into an Evidence blob and
# forge resolution state downstream.  We strip/escape these in every
# field before they land in the canonical document.
_HEADING_LINE_RE = re.compile(r"(?m)^(\s*)(#{1,6})(\s+)")

# Single-line fields where embedded newlines are a forgery surface
# (they could hoist a heading into the preamble region).  We collapse
# whitespace aggressively here to make the single-line contract loud.
_NEWLINE_RE = re.compile(r"[\r\n]+")

# F1/F29 (cycle 2, security): bare carriage returns are an injection
# vector.  Python's ``open(newline=None)`` (the default for
# :func:`pathlib.Path.read_text`) converts any `\r\n` OR bare `\r`
# to `\n` on read, so a ``"prose\r## Disposition"`` string written to
# disk becomes ``"prose\n## Disposition"`` after a round-trip --- and
# suddenly the ``## Disposition`` sits at a `^` boundary the parser
# recognises.  We normalize `\r\n` and bare `\r` to `\n` BEFORE the
# heading regex fires so the escape is robust against the disk layer's
# universal-newline translation.
_CR_NORMALIZE_RE = re.compile(r"\r\n|\r")


def _normalize_newlines(value: str) -> str:
    """Normalize ``\r\n`` and bare ``\r`` to ``\n``.

    Applied before every heading-escape pass so the parser never sees
    a ``\r``-prefixed heading after Python's universal-newline read
    converts the CR to LF.  Idempotent on input that already uses LF.
    """
    if not value:
        return value
    return _CR_NORMALIZE_RE.sub("\n", value)


def _escape_field(value: str) -> str:
    """Escape a multi-line field so heading-lines cannot inject sections.

    F1 (security): prefixes every ``^#{1,6}\\s`` line with a backslash so
    the resulting markdown renders the literal characters but
    :func:`parse_escalation` no longer sees them as h1/h2/... boundaries.
    Whitespace outside heading lines is preserved so agent prose reads
    normally.

    F1/F29 (cycle 2): ``\r`` and ``\r\n`` are normalized to ``\n`` BEFORE
    the heading regex fires.  Python's universal-newline read converts
    ``\r`` to ``\n`` on disk round-trip, so a ``\r``-prefixed heading
    inside a field would otherwise resurrect as a real heading after
    :func:`pathlib.Path.read_text` translates the CR.
    """
    if not value:
        return value
    normalized = _normalize_newlines(value)
    # Prefix heading markers so they're inert to Markdown heading parsing
    # but still visually identifiable to a human reader.
    return _HEADING_LINE_RE.sub(r"\1\\\2\3", normalized)


def _escape_single_line(value: str) -> str:
    """Escape a single-line field (title, classification, filed).

    F1 (security): rejects embedded newlines by replacing them with a
    single space so the value cannot carry its own ``## Disposition``
    block into the preamble region.  Also strips heading markers for
    defense in depth (a single-line field with ``## Evidence`` at the
    start would still be recognised as a heading by the parser).

    F1/F29 (cycle 2): ``\r`` and ``\r\n`` are normalized first so the
    newline regex catches bare carriage returns (the regex targets
    ``[\r\n]+`` already, but normalizing up front keeps the escape
    sequence consistent with :func:`_escape_field`).
    """
    if not value:
        return value
    normalized = _normalize_newlines(value)
    single = _NEWLINE_RE.sub(" ", normalized).strip()
    # Strip leading heading markers so ``## foo`` supplied as a title
    # can't be re-parsed as an h2 heading after rendering.
    return _HEADING_LINE_RE.sub(r"\1\\\2\3", single)


def render_escalation(form: EscalationForm) -> str:
    """Render an :class:`EscalationForm` to canonical markdown.

    Omits sections whose values are empty strings (Disposition is always
    emitted --- it is the resolution anchor).  Options are emitted as
    ``1. **label** --- description`` when a description is present, and
    ``1. label`` when it is empty.

    F1 (security): every field is escaped before rendering so that a
    drifted caller cannot inject a fake ``## Disposition`` block through
    a free-text field.  Single-line fields (title, classification,
    filed) have embedded newlines collapsed to a single space.  All
    multi-line fields have heading markers (``#``..``######``) prefixed
    with a backslash.  Re-parsing the rendered output therefore yields
    exactly the author-supplied sections --- the injection surface is
    closed.
    """
    title = _escape_single_line(form.title)
    classification = _escape_single_line(form.classification)
    filed = _escape_single_line(form.filed)

    lines: list[str] = [f"# Escalation: {title}", ""]

    # Preamble
    lines.append(f"**Classification:** {classification}")
    if filed:
        lines.append(f"**Filed:** {filed}")
    lines.append("")

    def _section(heading: str, body: str) -> None:
        if not body:
            return
        lines.append(f"## {heading}")
        lines.append(_escape_field(body))
        lines.append("")

    _section("Context", form.context)
    _section("Issue", form.issue)
    _section("Evidence", form.evidence)

    if form.options:
        lines.append("## Options")
        for i, opt in enumerate(form.options, 1):
            label = _escape_single_line(opt.label.strip())
            desc = _escape_field(opt.description.strip())
            if desc:
                lines.append(f"{i}. **{label}** \u2014 {desc}")
            else:
                lines.append(f"{i}. {label}")
        lines.append("")

    _section("Recommendation", form.recommendation)

    # Disposition --- always emitted.  F1: status is constrained to a
    # lower-case token, so newlines here would simply break the section
    # contract.  Notes get the same heading escape as any other body.
    lines.append("## Disposition")
    status = _escape_single_line(form.disposition_status or "open")
    lines.append(f"status: {status}")
    if form.disposition_notes:
        lines.append(_escape_field(form.disposition_notes))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Parse --- tolerant markdown to structured form
# ---------------------------------------------------------------------------


# H1 title: ``# Escalation:`` with colon, em-dash, en-dash, or hyphen.
_H1_RE = re.compile(
    r"^#\s+Escalation\s*[:\u2014\u2013-]\s*(.+?)\s*$",
    re.MULTILINE,
)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)

# Preamble bold key/value: ``**Key:** value`` (accepts colon-inside or
# colon-outside the bold span: both ``**Key:** value`` and
# ``**Key**: value`` appear in the wild).
#
# F24: the capture group is constrained to non-newline characters so
# that an empty-valued ``**Classification:**`` line does NOT
# accidentally consume a following ``**Filed:**`` line via the trailing
# ``\s*$`` anchor (``\s`` matches newlines; a greedy ``\s*`` there
# could otherwise extend the match across lines).  Continuation-line
# coalescing happens in :func:`_parse_preamble`, not in the regex.
_PREAMBLE_KV_RE = re.compile(
    r"^[ \t]*\*\*([^*:]+?):?\*\*[ \t]*:?[ \t]*([^\n]*?)[ \t]*$",
    re.MULTILINE,
)

# Canonical bold-numbered option heading: ``1. **Label** --- description``.
# F16: delimiter class accepts 1-3 repetitions of hyphen/en-dash/em-dash
# or a single colon, so ``--``, ``---`` and mixed-width dashes parse
# without leftover characters in the description.
_OPTION_BOLD_RE = re.compile(
    r"^\s*(\d+)\.\s*\*\*(.+?)\*\*\s*(?::|[\u2014\u2013-]{1,3})?\s*(.*?)\s*$",
    re.MULTILINE,
)
# Plain numbered option: ``1. plain text``.  Used by system-generated
# recovery escalations.
_OPTION_PLAIN_RE = re.compile(
    r"^\s*(\d+)\.\s+(.+?)\s*$",
    re.MULTILINE,
)
# Prose-authored option headings:
#   ``### Option A: Label`` / ``### Option A --- Label``
#   ``### (a) Label``       / ``### (A) Label``
# F16: same delimiter widening as ``_OPTION_BOLD_RE``.
# F23: trailing capture group uses ``(.*?)`` for symmetry with
# ``_OPTION_BOLD_RE``; a bare ``### Option A`` with no trailing label
# is now represented as an option with an empty label rather than being
# silently dropped from ``form.options``.
_OPTION_LETTER_HEADING_RE = re.compile(
    r"^###\s+(?:Option\s+[A-Za-z0-9]+|\([A-Za-z0-9]+\))\s*(?::|[\u2014\u2013-]{1,3})?\s*(.*?)\s*$",
    re.MULTILINE,
)


# --- Legacy heading aliases -----------------------------------------------
# Maps the legacy h2 headings we tolerate to the canonical target field.
# Case-insensitive match against the heading text (lowercased).
#
# ``## Analysis`` / ``## Impact`` / ``## Occurrences`` / ``## Target content``
# are treated as evidence fallbacks --- they carry agent reasoning and
# supporting detail.  ``## Problem`` / ``## Finding`` / ``## Findings``
# become issue fallbacks.  ``## Severity`` slots into classification if
# classification is otherwise empty.  ``## Fix`` becomes recommendation.
# ``## Source`` becomes context.
_LEGACY_TARGETS: dict[str, str] = {
    "analysis": "evidence",
    "impact": "evidence",
    "occurrences": "evidence",
    "target content": "evidence",
    "workaround": "evidence",
    "problem": "issue",
    "finding": "issue",
    "findings": "issue",
    "source": "context",
    "severity": "classification",
    "fix": "recommendation",
}


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Return (start_after_heading, end_before_next_h1_or_h2) or None.

    Case-insensitive match on ``heading``.
    """
    escaped = re.escape(heading)
    match = re.search(
        rf"(?im)^##\s+{escaped}\s*$", text,
    )
    if not match:
        return None
    start = match.end()
    next_h = re.search(r"(?m)^#{1,2}\s+", text[start:])
    end = start + next_h.start() if next_h else len(text)
    return start, end


def _last_section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Like :func:`_section_span` but returns the span of the LAST match.

    F1 (security): the Disposition section parser uses the last
    ``## Disposition`` block in the document.  An injected leading
    block (from a drifted caller attempting to forge resolution state
    via a free-text field) is therefore ignored in favour of the
    canonical trailing block that :func:`render_escalation` emits.
    """
    escaped = re.escape(heading)
    matches = list(re.finditer(rf"(?im)^##\s+{escaped}\s*$", text))
    if not matches:
        return None
    match = matches[-1]
    start = match.end()
    next_h = re.search(r"(?m)^#{1,2}\s+", text[start:])
    end = start + next_h.start() if next_h else len(text)
    return start, end


def _section_spans_all_h2(
    text: str,
) -> list[tuple[str, int, int]]:
    """Return [(heading_text, start_after, end_before_next_h1_or_h2), ...]."""
    matches = list(_H2_RE.finditer(text))
    spans: list[tuple[str, int, int]] = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # Bound by next h1 if any before the next h2.
        h1 = re.search(r"(?m)^#\s+", text[start:end])
        if h1:
            end = start + h1.start()
        spans.append((heading, start, end))
    return spans


def _strip_section(body: str) -> str:
    """Strip leading/trailing whitespace from a section body."""
    return body.strip()


def _parse_title(text: str) -> str:
    m = _H1_RE.search(text)
    return m.group(1).strip() if m else ""


def _parse_preamble(text: str) -> dict[str, str]:
    """Parse ``**Key:** value`` lines that appear before the first h2.

    Returns a lowercase-keyed dict.  Only the segment of *text* before
    the first ``## Heading`` is scanned, so keys that appear inside
    section bodies (e.g. the ``**Classification:**`` prefix inside
    ``## Severity``) don't leak into the preamble.

    F24: when a ``**Key:**`` line captures an empty value and the next
    non-blank line is non-bold prose (no ``**`` prefix and no
    ``## heading``), that continuation line is folded into the value.
    This handles the 2026-04-20 drift pattern where authors wrap
    classification onto the next line.
    """
    first_h2 = _H2_RE.search(text)
    end = first_h2.start() if first_h2 else len(text)
    preamble = text[:end]
    preamble_lines = preamble.splitlines()
    result: dict[str, str] = {}
    for m in _PREAMBLE_KV_RE.finditer(preamble):
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        if not key or key in result:
            continue
        if not value:
            # F24: look forward to the next non-blank, non-bold line
            # inside the preamble region and treat it as a continuation.
            # Find the line index where this match ended.
            match_end = m.end()
            # Count how many full preamble lines precede match_end.
            consumed = preamble[:match_end]
            line_idx = consumed.count("\n")
            for next_line in preamble_lines[line_idx + 1:]:
                stripped = next_line.strip()
                if not stripped:
                    continue
                # Stop if this is another bold key line OR a heading.
                if stripped.startswith("**") or stripped.startswith("#"):
                    break
                value = stripped
                break
        result[key] = value
    return result


def _extract_options(options_body: str, legacy_body: str = "") -> tuple[EscalationOption, ...]:
    """Extract option entries.

    Tries the canonical bold-numbered form first, then falls back to
    plain numbered form.  If neither matches in ``options_body``,
    scans ``legacy_body`` for ``### Option A:`` / ``### (a)`` headings.
    """
    options: list[EscalationOption] = []

    # Pass 1: bold-numbered ``1. **Label** --- description``
    if options_body:
        for m in _OPTION_BOLD_RE.finditer(options_body):
            label = m.group(2).strip()
            description = m.group(3).strip()
            options.append(EscalationOption(
                label=label, description=description,
            ))

    # Pass 2: plain-numbered ``1. text`` (system-generated writers)
    if not options and options_body:
        for m in _OPTION_PLAIN_RE.finditer(options_body):
            label = m.group(2).strip()
            # Only accept if the label isn't obviously bold-style
            # truncation (the bold regex already tried and failed).
            if not label.startswith("**"):
                options.append(EscalationOption(label=label))

    # Pass 3: prose-authored letter/parenthesis headings in the legacy
    # scan body (the full text).  These appear in prose escalations
    # like ``### Option A: Per-coordinator isolation``.
    if not options and legacy_body:
        heading_matches = list(_OPTION_LETTER_HEADING_RE.finditer(legacy_body))
        for i, m in enumerate(heading_matches):
            label = m.group(1).strip()
            body_start = m.end()
            # Body extends to the next option heading OR the next h2.
            body_end = (
                heading_matches[i + 1].start()
                if i + 1 < len(heading_matches)
                else len(legacy_body)
            )
            next_h2 = re.search(
                r"(?m)^##\s+", legacy_body[body_start:body_end],
            )
            if next_h2:
                body_end = body_start + next_h2.start()
            description = legacy_body[body_start:body_end].strip()
            options.append(EscalationOption(
                label=label, description=description,
            ))

    return tuple(options)


def _parse_disposition(text: str) -> tuple[str, str, str]:
    """Return ``(status, raw_status, notes)`` from ``## Disposition``.

    ``status`` is canonicalised: unknown tokens fall back to ``"open"``
    (F21).  ``raw_status`` preserves the original on-disk value so
    operational surfaces can surface "unknown: closed" for drifted
    files rather than silently hiding them.  Trailing lines after the
    ``status: ...`` line become ``notes`` (preserves free-form
    resolution commentary).

    F1 (security): prefers the LAST ``## Disposition`` block when
    multiple are present, so an injected block at the top of a rendered
    field cannot forge the resolution state ahead of the one
    :func:`render_escalation` places at the tail.
    F26: strips ``**`` bold wrappers from each line before matching
    ``status:``; legacy files with ``**Status:** resolved`` inside the
    Disposition section are now recognised.
    F21 (cycle 2): unknown tokens (``status: closed``) canonicalise to
    ``"open"`` in ``status`` but survive verbatim in ``raw_status`` so
    the drift is visible to operators.
    """
    span = _last_section_span(text, "Disposition")
    if span is None:
        return "open", "open", ""
    body = text[span[0]:span[1]].strip()
    if not body:
        return "open", "open", ""
    lines = body.splitlines()
    raw = "open"
    notes_lines: list[str] = []
    status_seen = False
    for line in lines:
        stripped = line.strip()
        # F26: strip bold wrappers like ``**Status:**`` before matching.
        # ``.strip('*')`` only peels outer asterisks; the form
        # ``**Status:** value`` has ``*`` characters INSIDE the line,
        # so we remove all ``*`` characters defensively before the
        # ``status:`` prefix check.  Any ``*`` in the value itself is
        # lost, but values are constrained to short tokens (open,
        # resolved, overridden, ...) that never contain ``*``.
        bare = stripped.replace("*", "").strip()
        if not status_seen and bare.lower().startswith("status:"):
            _, _, value = bare.partition(":")
            value = value.strip().lower()
            if value:
                raw = value
            status_seen = True
            continue
        notes_lines.append(line)
    notes = "\n".join(notes_lines).strip()
    # F21: canonicalise unknown tokens to "open" while preserving raw.
    status = raw if raw in VALID_DISPOSITION_STATUSES else "open"
    return status, raw, notes


# ---------------------------------------------------------------------------
# Public disposition helpers (F27 cycle 2 consolidation)
# ---------------------------------------------------------------------------
#
# Prior cycles carried three independent readers of disposition state
# that disagreed on whether to trust the FIRST or LAST ``## Disposition``
# block:
#
#   - ``_parse_disposition`` (this module)     --- LAST
#   - ``_find_disposition_span`` in supervisor_tools.py --- FIRST
#   - ``coordinator.py:570`` raw regex         --- FIRST (full-file)
#
# F2 / F27 fix: expose ONE canonical LAST-match helper in the schema
# module.  Both the supervisor's resolver (write side) and the
# coordinator's DB-15 D5 cycle-count-reset (read side) route through
# this helper so writer and reader agree on which Disposition block
# wins.


def find_last_disposition_span(text: str) -> tuple[int, int] | None:
    """Return ``(heading_start, section_end)`` of the LAST ``## Disposition``.

    Returns ``None`` if no ``## Disposition`` heading is present.  The
    span covers the heading line itself through the end of the section
    (bounded by the next ``#`` / ``##`` heading or the end of the file).

    F2 / F27 (cycle 2): canonical source for the Disposition span.  The
    supervisor's ``clou_resolve_escalation`` splice target and the
    parser's ``_parse_disposition`` read target must agree, so both
    route through this helper.  The LAST-match orientation matches
    :func:`render_escalation`'s layout (Disposition is the terminal
    section) and defeats F1-class injection attempts in leading fields.
    """
    matches = list(re.finditer(r"(?im)^##\s+Disposition\s*$", text))
    if not matches:
        return None
    match = matches[-1]
    start = match.start()
    tail = text[match.end():]
    next_h = re.search(r"(?m)^#{1,2}\s+", tail)
    if next_h:
        end = match.end() + next_h.start()
    else:
        end = len(text)
    return start, end


def parse_latest_disposition(text: str) -> str:
    """Return the canonicalised disposition status from the LAST block.

    Convenience wrapper around :func:`_parse_disposition` that exposes
    only the canonical status string.  Unknown tokens canonicalise to
    ``"open"`` (see :func:`parse_latest_disposition_raw` for the raw
    token).  Returns ``"open"`` when no ``## Disposition`` block is
    present.

    F27 (cycle 2): consumed by ``coordinator.py``'s DB-15 D5
    cycle-count-reset logic (replacing a raw regex that saw every
    ``^status:`` line --- including forgeries in field bodies).  Routes
    through :func:`find_last_disposition_span` so the reader always
    honours the same block the supervisor's resolver edits.
    """
    status, _raw, _notes = _parse_disposition(text)
    return status


def parse_latest_disposition_raw(text: str) -> str:
    """Return the raw (pre-canonicalisation) disposition token.

    When a legacy or drifted file carries an unknown status
    (``status: closed``, ``status: pending``), :func:`_parse_disposition`
    canonicalises to ``"open"``.  This helper returns the unmodified
    on-disk token so operational surfaces can distinguish "legitimately
    open" from "unknown status suppressed" and surface the drift.

    Returns ``"open"`` when no ``## Disposition`` block is present.
    """
    _status, raw, _notes = _parse_disposition(text)
    return raw


def parse_escalation(text: Union[str, "Path"]) -> EscalationForm:
    """Parse an escalation.md (possibly drifted) into structured form.

    Accepts the canonical layout and every legacy shape found on disk:
    ``## Analysis`` / ``## Problem`` / ``## Finding`` headings,
    ``### Option A:`` / ``### (a)`` option headings, ``**Severity:**``
    preamble, etc.  Missing fields default to empty strings; disposition
    status defaults to "open".  Never raises on malformed input.

    For convenience, accepts either raw markdown text or a ``Path`` to
    a file on disk.
    """
    # Allow Path input for ergonomics --- most call-sites read a file.
    if isinstance(text, Path):
        try:
            text = text.read_text(encoding="utf-8")
        except OSError:
            return EscalationForm(title="", classification="")

    title = _parse_title(text)
    preamble = _parse_preamble(text)

    # Classification preamble keys, in order of preference.  Legacy
    # files use ``**Severity:**`` as a classification synonym.
    classification = (
        preamble.get("classification", "")
        or preamble.get("severity", "")
    )
    filed = preamble.get("filed", "") or preamble.get("raised", "")
    # ``**Status:**`` preamble overrides ``## Disposition`` status only
    # if Disposition is absent (it rarely is).
    preamble_status = preamble.get("status", "").lower()

    # Scan h2 sections: canonical first, fall back to legacy aliases.
    context = ""
    issue = ""
    evidence_parts: list[str] = []
    recommendation = ""
    options_body = ""

    # Track whether a canonical field has been set; legacy fallbacks
    # only populate an unset field (canonical wins).
    canonical_seen: set[str] = set()

    # First pass: canonical headings.
    for heading in ("Context", "Issue", "Evidence", "Options", "Recommendation"):
        span = _section_span(text, heading)
        if span is None:
            continue
        body = _strip_section(text[span[0]:span[1]])
        lower = heading.lower()
        if lower == "context":
            context = body
            canonical_seen.add("context")
        elif lower == "issue":
            issue = body
            canonical_seen.add("issue")
        elif lower == "evidence":
            # F4: only mark evidence as canonically seen when the
            # section has actual content.  An empty ``## Evidence``
            # placeholder must NOT suppress the legacy ``## Analysis``
            # / ``## Impact`` / ``## Occurrences`` fallback --- that's
            # the exact 2026-04-20 drift pattern the remolding was
            # meant to rescue.
            if body:
                evidence_parts.append(body)
                canonical_seen.add("evidence")
        elif lower == "recommendation":
            recommendation = body
            canonical_seen.add("recommendation")
        elif lower == "options":
            options_body = body
            canonical_seen.add("options")

    # Second pass: legacy aliases.  Each aliased section only fills an
    # unset canonical field; when multiple legacy sections target the
    # same canonical field (e.g. ``## Analysis`` + ``## Impact``), they
    # are joined with a blank line.
    for heading, start, end in _section_spans_all_h2(text):
        lower = heading.lower()
        if lower in (
            "context", "issue", "evidence", "options",
            "recommendation", "disposition",
        ):
            continue
        # Strip trailing punctuation from heading (``## Severity:``).
        canonical_key = lower.rstrip(":.-\u2014\u2013 ")
        target = _LEGACY_TARGETS.get(canonical_key)
        if target is None:
            continue
        body = _strip_section(text[start:end])
        if not body:
            continue
        if target == "context" and not context:
            context = body
        elif target == "issue" and not issue:
            issue = body
        elif target == "evidence":
            # Evidence accumulates from every legacy alias.
            if "evidence" not in canonical_seen:
                evidence_parts.append(body)
        elif target == "recommendation" and not recommendation:
            recommendation = body
        elif target == "classification" and not classification:
            classification = body

    evidence = "\n\n".join(p for p in evidence_parts if p)

    # Options: canonical body first; fall back to legacy scan across
    # the whole text for ``### Option A:`` / ``### (a)`` headings.
    options = _extract_options(options_body, legacy_body=text)

    disposition_status, disposition_status_raw, disposition_notes = (
        _parse_disposition(text)
    )
    if preamble_status and disposition_status == "open":
        # ``**Status:** resolved`` preamble found AND no explicit
        # ``## Disposition`` block (or block said open).  Prefer
        # preamble value for legacy files that encode status there.
        # F1: use ``_last_section_span`` for consistency with
        # ``_parse_disposition``'s prefer-last orientation.
        span = _last_section_span(text, "Disposition")
        if span is None:
            # F21: canonicalise via the same valid-set filter so the
            # preamble path doesn't reintroduce unknown-status drift.
            disposition_status_raw = preamble_status
            disposition_status = (
                preamble_status
                if preamble_status in VALID_DISPOSITION_STATUSES
                else "open"
            )

    return EscalationForm(
        title=title,
        classification=classification,
        filed=filed,
        context=context,
        issue=issue,
        evidence=evidence,
        options=options,
        recommendation=recommendation,
        disposition_status=disposition_status,
        disposition_notes=disposition_notes,
        disposition_status_raw=disposition_status_raw,
    )


# ---------------------------------------------------------------------------
# M49b B4: engine halt gate — scan for open engine-gated escalations
# ---------------------------------------------------------------------------


def find_open_engine_gated_escalation(
    esc_dir: Path,
) -> tuple[Path, EscalationForm] | None:
    """Scan *esc_dir* for the first OPEN engine-gated escalation.

    M49b B4: the coordinator's pre-dispatch halt gate reads this
    helper on every cycle-loop iteration.  Returns ``(path, form)``
    for the first escalation file whose parsed form has:

    - ``classification in ENGINE_GATED_CLASSIFICATIONS`` (i.e. the
      classification is code-written and explicitly whitelisted for
      engine control-flow branching per the module's
      "don't-branch-on-classification" contract exception).
    - ``disposition_status in OPEN_DISPOSITION_STATUSES`` (the
      canonical non-terminal tuple
      ``("open", "investigating", "deferred")``).  ``deferred`` is
      included: the escalation protocol treats it as still-open,
      and bypassing ``deferred`` would let the engine dispatch a
      halt the supervisor explicitly parked.

    Returns ``None`` if the directory does not exist, is empty, or
    contains no matching escalation.

    **Fail-open on corruption.**  A single unparseable file is logged
    at WARNING and skipped, not fatal.  Rationale: wedging the
    engine indefinitely on any disk-level corruption is strictly
    worse than dispatching while the operator cleans up the bad
    file (the fail-closed alternative).  The operator sees the
    warning and can re-file or delete.

    Files are scanned in sorted order (timestamp-prefixed filenames
    give deterministic "oldest first" iteration); the first match
    wins.  Multiple open halts on the same milestone all remain
    queued — the supervisor resolves them one at a time, and the
    engine only resumes once ALL engine-gated halts are in a
    terminal disposition.
    """
    if not esc_dir.is_dir():
        return None
    for esc_file in sorted(esc_dir.glob("*.md")):
        try:
            text = esc_file.read_text(encoding="utf-8")
            form = parse_escalation(text)
        except Exception as exc:
            _log.warning(
                "engine halt gate: skipping unparseable escalation "
                "%s (%s: %s)",
                esc_file,
                type(exc).__name__,
                exc,
            )
            # M49b E4 (closes B9/F3): emit structured telemetry so
            # operators have a tripwire for the "safety rail is
            # blind" state.  A corrupted escalation file makes the
            # halt gate fail open — without this event, the only
            # signal is a log line.  The filename is particularly
            # interesting if it matches ``*-trajectory-halt.md`` (a
            # halt that the gate is silently skipping over).
            try:
                from clou import telemetry as _tlm
                _tlm.event(
                    "halt_gate.parse_failure",
                    path=str(esc_file),
                    filename=esc_file.name,
                    exception_type=type(exc).__name__,
                    is_halt_file=("trajectory-halt" in esc_file.name),
                )
            except Exception:
                # Telemetry failure must never propagate into the
                # halt-gate hot path.  Swallow silently — the log
                # warning above is the fallback audit.
                pass
            continue
        if (
            form.classification in ENGINE_GATED_CLASSIFICATIONS
            and form.disposition_status in OPEN_DISPOSITION_STATUSES
        ):
            return esc_file, form
    return None
