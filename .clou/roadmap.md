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

### 33. Supervisor Parallel Milestones — completed
path: 33-supervisor-parallel-milestones
description: Update the supervisor protocol to drive multiple milestones concurrently when the arc contains independent milestones. Batch crystallization of dependency layers, parallel spawn via clou_spawn_parallel_coordinators, multi-completion disposition with per-milestone evaluation, and layer-by-layer sharpening. Prompt-only change — M31's runtime machinery is complete. 7 cycles, 71m, 13 agents. Quality gate converged 4→0.

### 34. ASSESS Compositionality Reduction — completed
path: 34-assess-compositionality-reduction
description: Reduce ASSESS cycle compositionality from 5+ file reads to ≤2-hop pre-composed context. Instrument cognitive metrics (read set size, reference density, compositional span) and wire into metrics.md. DB-20 Steps 1-2. 15 cycles, 248m, 37 agents. Quality gate converged 16→10→1→1→1→0.
**Independent of:** Memory Pattern Influence Telemetry

### 35. Memory Pattern Influence Telemetry — completed
path: 35-memory-pattern-influence-telemetry
description: Add retrieval and reference tracing telemetry to the memory pipeline. Measure whether patterns from memory.md actually reach and influence coordinator decisions. Wire pattern influence data into metrics.md. 14 cycles, 169m, 29 agents. Quality gate converged 10→4→2→2→0.
**Independent of:** ASSESS Compositionality Reduction

### 36. ORIENT Cycle Prefix — sketch
path: 36-orient-cycle-prefix
description: Add ORIENT as the first cycle in every coordinator session. Reads an adaptive small set (intents.md + status.md + glob phases/*/execution.md + git diff --stat) and emits a structured judgment artifact `{next_action, rationale, evidence_paths, expected_artifact}` per DB-14 ArtifactForm. Orchestrator logs the judgment alongside its own determine_next_cycle choice but does not yet act on disagreements — telemetry only. Establishes the new entry-point protocol (coordinator-orient.md) and the judgment artifact form without changing existing dispatch authority. Foundational for the cycle-type-as-prescription redesign uncovered by the safety-net loop (2026-04-18).

### 37. ORIENT Cycle Gating — sketch
path: 37-orient-cycle-gating
description: Promote ORIENT from telemetry to gating. When the coordinator's judgment disagrees with orchestrator's determine_next_cycle, judgment wins. Orchestrator validates judgment.evidence_paths exist on disk before honoring (sanity check, not gatekeeping). The safety-net loop scenario — PLAN repeated 3× with phases_completed:0 despite 4 execution.md files showing completed and 239 tests passing — becomes structurally impossible: ORIENT routes to EXIT on first re-entry. Includes a regression test reconstructed from the 2026-04-18 production trace.
**Depends on:** ORIENT Cycle Prefix

### 38. Artifact-Authoritative Bookkeeping — sketch
path: 38-artifact-authoritative-bookkeeping
description: phases_completed becomes orchestrator-computed from `phases/*/execution.md` evidence at cycle boundary; coordinator loses write authority over the field. status.md table becomes a rendered view of disk truth, not a coordinator-written ledger. PLAN cannot demote a completed phase to pending. Extends M21 R1's "status.md as render of checkpoint" pattern to phase-completion bookkeeping. Independently fixes the safety-net loop class via a different path than M37 — completion bookkeeping can never diverge from artifacts because artifacts are the source.
**Depends on:** ORIENT Cycle Prefix
**Independent of:** ORIENT Cycle Gating

### 39. Cycle-Type Prescription Dissolution — sketch
path: 39-cycle-type-prescription-dissolution
description: determine_next_cycle inverts into validate_judgment — its job becomes auditing the coordinator's chosen action against artifacts rather than choosing the cycle type. The cycle-type→read-set table is deleted. coordinator-orient.md becomes the sole entry-point protocol; existing coordinator-{plan,execute,assess,verify,exit}.md files become behavior libraries loaded post-judgment. ~20 `if cycle_type == X` branches in coordinator.py route off `judgment.next_action`. Cycle types stop being states the orchestrator runs through and become the vocabulary the coordinator uses to describe what it's about to do.
**Depends on:** ORIENT Cycle Gating, Artifact-Authoritative Bookkeeping

### 40. Adaptive Read-Set Composition — sketch
path: 40-adaptive-read-set-composition
description: Read sets compose from `judgment.evidence_paths` plus minimal behavior-specific augments. The cycle-type→read-set categorical mapping is fully retired. M34's reference-density telemetry graduates from observability to invariant: density > 0 required on every cycle (regression test against the M34 baseline). The coordinator's behavior session asks "what do I need to cite?" before "what am I doing?" — closing the loop the M34 instrumentation opened.
**Depends on:** Cycle-Type Prescription Dissolution
