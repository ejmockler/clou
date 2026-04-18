# Understanding

Durable conceptual memory. Each entry traces to a specific
user response via ask_user — never updated silently.

## What this project is becoming

## Active tensions

### SDK 0.1.60 supersedes M27's transcript capture path
- **Asked:** (user-initiated) "are we using the latest llms available across our stack?" — led to discovering SDK was 14 versions behind.
- **Response:** Bumped 0.1.49 → 0.1.63 cleanly (no breaking changes, no APIs Clou uses changed). 640 SDK-adjacent tests still green.
- **Framing:** SDK 0.1.60 ships `list_subagents()` and `get_subagent_messages()` — the SDK now exposes natively what M27 had to build via PostToolUse hook capture in `transcript_store.py`. Could simplify or replace M27's capture mechanism. Not blocking; not part of the ORIENT-first redesign arc; a latent simplification opportunity worth a separate look once the architectural redesign settles. File a milestone after M36–M40 land if the M27 surface still warrants reduction.
- **When:** 2026-04-18
- **Fed into:** (pending — not yet milestone-shaped)

## Continuity

## Resolved
### Task graph observability lacks depth
- **Asked:** What works but not well enough in Clou right now?
- **Response:** When the coordinator runs, the user can't drill into the task graph to satisfaction. No progressive way to see the context window of each agent, tool calls, etc. Should respect the existing design system and foundations.
- **Framing:** The task graph shows topology but not substance. The gap is between "task X is running" and "here's what agent X is seeing, thinking, and doing." Need progressive drill-down that's both live (real-time streaming during execution) and reviewable (after-the-fact inspection). Two depth layers: structured activity summary (reads, writes, decisions) at the top, raw tool call stream with expandable I/O underneath. Hybrid navigation: inline preview within the task graph widget, full screen for deep inspection. All consistent with the design system — breathing for liveness, progressive disclosure for depth, atmospheric not demanding.
- **When:** 2026-04-08
- **Fed into:** intents.md, milestone.md, requirements.md (26-agent-observability)

### SDK agent data is name-only — need transcript access
- **Asked:** The SDK only gives tool names for coordinator agents. Given this, what matters more — real-time enrichment or post-hoc inspection?
- **Response:** Both, but post-hoc is higher priority. Being able to review the full agent conversation after completion matters more than live streaming of rich data.
- **Framing:** The SDK's TaskProgressMessage is structurally minimal for agents (name + count only). Real-time enrichment requires deeper SDK integration. Post-hoc inspection can work from agent transcripts/conversation histories if they're persisted or capturable. Post-hoc first, real-time enrichment second.
- **When:** 2026-04-09
- **Fed into:** intents.md, milestone.md, requirements.md (27-agent-transcript-inspection)

### Real-time tool I/O during execution
- **Asked:** (user-initiated) User said "do it" when presented with real-time enrichment as a potential next step.
- **Response:** Directive — no hedging. Wants live tool I/O as agents work.
- **Framing:** M27's PostToolUse hook already captures tool calls in real-time. The gap is that enrichment only fires on detail screen open for completed agents. Wire the captured data to the UI as it arrives — detail screen gets live enriched entries, inline preview updates with substance. Post-hoc path stays unchanged.
- **When:** 2026-04-09
- **Fed into:** intents.md, milestone.md, requirements.md (28-realtime-enrichment)

### Memory lifecycle is designed but not wired
- **Asked:** (user-initiated) User asked how Clou handles growing golden context and how it should.
- **Response:** Directive — "can we structure everything we've identified into a milestone? a large task graph mapping out everything we must do? all priorities." Pre-converged, no hedging.
- **Framing:** DB-18 is fully designed but only ~40-50% wired after M22/M23. Seven gaps: decay never persists status fields, compaction has zero callers, temporal invalidation never invoked, supervisor annotation not built, understanding.md has no lifecycle, scored retrieval is protocol-only, consolidation overwrites instead of accumulating. One wide milestone to complete all of it.
- **When:** 2026-04-10
- **Fed into:** intents.md, milestone.md, requirements.md (29-memory-lifecycle-completion)

### Research foundations gap: mechanism without evidence
- **Asked:** Have we really realized a full implementation of our research foundations?
- **Response:** No. The infrastructure is built but nothing has run. Telemetry emits into void, per-intent execution.md never filled, convergence enforcement never rejected a real compose.py. Step 7 (cross-milestone typed deps) is completely disconnected — validate_roadmap() exists but nothing creates, reads, or validates a roadmap.py. The DB-08 annotation format is the simpler path.
- **Framing:** The system has mechanisms built but never exercised (Steps 1, 4, 6, 8, C), stubs waiting on data (Steps 3, 8), and disconnected scaffolding (Step 7). Step 7 is the only one that can be wired now — the others need accumulated telemetry from real milestone runs. Wire Step 7 with full parallel dispatch first, then run the system to collect evidence on everything.
- **When:** 2026-04-11
- **Fed into:** intents.md, milestone.md, requirements.md (31-cross-milestone-parallel-dispatch)

### Orchestrator cleanup authority for obsolete scaffolding
- **Asked:** Coordinator couldn't delete `.clou/roadmap.py.example` (write boundary). Should we reconcile (keep boundary, escalate via handoff), expand coordinator access, or extend orchestrator cleanup?
- **Response:** Extend orchestrator cleanup authority — it already owns DB-18 lifecycle cleanup (telemetry, episodic archival). Obsolete scaffolding is the same category of housekeeping.
- **Framing:** The orchestrator gains authority to delete/modify root-level `.clou/` files flagged as obsolete. The coordinator boundary stays milestone-scoped. When a milestone deprecates a project-root file, the handoff flags it, and the orchestrator's post-milestone cleanup handles removal. This extends the existing DB-18 cleanup pipeline rather than creating a new permission exception.
- **When:** 2026-04-11
- **Fed into:** intents.md, milestone.md, requirements.md (32-orchestrator-cleanup-authority)

### Cycle-type-as-prescription is a class of bug
- **Asked:** (user-initiated) "examine the telemetry. is this what clou really is doing?" — re: a screen showing Clou in PLAN #2 with four pending phases in the brutalist-mcp safety-net milestone.
- **Response:** No — the UI was concealing a re-planning loop. Telemetry showed all 4 phases had executed successfully ~30 minutes earlier (239 tests passing, files on disk), but cycle counter oscillated 1→2→3→2→3 across re-spawns. After 3 PLAN repeats with `phases_completed:0`, the staleness guard finally fired with "Cycle type 'PLAN' has repeated 3 times with phases_completed stuck at 0."
- **Framing:** The coordinator's own decisions.md correctly diagnosed the bookkeeping inconsistency three cycles in a row but had no affordance to fix it. Root cause: PLAN's read set is `[milestone.md, intents.md, requirements.md, project.md]` — execution evidence is not in PLAN's sight. PLAN re-derives status.md from compose.py (defaulting all phases to pending) and writes `phases_completed:0` because PLAN has no execution context. The bug isn't a missing path in the read set — it's that *cycle types prescribe both the agent's role and what it can perceive*, so reality (execution.md) cannot enter PLAN by design. Same pattern produces M34's reference-density 0.0 finding (PLAN reads 4 files, cites zero) and the staleness loop — both are symptoms of cycle-type-as-prescription.
- **When:** 2026-04-18
- **Fed into:** roadmap.md (36–40 ORIENT-first redesign arc)

### ORIENT-first as the structural cure
- **Asked:** "what's truly the best; structurally correct and resonant with the foundations of transformers at scale and the emergent capabilities and tendancies of the agent?"
- **Response:** Replace categorical cycle dispatch with reality-driven orientation. Every coordinator session begins with ORIENT (small adaptive read set), emits a structured judgment `{next_action, rationale, evidence_paths, expected_artifact}`, then runs the chosen behavior. Cycle types become a vocabulary the coordinator uses to describe what it's about to do, not states the orchestrator dispatches. Bookkeeping shifts from coordinator-declared to orchestrator-computed-from-artifacts (extends M21 R1). Read sets become situational, not categorical (cures M34's reference-density 0.0 structurally).
- **Framing:** Three foundations resonate. §2 (attention IS retrieval): first tokens are architecturally privileged — anchor on observed truth, not assigned role, so the model retrieves "completion-shaped" patterns when reality is completion-shaped. §16 (memory is retrieval, sparse > broad): ORIENT is sparse retrieval applied to the agent's own situation; reference density 0.0 becomes structurally impossible because the agent reads what it cites. §9 (Kambhampati): verification-first, generation-second — PLAN runs only when ORIENT confirms the gap requires planning. The redesign decomposes into five sequential-with-parallelism milestones: ORIENT prefix → ORIENT gating ∥ artifact-authoritative bookkeeping → cycle-type prescription dissolution → adaptive read-set composition. Each independently shippable; each leaves the system in a better state.
- **When:** 2026-04-18
- **Fed into:** roadmap.md (36–40 ORIENT-first redesign arc)

