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

### 36. ORIENT Cycle Prefix — completed
path: 36-orient-cycle-prefix
description: Add ORIENT as the first cycle in every coordinator session. Reads an adaptive small set (intents.md + status.md + glob phases/*/execution.md + git diff --stat) and emits a structured judgment artifact `{next_action, rationale, evidence_paths, expected_artifact}` per DB-14 ArtifactForm. Orchestrator logs the judgment alongside its own determine_next_cycle choice but does not yet act on disagreements — telemetry only. Establishes the new entry-point protocol (coordinator-orient.md) and the judgment artifact form without changing existing dispatch authority. 14 cycles, 245m, 48 agents (33 completed, 15 stopped during pytest probing). 52 tests pass. Cycle 4 trajectory breakdown triggered the M49b halt primitive in production (first dog-food firing); supervisor dispositioned re-scope with preference (a) converge-and-exit; coordinator accepted and shipped 5 functional phases + orient_integration, deferring 36 cycle-4 findings to named follow-up milestones (M50, M42). First validation of the ORIENT + judgment pipeline and the M49a/b halt arc.

### 50. Orient Gating Prerequisites Hardening — completed (partial / pivoted)
path: 50-orient-gating-prerequisites
description: Four intended structural fixes; only I1 (vocabulary canonicalization) executed. Engine logged outcome=completed but only 1 of 5 phases ran. Five consecutive divergent cycle-pairs (M41 anti-convergence pattern) on the halt-gate surface led to abandon disposition; EXIT-cycle worker did not honor the supervisor's revert direction. Production ships: T1-T11 vocabulary baseline (structured cycle-type tokens `EXECUTE_REWORK`/`EXECUTE_VERIFY` + migration sweep + parser rejection of legacy tokens), T12 stash-field discriminator (preserves stash-slot semantics), and cycle-3/4 critical fixes (F5 protocol_stem routing, F1/F15/F25 task_graph helper, F2/F19 _JUDGMENT_NEXT_ACTION_RE prefix-tolerance, F12 parse_checkpoint narrowed coercion). Production also ships the broken T13-T21 halt-gate scaffold (5 known security findings: CWE-59/61/340/367 + mount-namespace bypass; 4 critical regressions: F12 _MAX_CYCLES disarmed, F19 supervisor permanent wedge, F20 budget split-brain, F25 stash-the-default; 8 verbatim re-surfaces of the bounded brief) — explicitly tolerated as transient scaffold to be SUPERSEDED by M51 (judgment-gates-dispatch). Phases I2/I3/I4 + m36_regression_replay deferred to M51's structural absorption. 16 cycles, 269m duration, 39 agents, 4 halt firings. M50 reinforced patterns: cheapest-viable-path inline (5x), pre-authorize halt thresholds (4x), coordinator self-recognition reliable (4x), M41 structural (5x), halt-arc dog-food (5x). Operational: clou_write_checkpoint MCP server restart required after exit (vocabulary frozenset bootstrap-state).
**Depends on:** ORIENT Cycle Prefix

### 51. ORIENT Cycle Gating — superseded by M52
path: 51-orient-cycle-gating
description: Originally M37's judgment-gates-dispatch decomposition vehicle. Deadlocked on a path-allowlist × phase-contract mismatch: phase.md declared the deliverable as `phases/p1_judgment_layer_spec/spec.md`, but worker-tier write hook permits only `milestones/*/phases/*/execution.md`. Worker degraded by embedding the spec in `execution.md`; supervisor LLM kept reading phase.md, looking for spec.md, re-dispatching. Three EXECUTE cycles fired generic staleness; M51 was respawned by autonomous run on 2026-04-26 producing a substantive 1193-line spec, then died on a stacked failure (brutalist subprocess startup + supervisor CLI exit 143). The substantive spec content is preserved as research input under M52. Closed as superseded by M52 — same class of bug as M50 round-4 (halt machinery cannot distinguish "tooling refused the work" from "engine lost coherence").
**Superseded by:** M52 Substance-Typed Deliverables

### 52. Substance-Typed Deliverables — shipped
path: 52-substance-and-capability
description: Phase deliverables are content-typed artifacts, not filename-coupled paths. `clou/artifacts.py` ships an artifact-type registry (frozen dataclass + `MappingProxyType` + 4-backtick fenced-block grammar with content-addressed identity (sha256 of canonicalized body) and 4-tuple location identity `(milestone, phase, type, content_sha)`). Phase-acceptance gate (`clou/phase_acceptance.py`) is a pure function over pre-read text; engine integration (`coordinator.py::_run_phase_acceptance_gate`) calls it at the start of every ASSESS cycle and persists the verdict into the checkpoint envelope's `last_acceptance_verdict` field (wire format `<phase>|<decision>|<content_sha>` or `none`). `clou_write_checkpoint` enforces F33 strict gating (advance refused unless prev_cp.verdict is Advance for the right phase + single-phase increment), with F40/F41 bootstrap grace for legacy phase.md (one-shot per advance). LLM `coordinator-assess.md` rewritten to read the verdict from cycle context; `supervisor.md` got an explicit `<phase-deliverables>` anti-pattern note naming filename-coupled deliverables as the M51 deadlock class. `phase.md` linter (`lint_phase_md`) refuses unregistered types. M51's 1193-line autonomous-run content, wrapped in artifact fences, advances under the new gate (`test_m51_real_content_advances`). DB-22 captures the architectural decision; protocols/coordinator.md gains a "Phase Acceptance Gate (M52)" section. Originally scoped four moves; rescoped to substance-only after seven brutalist rounds surfaced cross-coupling. Capability registry → M52b. Typed halt taxonomy → M52c. Reflective dispatch dropped at round 2.
**Depends on:** Trajectory-Halt primitive (M49a + M49b)
**Status:** All phase-3/7/8/9 mechanical and documentation tasks shipped. Brutalist final gates (`p3.G`, `p7.G`, `p9.G`) require user-invoked `/ultrareview` and remain pending.

### 52b. Capability Registry — planned
path: 52b-capability-registry
description: First-class `Tier` registry (worker, verifier, coordinator, supervisor, brutalist, assess-evaluator) replacing hand-edited `clou/hooks.py:WRITE_PERMISSIONS`. PLAN-time validation that the assigned tier can emit the declared deliverable. Resolves the class of bug where PLAN ships a phase EXECUTE physically cannot complete. Deferred from M52 because the capability registry interacts with the artifact registry, prompts, and hook write boundaries; composing all three in one milestone produced cross-coupling that drove M52's round-on-round divergence. M52a's seed types reveal which capabilities matter; M52b formalizes against observed need.
**Depends on:** M52 Substance-Typed Deliverables

### 52c. Typed Halt Taxonomy — planned
path: 52c-typed-halt-taxonomy
description: `Halt = InfraFriction | GateDeadlock | TrajectoryHalt`. Per-kind counters (`infra_friction_count`, `gate_deadlock_count`, `divergence_count`) replacing unary `staleness_count`. Per-kind thresholds and disposition contracts. Sibling MCP verbs (`clou_halt_infra_friction`, `clou_halt_gate_deadlock`) preserve M49b's `clou_halt_trajectory` pattern. Resolves M50 round-4's rate-limit-as-divergence misclassification and M51's generic-staleness conflation. Deferred from M52 because the halt-classification site is a single concentrated point of risk; M52's gate produces a single new halt-class signal (GateDeadlock); accumulating operational data on that one signal before generalizing the sum type is safer than designing the full taxonomy in advance.
**Depends on:** M52 Substance-Typed Deliverables, M52b Capability Registry
**Inputs from M50:** spec-first discipline; coordinator self-recognition reliable; brutalist multi-CLI panel signal load-bearing; pre-authorized halt thresholds work; EXIT-cycle worker rationalization risk

### 37. ORIENT Cycle Gating — superseded by M51
path: 37-orient-cycle-gating (no directory; superseded)
description: Original sketch promoted ORIENT from telemetry to gating with judgment authority over `determine_next_cycle`. Per user disposition at M50 close, this work dispatches as M51 (`51-orient-cycle-gating`) with hybrid framing — see M51 entry above.

### 38. Artifact-Authoritative Bookkeeping — sketch
path: 38-artifact-authoritative-bookkeeping
description: phases_completed becomes orchestrator-computed from `phases/*/execution.md` evidence at cycle boundary; coordinator loses write authority over the field. status.md table becomes a rendered view of disk truth, not a coordinator-written ledger. PLAN cannot demote a completed phase to pending. Extends M21 R1's "status.md as render of checkpoint" pattern to phase-completion bookkeeping. Independently fixes the safety-net loop class via a different path than M37 — completion bookkeeping can never diverge from artifacts because artifacts are the source.
**Depends on:** ORIENT Cycle Prefix
**Independent of:** ORIENT Cycle Gating (M51)

### 39. Cycle-Type Prescription Dissolution — sketch
path: 39-cycle-type-prescription-dissolution
description: determine_next_cycle inverts into validate_judgment — its job becomes auditing the coordinator's chosen action against artifacts rather than choosing the cycle type. The cycle-type→read-set table is deleted. coordinator-orient.md becomes the sole entry-point protocol; existing coordinator-{plan,execute,assess,verify,exit}.md files become behavior libraries loaded post-judgment. ~20 `if cycle_type == X` branches in coordinator.py route off `judgment.next_action`. Cycle types stop being states the orchestrator runs through and become the vocabulary the coordinator uses to describe what it's about to do.
**Depends on:** ORIENT Cycle Gating (M51), Artifact-Authoritative Bookkeeping

### 40. Adaptive Read-Set Composition — sketch
path: 40-adaptive-read-set-composition
description: Read sets compose from `judgment.evidence_paths` plus minimal behavior-specific augments. The cycle-type→read-set categorical mapping is fully retired. M34's reference-density telemetry graduates from observability to invariant: density > 0 required on every cycle (regression test against the M34 baseline). The coordinator's behavior session asks "what do I need to cite?" before "what am I doing?" — closing the loop the M34 instrumentation opened.
**Depends on:** Cycle-Type Prescription Dissolution

### 41. Escalation Remolding + User-Modal Pathway Closure — paused-partial
path: 41-escalation-remolding
description: Apply the DB-21 drift-class recipe to `.clou/milestones/*/escalations/*.md` as an agent-to-agent decision record, then close the user-modal pathway that was mis-surfacing these files to the UI. All 5 phases shipped functional (escalation_schema, hook_and_permissions, ui_pathway_closure, mcp_file_escalation, regression_tests). Brutalist rework trajectory (cycle 1 → cycle 2: 32 → 29 findings, severity UP, with 3 "fix-incomplete" callbacks and 3 rework-introduced bugs) did not converge — symptom of cycle-type-prescription in rework cycles. Coordinator paused at cycle 2; 23 valid findings deferred to M48 (residual closure under ORIENT-first). Escalations: staleness overridden; F6 (seen-escalations global race) deferred to future parallel-dispatch milestone.
**Depends on:** DB-21 recipe (landed)
**Status note:** Functional code ships; residual brutalist findings closed under M48 post-M40.

### 42. decisions.md Classifications Remolding — sketch
path: 42-decisions-classifications-remolding
description: Apply DB-21 recipe to the classification entries in `decisions.md`. Per DB-21's "Pending application" table: smaller than assessment — classifications-only, keep coordinator narrative freeform. Today four agent roles (assess-evaluator, coordinator-assess, coordinator-verify, coordinator-replan) write five different cycle-entry schemas (`### Valid:`, `### Noise:`, `### Architectural:`, `### Security:`, `### Accepted:`, `### Overridden:`, `### Tradeoff:`) into the same file with no merge semantics, and three modules (validation.py, recovery_compaction.py, recovery_checkpoint.py) regex-parse them back. `DecisionsForm` + `clou_append_decisions` with merge-by-cycle-num (like `clou_append_classifications`) retires 8+ A-class regex sites downstream. Coordinator's freeform judgment prose stays untouched.
**Depends on:** DB-21 recipe (landed), Adaptive Read-Set Composition (M40)
**Independent of:** Escalation Remolding

### 43. handoff.md Remolding — sketch
path: 43-handoff-remolding
description: Apply DB-21 recipe to `.clou/milestones/*/handoff.md`. Per DB-21's "Pending application" table: full vertical slice, ~same scope as assessment. Today coordinator-exit.md and verifier.md both prescribe a rigid 8-section template (`## Environment`, `## What Was Built`, `## Walk-Through` with `### Flow {N}:`, `## Manual Steps`, `## Third-Party Services`, `## What the Agent Verified`, `## Known Limitations`, `## What to Look For`) that agents must replicate verbatim. `HandoffForm` with sub-dataclasses per section + `clou_write_handoff` writer tool collapses the two prompts' templates into one schema.
**Depends on:** DB-21 recipe (landed), Adaptive Read-Set Composition (M40)
**Independent of:** Escalation Remolding, decisions.md Classifications Remolding

### 44. intents.md Schema-First Integration — sketch
path: 44-intents-schema-first
description: Apply DB-21 recipe to `.clou/milestones/*/intents.md`. Per DB-21's "Pending application" table: integrate into schema-first. An `ArtifactForm` validation already exists for this file; the missing pieces are the dataclass-centric render/parse functions, a `clou_write_intents` MCP writer (or extend `clou_create_milestone`), and hook-level Write denial for the supervisor tier once the writer is live. Smaller than assessment because validation infrastructure already partially exists.
**Depends on:** DB-21 recipe (landed), Adaptive Read-Set Composition (M40)
**Independent of:** Escalation Remolding, decisions.md Classifications Remolding, handoff.md Remolding

### 45. Supervisor-Artifact DB-21 Adjudication — sketch
path: 45-supervisor-artifact-adjudication
description: Audit `memory.md`, `roadmap.md`, and `understanding.md` against DB-21's "When to apply" rubric — apply the recipe only to files whose validator has errored/warned in the wild, not preemptively. DB-21 explicitly exempts narrative prose with no structural validation. Current state: supervisor is the sole writer of all three; `graph.py` regex-parses roadmap.md (10 A-class sites); `recovery_compaction.py` regex-parses memory.md (4 A-class sites). Rubric outputs per file: either (a) remold via DB-21 recipe with `MemoryForm` / `RoadmapForm` / `UnderstandingForm`, or (b) document a deliberate freeform exemption with a parse-tolerance contract for the downstream reader. The adjudication is the milestone; the remolding (if warranted) spawns follow-up milestones rather than living inside this one.
**Depends on:** DB-21 recipe (landed), Adaptive Read-Set Composition (M40)

### 46. execution.md Direct-Write Loophole Closure — sketch
path: 46-execution-direct-write-closure
description: `clou_write_execution` exists but `Write` to `phases/*/execution.md` is still granted to the worker tier — a DB-21 step 5 anti-pattern ("don't grant direct Write alongside enforced structure"). Close the loophole: remove `execution.md` from worker `WRITE_PERMISSIONS`, add the MCP tool to the worker `AgentSpec.tools`, add a hook-deny regression test, update `worker.md` prompt to instruct tool invocation rather than direct authoring. Small, mechanical; the slug-drift remolding (DB-21) already partly paved this path.
**Depends on:** DB-21 recipe (landed), Adaptive Read-Set Composition (M40)

### 47. Downstream Regex Collapse — sketch
path: 47-downstream-regex-collapse
description: The P0 regex inventory (128 sites, 89 class-A LLM-output) collapses progressively as each DB-21 remolding lands. After M41-M44 and whichever sub-milestones M45 produces, sweep `validation.py`, `recovery_compaction.py`, `recovery_checkpoint.py`, `recovery_selfheal.py`, `recovery_consolidation.py`, and `graph.py` — for every regex over a remolded artifact, replace with `parse_<artifact>()` + dataclass field access. Final scan invariant: zero class-A regex sites reading a remolded artifact. Any remaining class-A regex (on artifacts deliberately left freeform per M45's adjudication) is documented as an intentional exception, not tech debt.
**Depends on:** Escalation Remolding, decisions.md Classifications Remolding, handoff.md Remolding, intents.md Schema-First Integration, M48 Residual Findings Closure

### 48. M41 Residual Findings Closure — sketch
path: 48-m41-residual-findings-closure
description: Close the 23 valid findings (8 critical, 15 major, 6 minor) deferred from M41's cycle-2 ASSESS. Critical cluster: 3 "cycle-1-fix-incomplete" callbacks (F1 CR bypass + DB-15 D5 raw regex + unescaped supervisor notes, F3 fail-closed dead code via pre_matcher exclusion, F5 recovery writers same-second overwrite), 3 rework-introduced bugs (F2 writer/reader Disposition inconsistency — clou_resolve_escalation edits first vs parse_escalation reads last, F6 _atomic_write tmp-file unlink races + collateral .tmp deletion, F7 three-source enum divergence), 1 scope reveal (F4 supervisor cannot file escalations — split-ownership one-way), 1 architectural callback (F8 clou_remove_artifact trusts tolerant parser as deletion gate). Major cluster centers on containment gaps (F11 symlink vector, F15 _PATH_KEYS fail-open, F9 interpreter alternation holes) and robustness (F12 null-byte ValueError leak, F13 DoS surface, F14 final-cycle flush race, F16/F17 writer defects, F18/F21 classification/disposition enforcement holes). Runs under the new ORIENT-first architecture so the coordinator can perceive its own rework trajectory. Also a live test of whether ORIENT-first eliminates the cycle-type-prescription symptom that stalled M41.
**Depends on:** Adaptive Read-Set Composition (M40)

### 49a. Trajectory-Halt Bridging — completed
path: 49a-trajectory-halt-bridging
description: Worker probe-pattern suppression (three PreToolUse/PostToolUse rules: Bash-pytest-background deny, Task deny, duplicate-signature system-reminder) + halt-primitive scaffolding (classification extension `trajectory_halt` in `VALID_CLASSIFICATIONS` + `clou_halt_trajectory` MCP verb writing EscalationForm with three pre-populated options). G1+G2 brutalist gates converged (0 findings round 2). 420 tests pass, zero regressions. The halt verb writes escalation files correctly but the engine did not yet honor them — inert, activated by M49b. See `.clou/milestones/49a-trajectory-halt-bridging/handoff.md`.

### 49b. Trajectory-Halt Engine Activation — completed
path: 49b-trajectory-halt-engine
description: Engine halt gate + supervisor re-entry + brutalist-grounded spec corrections. Three rounds of brutalist review (B5 engine state machine, B7 supervisor re-entry, B9 final integrated slate) each produced convergent findings that became dedicated fix layers (C1-C4, D1-D5, E1-E5). Engine halt gate at top of `run_coordinator` loop + pre-COMPLETE gate (C2) + `clou_dispose_halt` supervisor verb with atomic checkpoint-first ordering (D1) + `pre_halt_next_step` stash preservation threaded through 9 rewrite sites + HALTED defense on both stash fields (D3) + single-source-of-truth option labels (D4) + dispatch-primacy in supervisor prompt (E5) + supervisor allow-list (E2) + runtime prompt sync (E3) + halt-gate parse-failure telemetry (E4). 569 tests pass, ~80 new, zero regressions. Full halt → dispose → resume arc covered by integration test (B8). See `.clou/milestones/49b-trajectory-halt-engine/handoff.md`.

### 49. Trajectory-Breakdown Halt Primitive (ORIENT-first integration) — sketch
path: 49-trajectory-breakdown-halt
description: Post-M40 integration of the halt primitive under the ORIENT-first architecture — the coordinator's judgment layer chooses HALT as a cycle-type action alongside PLAN/EXECUTE/ASSESS/VERIFY/EXIT, grounded in ORIENT's reality-driven read set rather than rule-based staleness tripwires. M49a+M49b shipped the scaffolding + engine activation under the pre-ORIENT architecture and the halt fired in production during M36 cycle 4 (first dog-food firing, acceptance criterion #4 earned); M49 completes the integration under the new dispatch model so HALT becomes a first-class ORIENT choice rather than an exceptional coordinator verb.
**Depends on:** Cycle-Type Prescription Dissolution (M39), M41 Residual Findings Closure (M48)
**Motivating trace:** `telemetry/8d4c7878a56c.jsonl` cycle-span records 58→33→28; F28 meta-finding assessment.md:277-288; coordinator proposal filed 12:05 UTC 2026-04-22 without halting current loop; M36 cycle 4 halt escalation `.clou/milestones/36-orient-cycle-prefix/escalations/20260423-223840-trajectory-halt.md` (production firing).
