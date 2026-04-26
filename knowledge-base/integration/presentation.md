# Presentation Layer Integration

How SDK events become atmospheric changes. This document maps the exact join point between the Claude Agent SDK's message stream and Textual's widget tree — the bridge that makes the breathing conversation work.

## The Concurrency Model

Textual's `App.run()` owns the asyncio event loop. Everything else runs inside it.

```
Textual App (owns event loop)
    │
    ├── Supervisor Worker (@work, async, exclusive)
    │   │
    │   └── ClaudeSDKClient (supervisor session)
    │       │
    │       ├── receive_messages() yields:
    │       │   AssistantMessage → post ClouSupervisorText
    │       │   StreamEvent      → post ClouStreamChunk
    │       │   ResultMessage    → post ClouTurnComplete
    │       │   RateLimitEvent   → post ClouRateLimit
    │       │
    │       └── MCP tool handler: clou_spawn_coordinator
    │           │
    │           │ posts ClouCoordinatorSpawned (DIALOGUE → BREATH)
    │           │
    │           └── Coordinator Loop (run_coordinator)
    │               │
    │               └── ClaudeSDKClient (per-cycle, fresh session)
    │                   │
    │                   ├── AssistantMessage  → post ClouBreathEvent
    │                   ├── TaskStartedMsg    → post ClouAgentSpawned
    │                   ├── TaskProgressMsg   → post ClouAgentProgress
    │                   ├── TaskNotificationMsg → post ClouAgentComplete
    │                   ├── (new escalation file) → post ClouBreathEvent
    │                   │      "escalation filed: {classification}: {name}"
    │                   └── ResultMessage     → post ClouCycleComplete
    │
    │           posts ClouCoordinatorComplete (BREATH → HANDOFF/DIALOGUE)
    │
    ├── Mode State Machine (reactive attribute on App)
    ├── Conversation Widget (RichLog + streaming overlay)
    ├── Breath Widget (curated status lines)
    ├── Status Bar (custom Static, docked bottom)
    └── Input Widget (TextArea for multi-line)
```

**No escalation modal.** Escalations are agent-to-agent decision records (see `memory/project_escalations_are_agent_to_agent.md` and `knowledge-base/protocols/escalation.md`). New escalation files surface as **passive breath events** — a single status line, no modal, no mode transition, no user-visible decision affordance. User-facing decisions use `ask_user_mcp`, which is a separate channel.

**Why this works:** The supervisor `ClaudeSDKClient` runs as an async `@work` worker on the Textual event loop. Async workers can directly post messages to the app. When the supervisor calls `clou_spawn_coordinator`, the SDK routes to the in-process MCP tool handler, which runs the coordinator loop — still on the same event loop. The coordinator creates its own `ClaudeSDKClient` instances (separate subprocesses, separate sessions), but iterating their messages happens in the same async context. All `post_message()` calls are on the same event loop. No threads. No `call_from_thread()`. No synchronization.

**The supervisor session blocks during coordinator runs.** When the supervisor calls the MCP tool, the CLI subprocess waits for the tool result. No supervisor messages flow during this time. The bridge naturally switches from routing supervisor messages (dialogue mode) to routing coordinator messages (breath mode). When the coordinator finishes, the MCP tool returns, the supervisor resumes, and the bridge switches back.

## SDK Message → Clou Message Mapping

### Supervisor Messages (Dialogue Mode)

| SDK Type | Content/Condition | Clou Message | Action |
|----------|------------------|-------------|--------|
| `AssistantMessage` | `TextBlock` in content | `ClouSupervisorText(text, model)` | Append to conversation widget |
| `AssistantMessage` | `ThinkingBlock` in content | `ClouThinking(text)` | Show dimmed in conversation (collapsible) |
| `AssistantMessage` | `ToolUseBlock(name="clou_spawn_coordinator")` | `ClouCoordinatorSpawned(milestone)` | Transition DIALOGUE → BREATH |
| `AssistantMessage` | `ToolUseBlock(name="clou_status")` | `ClouStatusRequest()` | Render status inline in conversation |
| `AssistantMessage` | `ToolUseBlock` (other tools) | `ClouToolUse(name, input)` | Show tool use indicator in conversation |
| `UserMessage` | `ToolResultBlock` | `ClouToolResult(tool_id, content)` | Show result (collapsed) in conversation |
| `StreamEvent` | `include_partial_messages=True` | `ClouStreamChunk(text, uuid)` | Update streaming overlay in conversation |
| `ResultMessage` | Turn complete | `ClouTurnComplete(usage, cost, duration)` | Update status bar, re-enable input |
| `RateLimitEvent` | Rate limit state change | `ClouRateLimit(status, resets_at)` | Show warning in status bar |

### Coordinator Messages (Breath Mode)

| SDK Type | Content/Condition | Clou Message | Action |
|----------|------------------|-------------|--------|
| `AssistantMessage` | `TextBlock` (coordinator reasoning) | `ClouBreathEvent(text, cycle_type)` | Parse → curated status line in breath widget |
| `AssistantMessage` | `ToolUseBlock(name="Task")` | `ClouAgentDispatched(description)` | Show agent dispatch in breath widget |
| `TaskStartedMessage` | Agent team member spawned | `ClouAgentSpawned(task_id, description)` | Update breath widget, increment agent count |
| `TaskProgressMessage` | Agent working | `ClouAgentProgress(task_id, tool, tokens)` | Update breath widget activity indicator |
| `TaskNotificationMessage` | `status="completed"` | `ClouAgentComplete(task_id, summary)` | Show completion in breath widget |
| `TaskNotificationMessage` | `status="failed"` | `ClouAgentFailed(task_id, summary)` | Show failure, potential escalation trigger |
| `ResultMessage` | Cycle complete | `ClouCycleComplete(cycle_num, next_step)` | Update status bar, breath widget cycle indicator |

### Coordinator Lifecycle (Mode Transitions)

| Event | Clou Message | Mode Transition |
|-------|-------------|----------------|
| MCP tool `clou_spawn_coordinator` called | `ClouCoordinatorSpawned(milestone)` | DIALOGUE → BREATH |
| Escalation file written to golden context | `ClouBreathEvent(text="escalation filed: …")` | *(none — passive)* |
| Coordinator returns "completed" | `ClouCoordinatorComplete(milestone, "completed")` | BREATH → HANDOFF |
| Coordinator returns "escalated_*" | `ClouCoordinatorComplete(milestone, "escalated")` | BREATH → DIALOGUE |
| User dismisses handoff | `ClouHandoffDismissed()` | HANDOFF → DIALOGUE |
| User types during breath mode | `ClouUserInterrupt(text)` | BREATH → DIALOGUE (status line keeps breath metrics) |

DECISION mode survives this retirement — brutalist findings, validation failures, and any future agent-authored prompts still use it. Only the escalation-arrival pathway is closed.

## Clou Message Types

```python
from textual.message import Message
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


class Mode(Enum):
    DIALOGUE = auto()
    BREATH = auto()
    DECISION = auto()
    HANDOFF = auto()


# --- Supervisor / Dialogue messages ---

class ClouSupervisorText(Message):
    """Supervisor assistant text for the conversation."""
    def __init__(self, text: str, model: str) -> None:
        self.text = text
        self.model = model
        super().__init__()

class ClouThinking(Message):
    """Model thinking/reasoning block."""
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()

class ClouStreamChunk(Message):
    """Partial streaming token for live rendering."""
    def __init__(self, text: str, uuid: str) -> None:
        self.text = text
        self.uuid = uuid
        super().__init__()

class ClouToolUse(Message):
    """Supervisor is using a tool."""
    def __init__(self, name: str, tool_input: dict) -> None:
        self.name = name
        self.tool_input = tool_input
        super().__init__()

class ClouToolResult(Message):
    """Tool result returned."""
    def __init__(self, tool_use_id: str, content: str, is_error: bool) -> None:
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error
        super().__init__()

class ClouTurnComplete(Message):
    """Supervisor turn finished."""
    def __init__(self, input_tokens: int, output_tokens: int,
                 cost_usd: float | None, duration_ms: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.duration_ms = duration_ms
        super().__init__()

class ClouRateLimit(Message):
    """Rate limit state change."""
    def __init__(self, status: str, resets_at: int | None) -> None:
        self.status = status
        self.resets_at = resets_at
        super().__init__()


# --- Coordinator / Breath messages ---

class ClouCoordinatorSpawned(Message):
    """Coordinator session started for a milestone."""
    def __init__(self, milestone: str) -> None:
        self.milestone = milestone
        super().__init__()

class ClouBreathEvent(Message):
    """Curated status line from coordinator activity."""
    def __init__(self, text: str, cycle_type: str, phase: str | None) -> None:
        self.text = text
        self.cycle_type = cycle_type
        self.phase = phase
        super().__init__()

class ClouAgentSpawned(Message):
    """Agent team member dispatched."""
    def __init__(self, task_id: str, description: str) -> None:
        self.task_id = task_id
        self.description = description
        super().__init__()

class ClouAgentProgress(Message):
    """Agent team member working."""
    def __init__(self, task_id: str, last_tool: str | None,
                 total_tokens: int, tool_uses: int) -> None:
        self.task_id = task_id
        self.last_tool = last_tool
        self.total_tokens = total_tokens
        self.tool_uses = tool_uses
        super().__init__()

class ClouAgentComplete(Message):
    """Agent team member finished."""
    def __init__(self, task_id: str, status: str, summary: str) -> None:
        self.task_id = task_id
        self.status = status
        self.summary = summary
        super().__init__()

class ClouCycleComplete(Message):
    """Coordinator cycle finished."""
    def __init__(self, cycle_num: int, cycle_type: str,
                 next_step: str, phase_status: dict) -> None:
        self.cycle_num = cycle_num
        self.cycle_type = cycle_type
        self.next_step = next_step
        self.phase_status = phase_status
        super().__init__()

class ClouCoordinatorComplete(Message):
    """Coordinator finished for milestone."""
    def __init__(self, milestone: str, result: str) -> None:
        self.milestone = milestone
        self.result = result
        super().__init__()


# --- Escalation messages ---
#
# Escalations surface as ``ClouBreathEvent`` (see above) — a single
# status line per newly filed file. There is no ``ClouEscalationArrived``
# or ``ClouEscalationResolved`` message and no modal pathway.  See
# ``knowledge-base/protocols/escalation.md`` and
# ``memory/project_escalations_are_agent_to_agent.md``.


# --- Handoff messages ---

class ClouHandoff(Message):
    """Milestone handoff ready."""
    def __init__(self, milestone: str, handoff_path: Path) -> None:
        self.milestone = milestone
        self.handoff_path = handoff_path
        super().__init__()


# --- Metrics (continuous) ---

class ClouMetrics(Message):
    """Updated token/cost metrics."""
    def __init__(self, tier: str, milestone: str | None,
                 input_tokens: int, output_tokens: int,
                 cost_usd: float | None) -> None:
        self.tier = tier
        self.milestone = milestone
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        super().__init__()
```

## The Bridge Implementation

The bridge is not a separate component. It's two async loops integrated into the orchestrator — one for the supervisor session, one for coordinator sessions. Both post Textual messages to the app.

```python
# Conceptual shape — the supervisor message loop

class ClouApp(App):

    mode: reactive[Mode] = reactive(Mode.DIALOGUE)

    def on_mount(self) -> None:
        self.run_supervisor()

    @work(exclusive=True)
    async def run_supervisor(self) -> None:
        """Main supervisor session — runs for the lifetime of the app."""
        project_dir = Path.cwd()
        clou_tools = self._build_clou_tools(project_dir)
        clou_server = create_sdk_mcp_server("clou", tools=clou_tools)

        options = ClaudeAgentOptions(
            system_prompt=load_prompt("supervisor", project_dir),
            permission_mode="acceptEdits",
            cwd=str(project_dir),
            model="opus",
            hooks=build_hooks("supervisor", project_dir),
            mcp_servers={"clou": clou_server},
            include_partial_messages=True,  # enables StreamEvent
        )

        async with ClaudeSDKClient(options) as supervisor:
            self._supervisor = supervisor

            # Initial prompt
            await supervisor.query(build_initial_prompt(project_dir))

            # Message loop — runs until app exits
            async for msg in supervisor.receive_messages():
                self._route_supervisor_message(msg)

    def _route_supervisor_message(self, msg: Message) -> None:
        """Convert SDK message to Clou message and post to widget tree."""

        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self.post_message(ClouSupervisorText(
                        text=block.text, model=msg.model))
                elif isinstance(block, ThinkingBlock):
                    self.post_message(ClouThinking(text=block.thinking))
                elif isinstance(block, ToolUseBlock):
                    self.post_message(ClouToolUse(
                        name=block.name, tool_input=block.input))

        elif isinstance(msg, StreamEvent):
            # Extract text delta from raw stream event
            text = extract_stream_text(msg.event)
            if text:
                self.post_message(ClouStreamChunk(
                    text=text, uuid=msg.uuid))

        elif isinstance(msg, ResultMessage):
            usage = msg.usage or {}
            self.post_message(ClouTurnComplete(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=msg.total_cost_usd,
                duration_ms=msg.duration_ms,
            ))
            self.post_message(ClouMetrics(
                tier="supervisor", milestone=None,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=msg.total_cost_usd,
            ))

        elif isinstance(msg, RateLimitEvent):
            self.post_message(ClouRateLimit(
                status=msg.rate_limit_info.status,
                resets_at=msg.rate_limit_info.resets_at,
            ))

    def _build_clou_tools(self, project_dir: Path) -> list:
        """Build MCP tools — this is where breath mode connects."""

        @tool("clou_spawn_coordinator",
              "Spawn coordinator for a milestone.",
              {"milestone": str})
        async def spawn_coordinator(args):
            milestone = args["milestone"]

            # --- TRANSITION: DIALOGUE → BREATH ---
            self.post_message(ClouCoordinatorSpawned(milestone=milestone))

            # Run coordinator with breath routing
            result = await self._run_coordinator_with_bridge(
                project_dir, milestone)

            # --- TRANSITION: BREATH → HANDOFF or DIALOGUE ---
            self.post_message(ClouCoordinatorComplete(
                milestone=milestone, result=result))

            return {"content": [{"type": "text",
                    "text": f"Coordinator for '{milestone}' {result}."}]}

        @tool("clou_status", "Get current Clou status.", {})
        async def clou_status(args):
            status = read_clou_status(project_dir)
            return {"content": [{"type": "text", "text": status}]}

        return [spawn_coordinator, clou_status]
```

```python
# Conceptual shape — the coordinator bridge

    async def _run_coordinator_with_bridge(
        self, project_dir: Path, milestone: str
    ) -> str:
        """Run coordinator loop, posting breath events to the app."""
        # (lifecycle logic from orchestrator.md)

        while True:
            cycle_type, read_set = determine_next_cycle(
                checkpoint_path, milestone)

            if cycle_type == "COMPLETE":
                return "completed"

            result = await self._run_single_cycle_with_bridge(
                project_dir, milestone, cycle_type, read_set)

            # ... validation, retry, escalation logic ...

    async def _run_single_cycle_with_bridge(
        self, project_dir: Path, milestone: str,
        cycle_type: str, read_set: list[str]
    ) -> str:
        """Run one coordinator cycle, bridging its messages to breath."""

        prompt = build_cycle_prompt(
            project_dir, milestone, cycle_type, read_set)

        options = ClaudeAgentOptions(
            system_prompt=load_prompt("coordinator", project_dir,
                                      milestone=milestone),
            permission_mode="bypassPermissions",
            cwd=str(project_dir),
            model="opus",
            agents=build_agent_definitions(project_dir, milestone),
            hooks=build_hooks("coordinator", project_dir),
            mcp_servers={"brutalist": brutalist_mcp_config()},
            max_turns=200,
        )

        try:
            async with ClaudeSDKClient(options) as coordinator:
                await coordinator.query(prompt)

                async for msg in coordinator.receive_response():
                    self._route_coordinator_message(
                        msg, milestone, cycle_type)

            return read_cycle_outcome(project_dir, milestone)
        except Exception as e:
            return "failed"

    def _route_coordinator_message(
        self, msg, milestone: str, cycle_type: str
    ) -> None:
        """Convert coordinator SDK message to breath events."""

        if isinstance(msg, AssistantMessage):
            # Extract meaningful status from coordinator's reasoning
            text = extract_coordinator_status(msg, cycle_type)
            if text:
                phase = extract_current_phase(msg)
                self.post_message(ClouBreathEvent(
                    text=text, cycle_type=cycle_type, phase=phase))

        elif isinstance(msg, TaskStartedMessage):
            self.post_message(ClouAgentSpawned(
                task_id=msg.task_id,
                description=msg.description,
            ))

        elif isinstance(msg, TaskProgressMessage):
            self.post_message(ClouAgentProgress(
                task_id=msg.task_id,
                last_tool=msg.last_tool_name,
                total_tokens=msg.usage["total_tokens"],
                tool_uses=msg.usage["tool_uses"],
            ))

        elif isinstance(msg, TaskNotificationMessage):
            self.post_message(ClouAgentComplete(
                task_id=msg.task_id,
                status=msg.status,
                summary=msg.summary,
            ))

        elif isinstance(msg, ResultMessage):
            usage = msg.usage or {}
            self.post_message(ClouMetrics(
                tier="coordinator", milestone=milestone,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=msg.total_cost_usd,
            ))
```

## The Conversation Widget Problem

RichLog has no "update last line" API. Streaming LLM tokens need a different approach.

**Solution: Two-layer conversation surface.**

```
┌─────────────────────────────────────────┐
│  RichLog (completed turns)              │  ← Scrollable history
│    [user message]                       │
│    [assistant response - complete]      │
│    [user message]                       │
│    [assistant response - complete]      │
│                                         │
├─────────────────────────────────────────┤
│  Static (active streaming turn)         │  ← Updated on each StreamChunk
│    [tokens arriving live...]            │
└─────────────────────────────────────────┘
```

- `RichLog`: Holds all completed conversation turns. Each entry is a Rich renderable (Panel, Markdown, Text). Append-only.
- `Static` (or custom widget): Holds the currently streaming response. Updated via `widget.update(content)` on each `ClouStreamChunk`. When `ClouTurnComplete` fires, the complete response moves to `RichLog` and the `Static` clears.

For the streaming markdown rendering, use McGugan's block-finalization approach:
1. Accumulate tokens in a buffer
2. Parse markdown, identify finalized blocks
3. Render finalized blocks into the RichLog (immutable, never re-rendered)
4. Keep only the tail block in the Static (the only mutable element)

## Escalation Detection (Passive Notification)

Escalations are written to `.clou/milestones/<name>/escalations/<timestamp>-<slug>.md` **only** by the sanctioned writer paths:

- The MCP tool `mcp__clou_coordinator__clou_file_escalation` (coordinator authorship).
- `clou/recovery_escalation.py` (system-authored: cycle-limit, agent-crash, validation-failure, staleness).

Direct `Write`/`Edit`/`MultiEdit` from agents is denied by the PreToolUse hook. See `knowledge-base/protocols/escalation.md` for schema and `clou/escalation.py` for the canonical form.

Passive detection lives in the coordinator loop (`clou/coordinator.py`, `_announce_new_escalations`). The notifier:

1. Diffs the escalations directory against `.clou/milestones/{milestone}/active/seen-escalations.txt`. (C2: the seen file is per-milestone to close a cross-coordinator race under parallel dispatch; the legacy global path `.clou/active/seen-escalations.txt` is read once on migration, then abandoned.)
2. Parses each newly seen file via `clou.escalation.parse_escalation`.
3. Posts a `ClouBreathEvent(text="escalation filed: {classification}: {filename}", cycle_type="", phase=None)` per new file.
4. Parse failures emit a distinct `"escalation filed (parse-error): {filename}"` event so drift is visible at the status line.
5. `seen` bookkeeping is committed only after a successful post; a transient UI failure or `_app is None` defers the mark so the announcement is never silently lost.

```python
# In clou/coordinator.py (conceptual; see the real helper)

def _announce_new_escalations() -> None:
    _app = get_active_app()
    if _app is None:
        return  # defer mark-seen until UI attaches
    for esc_file in sorted(esc_dir.glob("*.md")):
        if esc_file.name in seen_escalations:
            continue
        try:
            form = parse_escalation(esc_file)
            text = f"escalation filed: {form.classification}: {esc_file.name}"
        except Exception:
            text = f"escalation filed (parse-error): {esc_file.name}"
        try:
            _app.post_message(
                ClouBreathEvent(text=text, cycle_type="", phase=None),
            )
        except Exception:
            continue  # retry on next cycle
        seen_escalations.add(esc_file.name)
    # single write after the loop, not per-file
    seen_path.write_text("\n".join(sorted(seen_escalations)) + "\n")
```

## Escalation Resolution (Agent-to-Agent)

Resolution is **agent-to-agent** — the supervisor reads the `## Disposition` section and updates `status` via the supervisor-tier resolution tool (not via a modal). There is no user-facing DECISION-mode transition for escalation arrival:

1. A new escalation file appears.
2. The passive notifier posts a single breath event.
3. The supervisor, on its next turn, reads `clou_status` (which filters to `open`/`investigating`/`deferred` per F30) and decides which escalation to tackle.
4. The supervisor invokes the resolution MCP tool to rewrite the `## Disposition` section. Other sections are preserved byte-for-byte.
5. The coordinator reads the disposition on its next cycle and, where applicable, resets cycle counters (DB-15 D5).

If the user needs to participate in the decision (credentials, outside-terminal action), the supervisor calls `ask_user_mcp` — a separate channel with explicit user surface.

## Status Bar

Textual's `Footer` only shows key bindings. The status bar is a custom `Static` widget docked to the bottom.

```python
class ClouStatusBar(Static):
    """Always-visible metrics bar."""

    milestone: reactive[str] = reactive("")
    cycle_type: reactive[str] = reactive("")
    cycle_num: reactive[int] = reactive(0)
    phase: reactive[str] = reactive("")
    input_tokens: reactive[int] = reactive(0)
    output_tokens: reactive[int] = reactive(0)
    cost_usd: reactive[float] = reactive(0.0)
    elapsed: reactive[float] = reactive(0.0)

    def render(self) -> RenderableType:
        if not self.milestone:
            return Text.assemble(
                ("clou", "bold"),
                ("  tokens: ", "dim"),
                (f"{self.input_tokens:,}↓ {self.output_tokens:,}↑", ""),
            )
        return Text.assemble(
            ("clou", "bold"),
            ("  ", ""),
            (self.milestone, "bold cyan"),
            ("  ", ""),
            (self.cycle_type, cycle_color(self.cycle_type)),
            (f" #{self.cycle_num}", "dim"),
            ("  ", ""),
            (self.phase or "", ""),
            ("  tokens: ", "dim"),
            (f"{self.input_tokens:,}↓ {self.output_tokens:,}↑", ""),
            ("  ", ""),
            (format_cost(self.cost_usd), "dim"),
        )
```

Reactive attributes auto-trigger `render()` on change. Message handlers on the App update them:

```python
def on_clou_metrics(self, msg: ClouMetrics) -> None:
    bar = self.query_one(ClouStatusBar)
    bar.input_tokens += msg.input_tokens
    bar.output_tokens += msg.output_tokens
    if msg.cost_usd:
        bar.cost_usd += msg.cost_usd

def on_clou_coordinator_spawned(self, msg: ClouCoordinatorSpawned) -> None:
    bar = self.query_one(ClouStatusBar)
    bar.milestone = msg.milestone

def on_clou_cycle_complete(self, msg: ClouCycleComplete) -> None:
    bar = self.query_one(ClouStatusBar)
    bar.cycle_num = msg.cycle_num
    bar.cycle_type = msg.next_step
```

## Mode Transitions as CSS

```tcss
/* Base conversation surface */
#conversation {
    background: $surface;
    color: $text;
}

/* Dialogue mode — full presence */
ClouApp.dialogue #conversation {
    opacity: 1.0;
    color: $text;
}

ClouApp.dialogue #breath {
    display: none;
}

/* Breath mode — conversation recedes */
ClouApp.breath #conversation {
    opacity: 0.4;
    max-height: 30%;
    overflow-y: hidden;
}

ClouApp.breath #breath {
    display: block;
    color: $text-muted;
}

/* Decision mode — breath pauses, card surfaces */
ClouApp.decision #breath {
    opacity: 0.3;
}

/* Handoff mode */
ClouApp.handoff #breath {
    display: none;
}

ClouApp.handoff #handoff {
    display: block;
}
```

Mode transitions in Python:

```python
def watch_mode(self, old: Mode, new: Mode) -> None:
    """Reactive watcher — fires when self.mode changes."""
    # Remove old mode class, add new
    self.remove_class(old.name.lower())
    self.add_class(new.name.lower())

    # Animate the atmospheric shift
    if new == Mode.BREATH:
        conv = self.query_one("#conversation")
        conv.styles.animate("opacity", 0.4, duration=1.5,
                           easing="out_cubic")

    elif new == Mode.DIALOGUE:
        conv = self.query_one("#conversation")
        conv.styles.animate("opacity", 1.0, duration=0.5,
                           easing="out_cubic")
```

## Breath Event Curation

The coordinator's `AssistantMessage` stream contains verbose reasoning. The bridge must **curate** this into breath-mode status lines. Not every coordinator message becomes a breath event.

```python
def extract_coordinator_status(
    msg: AssistantMessage, cycle_type: str
) -> str | None:
    """Extract a curated status line from coordinator reasoning.

    Returns None for messages that shouldn't surface in breath mode.
    Only meaningful state changes become breath events.
    """
    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            # Tool calls are meaningful state changes
            match block.name:
                case "Write" | "Edit":
                    path = block.input.get("file_path", "")
                    if "compose.py" in path:
                        return "compose.py updated"
                    if "execution.md" in path:
                        return None  # agent writes, too granular
                    if "phase.md" in path:
                        phase = Path(path).parent.name
                        return f"phase:{phase}  spec written"
                    if "decisions.md" in path:
                        return "decision logged"
                    if "status.md" in path:
                        return None  # routine checkpoint
                case "Agent":
                    desc = block.input.get("description", "")
                    return f"dispatching  {desc[:50]}"
                case name if name.startswith("mcp__brutalist__"):
                    tool = name.replace("mcp__brutalist__", "")
                    return f"brutalist  {tool}"
                case _:
                    return None

        # Text blocks: only surface if they contain phase/cycle transitions
        if isinstance(block, TextBlock):
            text = block.text.lower()
            if "phase complete" in text or "moving to" in text:
                return extract_transition_summary(block.text)

    return None
```

## User Input Routing

When the user types and presses Enter:

- **Dialogue mode:** Input goes to the supervisor session via `supervisor.query(text)`.
- **Breath mode:** Typing triggers BREATH → DIALOGUE transition. The breath metrics compress to the status bar. The input goes to the supervisor (which is blocked on the MCP tool — the user may be talking to the orchestrator directly, or their input will queue until the supervisor session resumes).
- **Decision mode:** Reserved for non-escalation decision cards (brutalist findings, validation failures). Input handling is specific to each card.
- **Handoff mode:** Input goes to the supervisor (which has resumed after the coordinator returned).

```python
async def on_input_submitted(self, event: Input.Submitted) -> None:
    text = event.value.strip()
    if not text:
        return

    event.input.clear()

    match self.mode:
        case Mode.DIALOGUE | Mode.HANDOFF:
            # Show user message in conversation
            self.query_one("#conversation").write(
                Text(f"\n{text}\n", style="bold"))
            # Send to supervisor
            await self._supervisor.query(text)

        case Mode.BREATH:
            # Transition to dialogue, then send
            self.mode = Mode.DIALOGUE
            self.query_one("#conversation").write(
                Text(f"\n{text}\n", style="bold"))
            await self._supervisor.query(text)

        case Mode.DECISION:
            pass  # Handled by the active decision card (e.g. brutalist findings)
```

## File Structure

```
clou/
├── orchestrator.py          # Session lifecycle (calls bridge internally)
├── coordinator.py           # Cycle engine; _announce_new_escalations lives here
├── escalation.py            # EscalationForm, render_escalation, parse_escalation
├── graph.py                 # Compose.py validation (exists)
├── ui/
│   ├── app.py               # ClouApp — Textual App, mode state machine,
│   │                        #   supervisor worker, MCP tool handlers
│   ├── clou.tcss            # Visual language as CSS
│   ├── theme.py             # Color palette, semantic tokens
│   ├── messages.py          # All ClouMessage types (above); no
│   │                        #   ClouEscalationArrived / Resolved
│   ├── bridge.py            # extract_coordinator_status, extract_stream_text,
│   │                        #   format helpers (parse_escalation lives in
│   │                        #   clou.escalation, not here)
│   ├── widgets/
│   │   ├── conversation.py  # Two-layer: RichLog + streaming Static
│   │   ├── breath.py        # Curated status line display
│   │   ├── handoff.py       # Markdown walk-through renderer
│   │   ├── status_bar.py    # Reactive metrics bar (custom Static)
│   │   ├── dag.py           # Compose.py dependency graph (on-demand)
│   │   └── context_tree.py  # Golden context tree (on-demand)
│   └── screens/
│       ├── main.py          # Primary screen: conversation + breath + status
│       ├── context.py       # Push-screen: golden context explorer
│       └── detail.py        # Push-screen: decisions, cost breakdown
```

## What This Design Resolves

| Problem | Resolution |
|---------|-----------|
| SDK and Textual share event loop | Async `@work` worker runs SDK client; both on Textual's loop |
| Supervisor blocks during coordinator | MCP tool handler runs coordinator inline; bridge switches routing |
| RichLog can't stream tokens | Two-layer surface: RichLog (history) + Static (streaming tail) |
| Escalation detection | Passive filesystem diff in `_announce_new_escalations`; posts `ClouBreathEvent` per new file |
| Mode transitions | Reactive `mode` attribute → CSS class toggle + `styles.animate()` |
| Breath event curation | `extract_coordinator_status()` filters coordinator messages |
| Footer can't show metrics | Custom `ClouStatusBar` widget (docked Static with reactives) |
| User input during breath | Mode transition BREATH → DIALOGUE, then route to supervisor |

## Open Engineering Questions

1. **`include_partial_messages` for the supervisor?** Enables `StreamEvent` for token streaming. Worth the extra message volume? Probably yes — the streaming feel is critical for dialogue mode.

2. **Coordinator `include_partial_messages`?** Probably not — we don't want to stream coordinator reasoning to the user. We want curated breath events. But `TaskProgressMessage` arrives without this flag, which is what we need for agent team visibility.

3. **Escalation while supervisor is blocked.** Escalations are agent-to-agent (see `knowledge-base/protocols/escalation.md`); they do not interrupt the supervisor's session. The supervisor picks up open escalations between coordinator runs via `clou_status` (F30 filters to actionable statuses). When user action is genuinely required (credentials, outside-terminal setup), the coordinator uses `ask_user_mcp` instead — a distinct channel with explicit user surface.

4. **Multiple coordinators (future).** The current design serializes coordinators. If we later parallelize them, the bridge needs to route breath events from multiple coordinators. The message types already include `milestone` for disambiguation. The status bar would need to show multiple milestones.

5. **Context exhaustion mid-cycle.** The orchestrator detects this via `ResultMessage` usage and sends a checkpoint instruction. The bridge should show this as a breath event: "context approaching limit  checkpointing".
