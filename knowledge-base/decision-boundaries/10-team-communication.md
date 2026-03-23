# DB-10: Team Communication

**Status:** DECIDED
**Decided:** 2026-03-19
**Severity:** Medium — affects coordinator↔agent team interaction quality
**Question:** What message formats, task assignment patterns, and completion signals do agent teams use?

## Decisions

1. **EXECUTE is a mechanical dispatch loop with circuit breakers.** The coordinator during EXECUTE exercises zero judgment about output quality. It walks compose.py's call graph, spawns one agent per function, gates sequential dispatch on SDK notifications, and uses two circuit breakers: (a) gather() early termination on member failure, (b) execution.md summary status check before dependent dispatch. All quality evaluation happens in ASSESS.

2. **Team lifecycle: per-EXECUTE-cycle.** Each EXECUTE cycle spawns a fresh team for the current phase's tasks. Teammates exist within the coordinator's session — when the session exits at cycle boundary, the team ends. Rework EXECUTE cycles spawn fresh teams that read the updated execution.md.

3. **One agent per compose.py function.** Each function signature gets its own agent. `gather()` tasks spawn in parallel. Sequential tasks spawn serially, gated by SDK `TaskNotificationMessage`. The worker system prompt (DB-04) says "your function signature" — singular. The constant fabric (DB-02) shapes every interaction: typed input → work → typed output.

4. **Stigmergic coordination only.** No SDK SendMessage, mailbox, or TaskCreate. Workers coordinate through the filesystem — they write code to the codebase and results to execution.md. The codebase IS the inter-agent communication channel. Typed dependencies in compose.py mean the second agent finds the first agent's artifact via its function signature's argument types.

5. **SDK notifications for lifecycle signals, execution.md for durable record.** `TaskNotificationMessage` (completed/failed/stopped) is the real-time signal the coordinator uses for dispatch gating and circuit breaker triggering. execution.md is the durable record the ASSESS cycle reads for evaluation.

6. **Agent briefing via Agent tool prompt.** The coordinator's message to each spawned worker combines function-specific context with protocol pointers. No mailbox. No task list. compose.py IS the task list.

## Research Basis

This decision was resolved through structured debate weighing empirical findings against architectural constraints. The positions tested: purely mechanical dispatch (zero coordination) vs. adaptive coordination capacity (lightweight observation during EXECUTE). The synthesis identified where each position wins.

### Why Mechanical Dispatch

**§10 (Multi-agent failure modes):** 79% of multi-agent failures are specification/coordination, not capability. Blackboard architectures outperform master-slave 13–57%. Stigmergy = file-system-as-communication — listed under what works. What fails: free-form chat between agents (hallucination amplification), self-reflection without external validation. The coordination mechanisms that adaptive capacity would add — coordinator interpretation, mid-execution reasoning, inter-agent messaging — ARE the failure modes the research identifies.

**§7 (Decomposition):** Session-per-cycle separates cognitive intents: PLAN = decomposition judgment, EXECUTE = dispatch, ASSESS = evaluation judgment. Each cycle type has one cognitive intent. The modular structure itself drives improvement (DecomP, ICLR 2023). Mixing dispatch and evaluation in EXECUTE splits semantic reasoning across cycles.

**§1 (Context is adversarial):** Every token degrades performance (13.9–85% drop even with perfect retrieval, Gao et al. EMNLP 2025). A thin EXECUTE cycle minimizes token usage. A coordinator that reads execution.md deeply during EXECUTE, evaluates partial results, and makes rework decisions is doing ASSESS's job — duplicating context and degrading its own dispatch performance.

**§4 (Observation masking > summarization):** JetBrains 2025 — 2.6% higher solve rate at 52% lower cost. Workers write execution.md as structured observation records. ASSESS reads them directly. Coordinator-interpreted summaries relayed between sessions are lower fidelity than the artifacts themselves.

### Why Circuit Breakers

**§9 (Plans fail):** Only 12% of LLM-generated plans are executable as-is (Kambhampati ICML 2024). compose.py is human-supervised, so better than baseline, but the gap between plan types and runtime artifacts is inherent. A dispatch loop without feedback is an open-loop controller — it works only when the plant model is perfect.

**§10 (Blackboard should be read):** execution.md IS the blackboard. Building the blackboard and then not reading it during the phase when reading would catch cascading failures converts the architecture from blackboard to master-slave — which §10 says underperforms by 13–57%. The circuit breaker reads a single structured status field from the blackboard at structurally defined moments (between task completions), converting EXECUTE from master-slave to blackboard-with-gating.

**Token cost is negligible:** ~15–30 tokens for a summary status line against an 800+ token read set is a 2–3% increase. §1's degradation curves operate across orders of magnitude, not from 30 additional tokens.

### What Was Rejected

**Dispatch observations to active/coordinator.md** — ASSESS reads execution.md directly. Worker-written structured artifacts are higher fidelity than coordinator-interpreted observations (§4). Adding coordinator observations creates a second representation that can diverge.

**Sequential chain consolidation (one agent for a chain)** — This is a PLAN concern. If two sequential tasks are semantically unified, they should be one function in compose.py. The fix is in the composition, not in the dispatch.

**Inter-agent communication** — No SendMessage, no mailbox, no peer awareness. §10 validates stigmergy. compose.py guarantees independence within concurrent sets. Sequential tasks have explicit typed dependencies mediated by the filesystem.

**SDK TaskCreate/TaskList** — compose.py IS the task list. execution.md IS the result record. active/coordinator.md IS the progress tracker. Adding SDK task management creates a parallel state layer that could diverge from golden context, violating Principle 8.

## The Dispatch Protocol

The coordinator-execute.md protocol file (~400–500 tokens) specifies the EXECUTE cycle's full behavior:

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

### Circuit Breaker Specification

The circuit breaker is mechanical — pattern matching on structured fields, not quality evaluation.

**gather() early termination:**
- Trigger: `TaskNotificationMessage` with status `failed` or `stopped` from any gather() member
- Action: abort remaining gather() members via SDK, preserve all execution.md entries
- Rationale: remaining members are building against an invalidated foundation — their compute is wasted
- This extends DB-05's crash handling to individual gather() members

**Sequential status check:**
- Trigger: after `TaskNotificationMessage` with status `completed`, read execution.md `## Summary` section (~1 line: `failures:` and `blockers:` fields)
- Check: `failures: none` and `blockers: none`
- If anomaly: write checkpoint with `next_step: ASSESS`, exit — ASSESS evaluates the full picture
- If clean: dispatch next sequential task
- Token cost: ~15–30 tokens per check
- This catches the gap between "process completed" and "output is viable" before dispatching dependent tasks

The coordinator does NOT:
- Read full execution.md during EXECUTE (that's ASSESS)
- Evaluate output quality (that's ASSESS + Brutalist)
- Make rework decisions (that's ASSESS)
- Send messages to workers (stigmergy only)
- Interpret or relay worker state (§4: observation masking > summarization)

## Agent Briefing

The coordinator's message to each spawned worker via the Agent tool's prompt parameter:

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

This follows inception prompting (§10): "carefully crafted initialization prompt that assigns roles... prompt engineering occurs only at initialization — agents prompt each other autonomously after." The worker system prompt (DB-04) provides identity + invariants. The briefing provides instance-specific context. After initialization, the worker works autonomously from compose.py + phase.md.

## The Inter-Tier Communication Stack

| Channel | Mechanism | Content | Direction |
|---|---|---|---|
| Task specification | compose.py function signatures | Typed inputs, outputs, criteria | Coordinator → Worker (via briefing) |
| Narrative context | phase.md | Phase scope, domain context | Coordinator → Worker (via briefing) |
| Coding conventions | project.md | Style, patterns, constraints | Coordinator → Worker (via briefing) |
| Worker protocol | worker.md | Behavioral specification | Orchestrator → Worker (via system prompt pointer) |
| Artifact channel | Codebase (filesystem) | Code, tests, configs | Worker → Worker (stigmergic) |
| Result record | execution.md | Status, files changed, errors | Worker → Coordinator (via ASSESS read) |
| Lifecycle signal | SDK TaskNotificationMessage | completed/failed/stopped + usage | Worker → Coordinator (real-time) |
| Circuit breaker | execution.md summary line | failures/blockers fields | Worker → Coordinator (EXECUTE read) |
| Dispatch state | active/coordinator.md | Phase position, next step | Coordinator → Coordinator (inter-cycle) |

No mailbox. No SendMessage. No TaskCreate. No inter-worker communication. The filesystem mediates everything.

## Error Reporting Format

Workers report errors in execution.md per the DB-08 schema:

```markdown
### T3: Implement payment webhook
**Status:** failed
**Error:** Stripe webhook signature verification fails with test mode keys.
  The signing secret in .env.local appears to be from a previous CLI session.
**Attempted:** Verified key format, tested with direct API call (works),
  webhook endpoint receives but can't verify signature.
**Recommendation:** User should restart `stripe listen` and update
  STRIPE_WEBHOOK_SECRET in .env.local with the new signing secret.
**Files changed:** src/webhooks/stripe.ts (partial — handler works,
  signature verification untested)
```

The ASSESS coordinator reads this and decides:
- Credential issue → file `credential_request` escalation
- Code issue → create rework task for next EXECUTE cycle
- Scope issue → escalate to supervisor
- Brutalist finding → log in decisions.md with reasoning

Error triage is ASSESS's cognitive intent, not EXECUTE's.

## Team Size

Bounded by phase task count — compose.py defines this. Typical phases have 1 task or 1 gather() group with 2–4 concurrent tasks. Maximum concurrent teammates per phase is the number of functions in the largest gather() group.

No artificial cap. The compose.py structure IS the cap. If a gather() group has 7 concurrent tasks, that's 7 agents. The research (§10) suggests coordination failures increase with agent count, but Clou's design eliminates inter-agent coordination entirely (stigmergy only, no peer communication). The coordination complexity is O(1) per task — the coordinator spawns it and monitors its notification.

Design pressure toward smaller phases (DB-03: mid-cycle exhaustion signals phases are too large) naturally limits team size.

## Prior Resolution Summary

Most of DB-10's original questions were resolved by cascading effects from DB-01 through DB-09:

| Original Question | Resolved By | Resolution |
|---|---|---|
| Communication medium | DB-03, §10 | Golden context as blackboard. execution.md sole durable artifact |
| Task specification format | DB-02 | compose.py typed-function signatures |
| Crash recovery signaling | DB-05 | Kill, preserve execution.md, escalate |
| Commit discipline | DB-05 | Coordinator-only commits |
| execution.md schema | DB-08 | Front-loaded summary + tasks in execution order |
| Verifier→coordinator channel | DB-09 | Perceptual record (verification/execution.md + artifacts/) |
| Write boundaries | DB-01 | PreToolUse hooks enforce ownership map |
| Inter-worker communication | DB-02 | compose.py guarantees independence within gather() sets |

## Resolved Questions

- [x] Choose team scope model → per-EXECUTE-cycle (structurally determined by DB-03)
- [x] Define task assignment protocol → compose.py function signatures via Agent tool briefing
- [x] Define completion signaling → SDK TaskNotificationMessage (real-time) + execution.md (durable)
- [x] Define teammate error reporting format → execution.md per DB-08 schema
- [x] Define coordinator message templates → Agent briefing template (above)
- [x] Determine maximum team size → bounded by compose.py gather() group size, no artificial cap

## Cascading Effects

- **coordinator-execute.md protocol file:** Must encode the dispatch loop with circuit breakers as specified above. ~400–500 tokens.
- **Orchestrator `is_agent_team_crash()`:** Must detect individual gather() member failures (not just full team crashes) to support the early termination circuit breaker.
- **Worker protocol (worker.md):** Must instruct workers to write execution.md summary section FIRST (before task details) so the circuit breaker can read it immediately on task completion.
- **DB-05 extension:** gather() early termination adds a new failure handling path — abort siblings, preserve all execution.md, proceed to ASSESS (not escalate, since this is a single-task failure, not a team crash).
