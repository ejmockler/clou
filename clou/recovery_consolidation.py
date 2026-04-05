"""Memory consolidation for completed milestones (DB-18).

Provides consolidate_pending(), consolidate_milestone(), and metrics
parsing helpers (_parse_metrics_header, _count_metrics_section_rows,
_analyze_compose, etc.).

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import ast as _ast
import logging
import re
from pathlib import Path

from clou.recovery_checkpoint import _safe_int, _validate_milestone
from clou.recovery_compaction import (
    _apply_decay,
    _consolidated_milestones,
    _milestone_sort_key,
    _parse_memory,
    _reinforce_or_create,
    _render_memory,
)

_log = logging.getLogger(__name__)


def _parse_metrics_header(content: str) -> dict[str, str]:
    """Extract key: value pairs from the metrics.md header."""
    fields: dict[str, str] = {}
    for line in content.split("\n"):
        if line.startswith("#"):
            continue
        m = re.match(r"^(\w[\w_]*):\s*(.+)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
        elif line.strip() == "":
            # Stop at first blank line after header block.
            if fields:
                break
    return fields


def _count_metrics_section_rows(content: str, section_header: str) -> int:
    """Count data rows in a metrics.md table section.

    Skips the header row and separator row. Returns 0 if section is absent.
    """
    count = 0
    in_section = False
    for line in content.split("\n"):
        if line.strip() == section_header:
            in_section = True
            continue
        if in_section and line.startswith("##"):
            break
        if in_section and line.startswith("|") and not line.startswith("|--"):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells and not all(c.replace(" ", "").isalpha() for c in cells):
                count += 1
    return count


def _count_qg_unavailable(content: str) -> int:
    """Count quality gate tool unavailability events from ## Quality Gate table.

    Returns the number of rows where Tools Unavailable is not 'none'.
    """
    count = 0
    in_section = False
    header_skipped = False
    for line in content.split("\n"):
        if line.strip() == "## Quality Gate":
            in_section = True
            continue
        if in_section and line.startswith("##"):
            break
        if in_section and line.startswith("|") and not line.startswith("|--"):
            if not header_skipped:
                header_skipped = True
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 4 and cells[3] != "none":
                count += 1
    return count


def _parse_cycle_types(content: str) -> list[str]:
    """Extract cycle type sequence from the metrics.md Cycles table."""
    types: list[str] = []
    in_table = False
    for line in content.split("\n"):
        if "## Cycles" in line:
            in_table = True
            continue
        if in_table and line.startswith("##"):
            break
        if in_table and line.startswith("|") and not line.startswith("|--") and "Type" not in line:
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 3:
                types.append(cells[2])  # Type column
    return types


def _analyze_compose(compose_path: Path) -> tuple[int, bool]:
    """Analyze compose.py topology via graph.py's existing AST infrastructure.

    Returns (phase_count, has_gather). Uses extract_dag_data() from
    clou.graph (the canonical AST analysis, already validated by DB-02).
    """
    if not compose_path.exists():
        return 0, False

    try:
        from clou.graph import extract_dag_data

        source = compose_path.read_text(encoding="utf-8")
        tasks, deps = extract_dag_data(source)
    except (OSError, SyntaxError, Exception):
        return 0, False

    phase_count = sum(1 for t in tasks if t["name"] != "verify")

    has_gather = False
    try:
        tree = _ast.parse(source)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.AsyncFunctionDef) and node.name in (
                "execute", "execute_milestone",
            ):
                for child in _ast.walk(node):
                    if isinstance(child, _ast.Call):
                        func = child.func
                        if (
                            isinstance(func, _ast.Name) and func.id == "gather"
                        ) or (
                            isinstance(func, _ast.Attribute) and func.attr == "gather"
                        ):
                            has_gather = True
                            break
                break
    except (SyntaxError, Exception):
        pass

    return phase_count, has_gather


def consolidate_pending(project_dir: Path) -> int:
    """Consolidate any completed milestones not yet in memory.md (DB-18).

    Compares milestone directories (those with metrics.md) against the
    milestones already recorded in memory.md's ``observed`` fields.
    Consolidates the difference in chronological order.

    Returns count of milestones consolidated.
    """
    clou_dir = project_dir / ".clou"
    milestones_dir = clou_dir / "milestones"
    if not milestones_dir.exists():
        return 0

    memory_path = clou_dir / "memory.md"
    already_consolidated = _consolidated_milestones(memory_path)

    pending: list[str] = []
    for ms_dir in milestones_dir.iterdir():
        if not ms_dir.is_dir():
            continue
        if (ms_dir / "metrics.md").exists() and ms_dir.name not in already_consolidated:
            pending.append(ms_dir.name)

    if not pending:
        return 0

    pending.sort(key=_milestone_sort_key)

    count = 0
    for ms_name in pending:
        try:
            if consolidate_milestone(project_dir, ms_name):
                count += 1
                _log.info("Pending consolidation: %s", ms_name)
        except Exception:
            _log.warning(
                "Pending consolidation failed for %r", ms_name, exc_info=True,
            )

    return count


def consolidate_milestone(
    project_dir: Path,
    milestone: str,
) -> bool:
    """Consolidate a completed milestone into operational memory (DB-18).

    Reads metrics.md (orchestrator-written) and compose.py (AST-parsed)
    to extract structural patterns. Updates .clou/memory.md with new or
    reinforced patterns. Applies milestone-distance decay.

    Returns True if consolidation was performed.
    """
    _validate_milestone(milestone)

    clou_dir = project_dir / ".clou"
    ms_dir = clou_dir / "milestones" / milestone

    metrics_path = ms_dir / "metrics.md"
    if not metrics_path.exists():
        _log.warning("consolidate_milestone: no metrics.md for %r", milestone)
        return False

    metrics_content = metrics_path.read_text(encoding="utf-8")
    header = _parse_metrics_header(metrics_content)

    compose_path = ms_dir / "compose.py"
    phase_count, has_gather = _analyze_compose(compose_path)

    memory_path = clou_dir / "memory.md"
    if memory_path.exists():
        patterns = _parse_memory(memory_path.read_text(encoding="utf-8"))
    else:
        patterns = []

    all_milestones = sorted(
        [d.name for d in (clou_dir / "milestones").iterdir() if d.is_dir()],
        key=_milestone_sort_key,
    )

    # 1. Cost calibration: cycles, tokens, duration.
    cycles = _safe_int(header.get("cycles", "0"))
    tokens_out = _safe_int(header.get("tokens_out", "0"))
    duration = header.get("duration", "unknown")
    _reinforce_or_create(
        patterns,
        name="cycle-count-distribution",
        type_="cost-calibration",
        milestone=milestone,
        description=(
            f"{cycles} cycles, ~{tokens_out:,} output tokens, {duration}."
        ),
    )

    # 2. Decomposition topology from compose.py AST.
    if phase_count > 0:
        topology = "parallel (gather)" if has_gather else "sequential"
        _reinforce_or_create(
            patterns,
            name="decomposition-topology",
            type_="decomposition",
            milestone=milestone,
            description=(
                f"{phase_count} phases, {topology} execution."
            ),
        )

    # 3. Escalation patterns from metrics header.
    validation_failures = _safe_int(header.get("validation_failures", "0"))
    crash_retries = _safe_int(header.get("crash_retries", "0"))
    if validation_failures > 0 or crash_retries > 0:
        _reinforce_or_create(
            patterns,
            name="validation-noise",
            type_="debt",
            milestone=milestone,
            description=(
                f"{validation_failures} validation failures, "
                f"{crash_retries} crash retries."
            ),
        )

    # 4. Rework -- read from ## Rework section (DB-18 telemetry extension).
    rework_count = _count_metrics_section_rows(metrics_content, "## Rework")
    if rework_count > 0:
        _reinforce_or_create(
            patterns,
            name="rework-frequency",
            type_="escalation",
            milestone=milestone,
            description=f"{rework_count} rework cycle(s) triggered.",
        )

    # 5. Quality gate -- read from ## Quality Gate section (DB-18).
    qg_rows = _count_metrics_section_rows(metrics_content, "## Quality Gate")
    if qg_rows > 0:
        unavail_count = _count_qg_unavailable(metrics_content)
        if unavail_count > 0:
            _reinforce_or_create(
                patterns,
                name="quality-gate-availability",
                type_="quality-gate",
                milestone=milestone,
                description=(
                    f"Quality gate had {unavail_count} unavailable tool(s) "
                    f"across {qg_rows} invocation(s)."
                ),
            )

    # 6. Escalation -- read from ## Escalations section (DB-18).
    esc_rows = _count_metrics_section_rows(metrics_content, "## Escalations")
    if esc_rows > 0:
        _reinforce_or_create(
            patterns,
            name="escalation-frequency",
            type_="escalation",
            milestone=milestone,
            description=f"{esc_rows} escalation(s) created.",
        )

    # 7. Outcome pattern.
    outcome = header.get("outcome", "unknown")
    if outcome.startswith("escalated"):
        _reinforce_or_create(
            patterns,
            name="escalation-outcome",
            type_="escalation",
            milestone=milestone,
            description=f"Milestone ended with outcome: {outcome}.",
        )

    _apply_decay(patterns, milestone, all_milestones)

    memory_path.write_text(_render_memory(patterns), encoding="utf-8")
    _log.info("Consolidated %r into memory.md (%d patterns)", milestone, len(patterns))

    return True
