"""Structured telemetry — two layers for two audiences.

Layer 2 (JSONL span log):
    Detailed event stream for external agents and post-hoc debugging.
    Self-describing records: session → milestone → cycle → agent.
    Output: .clou/telemetry/{session_id}.jsonl

Layer 1 (golden context summary):
    Agent-readable metrics written into golden context at milestone
    completion.  The supervisor reads this to inform future planning.
    Output: .clou/milestones/{milestone}/metrics.md

Public API:
    init(session_id, project_dir) -> SpanLog
    span(name, **attrs)           -> context manager yielding mutable dict
    event(name, **attrs)          -> None
    read_log(path)                -> list[dict]
    write_milestone_summary(project_dir, milestone, outcome) -> None
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generator

from clou.graph import compute_topology

_log_mod = logging.getLogger(__name__)


class SpanLog:
    """Append-only JSONL span log for one session."""

    __slots__ = ("_path", "_epoch")

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep telemetry out of git — git_commit_phase does `git add -A`.
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n!.gitignore\n")
        self._epoch = time.monotonic()

    @property
    def path(self) -> Path:
        return self._path

    def _elapsed(self) -> float:
        """Seconds since session start (monotonic)."""
        return time.monotonic() - self._epoch

    def _emit(self, record: dict[str, Any]) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            _log_mod.warning("telemetry write failed: %s", self._path, exc_info=True)

    @contextmanager
    def span(
        self, name: str, **attrs: Any
    ) -> Generator[dict[str, Any], None, None]:
        """Timed span — mutate the yielded dict to enrich before close."""
        t0 = self._elapsed()
        record: dict[str, Any] = {
            "span": name,
            "wall": datetime.now(UTC).isoformat(),
            "t0_s": round(t0, 3),
            **attrs,
        }
        try:
            yield record
        finally:
            record["duration_ms"] = round((self._elapsed() - t0) * 1000)
            self._emit(record)

    def event(self, name: str, **attrs: Any) -> None:
        """Zero-duration point event."""
        self._emit({
            "event": name,
            "wall": datetime.now(UTC).isoformat(),
            "t_s": round(self._elapsed(), 3),
            **attrs,
        })


# ---------------------------------------------------------------------------
# Module-level singleton — no-ops when uninitialised
# ---------------------------------------------------------------------------

_log: SpanLog | None = None


def init(session_id: str, project_dir: Path) -> SpanLog:
    """Initialise the global span log for a session."""
    global _log
    path = project_dir / ".clou" / "telemetry" / f"{session_id}.jsonl"
    _log = SpanLog(path)
    _log.event("session.start", session_id=session_id)
    return _log


@contextmanager
def span(name: str, **attrs: Any) -> Generator[dict[str, Any], None, None]:
    """Module-level span — no-op dict if telemetry not initialised."""
    if _log is not None:
        with _log.span(name, **attrs) as record:
            yield record
    else:
        yield {}


def event(name: str, **attrs: Any) -> None:
    """Module-level event — no-op if telemetry not initialised."""
    if _log is not None:
        _log.event(name, **attrs)


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def read_log(path: Path) -> list[dict[str, Any]]:
    """Read all records from a telemetry JSONL file."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ---------------------------------------------------------------------------
# Per-task data extraction
# ---------------------------------------------------------------------------


def extract_task_data(
    records: list[dict[str, Any]],
    milestone: str,
) -> list[dict[str, Any]]:
    """Extract per-task execution data from telemetry records.

    Returns list of dicts, each with keys:
        task_name: str — agent description (matched to DAG task name)
        task_id: str — SDK task identifier
        cycle_num: int — which cycle the task ran in
        duration_s: float — wall-clock seconds (from t_s timestamps)
        tokens: int — total tokens consumed
        tool_uses: int — number of tool invocations
        status: str — "completed", "failed", "orphaned"

    Ordered by: cycle_num, then task_name alphabetically.
    """
    # Partition agent.start and agent.end events for this milestone.
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.get("milestone") != milestone:
            continue
        tid = r.get("task_id")
        if tid is None:
            continue
        if r.get("event") == "agent.start":
            starts[tid] = r
        elif r.get("event") == "agent.end":
            ends[tid] = r

    results: list[dict[str, Any]] = []

    # Process all known task_ids (union of starts and ends).
    all_task_ids = set(starts) | set(ends)
    for tid in all_task_ids:
        start = starts.get(tid)
        end = ends.get(tid)

        if start and end:
            # Normal case: both start and end exist.
            t_start = start.get("t_s", 0.0)
            t_end = end.get("t_s", 0.0)
            duration_s = round(t_end - t_start, 3)
            if duration_s < 0:
                duration_s = 0.0
            results.append({
                "task_name": start.get("description", tid),
                "task_id": tid,
                "cycle_num": start.get("cycle_num", 0),
                "duration_s": duration_s,
                "tokens": end.get("total_tokens", 0),
                "tool_uses": end.get("tool_uses", 0),
                "status": end.get("status", "completed"),
            })
        elif start and not end:
            # Orphaned: start without end.
            results.append({
                "task_name": start.get("description", tid),
                "task_id": tid,
                "cycle_num": start.get("cycle_num", 0),
                "duration_s": 0.0,
                "tokens": 0,
                "tool_uses": 0,
                "status": "orphaned",
            })
        else:
            # End without start (unusual but handle gracefully).
            assert end is not None
            results.append({
                "task_name": tid,
                "task_id": tid,
                "cycle_num": end.get("cycle_num", 0),
                "duration_s": 0.0,
                "tokens": end.get("total_tokens", 0),
                "tool_uses": end.get("tool_uses", 0),
                "status": end.get("status", "completed"),
            })

    # Sort by (cycle_num, task_name) for deterministic output.
    results.sort(key=lambda d: (d["cycle_num"], d["task_name"]))
    return results


# ---------------------------------------------------------------------------
# Layer 1 — golden context summary
# ---------------------------------------------------------------------------


def _fmt_duration(ms: int) -> str:
    """Format milliseconds as human-readable duration."""
    s = ms / 1000
    if s < 60:
        return f"{s:.0f}s"
    m = int(s // 60)
    sec = int(s % 60)
    return f"{m}m {sec:02d}s"


def write_milestone_summary(
    project_dir: Path,
    milestone: str,
    outcome: str,
) -> None:
    """Aggregate JSONL spans into an agent-readable metrics.md.

    Reads the current session's span log, filters for *milestone*,
    and writes a structured summary to golden context.  The supervisor
    reads this file when planning future milestones.
    """
    if _log is None:
        return

    records = read_log(_log.path)

    # Partition records for this milestone.
    cycles = [
        r for r in records
        if r.get("span") == "cycle" and r.get("milestone") == milestone
    ]
    agent_starts: dict[str, dict[str, Any]] = {
        r["task_id"]: r for r in records
        if r.get("event") == "agent.start" and r.get("milestone") == milestone
    }
    agent_ends: dict[str, dict[str, Any]] = {
        r["task_id"]: r for r in records
        if r.get("event") == "agent.end" and r.get("milestone") == milestone
    }
    incidents = [
        r for r in records
        if r.get("milestone") == milestone
        and r.get("event") in (
            "crash", "agent_crash", "context_exhausted", "validation_failure",
        )
    ]

    # Aggregates.
    total_duration_ms = sum(c.get("duration_ms", 0) for c in cycles)
    total_in = sum(c.get("input_tokens", 0) for c in cycles)
    total_out = sum(c.get("output_tokens", 0) for c in cycles)
    agents_completed = sum(
        1 for ae in agent_ends.values() if ae.get("status") == "completed"
    )
    agents_failed = sum(
        1 for ae in agent_ends.values() if ae.get("status") != "completed"
    )
    orphaned = set(agent_starts) - set(agent_ends)

    # -- Header (key: value — matches checkpoint format) --
    lines: list[str] = [
        f"# Metrics: {milestone}\n",
        f"outcome: {outcome}",
        f"cycles: {len(cycles)}",
        f"duration: {_fmt_duration(total_duration_ms)}",
        f"tokens_in: {total_in}",
        f"tokens_out: {total_out}",
        f"agents_spawned: {len(agent_starts)}",
        f"agents_completed: {agents_completed}",
        f"agents_failed: {agents_failed + len(orphaned)}",
        f"crash_retries: {sum(1 for i in incidents if i['event'] == 'crash')}",
        f"validation_failures: {sum(1 for i in incidents if i['event'] == 'validation_failure')}",
        f"context_exhaustions: {sum(1 for i in incidents if i['event'] == 'context_exhausted')}",
    ]

    # -- Cycle table --
    if cycles:
        lines.extend([
            "",
            "## Cycles",
            "",
            "| # | Type | Duration | Tokens In | Tokens Out | Outcome |",
            "|---|------|----------|-----------|------------|---------|",
        ])
        for c in cycles:
            lines.append(
                f"| {c.get('cycle_num', '?')} "
                f"| {c.get('cycle_type', '?')} "
                f"| {_fmt_duration(c.get('duration_ms', 0))} "
                f"| {c.get('input_tokens', 0):,} "
                f"| {c.get('output_tokens', 0):,} "
                f"| {c.get('outcome', '?')} |"
            )

    # -- Agent table --
    all_task_ids = list(agent_ends) + sorted(orphaned)
    if all_task_ids:
        lines.extend([
            "",
            "## Agents",
            "",
            "| Description | Cycle | Status | Tokens | Tools |",
            "|-------------|-------|--------|--------|-------|",
        ])
        for task_id in all_task_ids:
            start = agent_starts.get(task_id, {})
            desc = start.get("description", task_id)[:40].replace("|", "/").replace("\n", " ")
            if task_id in agent_ends:
                ae = agent_ends[task_id]
                lines.append(
                    f"| {desc} "
                    f"| {ae.get('cycle_num', '?')} "
                    f"| {ae.get('status', '?')} "
                    f"| {ae.get('total_tokens', 0):,} "
                    f"| {ae.get('tool_uses', 0)} |"
                )
            else:
                lines.append(
                    f"| {desc} "
                    f"| {start.get('cycle_num', '?')} "
                    f"| orphaned "
                    f"| — "
                    f"| — |"
                )

    # -- Topology section --
    compose_path = (
        project_dir / ".clou" / "milestones" / milestone / "compose.py"
    )
    topo: dict[str, Any] | None = None
    if compose_path.exists():
        try:
            compose_source = compose_path.read_text(encoding="utf-8")
            topo = compute_topology(compose_source)
        except Exception:
            _log_mod.warning(
                "topology computation failed for %s", milestone, exc_info=True,
            )

    if topo is not None and topo.get("depth", 0) > 0:
        lines.extend([
            "",
            "## Topology",
            "",
            f"width: {topo['width']}",
            f"depth: {topo['depth']}",
            f"layer_count: {topo['layer_count']}",
            f"gather_groups: {json.dumps(topo['gather_groups'])}",
            f"layers: {json.dumps(topo['layers'])}",
        ])

    # -- Per-Task Data table --
    task_data = extract_task_data(records, milestone)
    if task_data and topo is not None and topo.get("layers"):
        # Build task_name -> layer mapping from topology layers.
        task_layer: dict[str, int] = {}
        for layer_idx, layer_names in enumerate(topo["layers"]):
            for name in layer_names:
                task_layer[name] = layer_idx

        # Enrich task data with layer info and re-sort by (layer, task_name).
        enriched: list[dict[str, Any]] = []
        for td in task_data:
            layer = task_layer.get(td["task_name"], None)
            enriched.append({**td, "layer": layer})

        enriched.sort(
            key=lambda d: (
                d["layer"] if d["layer"] is not None else float("inf"),
                d["task_name"],
            ),
        )

        lines.extend([
            "",
            "## Per-Task Data",
            "",
            "| Layer | Task | Duration | Tokens | Tools | Status |",
            "|-------|------|----------|--------|-------|--------|",
        ])
        for td in enriched:
            layer_str = str(td["layer"]) if td["layer"] is not None else "\u2014"
            dur_ms = int(td["duration_s"] * 1000)
            lines.append(
                f"| {layer_str} "
                f"| {td['task_name']} "
                f"| {_fmt_duration(dur_ms)} "
                f"| {td['tokens']:,} "
                f"| {td['tool_uses']} "
                f"| {td['status']} |"
            )
    elif task_data:
        # No topology but task data exists: emit without layer info.
        lines.extend([
            "",
            "## Per-Task Data",
            "",
            "| Layer | Task | Duration | Tokens | Tools | Status |",
            "|-------|------|----------|--------|-------|--------|",
        ])
        for td in task_data:
            dur_ms = int(td["duration_s"] * 1000)
            lines.append(
                f"| \u2014 "
                f"| {td['task_name']} "
                f"| {_fmt_duration(dur_ms)} "
                f"| {td['tokens']:,} "
                f"| {td['tool_uses']} "
                f"| {td['status']} |"
            )

    # -- Quality gate results (DB-18 telemetry extension) --
    qg_events = [
        r for r in records
        if r.get("event") == "quality_gate.result"
        and r.get("milestone") == milestone
    ]
    if qg_events:
        lines.extend([
            "",
            "## Quality Gate",
            "",
            "| Cycle | Tools Invoked | Tools Unavailable | Tool Count |",
            "|-------|---------------|-------------------|------------|",
        ])
        for qg in qg_events:
            invoked = ", ".join(qg.get("tools_invoked", []))
            unavail = ", ".join(qg.get("tools_unavailable", []))
            lines.append(
                f"| {qg.get('cycle_num', '?')} "
                f"| {invoked or 'none'} "
                f"| {unavail or 'none'} "
                f"| {qg.get('finding_count', 0)} |"
            )

    # -- Rework events (DB-18 telemetry extension) --
    rework_events = [
        r for r in records
        if r.get("event") == "cycle.rework"
        and r.get("milestone") == milestone
    ]
    if rework_events:
        lines.extend([
            "",
            "## Rework",
            "",
            "| Cycle | From | To | Phase |",
            "|-------|------|----|-------|",
        ])
        for rw in rework_events:
            lines.append(
                f"| {rw.get('cycle_num', '?')} "
                f"| {rw.get('from_step', '?')} "
                f"| {rw.get('to_step', '?')} "
                f"| {rw.get('phase', '?')} |"
            )

    # -- Escalation summary (DB-18 telemetry extension) --
    esc_events = [
        r for r in records
        if r.get("event") == "escalation.created"
        and r.get("milestone") == milestone
    ]
    if esc_events:
        lines.extend([
            "",
            "## Escalations",
            "",
            "| Cycle | Classification | Severity |",
            "|-------|----------------|----------|",
        ])
        for esc in esc_events:
            lines.append(
                f"| {esc.get('cycle_num', '?')} "
                f"| {esc.get('classification', '?')} "
                f"| {esc.get('severity', '?')} |"
            )

    # -- Incidents --
    if incidents:
        lines.extend(["", "## Incidents", ""])
        for inc in incidents:
            ev = inc.get("event", "?")
            cn = inc.get("cycle_num", "?")
            detail = ""
            if ev == "crash":
                detail = f"attempt {inc.get('attempt', '?')}"
            elif ev == "validation_failure":
                detail = (
                    f"attempt {inc.get('attempt', '?')}, "
                    f"{inc.get('error_count', '?')} errors"
                )
            suffix = f" ({detail})" if detail else ""
            lines.append(f"- Cycle {cn}: {ev}{suffix}")

    content = "\n".join(lines) + "\n"
    metrics_path = (
        project_dir / ".clou" / "milestones" / milestone / "metrics.md"
    )
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(content, encoding="utf-8")
