# DB-07: Milestone Ownership

**Status:** DECIDED — Split files with clean ownership boundaries
**Severity:** Medium — affects protocol clarity and per-cycle context quality
**Decided:** 2026-03-19

## Decision

**Split `milestone.md` into two files: `milestone.md` (supervisor's spec, immutable after handoff) and `status.md` (coordinator's progress journal). Each file has a single owner. No exceptions to the write boundary.**

This decision is grounded in Clou's research foundations: file boundaries must align with information boundaries because the per-cycle read set — Clou's primary mechanism for combating context degradation — selects at file granularity.

## The Research-Grounded Argument

The question is not operational convenience (though hook enforcement is simpler with split files). The question is: **what is the optimal unit of context for a transformer reading golden context at cycle boundaries?**

### Distractor Interference at File Granularity

Context Is Adversarial (Research Foundations §1): every token the coordinator reads that isn't relevant to its current cycle is a distractor — not neutral, *actively harmful*. Semantically similar distractors are the worst kind: "models perform better with shuffled, incoherent haystacks than logically structured ones — structure introduces more confusable signals."

A spec and a status update for the same milestone are maximally semantically similar. They share terminology, entities, scope descriptions. When the coordinator reads a combined file during an EXECUTE cycle to check progress, the spec section is dense, relevant-seeming distractor text that the transformer must attend to and ultimately discard.

### Per-Cycle Read Set Alignment

The per-cycle read set (DB-03, DB-04) is Clou's primary mechanism against context degradation. Each cycle type gets exactly the golden context files it needs. But this mechanism selects at **file granularity** — it can include or exclude a file, not a section within a file.

| Cycle | Needs spec? | Needs status? |
|---|---|---|
| PLAN | Yes | No |
| EXECUTE | No | Yes |
| ASSESS | No (constraints in requirements.md) | Yes |
| VERIFY | No (outcomes in intents.md) | Yes |
| EXIT | No | Yes |

In 4 of 5 cycle types, the coordinator needs status but not the spec. With a combined file, every EXECUTE, ASSESS, VERIFY, and EXIT cycle loads the entire specification as a distractor. With split files, the orchestrator loads only `status.md` for those cycles, eliminating the distractor entirely.

### Chunking and Hierarchical Memory

Research Foundations §11: "each golden context file is a semantic chunk." A specification and a progress tracker are different semantic chunks — different authors, different update frequencies (spec: once; status: every cycle), different consumers, different temporal horizons (spec: immutable after creation; status: most frequently mutated file in a milestone's lifecycle).

The golden context already implements hierarchical memory — "project.md (long-term), milestone.md (medium-term), execution.md (short-term)." But a combined milestone.md conflates two memory levels: the spec is medium-term (set once, stable), the status is working state (updated every cycle). Splitting respects the hierarchy.

### Enforcement Alignment

DB-01 hooks operate on file paths. DB-05 structural validation checks file existence and section presence. Both are simpler and more robust with split files:
- **Hooks:** path matching (`status.md` → coordinator; `milestone.md` → supervisor). No need to parse file internals.
- **Validation:** check that `status.md` exists and has required sections. No need to verify divider position.

## Updated File Tree

```
milestones/<name>/
├── milestone.md        # [supervisor writes, immutable after handoff]
├── intents.md          # [supervisor writes] — observable outcomes (DB-14)
├── requirements.md     # [supervisor writes] — implementation constraints
├── status.md           # [coordinator writes]
├── compose.py          # [coordinator writes]
├── decisions.md        # [coordinator writes]
├── handoff.md          # [verification agent writes]
├── escalations/
└── phases/
```

## `status.md` Schema

```markdown
# Status: <milestone-name>

## Current State
phase: <current phase name>
cycle: <cycle number>
last_updated: <ISO timestamp>

## Phase Progress
| Phase | Status | Summary |
|---|---|---|
| foundation | complete | 2 tables, 3 indexes |
| core | complete | User model + auth service |
| api-layer | in_progress | 4/7 endpoints done |
| frontend | pending | — |
| verification | pending | — |

## Notes
- <timestamped progress notes, decisions in context, blockers>
```

**Distinct from `active/coordinator.md`:** The checkpoint is a machine-oriented pointer — it tells the orchestrator what cycle to run next. `status.md` is a human-readable progress journal — it tells the supervisor (and the user) what happened. The checkpoint is deleted when the milestone completes; the status file persists as part of the milestone record.

## Per-Cycle Read Sets (Updated)

```
PLAN:    milestone.md, intents.md, requirements.md, project.md
EXECUTE: status.md, compose.py, phase.md
ASSESS:  status.md, compose.py, execution.md, requirements.md, decisions.md, assessment.md
VERIFY:  status.md, intents.md, compose.py
EXIT:    status.md, handoff.md, decisions.md
```

Note: the coordinator checkpoint (`milestones/<name>/active/coordinator.md`) is read by the orchestrator, not by the coordinator agent. The orchestrator extracts cycle context and injects it into the cycle prompt. The checkpoint is not in any agent read set.

## Why Not Divider Convention

Option A (divider in a single file) trades file simplicity for enforcement complexity. The divider is a convention the model must remember; file ownership enforced by hooks (DB-01) is structural. More critically, the divider prevents the per-cycle read set from excluding the irrelevant section — the fundamental mechanism against context degradation operates at file granularity, not section granularity.

## Impact on Other Documents

Updated as part of this decision:
- `golden-context.md` — file tree, file purposes, ownership table, update timing
- `write-protocol.md` — clean ownership (no split-ownership exception for milestone.md)
- `protocols/coordinator.md` — per-cycle read sets, EXIT step, artifacts table, write-back
- `integration/orchestrator.md` — `determine_next_cycle` read sets
- `architecture.md` — coordinator produces description
- `decision-boundaries/03-context-window-lifecycle.md` — write-back table, EXIT cycle produces
