# Clou Architecture

## Origin

Clou emerged from a whiteboard with two halves:

**Left side** — a hierarchical project scaffolding with three nested tiers (Project → Milestones → Phases), each with human-in-the-loop icons and recursive arrows suggesting self-similar structure. Each tier produces markdown artifacts and loops back into itself.

**Right side** — an agent runtime reasoning loop: environment feeds into User Intention, which flows to scope determination, "what next?", task graph consultation, and "journey complete?" checking.

The critical insight: **the planning layer is the bottleneck, not code generation.** The project management ontology (roadmaps, milestones, phases) should be a first-class, persistent, human-readable structure the agent maintains — not an ephemeral chain-of-thought that vanishes after execution.

## What Clou Is

Clou is an orchestrator for structured work. It manages a three-tier agent hierarchy, persistent planning state, quality gates, and verification — turning LLM sessions into a system that can execute multi-milestone projects with human oversight at natural boundaries.

Software construction is the first domain. The architecture (session hierarchy, judgment loops, golden context, typed task DAGs) is domain-agnostic. What varies per domain is the tool configuration, quality gates, verification modalities, and write permissions — collectively, the **harness template** (DB-11). The orchestrator reads the active template and configures itself accordingly.

## System Structure

```
clou_orchestrator.py (Python, Claude Agent SDK)
    │
    ├─ ClaudeSDKClient → Supervisor (agent session, user-facing)
    │                         │
    │                         │ calls clou_spawn_coordinator MCP tool
    │                         ↓
    ├─ ClaudeSDKClient → Coordinator (agent session, per milestone)
    │                         │
    │                         │ native Agent Teams
    │                         ↓
    │                    Agent Teams (worker agents)
    │
    ├─ Hook enforcement (write boundaries per tier)
    ├─ Token tracking (per session, cumulative)
    ├─ Context exhaustion monitoring (restart from checkpoint)
    └─ Custom MCP tools (clou_spawn_coordinator, clou_status, clou_init)
```

The orchestrator is a thin Python script (~500-800 lines) that manages session lifecycles via the Claude Agent SDK. It is invisible plumbing — the user interacts with the supervisor. See [Orchestrator](./integration/orchestrator.md) for implementation details.

## Three-Tier Session Hierarchy

### Tier 1: Supervisor

The user's direct interface. An agent session that is always available for conversation.

**Owns:** Project-level planning — roadmap, milestone creation, user request processing, milestone completion evaluation.

**Does not:** Touch code. Manage agent teams. See inter-agent messages.

**Produces:** `project.md`, `roadmap.md`, milestone directories with specs and requirements.

**Key characteristic:** The supervisor's context window is preserved for strategic reasoning by delegating all implementation detail to the coordinator.

### Tier 2: Coordinator

An agent session scoped to a single milestone's lifecycle. Spawned by the supervisor when a milestone begins.

**Owns:** Task planning (building the DAG), agent team orchestration, quality assessment (via template's quality gate), critical evaluation of feedback, escalation authoring, verification orchestration.

**Overall: a judgment loop, not a dispatch loop.** The coordinator doesn't just assign work and collect results. It evaluates the evaluator (Brutalist), makes calls within its delegated authority, logs reasoning, and escalates what it can't resolve. The judgment is decomposed across cycle types: PLAN (decomposition), ASSESS (evaluation), VERIFY (experience assessment). EXECUTE is the one cycle where judgment is deliberately absent — it is a mechanical dispatch loop following compose.py's call graph (DB-10).

**Produces:** `compose.py` (typed-function call graph), `decisions.md`, `status.md`, phase directories, escalations.

**Exit condition:** ALL of: phases complete, acceptance criteria met, verification phase produced `handoff.md` with confirmed golden paths, no blocking escalations, dev environment running.

### Tier 3: Agent Teams

One agent per compose.py function, spawned by the coordinator via the SDK's Agent tool during EXECUTE cycles. Each agent gets a fresh context scoped to a single function signature.

**Owns:** Execution artifacts. For software construction: code, tests, and codebase changes. Agent definitions (tools, capabilities) come from the active harness template.

**Communicates:** Stigmergic coordination through the filesystem only. Workers write results to execution.md and (for software) code to the codebase. No mailbox, no SendMessage, no TaskCreate, no inter-worker communication. Typed dependencies in compose.py mean each agent finds prior agents' artifacts via function argument types.

**Produces:** `execution.md` within assigned phases, plus domain-specific artifacts (codebase changes for software).

**Lifecycle:** Per-EXECUTE-cycle. Fresh agents each cycle. Teams end when the coordinator session exits at cycle boundary.

**Key characteristic:** Agent teams are the only tier that produces domain-specific artifacts directly.

## Key Architectural Decisions

These decisions were made during the design conversation and are settled (not open decision boundaries).

### 1. The supervisor never sees inter-agent messages

The coordinator compresses team activity into structured artifacts (decisions, escalations, execution results). This preserves the supervisor's context window for strategic reasoning. If the supervisor needs to understand why something went sideways, `decisions.md` is the interface — not raw message logs.

**Exception:** Escalation. The coordinator can write structured escalations that the supervisor checks. But escalations are engineered artifacts with analysis and recommendations, not raw passthrough.

### 2. Coordinators run sequentially by default, parallel when annotated

Each milestone depends on the previous unless the supervisor explicitly
reasons otherwise. The roadmap supports dependency annotations per DB-08:
`Depends on:` and `Independent of:` fields on milestone entries. When
the orchestrator finds validated `Independent of:` annotations, it
dispatches those coordinators concurrently via asyncio.gather. Each
coordinator gets its own session; no direct coordinator-to-coordinator
communication (by design). If one coordinator fails, siblings continue.

**Principle:** Serial execution is the default. Parallel is opt-in via
dependency annotations -- earned by the supervisor's explicit reasoning
that no artifact flows between the milestones.

### 3. Quality gates are essential infrastructure

External quality assessment is a blocking requirement. The coordinator cannot self-assess quality — research shows self-reflection produces false beliefs that persist indefinitely (§9, §10). A required quality gate that is unavailable is a hard error that escalates to the supervisor and user.

Brutalist MCP is the quality gate for the software-construction template. The coordinator critically evaluates Brutalist feedback (not deferential, not reflexive), but the evaluation requires Brutalist to be present. Other templates may use different gates — the pattern (invoke gate → evaluate critically → accept/override/escalate) is fixed; the specific gate is a template parameter. See [DB-11](./decision-boundaries/11-harness-architecture.md).

### 4. The coordinator builds the task DAG

The supervisor says "here's the milestone, here are the requirements, here's the acceptance criteria." The coordinator reads the codebase (through its agent teams), figures out the actual dependency structure of the work, builds the task graph, and orchestrates execution.

**Rationale:** The coordinator has access to the codebase through agent teams and can construct the graph empirically rather than speculatively. The supervisor would be guessing.

### 5. Session-per-cycle with golden context as sole compaction

Each coordinator cycle (PLAN, EXECUTE, ASSESS, VERIFY, EXIT) is a fresh `ClaudeSDKClient` session. The golden context is the only mechanism for transferring state between cycles — no reliance on SDK context compression, session persistence, or conversational continuity. The orchestrator reads `active/coordinator.md` between cycles, determines the next cycle type, constructs a targeted prompt with pointers to the specific golden context files needed, and spawns a fresh session. Git commits happen at phase completion boundaries.

This unifies four roles of golden context: human-legibility surface, crash recovery, inter-cycle state transfer, and compaction. Same files, same format, four purposes.

### 6. Light system prompts + per-cycle protocol files

Each tier's system prompt is a small XML template (~800–2,000 tokens) containing an identity anchor, critical invariants, and a pointer to a protocol file. The full behavioral specification lives in protocol files the agent reads as its first action. The coordinator has per-cycle-type protocol files (PLAN, EXECUTE, ASSESS, VERIFY, EXIT) — each session reads exactly one.

This is grounded in research (see [Research Foundations](./research-foundations.md)): system prompts have no architectural privilege, instruction density degrades past a threshold, decomposition outperforms monolithic prompts, and first tokens are architecturally privileged (attention sinks). No CLAUDE.md — all prompt content via SDK `system_prompt`.

### 7. Quality gate provides feedback; coordinator is judge

The quality gate (Brutalist MCP for software construction) provides raw, unsweetened feedback from multiple model perspectives. The coordinator evaluates this feedback against the milestone's requirements and constraints:
- Valid feedback → rework cycle
- Invalid feedback → override logged in `decisions.md` with reasoning
- Feedback exceeding coordinator authority → escalation to supervisor

The coordinator's relationship with the quality gate is critical, not deferential. The gate provides signal; the coordinator decides what to do with it.

### 8. Verification is a full phase

Not a step. Not a test suite. A complete phase with its own `phase.md`, agent team, and three stages:
1. Environment materialization (real services, no mocks of your own infra)
2. Agentic path walking (Playwright for web, HTTP for APIs, CLI invocation for tools)
3. Handoff preparation (running environment, guided walk-through for the user)

### 9. The orchestrator is invisible plumbing

The user runs `python clou/orchestrator.py` (or a simple `clou` CLI entry point). This starts the supervisor session. The user interacts with the supervisor — they never see the orchestrator. Lifecycle commands (`clou_init`, `clou_status`) are MCP tools the supervisor calls, handled by the orchestrator in-process. The golden context structure, the protocols, and the prompts are the product. The orchestrator is infrastructure.

### 10. Error recovery: fail loud, validate structure, coordinator-only commits

Crashes and infrastructure failures escalate to the supervisor and user — no silent retries. The orchestrator validates golden context structure at cycle boundaries, including cross-file consistency between checkpoint and status.md (revert and retry on failure). Agent teams write code but only the coordinator commits to git via selective staging — only files in `git diff` filtered by exclude patterns, not `git add -A` (DB-15 D4). 20-cycle milestone cap prevents runaway loops; cycle count resets after resolved escalation (DB-15 D5). The user can interrupt at cycle boundary via `/stop` (DB-15 D1). Inter-phase smoke tests catch compositional failures at phase boundaries. See [DB-05](./decision-boundaries/05-error-recovery.md), [DB-15](./decision-boundaries/15-architectural-tensions.md).

### 11. Opus everywhere, token tracking, budget-aware cycle control

All tiers use Opus — maximum quality at every tier. Cost control comes from the coordinator's critical evaluation of whether each cycle is productive, the 20-cycle cap (DB-05), and per-milestone soft budgets (DB-15). Cost is tracked in tokens (stable across pricing changes). The harness template defines an optional `budget_usd` — at 50%/75%/100% thresholds, the orchestrator injects cost-awareness into the cycle prompt as a cognitive affordance. decisions.md is structurally compacted at cycle boundary to prevent golden context bloat (DB-15 D3). Model selection is hardcoded; configurability deferred. See [DB-06](./decision-boundaries/06-token-economics.md), [DB-15](./decision-boundaries/15-architectural-tensions.md).

### 12. No mocks of your own infrastructure

Mock at the boundary of your control, never within it. Your own services run real. Third-party services use their sandbox/test mode. The only acceptable mock is for external services that provide no testing infrastructure at all.

### 13. Adaptive context injection — the memory architecture

The system's memory model is not multi-tiered storage (short-term / long-term / episodic). It is **adaptive retrieval**: the orchestrator decides which files at which resolution for each specific cycle. Session-per-cycle (Decision 5) is the working memory architecture. Per-cycle read sets are observation masking. `decisions.md` compaction is structural summarization. The orchestrator is the retrieval system.

This is grounded in four research threads:

- **Chunking research.** Quality peaks at 7-9 chunks filling 40-70% of the context window. Current coordinator read sets are 4-7 files — inside the sweet spot. Adding files past this threshold actively degrades performance (see Research Foundations §1: context degradation — every unnecessary token is harmful, not neutral).
- **Factory.ai anchored iterative summarization.** Structured compaction with persistent sections scored 3.70/5.0 vs 3.44 (Anthropic) and 3.35 (OpenAI) on 36K real coding sessions. This validates `decisions.md` structural compaction (DB-15 D3): keep recent 3 cycles full, summarize older to one-line entries. The persistent sections are the anchor; the summaries are lossy but structurally stable.
- **ACON framework.** Meta-learning on compaction boundaries — observe what breaks when information is dropped, update compaction rules. 26-54% memory reduction while preserving 95%+ accuracy. This is the principle behind the coordinator's cycle-boundary validation: if golden context fails structural checks after compaction, revert and retry.
- **Context degradation (Research Foundations §1).** The transformer attention mechanism means irrelevant tokens don't merely waste space — they actively compete for attention weight with relevant tokens. The right 7-9 files at the right resolution for this specific moment is the entire memory strategy. There is no background store the agent "checks when needed." If it's not in the read set, it doesn't exist for that cycle.

The key insight: **the transformer doesn't need categories of memory. It needs the right files at the right resolution for this specific moment.** The orchestrator's per-cycle prompt construction — selecting which golden context files to include, at what level of detail — is the retrieval mechanism. No vector database, no RAG pipeline, no memory taxonomy. The orchestrator reads `active/coordinator.md`, determines the cycle type, and constructs a read set. That read set IS the agent's memory for that cycle.

## Harness Template Layer

Between the orchestrator and the agent definitions sits the **harness template** — a capability profile that specifies what tools each tier has, what quality gates run, what verification modalities are available, and what write permissions apply.

The orchestrator reads the active template (recorded in `project.md`) and configures agent definitions, MCP servers, and hook enforcement accordingly. The supervisor selects the template during project initialization based on user intent. The user never interacts with the harness directly.

The harness template also defines **artifact forms** (DB-14) — cognitive affordances for golden context artifacts. Each form specifies a criterion template, required sections, and anti-patterns. The PostToolUse hook validates every write against the artifact's form, giving the agent immediate feedback when it produces wrong-level content (e.g., implementation specs in intents.md). This is LLM-Modulo applied to narrative artifacts — the same external-verification pattern that makes compose.py AST validation work.

The supervisor crystallizes milestones with three artifacts: `milestone.md` (scope), `intents.md` (observable outcomes — DB-14), and `requirements.md` (implementation constraints). Intent and specification are incommensurable concerns that need separate containers (DB-07 principle applied at the intent→plan boundary). The verifier walks golden paths against intents.md, not requirements.md — ensuring verification requires observing the running system.

Software construction is the first (and currently only) template. The architecture supports additional templates for other domains — the three-tier hierarchy, judgment loop, golden context, and session-per-cycle are domain-agnostic infrastructure. See [DB-11](./decision-boundaries/11-harness-architecture.md), [DB-14](./decision-boundaries/14-intent-specification-separation.md).

## System Topology

```
┌──────────────────────────────────────────────────────────────┐
│  clou_orchestrator.py (Python)                               │
│    │                                                          │
│    ├─ Harness template: loaded from clou/harnesses/<name>.py │
│    │    configures: agent tools, quality gates, MCP servers,  │
│    │    write permissions, compose conventions                │
│    │                                                          │
│    ├─ Hooks: write boundary enforcement per tier (from template)│
│    ├─ MCP: clou_spawn_coordinator, clou_status, clou_init  │
│    ├─ Token tracking: per session, cumulative                 │
│    ├─ Session-per-cycle: golden context as sole compaction     │
│    │                                                          │
│    ├─ ClaudeSDKClient ──────────────────────────────────┐     │
│    │  User                                               │     │
│    │    ↕ conversation (stdin/stdout)                     │     │
│    │  Supervisor (agent session)                          │     │
│    │    │ reads: .clou/project.md, roadmap.md, requests  │     │
│    │    │ writes: project.md, roadmap.md, milestone specs │     │
│    │    │ selects: harness template                       │     │
│    │    │ calls: clou_spawn_coordinator(milestone)       │     │
│    │    └────────────────────────────────────────────────┘     │
│    │                                                          │
│    ├─ ClaudeSDKClient (spawned per milestone) ──────────┐     │
│    │  Coordinator (agent session)                        │     │
│    │    │ reads: milestone.md, requirements.md, project   │     │
│    │    │ writes: compose.py, status.md, decisions.md       │     │
│    │    │ invokes: quality gate (from template)           │     │
│    │    │                                                 │     │
│    │    ↓ manages (native Agent Teams)                    │     │
│    │  Agent Teams (per-template agent definitions)        │     │
│    │    │ reads: compose.py, phase.md, codebase            │     │
│    │    │ writes: execution.md, code changes              │     │
│    │    │                                                 │     │
│    │    ↓ verification phase                              │     │
│    │  Verification Agent (per-template tools)             │     │
│    │    │ materializes: dev environment                    │     │
│    │    │ walks: golden paths (per verification modalities)│     │
│    │    │ writes: handoff.md                               │     │
│    │    └────────────────────────────────────────────────┘     │
│    │                                                          │
│    ↓ User receives handoff with running environment           │
└──────────────────────────────────────────────────────────────┘
```

## What Clou Adds Beyond Single-Session Agents

| Capability | Single-session agent | Clou |
|---|---|---|
| Session hierarchy | 2-tier (lead → teammates) | 3-tier (supervisor → coordinator → teams) |
| Session lifecycle | Manual | Orchestrator manages spawn, monitor, restart |
| Persistent planning state | None (context window only) | `.clou/` golden context |
| Quality gates | None | Pluggable quality gates with critical evaluation (DB-11) |
| Domain-adaptive tool configuration | Hardcoded | Harness templates configure tools, gates, verification per domain (DB-11) |
| Human-in-the-loop | Ad hoc conversation | Structured escalation protocol |
| Verification | Manual | Agentic path walking + prepared handoff |
| Third-party services | Manual setup | Credential management protocol |
| Crash recovery | Session resume (experimental) | Session-per-cycle + golden context checkpoints |
| Write boundary enforcement | None | Orchestrator hooks per tier (from template) |
| Token tracking | Per-session only | Orchestrator tracks cumulative tokens per milestone |
| Audit trail | Git history only | `decisions.md`, escalation dispositions |
