# Proposal: Rework closure verification in the orchestrator

**Filed by:** coordinator for milestone `36-orient-cycle-prefix`, cycle 2
**Estimated scope:** multi-day

## Rationale

The M36 cycle-1 → cycle-2 transition exposed a systemic process gap: 24 rework items classified `valid` in cycle-1 `decisions.md` were never applied to the codebase, yet the orchestrator advanced through two subsequent layers (judgment_mcp_tool, disagreement_telemetry) and attempted a third (orient_integration, which itself produced zero artifacts). The cycle-2 brutalist gate re-surfaced 22 of those 24 items verbatim; the evaluator classified every re-surfacing as still-valid, and file mtimes on `clou/hooks.py` (Apr 21 23:25), `clou/judgment.py` (Apr 21 23:23), `clou/recovery_checkpoint.py` (Apr 21 23:24), and `clou/_prompts/coordinator-orient.md` (Apr 21 23:24) confirm no code change since initial layer-1 execution. The rework cycle did not discharge its work, but the phase status transitioned to `completed` anyway. This is not a one-off M36 defect — it is a latent property of the rework loop: nothing structurally couples "cycle-N ASSESS queued these rework items" to "cycle-N+1 EXECUTE applied them" before phase completion is declared. Any milestone using the rework pattern inherits this gap. The memory pattern `rework-frequency` already fires in M20/M21/M22/M29/M35/M41 — if those cycles had similar silent-skip behavior, the resulting product would be quietly under-reworked.

## Cross-Cutting Evidence

M36 cycle-1 decisions.md queued 24 rework items across judgment_schema, hook_and_permissions, orient_protocol phases. M36 cycle-2 assessment.md re-surfaced F2/F3/F4/F6/F7/F8/F9/F10/F11/F18/F19/F20/F21/F22/F24/F28 as direct re-surfacings of cycle-1 F1/F31/F35/F6/F7/F5/F23/F11/F8+F9+F10+F12+F13+F14/F58/F3/F24/F34/F29/F13. File mtimes in /Users/noot/clou/clou/ for hooks.py/judgment.py/recovery_checkpoint.py/_prompts/coordinator-orient.md all predate layer-2 start. Current .clou/milestones/36-orient-cycle-prefix/status.md marks judgment_schema/hook_and_permissions/orient_protocol as "completed" despite unapplied rework. Memory pattern rework-frequency observed at 36-orient-cycle-prefix reinforced:6 last_active:41-escalation-remolding — active recurring signal. Memory pattern quality-gate-availability observed at 36-orient-cycle-prefix reinforced:17 also reinforces this — multiple prior milestones had the same process shape.

## Recommendation

Extend the orchestrator's phase-advancement guard: before marking a phase `completed`, require that each rework item queued in the most recent cycle's decisions.md has either (a) been applied AND re-asserted by a subsequent brutalist pass on the same phase (with no re-surfacing), or (b) been explicitly dismissed by a subsequent coordinator judgment logged in decisions.md. Candidate mechanisms: (1) a typed `rework_queue` field on the Checkpoint dataclass that survives across cycles, drained only by subsequent assessment confirmations; (2) a separate `active/rework_queue.md` that the coordinator reads during EXECUTE (rework) and that ASSESS must reconcile against its own findings; (3) instrumentation that diffs `assessment.md` across cycles and telemetry-alerts if the rework item count does not monotonically decrease. Suggest sizing as multi-milestone scope: checkpoint schema extension + orchestrator guard + rework-queue persistence + cross-cycle reconciliation tests + retroactive audit of M20/M21/M22/M29/M35/M41 rework cycles to check whether they have the same silent-skip shape.

## Disposition

status: accepted

Crystallized as M50 intent I4 (rework closure verification as orchestrator-side guard at phase-completion boundary). Complementary to M38's artifact-authoritative bookkeeping.
