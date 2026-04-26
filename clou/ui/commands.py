"""Slash command system — discoverable harness surface.

Provides the Command abstraction, a static registry, and the dispatch
function that intercepts ``/``-prefixed input before it reaches the
supervisor session.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import TYPE_CHECKING

from rich.text import Text

from clou.ui.mode import Mode
from clou.ui.theme import PALETTE

if TYPE_CHECKING:
    from clou.ui.app import ClouApp

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_MUTED_HEX = PALETTE["text-muted"].to_hex()

# All modes — default for commands with no restriction.
_ALL_MODES: frozenset[Mode] = frozenset(Mode)

# ---------------------------------------------------------------------------
# Command protocol
# ---------------------------------------------------------------------------

#: Handler signature: async (app, args_string) -> None
CommandHandler = Callable[["ClouApp", str], Awaitable[None]]


@dataclass(frozen=True)
class SubItem:
    """A sub-option within a command's submenu."""

    label: str
    description: str
    args: str  # appended to the parent command when dispatched


#: Factory that produces sub-items dynamically (e.g. session list).
ItemsFactory = Callable[["ClouApp"], tuple[SubItem, ...]]


@dataclass(frozen=True)
class Command:
    """A single slash command."""

    name: str
    description: str
    handler: CommandHandler
    shortcut: str = ""
    modes: frozenset[Mode] = field(default_factory=lambda: _ALL_MODES)
    items: tuple[SubItem, ...] = ()  # static submenu entries
    items_factory: ItemsFactory | None = None  # dynamic submenu entries


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Command] = {}


def register(cmd: Command) -> Command:
    """Add a command to the registry."""
    _REGISTRY[cmd.name] = cmd
    return cmd


def get(name: str) -> Command | None:
    """Look up a command by name."""
    return _REGISTRY.get(name)


def all_commands() -> list[Command]:
    """Return all registered commands sorted by name."""
    return sorted(_REGISTRY.values(), key=lambda c: c.name)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def dispatch(app: ClouApp, text: str) -> bool:
    """Try to dispatch *text* as a slash command.

    Returns ``True`` if the text was handled (even if the command was
    unknown — an error is rendered).  Returns ``False`` only when *text*
    does not start with ``/``.
    """
    if not text.startswith("/"):
        return False

    parts = text[1:].split(None, 1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    if not name:
        # Bare "/" — show help.
        cmd = get("help")
        if cmd is not None:
            await cmd.handler(app, "")
        return True

    cmd = get(name)
    if cmd is None:
        _render_error(app, f"unknown command: /{name}")
        return True

    if app.mode not in cmd.modes:
        mode_names = ", ".join(sorted(m.name.lower() for m in cmd.modes))
        _render_error(app, f"/{name} is available in {mode_names} mode")
        return True

    await cmd.handler(app, args)
    return True


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def render_command_output(app: ClouApp, renderable: Text | str) -> None:
    """Write command output into the conversation surface."""
    from clou.ui.widgets.conversation import ConversationWidget

    conv = app.query_one(ConversationWidget)
    conv.add_command_output(renderable)


def _render_error(app: ClouApp, text: str) -> None:
    """Write a brief dim error into the conversation surface."""
    from clou.ui.widgets.conversation import ConversationWidget

    conv = app.query_one(ConversationWidget)
    conv.add_command_error(text)


# ---------------------------------------------------------------------------
# Built-in commands
# ---------------------------------------------------------------------------


async def _cmd_help(app: ClouApp, args: str) -> None:
    """List all available slash commands."""
    lines: list[tuple[str, str, str]] = []
    max_name = 0
    max_desc = 0
    for cmd in all_commands():
        max_name = max(max_name, len(cmd.name) + 1)  # +1 for "/"
        max_desc = max(max_desc, len(cmd.description))
        lines.append((f"/{cmd.name}", cmd.description, cmd.shortcut))

    result = Text()
    for i, (name, desc, shortcut) in enumerate(lines):
        if i > 0:
            result.append("\n")
        result.append(f"  {name:<{max_name + 2}}", style=f"bold {_GOLD_HEX}")
        result.append(f"{desc}", style=_DIM_HEX)
        if shortcut:
            result.append(f"  {shortcut}", style=_MUTED_HEX)

    render_command_output(app, result)


async def _cmd_clear(app: ClouApp, args: str) -> None:
    """Clear conversation history."""
    app.action_clear()


register(
    Command(
        name="help",
        description="this list",
        handler=_cmd_help,
    )
)

register(
    Command(
        name="clear",
        description="clear conversation",
        handler=_cmd_clear,
        shortcut="⌃L",
    )
)


async def _cmd_cost(app: ClouApp, args: str) -> None:
    """Show token usage and session cost."""
    from clou.ui.widgets.status_bar import ClouStatusBar, format_cost, format_tokens

    if args.strip() == "detail":
        app.action_show_costs()
        return

    bar = app.query_one(ClouStatusBar)
    elapsed = time.monotonic() - app._session_start_time
    mins, secs = divmod(int(elapsed), 60)
    hrs, mins = divmod(mins, 60)
    duration = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"

    result = Text()
    result.append("  input   ", style=_DIM_HEX)
    result.append(format_tokens(bar.input_tokens))
    result.append("\n  output  ", style=_DIM_HEX)
    result.append(format_tokens(bar.output_tokens))
    result.append("\n  cost    ", style=_DIM_HEX)
    result.append(format_cost(bar.cost_usd), style=f"bold {_GOLD_HEX}")
    result.append("\n  session ", style=_DIM_HEX)
    result.append(duration)

    render_command_output(app, result)


register(
    Command(
        name="cost",
        description="token usage and cost",
        handler=_cmd_cost,
        shortcut="⌃T",
        items=(
            SubItem("summary", "quick overview", ""),
            SubItem("detail", "detailed breakdown", "detail"),
        ),
    )
)


async def _cmd_dag(app: ClouApp, args: str) -> None:
    """Show the DAG viewer."""
    app.action_show_dag()


register(
    Command(
        name="dag",
        description="task DAG viewer",
        handler=_cmd_dag,
        shortcut="⌃D",
        modes=frozenset({Mode.BREATH, Mode.HANDOFF}),
    )
)


async def _cmd_context(app: ClouApp, args: str) -> None:
    """Show the golden context tree."""
    app.action_show_context()


register(
    Command(
        name="context",
        description="golden context tree",
        handler=_cmd_context,
        shortcut="⌃G",
    )
)


async def _cmd_diff(app: ClouApp, args: str) -> None:
    """Show git diff output with syntax highlighting."""
    import asyncio

    from clou.ui.diff import render_diff

    async def _run_git(*cmd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            if "not a git repository" in err.lower():
                return ""
            return ""
        return stdout.decode()

    parts = args.strip().split() if args.strip() else []
    staged_only = "staged" in parts or "--staged" in parts
    path_args = [p for p in parts if p not in ("staged", "--staged")]

    if staged_only:
        raw = await _run_git("diff", "--cached", "--no-color", *path_args)
    else:
        # Show both staged and unstaged.
        unstaged = await _run_git("diff", "--no-color", *path_args)
        staged = await _run_git("diff", "--cached", "--no-color", *path_args)
        raw = unstaged + staged

    if not raw.strip():
        # Check if we're even in a git repo.
        check = await _run_git("rev-parse", "--git-dir")
        if not check.strip():
            _render_error(app, "not a git repository")
        else:
            _render_error(app, "no changes")
        return

    rendered = render_diff(raw)
    line_count = raw.count("\n")

    if line_count > 50:
        from clou.ui.screens.detail import DetailScreen

        app.push_screen(DetailScreen(title="Diff", content=raw))
    else:
        render_command_output(app, rendered)


register(
    Command(
        name="diff",
        description="show git diff",
        handler=_cmd_diff,
        items=(
            SubItem("all", "staged and unstaged", ""),
            SubItem("staged", "staged changes only", "staged"),
        ),
    )
)


async def _cmd_export(app: ClouApp, args: str) -> None:
    """Export conversation history to a Markdown file."""
    from datetime import datetime
    from pathlib import Path

    from clou.ui.history import export_markdown

    include_tools = "--full" in args
    parts = [p for p in args.split() if p != "--full"]
    if parts:
        output_path = Path(parts[0]).expanduser()
    else:
        ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        export_dir = app._project_dir / ".clou" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"conversation-{ts}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    md = export_markdown(app._conversation_history, include_tools=include_tools)
    output_path.write_text(md)
    render_command_output(app, f"exported to {output_path}")


register(
    Command(
        name="export",
        description="export conversation to markdown",
        handler=_cmd_export,
        items=(
            SubItem("standard", "conversation only", ""),
            SubItem("full", "include tool calls", "--full"),
        ),
    )
)


async def _cmd_status(app: ClouApp, args: str) -> None:
    """Show current session status."""
    from clou.ui.widgets.status_bar import ClouStatusBar, format_cost, format_tokens

    bar = app.query_one(ClouStatusBar)

    elapsed = time.monotonic() - app._session_start_time
    mins, secs = divmod(int(elapsed), 60)
    hrs, mins = divmod(mins, 60)
    duration = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"

    result = Text()

    if bar.milestone:
        result.append("  milestone  ", style=_DIM_HEX)
        result.append(bar.milestone, style=f"bold {_GOLD_HEX}")
        if bar.cycle_type:
            result.append("\n  cycle      ", style=_DIM_HEX)
            result.append(f"{bar.cycle_type} #{bar.cycle_num}")
        if bar.phase:
            result.append("\n  phase      ", style=_DIM_HEX)
            result.append(bar.phase)

        # Task progress from DAG.
        if app._dag_tasks:
            done = sum(1 for t in app._dag_tasks if t.get("status") == "done")
            total = len(app._dag_tasks)
            result.append("\n  tasks      ", style=_DIM_HEX)
            result.append(f"{done}/{total}")
    else:
        result.append("  no active milestone", style=_DIM_HEX)

    result.append("\n  tokens     ", style=_DIM_HEX)
    in_tok = format_tokens(bar.input_tokens)
    out_tok = format_tokens(bar.output_tokens)
    result.append(f"{in_tok} in / {out_tok} out")
    result.append("\n  cost       ", style=_DIM_HEX)
    result.append(format_cost(bar.cost_usd), style=f"bold {_GOLD_HEX}")
    result.append("\n  session    ", style=_DIM_HEX)
    result.append(duration)
    result.append("\n  mode       ", style=_DIM_HEX)
    result.append(app.mode.name.lower())

    render_command_output(app, result)


register(
    Command(
        name="status",
        description="session status summary",
        handler=_cmd_status,
    )
)


async def _cmd_compact(app: ClouApp, args: str) -> None:
    """Request context compaction of the supervisor session."""
    import asyncio
    from pathlib import Path

    # Persist conversation history to disk.
    history_path = app._project_dir / ".clou" / "active" / "supervisor-history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    with history_path.open("w") as f:
        for entry in app._conversation_history:
            f.write(
                json.dumps(
                    {
                        "role": entry.role,
                        "content": entry.content,
                        "timestamp": entry.timestamp,
                    }
                )
                + "\n"
            )

    # Signal the orchestrator.
    app._compact.complete.clear()
    app._compact.instructions = args.strip()
    app._compact.requested.set()

    render_command_output(app, Text("  compacting...", style=f"italic {_DIM_HEX}"))

    # Wait for completion.  No timeout — compaction involves a full
    # supervisor LLM pass and can legitimately take several minutes on
    # large transcripts.  If the supervisor is genuinely stuck, the user
    # can Ctrl-C; adding a ceiling would kneecap legitimate long runs.
    await app._compact.complete.wait()

    # Show confirmation.
    count = app._compact.count
    result = Text()
    result.append("  compacted", style=f"bold {_GOLD_HEX}")
    result.append(f"  (compaction #{count})", style=_DIM_HEX)

    if count >= 3:
        result.append(
            f"\n  ⚠ {count} compactions — significant context loss likely",
            style=f"bold {PALETTE['accent-rose'].to_hex()}",
        )

    render_command_output(app, result)


register(
    Command(
        name="compact",
        description="compact supervisor context",
        handler=_cmd_compact,
        modes=frozenset({Mode.DIALOGUE}),
    )
)


async def _cmd_model(app: ClouApp, args: str) -> None:
    """Show current model. Switching is not yet implemented."""
    if args.strip():
        result = Text()
        result.append(
            "  model switching is not yet implemented",
            style=_DIM_HEX,
        )
        result.append(f"\n  current model: {app._model}", style=f"bold {_GOLD_HEX}")
        render_command_output(app, result)
        return

    result = Text()
    result.append("  model  ", style=_DIM_HEX)
    result.append(app._model, style=f"bold {_GOLD_HEX}")
    render_command_output(app, result)


register(
    Command(
        name="model",
        description="show or switch model",
        handler=_cmd_model,
        modes=frozenset({Mode.DIALOGUE}),
        items=(
            SubItem("opus", "claude opus", "opus"),
            SubItem("sonnet", "claude sonnet", "sonnet"),
            SubItem("haiku", "claude haiku", "haiku"),
        ),
    )
)


async def _cmd_exit(app: ClouApp, args: str) -> None:
    """Exit clou."""
    app.exit()


register(
    Command(
        name="exit",
        description="exit clou",
        handler=_cmd_exit,
    )
)


def _relative_time(timestamp: float) -> str:
    """Format a timestamp as a human-readable relative time."""
    delta = time.time() - timestamp
    if delta < 60:
        return "just now"
    if delta < 3600:
        mins = int(delta / 60)
        return f"{mins}m ago"
    if delta < 86400:
        hrs = int(delta / 3600)
        return f"{hrs}h ago"
    if delta < 172800:
        return "yesterday"
    from datetime import datetime
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%b %d")


def _resume_items_factory(app: ClouApp) -> tuple[SubItem, ...]:
    """Build dynamic sub-items from available sessions."""
    from clou.session import list_sessions, session_preview

    sessions = list_sessions(app._project_dir)
    current_id = app._session.session_id if app._session else ""
    items: list[SubItem] = []
    for info in sessions[:10]:
        if info.session_id == current_id:
            continue
        preview = session_preview(app._project_dir, info.session_id, max_chars=50)
        label = _relative_time(info.started_at)
        desc = f'"{preview}"  {info.model}' if preview else info.model
        items.append(SubItem(label=label, description=desc, args=info.session_id))
    return tuple(items)


async def _cmd_resume(app: ClouApp, args: str) -> None:
    """Resume a previous session or list available sessions."""
    if args.strip():
        sid = args.strip()
        # Reject resuming current session.
        current_id = app._session.session_id if app._session else ""
        if sid == current_id:
            _render_error(app, "already in this session")
            return
        # Validate session exists.
        from clou.session import session_path
        if not session_path(app._project_dir, sid).exists():
            _render_error(app, f"session not found: {sid}")
            return
        app.resume_session(sid)
        return

    # No args — list available sessions.
    from clou.session import list_sessions, session_preview

    sessions = list_sessions(app._project_dir)
    current_id = app._session.session_id if app._session else ""
    resumable = [s for s in sessions if s.session_id != current_id][:10]
    if not resumable:
        _render_error(app, "no previous sessions to resume")
        return

    result = Text()
    for i, info in enumerate(resumable):
        if i > 0:
            result.append("\n")
        preview = session_preview(app._project_dir, info.session_id, max_chars=50)
        rel = _relative_time(info.started_at)
        result.append(f"  {rel:<12}", style=_DIM_HEX)
        if preview:
            result.append(f'"{preview}"', style=f"italic {_DIM_HEX}")
            result.append(f"  {info.model}", style=_MUTED_HEX)
        else:
            result.append(info.model, style=_MUTED_HEX)
        result.append(f"  {info.session_id}", style=_MUTED_HEX)

    result.append(f"\n\n  /resume <id>", style=f"bold {_GOLD_HEX}")
    result.append(" to resume a session", style=_DIM_HEX)
    render_command_output(app, result)


register(
    Command(
        name="resume",
        description="resume a previous session",
        handler=_cmd_resume,
        items_factory=_resume_items_factory,
    )
)


async def _cmd_stop(app: ClouApp, args: str) -> None:
    """Stop the running coordinator at the next cycle boundary."""
    if app.mode not in (Mode.BREATH, Mode.DECISION):
        render_command_output(
            app,
            Text("No coordinator running", style=_MUTED_HEX),
        )
        return
    if app._stop_requested.is_set():
        render_command_output(
            app,
            Text("Stop already pending — waiting for cycle boundary", style=_MUTED_HEX),
        )
        return
    app._stop_requested.set()
    render_command_output(
        app,
        Text(
            "Stop requested — coordinator will pause at next cycle boundary",
            style=_GOLD_HEX,
        ),
    )


register(
    Command(
        name="stop",
        description="stop the running coordinator",
        handler=_cmd_stop,
        modes=frozenset({Mode.BREATH, Mode.DECISION}),
    )
)
