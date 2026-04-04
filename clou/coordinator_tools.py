"""MCP tools for the coordinator session — protocol artifact writers.

These tools replace freeform Write calls for protocol artifacts.
The coordinator provides values; code handles format.  This eliminates
the validation→self-heal→retry loop caused by LLM format drift.

Narrative artifacts (decisions.md, assessment.md) remain freeform.

Public API:
    build_coordinator_mcp_server(project_dir, milestone) -> MCP server
"""

from __future__ import annotations

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


def build_coordinator_mcp_server(
    project_dir: Path,
    milestone: str,
) -> Any:
    """Build an in-process MCP server with protocol artifact tools.

    Scoped to a single milestone — all writes target that milestone's
    directory.  The coordinator session gets this alongside template
    MCP servers (e.g. brutalist).
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
            "phase": str,
            "cycle": int,
            "next_step": str,
            "phase_progress": dict,
            "notes": str,
        },
    )
    async def update_status_tool(args: dict[str, Any]) -> dict[str, Any]:
        phase_progress = args.get("phase_progress")
        if isinstance(phase_progress, dict):
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
            "phase": str,
            "status": str,
            "tasks": list,
            "failures": str,
            "blockers": str,
            "notes": str,
        },
    )
    async def write_execution_tool(args: dict[str, Any]) -> dict[str, Any]:
        tasks_raw = args.get("tasks", [])
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

    return create_sdk_mcp_server(
        "clou_coordinator",
        tools=[write_checkpoint_tool, update_status_tool, write_execution_tool],
    )
