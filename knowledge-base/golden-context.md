# Golden Context (`.clou/`)

The `.clou/` directory is the system of record for all Clou planning state. It is checked into the repository. It survives session crashes, context window exhaustion, and coordinator restarts. Every tier reads it; each tier writes only to files it owns.

## File Tree

```
.clou/
├── project.md                          # [supervisor writes]
├── roadmap.md                          # [supervisor writes]
├── requests.md                         # [user appends, supervisor reads]
│
├── prompts/                            # [orchestrator reads, developer maintains]
│   ├── supervisor-system.xml           # Supervisor system prompt template (identity + invariants)
│   ├── supervisor.md                   # Supervisor protocol (agent reads on startup)
│   ├── coordinator-system.xml          # Coordinator system prompt template ({{milestone}})
│   ├── coordinator-plan.md             # PLAN cycle protocol (agent reads)
│   ├── coordinator-execute.md          # EXECUTE cycle protocol
│   ├── coordinator-assess.md           # ASSESS cycle protocol
│   ├── coordinator-verify.md           # VERIFY cycle protocol
│   ├── coordinator-exit.md             # EXIT cycle protocol
│   ├── worker-system.xml               # Worker system prompt template ({{milestone}}, {{phase}})
│   ├── worker.md                       # Worker protocol (agent reads)
│   ├── verifier-system.xml             # Verifier system prompt template ({{milestone}})
│   └── verifier.md                     # Verifier protocol (agent reads)
│
├── services/                           # [project-level, reusable across milestones]
│   └── <service-name>/
│       ├── setup.md                    # [agent writes]
│       ├── status.md                   # [agent writes, user confirms]
│       └── .env.example                # [agent writes]
│
├── milestones/
│   └── <milestone-name>/
│       ├── milestone.md                # [supervisor writes, immutable after handoff]
│       ├── status.md                   # [coordinator writes]
│       ├── requirements.md             # [supervisor writes]
│       ├── compose.py                  # [coordinator writes] — typed-function call graph
│       ├── decisions.md                # [coordinator writes]
│       ├── handoff.md                  # [verification agent writes]
│       ├── escalations/
│       │   └── <timestamp>-<slug>.md   # [coordinator writes, supervisor resolves]
│       │
│       └── phases/
│           ├── <implementation-phase>/
│           │   ├── phase.md            # [coordinator writes]
│           │   └── execution.md        # [agent team writes]
│           │
│           └── verification/           # [always the final phase]
│               ├── phase.md            # [coordinator writes]
│               ├── execution.md        # [verification agent writes]
│               └── artifacts/          # [verification agent writes]
│                   └── *.png, *.json   # Raw perception captures (DB-09)
│
└── active/
    ├── supervisor.md                   # [supervisor checkpoint]
    └── coordinator.md                  # [coordinator checkpoint]
```

## File Purposes

### Top-Level (Supervisor-Owned)

#### `project.md`
The single source of truth for project-level everything. Vision, scope, constraints, architectural principles, tech stack decisions, global patterns. This replaces a separate top-level `requirements.md` — project-wide constraints live here because they are part of the project definition.

**Written by:** Supervisor
**Read by:** All tiers
**Updated:** When the user changes project scope, constraints, or direction

#### `roadmap.md`
Ordered list of milestones. Sequential dependencies by default (each depends on the previous). The structure must support future independence annotations for parallel coordinators.

**Written by:** Supervisor
**Read by:** Supervisor (for sequencing), Coordinator (for context)
**Updated:** When milestones are added, reordered, or completed
**Schema:** See [DB-08: File Schemas](./decision-boundaries/08-file-schemas.md)

#### `requests.md`
Raw user input: feature requests, change requests, feedback, priorities. The user's voice in the system. The supervisor reads and processes it into roadmap and milestone changes.

**Written by:** User (directly or through supervisor conversation)
**Read by:** Supervisor
**Updated:** Anytime — the user can append at will
**Distinct from:** `requirements.md` (which is the supervisor's processed output per milestone)

### Prompts (Two-Layer Architecture)

Clou uses a two-layer prompt architecture (DB-04, decided). Each tier has a **system prompt template** (small, identity + invariants, loaded by orchestrator into `system_prompt`) and a **protocol file** (full behavioral specification, read by the agent as its first action). This is grounded in research: system prompts have no architectural privilege over read content, instruction density degrades past a threshold, and decomposition outperforms monolithic prompts. See [Research Foundations](./research-foundations.md) §5–7 and [DB-04](./decision-boundaries/04-prompt-system-architecture.md).

#### System Prompt Templates (`*-system.xml`)

Small XML-structured templates (~800–2,000 tokens) containing identity anchor, critical invariants, and pointer to protocol file. Loaded by the orchestrator via `ClaudeAgentOptions.system_prompt`. Parameterized with `{{milestone}}`, `{{phase}}`, etc.

- `supervisor-system.xml` — ~1,500–2,000 tokens
- `coordinator-system.xml` — ~800–1,200 tokens (parameterized with `{{milestone}}`)
- `worker-system.xml` — ~400–600 tokens (parameterized with `{{milestone}}`, `{{phase}}`)
- `verifier-system.xml` — ~600–800 tokens (parameterized with `{{milestone}}`)

**Maintained by:** Developer (part of Clou's core engineering)
**Read by:** Orchestrator (at session creation)

#### Protocol Files (`*.md`)

Full behavioral specifications that agents read during execution. The coordinator has per-cycle-type protocol files — each session reads exactly one.

- `supervisor.md` — Full supervisor protocol (read on startup)
- `coordinator-plan.md` — PLAN cycle: read requirements, write compose.py + phase specs
- `coordinator-execute.md` — EXECUTE cycle: dispatch agent teams, monitor
- `coordinator-assess.md` — ASSESS cycle: evaluate results, invoke Brutalist, decide
- `coordinator-verify.md` — VERIFY cycle: dispatch verification agent
- `coordinator-exit.md` — EXIT cycle: evaluate handoff, write final status, exit
- `worker.md` — Agent team member protocol
- `verifier.md` — Verification agent protocol (three stages + handoff schema)

**Maintained by:** Developer
**Read by:** Agents (as first action in each session/cycle)

### Services (Project-Level)

#### `services/<service-name>/setup.md`
Precise, project-specific step-by-step setup guide for a third-party service. Not generic documentation — tailored to exactly what this project needs from this service. Includes verification commands.

**Written by:** Coordinator's agent (during planning phase when service dependency is discovered)
**Read by:** User (to follow setup steps)

#### `services/<service-name>/status.md`
Current configuration state of the service. Tracks whether credentials are provided, verified, and working.

**Written by:** Agent (initial creation), User (confirmation after setup)
**Read by:** Coordinator (to determine if service-dependent tasks can proceed)

#### `services/<service-name>/.env.example`
Expected environment variables with placeholder names but no values. The template for what the user needs to provide.

**Written by:** Agent
**Read by:** User

### Milestones

#### `milestones/<name>/milestone.md`
The milestone specification. Contains:
- What is being built (product terms)
- Why it matters (context from project.md)
- Scope boundaries (in/out)
- Delegated authority (what the coordinator can decide autonomously)
- Acceptance criteria (how completion is judged)

**Written by:** Supervisor (immutable after handoff)
**Read by:** Coordinator (during PLAN cycle as primary directive)

#### `milestones/<name>/status.md`
The coordinator's progress journal. Contains:
- Current phase and cycle number
- Phase progress table (status and summary per phase)
- Timestamped progress notes

**Written by:** Coordinator (updated at every cycle boundary)
**Read by:** Coordinator (during EXECUTE, ASSESS, VERIFY, EXIT cycles), Supervisor (for progress visibility)

**Distinct from `active/coordinator.md`:** The checkpoint is a machine-oriented pointer (what cycle to run next). `status.md` is a human-readable progress journal (what happened). The checkpoint is deleted when the milestone completes; `status.md` persists as part of the milestone record. See [DB-07](./decision-boundaries/07-milestone-ownership.md).

#### `milestones/<name>/requirements.md`
The scoped contract between supervisor and coordinator:
- Functional requirements
- Non-functional requirements (performance, accessibility)
- Integration requirements (services, APIs)
- Constraints (tech stack, patterns)

**Written by:** Supervisor
**Read by:** Coordinator, Verification agent

#### `milestones/<name>/decisions.md`
The coordinator's judgment log. Every time the coordinator:
- Overrides Brutalist feedback (with reasoning)
- Makes a non-obvious tradeoff
- Exercises delegated authority on an edge case

Entries are grouped by cycle (`## Cycle N`) and ordered newest-first — most recent cycle at top, aligning the attention sink (§2) with the most relevant content for the ASSESS coordinator. Two entry types: Brutalist Assessment (accepted/overridden findings) and Coordinator Judgment (tradeoffs, authority edge cases). See [DB-08](./decision-boundaries/08-file-schemas.md) for the full schema.

**Written by:** Coordinator (prepends new cycle groups at top)
**Read by:** Coordinator (ASSESS for continuity, EXIT for audit completeness), Supervisor (for milestone evaluation)

#### `milestones/<name>/handoff.md`
The prepared handoff from agent to human. Written by the verification agent after successfully walking golden paths. Includes:
- Running environment with URLs and startup commands
- What was built (user-perspective description)
- Walk-through steps for each verified flow
- Third-party service states
- What the agent verified
- Known limitations
- What to look for (subjective quality the agent couldn't assess)

**Written by:** Verification agent
**Read by:** Supervisor (for completion evaluation), User (for testing)

#### `milestones/<name>/escalations/<timestamp>-<slug>.md`
Structured escalation from coordinator to supervisor. See [Escalation Protocol](./protocols/escalation.md) for full schema.

**Written by:** Coordinator
**Resolved by:** Supervisor (fills in Disposition section)

### Phases

#### `phases/<phase-name>/phase.md`
Phase specification. What work this phase covers, scoped from the coordinator's task decomposition.

**Written by:** Coordinator
**Read by:** Agent teams

#### `milestones/<name>/compose.py`
The typed-function call graph for the entire milestone. The coordinator writes this once after reading `milestone.md` and `requirements.md`. Each task is a Python async function with typed parameters (inputs/dependencies), return type (output artifact), and a docstring (description + success criteria). The `execute()` function composes all tasks — sequential steps, `gather()` for concurrency, arguments for data flow. Phases are marked with comments within `execute()`.

The orchestrator validates `compose.py` via AST parsing (cycle detection, type compatibility, completeness). See [DB-02](./decision-boundaries/02-task-dag-implementation.md) for the full decision.

**Written by:** Coordinator
**Read by:** Coordinator (to drive phase execution), Agent teams (for task context), Orchestrator (for validation)
**Validated by:** Orchestrator PostToolUse hook using `graph.py`

#### `phases/<phase-name>/execution.md`
Results, status, and output from agent team execution. Front-loaded `## Summary` with task counts, failures, and blockers (attention sink aligned with actionable content for ASSESS). Single `## Tasks` section in execution order — supports incremental writes and all task states (pending, in_progress, completed, failed). See [DB-08](./decision-boundaries/08-file-schemas.md) for the full schema.

**Written by:** Agent teams (incrementally during execution — workers MUST write the `## Summary` section first for circuit breaker reads)
**Read by:** Coordinator — summary status line during EXECUTE (circuit breaker, ~15-30 tokens), full file during ASSESS (quality evaluation)

### Active (Inter-Cycle State Transfer)

The `active/` directory holds checkpoint files that serve as the sole state transfer mechanism between sessions. Each coordinator cycle is a fresh session — these checkpoints are how the orchestrator knows what to do next and how the new session knows where to pick up.

Golden context checkpoints unify four roles:
1. **Human-legibility surface** — the user can inspect current state
2. **Crash recovery** — restart the same cycle if a session dies
3. **Inter-cycle state transfer** — the only way state moves between cycles
4. **Compaction** — reading golden context fresh IS the context reset

#### `active/supervisor.md`
Supervisor's checkpoint state:
- Current milestone (which one is active)
- Loop position (what step the supervisor was on)
- Last evaluation result
- Open escalation count

**Written by:** Supervisor (at each loop boundary)
**Read by:** Orchestrator (to construct supervisor resume prompt), Supervisor (to reconstruct state)

#### `active/coordinator.md`
Coordinator's checkpoint state — a pointer, not a summary:
- Cycle count and current step (PLAN, EXECUTE, ASSESS, VERIFY, EXIT)
- Next step (what the next cycle should do)
- Phase status (which are complete, in-progress, pending, blocked)
- Partial progress (only if mid-cycle checkpoint due to context exhaustion)

The checkpoint tells the orchestrator *what cycle to run next* and tells the new session *where to look* in the golden context. The reasoning is in `decisions.md`. The results are in `execution.md`. The plan is in `compose.py`.

**Written by:** Coordinator (at every cycle boundary, before session exit)
**Read by:** Orchestrator (to determine next cycle type and construct prompt), Coordinator (to reconstruct state)

## Ownership Rules

| Tier | Writes | Reads |
|---|---|---|
| User | `requests.md`, credential confirmations in `services/*/status.md` | Anything (via supervisor conversation) |
| Supervisor | `project.md`, `roadmap.md`, `requests.md` (processing), milestone creation (`milestone.md`, `requirements.md`), escalation dispositions, `active/supervisor.md` | Everything |
| Coordinator | `compose.py`, `status.md`, `decisions.md`, `escalations/`, phase directories (`phase.md`), `active/coordinator.md` | Everything in its milestone directory + top-level project files |
| Agent Teams | `execution.md` within assigned phase | Phase spec + codebase |
| Verification Agent | `execution.md` in verification phase, `handoff.md` | Milestone spec + requirements + running environment |

## Structural Validation

The orchestrator validates golden context structure at cycle boundaries — after the coordinator session exits, before spawning the next cycle. Validation checks form, not content:

- Required files exist for the cycle that just completed
- Required markdown sections are present (e.g., `active/coordinator.md` has `## Cycle`, `## Phase Status`)
- No structural corruption (unclosed sections, missing required fields)

If validation fails: git-revert golden context files to pre-cycle state, restart the same cycle with error feedback, escalate to supervisor after 3 consecutive failures. See [DB-05](./decision-boundaries/05-error-recovery.md) for the full protocol.

## Update Timing

- **Reads** flow downward freely.
- **Writes** flow laterally or downward only. Nobody writes upward.
- **Coordinator writes at cycle boundaries** — each cycle is a fresh session; all state is written to golden context before exit.
- **Structural validation at cycle boundaries** — the orchestrator validates golden context structure after each coordinator cycle.
- **Agent teams write `execution.md` during the cycle** — the only mid-cycle golden context write by non-coordinator agents. `execution.md` is written incrementally (not only at completion) so that crash recovery can preserve partial work.
- **Coordinator-only commits at phase completion.** Agent teams write code but do not commit. The coordinator reviews changes and commits tractable deltas at phase completion.
- **`milestone.md` is immutable after handoff** — supervisor creates it, coordinator reads but never writes to it. Coordinator progress goes in `status.md`. See [DB-07](./decision-boundaries/07-milestone-ownership.md).
