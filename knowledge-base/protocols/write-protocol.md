# Write Protocol

## Core Rule

**Ownership boundaries are write boundaries.** Each tier writes only to files it owns. Nobody writes upward. Reads flow freely downward.

## Ownership Map

```
User writes:
  .clou/requests.md                           (append only)
  .clou/services/*/status.md                  (credential confirmation)

Supervisor writes:
  .clou/project.md
  .clou/roadmap.md
  .clou/requests.md                           (processing annotations)
  .clou/milestones/<name>/milestone.md         (creation only)
  .clou/milestones/<name>/requirements.md
  .clou/milestones/<name>/escalations/*.md     (disposition section only)
  .clou/active/supervisor.md

Coordinator writes:
  .clou/milestones/<name>/compose.py           (typed-function call graph)
  .clou/milestones/<name>/status.md
  .clou/milestones/<name>/decisions.md
  .clou/milestones/<name>/escalations/*.md     (creation)
  .clou/milestones/<name>/phases/*/phase.md
  .clou/active/coordinator.md

Agent Teams write:
  .clou/milestones/<name>/phases/*/execution.md
  (plus actual codebase files)

Verification Agent writes:
  .clou/milestones/<name>/phases/verification/execution.md
  .clou/milestones/<name>/handoff.md

Service Discovery Agent writes:
  .clou/services/<name>/setup.md
  .clou/services/<name>/.env.example
  .clou/services/<name>/status.md              (initial creation)
```

## Read Permissions

All tiers can read everything at or below their level:
- **Supervisor:** reads everything in `.clou/`
- **Coordinator:** reads everything in its milestone directory + top-level project files (`project.md`, `roadmap.md`)
- **Agent Teams:** reads their function signature from `compose.py`, phase context from `phase.md`, + the codebase
- **Verification Agent:** reads milestone spec + requirements + runs against live environment

## Update Timing

### Standard Updates
Updates happen at **loop boundaries**, not continuously. Each loop iteration produces a coherent snapshot of state.

- **Supervisor:** Updates golden context when it completes an evaluation cycle — after reviewing a milestone's completion, before advancing to the next.
- **Coordinator:** Updates at cycle boundaries — after a Brutalist assessment, after accepting or reworking a phase.
- **Agent Teams:** Update `execution.md` when they complete their assigned work.

### Checkpoint Files at Cycle Boundaries
Each coordinator cycle is a fresh session. `active/coordinator.md` is written at the end of every cycle — it is the sole state transfer mechanism between sessions. `active/supervisor.md` is written at supervisor loop boundaries. These checkpoint files are pointers, not summaries — they tell the next session where to look in the golden context.

### Coordinator-Only Commits at Phase Completion

Agent teams write code but do NOT commit. The coordinator is the sole committer. At phase completion, the coordinator reviews `execution.md` and code changes, then commits a tractable delta — logically coherent changes focused on the implementation. No conversation artifacts, debug output, or intermediate states.

This provides:
- **Rollback granularity** — per-phase without per-cycle overhead
- **Clean history** — only reviewed, coherent changes enter git
- **Conflict prevention** — coordinator resolves conflicts before committing, not after

## Split Ownership: `escalations/*.md`

Escalation files are created by the coordinator but contain a Disposition section resolved by the supervisor. The boundary is explicit:
- Everything above `## Disposition` is coordinator-owned
- The `## Disposition` section is supervisor-owned

## Enforcement

Ownership enforcement operates at two levels:

1. **Hooks (DB-01, decided):** PreToolUse hooks validate file paths against the tier's ownership map. Writes to unauthorized `.clou/` paths are denied with an explicit error message.
2. **Structural validation (DB-05, decided):** The orchestrator validates golden context file structure at cycle boundaries. Malformed writes trigger revert-and-retry.
3. **Git boundary:** Only the coordinator commits to git. Agent teams write code but do not run `git commit`. This ensures all committed changes are reviewed and coherent.

## Coherence Guarantees

The write protocol ensures:
1. **No write conflicts** — only one tier writes to any given file (with escalations as the sole documented exception)
2. **Clean audit trail** — you can look at any file and know which tier produced it
3. **Safe restart** — on session restart, the tier reads its checkpoint and the golden context files it owns, reconstructing state without ambiguity about what's current
4. **No upward contamination** — implementation details don't leak into planning artifacts
