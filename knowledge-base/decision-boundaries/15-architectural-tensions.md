# DB-15: Architectural Tension Resolution

**Status:** DECIDED
**Decided:** 2026-03-28
**Severity:** High — five tensions that compound over milestone lifetime, affect cost, user trust, and system reliability
**Question:** How do we resolve the five structural tensions identified through 8+ milestones of autonomous operation?

## Decision

**Five decisions, each grounded in research foundations and validated against existing decision boundaries:**

1. **Cycle-boundary message check** — user messages and `/stop` checked between cycles, not mid-cycle (Tension 1)
2. **Budget-aware cycle control** — soft per-milestone budget with warning thresholds, recurring-finding detection, quality gate scope pruning (Tension 2)
3. **Structural compaction of decisions.md** — keep recent 3 cycles full, summarize older to one-line entries (Tension 3)
4. **Selective git staging** — stage files from execution.md ∩ git diff, not `git add -A` (Tension 4)
5. **Escalation recovery via cycle count reset** — resolved escalation resets the 20-cycle cap for re-spawn (Tension 5)

## Tension 1: Supervisor Blocks During Coordinator

### The Problem

`run_coordinator()` blocks inside the MCP tool handler for 5-20 cycles (30-120 minutes). The supervisor cannot hear the user. Typing queues for post-coordinator processing. No cancel mechanism exists.

### Research Grounding

- **OpenHands event-stream architecture**: User messages enter the event stream at any time; agent reads them at step boundaries. "The user may interrupt the agent at any moment to provide additional feedback."
- **LangGraph `interrupt()` function**: Suspends execution at checkpoints, persists state, surfaces data to user, resumes from checkpoint with user response.
- **Kambhampati §9**: Plans need external verification at boundaries, not mid-execution re-planning. Mid-cycle interruption would require context transfer and re-planning — more expensive than letting the cycle complete.
- **DB-03 (Session-per-cycle)**: Each cycle is autonomous. The checkpoint between cycles is the natural handoff point. Interrupting a session mid-execution is a crash.
- **Situated cognition §11**: Plans are resources adapted during execution. The EXECUTE→ASSESS loop is situated action. The cycle boundary is where adaptation happens.

### Decision: D1 — Cycle-Boundary Message Check

The orchestrator checks `app._user_input_queue` between cycles (after `_run_single_cycle` returns, before the next `determine_next_cycle`). If a message is waiting:

1. Post `ClouCoordinatorPaused` to UI
2. Route message to supervisor session
3. Supervisor processes — may modify golden context, respond to user
4. If supervisor re-spawns same milestone: orchestrator resumes loop (reads updated checkpoint)
5. If supervisor doesn't re-spawn: orchestrator exits cleanly

A `/stop` slash command sets `_stop_requested: asyncio.Event` checked at cycle boundary. On stop: coordinator writes mid-cycle checkpoint (existing exhaustion protocol), exits, control returns to supervisor.

### Why Not Mid-Cycle Interruption

Mid-cycle interruption requires killing a live SDK session. The coordinator may be mid-write to golden context — partial writes corrupt state. The session-per-cycle design makes cycle boundaries the only safe interrupt points. This is consistent with the OpenHands pattern: interrupt at step boundaries, not mid-step.

### Why Not Continuous User Channel

A persistent user→coordinator channel would require the coordinator to read user messages during execution — adding tokens to an already-constrained context window, and mixing user input with golden context (violating DB-07's ownership boundaries). The supervisor IS the user channel. The cycle boundary is where the supervisor's authority applies.

## Tension 2: Cost Scales with Thrashing

### The Problem

An ASSESS→EXECUTE rework loop that doesn't converge burns the same cost per cycle regardless of progress. The convergence detector fires after the expensive ASSESS cycle. No pre-assessment heuristic avoids redundant work. No budget warning thresholds.

### Research Grounding

- **BAVT (2026)**: Budget-aware value tree uses remaining resource ratio as scaling exponent. Under low-budget constraints, frequently surpasses high-budget performance by avoiding compounding errors.
- **BATS (2025)**: Budget Tracker with four spending regimes (HIGH ≥70%, MEDIUM 30-70%, LOW 10-30%, CRITICAL <10%) with different behavioral modes at each level.
- **DB-06**: "Thrashing is the enemy, not the model. The coordinator's critical evaluation is the primary cost control."
- **DB-05**: 20-cycle cap prevents infinite loops. Convergence detection overrides rework when ASSESS converges.

### Decision: D2 — Budget-Aware Cycle Control

**D2a: Per-milestone soft budget with warning thresholds.**

`HarnessTemplate` gains `budget_usd: float | None` (default None = unlimited). The orchestrator tracks cumulative cost via `ClouMetrics` at cycle boundaries:

- **50% spent**: Log warning, post `ClouBudgetWarning` to UI
- **75% spent**: Warning + inject cost context into next cycle's prompt: "75% of budget consumed after N cycles — evaluate remaining work critically"
- **100% spent**: Escalate as budget-exhausted (same path as cycle-limit escalation)

Soft budget: advisory context, not hard enforcement. The 20-cycle cap remains the structural ceiling. The budget warning gives the coordinator cost-awareness as a cognitive affordance — the same principle as ArtifactForm (DB-14): structure shapes cognition.

**D2b: Recurring-finding detection.**

Extend `assess_convergence()` in recovery.py: if a finding was accepted (rework requested) in cycle N but the same or similar finding appears in cycle N+2, the rework didn't fix the root cause. Inject into the next cycle's prompt: "Finding X recurred after rework — consider whether the approach needs to change."

Detection: fuzzy match finding text across `decisions.md` cycle groups. The finding text is structured (`**Finding:** "exact text"`) — substring match on quoted finding text is sufficient.

**D2c: Quality gate scope pruning.**

The orchestrator passes `changed_domains` (derived from `git diff --stat` file extensions/paths) into the ASSESS prompt. The assessor protocol uses this to skip irrelevant roast tools. Already guidance in `coordinator-assess.md` — this makes it structural by providing the data.

## Tension 3: Golden Context Grows Monotonically

### The Problem

decisions.md grows every ASSESS cycle with full finding details. By cycle 15: ~10K tokens of decision history. The coordinator reads it during ASSESS for continuity — but most of the detail from early cycles is irrelevant to current work.

### Research Grounding

- **Research Foundations §1**: Every token not relevant to the current cycle is a distractor — not neutral, actively harmful. Semantically similar distractors (old decisions about the same code) are the worst kind.
- **Factory.ai anchored iterative summarization**: Maintain structured persistent summary with explicit sections. Only newly-dropped spans are summarized and merged. Scored 3.70 vs 3.44 (Anthropic), 3.35 (OpenAI) on 36K real sessions.
- **DB-07**: Per-cycle read sets operate at file granularity. The ASSESS read set includes all of decisions.md — no way to include only recent cycles.
- **DB-03**: "Mid-cycle exhaustion signals phases are too large." But decisions.md growth is proportional to cycle count, not phase size.

### Decision: D3 — Structural Compaction at Cycle Boundary

After validation passes and before the next cycle, if decisions.md exceeds a threshold (default: 4000 tokens, ~8 cycle groups), the orchestrator compacts:

1. Keep the most recent 3 cycle groups in full detail (newest-first ordering preserved)
2. Replace older cycle groups with structural summaries:
   ```
   ## Cycle N — Quality Gate Assessment (compacted)
   Accepted: 3 findings (auth validation, error handling, test coverage)
   Overridden: 2 findings (style preference, premature optimization)
   ```
3. Write the compacted decisions.md back

This is NOT LLM summarization — it's structural transformation. The finding titles are preserved (one-line each), the full reasoning is pruned. Git history preserves the complete text. The coordinator gets continuity ("I've been accepting auth findings repeatedly") without the full reasoning chains from 10 cycles ago.

**Why 3 recent cycles**: The coordinator's ASSESS evaluation needs to see its own recent judgment patterns. Older than 3 cycles, the detail is rarely referenced — the pattern is what matters, and the compacted summary preserves pattern signals.

**Why not summarize execution.md**: Already scoped per-phase. The coordinator only reads the current phase's execution.md during ASSESS. Completed phases' execution.md is never re-read. No compaction needed.

## Tension 4: git add -A Stages Everything

### The Problem

`git_commit_phase` runs `git add -A`, staging the entire working tree. Build artifacts, incomplete .gitignore, secrets, telemetry, session transcripts — all staged. On large repos, `git add -A` can exceed the 30-second timeout, killing the process and corrupting the git index.

### Research Grounding

- **Aider**: Auto-commits per change with LLM-generated messages. Before editing dirty files, commits preexisting uncommitted changes separately. Granular, reviewable commits.
- **Claude Code**: Stash-based checkpoints. Separate from user's git workflow.
- **DB-05**: "Agent teams write code but do not commit. The coordinator reviews and commits at phase completion. Commits contain tractable deltas."
- **Telemetry.py:42**: Already self-aware: "Keep telemetry out of git — git_commit_phase does git add -A." Writes its own .gitignore as a workaround.

### Decision: D4 — Selective Staging from execution.md + git diff

Replace `git add -A` with a targeted staging pipeline:

1. Run `git diff --name-only` to get actually-changed files
2. Parse current phase's execution.md for file paths mentioned in task entries
3. Stage files that appear in git diff AND are outside `.clou/` (golden context is committed separately)
4. Exclude files matching `_STAGING_EXCLUDE_PATTERNS`:
   - `.clou/telemetry/*`, `.clou/sessions/*`
   - `node_modules/`, `__pycache__/`, `*.pyc`
   - `.env`, `.env.*`
   - Common build artifact patterns
5. If no files match (parsing failure or empty diff), fall back to `git diff --name-only` filtered by exclude patterns — still better than `git add -A`

The orchestrator knows what was worked on (execution.md), git knows what actually changed (`git diff`). Their intersection is the correct staging set.

**Why not per-file commits (Aider pattern)**: Clou's coordinator-only-commits model produces per-phase commits that are larger but more coherent — a single commit per phase maps to a logical unit of work. Per-file commits would fragment the history and make revert harder.

## Tension 5: Escalations Are Terminal

### The Problem

After escalation (cycle limit, agent crash, validation failure), the coordinator stops. The supervisor discusses with the user, but re-spawning reads the same checkpoint. The 20-cycle-cap escalation is permanently stuck: `read_cycle_count` returns 20, the cap fires immediately on re-spawn.

### Research Grounding

- **LangGraph checkpoint+resume**: `interrupt()` persists state. User responds via `Command`. Execution resumes from checkpoint. The key: the interrupt preserves state AND allows the user to modify it before resume.
- **DB-05**: "When the cap is hit, the coordinator escalates to the supervisor with a diagnosis of why convergence failed." The escalation IS the diagnosis. The supervisor IS the decision-maker.
- **DB-01**: `clou_spawn_coordinator` is the resume mechanism. The orchestrator reads the checkpoint at startup. Modified golden context = different behavior on re-spawn.

### Decision: D5 — Cycle Count Reset on Re-Spawn After Resolved Escalation

When `run_coordinator` starts, it checks for resolved escalations (escalation files with a `disposition:` field). If the latest escalation is resolved:

1. Reset `cycle_count` to 0 — the supervisor's decision to retry constitutes a new attempt
2. Log the reset: "Cycle count reset after resolved escalation: {disposition}"
3. Continue from checkpoint — `determine_next_cycle` reads the existing checkpoint and routes normally

The supervisor's escalation resolution flow:
1. Read escalation diagnosis + recommendation
2. Discuss with user: "The coordinator hit 20 cycles because X. I recommend Y."
3. If retrying: modify golden context (scope, requirements, constraints), re-spawn
4. The re-spawn gets a fresh 20-cycle budget

Improve the escalation return message to guide the supervisor:
```
Coordinator escalated: {type}
Diagnosis: {summary from escalation file}
To retry: modify golden context (scope, requirements), then call clou_spawn_coordinator.
Cycle count resets on retry after escalation resolution.
```

**Why not automatic retry**: The escalation exists because the coordinator couldn't solve the problem in 20 cycles. Automatic retry without supervisor intervention would repeat the same failure. The supervisor must modify the golden context (adjust scope, clarify requirements, resolve external blockers) before retry is useful.

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-01 (Spawning)** | Orchestrator gains cycle-boundary pause/resume capability. spawn_coordinator becomes re-entrant for resolved escalations. |
| **DB-03 (Context)** | decisions.md compaction reduces per-cycle context load. Budget warnings inject cost-awareness into coordinator context. |
| **DB-05 (Recovery)** | Escalation recovery path extended. Cycle count resettable. Selective staging replaces git add -A. |
| **DB-06 (Economics)** | budget_usd on HarnessTemplate. Warning thresholds at 50/75/100%. Recurring-finding detection. |
| **DB-08 (Schemas)** | decisions.md gains compacted cycle group format. |
| **DB-11 (Harness)** | HarnessTemplate gains budget_usd field. |
| **DB-14 (Intents)** | No direct impact — intents.md is not affected by these tensions. |

## Implementation Scope

| Component | Change |
|---|---|
| `orchestrator.py` | Cycle-boundary message check, stop event, budget tracking, escalation cycle reset |
| `recovery.py` | `compact_decisions()`, selective staging in `git_commit_phase`, recurring-finding detection in `assess_convergence` |
| `harness.py` | `budget_usd` field on HarnessTemplate |
| `messages.py` | `ClouCoordinatorPaused`, `ClouBudgetWarning` |
| `app.py` | Handle paused state, /stop command |
| `commands.py` | Register /stop |
| `breath.py` | Render paused state |
| `_prompts/coordinator-assess.md` | Changed-domains scoping guidance |
| Tests | All new behavior covered |
