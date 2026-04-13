# Roadmap

## Milestones

### 1. DB-12 Validation Tiers — completed
### 2. Test Hygiene & Trailing Debt — completed
### 3. Deferred Debt Sweep — completed
### 4. Validator Resilience — completed
### 5. Orchestrator Loop Integrity — completed
### 6. Live Task Graph — completed
### 7. Supervisor Understanding — completed
### 8. Schema Fix + TurnController — completed
### 9. Proxy Removal (attempt 1) — completed
### 10. Checkpoint Integrity — completed
### 11. Proxy Removal (attempt 2) — completed

### 12. The Reasoning Loop — completed
path: 12-reasoning-loop
description: Supervisor prompt restructured into tool-mediated reasoning loop. Environment scan, ask_user targeting, understanding.md with DB-13 sections.

### 13. Convergence and Crystallization — completed
path: 13-convergence-crystallization
description: Convergence test, intent drafting with user confirmation, crystallization derivation from understanding.md, solution-to-outcome translation. 5 cycles, 32m, 6 agents.

### 14. Arc Formation — completed
path: 14-arc-formation
description: Arc reasoning, presentation, sharpening, and revision. Supervisor reasons about full journey, user confirms arc before execution. 3 cycles, 24m, 6 agents.

### 15. Disposition-Aware Questioning — completed
path: 15-disposition-aware-questioning
description: Exploring vs converging question strategies. Two signal sources (understanding.md density + user response characteristics), gradient-based question selection, Groan Zone handling with convergence suppression, fast path for pre-converged users. 3 cycles.

### 16. Width-Aware Planning — completed
path: 16-width-aware-planning
description: Coordinator planner gains width-aware decomposition guidance. Task graphs express independence via gather() when scope has parallel workstreams, reason about critical path, and stay narrow when scope is single-dimensional. Research foundations gain decomposition topology section grounding the guidance in LAMaS, LLMCompiler, HiPlan, and ACONIC.

### 17. Runtime Safeguards — completed
path: 17-runtime-safeguards
description: Execution layer gains safeguards for wider task graphs: sharded execution state for concurrent agents, partial failure handling (selective abort of dependents only), and per-task resource bounds with failure context for ASSESS. 8 cycles, 152m, 18 agents.

### 18. Topology Instrumentation — completed
path: 18-topology-instrumentation
description: Lightweight instrumentation capturing task graph topology (width, depth, layer count, gather group sizes) and per-task execution data (timing, tokens) in metrics.md. Closes the feedback loop on M16's width-aware planning guidance with structured, measurable data. 3 cycles, 30m, 8 agents. Coordinator crashed during integration test phase; core implementation and 104 unit tests delivered successfully.

### 19. Topology Integration Tests — completed
path: 19-topology-integration-tests
description: End-to-end integration tests for M18's topology instrumentation pipeline. 8 test scenarios exercising compose.py → topology extraction → telemetry → metrics.md, covering wide/linear/diamond/single-task graphs, graceful degradation (missing compose.py, empty telemetry), and edge cases (orphaned agents, section preservation).

### 20. Tech Debt Sweep — completed
path: 20-tech-debt-sweep
description: Comprehensive sweep of all validated tech debt from M1–M19. 14 intents across 4 categories: 5 test gaps (T1–T5 from brutalist R2-3), 3 edge case production fixes (V4/V6/V10 git scoping and revert safety), 3 topology integration test additions (from M19 untested quadrants), and structural decomposition of recovery.py (1,736 lines → ~5 modules of ≤400 lines each) plus _active_app accessor pattern and retry counter checkpoint persistence. Clears the full validated debt inventory.

### 21. Recovery Architecture — completed
path: 21-recovery-architecture
description: Four structural fixes to the recovery machinery that eliminate the cascade failure pattern observed across M18–M20. R1: Unified state — status.md becomes a render-only view of checkpoint, eliminating divergence by construction. R2: Typed cycle outcomes — ADVANCED/INCONCLUSIVE/INTERRUPTED/FAILED replace binary progress/stuck, preventing ASSESS staleness when brutalist tools are rate-limited. R3: Health-signal timeout classification — timeout handler tracks message context and classifies interruptions (sleep, network) vs genuine crashes, replacing the single TimeoutError → agent_team_crash path. R4: Classified worker errors — transient (network, sleep) auto-restart with cooldown; terminal (config, code) escalate once. Serial phases: R1 → R2 → R3 → R4. Each phase gets brutalist review loop until convergence.

### 22. Lifecycle Pipeline Wiring — completed
path: 22-lifecycle-pipeline-wiring
description: Fix the three lifecycle mechanisms that have code but never fire — pattern decay (status field never persisted), episodic archival (consolidated gate fails), decisions compaction (zero callers) — so the full pipeline consolidation → decay → archival → compaction executes end-to-end at orchestrator startup. 4 cycles, 116m, 16 agents. Quality gate found 2 actionable issues (escalation files in retroactive archival + path traversal validation), both fixed.

### 23. Lifecycle Extension — completed
path: 23-lifecycle-extension
description: Wire the five DB-18 mechanisms that have no code yet: telemetry cleanup (JSONL deletion post-consolidation), session cleanup (transcript archival), quality gate telemetry events (emitters in orchestrator/coordinator), scored retrieval (filter memory.md by pattern type per cycle, rank by reinforced/last_active), and temporal invalidation (mark patterns invalidated when contradicted). Extends the working pipeline from M22 with the full DB-18 design. 5 cycles, 80m, 16 agents. Coordinator escalated with staleness after completing all work; accepted by user on manual verification.

### 24. Verified Decomposition — completed
path: 24-verified-decomposition
description: Implement DB-02's compose.py design: graph.py validator with 9 structural checks + intent coverage, PostToolUse hook with rejection-regeneration (LLM-Modulo pattern), coordinator prompt updates for one-line docstrings and real typed arguments, and graph-spec separation where phase.md carries the full agent briefing. 9 cycles, 228m, 42 agents. All 6 intents verified, 204 tests pass. Known limitation: keyword-overlap heuristic for intent coverage matching.

### 25. False Dependency Detection — completed
path: 25-false-dependency-detection
description: Topology-level verification beyond structural validity. Single-use wrapper chain detection (promoting M24's advisory to blocking), WAR-equivalent false dependency detection, file/module independence analysis, and actionable error messages with parallel alternatives. Extends M24's graph.py validator with Research Foundations §19 heuristics to catch fabricated serial chains in structurally valid graphs. 15 cycles, 155m, 28 agents. All 4 intents verified, 239 tests in graph+hooks, 709 across core suites.

### 26. Agent Observability — completed
path: 26-agent-observability
description: Progressive drill-down from task graph into individual agents. Extends the data layer with per-tool event messages, model state, and bridge routing. Adds inline preview expansion in the task graph widget (structured activity summary with breathing liveness). Builds a dedicated agent detail screen with full tool call stream, expandable I/O, and live streaming. Both live during execution and reviewable after completion. All within the existing design system language. 9 cycles, 314m, 43 agents. All 4 intents verified, 272 milestone tests + 727 UI regression tests. Known limitation: SDK provides tool names only for coordinator agents — input/output placeholders.

### 27. Agent Transcript Inspection — completed
path: 27-agent-transcript-inspection
description: Wire real tool call content into M26's detail screen. PostToolUse hook captures subagent tool calls into bounded transcript store. Enrichment layer rebuilds ToolInvocations with real input (via tool_summary.py) and output data for completed agents. 3 cycles, 176m, 14 agents. All 3 intents verified, 95 transcript + 365 core tests. Quality gate converged 4→1→0.

### 28. Real-Time Enrichment — completed
path: 28-realtime-enrichment
description: Wire M27's transcript capture to the UI in real-time. Live tool calls in the detail screen and inline preview show actual input/output data as agents work, not just after completion. Post-hoc enrichment path unchanged. 2 cycles, 13m, 2 agents. All 3 intents verified, 945 tests.

### 29. Memory Lifecycle Completion — completed
path: 29-memory-lifecycle-completion
description: Complete the full DB-18 memory architecture. Seven mechanisms that exist as dead code, protocol-only guidance, or missing implementation become operational: decay persistence (status fields written to disk), decisions compaction (wired to caller), temporal invalidation (invoked during consolidation), supervisor annotation (write path during re-entry), understanding.md lifecycle (resolved migration and staleness archival), scored retrieval enforcement (filter by status and type at prompt assembly), and consolidation enrichment (distributions instead of single values). 9 cycles, 223m, 41 agents. All 7 intents verified, 240 tests. Quality gate converged 9→2→3→2→1→0.

### 30. Understanding Prose Preservation — completed
path: 30-understanding-prose-preservation
description: Fix _parse_understanding_sections() and _render_understanding() so section-level prose between ## headers and first ### entries survives round-trip through compact_understanding(). 7 cycles, 53m, 10 agents. 18 tests pass. Tech debt from M29 resolved.

### 31. Cross-Milestone Dependencies & Parallel Dispatch — completed
path: 31-cross-milestone-parallel-dispatch
description: Wire the full DB-20 Step 7 pipeline: supervisor writes dependency annotations (DB-08 schema) on roadmap.md entries during arc formation and sharpening, orchestrator validates annotation consistency (no cycles, no dangling refs), and dispatches independent coordinators concurrently via asyncio.gather on separate SDK sessions. Partial failure isolation ensures one coordinator's failure doesn't cascade. 7 cycles, 65m, 8 agents. 193 tests. Quality gate converged 10→0.

### 32. Orchestrator Cleanup Authority — completed
path: 32-orchestrator-cleanup-authority
description: Extend the orchestrator's DB-18 lifecycle cleanup pipeline with authority to delete root-level `.clou/` files flagged as obsolete in handoff.md known limitations. Keeps coordinator write boundary milestone-scoped. 2 cycles, 19m, 4 agents. 474 tests (40 M32-specific). Quality gate converged 22→20→14 findings, 9→1→0 actionable.
