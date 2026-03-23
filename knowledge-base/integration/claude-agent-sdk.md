# Claude Agent SDK — Capabilities & Constraints

This document maps what the Claude Agent SDK actually provides against what Clou needs. The SDK source is at `~/claude-agent-sdk/`.

**Clou's orchestrator is a Python application built on this SDK.** The orchestrator uses `ClaudeSDKClient` to manage supervisor and coordinator sessions, `create_sdk_mcp_server` for custom Clou tools, `HookMatcher` for write boundary enforcement, and `AgentDefinition` for worker/verifier agent teams. See [Orchestrator](./orchestrator.md) for implementation details.

## SDK Overview

The Claude Agent SDK wraps the Claude Code CLI as a subprocess, providing a programmatic interface for building AI agents. Both Python and TypeScript SDKs exist with the same subprocess architecture.

```
Your App → SDK (query/client) → SubprocessTransport → Claude Code CLI → Claude API
                                      ↕ JSON over stdin/stdout
```

**Python:** `pip install claude-agent-sdk` (v0.1.48, Python 3.10+, CLI bundled)
**TypeScript:** `npm install @anthropic-ai/claude-agent-sdk` (v0.2.79, Node 18+)

## Two API Patterns

### `query()` — One-Shot

```python
async for message in query(prompt="...", options=options):
    # process messages
```

- Unidirectional: send all input upfront, receive all output
- Best for batch processing, fire-and-forget, automation
- No follow-ups or interrupts
- Each call spawns its own CLI subprocess

### `ClaudeSDKClient` — Bidirectional

```python
async with ClaudeSDKClient(options) as client:
    await client.query("first message")
    async for msg in client.receive_response():
        # process
    await client.query("follow-up")
    async for msg in client.receive_response():
        # process
```

- Stateful, multi-turn conversations
- Supports interrupts, model switching, permission changes
- Session persistence (experimental in TS: `unstable_v2_resumeSession`)
- Single subprocess for the session lifetime

## What the SDK Provides That Clou Uses

### AgentDefinition

```python
@dataclass
class AgentDefinition:
    description: str
    prompt: str
    tools: list[str] | None = None
    model: Literal["sonnet", "opus", "haiku", "inherit"] | None = None
    skills: list[str] | None = None
    memory: Literal["user", "project", "local"] | None = None
    mcpServers: list[str | dict[str, Any]] | None = None
```

A named agent configuration. Flat dictionary of agents passed via `ClaudeAgentOptions.agents`. No dependency fields, no execution ordering — ordering is determined by the lead agent's reasoning.

**Clou implication:** Clou's coordinator defines agent team members as `AgentDefinition`s. The coordinator's prompt must encode the ordering logic that the SDK doesn't provide.

### ClaudeAgentOptions

Key fields relevant to Clou:

```python
@dataclass
class ClaudeAgentOptions:
    system_prompt: str | None = None          # Tier-specific prompt
    allowed_tools: list[str] | None = None    # Tool whitelist
    disallowed_tools: list[str] | None = None # Tool blacklist
    permission_mode: str | None = None        # "default", "acceptEdits", "plan", "bypassPermissions"
    model: str | None = None                  # "sonnet", "opus", "haiku"
    max_turns: int | None = None              # Conversation turn limit
    cwd: str | None = None                    # Working directory
    hooks: dict | None = None                 # Pre/PostToolUse hooks
    agents: dict[str, AgentDefinition] | None = None  # Subagent definitions
    mcp_servers: dict | None = None           # MCP server configs
    setting_sources: list | None = None       # Which settings to load
    enable_file_checkpointing: bool = False   # File state tracking
```

### Hooks System

```python
hooks = {
    'PreToolUse': [HookMatcher(matcher="Bash", hooks=[callback])],
    'PostToolUse': [HookMatcher(matcher=None, hooks=[callback])],
    'SubagentStart': [...],
    'SubagentStop': [...],
}
```

Hook events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `PermissionRequest`, `Stop`, `Notification`, `ConfigChange`

Hook inputs include `agent_id` and `agent_type` fields when firing from within a subagent, enabling attribution of hooks to specific agents.

**Clou implication:** Hooks are how Clou can enforce write boundaries (block writes to files outside a tier's ownership) and track agent team activity.

### Message Types

- `AssistantMessage` — Claude's response (text, tool use blocks)
- `UserMessage` — User input
- `SystemMessage` — System events (init, control)
- `ResultMessage` — Completion with cost, usage, duration, stop reason
- `TaskStartedMessage` — Subagent spawned
- `TaskProgressMessage` — Subagent progress (with `parent_tool_use_id`)
- `TaskNotificationMessage` — Subagent completed/failed/stopped

**Clou implication:** `TaskStartedMessage`, `TaskProgressMessage`, and `TaskNotificationMessage` give the coordinator visibility into agent team activity without reading inter-agent messages.

### In-Process MCP Servers

```python
@tool("my_tool", "Description", {"param": str})
async def my_tool(args):
    return {"content": [{"type": "text", "text": "result"}]}

server = create_sdk_mcp_server("my_server", tools=[my_tool])
```

Define custom tools as Python functions running in the same process. No subprocess overhead.

**Clou implication:** Clou could define custom tools (e.g., golden context read/write helpers, escalation filing) as in-process MCP tools. This is a potential pattern for enforcing protocols programmatically.

## What the SDK Does NOT Provide

### No Task Graph / DAG Scheduler
The `Task` tool spawns subagents. There are no explicit dependency edges, no barrier/join primitives, no conditional branching. The "DAG" is the lead agent deciding what to spawn when. See [DB-02](../decision-boundaries/02-task-dag-implementation.md).

### No Inter-Agent Messaging (Within SDK)
Subagents spawned via `Task` can only report back to the agent that spawned them. There is no peer-to-peer communication within a single session's subagent tree.

The Claude Code Agent Teams feature provides file-based mailboxes for peer communication, but Clou does not use this. Clou uses stigmergic coordination through the filesystem only (DB-10). See [Agent Teams](./agent-teams.md).

### No Nested Teams (Resolved)
The SDK doesn't support a teammate being the lead of a different team. Clou resolves this with the orchestrator (DB-01, decided) — supervisor and coordinator are independent `ClaudeSDKClient` sessions, not teammates. The coordinator is its own team lead. No nesting required.

### No Session Persistence (Python SDK)
The Python SDK does not have the `unstable_v2_resumeSession` API. Session state lives in the CLI subprocess. When the process dies, the session state is lost (except what's in transcript files and the golden context).

The TypeScript SDK has experimental session resumption.

### No Structured Output Enforcement
`output_format` exists but is not reliable for enforcing structured golden context writes. The agent can be prompted to write structured files, but there's no schema validation layer.

## Concurrency Model

Nothing stops you from running multiple independent `query()` or `ClaudeSDKClient` instances concurrently:

```python
import asyncio

async def run_agent(task, tools):
    options = ClaudeAgentOptions(tools=tools, model="haiku")
    results = []
    async for msg in query(prompt=task, options=options):
        results.append(msg)
    return results

# Fan-out: N independent agents in parallel
results = await asyncio.gather(
    run_agent("task A", ["WebSearch", "Write"]),
    run_agent("task B", ["WebSearch", "Write"]),
)
```

Each `query()` call spawns its own CLI subprocess — fully independent, truly parallel. But you build the graph yourself.

**Clou implication:** Future parallel coordinators can be implemented as concurrent `ClaudeSDKClient` instances managed by a thin orchestration layer.

## SDK Source Locations

- Python SDK: `~/claude-agent-sdk/claude-agent-sdk-python/`
  - `src/claude_agent_sdk/client.py` — ClaudeSDKClient (499 lines)
  - `src/claude_agent_sdk/query.py` — query() function (124 lines)
  - `src/claude_agent_sdk/types.py` — All type definitions (1204 lines)
  - `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` — CLI subprocess management
- TypeScript SDK: `~/claude-agent-sdk/claude-agent-sdk-typescript/` (stub repo, code on npm)
- Demos: `~/claude-agent-sdk/claude-agent-sdk-demos/`
