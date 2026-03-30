# Clou Knowledge Base

Clou is an orchestrator for structured work — it manages a three-tier hierarchy of agent sessions, persistent planning state, quality gates, and verification. Clou's thesis: the planning layer — not generation — is the bottleneck in agentic systems. The system maintains a persistent, human-readable golden context (`.clou/`) that serves as both the agent's working memory and the human's legibility surface.

Software construction is the first domain. The architecture is domain-agnostic; what varies per domain is the harness template — tool configuration, quality gates, verification modalities, and write permissions. See [DB-11](./decision-boundaries/11-harness-architecture.md).

## Knowledge Base Structure

### Core Specification
- [Architecture](./architecture.md) — Three-tier session hierarchy, key decisions, system topology
- [Golden Context](./golden-context.md) — `.clou/` file tree, file purposes, schemas, ownership rules
- [Design Principles](./design-principles.md) — The invariants that govern all Clou decisions
- [Interface](./interface.md) — The breathing conversation: presentation layer design grounded in perceptual engineering, four modes of inhabitation, visual language, technology stack (Textual + Rich)
- [Visual Language](./visual-language.md) — Design system: OKLCH color palette, breathing animation, shimmer, gradients, design tokens, attentional salience, typographic hierarchy
- [Research Foundations](./research-foundations.md) — How transformers digest context at scale, what works for multi-agent prompting, what current approaches miss. Grounds Clou's design in research.

### Protocols
- [Supervisor Protocol](./protocols/supervisor.md) — User-facing session: role, loop, outputs
- [Coordinator Protocol](./protocols/coordinator.md) — Milestone-scoped session: role, loop, DAG, exit conditions
- [Write Protocol](./protocols/write-protocol.md) — Ownership boundaries, write rules, update timing
- [Escalation Protocol](./protocols/escalation.md) — Structured signals between tiers, schema, severity semantics
- [Verification Protocol](./protocols/verification.md) — Environment materialization, agentic path walking, handoff
- [Services Protocol](./protocols/services.md) — Third-party service integration, credential management

### Integration
- [Orchestrator](./integration/orchestrator.md) — **Clou's core runtime**: session lifecycle management, custom MCP tools, hook enforcement, cost tracking
- [Presentation Layer](./integration/presentation.md) — **The bridge**: SDK message → Textual message mapping, concurrency model, mode transitions, widget architecture, breath event curation
- [Claude Agent SDK](./integration/claude-agent-sdk.md) — SDK capabilities, constraints, what Clou can and cannot use
- [Agent Teams](./integration/agent-teams.md) — One agent per function, stigmergic coordination, dispatch loop with circuit breakers
- [Brutalist MCP](./integration/brutalist-mcp.md) — Multi-perspective quality gate, available tools, usage patterns
- [Playwright MCP](./integration/playwright-mcp.md) — Browser-driven verification, tool inventory, constraints

### Implementation
- [Status](./implementation/status.md) — What's built, what's next, quality gate results. Front-loaded like Clou's own `execution.md`.
- [Findings](./implementation/findings.md) — Discoveries during implementation that affect design. Newest-first like Clou's own `decisions.md`.
- [Slash Commands](./implementation/slash-commands.md) — Dispatch layer, completion menu, and per-command design plans for the discoverable harness surface.

### Decision Boundaries
Architectural decisions resolved during the design phase. Each documents the problem, options with tradeoffs, constraints from the SDK, research basis, and final decision.

- [DB-01: Spawning Mechanism](./decision-boundaries/01-spawning-mechanism.md) — **DECIDED**: Orchestrator wrapper (Option B)
- [DB-02: Task DAG Implementation](./decision-boundaries/02-task-dag-implementation.md) — **DECIDED**: Sequential phases + typed-function composition (`compose.py`)
- [DB-03: Context Window Lifecycle](./decision-boundaries/03-context-window-lifecycle.md) — **DECIDED**: Session-per-cycle, golden context as sole compaction
- [DB-04: Prompt System Architecture](./decision-boundaries/04-prompt-system-architecture.md) — **DECIDED**: Light system_prompt + per-cycle protocol files + XML structure
- [DB-05: Error Recovery](./decision-boundaries/05-error-recovery.md) — **DECIDED**: 20-cycle cap, coordinator-only commits, structural validation, Brutalist as essential infrastructure, inter-phase smoke tests
- [DB-06: Token Economics](./decision-boundaries/06-token-economics.md) — **DECIDED**: Opus everywhere, all relevant Brutalist domains, token tracking, no budget limit, hardcoded model selection
- [DB-07: Milestone Ownership](./decision-boundaries/07-milestone-ownership.md) — **DECIDED**: Split files (`milestone.md` immutable spec + `status.md` coordinator progress), per-cycle read set alignment
- [DB-08: File Schemas](./decision-boundaries/08-file-schemas.md) — **DECIDED**: `roadmap.md` (numbered milestones + dependency annotations), `execution.md` (front-loaded failure summary + execution-order tasks), `decisions.md` (newest-first, cycle-grouped), no separate schema files
- [DB-09: Verification Generalization](./decision-boundaries/09-verification-generalization.md) — **DECIDED**: Verification modalities (composable, coordinator-selected), mediated perception for Brutalist, three perception stages + structural experience assessment, manual as declared residual
- [DB-10: Team Communication](./decision-boundaries/10-team-communication.md) — **DECIDED**: Mechanical dispatch with circuit breakers, one agent per function, stigmergy only, no mailbox/SendMessage/TaskCreate
- [DB-11: Harness Architecture](./decision-boundaries/11-harness-architecture.md) — **DECIDED**: Harness templates (capability profiles per domain), Python module format, supervisor selects, quality gate pluggability, capability axes not domain taxonomy, environment detection as template capability
- [DB-12: Validation Tiers](./decision-boundaries/12-validation-tiers.md) — **DECIDED**: Three-tier golden context validation (AST for compose.py, strict parsing for checkpoints, form-only for narratives)
- [DB-13: Supervisor Disposition](./decision-boundaries/13-supervisor-disposition.md) — **DECIDED**: Two dispositions (exploring/converging) inferred from conversational signals, understanding.md as cumulative cognitive artifact, anti-fixation discipline
- [DB-14: Intent-Specification Separation](./decision-boundaries/14-intent-specification-separation.md) — **DECIDED**: Split intents.md (observable outcomes) from requirements.md (implementation constraints), artifact forms as harness template concept, PostToolUse validation
- [DB-15: Architectural Tensions](./decision-boundaries/15-architectural-tensions.md) — **DECIDED**: Five tension resolutions — cycle-boundary message check, budget-aware cycle control, decisions.md structural compaction, selective git staging, escalation recovery via cycle count reset
