# Resolve Architectural Tensions

Five tensions identified in clou's runtime path. Each is addressed with a
decision grounded in the knowledge base, research foundations, and external
state of the art. No speculative features — each decision resolves a concrete
failure mode.

---

## Tension 1: Supervisor blocks during coordinator — no mid-flight course correction

### The Problem
`run_coordinator()` is called inside the MCP tool handler and blocks until
all cycles complete. The supervisor can't hear the user. Typing goes to a
queue that's only drained after the coordinator finishes. The user can't
cancel, redirect, or provide feedback during a 30-120 minute autonomous run.

### Research Grounding
- **OpenHands**: Event-stream injection — user messages enter the stream,
  agent reads them at step boundaries. "The user may interrupt the agent at
  any moment."
- **LangGraph**: `interrupt()` function suspends at checkpoints, surfaces
  data to user, resumes from checkpoint with user response.
- **Kambhampati §9**: Plans need external verification at boundaries, not
  mid-execution re-planning. The cycle boundary IS the natural interrupt
  point.
- **DB-03**: Session-per-cycle makes each cycle autonomous. The checkpoint
  between cycles is the natural handoff point.

### Decision: Cycle-Boundary Message Check

Between cycles (after `_run_single_cycle` returns, before the next
`determine_next_cycle`), the orchestrator checks the user input queue. If
a message is waiting:

1. Post `ClouCoordinatorPaused` to UI (breath mode shows "paused — reading
   your message")
2. Route the message to the supervisor session (which is alive but waiting)
3. Supervisor processes the message — can modify golden context (update
   requirements, adjust scope, write to milestone.md)
4. Supervisor responds to user
5. If supervisor calls `clou_spawn_coordinator` again for the SAME milestone,
   the orchestrator resumes the existing loop (reads updated checkpoint)
6. If supervisor doesn't re-spawn, the coordinator loop exits cleanly

This gives the user course correction at cycle boundaries (~2-10 minutes
between checks) without mid-cycle interruption. The checkpoint ensures no
state is lost.

**Also needed**: A `/stop` slash command that sets a flag the orchestrator
checks at cycle boundary. On stop: coordinator writes a mid-cycle checkpoint,
exits, control returns to supervisor.

### Implementation
- orchestrator.py: check `_user_input_queue` between cycles in the while loop
- orchestrator.py: add `_stop_requested: asyncio.Event` checked at cycle boundary
- app.py: add `/stop` command that sets the event
- messages.py: add `ClouCoordinatorPaused` message
- UI: show paused state in breath widget

---

## Tension 2: Cost scales with thrashing, not with value

### The Problem
An ASSESS→EXECUTE rework loop that doesn't converge burns the same cost per
cycle regardless of progress. The convergence detector fires AFTER the
expensive ASSESS cycle. No pre-assessment heuristic avoids redundant work.
No budget warning thresholds.

### Research Grounding
- **BAVT (2026)**: Budget-aware value tree uses remaining resource ratio as
  scaling exponent. Under low-budget constraints, frequently surpasses
  high-budget performance by avoiding compounding errors.
- **BATS (2025)**: Budget Tracker with four spending regimes: HIGH (>=70%),
  MEDIUM (30-70%), LOW (10-30%), CRITICAL (<10%).
- **DB-06**: "Thrashing is the enemy, not the model. The coordinator's
  critical evaluation is the primary cost control."
- **DB-05**: 20-cycle cap is the hard ceiling. Convergence detection
  overrides rework when ASSESS converges.

### Decision: Budget-Aware Cycle Control

Three mechanisms, each addressing a different failure mode:

**A. Per-milestone soft budget with warning thresholds.**
`HarnessTemplate` gains `budget_usd: float | None` (default None = no limit).
The orchestrator tracks cumulative cost via `ClouMetrics`. At cycle boundary:
- 50% spent: log warning, post `ClouBudgetWarning` to UI
- 75% spent: post warning + inject cost context into next cycle prompt
  ("75% of budget consumed after N cycles — be selective about rework")
- 100% spent: escalate as budget-exhausted (same path as cycle-limit)

This is a SOFT budget — advisory, not hard-cap. The 20-cycle cap remains
the hard ceiling. The budget warning gives the coordinator cost-awareness
that it currently lacks.

**B. Diminishing-returns detection.**
Track finding acceptance rate across ASSESS cycles. If the coordinator
accepts findings in cycle N but the same findings recur in cycle N+2,
the rework didn't fix them. The convergence detector already does part of
this (zero-accept detection). Extend it: if accepted findings RECUR
(same finding text or same file), log a warning and inject into the next
cycle prompt: "Finding X was accepted in cycle N but recurred — consider
whether the approach needs to change, not just the implementation."

**C. Quality gate scope pruning.**
Before dispatching the assessor, the coordinator checks what actually changed
since the last ASSESS. If no dependencies changed, skip `roast_dependencies`.
If no infrastructure changed, skip `roast_infrastructure`. The coordinator
already has this guidance in `coordinator-assess.md`, but it's prompt-level.
Make it structural: the orchestrator passes a `changed_domains` set derived
from `git diff` to the ASSESS prompt, and the assessor protocol uses it.

### Implementation
- harness.py: add `budget_usd: float | None = None` to HarnessTemplate
- orchestrator.py: track cumulative cost, check thresholds at cycle boundary
- messages.py: add `ClouBudgetWarning` message
- recovery.py: extend `assess_convergence` to detect recurring findings
- coordinator-assess.md: add changed-domains scoping guidance

---

## Tension 3: Golden context grows monotonically

### The Problem
decisions.md grows every ASSESS cycle. By cycle 15, the coordinator reads
10K+ tokens of decision history. execution.md grows with rework. The
coordinator's read set accumulates without compaction.

### Research Grounding
- **Factory.ai**: Anchored iterative summarization — maintain a structured
  persistent summary with explicit sections. When compression needed, only
  the newly-dropped span is summarized and merged. Scored 3.70 vs 3.44
  (Anthropic) and 3.35 (OpenAI) on 36K real sessions.
- **Research Foundations §1**: Every token the coordinator reads that isn't
  relevant to its current cycle is a distractor — not neutral, actively
  harmful.
- **DB-07**: Per-cycle read sets operate at file granularity. Can include
  or exclude a file, not a section.
- **DB-03**: "Design pressure: mid-cycle exhaustion signals phases are too
  large. The right response is to decompose phases into smaller units."

### Decision: Cycle-Boundary Compaction for decisions.md

decisions.md is the primary growth vector. Its schema (DB-08) groups entries
by cycle with newest-first ordering. The ASSESS coordinator reads it for
continuity — "what did I decide before?"

**Compaction rule**: At cycle boundary, after validation passes, if
decisions.md exceeds a threshold (e.g. 3000 tokens / ~50 entries), the
orchestrator compacts old cycles:

1. Keep the most recent 3 cycle groups in full detail
2. Replace older cycle groups with a one-line summary each:
   `## Cycle N — {cycle_type}: {accepted_count} accepted, {overridden_count} overridden`
3. Write the compacted decisions.md back

This is NOT LLM summarization — it's structural compaction. The full text
is preserved in git history. The compacted version gives the coordinator
continuity ("I accepted 3 findings in cycle 5") without the full detail
("here are the 3 findings and their 500-token reasoning blocks").

**For execution.md**: Already scoped per-phase. When a phase completes,
its execution.md is read-only (terminal status exemption in validation.py).
The coordinator only reads the CURRENT phase's execution.md. No compaction
needed — the per-phase scoping already handles it.

**For supervisor context**: The supervisor checkpoint
(active/supervisor.md) already exists. At milestone completion, the
supervisor writes a summary. Between milestones, the supervisor reads
the checkpoint, not the full conversation. This is the existing design
and it works.

### Implementation
- recovery.py: add `compact_decisions(path, keep_recent=3)` function
- orchestrator.py: call after validation passes, before next cycle
- No prompt changes needed — the coordinator reads whatever decisions.md
  contains

---

## Tension 4: git add -A stages everything

### The Problem
`git_commit_phase` runs `git add -A` which stages everything in the working
tree — build artifacts, secrets, incomplete .gitignore, telemetry files.
The 30-second timeout can corrupt the git index on large repos.

### Research Grounding
- **Aider**: Auto-commits per change. Before editing dirty files, commits
  user's preexisting uncommitted changes separately. Every change gets its
  own commit.
- **Claude Code**: Uses stash-based checkpoints that don't appear in git log.
  Separate from user's git workflow.
- **DB-05**: "Agent teams write code but do not commit. The coordinator
  reviews execution.md and code changes, then makes git commits at phase
  completion."

### Decision: Selective Staging from execution.md File List

Replace `git add -A` with selective staging derived from what the agents
actually wrote.

1. Parse execution.md for file paths mentioned in task entries (the `**Files:**`
   field or paths in code blocks)
2. Also run `git diff --name-only` to get the actual changed files
3. Intersect: only stage files that are BOTH mentioned in execution.md AND
   show up in git diff
4. If the intersection is empty (agent wrote but didn't record), fall back
   to `git diff --name-only` only (still better than `git add -A`)
5. Never stage files matching `.clou/telemetry/*`, `.clou/sessions/*`, or
   common artifact patterns (`node_modules/`, `__pycache__/`, `*.pyc`,
   `.env`)

This is a structural fix — the orchestrator knows what was worked on from
execution.md, and git knows what actually changed. Their intersection is
the correct staging set.

### Implementation
- recovery.py: replace `git add -A` in `git_commit_phase` with selective
  staging
- recovery.py: add `_extract_changed_files(execution_path)` helper
- recovery.py: add `_STAGING_EXCLUDE_PATTERNS` for common artifacts
- No prompt changes needed

---

## Tension 5: Escalations are terminal

### The Problem
After escalation, the coordinator stops. The supervisor can discuss it with
the user, but re-spawning reads the same checkpoint. The 20-cycle-cap
escalation is permanently stuck because `read_cycle_count` returns 20 and
the cap fires immediately.

### Research Grounding
- **LangGraph**: Checkpoint + resume. `interrupt()` persists state, user
  responds via `Command`, execution resumes from checkpoint.
- **DB-05**: Escalation = diagnosis + recommendation. The supervisor
  decides: resolve blocker, adjust scope, accept risk, or abandon.
- **Architecture §2**: The supervisor IS the authority. Escalations invoke
  supervisor judgment, not system termination.

### Decision: Supervisor-Resettable Cycle Count + Resume Tool

Two changes:

**A. Cycle count reset on re-spawn after escalation.**
When the supervisor calls `clou_spawn_coordinator` for a milestone that has
an escalation file with a disposition, the orchestrator resets the cycle
count. The supervisor has reviewed and decided — the 20-cycle cap was for
the PREVIOUS attempt. The new attempt gets a fresh count.

Implementation: `run_coordinator` checks for resolved escalations at startup.
If the latest escalation has a disposition field, reset `cycle_count` to 0
(or to the value in the checkpoint if partial progress should be preserved).

**B. Supervisor resume guidance.**
The supervisor's response to the user after an escalation should include
explicit guidance: "I can modify the scope and restart, or we can abandon
this milestone." The supervisor protocol already says this (step 8), but
the tool feedback from the escalated coordinator should be clearer:

```
Coordinator escalated: {type}.
Diagnosis: {escalation summary}
To retry: modify golden context and call clou_spawn_coordinator again.
Cycle count will reset.
```

### Implementation
- orchestrator.py: in `run_coordinator`, check for resolved escalations
  and reset cycle count
- orchestrator.py: improve the return message from escalated coordinators
  to guide the supervisor toward retry
- No new tools needed — the existing spawn tool + checkpoint system handles
  resume

---

## Task Graph

```
Phase 1: Decision boundaries (document only)
  └─ Write DB-15 covering all 5 tensions

Phase 2: Cycle-boundary interrupt (Tension 1)
  ├─ 2.1 Check user input queue between cycles
  ├─ 2.2 Add /stop command + stop event
  └─ 2.3 ClouCoordinatorPaused message + UI

Phase 3: Budget-aware cycle control (Tension 2)
  ├─ 3.1 budget_usd on HarnessTemplate
  ├─ 3.2 Cumulative cost tracking + threshold warnings
  └─ 3.3 Recurring finding detection in convergence

Phase 4: decisions.md compaction (Tension 3)
  └─ 4.1 compact_decisions() at cycle boundary

Phase 5: Selective git staging (Tension 4)
  └─ 5.1 Replace git add -A with execution.md-derived staging

Phase 6: Escalation recovery (Tension 5)
  ├─ 6.1 Reset cycle count on re-spawn after resolved escalation
  └─ 6.2 Improve escalation return message for supervisor

Phase 7: Tests + validation
  └─ 7.1 Tests for all new behavior
```

Phases 2-6 are independent (can be done in any order).
Phase 7 depends on all prior phases.
