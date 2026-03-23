"""Clou orchestrator — session lifecycle management.

Entry point for the Clou system. Manages supervisor and coordinator
sessions via the Claude Agent SDK. The orchestrator is invisible
plumbing — the user interacts with the supervisor session.

Public API:
    main() -> None
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from clou.ui.app import ClouApp

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SandboxSettings,
    TaskNotificationMessage,
    create_sdk_mcp_server,
    tool,
)

from clou.hooks import HookConfig, build_hooks
from clou.prompts import build_cycle_prompt, load_prompt
from clou.recovery import (
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    parse_checkpoint,
    read_cycle_count,
    read_cycle_outcome,
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_validation_escalation,
)
from clou.tokens import TokenTracker
from clou.tools import clou_init, clou_spawn_coordinator, clou_status
from clou.ui.bridge import _strip_ansi
from clou.validation import validate_golden_context

log = logging.getLogger("clou")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CYCLES = 20
_MAX_VALIDATION_RETRIES = 3
_MAX_CRASH_RETRIES = 3
_MAX_BUDGET_USD: float | None = None  # No per-cycle cost cap by default
_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_BRUTALIST_MCP: Any = {
    "command": "npx",
    "args": ["-y", "@brutalist/mcp@latest"],
    "type": "stdio",
}
_CDP_MCP: Any = {
    "command": "npx",
    "args": ["-y", "chrome-devtools-mcp@latest"],
    "type": "stdio",
}

# Module-level reference to the active Textual app.  Set by run_coordinator
# so _run_single_cycle can route messages without a signature change (which
# would break existing mocks in test_orchestrator.py).
_active_app: ClouApp | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate_milestone_name(name: str) -> None:
    """Validate milestone name (lowercase alphanumeric + hyphens)."""
    if not _MILESTONE_RE.match(name):
        msg = f"Invalid milestone name: {name!r} (must match [a-z0-9][a-z0-9-]*)"
        raise ValueError(msg)


def _to_sdk_hooks(
    hook_configs: dict[str, list[HookConfig]],
) -> Any:
    """Convert internal HookConfig to SDK HookMatcher.

    Returns a hooks dict compatible with ClaudeAgentOptions.hooks.
    Uses Any return because our HookCallback type is intentionally
    broader than the SDK's union type for testability.
    """
    return {
        event: [
            HookMatcher(matcher=cfg.matcher, hooks=cast(Any, cfg.hooks))
            for cfg in configs
        ]
        for event, configs in hook_configs.items()
    }


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
# Token tracking
# ---------------------------------------------------------------------------

_tracker = TokenTracker()


def _track(msg: object, tier: str = "supervisor", milestone: str | None = None) -> None:
    """Extract and track token usage from an SDK message."""
    if isinstance(msg, ResultMessage) and msg.usage:
        _tracker.track(msg.usage, tier=tier, milestone=milestone)


def _context_exhausted(msg: object) -> bool:
    """Check if a message indicates context exhaustion."""
    if isinstance(msg, ResultMessage) and msg.usage:
        return _tracker.is_context_exhausted(msg.usage)
    return False


# ---------------------------------------------------------------------------
# Agent team definitions
# ---------------------------------------------------------------------------


def _build_agents(project_dir: Path, milestone: str) -> dict[str, AgentDefinition]:
    """Build AgentDefinition dict for coordinator's agent teams."""
    return {
        "implementer": AgentDefinition(
            description=(
                "Implement code changes for assigned tasks. "
                "Read compose.py for your function signature, phase.md "
                "for context. Write results to execution.md."
            ),
            prompt=load_prompt("worker", project_dir, milestone=milestone),
            tools=[
                "Read",
                "Write",
                "Edit",
                "MultiEdit",
                "Bash",
                "Grep",
                "Glob",
                "WebSearch",
                "WebFetch",
            ],
            model="opus",
        ),
        "assessor": AgentDefinition(
            description=(
                "Invoke Brutalist quality gates on changed code and "
                "structure findings into assessment.md. Does not "
                "evaluate findings — captures only."
            ),
            prompt=load_prompt("assessor", project_dir, milestone=milestone),
            tools=[
                "Read",
                "Write",
                "Bash",
                "Grep",
                "Glob",
                "mcp__brutalist__roast_codebase",
                "mcp__brutalist__roast_architecture",
                "mcp__brutalist__roast_security",
                "mcp__brutalist__roast_product",
                "mcp__brutalist__roast_infrastructure",
                "mcp__brutalist__roast_file_structure",
                "mcp__brutalist__roast_dependencies",
                "mcp__brutalist__roast_test_coverage",
            ],
            model="opus",
        ),
        "verifier": AgentDefinition(
            description=(
                "Verify milestone completion by perceiving the software "
                "as a user would. Materialize the dev environment, walk "
                "golden paths, explore adversarially, prepare handoff.md."
            ),
            prompt=load_prompt("verifier", project_dir, milestone=milestone),
            tools=[
                "Read",
                "Write",
                "Bash",
                "Grep",
                "Glob",
                "WebSearch",
                "WebFetch",
                # CDP browser verification tools
                "mcp__cdp__navigate",
                "mcp__cdp__screenshot",
                "mcp__cdp__accessibility_snapshot",
                "mcp__cdp__evaluate_javascript",
                "mcp__cdp__click",
                "mcp__cdp__type",
                "mcp__cdp__network_get_response_body",
                "mcp__cdp__console_messages",
            ],
            model="opus",
        ),
    }


# ---------------------------------------------------------------------------
# MCP tools for the supervisor
# ---------------------------------------------------------------------------


def _build_mcp_server(
    project_dir: Path,
    app: ClouApp | None = None,
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

        if app is not None:
            from clou.ui.messages import ClouCoordinatorSpawned

            app.post_message(ClouCoordinatorSpawned(milestone=milestone))

        try:
            result = await run_coordinator(project_dir, milestone, app=app)
        except Exception:
            result = "error"
            log.exception("Coordinator crashed for %r", milestone)

        log.info("Coordinator for %r finished: %s", milestone, result)

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
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Coordinator for '{milestone}' {result}. "
                        f"Read {project_dir / '.clou' / 'milestones' / milestone / 'status.md'} "
                        f"and {project_dir / '.clou' / 'milestones' / milestone / 'handoff.md'} for results."
                    ),
                }
            ]
        }

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
        "Initialize .clou/ directory structure for a new project.",
        {"project_name": str, "description": str},
    )
    async def init_tool(args: dict[str, Any]) -> dict[str, Any]:
        text = await clou_init(project_dir, args["project_name"], args["description"])
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        "clou", tools=[spawn_coordinator_tool, status_tool, init_tool]
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
    clou_server = _build_mcp_server(project_dir, app=app)
    hooks = _to_sdk_hooks(build_hooks("supervisor", project_dir))

    # The SDK session's working directory is where the user invoked clou,
    # not necessarily where .clou/ lives (which may be the global workspace).
    work_dir = getattr(app, "_work_dir", project_dir) if app else project_dir

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("supervisor", project_dir),
        permission_mode="acceptEdits",
        cwd=str(work_dir),
        model="opus",
        hooks=hooks,
        mcp_servers={"clou": clou_server, "brutalist": _BRUTALIST_MCP},
    )

    async with ClaudeSDKClient(options=options) as supervisor:
        # Protocol files live in the bundled _prompts/ dir (global, not per-project).
        from clou.prompts import _BUNDLED_PROMPTS

        protocol_file = str(_BUNDLED_PROMPTS / "supervisor.md")

        # Golden context references must be absolute paths because the SDK
        # cwd is work_dir (where the user invoked clou), which may differ
        # from project_dir (where .clou/ lives — e.g. ~/.config/clou/).
        checkpoint = clou_dir / "active" / "supervisor.md"
        project_md = clou_dir / "project.md"
        roadmap_md = clou_dir / "roadmap.md"

        # Check for session resumption.
        resume_id = getattr(app, "_resume_session_id", None) if app else None
        if resume_id:
            from clou.resume import build_resumption_context

            resume_ctx = build_resumption_context(project_dir, resume_id)
            if resume_ctx:
                await supervisor.query(resume_ctx)
            else:
                # Fallback: resume_id was set but transcript is empty/missing.
                await supervisor.query(
                    f"Read your protocol file: {protocol_file}\n\n"
                    f"Then read {project_md} for context. "
                    "Greet the user and ask what they'd like to build."
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
                    f"Then read {project_md} for context. "
                    "Greet the user and ask what they'd like to build."
                )
            else:
                await supervisor.query(
                    f"Read your protocol file: {protocol_file}\n\n"
                    "Greet the user and ask what they'd like to build."
                )

        # Feed user input concurrently — receive_messages() is a
        # long-lived generator; query() injects into the same stream.
        async def _feed_user_input() -> None:
            assert app is not None
            while True:
                # Check for compact request alongside user input.
                compact_wait = asyncio.ensure_future(app._compact_requested.wait())
                input_wait = asyncio.ensure_future(app._user_input_queue.get())
                done, pending = await asyncio.wait(
                    {compact_wait, input_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()

                if compact_wait in done:
                    app._compact_requested.clear()
                    instructions = app._compact_instructions or (
                        "Summarize the conversation so far, preserving key "
                        "decisions, code context, and open tasks."
                    )
                    # Inject a compaction-focused message into the session.
                    # The SDK will process this within the existing context.
                    await supervisor.query(
                        f"[SYSTEM: Context compaction requested. {instructions}. "
                        f"Acknowledge briefly and continue.]"
                    )
                    app._compaction_count += 1
                    app._compact_complete.set()
                    continue

                if input_wait in done:
                    text = input_wait.result()
                    # Signal that this queued message is now being processed.
                    from contextlib import suppress

                    from clou.ui.messages import ClouProcessingStarted
                    from clou.ui.widgets.conversation import ConversationWidget

                    with suppress(LookupError):
                        app.query_one(ConversationWidget).post_message(
                            ClouProcessingStarted(text=text)
                        )
                    await supervisor.query(text)

        input_task: asyncio.Task[None] | None = None
        if app is not None:
            input_task = asyncio.create_task(_feed_user_input())

        # Resolve post target — post to ConversationWidget so handlers
        # fire there AND bubble up to the app for status bar updates.
        _post = app.post_message
        if app is not None:
            from contextlib import suppress

            from clou.ui.widgets.conversation import ConversationWidget

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
        finally:
            if input_task is not None:
                input_task.cancel()


# ---------------------------------------------------------------------------
# Coordinator session-per-cycle loop
# ---------------------------------------------------------------------------


async def run_coordinator(
    project_dir: Path,
    milestone: str,
    app: ClouApp | None = None,
) -> str:
    """Run a coordinator for a single milestone via session-per-cycle loop."""
    global _active_app
    _active_app = app

    validate_milestone_name(milestone)

    clou_dir = project_dir / ".clou"
    checkpoint_path = clou_dir / "active" / "coordinator.md"
    # Outside .clou/active/ so git_revert_golden_context doesn't touch it.
    milestone_marker = clou_dir / ".coordinator-milestone"
    validation_retries = 0
    crash_retries = 0
    pending_validation_errors: list[str] | None = None

    decisions_path = clou_dir / "milestones" / milestone / "decisions.md"
    seen_escalations: set[str] = set()

    # Clear stale checkpoint from a previous milestone.
    # Serial execution guarantees one coordinator at a time, so a
    # checkpoint belonging to a different milestone is always stale.
    if checkpoint_path.exists():
        prev = milestone_marker.read_text().strip() if milestone_marker.exists() else ""
        if prev != milestone:
            checkpoint_path.unlink()
    milestone_marker.parent.mkdir(parents=True, exist_ok=True)
    milestone_marker.write_text(milestone)

    try:
        while True:
            cycle_type, read_set = determine_next_cycle(
                checkpoint_path,
                milestone,
                decisions_path=decisions_path,
            )

            if cycle_type == "COMPLETE":
                log.info("Milestone %r complete", milestone)
                return "completed"

            cycle_count = read_cycle_count(checkpoint_path)
            if cycle_count >= _MAX_CYCLES:
                log.warning("Milestone %r hit %d-cycle limit", milestone, _MAX_CYCLES)
                await write_cycle_limit_escalation(project_dir, milestone, cycle_count)
                return "escalated_cycle_limit"

            prompt = build_cycle_prompt(
                project_dir,
                milestone,
                cycle_type,
                read_set,
                validation_errors=pending_validation_errors,
            )
            pending_validation_errors = None  # consumed

            log.info(
                "Milestone %r: cycle %d, type %s",
                milestone,
                cycle_count + 1,
                cycle_type,
            )

            if _active_app is not None:
                from clou.ui.messages import ClouStatusUpdate

                _active_app.post_message(
                    ClouStatusUpdate(
                        cycle_type=cycle_type,
                        cycle_num=cycle_count + 1,
                        phase="",
                    )
                )

            status = await _run_single_cycle(project_dir, milestone, cycle_type, prompt)

            if status == "failed":
                crash_retries += 1
                log.warning(
                    "Cycle crashed for %r (attempt %d/%d), retrying",
                    milestone,
                    crash_retries,
                    _MAX_CRASH_RETRIES,
                )
                if crash_retries >= _MAX_CRASH_RETRIES:
                    log.error("Milestone %r hit crash retry limit", milestone)
                    await write_agent_crash_escalation(project_dir, milestone)
                    return "escalated_crash_loop"
                continue

            if status == "agent_team_crash":
                log.error("Agent team crash for %r, escalating", milestone)
                await write_agent_crash_escalation(project_dir, milestone)
                return "escalated_agent_crash"

            if status == "exhausted":
                log.warning(
                    "Context exhausted for %r, continuing from checkpoint",
                    milestone,
                )
                # Exhaustion is not a crash — the agent wrote a mid-cycle
                # checkpoint.  Skip validation (golden context is partial)
                # and let determine_next_cycle route from the checkpoint.
                crash_retries = 0
                continue

            # Validate golden context structure
            validation_errors = validate_golden_context(project_dir, milestone)
            if validation_errors:
                validation_retries += 1
                log.warning(
                    "Validation failed for %r (attempt %d/%d): %s",
                    milestone,
                    validation_retries,
                    _MAX_VALIDATION_RETRIES,
                    validation_errors,
                )
                if validation_retries >= _MAX_VALIDATION_RETRIES:
                    await write_validation_escalation(
                        project_dir, milestone, validation_errors
                    )
                    return "escalated_validation"
                try:
                    await git_revert_golden_context(project_dir, milestone)
                except RuntimeError:
                    log.exception("Git revert failed for %r", milestone)
                pending_validation_errors = validation_errors
                continue
            else:
                validation_retries = 0
                crash_retries = 0

            # Coordinator-only git commit at phase completion
            if cycle_type == "EXECUTE" and checkpoint_path.exists():
                cp = parse_checkpoint(checkpoint_path.read_text())
                if cp.current_phase:
                    try:
                        await git_commit_phase(project_dir, milestone, cp.current_phase)
                    except RuntimeError:
                        log.warning(
                            "Git commit failed for phase %r",
                            cp.current_phase,
                        )

            if _active_app is not None:
                from clou.ui.messages import ClouCycleComplete

                _active_app.post_message(
                    ClouCycleComplete(
                        cycle_num=cycle_count + 1,
                        cycle_type=cycle_type,
                        next_step="",
                        phase_status={},
                    )
                )

            if cycle_type == "PLAN" and _active_app is not None:
                compose_path = clou_dir / "milestones" / milestone / "compose.py"
                if compose_path.exists():
                    try:
                        from clou.graph import extract_dag_data
                        from clou.ui.messages import ClouDagUpdate

                        source = compose_path.read_text(encoding="utf-8")
                        tasks, deps = extract_dag_data(source)
                        _active_app.post_message(ClouDagUpdate(tasks=tasks, deps=deps))
                    except Exception:
                        log.debug("Could not parse DAG from compose.py", exc_info=True)

            if _active_app is not None:
                esc_dir = clou_dir / "milestones" / milestone / "escalations"
                if esc_dir.is_dir():
                    for esc_file in sorted(esc_dir.glob("*.md")):
                        if esc_file.name not in seen_escalations:
                            seen_escalations.add(esc_file.name)
                            try:
                                from clou.ui.bridge import parse_escalation
                                from clou.ui.messages import ClouEscalationArrived

                                data = parse_escalation(esc_file)
                                _active_app.post_message(
                                    ClouEscalationArrived(
                                        path=esc_file,
                                        classification=data["classification"],
                                        issue=data["issue"],
                                        options=data["options"],
                                    )
                                )
                            except Exception:
                                log.debug(
                                    "Could not parse escalation %s",
                                    esc_file,
                                    exc_info=True,
                                )

        return "completed"  # unreachable, but satisfies mypy
    finally:
        _active_app = None


# ---------------------------------------------------------------------------
# Single cycle execution
# ---------------------------------------------------------------------------


async def _run_single_cycle(
    project_dir: Path,
    milestone: str,
    cycle_type: str,
    prompt: str,
) -> str:
    """Run one coordinator cycle as a fresh session."""
    hooks = _to_sdk_hooks(build_hooks("coordinator", project_dir, milestone=milestone))

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("coordinator", project_dir, milestone=milestone),
        permission_mode="bypassPermissions",
        cwd=str(project_dir),
        model="opus",
        agents=_build_agents(project_dir, milestone),
        hooks=hooks,
        max_budget_usd=_MAX_BUDGET_USD,
        effort="max" if cycle_type in ("ASSESS", "VERIFY") else "high",
        mcp_servers={"brutalist": _BRUTALIST_MCP, "cdp": _CDP_MCP},
        sandbox=SandboxSettings(
            enabled=True,
            autoAllowBashIfSandboxed=True,
            allowUnsandboxedCommands=False,
        ),
    )

    try:
        async with ClaudeSDKClient(options=options) as coordinator:
            await coordinator.query(prompt)

            async for msg in coordinator.receive_response():
                _track(msg, tier="coordinator", milestone=milestone)

                if _active_app is not None:
                    from clou.ui.bridge import route_coordinator_message

                    route_coordinator_message(
                        msg,
                        milestone,
                        cycle_type,
                        _active_app.post_message,
                    )

                if _context_exhausted(msg):
                    log.warning(
                        "Context exhaustion in %r cycle %s",
                        milestone,
                        cycle_type,
                    )
                    await coordinator.query(
                        "Context approaching limit. Write a mid-cycle "
                        "checkpoint to active/coordinator.md with partial "
                        "progress, then exit."
                    )
                    return "exhausted"

                if isinstance(msg, TaskNotificationMessage) and msg.status == "failed":
                    log.error(
                        "Agent team crash in %r: %s",
                        milestone,
                        msg.summary,
                    )
                    await coordinator.query(
                        "Agent team member crashed. Preserve all "
                        "execution.md entries. Do NOT retry. Write "
                        "checkpoint and exit."
                    )
                    return "agent_team_crash"

        return read_cycle_outcome(project_dir)

    except Exception:
        log.exception("Coordinator cycle crashed for %r", milestone)
        return "failed"


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
