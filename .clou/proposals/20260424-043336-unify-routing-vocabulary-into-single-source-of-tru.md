# Proposal: Unify routing vocabulary into single source of truth

**Filed by:** coordinator for milestone `50-orient-gating-prerequisites`, cycle 2
**Estimated scope:** day
**Depends on:** 50-orient-gating-prerequisites

## Rationale

Routing-vocabulary canonicalization in M50 surfaced a load-bearing dual source-of-truth: `recovery_checkpoint._VALID_NEXT_STEPS` (parser/router authority), `golden_context.VALID_NEXT_STEPS` (renderer/validator authority), `judgment.VALID_NEXT_ACTIONS` (re-export wrapper), and `_LEGACY_NEXT_STEPS` (migration-only data living in a runtime-hot module). The duplication is already realized — the two sets diverge today on the literal `'none'` (recovery rejects it, golden_context accepts it, validation+judgment routes diverge depending on which import executes first). M50 I1 canonicalized the legacy tokens but left the source-of-truth fork untouched. M50 rework will realign `'none'` as a tactical fix, but the broader architectural debt (8 moving parts that must stay aligned for a single token rename) needs a focused milestone: extract a single `clou/vocab.py` (or similar) module owning the canonical sets, the legacy mapping, and the migration helpers; have all consumers import from it; remove duplications. As a bonus cleanup, fold in extraction of `_atomic_write` (currently duplicated byte-for-byte between `clou/coordinator.py` and `clou/vocabulary_migration.py`) into a shared `clou/fs_utils.py`. Both are the same drift-class mistake the M50 migration was trying to fix in a different corner.

## Cross-Cutting Evidence

Concrete evidence from M50 phase i1 brutalist assessment (.clou/milestones/50-orient-gating-prerequisites/assessment.md): F19 `recovery_checkpoint._VALID_NEXT_STEPS` (clou/recovery_checkpoint.py:83) vs `golden_context.VALID_NEXT_STEPS` (clou/golden_context.py:56) are parallel sources; `validation.py` imports from `golden_context`, `judgment.py` imports from `recovery_checkpoint`. F26 the divergence is realized: `'none'` is in `golden_context` but not `recovery_checkpoint`. Same string, two verdicts, depending on import order. F18 `_LEGACY_NEXT_STEPS` is underscored-private but cross-imported by `clou/vocabulary_migration.py:39` — false private. F32 `_LEGACY_NEXT_STEPS` is migration-only data in a runtime-hot module. F25 a single token rename now depends on at least 8 moving parts staying aligned. F24/F31 `_atomic_write` is duplicated byte-for-byte between `clou/coordinator.py` and `clou/vocabulary_migration.py` (see vocabulary_migration.py:44-75 docstring acknowledging the duplication). Tests at `tests/test_halted_checkpoint.py:32` codify the duplication as acceptable.

## Recommendation

Sequence after M50 completes (M50's tactical 'none' alignment will reduce surface area). Single-day milestone: extract `clou/vocab.py`, redirect imports, delete duplications, extract `clou/fs_utils.py` for `_atomic_write`. Tests should assert both consumer modules import from the canonical source.

## Disposition

status: open
