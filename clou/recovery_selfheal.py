"""Self-heal logic for golden context validation errors.

Provides attempt_self_heal() and log_self_heal_attempt(), plus the
normalisation helpers they use (checkpoint re-render, status table fix,
missing Current State fields).

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clou.recovery_checkpoint import _validate_milestone, parse_checkpoint

_log = logging.getLogger(__name__)


def _is_coordinator_writable(rel_path: str, milestone: str) -> bool:
    """Return True if *rel_path* is a coordinator-owned file.

    *rel_path* is relative to ``.clou/`` (e.g. ``milestones/my-ms/status.md``).
    Includes both Write-permitted files (from hooks) and protocol artifact
    files that are written via MCP tools (not Write).  Self-heal needs
    access to both as defense-in-depth.
    """
    from clou.golden_context import PROTOCOL_ARTIFACT_PATTERNS
    from clou.hooks import WRITE_PERMISSIONS

    patterns = list(WRITE_PERMISSIONS.get("coordinator", []))
    # Protocol artifacts are written by MCP tools, not Write, but
    # self-heal (Python code) still needs to fix them.
    patterns.extend(PROTOCOL_ARTIFACT_PATTERNS)
    return any(fnmatch.fnmatch(rel_path, p) for p in patterns)


#: Field aliases agents commonly write instead of canonical names.
_CHECKPOINT_FIELD_ALIASES: dict[str, str] = {
    "phase": "current_phase",
    "current_cycle": "cycle",
    "current_step": "step",
    "cycle_type": "step",
}

#: Canonical checkpoint fields with default values when absent.
_CHECKPOINT_DEFAULTS: dict[str, str] = {
    "step": "PLAN",
    "current_phase": "",
    "phases_completed": "0",
    "phases_total": "0",
}


def _normalise_checkpoint(content: str) -> tuple[str, list[str]]:
    """Normalise a coordinator checkpoint via parse->re-render.

    Returns ``(new_content, list_of_descriptions)``.  Idempotent.
    Uses ``parse_checkpoint`` (which handles aliases and defaults)
    then ``render_checkpoint`` (which guarantees canonical format).
    """
    from clou.golden_context import render_checkpoint

    fixes: list[str] = []

    # Parse existing fields to detect what's present.
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):\s*(.+)$", content):
        fields[match.group(1)] = match.group(2).strip()

    # Bail if required keys are missing -- nothing safe to normalise.
    if "cycle" not in fields and "current_cycle" not in fields:
        return content, fixes
    if "next_step" not in fields:
        return content, fixes

    # Parse (handles aliases, defaults) then re-render (canonical format).
    cp = parse_checkpoint(content)
    new_content = render_checkpoint(
        cycle=cp.cycle,
        step=cp.step,
        next_step=cp.next_step,
        current_phase=cp.current_phase,
        phases_completed=cp.phases_completed,
        phases_total=cp.phases_total,
        validation_retries=cp.validation_retries,
        readiness_retries=cp.readiness_retries,
        crash_retries=cp.crash_retries,
        staleness_count=cp.staleness_count,
    )

    if new_content != content:
        fixes.append("re-rendered checkpoint via serializer")

    return new_content, fixes


#: Common status value misspellings and their canonical forms.
_STATUS_NORMALISATION: dict[str, str] = {
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "in_Progress": "in_progress",
    "In Progress": "in_progress",
    "IN PROGRESS": "in_progress",
    "In_Progress": "in_progress",
    "IN_PROGRESS": "in_progress",
}


def _normalise_status_in_table(content: str) -> tuple[str, list[str]]:
    """Normalise status values in a markdown Phase Progress table.

    Scans table rows for non-canonical status values and replaces them.
    Returns ``(new_content, list_of_descriptions)`` where descriptions
    explain each fix applied.  The function is idempotent.
    """
    fixes: list[str] = []
    lines = content.split("\n")
    new_lines: list[str] = []

    in_progress_table = False
    for line in lines:
        # Detect Phase Progress section (case-insensitive).
        if re.match(r"^##\s+Phase\s+Progress", line, re.I):
            in_progress_table = True
            new_lines.append(line)
            continue

        # Leave table region on next section header.
        if in_progress_table and re.match(r"^##\s+", line) and not re.match(
            r"^##\s+Phase\s+Progress", line, re.I
        ):
            in_progress_table = False

        if in_progress_table and "|" in line:
            # Skip header/divider rows.
            if re.match(r"^\s*\|[-\s|:]+\|\s*$", line) or re.match(
                r"^\s*\|.*Phase.*Status", line, re.I
            ):
                new_lines.append(line)
                continue

            cells = line.split("|")
            modified = False
            for idx, cell in enumerate(cells):
                stripped = cell.strip()
                lower = stripped.lower()
                if lower in {v.lower() for v in _STATUS_NORMALISATION}:
                    # Look up the canonical form.
                    canonical: str | None = None
                    for bad, good in _STATUS_NORMALISATION.items():
                        if bad.lower() == lower:
                            canonical = good
                            break
                    if canonical is not None and stripped != canonical:
                        fixes.append(
                            f"normalised status '{stripped}' -> '{canonical}'"
                        )
                        cells[idx] = cell.replace(stripped, canonical)
                        modified = True
            if modified:
                new_lines.append("|".join(cells))
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    return "\n".join(new_lines), fixes


def _add_missing_current_state_fields(
    content: str, milestone: str, project_dir: Path
) -> tuple[str, list[str]]:
    """Add missing phase:/cycle:/status: fields to the Current State section.

    Reads defaults from the coordinator checkpoint when available.
    Returns ``(new_content, list_of_descriptions)``.  Idempotent.
    """
    fixes: list[str] = []

    # Only operate if Current State section exists.
    if not re.search(r"(?mi)^##\s+Current\s+State", content):
        return content, fixes

    # Find the Current State section boundaries.
    cs_match = re.search(r"(?mi)^(##\s+Current\s+State)\s*\n", content)
    if not cs_match:
        return content, fixes

    section_start = cs_match.end()
    # Find next ## header or end of file.
    next_header = re.search(r"(?m)^##\s+", content[section_start:])
    section_end = section_start + next_header.start() if next_header else len(content)
    section_text = content[section_start:section_end]

    # Read checkpoint defaults (milestone-scoped path).
    checkpoint_path = project_dir / ".clou" / "milestones" / milestone / "active" / "coordinator.md"
    default_phase = "unknown"
    default_cycle = "1"
    if checkpoint_path.exists():
        cp = parse_checkpoint(checkpoint_path.read_text())
        if cp.current_phase:
            default_phase = cp.current_phase
        default_cycle = str(max(cp.cycle, 1))

    additions: list[str] = []

    if not re.search(r"(?m)^phase:", section_text):
        additions.append(f"phase: {default_phase}")
        fixes.append(f"added missing 'phase: {default_phase}' to Current State")

    if not re.search(r"(?m)^cycle:", section_text):
        additions.append(f"cycle: {default_cycle}")
        fixes.append(f"added missing 'cycle: {default_cycle}' to Current State")

    if not re.search(r"(?m)^status:", section_text):
        additions.append("status: in_progress")
        fixes.append("added missing 'status: in_progress' to Current State")

    if additions:
        # Insert after the section header line (before existing content).
        insert_text = "\n".join(additions) + "\n"
        content = content[:section_start] + insert_text + content[section_start:]

    return content, fixes


def attempt_self_heal(
    project_dir: Path,
    milestone: str,
    errors: list[Any],
) -> list[str]:
    """Attempt to fix validation errors in coordinator-writable files.

    Returns a list of descriptions of what was fixed (empty = nothing fixable).
    Only modifies files matching coordinator ``WRITE_PERMISSIONS`` patterns.
    Fixes are deterministic, safe, and idempotent.
    """
    from clou.validation import ValidationFinding

    _validate_milestone(milestone)

    all_fixes: list[str] = []

    # Group errors by file path.
    path_errors: dict[str, list[Any]] = {}
    for err in errors:
        if isinstance(err, ValidationFinding):
            path_errors.setdefault(err.path, []).append(err)
        else:
            # Legacy string errors -- cannot determine path, skip.
            continue

    for rel_path, _file_errors in path_errors.items():
        # Only touch coordinator-writable files.
        # The rel_path from validation is relative to .clou/ (e.g.
        # "milestones/my-ms/status.md").
        if not _is_coordinator_writable(rel_path, milestone):
            _log.debug(
                "Self-heal: skipping %r --- not coordinator-writable", rel_path,
            )
            continue

        abs_path = project_dir / ".clou" / rel_path
        if not abs_path.exists():
            continue

        content = abs_path.read_text()
        original = content
        fixes: list[str] = []

        # Apply fixers based on the file type.
        if rel_path.endswith("active/coordinator.md"):
            content, cp_fixes = _normalise_checkpoint(content)
            fixes.extend(cp_fixes)

        if rel_path.endswith("status.md"):
            content, table_fixes = _normalise_status_in_table(content)
            fixes.extend(table_fixes)

            content, field_fixes = _add_missing_current_state_fields(
                content, milestone, project_dir,
            )
            fixes.extend(field_fixes)

        if fixes and content != original:
            abs_path.write_text(content)
            for fix_desc in fixes:
                _log.info("Self-heal [%s]: %s", rel_path, fix_desc)
            all_fixes.extend(fixes)

    return all_fixes


def log_self_heal_attempt(
    project_dir: Path,
    milestone: str,
    fixes: list[str],
    remaining_errors: list[Any],
) -> None:
    """Log self-heal attempt to decisions.md (coordinator-writable).

    Appends a brief note documenting what was auto-fixed and what
    errors remain (if any).
    """
    from clou.validation import ValidationFinding

    _validate_milestone(milestone)

    decisions_path = (
        project_dir / ".clou" / "milestones" / milestone / "decisions.md"
    )

    now = datetime.now(UTC).isoformat()
    lines: list[str] = [
        "",
        f"### Self-Heal ({now})",
        "",
    ]

    if fixes:
        lines.append("**Auto-fixed:**")
        for fix in fixes:
            lines.append(f"- {fix}")
        lines.append("")

    if remaining_errors:
        lines.append("**Remaining errors (unfixable):**")
        for err in remaining_errors:
            if isinstance(err, ValidationFinding):
                lines.append(f"- [{err.path}] {err.message}")
            else:
                lines.append(f"- {err}")
        lines.append("")

    note = "\n".join(lines)

    if decisions_path.exists():
        decisions_path.write_text(decisions_path.read_text() + note)
    else:
        # Create the file if it doesn't exist -- the coordinator is
        # allowed to write decisions.md.
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        decisions_path.write_text(f"# Decisions\n{note}")
