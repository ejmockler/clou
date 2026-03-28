# Coordinator Protocol

## Role

The coordinator owns a single milestone's lifecycle. It is an agent session that runs an autonomous judgment loop: plan the work, dispatch agent teams, collect results, run quality gates for assessment, critically evaluate feedback, and decide whether to accept, rework, or escalate. The coordinator exits when the milestone's acceptance criteria are met.

The coordinator is NOT a dispatcher. It does not simply assign work and collect results. It exercises judgment at every step. Its relationship with the quality gate is critical, not deferential — it evaluates the evaluator. The specific quality gate (e.g., Brutalist for software construction) comes from the active harness template (DB-11).

## Coordinator Loop

```
1. INITIALIZE
   a. Read milestone.md, requirements.md, project.md
   b. Read active/coordinator.md if resuming from crash
   c. Establish delegated authority boundaries

2. PLAN
   a. Survey codebase through agent teams (read-only exploration)
   b. Identify third-party service dependencies
   c. Check services/*/status.md for configuration state
   d. If unconfigured services needed:
      - Write services/<name>/setup.md with precise instructions
      - Write services/<name>/.env.example
      - File credential_request escalation (degraded severity)
   e. Map requirements to code areas
   f. Identify dependency structure of the work
   g. Write compose.py — the typed-function call graph for the milestone
      - Each task is an async function with typed params and return type
      - execute() composes all tasks with gather() for concurrency
      - Phase boundaries are marked with comments
      - The orchestrator validates via AST (cycles, types, completeness)
   h. For each phase, write phase.md (narrative scope for agent teams)
   i. Write active/coordinator.md checkpoint

3. EXECUTE (mechanical dispatch with circuit breakers)
   a. Read compose.py — identify current phase's function(s)
   b. Read phase.md — narrative context for agent briefings
   c. For each task group in phase order:
      - gather() group: spawn N agents in parallel (one per function)
        → CIRCUIT BREAKER: if any member fails, abort remaining members
      - Sequential task: spawn 1 agent, wait for completion
        → CIRCUIT BREAKER: read execution.md summary status line
          before dispatching dependent task
   d. The coordinator exercises zero judgment about output quality
      during EXECUTE — all evaluation happens in ASSESS
   e. Park branches blocked on credential_request escalations
   f. Write active/coordinator.md checkpoint

4. ASSESS
   a. Run quality gate tools on completed work — invoke the assess agent
      defined by the active harness template (DB-11). For software-construction:
      query all relevant Brutalist domains (roast_codebase, roast_architecture,
      roast_security, roast_infrastructure, roast_dependencies,
      roast_test_coverage, roast_file_structure — based on what changed).
   b. Evaluate quality gate feedback critically against:
      - Milestone requirements and constraints
      - Delegated authority boundaries
      - Project-level principles from project.md
   c. For each finding:
      - Valid feedback → create rework tasks, loop to EXECUTE
        If repeated rework on the same task fails, the coordinator may
        re-decompose the failed task into finer subtasks rather than
        retrying at the same granularity (ADaPT pattern — see Research
        Foundations §9). Re-decomposition updates compose.py and is
        re-validated by the orchestrator via AST parsing.
      - Invalid feedback → log override reasoning in decisions.md
      - Exceeds authority → file escalation
   d. Write active/coordinator.md checkpoint

5. VERIFY (see Verification Protocol)
   a. Create verification/ phase directory
   b. Write verification phase.md (acceptance criteria as test plan, selected verification modalities)
   c. Verification agent executes perception stages:
      - Stage 1: Environment materialization
      - Stage 2: Golden path walking (with perceptual record capture)
      - Stage 3: Exploratory testing (scope per coordinator's plan)
   d. Coordinator reads perceptual record (verification/execution.md + artifacts/)
   e. Coordinator invokes quality gate verify tools on perceptual record
      (for software-construction: Brutalist roast_product)
   f. Coordinator evaluates verifier findings + quality gate assessment against acceptance criteria
   g. If issues → rework EXECUTE with findings, then re-VERIFY
   h. If satisfied → dispatch handoff preparation, proceed to EXIT
   i. Write active/coordinator.md checkpoint

6. EXIT
   a. Verify all exit conditions are met
   b. Write final status to status.md
   c. Confirm handoff.md is complete with running environment
   d. Update active/coordinator.md with final state
   e. Signal completion to supervisor
```

## Exit Conditions

The coordinator exits when ALL of:

- [ ] All implementation phases complete
- [ ] Acceptance criteria from `requirements.md` are met
- [ ] Quality gate code assessment has been run and all findings resolved (accepted or overridden with reasoning)
- [ ] Verification phase has produced `handoff.md` with confirmed golden paths
- [ ] Quality gate experience assessment has been run within VERIFY and all findings resolved
- [ ] No open blocking escalations
- [ ] Dev environment is running and accessible
- [ ] All service dependencies are configured and verified

## Planning Phase Detail

Before dispatching any work, the coordinator:

1. **Surveys the codebase** — reads files and explores the existing code structure, patterns, and conventions
2. **Reads requirements** — maps each requirement to specific code areas that need changes
3. **Identifies dependencies** — determines which pieces of work depend on which others
4. **Writes the composition** — `compose.py` expresses the full milestone as a typed-function call graph. Tasks are functions. Dependencies are arguments. Concurrency is `gather()`. The orchestrator validates the call graph automatically (cycle detection, type compatibility, completeness).
5. **Writes phase narratives** — `phase.md` for each phase provides narrative scope and context for agent teams

This planning phase is distinct from execution. The coordinator does not start writing code until it has a validated composition. The composition is a written artifact (`compose.py`) that can be reviewed, validated by the orchestrator, and that survives session restarts.

## Coordinator's Relationship with the Quality Gate

The quality gate (Brutalist for software-construction, per DB-11) provides feedback. The coordinator decides what to do with it. The coordinator's judgment criteria:

1. **Does the feedback address a real issue?** — The quality gate may flag a pattern as problematic when it's an intentional architectural decision documented in `project.md`.

2. **Does the issue matter for this milestone?** — A code quality suggestion may be valid but out of scope for the current acceptance criteria.

3. **Is the fix within delegated authority?** — If fixing the issue would change the API contract or require scope expansion, it needs escalation.

4. **Is the cost of fixing proportionate?** — A minor style issue found in the last assessment cycle shouldn't trigger a full rework if the milestone is otherwise complete.

Every exercise of judgment is logged in `decisions.md`, newest-first, grouped by cycle. Two entry types:
- **Quality Gate Assessment** (accepted/overridden): what the gate said → action → reasoning
- **Coordinator Judgment** (tradeoffs, authority edge cases): context → decision → reasoning

New cycle groups are prepended at the top of the file — most recent judgments occupy the attention sink position. See [DB-08](../decision-boundaries/08-file-schemas.md) for the full schema.

## Session-per-Cycle Model

Each coordinator cycle (PLAN, EXECUTE, ASSESS, VERIFY, EXIT) runs as a **fresh session**. The golden context is the sole state transfer mechanism between cycles — no reliance on SDK context compression or conversational continuity. The orchestrator reads `active/coordinator.md` between cycles, determines the next cycle type, and constructs a targeted prompt pointing to the specific golden context files needed.

**What the coordinator reads per cycle** (only what's needed for that cycle type):
- PLAN: milestone.md, requirements.md, project.md
- EXECUTE: status.md, compose.py, current phase's phase.md, active/coordinator.md
- ASSESS: status.md, compose.py, current phase's execution.md, requirements.md, decisions.md
- VERIFY: status.md, requirements.md, compose.py, active/coordinator.md
- EXIT: status.md, handoff.md, decisions.md, active/coordinator.md

**What the coordinator does NOT read:**
- Completed phases' full execution.md (prior cycles' results are in decisions.md)
- Source code files (agent teams handle that; coordinator manages by exception)
- Inter-agent message history (ephemeral, results captured in execution.md)

**Write-back at cycle boundary:** Before exiting, the coordinator writes `active/coordinator.md` (position, phase status, next step), any new `decisions.md` entries, and `status.md` updates. This is the coordinator's last act — the orchestrator confirms exit before spawning the next cycle.

**Coordinator-only commits at phase completion.** Agent teams write code but do not commit. The coordinator reviews changes and commits tractable deltas at phase completion. See "Coordinator-Only Commits" section below.

For the full lifecycle design, see [DB-03](../decision-boundaries/03-context-window-lifecycle.md).

## Coordinator-Produced Artifacts

| Artifact | When | Purpose |
|---|---|---|
| `compose.py` | During PLAN step | Typed-function call graph for entire milestone |
| `phase.md` per phase | During PLAN step | Narrative scope for agent teams |
| `decisions.md` entries | During ASSESS step | Judgment log for quality gate overrides and tradeoffs |
| `escalations/*.md` | Anytime | Structured signals to supervisor |
| `status.md` | Each cycle boundary | Current progress visible to supervisor |
| `active/coordinator.md` | Each checkpoint | Crash recovery state |
| Verification phase spec | Before VERIFY step | Acceptance criteria translated to test plan |

## Cycle Counting and Limits

The coordinator tracks cycle number (how many PLAN-EXECUTE-ASSESS iterations have occurred). This serves:
- **Audit trail:** Each `decisions.md` entry and escalation is tagged with the cycle number
- **Termination awareness:** A high cycle count indicates thrashing
- **Cost awareness:** Each cycle consumes tokens across multiple agent sessions

**Hard limit: 20 cycles per milestone.** The coordinator critically evaluates quality gate feedback at each ASSESS cycle — is this finding valid? Does it matter for this milestone? Is the fix proportionate? — and decides whether to enter another cycle or proceed. The 20-cycle cap is a safety net, not the primary termination mechanism. The coordinator's critical evaluation of whether each cycle is productive IS the primary mechanism.

When the cap is hit, the coordinator escalates to the supervisor with:
- Cycle history summary and pattern analysis
- Unresolved findings and why they persist
- Recommendation (ship as-is, rework scope, or abandon)

## Coordinator-Only Commits

Agent teams write code but do NOT commit. The coordinator is the sole committer:
1. Agent teams complete work during EXECUTE cycle, write results to `execution.md`
2. Coordinator reviews `execution.md` and code changes during ASSESS cycle
3. At phase completion, coordinator makes a git commit with a descriptive message
4. Commits contain tractable deltas — logically coherent changes focused on the implementation itself. No conversation artifacts, debug output, or intermediate states.

## Inter-Phase Smoke Tests

At phase completion, before advancing to the next phase, the coordinator dispatches an integrative smoke test:
- **Golden path walking** — exercise the end-to-end journey across all completed phases
- **Integration verification** — confirm that phase outputs compose correctly with prior phases
- **Environment health** — verify dev environment still starts and services connect

If the smoke test reveals a regression or integration failure, the coordinator enters a rework EXECUTE cycle targeting the specific integration point. This catches cascading compositional failures early — before they compound through subsequent phases.

## Agent Team Crash Handling

When a teammate crashes during an EXECUTE cycle:
1. Orchestrator detects teammate death
2. Orchestrator kills remaining team members
3. All `execution.md` entries written before the crash are preserved
4. Orchestrator escalates to supervisor with crash details and preserved work
5. Supervisor informs the user

The coordinator does NOT silently retry. The crash exits the coordinator loop entirely, because the failure may indicate a systemic issue requiring human awareness.
