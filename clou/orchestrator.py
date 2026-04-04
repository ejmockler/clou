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
from clou.recovery import validate_milestone_name
from clou.tokens import MODEL, track as _track
from clou.harness import (
    HarnessTemplate,
    load_template,
    read_template_name,
)
from clou.hooks import build_hooks, to_sdk_hooks
from clou.prompts import load_prompt
from clou.recovery import consolidate_pending
from clou.gate import UserGate
from clou.tools import clou_create_milestone, clou_init, clou_spawn_coordinator, clou_status
from clou.ui.bridge import _strip_ansi
from clou import telemetry

log = logging.getLogger("clou")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

    # Ensure operational memory is consistent before the supervisor
    # reads memory.md at orient (DB-18). Consolidates any completed
    # milestones not yet reflected in memory.md — handles bootstrap,
    # crash recovery, and code deployment across existing projects.
    if clou_dir.exists():
        try:
            n = consolidate_pending(project_dir)
            if n:
                log.info("Consolidated %d pending milestone(s) into memory.md", n)
        except Exception:
            log.warning("Pending consolidation failed", exc_info=True)

    user_gate = UserGate()
    clou_server = _build_mcp_server(project_dir, app=app, gate=user_gate)

    # Load harness template for MCP servers and hook permissions.
    tmpl_name = read_template_name(project_dir)
    tmpl = load_template(tmpl_name)
    hooks = _to_sdk_hooks(build_hooks("supervisor", project_dir, template=tmpl))

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
            "mcp__clou__clou_status",
            "mcp__clou__clou_init",
            "mcp__clou__clou_create_milestone",
        ],
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

        _dialogue_turns_in_flight = 0  # queries sent but no ResultMessage yet

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
                            "Summarize the conversation so far, preserving key "
                            "decisions, code context, and open tasks."
                        )
                        await supervisor.query(
                            f"[SYSTEM: Context compaction requested. {instructions}. "
                            f"Acknowledge briefly and continue.]"
                        )
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
