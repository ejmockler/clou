# Agent Teams in Clou

## Overview

Clou uses the Claude Agent SDK's Agent tool to spawn worker agents during EXECUTE cycles. Each worker is scoped to a single compose.py function. Workers coordinate through the filesystem (stigmergy), not through direct messaging. The coordinator dispatches mechanically following compose.py's call graph structure.

**Key decision (DB-10):** Mechanical dispatch with circuit breakers. No mailbox, no SendMessage, no TaskCreate, no inter-worker communication. Stigmergy only.

## How Clou Spawns Agent Teams

### Architecture

```
clou_orchestrator.py
    │
    ├─ ClaudeSDKClient → Supervisor (agent session)
    │                         │ calls clou_spawn_coordinator MCP tool
    │                         ↓
    ├─ ClaudeSDKClient → Coordinator (agent session, per cycle)
    │                         │ uses Agent tool to spawn workers
    │                         ↓
    │                    Worker agents (subprocesses of coordinator session)
```

The coordinator uses `AgentDefinition` types registered via `ClaudeAgentOptions.agents`. During EXECUTE, it invokes the Agent tool for each compose.py function, spawning independent worker subprocesses.

### One Agent Per Function

DB-02 and DB-04 establish this:
- DB-02: "Each agent receives its function signature as its briefing"
- DB-04 worker system prompt: "Read your function signature in compose.py" — singular

Each compose.py function gets its own agent. `gather()` tasks spawn in parallel. Sequential tasks spawn serially, gated by SDK `TaskNotificationMessage`.

### Agent Definitions (Template-Driven)

Agent definitions come from the active harness template (DB-11). The template's `agents` dict maps agent names to `AgentSpec` objects specifying description, prompt_ref, tier, tools, and model. The orchestrator's `build_agent_definitions()` reads the template and constructs `AgentDefinition` objects. See [Orchestrator](./orchestrator.md) for the loading code.

For the software-construction template, the agents are:
- **implementer** (tier: worker) — code editing tools, web search
- **assessor** (tier: assessor) — Brutalist quality gate tools
- **verifier** (tier: verifier) — CDP browser tools, web search

This replaces the previous hardcoded dict. New templates define different agent sets with different tools appropriate to their domain.

### Agent Briefing

The coordinator's message to each spawned worker via the Agent tool's `prompt` parameter:

```
You are implementing `implement_auth` for milestone 'user-authentication', phase 'services'.

Read your protocol file: .clou/prompts/worker.md

Then read these files:
- .clou/milestones/user-authentication/compose.py — find your function
  signature `implement_auth`. Your inputs: UserModel. Your output: AuthService.
  Your criteria are in the docstring.
- .clou/milestones/user-authentication/phases/services/phase.md — phase context
- .clou/project.md — coding conventions

Write your results to:
- .clou/milestones/user-authentication/phases/services/execution.md

Write execution.md incrementally as you complete tasks. If you encounter
errors, write them with status, error details, and recommendation.
```

This follows inception prompting (§10): prompt engineering at initialization only — the worker works autonomously after.

## Communication: Stigmergy Only

### What Clou Uses

| Channel | Mechanism | Direction |
|---|---|---|
| Task specification | compose.py function signature (via briefing) | Coordinator → Worker |
| Narrative context | phase.md (via briefing) | Coordinator → Worker |
| Artifact exchange | Codebase (filesystem) | Worker → Worker (indirect) |
| Result record | execution.md (incremental writes) | Worker → Coordinator (via ASSESS read) |
| Lifecycle signal | SDK TaskNotificationMessage | Worker → Coordinator (real-time) |
| Circuit breaker | execution.md summary status line | Worker → Coordinator (EXECUTE read) |

### What Clou Does NOT Use

- **Mailbox system** (`~/.claude/<teamName>/inboxes/`) — not used. Stigmergy replaces all inter-agent messaging.
- **SendMessage** — not used. No inter-agent or coordinator→worker messages after initial briefing.
- **TaskCreate / TaskList / TaskUpdate** — not used. compose.py IS the task list. execution.md IS the result record. active/coordinator.md IS the progress tracker.
- **Team self-claiming** — not used. The coordinator spawns specific agents for specific tasks via Agent tool.
- **Polling** — not used. SDK TaskNotificationMessage provides event-driven completion signals.

**Why not:** §10 validates stigmergy (blackboard architectures outperform master-slave 13–57%). Adding direct messaging creates the coordination failure surface that accounts for 79% of multi-agent failures. compose.py already encodes dispatch order, concurrency, and dependencies — a separate task management layer would be redundant and could diverge from golden context (violating Principle 8).

## Team Lifecycle

### Per-EXECUTE-Cycle

Each EXECUTE cycle spawns a fresh team for the current phase's tasks. Workers are subprocesses of the coordinator session — when the session exits at cycle boundary, workers terminate.

- Rework EXECUTE cycles spawn fresh teams that read the updated execution.md
- No worker state persists across cycles (session-per-cycle, DB-03)
- Clean context per phase — no distractor accumulation from prior phases (§1)

### Dispatch Loop

```
1. Read compose.py — identify current phase's function(s)
2. Read phase.md — narrative context for agent briefings
3. Read active/coordinator.md — current phase position

4. For each task group in phase order:

   a. gather() group:
      - Spawn N agents in parallel (one per function)
      - Monitor TaskNotificationMessages
      - CIRCUIT BREAKER: If any member fails → abort remaining
        members, preserve execution.md, write checkpoint, exit
      - Collect all completion states

   b. Sequential task:
      - Spawn 1 agent
      - On completion: read execution.md summary status line (~10 tokens)
      - CIRCUIT BREAKER: If anomaly detected (failures, blockers) →
        write checkpoint, exit → ASSESS reads full state
      - If clean: proceed to next task

5. After all tasks complete:
   - Write active/coordinator.md checkpoint (phase status, next step: ASSESS)
   - Exit
```

The coordinator exercises zero judgment about output quality during EXECUTE. All evaluation happens in ASSESS.

## SDK Primitives Used

### TaskNotificationMessage

The primary lifecycle signal. Emitted when a worker completes, fails, or is stopped.

```python
# Fields used by Clou's dispatch loop:
task_id: str           # Maps to compose.py function name
status: str            # "completed" | "failed" | "stopped"
summary: str           # Worker-generated summary
usage: TaskUsage       # {total_tokens, tool_uses, duration_ms}
```

The coordinator receives these in its message stream. `completed` → proceed (with circuit breaker check for sequential tasks). `failed` or `stopped` → circuit breaker triggers.

### TaskStartedMessage / TaskProgressMessage

Used for monitoring but not for dispatch decisions. The orchestrator uses TaskProgressMessage for token tracking.

## Circuit Breakers

Two mechanical safety valves — pattern matching on structured fields, not quality evaluation.

### gather() Early Termination

- **Trigger:** TaskNotificationMessage with status `failed` or `stopped` from any gather() member
- **Action:** Abort remaining gather() members via SDK, preserve all execution.md entries
- **Rationale:** Remaining members are building against an invalidated foundation
- **Extends:** DB-05's crash handling to individual gather() members

### Sequential Status Check

- **Trigger:** After TaskNotificationMessage with status `completed`, read execution.md `## Summary` section
- **Check:** `failures: none` and `blockers: none`
- **If anomaly:** Write checkpoint with `next_step: ASSESS`, exit
- **If clean:** Dispatch next sequential task
- **Token cost:** ~15–30 tokens per check

## Crash Recovery (DB-05)

When a worker crashes during an EXECUTE cycle:
1. Orchestrator detects crash via SDK events
2. Orchestrator kills remaining team members
3. All `execution.md` entries written before the crash are preserved — workers MUST write execution.md incrementally
4. Orchestrator writes an `agent_team_crash` escalation to the supervisor
5. Supervisor informs the user
6. The coordinator loop exits — no silent retries

The rationale: crashes may indicate systemic issues (codebase too large for context, broken tools, environment problems) that require human awareness.

## Git Discipline

Agent teams write code via Write/Edit tools. They do **NOT** run `git commit`. The coordinator is the sole committer (DB-05). This ensures:
- All committed changes are reviewed by the coordinator
- Commits contain tractable deltas, not conversation artifacts
- Conflict resolution happens before commit, not after
- Git history is clean and revertable per-phase

## Team Size

Bounded by compose.py's structure. Typical phases have 1 task or 1 gather() group with 2–4 concurrent tasks. Maximum concurrent workers per phase = number of functions in the largest gather() group.

No artificial cap. The compose.py structure IS the cap. Design pressure toward smaller phases (DB-03: mid-cycle exhaustion signals phases are too large) naturally limits team size. Since Clou eliminates inter-agent coordination entirely (stigmergy only), coordination complexity is O(1) per task.

## Experimental Status Note

The Claude Agent SDK's Agent tool and team features are actively evolving. Clou's design is intentionally resilient to changes in the underlying implementation:
- Golden context and protocols work regardless of how agents are spawned
- Stigmergic coordination depends only on filesystem access, not on specific SDK features
- The dispatch loop's structure (compose.py → Agent tool → notifications) uses stable SDK primitives
