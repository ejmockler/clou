"""Clou orchestrator — session lifecycle management.

Entry point for the Clou system. Manages supervisor and coordinator
sessions via the Claude Agent SDK. The orchestrator is invisible
plumbing — the user interacts with the supervisor session.

Telemetry integration:
    The orchestrator instruments the milestone lifecycle via
    ``clou.telemetry``.  Milestone start/end events, per-cycle spans
    (with token deltas from ``clou.tokens.TokenTracker``), agent
    lifecycle events, and failure events are emitted into the JSONL
    span log.  At milestone completion, ``write_milestone_summary``
    aggregates the span log into ``.clou/milestones/<name>/metrics.md``
    (golden context, Narrative validation tier — DB-12).

Public API:
    main() -> None
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from clou.ui.app import ClouApp

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SandboxSettings,
    TaskNotificationMessage,
    ToolPermissionContext,
    create_sdk_mcp_server,
    tool,
)

from clou.harness import (
    HarnessTemplate,
    load_template,
    read_template_name,
    template_mcp_servers,
)
from clou.hooks import HookConfig, build_hooks
from clou.prompts import build_cycle_prompt, load_prompt
from clou.recovery import (
    attempt_self_heal,
    determine_next_cycle,
    git_commit_phase,
    git_revert_golden_context,
    log_self_heal_attempt,
    parse_checkpoint,
    read_cycle_count,
    read_cycle_outcome,
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_staleness_escalation,
    write_validation_escalation,
)
from clou.gate import UserGate
from clou.tokens import TokenTracker
from clou.tools import clou_create_milestone, clou_init, clou_spawn_coordinator, clou_status
from clou.ui.bridge import _strip_ansi
from clou.validation import (
    ValidationFinding,
    errors_only,
    validate_delivery,
    validate_golden_context,
    validate_readiness,
    warnings_only,
)
from clou import telemetry

log = logging.getLogger("clou")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CYCLES = 20
_NEXT_STEP: dict[str, str] = {
    "PLAN": "EXECUTE",
    "EXECUTE": "ASSESS",
    "ASSESS": "VERIFY",
    "VERIFY": "EXIT",
    "EXIT": "COMPLETE",
}
_MAX_VALIDATION_RETRIES = 3
_MAX_CRASH_RETRIES = 3
_STALENESS_THRESHOLD = 3
_MAX_BUDGET_USD: float | None = None  # No per-cycle cost cap by default
_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

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
_cumulative_cost_usd: dict[str, float] = {}  # milestone → cumulative USD


def _track(msg: object, tier: str = "supervisor", milestone: str | None = None) -> None:
    """Extract and track token usage and cost from an SDK message."""
    if isinstance(msg, ResultMessage) and msg.usage:
        _tracker.track(msg.usage, tier=tier, milestone=milestone)
    if isinstance(msg, ResultMessage) and milestone:
        cost = getattr(msg, "total_cost_usd", None)
        if cost is not None:
            _cumulative_cost_usd[milestone] = (
                _cumulative_cost_usd.get(milestone, 0.0) + cost
            )


def _context_exhausted(msg: object) -> bool:
    """Check if a message indicates context exhaustion."""
    if isinstance(msg, ResultMessage) and msg.usage:
        return _tracker.is_context_exhausted(msg.usage)
    return False


_ENV_PROBE_MAX_LINES: int = 20


async def _capture_working_tree_state(project_dir: Path) -> str | None:
    """Capture git diff --stat for the working tree (truncated).

    Returns the diff stat output (max ``_ENV_PROBE_MAX_LINES`` lines),
    or None if clean or error.  Used to make partial work from failed
    cycles visible to the next cycle's coordinator (DB-15 D6:
    describe the environment, don't try to control it).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode(errors="replace").strip()
        if not output:
            return None
        lines = output.splitlines()
        if len(lines) > _ENV_PROBE_MAX_LINES:
            shown = "\n".join(lines[:_ENV_PROBE_MAX_LINES])
            return f"{shown}\n... and {len(lines) - _ENV_PROBE_MAX_LINES} more files"
        return output
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Agent team definitions
# ---------------------------------------------------------------------------


def _build_agents(
    project_dir: Path,
    milestone: str,
    template: HarnessTemplate | None = None,
) -> dict[str, AgentDefinition]:
    """Build AgentDefinition dict for coordinator's agent teams.

    When *template* is provided, agent definitions are derived from the
    template's ``agents`` dict.  Otherwise falls back to the default
    software-construction template.
    """
    if template is None:
        template = load_template("software-construction")

    return {
        name: AgentDefinition(
            description=spec.description,
            prompt=load_prompt(spec.prompt_ref, project_dir, milestone=milestone),
            tools=spec.tools,
            model=spec.model,
        )
        for name, spec in template.agents.items()
    }


# ---------------------------------------------------------------------------
# MCP tools for the supervisor
# ---------------------------------------------------------------------------


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
        ms_dir = project_dir / ".clou" / "milestones" / milestone
        if result in ("paused", "stopped"):
            guidance = (
                f"Coordinator for '{milestone}' {result}. "
                f"The user's message is in the input queue — "
                f"address it, then call clou_spawn_coordinator "
                f"to resume. The coordinator will continue from "
                f"its checkpoint."
            )
        elif result.startswith("escalated"):
            guidance = (
                f"Coordinator for '{milestone}' {result}. "
                f"Read {ms_dir / 'escalations'} for diagnosis. "
                f"To retry: modify golden context, then call "
                f"clou_spawn_coordinator. Cycle count resets "
                f"after escalation resolution."
            )
        else:
            guidance = (
                f"Coordinator for '{milestone}' {result}. "
                f"Read {ms_dir / 'handoff.md'}, "
                f"{ms_dir / 'decisions.md'}, "
                f"{ms_dir / 'status.md'}, "
                f"and {ms_dir / 'metrics.md'} for results."
            )
        return {"content": [{"type": "text", "text": guidance}]}

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
        "ask_user",
        "Pause and wait for the user's response. Call this after "
        "presenting questions or choices in your text output. "
        "Returns the user's answer as plain text.",
        {},
    )
    async def ask_user_tool(args: dict[str, Any]) -> dict[str, Any]:
        if gate is None:
            return {
                "content": [{"type": "text", "text": "No gate available."}],
                "is_error": True,
            }
        gate.open()
        answer = await gate.wait()
        return {"content": [{"type": "text", "text": answer}]}

    return create_sdk_mcp_server(
        "clou",
        tools=[spawn_coordinator_tool, status_tool, init_tool, create_milestone_tool, ask_user_tool],
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
    user_gate = UserGate()
    clou_server = _build_mcp_server(project_dir, app=app, gate=user_gate)

    # Load harness template for MCP servers and hook permissions.
    tmpl_name = read_template_name(project_dir)
    tmpl = load_template(tmpl_name)
    hooks = _to_sdk_hooks(build_hooks("supervisor", project_dir, template=tmpl))

    # Supervisor gets quality gate MCP servers + clou (not all template
    # servers — e.g., CDP is for the verifier, not the supervisor).
    gate_servers = {g.mcp_server for g in tmpl.quality_gates}
    all_mcp = template_mcp_servers(tmpl)
    mcp_dict: dict[str, Any] = {
        name: spec for name, spec in all_mcp.items() if name in gate_servers
    }
    mcp_dict["clou"] = clou_server

    # The SDK session's working directory is where the user invoked clou,
    # not necessarily where .clou/ lives (which may be the global workspace).
    work_dir = getattr(app, "_work_dir", project_dir) if app else project_dir

    # Block built-in AskUserQuestion — it requires the interactive
    # permission component which bypassPermissions skips.  The model
    # should use ask_user (clou MCP tool) instead.
    async def _can_use_tool(
        name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if name == "AskUserQuestion":
            return PermissionResultDeny(
                message="Use ask_user instead. It pauses for user input.",
            )
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("supervisor", project_dir),
        permission_mode="bypassPermissions",
        cwd=str(work_dir),
        model="opus",
        hooks=hooks,
        mcp_servers=mcp_dict,
        can_use_tool=_can_use_tool,
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

        # Feed user input concurrently — start BEFORE initial queries so
        # messages typed while the supervisor initialises aren't stuck in
        # the queue.  The task blocks on _user_input_queue.get() until
        # input arrives, then calls supervisor.query() which serialises
        # with the SDK stream.
        _input_dead = asyncio.Event()

        async def _feed_user_input() -> None:
            assert app is not None
            while True:
                # Check for compact request alongside user input.
                compact_wait = asyncio.ensure_future(app._compact_requested.wait())
                input_wait = asyncio.ensure_future(app._user_input_ready.wait())
                done, pending = await asyncio.wait(
                    {compact_wait, input_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()

                try:
                    if compact_wait in done:
                        app._compact_requested.clear()
                        instructions = app._compact_instructions or (
                            "Summarize the conversation so far, preserving key "
                            "decisions, code context, and open tasks."
                        )
                        await supervisor.query(
                            f"[SYSTEM: Context compaction requested. {instructions}. "
                            f"Acknowledge briefly and continue.]"
                        )
                        app._compaction_count += 1
                        app._compact_complete.set()

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
                            await supervisor.query(text)

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
                # If the input feeder died (e.g. broken pipe), stop
                # consuming — the session is unrecoverable.
                if _input_dead.is_set():
                    log.warning("Input feeder dead — stopping supervisor")
                    break
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

    # Load the active harness template once per milestone.
    tmpl_name = read_template_name(project_dir)
    tmpl = load_template(tmpl_name)
    log.info("Using harness template: %s", tmpl.name)

    clou_dir = project_dir / ".clou"
    checkpoint_path = clou_dir / "milestones" / milestone / "active" / "coordinator.md"
    # Outside .clou/active/ so git_revert_golden_context doesn't touch it.
    milestone_marker = clou_dir / ".coordinator-milestone"
    validation_retries = 0
    readiness_retries = 0
    crash_retries = 0
    pending_validation_errors: list[ValidationFinding] | None = None
    _pending_working_tree: str | None = None

    # Staleness detection state (F3).
    _prev_cycle_type: str | None = None
    _prev_phases_completed: int = -1
    _staleness_count: int = 0
    _saw_type_change: bool = False  # True if cycle type changed since last reset

    decisions_path = clou_dir / "milestones" / milestone / "decisions.md"
    seen_path = clou_dir / "active" / "seen-escalations.txt"
    seen_escalations: set[str] = set()
    if seen_path.exists():
        seen_escalations = set(seen_path.read_text().splitlines())

    # DB-15 D5: Reset cycle count if the LATEST escalation was resolved.
    # Check only the most recent escalation (sorted by timestamp filename).
    esc_dir = clou_dir / "milestones" / milestone / "escalations"
    if esc_dir.is_dir() and checkpoint_path.exists():
        esc_files = sorted(esc_dir.glob("*.md"))
        latest = esc_files[-1] if esc_files else None
        resolved = bool(
            latest
            and re.search(
                r"(?m)^status:\s*(resolved|overridden)",
                latest.read_text(encoding="utf-8"),
            )
        )
        if resolved:
            # Only reset if not already consumed (prevent replay on re-spawn).
            cp = parse_checkpoint(checkpoint_path.read_text())
            if cp.cycle > 0:
                checkpoint_path.write_text(
                    f"cycle: 0\n"
                    f"step: {cp.step}\n"
                    f"next_step: {cp.next_step}\n"
                    f"current_phase: {cp.current_phase}\n"
                    f"phases_completed: {cp.phases_completed}\n"
                    f"phases_total: {cp.phases_total}\n"
                )
            log.info(
                "Cycle count reset for %r after resolved escalation",
                milestone,
            )

    def _post_new_escalations() -> None:
        """Scan for new escalation files and post them to the UI."""
        if _active_app is None:
            return
        esc_dir = clou_dir / "milestones" / milestone / "escalations"
        if not esc_dir.is_dir():
            return
        for esc_file in sorted(esc_dir.glob("*.md")):
            if esc_file.name not in seen_escalations:
                seen_escalations.add(esc_file.name)
                seen_path.write_text(
                    "\n".join(sorted(seen_escalations)) + "\n"
                )
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

    # Clear stale checkpoint from a previous milestone.
    # Serial execution guarantees one coordinator at a time, so a
    # checkpoint belonging to a different milestone is always stale.
    if checkpoint_path.exists():
        prev = milestone_marker.read_text().strip() if milestone_marker.exists() else ""
        if prev != milestone:
            checkpoint_path.unlink()
    milestone_marker.parent.mkdir(parents=True, exist_ok=True)
    milestone_marker.write_text(milestone)

    _ms_outcome = "unknown"
    telemetry.event("milestone.start", milestone=milestone)
    try:
        while True:
            # --- Cycle-boundary checks (DB-15) ---

            # Check for /stop request.
            if (
                _active_app is not None
                and hasattr(_active_app, "_stop_requested")
                and isinstance(_active_app._stop_requested, asyncio.Event)
                and _active_app._stop_requested.is_set()
            ):
                _active_app._stop_requested.clear()
                log.info("Stop requested for %r at cycle boundary", milestone)
                _ms_outcome = "stopped"
                return "stopped"

            # Check for user messages at cycle boundary.
            # If the user typed during autonomous work, pause the coordinator
            # and let the supervisor handle it.
            if (
                _active_app is not None
                and hasattr(_active_app, "_user_input_queue")
                and isinstance(_active_app._user_input_queue, deque)
                and _active_app._user_input_queue
            ):
                from clou.ui.messages import ClouCoordinatorPaused

                cycle_count_now = read_cycle_count(checkpoint_path)
                log.info(
                    "User message pending at cycle boundary for %r "
                    "(cycle %d) — pausing coordinator",
                    milestone,
                    cycle_count_now,
                )
                _active_app.post_message(
                    ClouCoordinatorPaused(
                        cycle_num=cycle_count_now,
                        reason="user message pending",
                    )
                )
                _ms_outcome = "paused"
                return "paused"

            # Check budget (DB-15 D2a).
            if tmpl.budget_usd is not None:
                spent = _cumulative_cost_usd.get(milestone, 0.0)
                pct = spent / tmpl.budget_usd if tmpl.budget_usd > 0 else 0.0
                if pct >= 1.0:
                    log.warning(
                        "Budget exhausted for %r: $%.2f / $%.2f",
                        milestone, spent, tmpl.budget_usd,
                    )
                    _ms_outcome = "escalated_budget"
                    return "escalated_budget"
                if pct >= 0.5 and _active_app is not None:
                    from clou.ui.messages import ClouBudgetWarning

                    threshold = 75 if pct >= 0.75 else 50
                    _active_app.post_message(
                        ClouBudgetWarning(
                            spent_usd=spent,
                            budget_usd=tmpl.budget_usd,
                            pct=threshold,
                        )
                    )

            cycle_type, read_set = determine_next_cycle(
                checkpoint_path,
                milestone,
                decisions_path=decisions_path,
            )

            if cycle_type == "COMPLETE":
                log.info("Milestone %r complete", milestone)
                if seen_path.exists():
                    seen_path.unlink()
                _ms_outcome = "completed"
                return "completed"

            cycle_count = read_cycle_count(checkpoint_path)
            if cycle_count >= _MAX_CYCLES:
                log.warning("Milestone %r hit %d-cycle limit", milestone, _MAX_CYCLES)
                await write_cycle_limit_escalation(project_dir, milestone, cycle_count)
                _post_new_escalations()
                _ms_outcome = "escalated_cycle_limit"
                return "escalated_cycle_limit"

            # Staleness detection (F3): track consecutive same-type cycles
            # with no phase advancement.
            _cp_now = (
                parse_checkpoint(checkpoint_path.read_text())
                if checkpoint_path.exists()
                else None
            )
            _phases_now = _cp_now.phases_completed if _cp_now else 0
            if cycle_type != _prev_cycle_type:
                # Cycle type changed (e.g. EXECUTE→ASSESS or ASSESS→EXECUTE).
                # Track the change but don't count as staleness.
                _saw_type_change = True
                _staleness_count = 1
                _prev_cycle_type = cycle_type
                _prev_phases_completed = _phases_now
            elif _phases_now != _prev_phases_completed:
                # Phase advancement — real progress.
                _staleness_count = 1
                _saw_type_change = False
                _prev_phases_completed = _phases_now
            else:
                # Same cycle type, same phases_completed.
                # Only count as stale if we haven't seen a type change
                # (i.e. an ASSESS cycle) since the last reset.
                # EXECUTE→ASSESS→EXECUTE(rework) is progress, not staleness.
                if _saw_type_change:
                    _saw_type_change = False
                    _staleness_count = 1
                else:
                    _staleness_count += 1

            if _staleness_count >= _STALENESS_THRESHOLD:
                _cp_next = _cp_now.next_step if _cp_now else "unknown"
                log.warning(
                    "Staleness detected for %r: %s repeated %d times "
                    "with phases_completed=%d",
                    milestone, cycle_type, _staleness_count, _phases_now,
                )
                await write_staleness_escalation(
                    project_dir, milestone, cycle_type,
                    _staleness_count, _phases_now, _cp_next,
                )
                _post_new_escalations()
                _ms_outcome = "escalated_staleness"
                return "escalated_staleness"

            # Pre-cycle readiness: verify the context this cycle needs exists.
            milestone_dir = clou_dir / "milestones" / milestone
            readiness = validate_readiness(
                clou_dir, milestone_dir, read_set, cycle_type, milestone,
            )
            readiness_errors = errors_only(readiness)
            if readiness_errors:
                readiness_retries += 1
                log.warning(
                    "Readiness check failed for %r %s (attempt %d/%d): %s",
                    milestone, cycle_type,
                    readiness_retries, _MAX_VALIDATION_RETRIES,
                    [e.message for e in readiness_errors],
                )
                telemetry.event(
                    "readiness_failed", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                    error_count=len(readiness_errors),
                    attempt=readiness_retries,
                )
                if readiness_retries >= _MAX_VALIDATION_RETRIES:
                    await write_validation_escalation(
                        project_dir, milestone, readiness,
                    )
                    _post_new_escalations()
                    _ms_outcome = "escalated_validation"
                    return "escalated_validation"
                continue
            readiness_warnings = warnings_only(readiness)
            if readiness_warnings:
                log.info(
                    "Readiness warnings for %r %s (non-blocking): %s",
                    milestone, cycle_type,
                    [w.message for w in readiness_warnings],
                )

            # Extract DAG before prompt building — same data feeds UI and prompt.
            dag_data = None
            compose_path = clou_dir / "milestones" / milestone / "compose.py"
            if compose_path.exists():
                try:
                    from clou.graph import extract_dag_data

                    source = compose_path.read_text(encoding="utf-8")
                    dag_data = extract_dag_data(source)
                except Exception:
                    log.debug("Could not parse DAG from compose.py", exc_info=True)

            # For EXECUTE cycles, probe environment state even without
            # a prior failure — the agent team should see the codebase
            # as it actually is (describe-and-adapt, DB-15).
            env_state = _pending_working_tree
            if env_state is None and cycle_type == "EXECUTE":
                env_state = await _capture_working_tree_state(project_dir)

            # Extract current_phase from checkpoint for path resolution.
            _current_phase: str | None = None
            if checkpoint_path.exists():
                try:
                    _current_phase = parse_checkpoint(
                        checkpoint_path.read_text()
                    ).current_phase or None
                except Exception:
                    pass

            prompt = build_cycle_prompt(
                project_dir,
                milestone,
                cycle_type,
                read_set,
                validation_errors=pending_validation_errors,
                template=tmpl,
                dag_data=dag_data if cycle_type == "EXECUTE" else None,
                working_tree_state=env_state,
                current_phase=_current_phase,
            )
            pending_validation_errors = None  # consumed
            _pending_working_tree = None

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

                # Post DAG at cycle start too — compose.py exists from PLAN onward.
                if dag_data is not None:
                    try:
                        from clou.ui.messages import ClouDagUpdate

                        tasks, deps = dag_data
                        _active_app.post_message(ClouDagUpdate(tasks=tasks, deps=deps))
                    except Exception:
                        log.debug("Could not post DAG to UI", exc_info=True)

            _tok_before = _tracker.coordinator(milestone)
            with telemetry.span(
                "cycle", milestone=milestone, cycle_num=cycle_count + 1,
                cycle_type=cycle_type,
            ) as _cy:
                status = await _run_single_cycle(
                    project_dir, milestone, cycle_type, prompt,
                    cycle_num=cycle_count + 1, template=tmpl,
                )
                _tok_after = _tracker.coordinator(milestone)
                _cy["outcome"] = status
                _cy["input_tokens"] = _tok_after["input"] - _tok_before["input"]
                _cy["output_tokens"] = _tok_after["output"] - _tok_before["output"]

            if status == "failed":
                crash_retries += 1
                telemetry.event(
                    "crash", milestone=milestone,
                    cycle_num=cycle_count + 1, attempt=crash_retries,
                )
                log.warning(
                    "Cycle crashed for %r (attempt %d/%d), retrying",
                    milestone,
                    crash_retries,
                    _MAX_CRASH_RETRIES,
                )
                if crash_retries >= _MAX_CRASH_RETRIES:
                    log.error("Milestone %r hit crash retry limit", milestone)
                    await write_agent_crash_escalation(project_dir, milestone)
                    _post_new_escalations()
                    _ms_outcome = "escalated_crash_loop"
                    return "escalated_crash_loop"
                continue

            if status == "agent_team_crash":
                telemetry.event(
                    "agent_crash", milestone=milestone,
                    cycle_num=cycle_count + 1,
                )
                log.error("Agent team crash for %r, escalating", milestone)
                await write_agent_crash_escalation(project_dir, milestone)
                _post_new_escalations()
                _ms_outcome = "escalated_agent_crash"
                return "escalated_agent_crash"

            if status == "exhausted":
                telemetry.event(
                    "context_exhausted", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                )
                log.warning(
                    "Context exhausted for %r, continuing from checkpoint",
                    milestone,
                )
                # Exhaustion is not a crash — the agent wrote a mid-cycle
                # checkpoint.  Skip validation (golden context is partial)
                # and let determine_next_cycle route from the checkpoint.
                crash_retries = 0
                continue

            # Post-cycle delivery: verify the coordinator wrote its state.
            delivery = validate_delivery(
                clou_dir / "milestones" / milestone,
                checkpoint_path,
                milestone,
            )
            delivery_errors = errors_only(delivery)
            if delivery_errors:
                log.warning(
                    "Delivery check failed for %r: %s",
                    milestone,
                    [e.message for e in delivery_errors],
                )
                telemetry.event(
                    "delivery_failed", milestone=milestone,
                    cycle_num=cycle_count + 1, cycle_type=cycle_type,
                    error_count=len(delivery_errors),
                )

            # Validate golden context structure (content checks).
            findings = validate_golden_context(project_dir, milestone, template=tmpl)
            # Merge delivery errors into the content findings.
            findings.extend(delivery)
            validation_errors = errors_only(findings)
            validation_warnings = warnings_only(findings)

            if validation_warnings:
                log.info(
                    "Validation warnings for %r (non-blocking): %s",
                    milestone,
                    [w.message for w in validation_warnings],
                )
                telemetry.event(
                    "validation_warnings", milestone=milestone,
                    cycle_num=cycle_count + 1,
                    warning_count=len(validation_warnings),
                )

            if validation_errors:
                # Try self-heal before counting as a failure.
                healed = attempt_self_heal(
                    project_dir, milestone, validation_errors,
                )
                if healed:
                    log.info(
                        "Self-healed %d issue(s) for %r: %s",
                        len(healed), milestone, healed,
                    )
                    telemetry.event(
                        "self_heal", milestone=milestone,
                        cycle_num=cycle_count + 1,
                        fix_count=len(healed),
                    )
                    # Re-validate after heal.  Re-attach delivery findings
                    # — self-heal can't fix missing files, so delivery errors
                    # persist and must not be silently dropped.
                    findings = validate_golden_context(project_dir, milestone, template=tmpl)
                    findings.extend(delivery)
                    validation_errors = errors_only(findings)
                    validation_warnings = warnings_only(findings)
                    log_self_heal_attempt(
                        project_dir, milestone, healed, validation_errors,
                    )

                if validation_warnings and healed:
                    # Log warnings again after re-validation.
                    log.info(
                        "Validation warnings for %r after self-heal "
                        "(non-blocking): %s",
                        milestone,
                        [w.message for w in validation_warnings],
                    )

            if validation_errors:
                validation_retries += 1
                telemetry.event(
                    "validation_failure", milestone=milestone,
                    cycle_num=cycle_count + 1, attempt=validation_retries,
                    error_count=len(validation_errors),
                    warning_count=len(validation_warnings),
                )
                log.warning(
                    "Validation failed for %r (attempt %d/%d): %s",
                    milestone,
                    validation_retries,
                    _MAX_VALIDATION_RETRIES,
                    validation_errors,
                )
                if validation_retries >= _MAX_VALIDATION_RETRIES:
                    await write_validation_escalation(
                        project_dir, milestone, findings
                    )
                    _post_new_escalations()
                    _ms_outcome = "escalated_validation"
                    return "escalated_validation"
                # Capture working tree state BEFORE reverting golden context.
                # This makes partial code changes from the failed cycle
                # visible to the retry coordinator (describe-and-adapt).
                _pending_working_tree = await _capture_working_tree_state(
                    project_dir
                )
                try:
                    await git_revert_golden_context(project_dir, milestone)
                except RuntimeError:
                    log.exception("Git revert failed for %r", milestone)
                pending_validation_errors = findings
                continue
            else:
                validation_retries = 0
                readiness_retries = 0
                crash_retries = 0

            # Compact decisions.md if it's grown too large (DB-15 D3).
            if decisions_path.exists():
                from clou.recovery import compact_decisions

                if compact_decisions(decisions_path):
                    log.info("Compacted decisions.md for %r", milestone)

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
                        next_step=_NEXT_STEP.get(cycle_type, ""),
                        phase_status={},
                    )
                )

            if _active_app is not None:
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

            _post_new_escalations()

        return "completed"  # unreachable, but satisfies mypy
    finally:
        try:
            telemetry.write_milestone_summary(project_dir, milestone, _ms_outcome)
            telemetry.event("milestone.end", milestone=milestone, outcome=_ms_outcome)
        except Exception:
            log.warning("telemetry summary write failed for %r", milestone, exc_info=True)
        _active_app = None


# ---------------------------------------------------------------------------
# Single cycle execution
# ---------------------------------------------------------------------------


async def _run_single_cycle(
    project_dir: Path,
    milestone: str,
    cycle_type: str,
    prompt: str,
    *,
    cycle_num: int = 0,
    template: HarnessTemplate | None = None,
) -> str:
    """Run one coordinator cycle as a fresh session."""
    if template is None:
        template = load_template("software-construction")

    hooks = _to_sdk_hooks(
        build_hooks(
            "coordinator", project_dir, milestone=milestone, template=template,
        )
    )

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("coordinator", project_dir, milestone=milestone),
        permission_mode="bypassPermissions",
        cwd=str(project_dir),
        model="opus",
        agents=_build_agents(project_dir, milestone, template),
        hooks=hooks,
        max_budget_usd=_MAX_BUDGET_USD,
        effort="max" if cycle_type in ("ASSESS", "VERIFY") else "high",
        mcp_servers=template_mcp_servers(template),
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
                    from contextlib import suppress

                    from clou.ui.bridge import route_coordinator_message
                    from clou.ui.widgets.breath import BreathWidget

                    # Post to the BreathWidget so handlers fire there.
                    # app.post_message doesn't propagate downward in
                    # Textual — messages bubble up, not down.
                    _coord_post = _active_app.post_message
                    with suppress(LookupError):
                        _coord_post = _active_app.query_one(
                            BreathWidget
                        ).post_message

                    route_coordinator_message(
                        msg,
                        milestone,
                        cycle_type,
                        _coord_post,
                    )

                # Agent lifecycle telemetry.
                # Detection logic parallels bridge.py's duck-typing for
                # TaskStartedMessage / TaskNotificationMessage.  Kept
                # separate: bridge translates for UI, this records for
                # the telemetry span log.
                if hasattr(msg, "task_id"):
                    if (
                        hasattr(msg, "description")
                        and not hasattr(msg, "status")
                        and not hasattr(msg, "last_tool_name")
                    ):
                        telemetry.event(
                            "agent.start",
                            milestone=milestone,
                            cycle_num=cycle_num,
                            task_id=msg.task_id,
                            description=msg.description,
                        )
                    elif hasattr(msg, "status") and hasattr(msg, "summary"):
                        _au = getattr(msg, "usage", {}) or {}
                        telemetry.event(
                            "agent.end",
                            milestone=milestone,
                            cycle_num=cycle_num,
                            task_id=msg.task_id,
                            status=msg.status,
                            total_tokens=_au.get("total_tokens", 0),
                            tool_uses=_au.get("tool_uses", 0),
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

        return read_cycle_outcome(project_dir, milestone)

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
