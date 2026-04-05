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
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from clou.golden_context import (
    assemble_execution,
    render_checkpoint,
    render_execution_summary,
    render_execution_task,
    render_status,
    sanitize_phase,
)
from clou.recovery import validate_milestone_name


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
            "cycle": int,
            "step": str,
            "next_step": str,
            "current_phase": str,
            "phases_completed": int,
            "phases_total": int,
        },
    )
    async def write_checkpoint_tool(args: dict[str, Any]) -> dict[str, Any]:
        content = render_checkpoint(
            cycle=args["cycle"],
            step=args["step"],
            next_step=args["next_step"],
            current_phase=args.get("current_phase", ""),
            phases_completed=args.get("phases_completed", 0),
            phases_total=args.get("phases_total", 0),
        )
        path = ms_dir / "active" / "coordinator.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"written": str(path), "next_step": args["next_step"]}

    @tool(
        "clou_update_status",
        "Write the milestone status file (status.md). "
        "Use this instead of writing the file directly.",
        {
            "type": "object",
            "properties": {
                "phase": {"type": "string"},
                "cycle": {"type": "integer"},
                "next_step": {"type": "string"},
                "phase_progress": {
                    "type": "object",
                    "description": (
                        "Map of phase name → status "
                        "(pending/in_progress/completed/failed)."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "notes": {"type": "string"},
            },
            "required": ["phase", "cycle", "next_step", "phase_progress", "notes"],
        },
    )
    async def update_status_tool(args: dict[str, Any]) -> dict[str, Any]:
        phase_progress = _coerce_json_object(
            args.get("phase_progress"), param="phase_progress"
        )
        if phase_progress is not None:
            phase_progress = {str(k): str(v) for k, v in phase_progress.items()}
        content = render_status(
            milestone=milestone,
            phase=args["phase"],
            cycle=args["cycle"],
            next_step=args.get("next_step", ""),
            phase_progress=phase_progress,
            notes=args.get("notes", ""),
        )
        path = ms_dir / "status.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"written": str(path)}

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
