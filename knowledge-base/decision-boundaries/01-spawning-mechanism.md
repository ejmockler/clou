# DB-01: Spawning Mechanism

**Status:** DECIDED — Option B (Orchestrator Wrapper)
**Decided:** 2026-03-19

## Decision

Clou uses a thin Python orchestrator script that manages session lifecycles via the Claude Agent SDK. The orchestrator is invisible plumbing — the user interacts with the supervisor session. The orchestrator spawns, monitors, and restarts sessions programmatically.

```
clou_orchestrator.py (Python, uses Claude Agent SDK)
    │
    ├─ ClaudeSDKClient → Supervisor session (user-facing)
    │
    ├─ ClaudeSDKClient → Coordinator session (when supervisor requests)
    │
    └─ Coordinator uses native Agent Teams for workers
```

## Why Option B

The orchestrator provides capabilities that Bash-spawned sessions cannot:

1. **Structured message stream** — typed `AssistantMessage`, `ResultMessage`, `TaskProgressMessage` instead of raw stdout
2. **Hook enforcement** — PreToolUse callbacks in the orchestrator process enforce write boundaries per tier
3. **Cost tracking** — `ResultMessage.total_cost_usd` and token usage per session
4. **Context exhaustion detection** — monitor `ResultMessage.usage.input_tokens` and restart sessions before context degrades
5. **Error recovery** — try/except around SDK calls with checkpoint-based restart
6. **Lifecycle control** — `interrupt()`, `disconnect()`, `stop_task()` per session
7. **Future parallel coordinators** — `asyncio.gather` on multiple coordinator sessions
8. **In-process MCP servers** — custom Clou tools via `@tool` decorator without subprocess overhead

## Concrete Architecture

```python
# clou_orchestrator.py

async def main(project_dir: str):
    """Clou entry point. Manages all session lifecycles."""

    # Start supervisor session
    supervisor = await start_supervisor(project_dir)

    # Main loop: supervisor runs, orchestrator monitors
    async for msg in supervisor.receive_messages():
        if is_coordinator_request(msg):
            milestone = extract_milestone(msg)
            await run_coordinator(project_dir, milestone)
            # Signal coordinator completion back to supervisor
            await supervisor.query("Coordinator completed. Read status.")

async def start_supervisor(project_dir: str) -> ClaudeSDKClient:
    options = ClaudeAgentOptions(
        system_prompt=load_prompt("supervisor"),
        tools={"type": "preset", "preset": "claude_code"},
        permission_mode="acceptEdits",
        cwd=project_dir,
        model="opus",
        hooks=build_hooks("supervisor"),
        mcp_servers=clou_mcp_servers(),
    )
    client = ClaudeSDKClient(options)
    await client.connect()
    return client

async def run_coordinator(project_dir: str, milestone: str):
    # NOTE: This pseudocode predates DB-03 (session-per-cycle) and DB-04
    # (light system prompts). See orchestrator.md for the current
    # session-per-cycle loop with per-cycle protocol files.
    options = ClaudeAgentOptions(
        system_prompt=load_prompt("coordinator", milestone=milestone),
        tools={"type": "preset", "preset": "claude_code"},
        permission_mode="bypassPermissions",
        cwd=project_dir,
        model="opus",
        agents=build_agent_definitions(milestone),
        hooks=build_hooks("coordinator"),
        mcp_servers=clou_mcp_servers(),
    )
    async with ClaudeSDKClient(options) as coordinator:
        await coordinator.query(f"Begin milestone: {milestone}")
        async for msg in coordinator.receive_response():
            track_progress(msg)
            if context_exhausted(msg):
                await checkpoint_and_restart(coordinator, milestone)
```

## Cascading Effects

This decision partially resolves or constrains:

- **DB-03 (Context Window):** Orchestrator monitors token usage via `ResultMessage.usage` and can restart sessions from checkpoints
- **DB-05 (Error Recovery):** Orchestrator wraps sessions in try/except, detects crashes, restarts from golden context
- **DB-06 (Token Economics):** Orchestrator collects `ResultMessage.total_cost_usd` per session for cost tracking
- **DB-04 (Prompt System):** Orchestrator passes `system_prompt` programmatically, can parameterize per milestone

## Supervisor ↔ Orchestrator Communication

The supervisor needs a way to signal "spawn a coordinator for milestone X" to the orchestrator. Options:

**A. Golden context signaling:** Supervisor writes to a known file (e.g., `active/supervisor.md` with `request: spawn_coordinator, milestone: X`). Orchestrator polls this file.

**B. Custom MCP tool:** Orchestrator provides an in-process MCP tool `clou_spawn_coordinator(milestone)` that the supervisor calls. The tool handler in the orchestrator process starts the coordinator session.

**C. Convention in message stream:** The orchestrator watches the supervisor's message stream for specific patterns (e.g., tool calls to Write that target `active/supervisor.md` with coordinator spawn requests).

**Recommended: B (Custom MCP tool).** This is the cleanest — the supervisor calls a tool, the orchestrator handles it. No polling, no pattern matching. The tool can return status to the supervisor.

```python
@tool("clou_spawn_coordinator", "Spawn a coordinator for a milestone", {
    "milestone": str,
})
async def spawn_coordinator(args):
    milestone = args["milestone"]
    # Orchestrator starts coordinator session
    result = await run_coordinator(project_dir, milestone)
    return {"content": [{"type": "text", "text": f"Coordinator completed: {result}"}]}

clou_server = create_sdk_mcp_server("clou", tools=[spawn_coordinator])
```

## What the Orchestrator is NOT

- Not a UI — the user never sees it
- Not a decision-maker — it doesn't reason about what to build
- Not a prompt — it's Python code, not an LLM session
- Not complex — it's lifecycle management and plumbing, ~500-800 lines estimated
