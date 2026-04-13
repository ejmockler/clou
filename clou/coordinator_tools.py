"""MCP tools for the coordinator session — protocol artifact writers.

These tools replace freeform Write calls for protocol artifacts.
The coordinator provides values; code handles format.  This eliminates
the validation→self-heal→retry loop caused by LLM format drift.

Narrative artifacts (decisions.md, assessment.md) remain freeform.

Public API:
    build_coordinator_mcp_server(project_dir, milestone) -> MCP server
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from clou.golden_context import (
    _extract_phase_names,
    assemble_execution,
    render_checkpoint,
    render_execution_summary,
    render_execution_task,
    render_status,
    render_status_from_checkpoint,
    sanitize_phase,
)
from clou.recovery import validate_milestone_name
from clou.recovery_checkpoint import Checkpoint, parse_checkpoint

_log = logging.getLogger(__name__)


def _coerce_json_object(value: Any, *, param: str) -> dict[str, Any] | None:
    """Return *value* as a dict, JSON-parsing a string if necessary.

    The SDK's simple schema shorthand maps Python ``dict`` to JSON Schema
    ``"string"`` (claude_agent_sdk/__init__.py:288), so the LLM may send a
    JSON-encoded string.  Full JSON Schemas fix the advertisement, but we
    still accept strings defensively so one drift does not crash the tool.
    Returns None for None/empty input.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{param}: expected JSON object, got unparseable string: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"{param}: expected JSON object, got {type(parsed).__name__}"
            )
        return parsed
    raise ValueError(
        f"{param}: expected object or JSON string, got {type(value).__name__}"
    )


def _coerce_json_array(value: Any, *, param: str) -> list[Any]:
    """Return *value* as a list, JSON-parsing a string if necessary.

    Mirrors :func:`_coerce_json_object` for list-typed parameters.
    Returns an empty list for None/empty input.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{param}: expected JSON array, got unparseable string: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError(
                f"{param}: expected JSON array, got {type(parsed).__name__}"
            )
        return parsed
    raise ValueError(
        f"{param}: expected array or JSON string, got {type(value).__name__}"
    )


def _render_and_write_status(
    ms_dir: Path,
    milestone: str,
    checkpoint: Checkpoint,
    phase_names: list[str] | None = None,
) -> Path:
    """Render status.md from a Checkpoint and write it to disk.

    Returns the path written.  This is the single code path for all
    status.md writes -- checkpoint tool and status tool both call this.
    """
    content = render_status_from_checkpoint(
        milestone=milestone,
        checkpoint=checkpoint,
        phase_names=phase_names or _extract_phase_names(ms_dir),
    )
    path = ms_dir / "status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _build_coordinator_tools(
    project_dir: Path,
    milestone: str,
) -> list[Any]:
    """Build the coordinator's ``SdkMcpTool`` list (testable seam).

    Exposes the decorated tools so unit tests can invoke handlers
    directly via ``tool.handler(args)`` without routing through the
    MCP transport.
    """
    validate_milestone_name(milestone)
    ms_dir = project_dir / ".clou" / "milestones" / milestone

    @tool(
        "clou_write_checkpoint",
        "Write the coordinator checkpoint (active/coordinator.md). "
        "Use this instead of writing the file directly — the tool "
        "guarantees correct format for cycle control flow.",
        {
            "type": "object",
            "properties": {
                "cycle": {"type": "integer"},
                "step": {"type": "string"},
                "next_step": {"type": "string"},
                "current_phase": {"type": "string"},
                "phases_completed": {"type": "integer"},
                "phases_total": {"type": "integer"},
                "cycle_outcome": {
                    "type": "string",
                    "description": (
                        "Cycle outcome classification: ADVANCED (default), "
                        "INCONCLUSIVE, INTERRUPTED, or FAILED."
                    ),
                },
                "valid_findings": {
                    "type": "integer",
                    "description": (
                        "Number of valid+security findings from this ASSESS "
                        "cycle. Set to 0 when no actionable findings. "
                        "Omit for non-ASSESS cycles."
                    ),
                },
            },
            "required": [
                "cycle", "step", "next_step",
                "current_phase", "phases_completed", "phases_total",
            ],
        },
    )
    async def write_checkpoint_tool(args: dict[str, Any]) -> dict[str, Any]:
        # Construct the Checkpoint once and use it for both the
        # checkpoint file and the status.md side-effect.  This avoids
        # a phantom Checkpoint that omits retry counter fields.
        #
        # Convergence tracking: if valid_findings is provided (ASSESS
        # cycle), compute consecutive_zero_valid from the previous
        # checkpoint.  This is structured state — no markdown parsing.
        valid_findings = args.get("valid_findings", -1)
        consecutive_zero_valid = 0
        prev_cp_path = ms_dir / "active" / "coordinator.md"
        if valid_findings >= 0 and prev_cp_path.exists():
            prev_cp = parse_checkpoint(prev_cp_path.read_text())
            if valid_findings == 0:
                consecutive_zero_valid = prev_cp.consecutive_zero_valid + 1
            # else: reset to 0 (valid findings found).

        cp = Checkpoint(
            cycle=args["cycle"],
            step=args["step"],
            next_step=args["next_step"],
            current_phase=args.get("current_phase", ""),
            phases_completed=args.get("phases_completed", 0),
            phases_total=args.get("phases_total", 0),
            cycle_outcome=args.get("cycle_outcome", "ADVANCED"),
            valid_findings=valid_findings,
            consecutive_zero_valid=consecutive_zero_valid,
        )
        content = render_checkpoint(
            cycle=cp.cycle,
            step=cp.step,
            next_step=cp.next_step,
            current_phase=cp.current_phase,
            phases_completed=cp.phases_completed,
            phases_total=cp.phases_total,
            cycle_outcome=cp.cycle_outcome,
            valid_findings=cp.valid_findings,
            consecutive_zero_valid=cp.consecutive_zero_valid,
        )
        path = ms_dir / "active" / "coordinator.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        # Side-effect: derive and write status.md from the same
        # Checkpoint.  This makes status.md a render-only view.
        status_path = _render_and_write_status(ms_dir, milestone, cp)

        return {
            "written": str(path),
            "status_written": str(status_path),
            "next_step": cp.next_step,
        }

    @tool(
        "clou_update_status",
        "Re-render status.md from the current checkpoint. "
        "Parameters are accepted for backward compatibility but "
        "status.md content is derived from the checkpoint.",
        {
            "type": "object",
            "properties": {
                "phase": {"type": "string"},
                "cycle": {"type": "integer"},
                "next_step": {"type": "string"},
                "phase_progress": {
                    "type": "object",
                    "description": (
                        "Map of phase name -> status "
                        "(pending/in_progress/completed/failed). "
                        "Ignored -- derived from checkpoint."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "notes": {"type": "string"},
            },
            "required": ["phase", "cycle", "next_step", "phase_progress", "notes"],
        },
    )
    async def update_status_tool(args: dict[str, Any]) -> dict[str, Any]:
        # Read the current checkpoint and re-render status.md from it.
        # If no checkpoint exists, fall back to the args provided by
        # the coordinator (backward compatibility for first PLAN cycle
        # before any checkpoint has been written).
        checkpoint_path = ms_dir / "active" / "coordinator.md"
        if checkpoint_path.exists():
            cp = parse_checkpoint(
                checkpoint_path.read_text(encoding="utf-8")
            )
            if args.get("notes"):
                _log.info(
                    "clou_update_status: notes parameter is deprecated "
                    "(status.md is now derived from checkpoint)"
                )
            status_path = _render_and_write_status(ms_dir, milestone, cp)
        else:
            # No checkpoint yet -- fall back to direct render for the
            # initial PLAN cycle.
            phase_progress = _coerce_json_object(
                args.get("phase_progress"), param="phase_progress"
            )
            if phase_progress is not None:
                phase_progress = {
                    str(k): str(v) for k, v in phase_progress.items()
                }
            content = render_status(
                milestone=milestone,
                phase=args["phase"],
                cycle=args["cycle"],
                next_step=args.get("next_step", ""),
                phase_progress=phase_progress,
                notes=args.get("notes", ""),
            )
            status_path = ms_dir / "status.md"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(content, encoding="utf-8")
        return {"written": str(status_path)}

    @tool(
        "clou_write_execution",
        "Write a complete execution.md for a phase, including summary "
        "and task entries. Use this instead of writing execution.md "
        "directly — the tool guarantees ## Summary and ## Tasks structure.",
        {
            "type": "object",
            "properties": {
                "phase": {"type": "string"},
                "status": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "description": "List of task entries for this phase.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "status": {"type": "string"},
                            "files_changed": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "tests": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                    },
                },
                "failures": {"type": "string"},
                "blockers": {"type": "string"},
            },
            "required": ["phase", "status", "tasks", "failures", "blockers"],
        },
    )
    async def write_execution_tool(args: dict[str, Any]) -> dict[str, Any]:
        tasks_items = _coerce_json_array(args.get("tasks"), param="tasks")
        tasks_raw: list[dict[str, Any]] = []
        for i, item in enumerate(tasks_items):
            coerced = _coerce_json_object(item, param=f"tasks[{i}]")
            if coerced is None:
                # A null or empty-string item in a protocol array is
                # malformed input, not "skip this slot".  Silent drop
                # would corrupt task counts and vanish evidence.
                raise ValueError(
                    f"tasks[{i}]: expected object, got null/empty"
                )
            files_raw = coerced.get("files_changed")
            if files_raw is not None:
                coerced["files_changed"] = _coerce_json_array(
                    files_raw, param=f"tasks[{i}].files_changed"
                )
            tasks_raw.append(coerced)
        tasks_completed = sum(1 for t in tasks_raw if t.get("status") == "completed")
        tasks_failed = sum(1 for t in tasks_raw if t.get("status") == "failed")
        tasks_in_progress = sum(1 for t in tasks_raw if t.get("status") == "in_progress")

        summary = render_execution_summary(
            status=args.get("status", "in_progress"),
            tasks_total=len(tasks_raw),
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            tasks_in_progress=tasks_in_progress,
            failures=args.get("failures", "none"),
            blockers=args.get("blockers", "none"),
        )

        task_blocks = []
        for i, t in enumerate(tasks_raw, 1):
            task_blocks.append(render_execution_task(
                task_id=i,
                name=t.get("name", f"Task {i}"),
                status=t.get("status", "pending"),
                files_changed=t.get("files_changed"),
                tests=t.get("tests", ""),
                notes=t.get("notes", ""),
            ))

        content = assemble_execution(summary, task_blocks)

        phase = sanitize_phase(args["phase"])
        path = ms_dir / "phases" / phase / "execution.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"written": str(path), "task_count": len(tasks_raw)}

    return [write_checkpoint_tool, update_status_tool, write_execution_tool]


def build_coordinator_mcp_server(
    project_dir: Path,
    milestone: str,
) -> Any:
    """Build an in-process MCP server with protocol artifact tools.

    Scoped to a single milestone — all writes target that milestone's
    directory.  The coordinator session gets this alongside template
    MCP servers (e.g. brutalist).
    """
    return create_sdk_mcp_server(
        "clou_coordinator",
        tools=_build_coordinator_tools(project_dir, milestone),
    )
