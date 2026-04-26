# Proposal: M51 — Vocabulary canonicalization structural cleanup + UI/test hardening

**Filed by:** coordinator for milestone `50-orient-gating-prerequisites`, cycle 1
**Estimated scope:** multi-day
**Depends on:** 50-orient-gating-prerequisites

## Rationale

M50 cycle-5 trajectory halt and cycle-1 ASSESS surfaced a consistent pattern: the M50 vocabulary canonicalization sweep closed the surface drift (legacy tokens → structured tokens) but left structural debt in the substrate that ENABLED the drift in the first place. The supervisor's M50 re-scope disposition (escalations/20260425-031626-trajectory-halt.md) explicitly committed to crystallizing M51 after M50 ships; this proposal captures the scope.

Three categories:

(A) **Structural cure for F2/F3 split-brain recovery model.** The `_LEGACY_NEXT_STEPS` dict (recovery_checkpoint.py:130-134) collapsed two incompatible mappings into one — `EXECUTE (rework) → reject-to-PLAN` and `none → coerce-to-COMPLETE`. The single dict drives four consumer behaviors keyed by field position. Future maintainers cannot reason about token semantics by membership alone. **Fix shape:** split into `_LEGACY_REJECT_TOKENS` (set) and `_LEGACY_COERCE_TOKENS` (dict[str, str]); migration consumes coerce; parser consumes both. Closes F14 (CLAUDE Medium architecture). Same root cause as F2/F3 — but the M50 re-scope addresses F2/F3 mechanically (revert F13 overreach), this addresses the structural enabler.

(B) **Routing-layer architectural rot.** `determine_next_cycle()` (recovery_checkpoint.py:430) is a god-function fusing routing + side-effecting disk mutations + AST parsing of compose.py + regex over decisions.md. Routing must be a pure function of state. F8 (GEMINI High + CODEX Medium two-model surfacing). Adjacent: `_filtered_memory.md` boundary check is TOCTOU-prone (F9 CODEX Medium). Also: `is_execute_family` lives in recovery_checkpoint.py but is imported by prompts.py — layering violation (F10 CLAUDE Major); the predicate belongs in a neutral cycle_types module.

(C) **UI/test hardening backlog.** `_CYCLE_COLOR_MAP` missing EXECUTE_REWORK / EXECUTE_VERIFY (F5 three-CLI surfacing) — rework cycles render as muted-gray instead of teal-execute family. task_graph widget ignores spawn_cycle — unmapped agents from prior phases render as current activity (F13 CODEX High). Two coordinator-owned write paths still use raw `write_text` instead of `_atomic_write` (F6 CODEX Critical, coordinator.py:1093, 1158). Path interpolation in build_cycle_prompt has no whitelist — typo `EXEUCTE` produces a path to a nonexistent file with no signal (F11 CLAUDE Major). Case-sensitivity asymmetry: `protocol_stem.lower()` vs exact-match uppercase write-path branches (F12 CLAUDE Major). build_cycle_prompt branch coverage gap: PLAN write paths, VERIFY extra path, EXIT path, root-scoped memory.md have no direct test coverage (F28 CODEX High). Migration startup tax O(milestone history) with no completion sentinel (F7 CODEX High + CLAUDE Low). Test-quality items: F22 (anti-fix theater test), F23 (single-line lint regex), F24 (asymmetric 'none' migration coverage), F25 (case-sensitive probe missing 'none'), F26 (caplog never queried), F27 (F5 protocol_file existence-only test).

The M50 supervisor disposition explicitly defers all of (B) + (C) plus F22/F27/the cycle-5 valid items minus F2/F3/F4 to M51. (A) is the structural cure for the F2/F3 split-brain class that M50's mechanical revert addresses but doesn't refactor.

## Cross-Cutting Evidence

Evidence paths:
- .clou/milestones/50-orient-gating-prerequisites/escalations/20260425-031626-trajectory-halt.md (supervisor's M51 commitment in disposition)
- .clou/milestones/50-orient-gating-prerequisites/assessment.md:300-435 (28 classified findings; 7 architectural + ~10 valid-deferred)
- clou/recovery_checkpoint.py:130-134 (_LEGACY_NEXT_STEPS dict — F14 root cause)
- clou/recovery_checkpoint.py:430 (determine_next_cycle god-function — F8)
- clou/recovery_checkpoint.py:660,676,683 (TOCTOU on _filtered_memory.md — F9)
- clou/prompts.py:83 (is_execute_family layering violation — F10)
- clou/prompts.py:87-88 (path interpolation no whitelist — F11)
- clou/ui/theme.py:129-134 + clou/ui/widgets/task_graph.py:513 (_CYCLE_COLOR_MAP drift — F5)
- clou/ui/widgets/task_graph.py:428 (spawn_cycle ignored — F13)
- clou/coordinator.py:1093, 1158 (raw write_text — F6)
- clou/coordinator.py:1219 (migration startup tax — F7)
- tests/test_vocabulary_migration.py:1513-1533 (single-line lint regex — F23)
- tests/test_vocabulary_migration.py:1794-1843 (anti-fix theater test — F22)
- tests/test_prompts.py:962-989 (existence-only protocol_file test — F27)

Three-model surfacing for F5; two-model for F2/F3, F4, F7, F8. Items repeated across cycle-3, cycle-4, cycle-5 ASSESS rounds without closure.

## Recommendation

Crystallize after M50 ships. Primary structural deliverable is the (A) split of _LEGACY_NEXT_STEPS — that's the cure for the recurring split-brain class. (B) and (C) are aggregate hardening; can be scoped per-intent or sequenced into a layered M51/M52 split if scope is too wide. The supervisor's M50 disposition pre-committed to this work, so the proposal is structuring input rather than novel scope.

## Disposition

status: open
