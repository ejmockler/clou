# Proposal: Crash-safe ORIENT-exit restoration

**Filed by:** coordinator for milestone `36-orient-cycle-prefix`, cycle 3
**Estimated scope:** day
**Depends on:** 36-orient-cycle-prefix

## Rationale

M36's ORIENT-exit restoration (coordinator.py:1291-1339) is an in-process guarantee only. The gate `not first_iteration and checkpoint_path.exists()` ensures restoration fires on iteration 2+ of a SINGLE run_coordinator invocation, but every fresh run_coordinator() starts with first_iteration=True. A process crash after ORIENT completes but before the next iteration's restoration runs leaves next_step=ORIENT on disk; the next session's session-start rewrite (coordinator.py:1366) skips because checkpoint already says ORIENT, and determine_next_cycle (recovery_checkpoint.py:541) dispatches ORIENT again. Result: completed ORIENT re-runs on next session, burning a cycle and likely reusing the same cycle number for the judgment path. This is a latent invariant failure that becomes load-bearing once M37 gates dispatch on judgment.next_action. Fixing it requires re-designing the restoration invariant (persistent "restoration pending" flag, OR unconditional session-start restoration when pre_orient_next_step is populated, OR idempotent ORIENT with dedupe on judgment path). Each option has trade-offs worth a dedicated milestone — the M36 scope explicitly held dispatch authority unchanged, and a persistent-flag design crosses that line.

## Cross-Cutting Evidence

.clou/milestones/36-orient-cycle-prefix/assessment.md (F7 — crash-replay trap, codex single-source but architecturally grounded); clou/coordinator.py:1291 (in-process-only gate); clou/coordinator.py:1366 (session-start rewrite skips when next_step==ORIENT); clou/recovery_checkpoint.py:541 (determine_next_cycle routes ORIENT on the second session); .clou/milestones/36-orient-cycle-prefix/decisions.md (Cycle 3 Round 2 F7 classification as architectural/out-of-milestone).

## Recommendation

Sequence after M36 closes and before M37 promotes judgment.next_action to gating authority. At that promotion, crash-replay of ORIENT is no longer cosmetic — it's dispatch-authority drift. Suggested approach: add a `cycle_started_at` timestamp and `judgment_path` field to the checkpoint; session-start detects completed ORIENT via judgment file existence and reuses the existing judgment rather than re-running the cycle.

## Disposition

status: accepted

Crystallized as M50 intent I2 (crash-safe ORIENT-exit restoration via judgment-file idempotency key).
