"""Golden context structural validation.

Validates golden context file structure at coordinator cycle boundaries.
Checks form, not content — the orchestrator doesn't judge whether decisions
are good, only whether files are well-formed.

Public API:
    validate_golden_context(project_dir: Path, milestone: str) -> list[str]
"""

from __future__ import annotations

import re
from pathlib import Path

# Valid status values per file type.
_TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
_MILESTONE_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked"})


def validate_golden_context(project_dir: Path, milestone: str) -> list[str]:
    """Validate golden context structure after a cycle.

    Returns errors (empty = valid). Only checks files that exist — the
    coordinator creates files as needed, so missing files are not errors.
    """
    errors: list[str] = []
    clou_dir = project_dir / ".clou"
    milestone_dir = clou_dir / "milestones" / milestone

    # Coordinator checkpoint (active/coordinator.md)
    checkpoint = clou_dir / "active" / "coordinator.md"
    if checkpoint.exists():
        errors += _validate_coordinator(checkpoint)

    # Per-phase: execution.md — check phase subdirectories (the actual write path)
    # and the flat path for backwards compatibility.
    execution_flat = milestone_dir / "execution.md"
    if execution_flat.exists():
        errors += _validate_execution(execution_flat)

    for execution_phase in sorted(milestone_dir.glob("phases/*/execution.md")):
        errors += _validate_execution(execution_phase)

    # Per-milestone: decisions.md
    decisions = milestone_dir / "decisions.md"
    if decisions.exists():
        errors += _validate_decisions(decisions)

    # Per-milestone: assessment.md
    assessment = milestone_dir / "assessment.md"
    if assessment.exists():
        errors += _validate_assessment(assessment)

    # Per-milestone: status.md
    status = milestone_dir / "status.md"
    if status.exists():
        errors += _validate_status(status)

    # Project-level: roadmap.md
    roadmap = clou_dir / "roadmap.md"
    if roadmap.exists():
        errors += _validate_roadmap(roadmap)

    return errors


# ---------------------------------------------------------------------------
# Per-file validators
# ---------------------------------------------------------------------------


def _validate_coordinator(path: Path) -> list[str]:
    """Validate active/coordinator.md checkpoint structure."""
    errors: list[str] = []
    content = path.read_text()
    for required in ("## Cycle", "## Phase Status"):
        if required not in content:
            errors.append(f"active/coordinator.md missing '{required}'")
    return errors


def _validate_execution(path: Path) -> list[str]:
    """Validate execution.md per-phase file structure."""
    rel = _rel(path)
    errors: list[str] = []
    content = path.read_text()

    # Must have ## Summary with status: field
    if "## Summary" not in content:
        errors.append(f"{rel} missing '## Summary'")
    else:
        summary_block = _section_text(content, "## Summary")
        if not re.search(r"(?m)^status:", summary_block):
            errors.append(f"{rel} '## Summary' missing 'status:' field")

    # Must have ## Tasks with at least one ### T<N>: entry
    if "## Tasks" not in content:
        errors.append(f"{rel} missing '## Tasks'")
    else:
        tasks_block = _section_text(content, "## Tasks")
        task_headers = re.findall(r"(?m)^### T\d+:", tasks_block)
        if not task_headers:
            errors.append(f"{rel} '## Tasks' has no '### T<N>:' entries")
        else:
            # Each task must have **Status:** with valid value
            errors += _check_task_statuses(tasks_block, rel)

    return errors


def _validate_decisions(path: Path) -> list[str]:
    """Validate decisions.md per-milestone file structure."""
    rel = _rel(path)
    errors: list[str] = []
    content = path.read_text()

    # Must contain at least one ## Cycle section
    cycle_sections = re.findall(r"(?m)^## Cycle", content)
    if not cycle_sections:
        errors.append(f"{rel} missing '## Cycle' section")
        return errors

    # Each cycle section must have at least one decision entry,
    # unless it's an ASSESS/Brutalist section (which may legitimately
    # have zero findings when convergence is reached).
    cycle_blocks = _split_sections(content, "## Cycle")
    for i, block in enumerate(cycle_blocks, 1):
        has_entry = bool(re.search(r"(?m)^### (Accepted|Overridden|Tradeoff):", block))
        if not has_entry:
            # ASSESS sections with zero findings are valid (convergence).
            first_line = block.strip().split("\n", 1)[0].lower()
            if "assess" in first_line or "brutalist" in first_line:
                continue
            errors.append(
                f"{rel} Cycle section {i} has no "
                f"'### Accepted:', '### Overridden:', or '### Tradeoff:' entry"
            )

    return errors


def _validate_assessment(path: Path) -> list[str]:
    """Validate assessment.md per-milestone file structure."""
    rel = _rel(path)
    errors: list[str] = []
    content = path.read_text()

    # Must have ## Summary with status: field
    if "## Summary" not in content:
        errors.append(f"{rel} missing '## Summary'")
        return errors

    summary_block = _section_text(content, "## Summary")
    status_match = re.search(r"(?m)^status:\s*(\S+)", summary_block)
    if not status_match:
        errors.append(f"{rel} '## Summary' missing 'status:' field")
        return errors

    # "blocked" is a valid terminal state — no findings required.
    if status_match.group(1) == "blocked":
        return errors

    # Must have ## Findings section
    if "## Findings" not in content:
        errors.append(f"{rel} missing '## Findings'")
        return errors

    # Each finding must have **Severity:** and **Finding:**
    findings_block = _section_text(content, "## Findings")
    finding_headers = re.findall(r"(?m)^### F\d+:", findings_block)
    if finding_headers:
        finding_blocks = _split_sections(findings_block, r"### F\d+:")
        for k, block in enumerate(finding_blocks, 1):
            if not re.search(r"\*\*Severity:\*\*", block):
                errors.append(f"{rel} finding {k} missing '**Severity:**'")
            if not re.search(r"\*\*Finding:\*\*", block):
                errors.append(f"{rel} finding {k} missing '**Finding:**'")

    return errors


def _validate_status(path: Path) -> list[str]:
    """Validate status.md per-milestone file structure."""
    rel = _rel(path)
    errors: list[str] = []
    content = path.read_text()

    if not re.search(r"(?mi)current state", content):
        errors.append(f"{rel} missing 'Current State' field")

    if not re.search(r"(?mi)phase progress", content):
        errors.append(f"{rel} missing 'Phase Progress' table")

    return errors


def _validate_roadmap(path: Path) -> list[str]:
    """Validate roadmap.md project-level file structure."""
    rel = _rel(path)
    errors: list[str] = []
    content = path.read_text()

    if "## Milestones" not in content:
        errors.append(f"{rel} missing '## Milestones'")
        return errors

    milestones_block = _section_text(content, "## Milestones")
    entries = re.findall(r"(?m)^### \d+\. ", milestones_block)
    if not entries:
        errors.append(f"{rel} '## Milestones' has no '### N. name' entries")
        return errors

    # Each milestone entry must have **Status:** with valid value
    entry_blocks = _split_sections(milestones_block, r"### \d+\.")
    for j, block in enumerate(entry_blocks, 1):
        match = re.search(r"\*\*Status:\*\*\s*(\S+)", block)
        if not match:
            errors.append(f"{rel} milestone entry {j} missing '**Status:**'")
        elif match.group(1) not in _MILESTONE_STATUSES:
            errors.append(
                f"{rel} milestone entry {j} has invalid status "
                f"'{match.group(1)}' "
                f"(expected one of: {', '.join(sorted(_MILESTONE_STATUSES))})"
            )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    """Return a short relative path for error messages.

    Walks up from path looking for `.clou` to produce a path relative to it.
    Falls back to the file name.
    """
    parts = path.parts
    for i, part in enumerate(parts):
        if part == ".clou":
            return "/".join(parts[i + 1 :])
    return path.name


def _section_text(content: str, header: str) -> str:
    """Extract text from *header* until the next section of equal or higher level."""
    level = len(header) - len(header.lstrip("#"))
    pattern = re.escape(header)
    match = re.search(rf"(?m)^{pattern}", content)
    if not match:
        return ""
    start = match.end()
    # Find next header at same or higher level
    next_header = re.search(rf"(?m)^#{{1,{level}}} ", content[start:])
    if next_header:
        return content[start : start + next_header.start()]
    return content[start:]


def _split_sections(content: str, header_pattern: str) -> list[str]:
    """Split content into blocks starting at each *header_pattern* match.

    Returns the text *after* each header until the next header of the same
    pattern (or end of string).
    """
    splits = list(re.finditer(rf"(?m)^{header_pattern}", content))
    blocks: list[str] = []
    for i, m in enumerate(splits):
        start = m.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        blocks.append(content[start:end])
    return blocks


def _check_task_statuses(tasks_block: str, rel: str) -> list[str]:
    """Check each task entry in ## Tasks for a valid **Status:** field."""
    errors: list[str] = []
    task_blocks = _split_sections(tasks_block, r"### T\d+:")
    for k, block in enumerate(task_blocks, 1):
        match = re.search(r"\*\*Status:\*\*\s*(\S+)", block)
        if not match:
            errors.append(f"{rel} task {k} missing '**Status:**'")
        elif match.group(1) not in _TASK_STATUSES:
            errors.append(
                f"{rel} task {k} has invalid status "
                f"'{match.group(1)}' "
                f"(expected one of: {', '.join(sorted(_TASK_STATUSES))})"
            )
    return errors
