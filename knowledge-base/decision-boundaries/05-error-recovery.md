# DB-05: Error Recovery

**Status:** DECIDED
**Decided:** 2026-03-19
**Severity:** High — unhandled failures break the system
**Question:** What happens when things go wrong at each tier?

## Decisions

1. **20-cycle milestone limit.** The coordinator has a hard cap of 20 cycles per milestone. The coordinator critically evaluates quality gate feedback for validity — the cap prevents infinite thrashing, not thoughtful iteration. When the cap is hit, the coordinator escalates to the supervisor with a diagnosis of why convergence failed.

2. **Agent team crash: kill, preserve, escalate.** When a teammate crashes, the orchestrator kills remaining team members, preserves any `execution.md` entries they wrote, and escalates to the supervisor. The supervisor informs the user. The crash exits the coordinator loop entirely — it does not retry silently.

3. **Coordinator-only commits.** Agent teams write code but do not commit. The coordinator reviews `execution.md` and code changes, then makes git commits at phase completion. Commits contain tractable deltas focused on the implementation — no conversation artifacts, no debug output, no intermediate states.

4. **Required quality gate unavailability is a hard error.** The active harness template's required quality gates are essential infrastructure (DB-11). If a required gate becomes unavailable during an ASSESS or VERIFY cycle, the coordinator escalates as `blocked` to the supervisor, which informs the user. The coordinator does not proceed without quality assessment. For the software-construction template, this means Brutalist. Other templates specify their own required gates.

5. **Structural validation of golden context at cycle boundaries.** The orchestrator validates golden context file structure after each cycle. If validation fails: git-revert golden context files to pre-cycle state, restart the same cycle with error feedback, escalate to supervisor after 3 consecutive validation failures. Validation is tiered (DB-12): AST for compose.py, strict key-value parsing for checkpoint files (active/coordinator.md, status.md), form-only checks for narrative files (execution.md, decisions.md). The orchestrator validates structure, not content quality — content quality is the quality gate's responsibility.

6. **Inter-phase smoke tests via golden path walking.** At phase completion boundaries, before advancing to the next phase, the coordinator dispatches an integrative smoke test — not "does it compile" but end-to-end golden path verification that exercises the journey across all completed phases. This catches cascading compositional failures early.

## Prior Resolutions

These failures were resolved by earlier decisions:

| Failure | Resolution | Source |
|---|---|---|
| F2.1: Coordinator crash mid-cycle | Session-per-cycle — restart same cycle from golden context | DB-03 |
| F2.3: Invalid compose.py | Orchestrator validates via AST; PostToolUse hook rejects with errors | DB-02 |
| F3.4: Agent writes outside boundary | PreToolUse hook enforces ownership map | DB-01 |
| F1.1: Supervisor session ends | `active/supervisor.md` checkpoint + orchestrator resume | DB-03 |

## Failure Taxonomy

### Tier 1: Supervisor Failures

#### F1.1: Supervisor session ends unexpectedly
**Cause:** Terminal closed, network interruption, context exhaustion
**Impact:** User loses conversational interface
**Recovery:** Orchestrator checks for `active/supervisor.md` on startup. If present, resume prompt instructs supervisor to read checkpoint and reconstruct state. In-flight reasoning is lost, but the golden context captures all durable state.

#### F1.2: Supervisor writes malformed golden context
**Cause:** LLM output error, interrupted write
**Impact:** Coordinator reads corrupt milestone.md or requirements.md
**Recovery:** Structural validation catches malformed files. Git provides rollback. The orchestrator does not validate supervisor writes (supervisor is interactive), but the coordinator's read-forward protocol will surface parse failures as errors in the cycle, triggering the revert-and-retry flow.

#### F1.3: Supervisor creates conflicting state
**Cause:** User modifies .clou/ directly while supervisor is writing
**Impact:** Race condition — supervisor's view diverges from reality
**Recovery:** Supervisor re-reads golden context at the top of each loop iteration — never caches state from a prior read.

### Tier 2: Coordinator Failures

#### F2.1: Coordinator session crashes mid-cycle
**Resolved by DB-03.** Session-per-cycle makes this trivial. If `active/coordinator.md` hasn't advanced, the orchestrator restarts the same cycle. The new session reads golden context and picks up from the last complete cycle boundary.

#### F2.2: Coordinator loops infinitely
**Cause:** Quality gate keeps finding issues, fixes create new issues, exit condition never met
**Impact:** Token burn, no progress
**Recovery:** 20-cycle milestone limit. The coordinator critically evaluates each gate finding — is this a real issue? Does it matter for this milestone? Is the fix proportionate? — and decides whether to enter another cycle or proceed. When the cap is hit, the coordinator writes an escalation with:
- Cycle history summary
- Unresolved findings and why they persist
- Recommendation (ship as-is, rework scope, or abandon)

The escalation type is `blocked` with severity `blocking`.

#### F2.3: Coordinator produces invalid plan
**Resolved by DB-02.** Orchestrator validates `compose.py` via AST parsing. PostToolUse hook rejects invalid call graphs with specific error messages. The coordinator fixes and rewrites until validation passes.

#### F2.4: Coordinator overrides valid quality gate feedback incorrectly
**Cause:** LLM misjudgment about feedback validity
**Impact:** Real issues ship to the user
**Recovery:** `decisions.md` captures every override with reasoning. The supervisor reviews decisions during milestone completion evaluation. If bad overrides are a pattern, the supervisor can tighten delegated authority for future milestones.

### Tier 3: Agent Team Failures

#### F3.1: Agent team member crashes
**Cause:** Context exhaustion, tool error, subprocess crash
**Impact:** Task partially complete; other teammates may depend on its output
**Recovery:** Orchestrator detects teammate death. Kill remaining team members. Preserve all `execution.md` entries written before the crash. Escalate to supervisor with:
- Which teammate crashed and which task it was working on
- What was preserved in execution.md
- What work remains incomplete

The supervisor informs the user. The crash exits the coordinator loop — it does not silently retry, because the crash may indicate a systemic issue (codebase too large for context, tool broken, etc.) that requires human awareness.

#### F3.2: Agent team member writes wrong files
**Cause:** LLM follows incorrect path, misunderstands phase spec
**Impact:** Wrong code changes, conflicts with other teammates
**Recovery:**
- Coordinator reviews `execution.md` and `git diff` during ASSESS cycle
- Quality gate catches quality issues
- Coordinator reverts and reassigns if necessary
- Coordinator-only commits ensure bad changes don't persist in git history

#### F3.3: Agent team members produce conflicting changes
**Cause:** Two members modify the same file
**Impact:** Code won't compile/run
**Recovery:**
- Coordinator resolves conflicts during ASSESS cycle (or assigns a teammate to resolve)
- Better phase decomposition prevents overlap (compose.py typed dependencies make data flow explicit)
- Inter-phase smoke tests catch integration failures early

#### F3.4: Agent team member writes outside ownership boundary
**Resolved by DB-01.** PreToolUse hook validates file paths against the tier's ownership map. Writes to unauthorized `.clou/` paths are denied with an explicit error message.

### Infrastructure Failures

#### F4.1: Required quality gate is unavailable
**Cause:** MCP server issue, network problem, CLI not installed
**Impact:** Coordinator can't run quality assessment
**Recovery:** Hard error (Decision 4). The coordinator writes an escalation:
- Type: `blocked`
- Severity: `blocking`
- Evidence: the specific error from the gate invocation
- Recommendation: check gate installation, network, dependencies (for software-construction: Brutalist npm package)

The supervisor reads the escalation and informs the user. The coordinator does not proceed without quality assessment — required gates are essential infrastructure (DB-11).

#### F4.2: Playwright MCP fails during verification
**Cause:** Browser binary missing, server not responding, timeout
**Impact:** Verification can't complete
**Recovery:** The verification agent distinguishes between:
- Environment issues (server crashed → restart and retry)
- Tool issues (browser won't install → escalate with specific error)
The verification agent attempts diagnosis before escalating.

#### F4.3: Git state becomes inconsistent
**Cause:** Agent team code changes create conflicts
**Impact:** Code state is ambiguous
**Recovery:** Coordinator-only commits prevent this at the source. Agent teams write code but don't commit. The coordinator reviews all changes and commits coherent, conflict-free deltas at phase completion.

## Structural Validation

The orchestrator validates golden context structure at cycle boundaries — after the coordinator session exits, before spawning the next cycle.

### What is validated

- Required files exist for the cycle that just completed
- Required markdown sections are present (e.g., `active/coordinator.md` has `## Cycle`, `## Phase Status`)
- No structural corruption (unclosed sections, missing required fields)

### What is NOT validated

- Content quality (the orchestrator doesn't judge whether decisions are good)
- Semantic correctness (the orchestrator doesn't check if phase status is accurate)
- Code quality (that's the quality gate's job)

### Failure handling

```
Coordinator exits cycle
    → Orchestrator validates golden context structure
    → If valid: proceed to next cycle
    → If invalid:
        1. Git-revert golden context files to pre-cycle state
        2. Do NOT count this toward the 20-cycle limit
        3. Restart the same cycle with additional context:
           "Previous cycle produced malformed golden context.
            Specific errors: [list]. Re-execute this cycle and
            ensure all golden context writes conform to schema."
        4. If same cycle fails validation 3 consecutive times:
           escalate to supervisor as 'degraded' with validation errors
```

## Inter-Phase Smoke Tests

At phase completion, before advancing to the next phase, the coordinator dispatches an integrative verification:

### What smoke tests cover

- **Golden path walking** — exercise the end-to-end journey across all completed phases
- **Integration points** — verify that phase outputs compose correctly with prior phases
- **Environment health** — confirm the dev environment still starts and services connect

### What smoke tests do NOT cover

- Full quality gate assessment (that's the ASSESS cycle)
- Comprehensive edge cases (that's the VERIFY phase at milestone end)
- Code quality review (coordinator handles that)

### When smoke tests fail

If the smoke test reveals a regression or integration failure:
1. The coordinator logs the finding in `decisions.md`
2. The coordinator enters a rework EXECUTE cycle targeting the specific failure
3. The rework cycle focuses on the integration point, not the entire phase

This catches cascading compositional failures early — before they compound through subsequent phases. Research shows compositionality breaks at 2+ hops (see [Research Foundations](../research-foundations.md) §9). Smoke tests at phase boundaries are the structural countermeasure.

## Git Protocol

### Coordinator-Only Commits

Agent teams write code via Write/Edit tools. They do NOT run `git commit`. The coordinator is the sole committer:

1. Agent teams complete work during EXECUTE cycle, updating `execution.md`
2. Coordinator reviews changes during ASSESS cycle
3. At phase completion, coordinator commits with a descriptive message
4. Commits contain tractable deltas — logically coherent changes, no intermediate states

### Commit Timing

- **Phase completion:** Primary commit point. All changes from a phase in one commit.
- **Rework cycles:** If ASSESS triggers rework, the rework changes are committed with the next phase completion or as a separate commit referencing the quality gate finding.

### Rollback

Git is the code recovery mechanism. If a phase's changes need to be reverted:
- Coordinator uses `git revert` on the phase completion commit
- Golden context is updated to reflect the rollback
- The phase can be re-attempted from a clean state

## Recovery Principles

1. **Golden context is the recovery mechanism.** Checkpoint files at cycle boundaries make any session restartable.

2. **Git is the code recovery mechanism.** Coordinator-only commits ensure clean, revertable history.

3. **Fail loud, not silent.** Crashes and infrastructure failures escalate to the supervisor and user — no silent retries that mask systemic issues.

4. **20-cycle cap prevents runaway loops.** The coordinator critically evaluates whether each cycle is productive. The cap is a safety net, not the primary termination mechanism.

5. **Re-read before acting.** Each cycle reads current golden context — no cached state from prior sessions.

6. **Structural validation at cycle boundaries.** The orchestrator validates form, not content. Invalid state is reverted and retried with feedback.

7. **Smoke tests at phase boundaries.** Integrative golden path verification catches compositional failures before they cascade.

## Cascading Effects

- **DB-06 (Token Economics):** The 20-cycle limit caps worst-case token burn per milestone. Smoke tests add per-phase verification cost but reduce rework cost from late-caught integration failures.
- **DB-08 (File Schemas):** Structural validation requires defined schemas for golden context files — required sections, required fields. Schemas must be machine-checkable.
- **DB-10 (Team Communication):** `execution.md` is the durable record of teammate work, including crash scenarios. Teammates must write incrementally, not only at completion.
