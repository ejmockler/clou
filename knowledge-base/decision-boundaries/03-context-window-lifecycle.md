# DB-03: Context Window Lifecycle

**Status:** DECIDED
**Severity:** High — affects system reliability
**Question:** How do the supervisor and coordinator handle context window exhaustion and session crashes?

**Decision:** Session-per-cycle. Each coordinator cycle is a fresh `ClaudeSDKClient` session. The golden context is the sole compaction mechanism — no reliance on SDK context compression for inter-cycle state transfer. Edge cases where a single cycle exceeds context are handled by mid-cycle checkpoint and restart, using the same golden context read-forward protocol.

## The Design

### Core Principle: Golden Context as Compaction

The golden context serves four unified roles:
1. **Human-legibility surface** — the user can read `.clou/` at any time
2. **Crash recovery mechanism** — any session can restart from golden context state
3. **Inter-cycle state transfer** — the ONLY way state moves between cycles
4. **Compaction** — reading golden context fresh IS the context reset

There is no separate compaction strategy. No reliance on SDK automatic compression for design-level correctness. The SDK may compress within a cycle as a safety net, but Clou's architecture assumes each cycle starts with a clean context window populated solely from golden context files.

### Session-per-Cycle Model

```
Orchestrator reads active/coordinator.md
    → determines cycle type (PLAN, EXECUTE, ASSESS, VERIFY, EXIT)
    → constructs targeted prompt with file pointers
    → spawns fresh ClaudeSDKClient session
    → session reads golden context files
    → session runs one cycle
    → session writes updated state to golden context
    → session exits
    → orchestrator reads active/coordinator.md again
    → spawns next session (or terminates if milestone complete)
```

Each cycle is autonomous. The session receives a prompt telling it what to do and what to read. It reads the golden context, does its work, writes results back, and exits. No conversational continuity between cycles.

### Why Not Long-Running Sessions

- Context compression may silently lose critical reasoning (Brutalist feedback, escalation analysis, type-contract evaluations)
- Context exhaustion timing is unpredictable — depends on codebase size, feedback volume, phase complexity
- Crash during a long-running session loses all in-progress state not yet checkpointed
- Session-per-cycle makes crash recovery trivial: if a session dies, restart the same cycle
- The golden context was designed for exactly this — externalized state that makes any session resumable

## Write-Back Protocol

How state propagates to golden context during and after a cycle.

### During a Cycle

Agent teams write `execution.md` as they complete tasks. This happens in real-time — teammates update their phase's `execution.md` with task status, files changed, and results. This is the only golden context write that happens mid-cycle by non-coordinator agents.

### At Cycle Boundary

The coordinator writes at the end of every cycle, before exit:

| File | What's Written | Purpose |
|---|---|---|
| `active/coordinator.md` | Position, phase status, cycle count, next step | Inter-cycle pointer — tells the next session where to pick up |
| `decisions.md` | New cycle group prepended at top (newest-first) | Judgment log — accumulates across cycles, most recent at top |
| `status.md` | Phase completion updates | Supervisor visibility into progress |
| `compose.py` | Only if the plan was revised (rare, after rework) | Plan artifact — usually written once in PLAN cycle |

The write-back is the coordinator's last act before exiting. The orchestrator confirms the session has terminated before spawning the next one.

### Atomicity

The coordinator writes all golden context updates before signaling exit. If the session crashes before completing writes, the golden context reflects the state at the *previous* cycle boundary — the orchestrator detects this (active/coordinator.md hasn't advanced) and restarts the same cycle.

## Read-Forward Protocol

How the next cycle ingests what it needs from golden context.

### Orchestrator's Role

The orchestrator is a lifecycle manager, not a context synthesizer. Between cycles:

1. Reads `active/coordinator.md` to determine current state
2. Determines what the next cycle should do (based on `current_step`)
3. Constructs a targeted prompt with pointers to the specific golden context files needed
4. Spawns a fresh session with that prompt

The orchestrator does NOT inject golden context content into the prompt. It provides pointers — the coordinator reads the files itself. This keeps the orchestrator decoupled from golden context schemas.

### Per-Cycle-Type Read Sets

The coordinator only reads what it needs for its current cycle type:

**PLAN cycle:**
```
Read:
- milestone.md, requirements.md (scope)
- project.md (conventions, constraints)
- active/coordinator.md (if resuming a failed PLAN)
Produces: compose.py, phase.md files, active/coordinator.md
```

**EXECUTE cycle:**
```
Read:
- compose.py (plan)
- current phase's phase.md (scope for agent teams)
- active/coordinator.md (which phase is current)
Produces: agent team output in execution.md, active/coordinator.md
```

**ASSESS cycle:**
```
Read:
- compose.py (type contracts to check against)
- current phase's execution.md (what was produced)
- requirements.md (acceptance criteria)
- decisions.md (prior judgments for continuity)
- active/coordinator.md (cycle count, phase status)
Produces: decisions.md entries, rework tasks or phase advancement, active/coordinator.md
```

**VERIFY cycle:**
```
Read:
- milestone.md, requirements.md (acceptance criteria)
- compose.py (verify() function)
- all phases' execution.md summaries (what to verify)
- active/coordinator.md (phase status)
Mid-cycle read: verification/execution.md + artifacts/ (perceptual record, after verifier completes)
Produces: verification/execution.md, verification/artifacts/, handoff.md,
          decisions.md entries (Brutalist experience assessment), active/coordinator.md
```

**EXIT cycle:**
```
Read:
- handoff.md (verify it's complete)
- active/coordinator.md (all exit conditions)
- decisions.md (audit completeness)
Produces: final status.md, active/coordinator.md (terminal state)
```

### What Is NOT Re-Read

- Completed phases' full `execution.md` (only current phase or verification summary)
- Prior cycles' Brutalist feedback (captured in `decisions.md`)
- Inter-agent message history (ephemeral, captured in `execution.md` results)
- Source code files (agent teams handle that; coordinator manages by exception)

## Edge Case: Mid-Cycle Context Exhaustion

A single cycle may exceed context if:
- An ASSESS cycle has extensive Brutalist feedback across many files
- A VERIFY cycle walks many golden paths with detailed results
- An EXECUTE cycle coordinates many concurrent agent teams with complex status

### Detection

The orchestrator monitors `ResultMessage.usage.input_tokens` per message. If token usage exceeds a threshold (configurable, default 75% of model context limit), the orchestrator signals the coordinator to checkpoint and exit.

### Handling

The coordinator writes a mid-cycle checkpoint to `active/coordinator.md` with partial progress:

```markdown
## Cycle
current_cycle: 3
current_step: ASSESS (partial)
assessed_tasks: [T1, T2, T3]
remaining_tasks: [T4, T5]
```

The orchestrator then spawns a new session for the *same* cycle type, but with the partial checkpoint indicating where to resume. The golden context read-forward protocol handles this identically to a normal cycle start — the new session reads active/coordinator.md and picks up from the partial state.

### Design Pressure

Mid-cycle exhaustion is a signal that phases are too large. The right response is usually to decompose phases into smaller units (more functions in `compose.py`, more `gather()` groups) rather than to optimize the compaction mechanism. Clou's architecture exerts pressure toward smaller, well-scoped tasks — which is also better for agent team parallelism.

## Supervisor Lifecycle

The supervisor has a different lifecycle. It is not "always running" — it is **always resumable**.

### Recovery Flow

1. User starts a new Claude session in the project directory
2. The orchestrator loads the supervisor system prompt from `.clou/prompts/supervisor-system.xml`
3. The orchestrator checks for `active/supervisor.md`
4. If checkpoint exists: prompt includes "Resume from checkpoint. Read `.clou/active/supervisor.md` and `.clou/roadmap.md` to reconstruct your state."
5. If no checkpoint: prompt includes "Read `.clou/project.md` for context. Greet the user."
6. The supervisor reads golden context and reconstructs its state

The illusion of persistence is created by the golden context. The supervisor's state is simple enough (current milestone, open escalations, roadmap position) that reconstruction from checkpoint is near-instantaneous.

### Supervisor vs. Coordinator Lifecycle Differences

| Property | Supervisor | Coordinator |
|---|---|---|
| Lifecycle driver | User interaction (interactive) | Orchestrator cycle loop (autonomous) |
| Session count | One at a time, restarted on crash | One per cycle, many per milestone |
| Context growth | Slow (strategic reasoning only) | Fast (implementation detail each cycle) |
| Compaction trigger | Session restart (user closes terminal, crash) | Every cycle boundary (by design) |
| State complexity | Low (position, escalations) | High (phase status, assessments, decisions) |

## Checkpoint Schema

### `active/supervisor.md`

```markdown
# Supervisor Checkpoint

## Position
active_milestone: milestone-name | none
roadmap_index: 2
last_action: evaluated-milestone | created-milestone | resolved-escalation
last_action_at: 2026-03-19T04:30:00Z

## Open Escalations
- milestone-name/20260319-041500-stripe-creds: degraded (credential_request)
- milestone-name/20260319-042000-api-conflict: blocking (conflict)

## Pending
- requests.md has 2 unprocessed entries (added since last check)
```

### `active/coordinator.md`

```markdown
# Coordinator Checkpoint

## Milestone
name: user-authentication
started_at: 2026-03-19T04:00:00Z

## Cycle
current_cycle: 3
current_step: ASSESS
next_step: EXECUTE (rework) | VERIFY | EXIT

## Phase Status
- phase-1-schema: completed (cycle 1)
- phase-2-core-logic: completed (cycle 2)
- phase-3-api-layer: in_progress (current)
- phase-4-frontend: pending
- verification: pending

## Blocked
- phase-4-frontend/task-stripe-integration: blocked on credential_request

## Brutalist Assessment
last_run: cycle 3
findings: 2 accepted, 1 overridden (see decisions.md)
rework_needed: false

## Partial Progress
(only present if mid-cycle checkpoint)
assessed_tasks: [T1, T2, T3]
remaining_tasks: [T4, T5]
```

The checkpoint is a pointer, not a summary. It tells the next session *where* to look in the golden context, not *what* the golden context says. The reasoning is in `decisions.md`. The results are in `execution.md`. The plan is in `compose.py`. The checkpoint just says which cycle, which step, which phase.

## Resolved Questions

- [x] **Lifecycle model:** Session-per-cycle. Golden context is the sole compaction mechanism.
- [x] **Context exhaustion:** Orchestrator monitors tokens. Mid-cycle exhaustion triggers checkpoint and restart of the same cycle. Design pressure toward smaller phases.
- [x] **Checkpoint schema:** Pointer-based. Position, phase status, partial progress. Reasoning lives in other golden context files.
- [x] **Supervisor recovery:** Always-resumable via golden context. Orchestrator checks for checkpoint on startup.
- [x] **SDK `resumeSession`:** Not relied upon. Session-per-cycle eliminates the need for session persistence entirely.

## Cascading Effects

- **DB-04 (Prompt System):** The orchestrator constructs a per-cycle-type prompt with targeted file pointers. Prompt architecture must support this parameterization.
- **DB-05 (Error Recovery):** Coordinator crash recovery is trivial — restart the same cycle. The golden context already has the state.
- **DB-06 (Token Economics):** Session-per-cycle has startup overhead per cycle (subprocess launch, context loading). Cost model must account for this.
- **DB-08 (File Schemas):** `active/coordinator.md` schema is now defined. Must support partial progress for mid-cycle checkpoints.
- **DB-10 (Team Communication):** Agent teams write `execution.md` during the cycle. The coordinator reads the full execution.md in the ASSESS cycle (different session). During EXECUTE, the coordinator reads only the summary status line (circuit breaker — ~15-30 tokens) before dispatching dependent tasks. DB-10 decided: mechanical dispatch, stigmergy only, one agent per function.
