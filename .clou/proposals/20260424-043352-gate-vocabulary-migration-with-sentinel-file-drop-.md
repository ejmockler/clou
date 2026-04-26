# Proposal: Gate vocabulary migration with sentinel-file (drop session-start tax)

**Filed by:** coordinator for milestone `50-orient-gating-prerequisites`, cycle 2
**Estimated scope:** afternoon
**Depends on:** 50-orient-gating-prerequisites

## Rationale

M50 I1 ships a vocabulary migration that runs at every coordinator session start (clou/coordinator.py:1184). Production data shows the migration is a one-shot: first run rewrote {checkpoints:0, decisions:9, judgments:3, prompts:2}; second run was {0,0,0,0}; subsequent runs will all be {0,0,0,0} for the rest of the project's life. Yet the helper does a full directory traversal of every milestone, every judgments/*.md, every decisions.md, and every prompt mirror on every coordinator start — O(total persisted files) per startup, scaling forever with project age. At ~50 milestones with ~5 judgments each, that's ~250 extra `read_text` calls per session, paid forever. Two clean fixes: (a) gate the call with a `.clou/.migrations/m50-i1.done` sentinel file; (b) extract to a `python -m clou.vocabulary_migration` script invoked once during release/upgrade. Either eliminates the steady-state cost. The current design satisfies M50 I1's 'session-start OR one-shot script' permission, but session-start was the wrong pick at scale.

## Cross-Cutting Evidence

M50 phase i1 brutalist assessment: F9 (codebase: 'idempotent is not cheap'); F21 (architecture: 'session-start hook scales with total historical artifact count'); F29 (architecture: 'session-start placement is the wrong layer — anti-scaling for a problem that resolves on run 1'). Production data from execution.md confirms the one-shot nature: first run {0,9,3,2}, second run {0,0,0,0}. The architecture team agreed this should be sentinel-gated or extracted to a release-time script.

## Recommendation

Afternoon milestone after M50 completes. Add a `.clou/.migrations/{milestone}-{intent}.done` sentinel-file convention; gate `migrate_legacy_tokens` call behind sentinel check; persist sentinel after successful sweep. Keep the helper itself unchanged (idempotency contract preserved). One sentinel per future migration; reusable pattern.

## Disposition

status: open
