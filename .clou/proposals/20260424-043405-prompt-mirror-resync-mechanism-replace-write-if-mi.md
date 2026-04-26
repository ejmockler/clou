# Proposal: Prompt-mirror resync mechanism (replace _write_if_missing pattern)

**Filed by:** coordinator for milestone `50-orient-gating-prerequisites`, cycle 2
**Estimated scope:** day
**Depends on:** 50-orient-gating-prerequisites

## Rationale

M50 I1 surfaced that the prompt-mirror system (`.clou/prompts/coordinator-*.md`) uses `_write_if_missing` (clou/tools.py:51-58) which never overwrites — meaning bundled-prompt updates in `clou/_prompts/` never propagate to project-local mirrors. M50 papered over this by including the prompt mirror in its vocabulary-token sweep, but that's the wrong tool for the job: prompts are template artifacts that need full resync when bundled source changes, not token-level patching. Concretely, when M50 added a new doc note to `clou/_prompts/coordinator-orient.md` explaining legacy-token rejection, that note never reached project-local mirrors created before M50. The migration-driven token rewrite would also leave the mirror without the doc note. The right fix is a `clou_resync_prompts` mechanism — invoked during `clou_init` upgrade or as a manual command — that replaces stale mirrors with current bundled content (with explicit operator confirmation if local edits exist). This decouples vocabulary migration from prompt initialization design debt.

## Cross-Cutting Evidence

M50 phase i1 brutalist assessment: F16 (codebase: '_write_if_missing creates half-upgraded prompts'); F22 (architecture: 'prompt sweeping couples this migration to an already split-brain prompt system'); F30 (architecture: 'prompt mirror coupling violates module boundaries — migration knows where mirror lives'). Concrete evidence: `clou/tools.py:51-58 _write_if_missing` never overwrites; `clou/_prompts/coordinator-orient.md` got a new doc note in M50 that never reaches existing mirrors; `clou/vocabulary_migration.py:184-188` knows the mirror's glob pattern. Tests at `tests/test_prompts.py:841` and `tests/test_halted_checkpoint.py:763` already guard the mirror as an independent artifact.

## Recommendation

Single-day milestone: add `clou_resync_prompts(force: bool = False)` MCP tool; integrate into `clou_init` upgrade path; document the user-facing behavior (force vs prompt-on-conflict); add `iter_project_prompt_mirrors()` shared between init and any future migration-style sweep. Removes the implicit coupling M50 introduced.

## Disposition

status: open
