"""Structured schema + render + parse for assessment.md.

Part of the validated-artifact remolding: assessment.md moves from
"freeform LLM prose that validator regex-checks" to "structured
dataclass that code renders AND validates."  One schema, one renderer,
one parser — LLM owns content, code owns format.

The parser is deliberately drift-tolerant: it accepts the canonical
``## Findings`` → ``### F{N}:`` form AND the emergent ``## Phase: X``
subsection form that the brutalist/evaluator started producing when
scope stretched beyond single-phase assessments.  Migration-era
assessments parse cleanly; canonical-era assessments round-trip via
``render_assessment(parse_assessment(text))``.

Public API:
    AssessmentForm — full structured form
    AssessmentSummary — summary sub-form
    Finding — one ``### F{N}:`` entry
    Classification — one evaluator classification entry
    ToolInvocation — one ``## Tools Invoked`` entry
    render_assessment(form) -> str
    parse_assessment(text) -> AssessmentForm
    merge_classifications(form, classifications) -> AssessmentForm
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Literal

FindingSeverity = Literal["critical", "major", "minor"]
AssessmentStatus = Literal["completed", "degraded", "blocked"]
ClassificationKind = Literal[
    "valid", "security", "architectural", "noise",
    "next-layer", "out-of-milestone", "convergence",
]

VALID_SEVERITIES: tuple[FindingSeverity, ...] = ("critical", "major", "minor")
VALID_STATUSES: tuple[AssessmentStatus, ...] = (
    "completed", "degraded", "blocked",
)
VALID_CLASSIFICATIONS: tuple[ClassificationKind, ...] = (
    "valid", "security", "architectural", "noise",
    "next-layer", "out-of-milestone", "convergence",
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One ``### F{N}:`` entry under ``## Findings``."""

    number: int
    title: str
    severity: FindingSeverity
    source_tool: str = ""
    source_models: tuple[str, ...] = ()
    affected_files: tuple[str, ...] = ()
    finding_text: str = ""
    context: str = ""
    phase: str | None = None  # multi-phase scope tag


@dataclass(frozen=True, slots=True)
class Classification:
    """Evaluator's classification of a specific finding."""

    finding_number: int
    classification: ClassificationKind
    action: str = ""
    reasoning: str = ""


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """One entry under ``## Tools Invoked``."""

    tool: str
    domain: str | None = None
    status: str = "invoked"
    note: str = ""


@dataclass(frozen=True, slots=True)
class AssessmentSummary:
    """Values under ``## Summary``."""

    status: AssessmentStatus
    tools_invoked: int = 0
    findings_total: int = 0
    findings_critical: int = 0
    findings_major: int = 0
    findings_minor: int = 0
    phase_evaluated: str = ""
    internal_reviewers: int | None = None
    gate_error: str | None = None


@dataclass(frozen=True, slots=True)
class AssessmentForm:
    """Full structured form of an assessment.md file."""

    phase_name: str
    summary: AssessmentSummary
    tools: tuple[ToolInvocation, ...] = ()
    findings: tuple[Finding, ...] = ()
    classifications: tuple[Classification, ...] = ()


# ---------------------------------------------------------------------------
# Render — canonical markdown from structured form
# ---------------------------------------------------------------------------


def render_assessment(form: AssessmentForm) -> str:
    """Render an AssessmentForm to canonical markdown.

    Produces the structure ``_validate_assessment`` (and the new parser)
    recognizes: ``# Assessment: {phase}`` → ``## Summary`` → optional
    ``## Tools Invoked`` → ``## Findings`` → optional ``## Classifications``.
    """
    lines: list[str] = [f"# Assessment: {form.phase_name}", ""]

    # --- Summary ---
    s = form.summary
    lines.append("## Summary")
    lines.append(f"status: {s.status}")
    lines.append(f"tools_invoked: {s.tools_invoked}")
    lines.append(
        f"findings: {s.findings_total} total, "
        f"{s.findings_critical} critical, "
        f"{s.findings_major} major, "
        f"{s.findings_minor} minor"
    )
    lines.append(f"phase_evaluated: {s.phase_evaluated}")
    if s.internal_reviewers is not None:
        lines.append(f"internal_reviewers: {s.internal_reviewers}")
    if s.gate_error is not None:
        lines.append(f"gate_error: {s.gate_error}")
    lines.append("")

    # --- Tools Invoked ---
    if form.tools:
        lines.append("## Tools Invoked")
        lines.append("")
        for t in form.tools:
            if t.domain:
                head = f"- {t.tool} (domain={t.domain}): {t.status}"
            else:
                head = f"- {t.tool}: {t.status}"
            if t.note:
                head += f" — {t.note}"
            lines.append(head)
        lines.append("")

    # --- Findings ---
    if form.findings:
        lines.append("## Findings")
        lines.append("")
        for f in form.findings:
            lines.append(f"### F{f.number}: {f.title}")
            lines.append(f"**Severity:** {f.severity}")
            if f.source_tool:
                lines.append(f"**Source tool:** {f.source_tool}")
            if f.source_models:
                lines.append(
                    f"**Source models:** {', '.join(f.source_models)}"
                )
            if f.affected_files:
                lines.append("**Affected files:**")
                for path in f.affected_files:
                    lines.append(f"  - {path}")
            if f.finding_text:
                lines.append(f"**Finding:** {f.finding_text}")
            if f.context:
                lines.append(f"**Context:** {f.context}")
            if f.phase:
                lines.append(f"**Phase:** {f.phase}")
            lines.append("")

    # --- Classifications ---
    if form.classifications:
        lines.append("## Classifications")
        lines.append("")
        finding_by_num = {f.number: f for f in form.findings}
        for c in form.classifications:
            f_ref = finding_by_num.get(c.finding_number)
            title = f_ref.title if f_ref else f"F{c.finding_number}"
            lines.append(f"### F{c.finding_number}: {title}")
            lines.append(f"**Classification:** {c.classification}")
            if c.action:
                lines.append(f"**Action:** {c.action}")
            if c.reasoning:
                lines.append(f"**Reasoning:** {c.reasoning}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Parse — tolerant markdown to structured form
# ---------------------------------------------------------------------------


# Accepts colon, em-dash, en-dash, or hyphen as the separator between
# "# Assessment" and the phase name — the drifted samples in the wild
# use em-dashes ("# Assessment — Layer 1 Cycle 2 Rework").
_H1_RE = re.compile(
    r"^#\s+Assessment\s*[:\u2014\u2013-]\s*(.+?)\s*$",
    re.MULTILINE,
)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_F_HEADER_RE = re.compile(r"^###\s+F(\d+):\s*(.*?)\s*$", re.MULTILINE)
_KV_RE = re.compile(
    r"^\*\*([^*:]+):\*\*\s*(.*?)\s*$", re.MULTILINE,
)
_FINDINGS_COUNT_RE = re.compile(
    r"^(\d+)\s*total,\s*(\d+)\s*critical,\s*(\d+)\s*major,\s*(\d+)\s*minor",
    re.IGNORECASE,
)


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Return (start_after_heading, end_before_next_h2_or_h1) or None."""
    escaped = re.escape(heading)
    match = re.search(
        rf"(?m)^##\s+{escaped}\s*$", text,
    )
    if not match:
        return None
    start = match.end()
    next_h = re.search(r"(?m)^#{1,2}\s+", text[start:])
    end = start + next_h.start() if next_h else len(text)
    return start, end


def _section_spans_all_h2(text: str) -> list[tuple[str, int, int]]:
    """Return [(heading_text, start_after, end_before_next_h1_or_h2), ...]."""
    matches = list(_H2_RE.finditer(text))
    spans: list[tuple[str, int, int]] = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # Bound by next h1 if any before next h2
        h1 = re.search(r"(?m)^#\s+", text[start:end])
        if h1:
            end = start + h1.start()
        spans.append((heading, start, end))
    return spans


def _parse_phase_name(text: str) -> str:
    m = _H1_RE.search(text)
    return m.group(1).strip() if m else ""


def _parse_summary(text: str) -> AssessmentSummary:
    """Parse ``## Summary`` block.  Unknown status defaults to ``completed``."""
    span = _section_span(text, "Summary")
    status: AssessmentStatus = "completed"
    tools_invoked = 0
    total = critical = major = minor = 0
    phase_evaluated = ""
    internal_reviewers: int | None = None
    gate_error: str | None = None

    if span is not None:
        body = text[span[0]:span[1]]
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "status":
                if value in VALID_STATUSES:
                    status = value  # type: ignore[assignment]
            elif key == "tools_invoked":
                try:
                    tools_invoked = int(value)
                except ValueError:
                    pass
            elif key == "findings":
                m = _FINDINGS_COUNT_RE.match(value)
                if m:
                    total = int(m.group(1))
                    critical = int(m.group(2))
                    major = int(m.group(3))
                    minor = int(m.group(4))
            elif key == "phase_evaluated":
                phase_evaluated = value
            elif key == "internal_reviewers":
                try:
                    internal_reviewers = int(value)
                except ValueError:
                    pass
            elif key == "gate_error":
                gate_error = value

    return AssessmentSummary(
        status=status,
        tools_invoked=tools_invoked,
        findings_total=total,
        findings_critical=critical,
        findings_major=major,
        findings_minor=minor,
        phase_evaluated=phase_evaluated,
        internal_reviewers=internal_reviewers,
        gate_error=gate_error,
    )


def _parse_tools(text: str) -> tuple[ToolInvocation, ...]:
    """Parse ``## Tools Invoked`` bullet list."""
    span = _section_span(text, "Tools Invoked")
    if span is None:
        return ()
    body = text[span[0]:span[1]]
    tools: list[ToolInvocation] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        entry = line[2:].strip()
        tool, _, rest = entry.partition(":")
        tool = tool.strip()
        status = rest.strip()
        # split off domain=...
        domain = None
        m = re.match(r"^(.+?)\s*\(domain=([^)]+)\)\s*$", tool)
        if m:
            tool = m.group(1).strip()
            domain = m.group(2).strip()
        note = ""
        if " — " in status:
            status, _, note = status.partition(" — ")
            status = status.strip()
            note = note.strip()
        tools.append(ToolInvocation(
            tool=tool,
            domain=domain,
            status=status or "invoked",
            note=note,
        ))
    return tuple(tools)


def _parse_finding_block(
    number: int, title: str, body: str, phase: str | None,
) -> Finding:
    """Extract key/value fields from a finding body."""
    severity: FindingSeverity = "minor"
    source_tool = ""
    source_models: list[str] = []
    affected_files: list[str] = []
    finding_text = ""
    context = ""
    explicit_phase = phase

    # Key/value lines.
    for m in _KV_RE.finditer(body):
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        if key == "severity":
            v = value.lower()
            # Accept ``major`` or ``major (High per critic)`` — pick
            # the first token that matches a valid severity.
            for token in re.split(r"[^a-z]+", v):
                if token in VALID_SEVERITIES:
                    severity = token  # type: ignore[assignment]
                    break
        elif key == "source tool":
            source_tool = value
        elif key == "source models":
            source_models = [x.strip() for x in value.split(",") if x.strip()]
        elif key == "finding":
            finding_text = value
        elif key == "context":
            context = value
        elif key == "phase":
            explicit_phase = value or explicit_phase

    # Affected files: bullet list following **Affected files:** line.
    aff_match = re.search(
        r"(?m)^\*\*Affected files:\*\*\s*$", body,
    )
    if aff_match:
        after = body[aff_match.end():]
        for line in after.splitlines():
            s = line.strip()
            if s.startswith("- ") or s.startswith("  - "):
                affected_files.append(s.lstrip("- ").strip())
            elif s == "":
                continue
            else:
                break

    return Finding(
        number=number,
        title=title,
        severity=severity,
        source_tool=source_tool,
        source_models=tuple(source_models),
        affected_files=tuple(affected_files),
        finding_text=finding_text,
        context=context,
        phase=explicit_phase,
    )


def _parse_findings(text: str) -> tuple[Finding, ...]:
    """Collect every ``### F{N}:`` finding, tolerant to phase-organized drift.

    Canonical: all F-entries live under ``## Findings``.
    Drifted:   F-entries live under ``## Phase: X`` subsections.
    We scan every h2 section, checking for F-entries inside; findings under
    ``## Phase: X`` get tagged ``phase=X``.

    ``## Classifications`` is explicitly excluded — its ``### F{N}:`` entries
    are classifications, not findings, and are handled separately.
    """
    findings: list[Finding] = []
    seen_numbers: set[int] = set()
    for heading, start, end in _section_spans_all_h2(text):
        heading_l = heading.lower()
        if heading_l == "classifications":
            continue
        # Phase tag for drifted phase-organized layouts.
        phase: str | None = None
        if heading_l.startswith("phase:"):
            phase = heading.split(":", 1)[1].strip() or None
        body = text[start:end]
        # Find F-entries in this section.
        f_matches = list(_F_HEADER_RE.finditer(body))
        for i, fm in enumerate(f_matches):
            number = int(fm.group(1))
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            title = fm.group(2).strip()
            block_start = fm.end()
            block_end = (
                f_matches[i + 1].start()
                if i + 1 < len(f_matches)
                else len(body)
            )
            block = body[block_start:block_end]
            findings.append(
                _parse_finding_block(number, title, block, phase),
            )
    findings.sort(key=lambda f: f.number)
    return tuple(findings)


def _parse_classifications(text: str) -> tuple[Classification, ...]:
    """Parse ``## Classifications`` section if present."""
    span = _section_span(text, "Classifications")
    if span is None:
        return ()
    body = text[span[0]:span[1]]
    results: list[Classification] = []
    f_matches = list(_F_HEADER_RE.finditer(body))
    for i, fm in enumerate(f_matches):
        number = int(fm.group(1))
        block_start = fm.end()
        block_end = (
            f_matches[i + 1].start()
            if i + 1 < len(f_matches)
            else len(body)
        )
        block = body[block_start:block_end]
        classification: ClassificationKind = "noise"
        action = ""
        reasoning = ""
        for m in _KV_RE.finditer(block):
            key = m.group(1).strip().lower()
            value = m.group(2).strip()
            if key == "classification":
                v = value.lower()
                if v in VALID_CLASSIFICATIONS:
                    classification = v  # type: ignore[assignment]
            elif key == "action":
                action = value
            elif key == "reasoning":
                reasoning = value
        results.append(Classification(
            finding_number=number,
            classification=classification,
            action=action,
            reasoning=reasoning,
        ))
    return tuple(results)


def parse_assessment(text: str) -> AssessmentForm:
    """Parse an assessment.md (possibly drifted) into structured form.

    Tolerant: accepts both canonical ``## Findings`` structure and the
    drift-era ``## Phase: X`` subsection form.  Unknown sections are
    ignored.  Missing fields default to empty/zero.
    """
    return AssessmentForm(
        phase_name=_parse_phase_name(text),
        summary=_parse_summary(text),
        tools=_parse_tools(text),
        findings=_parse_findings(text),
        classifications=_parse_classifications(text),
    )


# ---------------------------------------------------------------------------
# Merge — evaluator amendments
# ---------------------------------------------------------------------------


def merge_classifications(
    form: AssessmentForm,
    classifications: list[Classification],
) -> AssessmentForm:
    """Return a new AssessmentForm with classifications merged in.

    Existing classifications for the same finding_number are replaced
    (last-writer-wins per finding).  This is the evaluator's amendment
    pathway: brutalist writes initial form; evaluator calls this with
    its classifications; the result is re-rendered.
    """
    existing = {c.finding_number: c for c in form.classifications}
    for c in classifications:
        existing[c.finding_number] = c
    merged = tuple(
        sorted(existing.values(), key=lambda c: c.finding_number),
    )
    return replace(form, classifications=merged)
