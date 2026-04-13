# Understanding

Durable conceptual memory. Each entry traces to a specific
user response via ask_user — never updated silently.

## What this project is becoming

## Active tensions

## Continuity

## Resolved
### Task graph observability lacks depth
- **Asked:** What works but not well enough in Clou right now?
- **Response:** When the coordinator runs, the user can't drill into the task graph to satisfaction. No progressive way to see the context window of each agent, tool calls, etc. Should respect the existing design system and foundations.
- **Framing:** The task graph shows topology but not substance. The gap is between "task X is running" and "here's what agent X is seeing, thinking, and doing." Need progressive drill-down that's both live (real-time streaming during execution) and reviewable (after-the-fact inspection). Two depth layers: structured activity summary (reads, writes, decisions) at the top, raw tool call stream with expandable I/O underneath. Hybrid navigation: inline preview within the task graph widget, full screen for deep inspection. All consistent with the design system — breathing for liveness, progressive disclosure for depth, atmospheric not demanding.
- **When:** 2026-04-08
- **Fed into:** intents.md, milestone.md, requirements.md (26-agent-observability)

### Compose.py is docstrings with structural decoration
- **Asked:** What's the problem in the supervisor/planning layer with milestone scoping and decomposition?
- **Response:** User pointed at M23's task graphs — compose.py has empty NamedTuples, meaningless fields, specs in docstrings. DB-02 already prescribes the fix (graph.py validator, one-line docstrings, phase.md separation, width enforcement) but none of it is built.
- **Framing:** The compose.py format carries no verifiable information. The decision boundary is decided but not implemented. The coordinator generates hollow typed DAGs. Research (Routine 2025, Section 19) shows structural enforcement is the fix, not more prompt guidance.
- **When:** 2026-04-08
- **Fed into:** intents.md, milestone.md, requirements.md (24-verified-decomposition)

### Lifecycle pipeline: designed but not wired
- **Asked:** One milestone for the full DB-18 lifecycle, or fix the broken pipeline first and extend in a second?
- **Response:** Two milestones — fix broken pipeline first, then extend with the remaining DB-18 mechanisms.
- **Framing:** Three mechanisms have code but don't fire: _apply_decay() (status field never persisted), archive_milestone_episodic() (consolidated gate fails), compact_decisions() (zero callers). First milestone wires these so consolidation → decay → archival → compaction runs end-to-end. Second milestone implements the five designed-but-unbuilt DB-18 mechanisms.
- **When:** 2026-04-06
- **Fed into:** intents.md, milestone.md, requirements.md (22-lifecycle-pipeline-wiring)

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

