# Proposal: Canonicalize cycle-type vocabulary tokens (EXECUTE_REWORK / EXECUTE_VERIFY)

**Filed by:** coordinator for milestone `36-orient-cycle-prefix`, cycle 1
**Estimated scope:** day
**Depends on:** 36-orient-cycle-prefix

## Rationale

Two of the ten tokens in `_VALID_NEXT_STEPS` (`EXECUTE (rework)` and `EXECUTE (additional verification)`) contain spaces and parentheses. M36's judgment artifact requires LLMs to emit one of these tokens verbatim into `next_action`; any paraphrase (`EXECUTE(rework)`, `Execute (rework)`, `EXECUTE-rework`) parses cleanly but fails validation, producing a category of failure that gets worse as M37 promotes judgment to gating. The fragility is structural to the vocabulary itself, not to any one consumer. Replacing the punctuated tokens with structured identifiers (e.g. `EXECUTE_REWORK`, `EXECUTE_VERIFY`) removes the LLM-paraphrase failure mode for every current and future consumer at once. The change touches `recovery_checkpoint._VALID_NEXT_STEPS`, every `determine_next_cycle` branch that compares against these tokens, the rework-detection regex in `coordinator.py`, prompt copy that displays them, and any telemetry that records them — i.e. it is cross-cutting across dispatch, telemetry, prompts, and judgment. Out of M36 scope (which is scoped to the judgment artifact and ORIENT plumbing); ideal for a focused vocabulary-canonicalization milestone before M37 makes judgment authoritative.

## Cross-Cutting Evidence

- `clou/recovery_checkpoint.py:70-83` — `_VALID_NEXT_STEPS` defines the punctuated tokens.
- `clou/coordinator.py:1250` — rework detection uses `"rework" in _effective_next_step.lower()` substring match (would simplify to exact-token equality with structured tokens).
- `clou/judgment.py:46,274` — judgment validator inherits the same vocabulary; F15 in `.clou/milestones/36-orient-cycle-prefix/assessment.md` documents the LLM paraphrase failure mode.
- `clou/_prompts/*.md` — multiple coordinator prompt files display the tokens to the LLM verbatim.
- `clou/telemetry.py` — quality_gate.decision events include rework/advance distinctions that key on the substring.

## Recommendation

Sequence before M37 (judgment-as-gating). The vocabulary fragility is acceptable telemetry-only in M36 but becomes a routing-correctness defect once M37 lets judgment.next_action drive dispatch. A bounded one-day refactor to underscore-separated structured tokens removes the LLM paraphrase failure mode at its source.

## Disposition

status: accepted

Crystallized as M50 intent I1 (structured cycle-type tokens).
