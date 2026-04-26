# Proposal: Crash-safe ORIENT-exit restoration

**Filed by:** coordinator for milestone `36-orient-cycle-prefix`, cycle 3
**Estimated scope:** afternoon
**Depends on:** 36-orient-cycle-prefix

## Rationale

M36's ORIENT-exit restoration block runs only on iteration 2+ of a SINGLE run_coordinator() invocation (coordinator.py:1291 gate: `not first_iteration and checkpoint_path.exists()`). Every process start begins with `first_iteration=True`; if a checkpoint already says `next_step=ORIENT` (which it will, post-ORIENT), the session-start rewrite is skipped at coordinator.py:1366 and determine_next_cycle() routes ORIENT again at recovery_checkpoint.py:541. Any process crash or clean exit between ORIENT completion and the subsequent in-process restoration means a completed ORIENT re-runs on the next session — a replay trap that burns a fresh ORIENT pass before real work resumes. The in-process restoration is structurally not crash-safe. Fixing this requires one of: (a) a "restoration pending" flag that survives process death; (b) session-start ORIENT rewrite that skips when pre_orient_next_step is already populated; or (c) running restoration unconditionally at session start (not gated by iteration 2+). Each option has semantics trade-offs (cycle-numbering, telemetry boundaries, what "session start" means when restoration completes) that warrant dedicated design rather than a reflex one-liner patched into M36. The common case (iteration 2+ restoration within a single process) works correctly; crash-restart is a latent risk rather than a permanent regression — which is the exact signature of a follow-up milestone rather than an in-milestone blocker.

## Cross-Cutting Evidence

coordinator.py:1291 (first_iteration gate); coordinator.py:1366 (session-start rewrite skip path); recovery_checkpoint.py:541 (determine_next_cycle routes ORIENT when next_step==ORIENT); .clou/milestones/36-orient-cycle-prefix/assessment.md F7 (Cycle 3 Round 1); codex critic single-source with concrete failure mode walkthrough.

## Recommendation

Sequence after M36 completes. Consider folding alongside M37's "judgment gates dispatch" work if the semantics-redesign option (making restoration crash-safe at session start) dovetails with the dispatch-authority promotion.

## Disposition

status: superseded

Superseded by the later cycle-4 draft 20260423-223452-crash-safe-orient-exit-restoration.md, which adds the cycle_started_at + judgment_path design suggestion.
