# Proposal: ORIENT context budget management (windowing / rollup over phases/*/execution.md)

**Filed by:** coordinator for milestone `36-orient-cycle-prefix`, cycle 1
**Estimated scope:** day
**Depends on:** 36-orient-cycle-prefix, 37-judgment-gating-promotion

## Rationale

M36's I1 ORIENT read set is `intents.md + status.md + glob phases/*/execution.md + git diff --stat`, with the glob resolved at call time. This is the right adaptive shape for a fresh-or-mid-flight session, but the read-set size grows monotonically as new EXECUTE phases land — every cycle of every milestone re-reads every prior phase's full execution report. For long milestones (10+ phases) or repeated re-entries, the ORIENT context cost grows unboundedly: token cost per ORIENT cycle = sum(len(execution.md_i) for i in completed_phases). M36's planning explicitly chose the glob shape ("Listing the path unconditionally keeps the read-set composition deterministic") and bounding would change the I1 contract — out of M36 scope. The right next milestone scopes ORIENT context economy: windowing (last-N execution reports), rollup (a synthesized status digest derived from all execution.md), or pagination (return one execution.md per ORIENT cycle, rotating). The choice between strategies depends on what ORIENT actually needs to "observe before it acts" — which becomes empirically observable once M36's judgment telemetry lands and we can measure the ratio of judgment quality vs. read-set size.

## Cross-Cutting Evidence

- `clou/recovery_checkpoint.py:480-489` — `determine_next_cycle` ORIENT branch globs every `phases/*/execution.md` at call time.
- `clou/_prompts/coordinator-orient.md:18-21` — ORIENT prompt instructs the agent to read every file in the read set.
- F33 in `.clou/milestones/36-orient-cycle-prefix/assessment.md` (CLAUDE+CODEX) — two-model agreement on the unbounded growth concern.
- Memory `cycle-count-distribution`: median 7, max 15 cycles per milestone; with N phases each producing ~5KB execution.md, ORIENT pays ~75KB/cycle even on median milestones, scaling with phase count.

## Recommendation

Defer until after M36+M37 ship and produce judgment telemetry that lets us measure the actual relationship between read-set size and judgment quality. Premature optimization without the telemetry would risk choosing the wrong bounding strategy. Once M37's `cycle.judgment` events let us correlate (read-set token count) × (judgment.agreement rate), the right windowing strategy becomes empirically defensible rather than guessed.

## Disposition

status: rejected

Deferred per the proposal's own recommendation: needs judgment telemetry from live M37/M38 runs before a windowing strategy can be chosen empirically. Re-file after M37 ships if the read-set growth becomes visible in practice.
