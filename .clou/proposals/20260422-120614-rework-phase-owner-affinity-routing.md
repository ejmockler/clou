# Proposal: Rework phase-owner affinity routing

**Filed by:** coordinator for milestone `36-orient-cycle-prefix`, cycle 2
**Estimated scope:** day

## Rationale

Cycle-2 ASSESS rounds 1 and 2 both surfaced a routing defect in the coordinator's ASSESS protocol: when rework is needed, `current_phase` is kept at the assessed phase rather than re-targeted to the phase whose `phase.md` actually owns the code being critiqued. In milestone 36, cycle-2-r1 routed 24 cycle-1 rework items + 9 new findings to `orient_integration` — a phase whose `phase.md` §"Out of scope" explicitly forbids modifying phase-owner modules. The worker faithfully honored its `phase.md` and added tests only; production code remained unchanged. Cycle-2-r2 then found 28 new findings, 5+ of which are anti-fix tests that pin the unfixed bugs as contract. The convergence trajectory inverted: Layer1=58, r1=33, r2=28 new findings with CI green actively defending architectural rot. Root orchestrator-tier cause: ASSESS routing treats `current_phase` as a monotonic cursor rather than as a per-finding owner lookup.

## Cross-Cutting Evidence

File mtimes (shipped code unchanged since layer 1): clou/judgment.py Apr 21 23:23, clou/hooks.py Apr 21 23:25, clou/recovery_checkpoint.py Apr 21 23:24, clou/_prompts/coordinator-orient.md Apr 21 23:24. Ownership per .clou/milestones/36-orient-cycle-prefix/compose.py: F1/F2/F12/F13/F18 → orient_protocol; F7/F8/F9/F10/F22/F27 → hook_and_permissions; F11/F14 → judgment_schema. Scope declaration: .clou/milestones/36-orient-cycle-prefix/phases/orient_integration/phase.md §"Out of scope: Modifying any of the phase-owner modules." Evidence of prior-round routing mismatch: .clou/milestones/36-orient-cycle-prefix/decisions.md "Cycle 2 — EXECUTE Dispatch" entry documenting the worker's explicit scope reconciliation. Pattern prior art: cycle-2-r1 proposal "Rework closure verification in the orchestrator" filed against a sibling orchestrator-tier concern. Requirements file: .clou/milestones/36-orient-cycle-prefix/assessment.md §F28 (convergence meta-finding documenting the inversion).

## Recommendation

During ASSESS routing, when classified findings have an unambiguous phase-owner (per compose.py module ownership), re-target `current_phase` to that owner before writing the rework checkpoint. Multi-owner rework fans out to sequential EXECUTE cycles, one per owner, with phase-specific briefing scoped by that phase's phase.md. Non-ambiguous findings (integration tests, cross-phase contract) stay on the assessed phase. Candidate to fold into a future ORIENT-arc milestone (phase-owner affinity derivable from judgment.evidence_paths).

## Disposition

status: accepted

Crystallized as M50 intent I3 (phase-owner affinity routing for rework).
