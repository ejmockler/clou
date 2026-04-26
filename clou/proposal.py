"""Typed milestone proposals from coordinator to supervisor.

Under the zero-escalations principle, when a coordinator identifies
cross-cutting architectural work beyond its milestone scope, it files
a *milestone proposal* instead of escalating to the user.  The
supervisor reads proposals on startup and crystallizes them into real
milestones through the existing ``clou_create_milestone`` flow.

Authority is preserved: the coordinator can *propose*, the supervisor
can *accept, modify, or reject*.  No coordinator can spawn a milestone
directly — only the supervisor can, so the user's implicit delegation
of "what gets worked on next" stays at the supervisor tier.

This module implements the DB-21 drift-class remolding pattern:
- ``MilestoneProposalForm`` dataclass is the source of truth.
- ``render_proposal`` emits canonical markdown.
- ``parse_proposal`` reads markdown back to the form, drift-tolerantly.
- ``clou_propose_milestone`` (in coordinator_tools.py) is the sole
  write path.  Direct ``Write`` to ``.clou/proposals/`` is denied by
  hook (wired separately).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


#: Valid values for ``MilestoneProposalForm.status``.
#:
#: - ``open``: awaiting supervisor review.
#: - ``accepted``: supervisor crystallized it via ``clou_create_milestone``.
#: - ``rejected``: supervisor decided not to act; reason in disposition.
#: - ``superseded``: a later proposal or milestone covered the same ground.
VALID_STATUSES: tuple[str, ...] = (
    "open", "accepted", "rejected", "superseded",
)

ProposalStatus = Literal["open", "accepted", "rejected", "superseded"]


#: Valid values for ``MilestoneProposalForm.estimated_scope``.
#: Matches the roadmap sketch vocabulary so the supervisor can size
#: proposals against existing milestone history without re-interpretation.
VALID_SCOPES: tuple[str, ...] = (
    "afternoon", "day", "multi-day", "multi-milestone",
)

EstimatedScope = Literal["afternoon", "day", "multi-day", "multi-milestone"]


@dataclass(frozen=True, slots=True)
class MilestoneProposalForm:
    """Structured proposal from a coordinator to the supervisor.

    All fields are LLM-owned *values*; the layout is code-owned via
    ``render_proposal`` / ``parse_proposal``.  The coordinator never
    hand-writes the markdown — the writer tool does.

    ``depends_on`` lists other milestones (by path/slug) whose
    completion this proposal would serialize after.  ``independent_of``
    lists milestones explicitly identified as parallelizable.  Both
    are optional; the default is "sequential after the current
    roadmap," which matches supervisor defaults.
    """

    title: str
    filed_by_milestone: str
    filed_by_cycle: int
    rationale: str
    cross_cutting_evidence: str
    estimated_scope: EstimatedScope = "day"
    depends_on: tuple[str, ...] = ()
    independent_of: tuple[str, ...] = ()
    recommendation: str = ""
    status: ProposalStatus = "open"
    disposition: str = ""


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_proposal(form: MilestoneProposalForm) -> str:
    """Serialize a ``MilestoneProposalForm`` to canonical markdown.

    Deterministic: same input produces byte-identical output.  No
    "if llm_might_do_X" branches -- one shape only.
    """
    if form.estimated_scope not in VALID_SCOPES:
        raise ValueError(
            f"invalid estimated_scope {form.estimated_scope!r}; "
            f"must be one of {VALID_SCOPES!r}"
        )
    if form.status not in VALID_STATUSES:
        raise ValueError(
            f"invalid status {form.status!r}; "
            f"must be one of {VALID_STATUSES!r}"
        )

    lines: list[str] = []
    lines.append(f"# Proposal: {form.title}")
    lines.append("")
    lines.append(f"**Filed by:** coordinator for milestone `{form.filed_by_milestone}`, cycle {form.filed_by_cycle}")
    lines.append(f"**Estimated scope:** {form.estimated_scope}")
    if form.depends_on:
        lines.append(f"**Depends on:** {', '.join(form.depends_on)}")
    if form.independent_of:
        lines.append(f"**Independent of:** {', '.join(form.independent_of)}")
    lines.append("")
    lines.append("## Rationale")
    lines.append("")
    lines.append(form.rationale.rstrip())
    lines.append("")
    lines.append("## Cross-Cutting Evidence")
    lines.append("")
    lines.append(form.cross_cutting_evidence.rstrip())
    lines.append("")
    if form.recommendation:
        lines.append("## Recommendation")
        lines.append("")
        lines.append(form.recommendation.rstrip())
        lines.append("")
    lines.append("## Disposition")
    lines.append("")
    lines.append(f"status: {form.status}")
    if form.disposition:
        lines.append("")
        lines.append(form.disposition.rstrip())
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parse (drift-tolerant)
# ---------------------------------------------------------------------------


_BOLD_KV = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
_SECTION = re.compile(r"^##\s+(.+)\s*$")
_TITLE = re.compile(
    r"^#\s+(?:Proposal\s*[:\-\u2013\u2014]\s*)?(.+?)\s*$",
)
_FILED_BY = re.compile(
    r"coordinator for milestone\s+`?([\w.-]+)`?,?\s+cycle\s+(\d+)",
    re.IGNORECASE,
)


def parse_proposal(text: str) -> MilestoneProposalForm:
    """Parse markdown into a ``MilestoneProposalForm``, drift-tolerantly.

    Accepts:
    - Canonical shape from ``render_proposal``.
    - Drifted variants where field order, separator, or capitalization
      differs (LLM freeforms occasionally slip even through a typed
      writer when hand-editing happens).

    Missing fields default to the dataclass defaults.  No field is
    validated here; that's the writer tool's job before it calls
    render.  The parser's job is extraction, not gatekeeping.
    """
    title: str = ""
    filed_by_milestone: str = ""
    filed_by_cycle: int = 0
    estimated_scope: str = "day"
    depends_on: list[str] = []
    independent_of: list[str] = []
    status: str = "open"
    disposition_lines: list[str] = []

    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    preamble: list[str] = []

    for line in text.splitlines():
        m_section = _SECTION.match(line)
        if m_section:
            current_key = m_section.group(1).strip().lower()
            sections.setdefault(current_key, [])
            continue
        if current_key is None:
            # Preamble: title + metadata fields.
            if not title:
                m_title = _TITLE.match(line)
                if m_title:
                    title = m_title.group(1).strip()
                    continue
            m_kv = _BOLD_KV.match(line.strip())
            if m_kv:
                key = m_kv.group(1).strip().lower()
                value = m_kv.group(2).strip()
                if key in ("filed by", "filed"):
                    m_filed = _FILED_BY.search(value)
                    if m_filed:
                        filed_by_milestone = m_filed.group(1)
                        try:
                            filed_by_cycle = int(m_filed.group(2))
                        except ValueError:
                            filed_by_cycle = 0
                elif key in ("estimated scope", "scope"):
                    estimated_scope = value.strip()
                elif key in ("depends on",):
                    depends_on = [
                        s.strip() for s in value.split(",") if s.strip()
                    ]
                elif key in ("independent of",):
                    independent_of = [
                        s.strip() for s in value.split(",") if s.strip()
                    ]
            preamble.append(line)
            continue
        sections[current_key].append(line)

    def _section_text(*names: str) -> str:
        for name in names:
            if name in sections:
                return "\n".join(sections[name]).strip()
        return ""

    rationale = _section_text("rationale")
    evidence = _section_text(
        "cross-cutting evidence", "evidence", "cross cutting evidence",
    )
    recommendation = _section_text("recommendation")

    disposition_block = sections.get("disposition", [])
    for line in disposition_block:
        stripped = line.strip()
        if stripped.lower().startswith("status:"):
            status = stripped.split(":", 1)[1].strip().lower()
            continue
        if stripped:
            disposition_lines.append(line)
    disposition = "\n".join(disposition_lines).strip()

    if estimated_scope not in VALID_SCOPES:
        # Drift tolerance: accept the declared value as a plain string
        # on read (so we round-trip what was on disk) but coerce to the
        # canonical default at the type boundary.  Callers can detect
        # drift by comparing parse(text).estimated_scope to the raw.
        estimated_scope = "day"
    if status not in VALID_STATUSES:
        status = "open"

    return MilestoneProposalForm(
        title=title,
        filed_by_milestone=filed_by_milestone,
        filed_by_cycle=filed_by_cycle,
        rationale=rationale,
        cross_cutting_evidence=evidence,
        estimated_scope=estimated_scope,  # type: ignore[arg-type]
        depends_on=tuple(depends_on),
        independent_of=tuple(independent_of),
        recommendation=recommendation,
        status=status,  # type: ignore[arg-type]
        disposition=disposition,
    )


# ---------------------------------------------------------------------------
# Directory helper
# ---------------------------------------------------------------------------


def proposals_dir(clou_dir: Path) -> Path:
    """Return the project's proposals directory path.

    Proposals are project-scoped (not milestone-scoped) because they
    are ABOUT future milestones that don't exist yet.  The supervisor
    reads this directory on startup.
    """
    return clou_dir / "proposals"


#: Slug regex for proposal filenames -- lowercase, hyphen-separated,
#: ASCII only.  Matches the pattern used by escalation filenames.
_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")


def slugify_title(title: str, max_len: int = 50) -> str:
    """Derive a filename-safe slug from a proposal title.

    Deterministic -- same title produces same slug.  Length-capped.
    """
    slug = title.strip().lower()
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = _SLUG_SAFE_RE.sub("", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len]
