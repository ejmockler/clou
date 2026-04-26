"""Self-heal logic for golden context validation errors.

Provides attempt_self_heal() and log_self_heal_attempt(), plus the
normalisation helpers they use (checkpoint re-render, status re-render
from checkpoint).

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
    # M36 F2: match parse_checkpoint's ``[ \t]*`` + ``.*`` regex so an
    # empty value line like ``current_phase: \n`` doesn't greedily
    # swallow the next line and skew key detection.
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\w[\w_]*):[ \t]*(.*)$", content):
        fields[match.group(1)] = match.group(2).strip()

    # Bail if required keys are missing -- nothing safe to normalise.
    if "cycle" not in fields and "current_cycle" not in fields:
        return content, fixes
    if "next_step" not in fields:
        return content, fixes

    # Parse (handles aliases, defaults) then re-render (canonical format).
    # M36 F4 (round-4): pass ALL 15 render_checkpoint fields.  Previously
    # cycle_outcome, valid_findings, and consecutive_zero_valid were
    # silently defaulted on normalisation, wiping ASSESS convergence
    # state and halt-pending-review markers.  This is the same
    # wipe-class pattern as F2 applied to sibling convergence fields.
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
        cycle_outcome=cp.cycle_outcome,
        valid_findings=cp.valid_findings,
        consecutive_zero_valid=cp.consecutive_zero_valid,
        # M36 I1 (F2 rework): preserve ORIENT stash across self-heal
        # so normalisation doesn't wipe the restoration signal.
        pre_orient_next_step=cp.pre_orient_next_step,
        # M49b B6: same wipe-class concern for halt stash.
        pre_halt_next_step=cp.pre_halt_next_step,
        # M52 F38: same wipe-class — self-heal must not erase the
        # gate verdict.  Inheriting also keeps the strict-gating
        # guarantee (advance still requires a non-None verdict).
        last_acceptance_verdict=cp.last_acceptance_verdict,
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


def _rerender_status_from_checkpoint(
    project_dir: Path,
    milestone: str,
    status_path: Path,
) -> tuple[str | None, list[str]]:
    """Re-render status.md entirely from the checkpoint.

    If a valid checkpoint exists, derives status.md content from it
    (via ``render_status_from_checkpoint``).  This replaces the old
    approach of patching individual fields and normalising table values.

    Returns ``(new_content, list_of_descriptions)``.  Returns
    ``(None, [])`` when no checkpoint is available, signalling the
    caller to fall back to legacy normalization.
    """
    from clou.golden_context import _extract_phase_names, render_status_from_checkpoint

    fixes: list[str] = []

    checkpoint_path = (
        project_dir / ".clou" / "milestones" / milestone
        / "active" / "coordinator.md"
    )
    if not checkpoint_path.exists():
        return None, fixes

    original = status_path.read_text() if status_path.exists() else ""

    cp = parse_checkpoint(checkpoint_path.read_text())
    ms_dir = project_dir / ".clou" / "milestones" / milestone
    phase_names = _extract_phase_names(ms_dir)

    new_content = render_status_from_checkpoint(
        milestone=milestone,
        checkpoint=cp,
        phase_names=phase_names or None,
    )

    if new_content != original:
        fixes.append("re-rendered status.md from checkpoint")

    return new_content, fixes


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
            # Prefer re-rendering status.md from checkpoint (unified
            # state).  Falls back to legacy normalizers when no
            # checkpoint is available (e.g. before first PLAN cycle).
            rerendered, status_fixes = _rerender_status_from_checkpoint(
                project_dir, milestone, abs_path,
            )
            if rerendered is not None:
                # Checkpoint exists -- use the re-rendered content
                # (even if identical, don't fall back to legacy).
                content = rerendered
                fixes.extend(status_fixes)
            else:
                # No checkpoint available -- fall back to legacy
                # field/table normalization.
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
