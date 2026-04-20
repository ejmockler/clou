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

    @tool(
        "clou_brief_worker",
        "Construct the canonical worker briefing for a compose.py "
        "function.  The briefing contains the deterministic "
        "execution.md path computed by code — the coordinator calls "
        "this and pipes the returned text directly into the Task "
        "tool's prompt, removing any LLM-owned slug construction "
        "from worker dispatch.  Optional intent_ids append a per-"
        "intent structuring instruction; extra_reads append "
        "additional context files to the worker's read list.",
        {
            "type": "object",
            "properties": {
                "function_name": {
                    "type": "string",
                    "description": (
                        "The compose.py function name the worker will "
                        "implement.  Also the phase slug — one "
                        "function per phase directory."
                    ),
                },
                "intent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Intent IDs (e.g. I1, I3) from compose.py "
                        "docstrings.  When provided, the briefing "
                        "includes per-intent structuring guidance."
                    ),
                },
                "extra_reads": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional additional files the worker should "
                        "read, each as a path string relative to "
                        "project root (e.g. 'src/logger.ts')."
                    ),
                },
            },
            "required": ["function_name"],
        },
    )
    async def brief_worker_tool(args: dict[str, Any]) -> dict[str, Any]:
        from clou.shard import canonical_execution_path

        fn_raw = args.get("function_name")
        if not isinstance(fn_raw, str) or not fn_raw:
            return {
                "content": [{
                    "type": "text",
                    "text": "function_name must be a non-empty string",
                }],
                "is_error": True,
            }

        # sanitize_phase doubles as the function-name validator: a phase
        # directory's name is always the function name.  Drift between
        # "what the coordinator briefs" and "where workers actually land"
        # can't open up here because the tool resolves the path from the
        # validated name.
        try:
            fn = sanitize_phase(fn_raw)
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid function_name: {exc}",
                }],
                "is_error": True,
            }

        intent_ids_raw = _coerce_json_array(
            args.get("intent_ids"), param="intent_ids",
        )
        intent_ids: list[str] = []
        for i, item in enumerate(intent_ids_raw):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"intent_ids[{i}]: expected non-empty string"
                )
            intent_ids.append(item.strip())

        extra_reads_raw = _coerce_json_array(
            args.get("extra_reads"), param="extra_reads",
        )
        extra_reads: list[str] = []
        for i, item in enumerate(extra_reads_raw):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"extra_reads[{i}]: expected non-empty string"
                )
            extra_reads.append(item.strip())

        execution_rel = canonical_execution_path(fn)

        lines: list[str] = [
            f"You are implementing `{fn}` for milestone "
            f"'{milestone}', phase '{fn}'.",
            "",
            "Read your protocol file: .clou/prompts/worker.md",
            "",
            "Then read these files:",
            f"- .clou/milestones/{milestone}/compose.py — find your "
            f"function signature `{fn}`. Your criteria are in the "
            "docstring.",
            f"- .clou/milestones/{milestone}/phases/{fn}/phase.md",
            "- .clou/project.md — coding conventions",
        ]
        for extra in extra_reads:
            lines.append(f"- {extra}")
        lines += [
            "",
            "Write results to:",
            f"- .clou/milestones/{milestone}/{execution_rel}",
            "",
            "Write execution.md incrementally as you complete work.",
        ]
        if intent_ids:
            lines += [
                "",
                f"Your task addresses these intents: {', '.join(intent_ids)}",
                "Structure your execution.md with per-intent sections:",
                "## {intent_id}: {one-line description}",
                "Status: [implemented | in-progress | blocked]",
                "{details}",
            ]

        briefing = "\n".join(lines)
        return {"content": [{"type": "text", "text": briefing}]}

    @tool(
        "clou_write_assessment",
        "Write assessment.md from structured findings.  Use this "
        "instead of Write — code owns the canonical ## Summary / "
        "## Tools Invoked / ## Findings structure; you own the "
        "values.  Called by the brutalist to produce the initial "
        "assessment after a quality-gate run.  For evaluator "
        "classifications, use clou_append_classifications.",
        {
            "type": "object",
            "properties": {
                "phase_name": {
                    "type": "string",
                    "description": (
                        "Header label — for single-phase cycles pass "
                        "the phase slug; for multi-phase layers pass "
                        "a layer identifier (e.g. 'Layer 1 Cycle 2')."
                    ),
                },
                "summary": {
                    "type": "object",
                    "description": (
                        "status (completed|degraded|blocked), "
                        "tools_invoked, findings counts, "
                        "phase_evaluated.  For degraded gate runs also "
                        "pass internal_reviewers and gate_error."
                    ),
                },
                "findings": {
                    "type": "array",
                    "description": (
                        "Findings list.  Each entry: number, title, "
                        "severity (critical|major|minor), source_tool, "
                        "source_models, affected_files, finding_text, "
                        "context, optional phase for multi-phase layers."
                    ),
                },
                "tools": {
                    "type": "array",
                    "description": (
                        "Tool invocations.  Each: tool, domain, "
                        "status, optional note."
                    ),
                },
            },
            "required": ["phase_name", "summary"],
        },
    )
    async def write_assessment_tool(args: dict[str, Any]) -> dict[str, Any]:
        from clou.assessment import (
            AssessmentForm,
            AssessmentSummary,
            Finding,
            ToolInvocation,
            VALID_SEVERITIES,
            VALID_STATUSES,
            render_assessment,
        )

        phase_name = args.get("phase_name", "")
        if not isinstance(phase_name, str) or not phase_name.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "phase_name must be a non-empty string",
                }],
                "is_error": True,
            }

        summary_raw = _coerce_json_object(
            args.get("summary"), param="summary",
        )
        if summary_raw is None:
            return {
                "content": [{
                    "type": "text",
                    "text": "summary is required (status, findings counts, etc.)",
                }],
                "is_error": True,
            }

        status_raw = str(summary_raw.get("status", "")).lower().strip()
        if status_raw not in VALID_STATUSES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"summary.status must be one of "
                        f"{sorted(VALID_STATUSES)}, got {status_raw!r}"
                    ),
                }],
                "is_error": True,
            }

        def _int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        summary = AssessmentSummary(
            status=status_raw,  # type: ignore[arg-type]
            tools_invoked=_int(summary_raw.get("tools_invoked", 0)),
            findings_total=_int(summary_raw.get("findings_total", 0)),
            findings_critical=_int(
                summary_raw.get("findings_critical", 0),
            ),
            findings_major=_int(summary_raw.get("findings_major", 0)),
            findings_minor=_int(summary_raw.get("findings_minor", 0)),
            phase_evaluated=str(
                summary_raw.get("phase_evaluated", ""),
            ).strip(),
            internal_reviewers=(
                _int(summary_raw["internal_reviewers"])
                if "internal_reviewers" in summary_raw
                else None
            ),
            gate_error=(
                str(summary_raw["gate_error"]).strip()
                if "gate_error" in summary_raw
                else None
            ),
        )

        tools_raw = _coerce_json_array(args.get("tools"), param="tools")
        tools: list[ToolInvocation] = []
        for i, item in enumerate(tools_raw):
            coerced = _coerce_json_object(item, param=f"tools[{i}]")
            if coerced is None:
                continue
            tool_name = str(coerced.get("tool", "")).strip()
            if not tool_name:
                raise ValueError(
                    f"tools[{i}]: 'tool' field is required"
                )
            tools.append(ToolInvocation(
                tool=tool_name,
                domain=(
                    str(coerced["domain"]).strip()
                    if "domain" in coerced
                    else None
                ),
                status=str(coerced.get("status", "invoked")).strip(),
                note=str(coerced.get("note", "")).strip(),
            ))

        findings_raw = _coerce_json_array(
            args.get("findings"), param="findings",
        )
        findings: list[Finding] = []
        for i, item in enumerate(findings_raw):
            coerced = _coerce_json_object(item, param=f"findings[{i}]")
            if coerced is None:
                raise ValueError(
                    f"findings[{i}]: expected object, got null/empty"
                )
            severity = str(coerced.get("severity", "")).lower().strip()
            if severity not in VALID_SEVERITIES:
                raise ValueError(
                    f"findings[{i}].severity must be one of "
                    f"{sorted(VALID_SEVERITIES)}, got {severity!r}"
                )
            affected = _coerce_json_array(
                coerced.get("affected_files"),
                param=f"findings[{i}].affected_files",
            )
            source_models = _coerce_json_array(
                coerced.get("source_models"),
                param=f"findings[{i}].source_models",
            )
            phase_opt = coerced.get("phase")
            findings.append(Finding(
                number=_int(coerced.get("number", i + 1), i + 1),
                title=str(coerced.get("title", "")).strip(),
                severity=severity,  # type: ignore[arg-type]
                source_tool=str(coerced.get("source_tool", "")).strip(),
                source_models=tuple(
                    str(m).strip() for m in source_models if str(m).strip()
                ),
                affected_files=tuple(
                    str(p).strip() for p in affected if str(p).strip()
                ),
                finding_text=str(coerced.get("finding_text", "")).strip(),
                context=str(coerced.get("context", "")).strip(),
                phase=(str(phase_opt).strip() if phase_opt else None),
            ))

        form = AssessmentForm(
            phase_name=phase_name.strip(),
            summary=summary,
            tools=tuple(tools),
            findings=tuple(findings),
        )
        content = render_assessment(form)
        path = ms_dir / "assessment.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "written": str(path),
            "finding_count": len(findings),
            "status": summary.status,
        }

    @tool(
        "clou_append_classifications",
        "Append evaluator classifications to assessment.md.  Reads "
        "the existing (possibly-drifted) file, parses it into "
        "structured form, merges the classifications (last-writer-"
        "wins per finding), and re-renders to canonical form.  This "
        "is the evaluator's amendment pathway — no Write required, "
        "no drift possible.",
        {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "description": (
                        "Classifications list.  Each entry: "
                        "finding_number (int matching a Finding.number), "
                        "classification "
                        "(valid|security|architectural|noise|"
                        "next-layer|out-of-milestone|convergence), "
                        "action, reasoning."
                    ),
                },
            },
            "required": ["classifications"],
        },
    )
    async def append_classifications_tool(
        args: dict[str, Any],
    ) -> dict[str, Any]:
        from clou.assessment import (
            Classification,
            VALID_CLASSIFICATIONS,
            merge_classifications,
            parse_assessment,
            render_assessment,
        )

        path = ms_dir / "assessment.md"
        if not path.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "assessment.md does not exist — the brutalist "
                        "must run clou_write_assessment first"
                    ),
                }],
                "is_error": True,
            }

        raw = _coerce_json_array(
            args.get("classifications"), param="classifications",
        )
        if not raw:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "classifications must be a non-empty array"
                    ),
                }],
                "is_error": True,
            }

        classifications: list[Classification] = []
        for i, item in enumerate(raw):
            coerced = _coerce_json_object(
                item, param=f"classifications[{i}]",
            )
            if coerced is None:
                raise ValueError(
                    f"classifications[{i}]: expected object, got null"
                )
            kind = str(coerced.get("classification", "")).lower().strip()
            if kind not in VALID_CLASSIFICATIONS:
                raise ValueError(
                    f"classifications[{i}].classification must be one "
                    f"of {sorted(VALID_CLASSIFICATIONS)}, got {kind!r}"
                )
            try:
                number = int(coerced.get("finding_number"))
            except (TypeError, ValueError):
                raise ValueError(
                    f"classifications[{i}].finding_number must be an int"
                )
            classifications.append(Classification(
                finding_number=number,
                classification=kind,  # type: ignore[arg-type]
                action=str(coerced.get("action", "")).strip(),
                reasoning=str(coerced.get("reasoning", "")).strip(),
            ))

        existing = parse_assessment(path.read_text(encoding="utf-8"))
        merged = merge_classifications(existing, classifications)
        content = render_assessment(merged)
        path.write_text(content, encoding="utf-8")
        return {
            "written": str(path),
            "classification_count": len(merged.classifications),
        }

    return [
        write_checkpoint_tool,
        update_status_tool,
        write_execution_tool,
        brief_worker_tool,
        write_assessment_tool,
        append_classifications_tool,
    ]


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
