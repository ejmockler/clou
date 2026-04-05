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
        if p.name == name:
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


_NUMERIC_PREFIX_RE = re.compile(r"^(\d+)")


def _milestone_sort_key(name: str) -> tuple[int, str]:
    """Sort key for milestone names: numeric prefix first, then lexicographic."""
    m = _NUMERIC_PREFIX_RE.match(name)
    if m:
        return (int(m.group(1)), name)
    return (0, name)


def _consolidated_milestones(memory_path: Path) -> set[str]:
    """Return the set of milestones structurally consolidated into memory.md.

    Only considers orchestrator-authored patterns (cost-calibration) to
    avoid false positives from supervisor annotations that mention a
    milestone before structural consolidation ran for it.
    """
    if not memory_path.exists():
        return set()
    patterns = _parse_memory(memory_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for p in patterns:
        if p.type == "cost-calibration":
            seen.update(p.observed)
    return seen
