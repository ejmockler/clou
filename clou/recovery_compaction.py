"""Decisions compaction and memory pattern management (DB-15, DB-18).

Provides compact_decisions(), MemoryPattern dataclass, and pattern
parsing/rendering/reinforcement/decay helpers.

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from clou.recovery_checkpoint import _safe_int

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decisions compaction (DB-15 Tension 3)
# ---------------------------------------------------------------------------

_CYCLE_GROUP_RE = re.compile(r"(?m)^## Cycle \d+")


def compact_decisions(
    path: Path,
    *,
    keep_recent: int = 3,
    token_threshold: int = 4000,
) -> bool:
    """Compact old cycle groups in decisions.md.

    Keeps the most recent *keep_recent* cycle groups in full detail.
    Older groups are reduced to one-line summaries preserving finding
    counts and titles.  Full text is preserved in git history.

    Returns True if compaction was performed.
    """
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")

    # Rough token estimate: ~4 chars/token.
    if len(content) < token_threshold * 4:
        return False

    # Split into cycle groups.  Each group starts with "## Cycle N".
    splits = list(_CYCLE_GROUP_RE.finditer(content))
    if len(splits) <= keep_recent:
        return False  # Not enough groups to compact.

    # Everything before the first cycle group (preamble/heading).
    preamble = content[: splits[0].start()]

    # Collect groups newest-first (decisions.md is newest-first).
    groups: list[str] = []
    for i, match in enumerate(splits):
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        groups.append(content[match.start() : end])

    # Keep recent groups verbatim, compact older ones.
    recent = groups[:keep_recent]
    old = groups[keep_recent:]

    compacted_lines: list[str] = []
    for group in old:
        # Extract the heading line.
        heading_end = group.index("\n") if "\n" in group else len(group)
        heading = group[:heading_end].strip()

        # Already-compacted groups are preserved as-is.
        if "(compacted)" in heading:
            compacted_lines.append(group.rstrip() + "\n")
            continue

        # Count accepted/overridden findings.
        accepted = len(re.findall(r"(?m)^### Accepted:", group))
        overridden = len(re.findall(r"(?m)^### Overridden:", group))

        compacted_lines.append(
            f"{heading} (compacted)\n"
            f"Accepted: {accepted} | Overridden: {overridden}\n"
        )

    result = preamble + "".join(recent) + "\n".join(compacted_lines)
    path.write_text(result, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Memory patterns (DB-18)
# ---------------------------------------------------------------------------


@dataclass
class MemoryPattern:
    """A single pattern entry in memory.md."""

    name: str
    type: str  # decomposition | quality-gate | cost-calibration | escalation | debt
    observed: list[str] = field(default_factory=list)
    reinforced: int = 1
    last_active: str = ""
    invalidated: str = ""
    invalidation_reason: str = ""
    status: str = "active"  # active | fading | archived
    description: str = ""


#: Known metadata field names in memory.md pattern entries.
_MEMORY_FIELDS = frozenset({
    "type", "observed", "reinforced", "last_active",
    "invalidated", "invalidation_reason", "status",
})


def _parse_memory(content: str) -> list[MemoryPattern]:
    """Parse memory.md into structured pattern entries."""
    patterns: list[MemoryPattern] = []
    sections = re.split(r"(?m)^### ", content)
    for section in sections[1:]:  # skip preamble
        lines = section.strip().split("\n")
        if not lines:
            continue
        name = lines[0].strip()
        fields: dict[str, str] = {}
        desc_lines: list[str] = []
        for line in lines[1:]:
            if line.startswith("## "):
                break
            m = re.match(r"^(\w[\w_]*):\s*(.+)$", line)
            if m and m.group(1) in _MEMORY_FIELDS:
                fields[m.group(1)] = m.group(2).strip()
            elif line.strip():
                desc_lines.append(line.strip())

        observed_raw = fields.get("observed", "")
        observed = [o.strip() for o in observed_raw.split(",") if o.strip()]

        patterns.append(MemoryPattern(
            name=name,
            type=fields.get("type", ""),
            observed=observed,
            reinforced=_safe_int(fields.get("reinforced", "1"), 1),
            last_active=fields.get("last_active", ""),
            invalidated=fields.get("invalidated", ""),
            invalidation_reason=fields.get("invalidation_reason", ""),
            status=fields.get("status", "active"),
            description=" ".join(desc_lines),
        ))
    return patterns


def retrieve_patterns(
    patterns: list[MemoryPattern],
    *,
    type_filter: str | None = None,
    all_milestones: list[str] | None = None,
) -> list[MemoryPattern]:
    """Return patterns filtered by *type_filter*, excluding archived/invalidated.

    *all_milestones* is accepted for future recency scoring but currently
    unused beyond interface compatibility.
    """
    result: list[MemoryPattern] = []
    for p in patterns:
        # Exclude archived and invalidated patterns.
        if p.status == "archived":
            continue
        if p.invalidated:
            continue
        # Apply type filter when specified.
        if type_filter is not None and p.type != type_filter:
            continue
        result.append(p)
    return result


def _render_memory(patterns: list[MemoryPattern]) -> str:
    """Render pattern entries back to memory.md format."""
    lines = ["# Operational Memory", "", "## Patterns", ""]

    active = [p for p in patterns if p.status == "active"]
    fading = [p for p in patterns if p.status == "fading"]
    archived = [p for p in patterns if p.status == "archived"]

    for p in active + fading:
        lines.append(f"### {p.name}")
        lines.append(f"type: {p.type}")
        lines.append(f"observed: {', '.join(p.observed)}")
        lines.append(f"reinforced: {p.reinforced}")
        lines.append(f"last_active: {p.last_active}")
        if p.invalidated:
            lines.append(f"invalidated: {p.invalidated}")
            if p.invalidation_reason:
                lines.append(f"invalidation_reason: {p.invalidation_reason}")
        if p.status == "fading":
            lines.append(f"status: fading")
        lines.append("")
        if p.description:
            lines.append(p.description)
        lines.append("")

    if archived:
        lines.append("## Archived")
        lines.append("")
        for p in archived:
            lines.append(f"### {p.name}")
            lines.append(f"type: {p.type}")
            lines.append(f"observed: {', '.join(p.observed)}")
            lines.append(f"reinforced: {p.reinforced}")
            lines.append(f"last_active: {p.last_active}")
            if p.invalidated:
                lines.append(f"invalidated: {p.invalidated}")
                if p.invalidation_reason:
                    lines.append(f"invalidation_reason: {p.invalidation_reason}")
            lines.append(f"status: archived")
            lines.append("")
            if p.description:
                lines.append(p.description)
            lines.append("")

    return "\n".join(lines)


def _reinforce_or_create(
    patterns: list[MemoryPattern],
    name: str,
    type_: str,
    milestone: str,
    description: str,
) -> None:
    """Reinforce an existing pattern or create a new one.

    Idempotent per milestone: calling twice with the same milestone
    does not double-count reinforcement.
    """
    for p in patterns:
        if p.name == name and p.type == type_:
            if milestone not in p.observed:
                p.observed.append(milestone)
                p.reinforced += 1
            p.last_active = milestone
            p.description = description
            if p.invalidated:
                p.invalidated = ""
                p.invalidation_reason = ""
            if p.status != "active":
                p.status = "active"
            return
    patterns.append(MemoryPattern(
        name=name,
        type=type_,
        observed=[milestone],
        reinforced=1,
        last_active=milestone,
        description=description,
    ))


def _apply_decay(
    patterns: list[MemoryPattern],
    current_milestone: str,
    all_milestones: list[str],
    *,
    fading_threshold: int = 5,
    archive_threshold: int = 10,
) -> None:
    """Apply milestone-distance decay to patterns (DB-18 D3).

    Patterns with reinforced >= 5 are durable and exempt from decay.
    """
    if not all_milestones:
        return

    milestone_index: dict[str, int] = {
        m: i for i, m in enumerate(all_milestones)
    }
    current_idx = milestone_index.get(current_milestone)
    if current_idx is None:
        return

    for p in patterns:
        if p.reinforced >= 5:
            continue
        if p.invalidated:
            p.status = "archived"
            continue

        last_idx = milestone_index.get(p.last_active)
        if last_idx is None:
            continue

        distance = current_idx - last_idx
        if distance >= archive_threshold:
            p.status = "archived"
        elif distance >= fading_threshold and p.reinforced < 3:
            p.status = "fading"


# ---------------------------------------------------------------------------
# Temporal invalidation (DB-18 I3)
# ---------------------------------------------------------------------------

#: Structural keyword groups for contradiction detection.
#: Each group maps a pattern type to sets of mutually exclusive terms.
_CONTRADICTION_GROUPS: dict[str, list[set[str]]] = {
    "decomposition": [
        {"sequential", "parallel", "gather"},
    ],
    "escalation": [
        {"staleness", "validation_failure", "crash"},
    ],
}


def _detect_contradiction(
    existing_desc: str,
    new_desc: str,
    pattern_type: str,
) -> str:
    """Detect structural contradictions between existing and new descriptions.

    Returns a human-readable reason string if a contradiction is found,
    or an empty string if descriptions are compatible.

    Only checks structural keyword groups defined in _CONTRADICTION_GROUPS.
    Numeric-only changes (e.g., cycle count 2 -> 3) are NOT contradictions.
    """
    groups = _CONTRADICTION_GROUPS.get(pattern_type, [])
    if not groups:
        return ""

    existing_lower = existing_desc.lower()
    new_lower = new_desc.lower()

    for group in groups:
        existing_terms = {t for t in group if t in existing_lower}
        new_terms = {t for t in group if t in new_lower}
        if existing_terms and new_terms and existing_terms != new_terms:
            return (
                f"structural change: {', '.join(sorted(existing_terms))} "
                f"-> {', '.join(sorted(new_terms))}"
            )
    return ""


def _invalidate_contradictions(
    patterns: list[MemoryPattern],
    name: str,
    type_: str,
    new_description: str,
    milestone: str,
) -> bool:
    """Mark existing pattern as invalidated if new description contradicts it.

    Returns True if a contradiction was detected and the pattern was
    invalidated. Already-invalidated patterns are not double-invalidated.

    The invalidated pattern is preserved in memory.md (not deleted) with
    ``invalidated`` set to the milestone name and ``invalidation_reason``
    recording the detected structural change.
    """
    for p in patterns:
        if p.name != name or p.type != type_:
            continue
        # Skip already-invalidated patterns.
        if p.invalidated:
            return False
        reason = _detect_contradiction(p.description, new_description, type_)
        if reason:
            p.invalidated = milestone
            p.invalidation_reason = reason
            return True
    return False


_NUMERIC_PREFIX_RE = re.compile(r"^(\d+)")


def _milestone_sort_key(name: str) -> tuple[int, str]:
    """Sort key for milestone names: numeric prefix first, then lexicographic."""
    m = _NUMERIC_PREFIX_RE.match(name)
    if m:
        return (int(m.group(1)), name)
    return (0, name)


def _consolidated_milestones(
    memory_path: Path,
    milestones_dir: Path | None = None,
) -> set[str]:
    """Return the set of milestones structurally consolidated into memory.md.

    Only considers orchestrator-authored patterns (cost-calibration) to
    avoid false positives from supervisor annotations that mention a
    milestone before structural consolidation ran for it.

    When *milestones_dir* is provided, the result is intersected with
    actual milestone directory names to prune stale references from
    memory.md (e.g. after a milestone directory was deleted).
    """
    if not memory_path.exists():
        return set()
    patterns = _parse_memory(memory_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for p in patterns:
        if p.type == "cost-calibration":
            seen.update(p.observed)
    if milestones_dir is not None and milestones_dir.is_dir():
        existing = {
            d.name for d in milestones_dir.iterdir()
            if d.is_dir() and (d / "metrics.md").exists()
        }
        seen &= existing
    return seen


def _accumulate_distribution(existing_desc: str, new_value: int) -> str:
    """Accumulate a numeric value into a min/median/max distribution string.

    Parses existing distribution data from *existing_desc*, adds *new_value*,
    and returns a formatted distribution suffix string.
    """
    import statistics

    # Extract prior values from distribution string if present.
    values: list[int] = []
    dist_match = re.search(r"Distribution: cycles.*?\(n=(\d+)\)", existing_desc)
    if dist_match:
        # Extract individual values from stored representation.
        nums = re.findall(r"min=(\d+).*?median=(\d+).*?max=(\d+).*?n=(\d+)", existing_desc)
        if nums:
            _min, _med, _max, _n = int(nums[0][0]), int(nums[0][1]), int(nums[0][2]), int(nums[0][3])
            # Reconstruct approximate values from min/median/max.
            values = [_min] + [_med] * max(0, _n - 2) + [_max] if _n > 1 else [_min]
    elif existing_desc:
        # Try to parse a single cycle count from plain description.
        cycle_match = re.match(r"^(\d+)\s+cycles?", existing_desc)
        if cycle_match:
            values = [int(cycle_match.group(1))]

    values.append(new_value)
    n = len(values)
    med = int(statistics.median(values))
    return f"Distribution: cycles min={min(values)} median={med} max={max(values)} (n={n})."


_FED_INTO_RE = re.compile(r"\(([^)]+)\)\s*$")


def _extract_milestone_from_fed_into(entry_text: str) -> str | None:
    """Extract the milestone name from a 'Fed into:' line in an entry.

    Looks for the pattern ``(milestone-name)`` at the end of the line.
    Returns None if no Fed-into tag or no parenthesized milestone found.
    """
    for line in entry_text.split("\n"):
        if "**Fed into:**" in line or "Fed into:" in line:
            m = _FED_INTO_RE.search(line)
            if m:
                return m.group(1).strip()
    return None


def _parse_understanding_sections(
    content: str,
) -> tuple[str, dict[str, list[str]], dict[str, str]]:
    """Split understanding.md into preamble, section entries, and section prose.

    Returns ``(preamble, sections, section_prose)`` where *preamble* is
    everything before the first ``## `` header, *sections* maps section
    titles to lists of entry texts (each starting with ``### ``), and
    *section_prose* maps section titles to the prose text between the
    ``## `` header line and the first ``### `` entry (preserved verbatim).
    """
    # Split on ## headers (level 2).
    parts = re.split(r"(?m)^## ", content)
    preamble = parts[0]
    sections: dict[str, list[str]] = {}
    section_prose: dict[str, str] = {}

    for part in parts[1:]:
        # The section title is the first line.
        newline_idx = part.find("\n")
        if newline_idx == -1:
            title = part.strip()
            body = ""
        else:
            title = part[:newline_idx].strip()
            body = part[newline_idx + 1:]

        # Split body into entries at ### headers.
        entry_parts = re.split(r"(?m)^### ", body)
        # First element is text between ## header and first ### — preserve it.
        prose = entry_parts[0] if entry_parts else ""
        section_prose[title] = prose
        entries: list[str] = []
        for ep in entry_parts[1:]:
            entries.append("### " + ep)

        sections[title] = entries

    return preamble, sections, section_prose


def _render_understanding(
    preamble: str,
    sections: dict[str, list[str]],
    section_prose: dict[str, str] | None = None,
) -> str:
    """Render understanding.md from preamble, sections, and section prose.

    Each part from ``_parse_understanding_sections()`` already includes its
    exact original whitespace (preamble ends with ``\\n\\n``, prose starts
    with ``\\n`` and ends with ``\\n\\n``, entries start with ``### ``).
    Parts are concatenated directly without any separator.
    """
    parts = [preamble]
    for title, entries in sections.items():
        parts.append(f"## {title}\n")
        # Emit per-section prose verbatim when available.  The prose
        # captured by _parse_understanding_sections includes structural
        # whitespace (e.g. "\n" for the blank line between header and
        # first entry), so it must be emitted as-is for round-trip
        # fidelity.
        if section_prose is not None:
            prose = section_prose.get(title, "")
            if prose:
                parts.append(prose)
        if entries:
            parts.append("".join(entries))
    return "".join(parts)


def compact_understanding(
    understanding_path: Path,
    milestones_dir: Path,
    *,
    archive_threshold: int = 10,
) -> bool:
    """Resolve and archive understanding.md entries for completed milestones.

    Entries in "Active tensions" or "Continuity" whose "Fed into:" tag
    references a completed milestone (one with metrics.md) are moved to
    "Resolved".  Entries in "Resolved" referencing milestones older than
    *archive_threshold* milestones from the latest are removed entirely
    (git history preserves them).

    Returns True if any changes were made, False otherwise.
    """
    if not understanding_path.exists():
        return False

    content = understanding_path.read_text(encoding="utf-8")
    preamble, sections, section_prose = _parse_understanding_sections(content)

    # Determine which milestones are completed (have metrics.md).
    completed_milestones: set[str] = set()
    if milestones_dir.is_dir():
        for d in milestones_dir.iterdir():
            if d.is_dir() and (d / "metrics.md").exists():
                completed_milestones.add(d.name)

    # Build sorted milestone list for distance computation.
    all_milestones: list[str] = sorted(
        completed_milestones, key=_milestone_sort_key,
    )
    latest_idx = len(all_milestones) - 1 if all_milestones else -1

    milestone_index: dict[str, int] = {
        m: i for i, m in enumerate(all_milestones)
    }

    changed = False

    # --- Resolution: move completed entries to Resolved ---
    resolved_entries = sections.get("Resolved", [])
    for section_name in ("Active tensions", "Continuity"):
        if section_name not in sections:
            continue
        remaining: list[str] = []
        for entry in sections[section_name]:
            ms_name = _extract_milestone_from_fed_into(entry)
            if ms_name and ms_name in completed_milestones:
                resolved_entries.append(entry)
                changed = True
            else:
                remaining.append(entry)
        sections[section_name] = remaining

    # --- Archival: remove old Resolved entries ---
    if latest_idx >= 0 and resolved_entries:
        kept: list[str] = []
        for entry in resolved_entries:
            ms_name = _extract_milestone_from_fed_into(entry)
            if ms_name and ms_name in milestone_index:
                distance = latest_idx - milestone_index[ms_name]
                if distance > archive_threshold:
                    changed = True
                    continue  # Remove from file.
            kept.append(entry)
        resolved_entries = kept

    sections["Resolved"] = resolved_entries

    if not changed:
        return False

    rendered = _render_understanding(preamble, sections, section_prose)
    understanding_path.write_text(rendered, encoding="utf-8")
    return True
