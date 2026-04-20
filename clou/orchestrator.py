"""Clou orchestrator — supervisor session lifecycle management.

Entry point for the Clou system. Manages the supervisor session via the
Claude Agent SDK. The coordinator cycle engine lives in ``clou.coordinator``.

Telemetry integration:
    The orchestrator instruments the supervisor lifecycle via
    ``clou.telemetry``.  The coordinator's milestone-level telemetry
    (cycle spans, agent events, milestone summaries) is in
    ``clou.coordinator``.

Public API:
    main() -> None
"""

from __future__ import annotations

import asyncio
import logging
import re as _re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clou.ui.app import ClouApp

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolPermissionContext,
    create_sdk_mcp_server,
    tool,
)

from clou.coordinator import run_coordinator
from clou.graph import (
    parse_roadmap_annotations,
    validate_roadmap_annotations,
    compute_independent_sets,
)
from clou.recovery import validate_milestone_name
from clou.tokens import (
    MODEL,
    ContextPressure,
    track as _track,
    tracker as _tracker,
)
from clou.harness import (
    HarnessTemplate,
    load_template,
    read_template_name,
)
from clou.hooks import (
    build_hooks,
    is_cleanup_allowed,
    supervisor_cleanup_allowed,
    to_sdk_hooks,
)
from clou.prompts import load_prompt
from clou.recovery import parse_obsolete_flags, run_lifecycle_pipeline
from clou.gate import UserGate
from clou.tools import clou_create_milestone, clou_init, clou_spawn_coordinator, clou_status
from clou.ui.bridge import _strip_ansi
from clou import telemetry

log = logging.getLogger("clou")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_name(name: str, *, max_len: int = 80) -> str:
    """Sanitize a milestone name for safe embedding in error messages.

    Strips control characters and newlines, truncates to *max_len*.
    Prevents prompt-injection via malicious milestone names (F15).
    """
    cleaned = _re.sub(r"[\x00-\x1f\x7f]", "", name)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "..."
    return cleaned


def cleanup_obsolete_files(project_dir: Path, milestone: str) -> list[str]:
    """Delete files flagged as obsolete in a milestone's handoff.md.

    Parses the milestone's handoff.md for ``Obsolete:`` flags (via
    :func:`parse_obsolete_flags`), checks each against the orchestrator's
    cleanup scope (via :func:`is_cleanup_allowed`), and deletes permitted
    files that exist.

    Called from both ``_run_single_coordinator`` (real-time, after completion)
    and ``run_lifecycle_pipeline`` Stage 6 (startup catchup).

    Returns list of deleted file paths (relative to project_dir).
    """
    clou_dir = project_dir / ".clou"
    handoff_path = clou_dir / "milestones" / milestone / "handoff.md"
    flagged = parse_obsolete_flags(handoff_path)
    if not flagged:
        return []

    deleted: list[str] = []
    for path_str in flagged:
        # Strip `.clou/` prefix to get relative-to-.clou path for
        # permission checking.
        if path_str.startswith(".clou/"):
            relative = path_str[len(".clou/"):]
        else:
            log.warning(
                "Cleanup: flagged path %r does not start with .clou/, skipping",
                path_str,
            )
            continue

        if not is_cleanup_allowed(relative):
            log.warning(
                "Cleanup: %r not in cleanup scope, skipping", path_str,
            )
            continue

        target = clou_dir / relative
        if not target.exists():
            log.info("Cleanup: %r already absent, skipping", path_str)
            continue

        # F9: Defense-in-depth — refuse to follow symlinks.
        if target.is_symlink():
            log.warning(
                "Cleanup: %r is a symlink, skipping for safety", path_str,
            )
            continue

        try:
            target.unlink()
            deleted.append(path_str)
            log.info("Cleanup: deleted %r (flagged in %s handoff.md)", path_str, milestone)
            telemetry.event(
                "cleanup.obsolete_file_deleted",
                path=path_str,
                milestone=milestone,
            )
        except OSError:
            log.warning(
                "Cleanup: failed to delete %r", path_str, exc_info=True,
            )

    return deleted


# Keep _to_sdk_hooks as a thin re-export for backwards compatibility
# with tests that import it from here.
_to_sdk_hooks = to_sdk_hooks


def _display(msg: object) -> None:
    """Write a message to stdout for the user."""
    if isinstance(msg, ResultMessage):
        return
    if hasattr(msg, "content"):
        content = msg.content
        if isinstance(content, str):
            sys.stdout.write(_strip_ansi(content))
            sys.stdout.flush()
        elif isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    sys.stdout.write(_strip_ansi(block.text))
                    sys.stdout.flush()


# ---------------------------------------------------------------------------
# MCP tools for the supervisor
# ---------------------------------------------------------------------------


async def _run_single_coordinator(
    project_dir: Path,
    milestone: str,
    app: ClouApp | None = None,
) -> tuple[str, str]:
    """Run a single coordinator and emit UI events.

    Returns ``(milestone, result)`` where *result* is the coordinator's
    exit string (``"completed"``, ``"error"``, ``"escalated_*"``, etc.).
    """
    if app is not None:
        from clou.ui.messages import ClouCoordinatorSpawned

        app.post_message(ClouCoordinatorSpawned(milestone=milestone))

    try:
        result = await run_coordinator(project_dir, milestone, app=app)
    except Exception:
        result = "error"
        log.exception("Coordinator crashed for %r", milestone)

    log.info("Coordinator for %r finished: %s", milestone, result)

    # Post-completion cleanup: delete files flagged as obsolete in handoff.md.
    if result == "completed":
        try:
            cleanup_obsolete_files(project_dir, milestone)
        except Exception:
            log.warning(
                "Cleanup failed for %r", milestone, exc_info=True,
            )

    if result == "completed" and app is not None:
        handoff_path = (
            project_dir / ".clou" / "milestones" / milestone / "handoff.md"
        )
        if handoff_path.exists():
            from clou.ui.messages import ClouHandoff

            app.post_message(
                ClouHandoff(milestone=milestone, handoff_path=handoff_path)
            )

    if app is not None:
        from clou.ui.messages import ClouCoordinatorComplete

        app.post_message(
            ClouCoordinatorComplete(
                milestone=milestone,
                result=result,
            )
        )
    return milestone, result


def _build_milestone_guidance(project_dir: Path, milestone: str, result: str) -> str:
    """Build guidance text for a single milestone result."""
    ms_dir = project_dir / ".clou" / "milestones" / milestone
    if result in ("paused", "stopped"):
        return (
            f"Coordinator for '{milestone}' {result}. "
            f"The user's message is in the input queue --- "
            f"address it, then call clou_spawn_coordinator "
            f"to resume. The coordinator will continue from "
            f"its checkpoint."
        )
    if result.startswith("escalated"):
        return (
            f"Coordinator for '{milestone}' {result}. "
            f"Read {ms_dir / 'escalations'} for diagnosis. "
            f"To retry: modify golden context, then call "
            f"clou_spawn_coordinator. Cycle count resets "
            f"after escalation resolution."
        )
    return (
        f"Coordinator for '{milestone}' {result}. "
        f"Read {ms_dir / 'handoff.md'}, "
        f"{ms_dir / 'decisions.md'}, "
        f"{ms_dir / 'status.md'}, "
        f"and {ms_dir / 'metrics.md'} for results."
    )


def _build_mcp_server(
    project_dir: Path,
    app: ClouApp | None = None,
    gate: UserGate | None = None,
) -> Any:
    """Build the in-process MCP server with Clou tools."""

    @tool(
        "clou_spawn_coordinator",
        "Spawn a coordinator session for a milestone. "
        "The coordinator will run its full cycle autonomously.",
        {"milestone": str},
    )
    async def spawn_coordinator_tool(args: dict[str, Any]) -> dict[str, Any]:
        milestone = args["milestone"]
        validate_milestone_name(milestone)
        await clou_spawn_coordinator(project_dir, milestone)
        log.info("Spawning coordinator for milestone %r", milestone)

        _ms, result = await _run_single_coordinator(
            project_dir, milestone, app=app,
        )
        guidance = _build_milestone_guidance(project_dir, milestone, result)
        return {"content": [{"type": "text", "text": guidance}]}

    @tool(
        "clou_spawn_parallel_coordinators",
        "Spawn multiple independent coordinators concurrently. "
        "Validates independence via roadmap.md annotations before dispatch. "
        "Each coordinator runs its full cycle autonomously. If one fails, "
        "siblings continue. Returns per-milestone results.",
        {
            "type": "object",
            "properties": {
                "milestones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 5,
                    "description": "List of milestone names to dispatch concurrently.",
                },
            },
            "required": ["milestones"],
        },
    )
    async def spawn_parallel_coordinators_tool(
        args: dict[str, Any],
    ) -> dict[str, Any]:
        milestones: list[str] = args.get("milestones", [])
        if not milestones:
            return {
                "content": [{"type": "text", "text": "No milestones provided."}],
                "is_error": True,
            }

        # Dedup while preserving order (F3: prevent duplicate dispatches).
        milestones = list(dict.fromkeys(milestones))

        # Runtime cap on concurrent fan-out (F9).
        _MAX_PARALLEL = 5
        if len(milestones) > _MAX_PARALLEL:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Too many milestones ({len(milestones)}). "
                        f"Maximum {_MAX_PARALLEL} concurrent dispatches allowed."
                    ),
                }],
                "is_error": True,
            }

        # Validate milestone names.
        for ms in milestones:
            validate_milestone_name(ms)

        # Single milestone: dispatch serially (no parallelism needed).
        if len(milestones) == 1:
            ms = milestones[0]
            await clou_spawn_coordinator(project_dir, ms)
            log.info("Spawning coordinator for milestone %r (single)", ms)
            _ms, result = await _run_single_coordinator(
                project_dir, ms, app=app,
            )
            guidance = _build_milestone_guidance(project_dir, ms, result)
            return {"content": [{"type": "text", "text": guidance}]}

        # Read and parse roadmap.md for independence validation.
        roadmap_path = project_dir / ".clou" / "roadmap.md"
        if not roadmap_path.exists():
            # No roadmap: fall back to serial dispatch.
            log.warning("No roadmap.md found; dispatching milestones serially")
            return await _serial_fallback(
                project_dir, milestones, app,
                reason="No roadmap.md found",
            )

        roadmap_text = roadmap_path.read_text()
        graph = parse_roadmap_annotations(roadmap_text)
        validation_errors = validate_roadmap_annotations(graph)

        # If validation fails, fall back to serial dispatch.
        blocking_errors = [
            e for e in validation_errors if e.severity == "error"
        ]
        if blocking_errors:
            error_detail = "; ".join(str(e) for e in blocking_errors)
            log.warning(
                "Roadmap validation errors: %s; falling back to serial",
                error_detail,
            )
            # Sanitize error detail before embedding in LLM-facing
            # reason string (F15: prevent prompt injection via
            # malicious milestone names in roadmap.md).
            safe_detail = _sanitize_name(error_detail, max_len=200)
            return await _serial_fallback(
                project_dir, milestones, app,
                reason=f"Validation errors: {safe_detail}",
            )

        # Pairwise independence check (F10): verify that the specific
        # requested milestones declare independence from each other,
        # not just that some unrelated milestone has an annotation.
        if len(milestones) >= 2:
            missing_pairs: list[tuple[str, str]] = []
            for i in range(len(milestones)):
                for j in range(i + 1, len(milestones)):
                    a, b = milestones[i], milestones[j]
                    entry_a = graph.milestones.get(a)
                    entry_b = graph.milestones.get(b)
                    # At least one direction of independence must be declared.
                    a_declares_b = entry_a is not None and b in entry_a.independent_of
                    b_declares_a = entry_b is not None and a in entry_b.independent_of
                    if not (a_declares_b or b_declares_a):
                        missing_pairs.append((a, b))
            if missing_pairs:
                pair_strs = [f"({a}, {b})" for a, b in missing_pairs]
                log.info(
                    "No pairwise independence for %s; serial fallback",
                    pair_strs,
                )
                # F12: Use topological order for serial fallback.
                topo_sets = compute_independent_sets(graph)
                topo_lookup: dict[str, int] = {}
                for li, layer in enumerate(topo_sets):
                    for n in layer:
                        topo_lookup[n] = li
                topo_milestones = sorted(
                    milestones,
                    key=lambda m: topo_lookup.get(m, 0),
                )
                return await _serial_fallback(
                    project_dir, topo_milestones, app,
                    reason=(
                        "No pairwise independence annotations for: "
                        + ", ".join(pair_strs)
                    ),
                )

        # Compute independent sets and verify requested milestones
        # are in the same independent layer.
        independent_sets = compute_independent_sets(graph)

        # Build a lookup: milestone -> layer index.
        ms_to_layer: dict[str, int] = {}
        for layer_idx, layer in enumerate(independent_sets):
            for ms_name in layer:
                ms_to_layer[ms_name] = layer_idx

        # Check that all requested milestones are known and independent.
        requested = set(milestones)
        unknown = requested - set(ms_to_layer)
        if unknown:
            log.warning(
                "Unknown milestones %s not in roadmap; serial fallback",
                unknown,
            )
            safe_unknown = [_sanitize_name(u) for u in sorted(unknown)]
            return await _serial_fallback(
                project_dir, milestones, app,
                reason=f"Unknown milestones: {safe_unknown}",
            )

        # All requested milestones must be in the same layer.
        layers_used = {ms_to_layer[ms] for ms in milestones}
        if len(layers_used) > 1:
            log.warning(
                "Milestones span multiple layers (%s); serial fallback",
                layers_used,
            )
            # F12: Sort by topological layer order so dependencies
            # run before dependents in the serial fallback.
            topo_ordered = sorted(
                milestones, key=lambda m: ms_to_layer[m],
            )
            return await _serial_fallback(
                project_dir, topo_ordered, app,
                reason=(
                    "Milestones span different dependency layers "
                    "and cannot be dispatched concurrently"
                ),
            )

        # Validate milestone directories exist.
        for ms in milestones:
            await clou_spawn_coordinator(project_dir, ms)

        log.info(
            "Dispatching %d coordinators concurrently: %s",
            len(milestones), milestones,
        )
        telemetry.event(
            "parallel_dispatch.start",
            milestone_count=len(milestones),
            milestones=milestones,
        )

        # Concurrent dispatch with failure isolation.
        results = await asyncio.gather(
            *(
                _run_single_coordinator(project_dir, ms, app=app)
                for ms in milestones
            ),
            return_exceptions=True,
        )

        # Build combined guidance.
        guidance_parts: list[str] = []
        for i, outcome in enumerate(results):
            ms = milestones[i]
            if isinstance(outcome, BaseException):
                log.error(
                    "Coordinator for %r raised: %s", ms, outcome,
                    exc_info=outcome,
                )
                guidance_parts.append(
                    f"[{ms}] ERROR: coordinator failed"
                )
                # Post UI error event.
                if app is not None:
                    from clou.ui.messages import ClouCoordinatorComplete

                    app.post_message(
                        ClouCoordinatorComplete(
                            milestone=ms, result="error",
                        )
                    )
            else:
                _ms_name, result_str = outcome
                guidance_parts.append(
                    f"[{ms}] {_build_milestone_guidance(project_dir, ms, result_str)}"
                )

        telemetry.event(
            "parallel_dispatch.end",
            milestone_count=len(milestones),
        )

        combined = "\n\n".join(guidance_parts)
        return {"content": [{"type": "text", "text": combined}]}

    async def _serial_fallback(
        project_dir_: Path,
        milestones_: list[str],
        app_: ClouApp | None,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Dispatch milestones one at a time (serial fallback)."""
        guidance_parts: list[str] = [f"Serial fallback: {reason}"]
        for ms in milestones_:
            try:
                await clou_spawn_coordinator(project_dir_, ms)
            except ValueError as exc:
                guidance_parts.append(f"[{ms}] ERROR: {exc}")
                continue
            log.info("Spawning coordinator for milestone %r (serial)", ms)
            _ms, result = await _run_single_coordinator(
                project_dir_, ms, app=app_,
            )
            guidance_parts.append(
                f"[{ms}] {_build_milestone_guidance(project_dir_, ms, result)}"
            )
        combined = "\n\n".join(guidance_parts)
        return {"content": [{"type": "text", "text": combined}]}

    @tool(
        "clou_status",
        "Get current Clou status: active milestones, open escalations.",
        {},
    )
    async def status_tool(args: dict[str, Any]) -> dict[str, Any]:
        text = await clou_status(project_dir)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "clou_init",
        "Initialize .clou/ directory structure for a new project. "
        "Call this after converging with the user on what to build.",
        {"project_name": str, "description": str},
    )
    async def init_tool(args: dict[str, Any]) -> dict[str, Any]:
        text = await clou_init(
            project_dir, args["project_name"], args.get("description", "")
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "clou_create_milestone",
        "Create a new milestone directory with milestone.md, intents.md, and requirements.md. "
        "Call this after converging with the user, before spawning a coordinator. "
        "intents_content holds observable outcomes: 'When [trigger], [observable outcome].' "
        "requirements_content holds implementation constraints.",
        {
            "milestone": str,
            "milestone_content": str,
            "intents_content": str,
            "requirements_content": str,
        },
    )
    async def create_milestone_tool(args: dict[str, Any]) -> dict[str, Any]:
        milestone = args["milestone"]
        validate_milestone_name(milestone)
        text = await clou_create_milestone(
            project_dir,
            milestone,
            args["milestone_content"],
            args["requirements_content"],
            args.get("intents_content", ""),
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "clou_remove_artifact",
        "Remove an intermediate artifact from .clou/. Use for orphaned "
        "worker execution files, superseded brutalist assessments, "
        "stale escalations, or obsolete verifier artifacts from aborted "
        "or superseded runs. Protocol artifacts (milestone.md, "
        "intents.md, requirements.md, compose.py, phase.md, status.md, "
        "handoff.md, decisions.md, and root-level golden context) are "
        "immutable — attempts to remove them are rejected. Every "
        "removal is recorded (INFO log always; telemetry when the "
        "telemetry pipeline is healthy).",
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path relative to .clou/ "
                        "(e.g., 'milestones/foo/phases/bar/execution.md'). "
                        "A leading '.clou/' is tolerated but stripped. "
                        "Must resolve inside .clou/; traversal via .. "
                        "is rejected."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Rationale for removal (free-form, up to 2048 "
                        "characters). Recorded to the INFO log and, "
                        "when available, to telemetry. Required — "
                        "empty or whitespace-only reasons are rejected."
                    ),
                },
            },
            "required": ["path", "reason"],
        },
    )
    async def remove_artifact_tool(args: dict[str, Any]) -> dict[str, Any]:
        # Cap reason at a modest size — free-form but bounded to keep
        # telemetry/logs from absorbing context-window dumps.
        _MAX_REASON_LEN = 2048

        path_arg = args.get("path", "")
        reason = args.get("reason", "")

        if not isinstance(path_arg, str) or not path_arg:
            return {
                "content": [{
                    "type": "text",
                    "text": "path must be a non-empty string",
                }],
                "is_error": True,
            }
        if not isinstance(reason, str) or not reason.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "reason must be a non-empty string — describe "
                        "why this artifact is being removed"
                    ),
                }],
                "is_error": True,
            }
        if len(reason) > _MAX_REASON_LEN:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"reason exceeds {_MAX_REASON_LEN} characters "
                        f"(got {len(reason)}); summarize the rationale"
                    ),
                }],
                "is_error": True,
            }

        clou_dir = (project_dir / ".clou").resolve()
        # Tolerate a leading '.clou/' so the LLM's copy-paste habits don't
        # cause a false rejection; the canonical form is relative-to-.clou.
        rel = path_arg
        if rel.startswith(".clou/"):
            rel = rel[len(".clou/"):]

        # Check symlink-ness on the REQUESTED path, before resolve() —
        # Path.resolve() dereferences symlinks, so is_symlink() on the
        # resolved target is always False and would be dead code here.
        requested = clou_dir / rel
        if requested.is_symlink():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Refusing to follow symlink: .clou/{rel}"
                    ),
                }],
                "is_error": True,
            }

        try:
            target = requested.resolve()
            relative = target.relative_to(clou_dir)
        except (ValueError, OSError):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Path resolves outside .clou/: {path_arg!r}"
                    ),
                }],
                "is_error": True,
            }

        rel_str = relative.as_posix()

        if not supervisor_cleanup_allowed(rel_str):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Path {rel_str!r} is not in supervisor "
                        "cleanup scope. Protocol artifacts "
                        "(milestone.md, intents.md, requirements.md, "
                        "compose.py, phase.md, status.md, handoff.md, "
                        "decisions.md, root-level golden context) are "
                        "immutable."
                    ),
                }],
                "is_error": True,
            }

        if not target.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": f"File not found: .clou/{rel_str}",
                }],
                "is_error": True,
            }
        if not target.is_file():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Not a regular file: .clou/{rel_str} "
                        "(directories are out of scope for V1)"
                    ),
                }],
                "is_error": True,
            }

        try:
            size = target.stat().st_size
            target.unlink()
        except OSError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Failed to remove .clou/{rel_str}: {exc}"
                    ),
                }],
                "is_error": True,
            }

        # Audit trail is best-effort: log at INFO regardless, attempt
        # telemetry, and surface a warning if telemetry drops the event
        # so the failure is at least visible in logs.
        log.info(
            "Supervisor removed .clou/%s (%d bytes): %s",
            rel_str, size, reason,
        )
        try:
            telemetry.event(
                "supervisor.artifact_removed",
                path=rel_str,
                reason=reason,
                bytes=size,
            )
        except Exception:
            log.warning(
                "Telemetry emission failed for artifact removal "
                "(path=%s); log remains the audit of record",
                rel_str,
                exc_info=True,
            )

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Removed .clou/{rel_str} ({size} bytes). "
                    "Reason recorded."
                ),
            }],
        }

    @tool(
        "ask_user",
        "Ask the user a question. The question text and choices are "
        "displayed by the tool — do NOT write the question in your "
        "text output.  You may output reasoning or context before "
        "calling this tool, but the question itself goes here. "
        "An open-ended option is auto-appended to choices. "
        "Returns the user's answer as plain text.",
        {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 concrete answer options.",
                },
            },
            "required": ["question"],
        },
    )
    async def ask_user_tool(args: dict[str, Any]) -> dict[str, Any]:
        if gate is None:
            return {
                "content": [{"type": "text", "text": "No gate available."}],
                "is_error": True,
            }
        question: str = args.get("question", "")
        raw_choices: list[str] | None = args.get("choices")
        full_choices: list[str] | None = None
        if raw_choices is not None:
            full_choices = [*raw_choices, "Something else \u2014 I'll type my answer"]
        gate.open(question=question, choices=full_choices)
        telemetry.event(
            "dialogue.gate_opened",
            question_length=len(question),
            choice_count=len(full_choices) if full_choices else 0,
            source="ask_user_mcp",
        )
        answer = await gate.wait()
        telemetry.event("dialogue.gate_responded", answer_length=len(answer))
        return {"content": [{"type": "text", "text": answer}]}

    return create_sdk_mcp_server(
        "clou",
        tools=[
            spawn_coordinator_tool,
            spawn_parallel_coordinators_tool,
            status_tool,
            init_tool,
            create_milestone_tool,
            remove_artifact_tool,
            ask_user_tool,
        ],
    )


# ---------------------------------------------------------------------------
# Supervisor session
# ---------------------------------------------------------------------------


async def run_supervisor(
    project_dir: Path,
    app: ClouApp | None = None,
) -> None:
    """Start and manage the supervisor session."""
    clou_dir = project_dir / ".clou"

    # Full lifecycle pipeline at startup (DB-18): consolidation + decay,
    # episodic archival, and decisions compaction. Ensures operational
    # memory is consistent before the supervisor reads memory.md at
    # orient. Handles bootstrap, crash recovery, and code deployment
    # across existing projects.
    if clou_dir.exists():
        try:
            telemetry.event("startup.lifecycle_pipeline.start")
            consolidated = await run_lifecycle_pipeline(project_dir)
            telemetry.event("startup.lifecycle_pipeline.end",
                            consolidated=len(consolidated) if consolidated else 0)
            if consolidated:
                log.info(
                    "Lifecycle pipeline: consolidated %d milestone(s)",
                    len(consolidated),
                )
        except Exception:
            telemetry.event("startup.lifecycle_pipeline.error")
            log.warning("Lifecycle pipeline failed", exc_info=True)

    try:
        telemetry.event("startup.mcp_server.start")
        user_gate = UserGate()
        clou_server = _build_mcp_server(project_dir, app=app, gate=user_gate)
        telemetry.event("startup.mcp_server.end")
    except Exception:
        telemetry.event("startup.mcp_server.error")
        raise

    # Load harness template for MCP servers and hook permissions.
    try:
        telemetry.event("startup.hooks.start")
        tmpl_name = read_template_name(project_dir)
        tmpl = load_template(tmpl_name)
        hooks = _to_sdk_hooks(build_hooks("supervisor", project_dir, template=tmpl))
        telemetry.event("startup.hooks.end")
    except Exception:
        telemetry.event("startup.hooks.error")
        raise

    # Supervisor gets clou MCP server only — no quality gate servers.
    # Quality gates are invoked by the brutalist during ASSESS cycles,
    # not by the supervisor.  Keeping the tool set small prevents
    # Claude Code from deferring critical tools like ask_user behind
    # ToolSearch.
    mcp_dict: dict[str, Any] = {"clou": clou_server}

    # The SDK session's working directory is where the user invoked clou,
    # not necessarily where .clou/ lives (which may be the global workspace).
    work_dir = getattr(app, "_work_dir", project_dir) if app else project_dir

    # All tools auto-approved.  User questions go through the MCP
    # ask_user tool (gate-based), not the built-in AskUserQuestion.
    async def _can_use_tool(
        name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        return PermissionResultAllow()

    # Restrict to the tools the supervisor actually uses.
    # ask_user is the sole user-question tool — eagerly loaded via
    # allowed_tools so it never hides behind ToolSearch.
    options = ClaudeAgentOptions(
        system_prompt=load_prompt("supervisor", project_dir),
        cwd=str(work_dir),
        model=MODEL,
        hooks=hooks,
        mcp_servers=mcp_dict,
        can_use_tool=_can_use_tool,
        allowed_tools=[
            "Read", "Write", "Edit", "Glob", "Grep", "Bash",
            "mcp__clou__ask_user",
            "mcp__clou__clou_spawn_coordinator",
            "mcp__clou__clou_spawn_parallel_coordinators",
            "mcp__clou__clou_status",
            "mcp__clou__clou_init",
            "mcp__clou__clou_create_milestone",
            "mcp__clou__clou_remove_artifact",
        ],
    )

    telemetry.event("startup.sdk_client.start")
    async with ClaudeSDKClient(options=options) as supervisor:
        telemetry.event("startup.sdk_client.connected")
        # Protocol files live in the bundled _prompts/ dir (global, not per-project).
        from clou.prompts import _BUNDLED_PROMPTS

        protocol_file = str(_BUNDLED_PROMPTS / "supervisor.md")

        # Golden context references must be absolute paths because the SDK
        # cwd is work_dir (where the user invoked clou), which may differ
        # from project_dir (where .clou/ lives — e.g. ~/.config/clou/).
        checkpoint = clou_dir / "active" / "supervisor.md"
        project_md = clou_dir / "project.md"
        roadmap_md = clou_dir / "roadmap.md"

        # Feed user input concurrently — start BEFORE initial queries so
        # messages typed while the supervisor initialises aren't stuck in
        # the queue.  The task blocks on _user_input_queue.get() until
        # input arrives, then calls supervisor.query() which serialises
        # with the SDK stream.
        _input_dead = asyncio.Event()

        _dialogue_turns_in_flight = 0  # queries sent but no ResultMessage yet
        _block_checkpoint_sent = False  # guard against re-triggering BLOCK

        async def _feed_user_input() -> None:
            nonlocal _dialogue_turns_in_flight
            assert app is not None
            while True:
                # Check for compact request alongside user input.
                compact_wait = asyncio.ensure_future(app._compact.requested.wait())
                input_wait = asyncio.ensure_future(app._user_input_ready.wait())
                done, pending = await asyncio.wait(
                    {compact_wait, input_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()

                try:
                    if compact_wait in done:
                        app._compact.requested.clear()
                        instructions = app._compact.instructions or (
                            "Summarize the conversation so far. Preserve: "
                            "(1) the action chain (user requests → your "
                            "decisions → coordinator outcomes), "
                            "(2) key decisions and their rationale, "
                            "(3) current project state and open tasks. "
                            "Compress tool outputs to one-line summaries. "
                            "Compress coordinator spawn results older than "
                            "3 cycles to their exit status and key decisions."
                        )
                        compact_prompt = (
                            f"[SYSTEM: Context compaction requested. {instructions}. "
                            f"After summarizing, re-read {project_md} and "
                            f"{roadmap_md} to refresh your golden context.]"
                        )
                        _tracker.add_supervisor_delta(compact_prompt)
                        await supervisor.query(compact_prompt)
                        # Don't signal _compact_complete yet — the supervisor
                        # hasn't actually processed the compaction.  The
                        # receive_messages loop signals completion when the
                        # supervisor's ResultMessage arrives.
                        # Skip any ResultMessages for queries that were already
                        # in flight before the compact was sent.
                        app._compact.results_to_skip = _dialogue_turns_in_flight
                        app._compact.pending = True
                        _dialogue_turns_in_flight += 1

                    if input_wait in done:
                        # Pop from the front of the deque (FIFO).
                        if not app._user_input_queue:
                            app._user_input_ready.clear()
                            continue
                        text = app._user_input_queue.popleft()
                        if not app._user_input_queue:
                            app._user_input_ready.clear()

                        from contextlib import suppress

                        from clou.ui.messages import ClouProcessingStarted
                        from clou.ui.widgets.conversation import ConversationWidget

                        if user_gate.is_open:
                            # Route to the waiting MCP tool instead of
                            # injecting a new supervisor message.
                            user_gate.respond(text)
                        else:
                            # Deliver to the model FIRST — only then tell
                            # the UI the message was picked up.
                            _tracker.add_supervisor_delta(text)
                            await supervisor.query(text)
                            _dialogue_turns_in_flight += 1
                            telemetry.event(
                                "dialogue.user_input",
                                length=len(text),
                            )

                        with suppress(LookupError):
                            app.query_one(ConversationWidget).post_message(
                                ClouProcessingStarted(text=text)
                            )
                except Exception:
                    log.exception("_feed_user_input: query() failed")
                    # Surface the error so the user knows the session is broken.
                    from contextlib import suppress

                    from clou.ui.widgets.conversation import ConversationWidget

                    with suppress(LookupError):
                        conv = app.query_one(ConversationWidget)
                        conv.add_error_message(
                            "Lost connection to model — input may not be delivered."
                        )
                        conv.reset_turn_state()
                    # Drain the queue so stale messages don't pile up.
                    app._user_input_queue.clear()
                    app._user_input_ready.clear()
                    app._queue_count = 0
                    with suppress(LookupError):
                        app.query_one(ConversationWidget).update_queue_count(0)
                    # Signal the receive_messages loop to stop — otherwise the
                    # worker stays alive ("zombie") accepting input into a dead
                    # queue that nobody will ever read.
                    _input_dead.set()
                    return

        input_task: asyncio.Task[None] | None = None
        if app is not None:
            input_task = asyncio.create_task(_feed_user_input())

        # Check for session resumption.
        resume_id = getattr(app, "_resume_session_id", None) if app else None
        if resume_id:
            from clou.resume import build_resumption_context

            resume_ctx = build_resumption_context(project_dir, resume_id)
            if resume_ctx:
                await supervisor.query(resume_ctx)
            else:
                # Fallback: resume_id was set but transcript is empty/missing.
                log.warning("Session %s could not be restored", resume_id)
                if app is not None:
                    from contextlib import suppress

                    from clou.ui.widgets.conversation import ConversationWidget

                    with suppress(LookupError):
                        app.query_one(ConversationWidget).add_error_message(
                            f"Could not restore session {resume_id}. Starting fresh."
                        )
                await supervisor.query(
                    f"Read your protocol file: {protocol_file}\n\n"
                    f"Then read {project_md} for context. "
                    "The user's session could not be restored. "
                    "Greet them and orient from the project context."
                )
        else:
            # Normal startup — adapt based on whether project has golden context.
            if checkpoint.exists():
                await supervisor.query(
                    f"Read your protocol file: {protocol_file}\n\n"
                    f"Then resume from checkpoint. Read {checkpoint} "
                    f"and {roadmap_md} to reconstruct your state."
                )
            elif project_md.exists():
                await supervisor.query(
                    f"Read your protocol file: {protocol_file}\n\n"
                    f"Then read {project_md} and {roadmap_md} for context.\n\n"
                    "The user has an existing project. Orient yourself "
                    "from the golden context and greet them."
                )
            else:
                # No .clou/ — but there may be existing code in the directory.
                has_existing_code = any(
                    work_dir.glob(p)
                    for p in ("*.py", "*.ts", "*.js", "*.go", "*.rs",
                              "package.json", "pyproject.toml", "Cargo.toml",
                              "go.mod", "Makefile", "src/", "lib/")
                )
                if has_existing_code:
                    await supervisor.query(
                        f"Read your protocol file: {protocol_file}\n\n"
                        "This is the first time Clou is running in this "
                        "directory, but there is existing code here. "
                        "Greet the user and let them tell you what they "
                        "want to do with their project. They may want to "
                        "add a feature, fix a bug, refactor, or something "
                        "else. Follow the convergence protocol: listen, "
                        "then propose, then refine."
                    )
                else:
                    await supervisor.query(
                        f"Read your protocol file: {protocol_file}\n\n"
                        "This is a greenfield project — no code and no "
                        ".clou/ directory exist yet. Greet the user and "
                        "let them tell you what they're thinking. Follow "
                        "the convergence protocol: listen, then propose, "
                        "then refine. Do not start with questions."
                    )

        # Resolve post target — post to ConversationWidget so handlers
        # fire there AND bubble up to the app for status bar updates.
        # Only meaningful when app is present (the app-is-None path calls
        # _display() directly in the receive loop below).
        _post: Any = None
        if app is not None:
            from contextlib import suppress

            from clou.ui.widgets.conversation import ConversationWidget

            _post = app.post_message
            with suppress(LookupError):
                _post = app.query_one(ConversationWidget).post_message

        try:
            async for msg in supervisor.receive_messages():
                if app is not None:
                    from clou.ui.bridge import route_supervisor_message

                    route_supervisor_message(msg, _post)
                else:
                    _display(msg)
                _track(msg, tier="supervisor")
                if isinstance(msg, ResultMessage):
                    _dialogue_turns_in_flight = max(
                        _dialogue_turns_in_flight - 1, 0,
                    )
                    telemetry.event(
                        "dialogue.supervisor_response",
                        input_tokens=msg.usage.get("input_tokens", 0) if isinstance(msg.usage, dict) else (msg.usage.input_tokens if msg.usage else 0),
                        output_tokens=msg.usage.get("output_tokens", 0) if isinstance(msg.usage, dict) else (msg.usage.output_tokens if msg.usage else 0),
                    )
                    # Signal compact completion when the supervisor actually
                    # finishes processing the compaction prompt (not when it
                    # was merely written to stdin).  Skip ResultMessages that
                    # belong to queries sent before the compact.
                    if app is not None and app._compact.pending:
                        if app._compact.results_to_skip > 0:
                            app._compact.results_to_skip -= 1
                        else:
                            app._compact.pending = False
                            app._compact.count += 1
                            app._compact.complete.set()
                            telemetry.event(
                                "dialogue.compact",
                                compaction_num=app._compact.count,
                            )

                    # --- Graduated context pressure ------------------
                    _pressure = _tracker.supervisor_pressure()
                    if _pressure == ContextPressure.BLOCK and not _block_checkpoint_sent:
                        log.critical(
                            "Supervisor context at BLOCK threshold (%d tokens) "
                            "— forcing checkpoint",
                            _tracker.supervisor_context_estimate(),
                        )
                        telemetry.event(
                            "context.block",
                            estimate=_tracker.supervisor_context_estimate(),
                        )
                        # Notify UI of critical pressure.
                        if app is not None and _post is not None:
                            from clou.tokens import BLOCK_THRESHOLD
                            from clou.ui.messages import ClouContextPressure

                            _post(ClouContextPressure(
                                level="block",
                                estimate=_tracker.supervisor_context_estimate(),
                                threshold=BLOCK_THRESHOLD,
                            ))
                        # Force the supervisor to write a checkpoint.
                        _block_checkpoint_sent = True
                        await supervisor.query(
                            "[SYSTEM: Context window critically full. "
                            "Write your current state to "
                            f"{clou_dir / 'active' / 'supervisor.md'} "
                            "immediately as a checkpoint, then stop.]"
                        )
                        _dialogue_turns_in_flight += 1
                    elif (
                        _pressure == ContextPressure.COMPACT
                        and app is not None
                        and not app._compact.pending
                    ):
                        log.warning(
                            "Supervisor context at COMPACT threshold (%d tokens) "
                            "— auto-triggering compaction",
                            _tracker.supervisor_context_estimate(),
                        )
                        telemetry.event(
                            "context.auto_compact",
                            estimate=_tracker.supervisor_context_estimate(),
                        )
                        # Notify UI of compact-level pressure.
                        if _post is not None:
                            from clou.tokens import COMPACT_THRESHOLD
                            from clou.ui.messages import ClouContextPressure

                            _post(ClouContextPressure(
                                level="compact",
                                estimate=_tracker.supervisor_context_estimate(),
                                threshold=COMPACT_THRESHOLD,
                            ))
                        # Reuse the existing compact mechanism.
                        app._compact.instructions = (
                            "Context approaching limit. Summarize the "
                            "conversation, preserving: (1) the action chain "
                            "(user requests → your decisions → coordinator "
                            "outcomes), (2) key decisions and their rationale, "
                            "(3) current project state and open tasks. "
                            "Compress tool outputs and old coordinator spawn "
                            "results to one-line summaries. "
                            "After summarizing, re-read "
                            f"{project_md} and {roadmap_md} "
                            "to refresh your golden context."
                        )
                        app._compact.requested.set()
                    elif _pressure == ContextPressure.WARN:
                        telemetry.event(
                            "context.warn",
                            estimate=_tracker.supervisor_context_estimate(),
                        )
                        if app is not None and _post is not None:
                            from clou.tokens import WARN_THRESHOLD
                            from clou.ui.messages import ClouContextPressure

                            _post(ClouContextPressure(
                                level="warn",
                                estimate=_tracker.supervisor_context_estimate(),
                                threshold=WARN_THRESHOLD,
                            ))
                # If the input feeder died (e.g. broken pipe), stop
                # consuming — the session is unrecoverable.
                if _input_dead.is_set():
                    log.warning("Input feeder dead — stopping supervisor")
                    break
        finally:
            if input_task is not None:
                input_task.cancel()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Clou entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from clou.project import resolve_project_dir_or_exit

    work_dir = Path.cwd()
    project_dir = resolve_project_dir_or_exit()
    clou_dir = project_dir / ".clou"

    if not clou_dir.exists():
        await clou_init(project_dir, project_dir.name, "")

    from clou.ui.app import ClouApp

    app = ClouApp(project_dir=project_dir, work_dir=work_dir)
    await app.run_async()


def cli() -> None:
    """CLI entry point for ``python -m clou``."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
