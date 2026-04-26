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

### M36 checkpoint freeze — supervisor cannot unfreeze
- **Asked:** (supervisor-observed during re-entry 2026-04-23) Permission denied trying to edit `active/coordinator.md` to restore `current_phase: orient_integration`. Supervisor tier is write-scoped to project.md / roadmap.md / requests.md / understanding.md / memory.md / milestone.md / intents.md / requirements.md — not active/coordinator.md.
- **Response:** (pending user input — ask_user stream closed twice)
- **Framing:** When the user hand-edits a coordinator checkpoint to halt dispatch, only the user (or a later coordinator session) can unfreeze it. The supervisor has no authorized write path to the checkpoint body. This is consistent with DB-21's split-ownership model but surfaces a gap: there is no supervisor-tier "un-halt" verb for manually-frozen (as opposed to primitive-halted) milestones. `clou_dispose_halt` only works on engine-gated escalations that wrote `next_step=HALTED`, not on hand-edited `current_phase` stasis. If this resurfaces post-M37/M38 (artifact-authoritative bookkeeping), the class dissolves structurally — status is a render of disk truth and current_phase stops being coordinator-writable drift. Until then, user hand-edit or coordinator re-plan is the only path.
- **When:** 2026-04-23
- **Fed into:** (pending — may inform a future supervisor-tier manual-unfreeze affordance)

### Halt primitive ships end-to-end (M49a + M49b)
- **Asked:** (supervisor-observed during re-entry 2026-04-23) M49a shipped scaffolding 2026-04-22 (halt MCP verb + worker probe-pattern suppression + classification extension). M49b shipped engine activation + supervisor re-entry 2026-04-23 through brutalist-grounded iteration across B5 → C1-C4, B7 → D1-D5, B9 → E1-E5. 569 tests pass, zero regressions, full halt → dispose → resume arc covered by integration test.
- **Response:** (supervisor consolidation) User confirmed intent to resume M36 with halt primitive as safety net: "Resume M36 — orient_integration phase still pending, halt primitive now available if F28-class re-surface appears."
- **Framing:** The trajectory-halt class is now a first-class engine primitive: coordinator can file `clou_halt_trajectory` during ASSESS when re-surface is diagnosed; engine halt gate (top of run_coordinator loop + pre-COMPLETE gate) exits cleanly with HALTED_PENDING_REVIEW; supervisor session re-enters via orchestrator guidance naming `clou_dispose_halt`; atomic checkpoint-first write ordering guarantees crash-safe replay; stash preservation threading across 9 rewrite sites keeps `continue-as-is` semantics intact. M36 resumption is the first production test — if the F28 pattern re-surfaces on ASSESS of orient_integration, the coordinator has an affordance to halt. Acceptance #4 from M49a ("dog-fooded — halt primitive fires on its own work") now extensible: does it fire on M36's own work? TBD.
- **When:** 2026-04-23
- **Fed into:** roadmap.md (M49a, M49b completion entries), active/supervisor.md checkpoint

## Continuity

## Resolved
### M36 cycle-4 halt fires in production — halt primitive validated end-to-end
- **Asked:** M49a + M49b shipped the full halt primitive. How should I disposition the pair?
- **Response:** Exercise the halt first — resume M36 to watch it fire in the wild before accepting.
- **Framing:** Spawned M36 resume. Session-start ORIENT interposition ran. Coordinator's cycle-4 ASSESS detected anti-convergence (33→28→30→36 findings, two phases with literal zero production change across rounds 2-4, new criticals F6/F7 introduced under pretext of fixing) and invoked `clou_halt_trajectory`. Engine halt gate fired at next cycle start, returned `halted_pending_review`, orchestrator guidance named `clou_dispose_halt`. Supervisor re-entered, user chose re-scope, coordinator honored preference (a) converge-and-exit and shipped 5 functional phases with 52 tests. 36 cycle-4 security/architectural findings deferred with named follow-up milestones (M50, M42). First production firing of the full halt → dispose → resume arc. M49a/b acceptance #4 (dog-fooded) earned. Pattern: halt primitive IS the graceful exit from F28-class trajectories; re-scope with preference guidance is a legible supervisor-to-coordinator handoff.
- **When:** 2026-04-23
- **Fed into:** roadmap.md (M36, M49a, M49b → completed), disposition on 20260423-223840-trajectory-halt.md, memory inference pending

### M36 surfaced four pre-M37 hardening prerequisites
- **Asked:** How should I crystallize the five open proposals filed during M36?
- **Response:** One bundled M36.5 hardening milestone before M37 — accept #1, #3, #4, #5; defer #2; supersede the duplicate.
- **Framing:** Four specific pathologies M36 exposed are prerequisites for M37's dispatch-authority promotion. (1) LLMs paraphrase punctuated cycle-type tokens — becomes a routing defect once judgment gates dispatch. (2) ORIENT-exit restoration is in-process-only; process crash between ORIENT write and next-iteration restoration burns the cycle. (3) ASSESS routing keeps current_phase on the assessed cursor even when findings own other phases; two M36 phases (hooks.py, judgment.py) never received rework across 4 rounds. (4) queued rework items in decisions.md have no structural coupling to "rework applied" before phase completion is declared. Bundled as M50 with four intents (user approved verbatim). Sequential before M37. #2 (ORIENT context budget) deferred per own recommendation — needs judgment telemetry to choose strategy empirically.
- **When:** 2026-04-23
- **Fed into:** milestone.md, intents.md, requirements.md (50-orient-gating-prerequisites), roadmap.md (M50 current, M37 Depends-on updated)

### DB-21 rework trajectory surfaces cycle-type-prescription in rework cycles
- **Asked:** M41 trajectory shows cycle 1 ASSESS 32 findings → cycle 2 ASSESS 29 findings (23 valid) with severity UP (8 criticals, 3 "fix-incomplete" callbacks, 3 rework-introduced bugs). How should I dispose the staleness?
- **Response:** Pause M41 — this trajectory IS the M36-M40 evidence. Keep the functional work (5 phases shipped), defer the 23 findings to a follow-up milestone post-M40 where the coordinator under ORIENT-first can perceive its own rework trajectory.
- **Framing:** Rework EXECUTE has no read-set window into prior cycle outcomes, so the coordinator cannot see its own trajectory. New criticals get introduced while old ones close only partially, and the coordinator has no affordance to notice. This is the same cycle-type-prescription symptom the ORIENT-first arc dissolves — applied to rework instead of initial execution. Operationally: M41's 5 phases ship as-is (schema + hook + MCP + UI closure + regression tests are functional); the 23 valid findings are re-scoped into a new milestone sequenced after M40. M42-M47 (pending DB-21 remoldings) gain a `Depends on: ORIENT-first` annotation so they run under the new architecture.
- **When:** 2026-04-21
- **Fed into:** roadmap.md (M41 status change, M42-M47 dependency update, new M48 sketch), escalation dispositions (M41 staleness overridden, F6 deferred)

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

