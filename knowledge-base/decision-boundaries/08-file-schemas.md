# DB-08: File Schemas

**Status:** DECIDED
**Severity:** Medium — affects interoperability between tiers
**Question:** What are the concrete formats for golden context files?

**Decision:** Four sub-decisions, all research-grounded:
1. **`roadmap.md`** — Adopt proposed schema (numbered milestones with status, summary, dependency annotations). Sole consumer is supervisor; distractor concern (§1) doesn't apply.
2. **`execution.md`** — Unified `## Tasks` section in execution order with front-loaded failure summary. Aligns attention sinks (§2) with actionable content, supports incremental writes (§4), handles all task states.
3. **`decisions.md`** — Newest-first ordering, grouped by cycle. Aligns attention sink (§2) with most relevant content; cycle groups as coherent chunks (§11 event segmentation).
4. **Schema reference location** — No separate schema files. Schemas defined here (clouical reference), embedded in protocol files (agent compliance), implemented in orchestrator validation code (enforcement). Avoids instruction density overhead (§5) and token volume (§1).

## Prior Resolution

Several file schemas were resolved by earlier decision boundaries:

| File | Resolved by | Reference |
|---|---|---|
| `compose.py` | DB-02 | Typed-function call graph, AST-validated by orchestrator |
| `active/coordinator.md` | DB-03 | Pointer-based checkpoint (cycle, step, phase status, partial progress) |
| `active/supervisor.md` | DB-03 | Position, open escalations, pending items |
| `status.md` | DB-07 | Current State + Phase Progress table + Notes |
| `handoff.md` | Verification protocol | 7-section schema (environment, what was built, walk-through, services, verified, limitations, what to look for) |
| Escalation files | Escalation protocol | Classification, context, issue, evidence, options, recommendation, disposition |
| Schema enforcement | DB-05 | Structural validation at cycle boundaries (form, not content) |

## Design Constraints from Research

Three research findings constrain all schema decisions:

**Front-load critical content (§1–2).** Attention sinks give first tokens architectural privilege. Lost-in-the-middle degrades retrieval for mid-file content. Every schema must put the most actionable information first — status and failures before details and history.

**Structured markdown, not rigid formats (§6).** Output schemas should be "human-readable but with enough structure for reliable extraction — not rigid JSON." Structured markdown with headings, tables, and consistent field patterns.

**Every token is actively harmful (§1).** Gao et al. showed that even with perfect retrieval, task performance drops 13.9–85% as input length grows. Schemas must be concise. No preamble, no boilerplate, no fields that don't serve a consumer.

## Decided Schemas

### `roadmap.md`

```markdown
# Roadmap

## Milestones

### 1. project-setup
**Status:** completed
**Summary:** Initialize project structure, CI/CD, and development environment
**Completed:** 2026-03-18

### 2. user-authentication
**Status:** in_progress
**Summary:** User registration, login, session management, and authorization
**Started:** 2026-03-19
**Coordinator:** active

### 3. dashboard
**Status:** pending
**Summary:** Main dashboard with analytics widgets and data visualization
**Depends on:** user-authentication

### 4. payment-integration
**Status:** pending
**Summary:** Stripe integration for subscription billing
**Depends on:** user-authentication
**Independent of:** dashboard (candidate for parallel coordinator)

## Ordering
Default: sequential (each milestone depends on the previous).
Exceptions noted above with "Independent of" annotations.
```

**Design rationale:**
- Numbered for ordering — the supervisor reads this to answer "what's next?"
- Status values: `pending`, `in_progress`, `completed`, `blocked`
- Dependency annotations (`Depends on`, `Independent of`) record supervisor reasoning about milestone relationships. Currently Clou runs milestones sequentially (architecture decision #2: "Serial execution. Earn parallel."), but the annotations are cheap and serve the supervisor's own strategic reasoning about future parallelization.
- `roadmap.md` does not appear in any coordinator per-cycle read set (DB-03). The supervisor is the sole consumer. Distractor interference (§1) is not a concern — annotations are directly relevant to the supervisor's sequencing decisions.

**Structural validation:** Required sections: `## Milestones` with at least one `### N. name` entry. Each entry must have `**Status:**` with valid value.

### `execution.md`

```markdown
# Execution: <phase-name>

## Summary
status: in_progress
started: 2026-03-19T04:15:00Z
completed: —
tasks: 5 total, 3 completed, 1 failed, 1 in_progress
failures: T3 — SQL injection in search handler
blockers: none

## Tasks

### T1: Set up database schema
**Status:** completed
**Files changed:**
  - src/db/schema.ts (created)
  - src/db/migrations/001_users.sql (created)
  - src/db/migrations/002_sessions.sql (created)
**Tests:** 3 migration tests passing
**Notes:** Used uuid for primary keys per project.md convention

### T2: Implement user model
**Status:** completed
**Files changed:**
  - src/models/user.ts (created)
  - src/models/user.test.ts (created)
**Tests:** 8 unit tests passing
**Notes:** —

### T3: Implement search handler
**Status:** failed
**Error:** SQL injection vulnerability — user input concatenated into query string
**Files changed:**
  - src/handlers/search.ts (created, partial)
**Recommendation:** Parameterize all query inputs. See decisions.md cycle 2 for Brutalist finding.

### T4: Implement auth middleware
**Status:** in_progress
**Files changed:**
  - src/middleware/auth.ts (created, partial)
**Notes:** JWT validation complete, session management in progress

### T5: API error handling
**Status:** pending
**Files changed:** —

## Environment Impact
- New dependencies: bcrypt@5.1.0, pg@8.11.0
- New env vars needed: DATABASE_URL
- Migration required: yes (2 new migrations)
```

**Design rationale:**

The schema resolves three research tensions:

1. **Front-loaded failure summary (§2 attention sinks).** The `## Summary` section puts task counts, failures, and blockers at the top — the architecturally privileged position. The ASSESS coordinator (primary consumer) sees the actionable content first. If there are no failures, the summary says so in one line and the coordinator can scan lightly. If there are failures, they're named before any detail.

2. **Execution order, not outcome categories (§4 observation masking).** Agent teams write execution.md incrementally during the cycle (golden-context.md: "written incrementally so that crash recovery can preserve partial work"). Tasks complete in execution order. A schema with separate `## Tasks Completed` / `## Tasks Failed` sections forces the agent to reorganize the file as results come in — more write complexity, less natural structure. Execution order preserves temporal coherence and supports all states: pending, in_progress, completed, failed.

3. **All task states represented (§10 structured intermediate artifacts).** The original proposal had no place for `in_progress` or `pending` tasks. Mid-execution, these states are critical for crash recovery and coordinator visibility. Status per task handles the full lifecycle.

**Structural validation:** Required sections: `## Summary` with `status:` field, `## Tasks` with at least one `### T<N>:` entry. Each task must have `**Status:**` with valid value (`pending`, `in_progress`, `completed`, `failed`).

### `decisions.md`

```markdown
## Cycle 5 — Brutalist Assessment

### Accepted: Missing input validation on /api/orders
**Brutalist said:** "The orders endpoint accepts negative quantities and prices below zero"
**Action:** Created rework task to add validation middleware
**Reasoning:** Valid security finding. Acceptance criteria require "robust input validation on all endpoints."

### Overridden: "Should add rate limiting to all endpoints"
**Brutalist said:** "No rate limiting on any API endpoint"
**Action:** Override — no changes
**Reasoning:** Rate limiting is out of scope for this milestone (milestone.md scope boundaries). Will be addressed in infrastructure milestone per roadmap.

## Cycle 5 — Coordinator Judgment

### Tradeoff: Chose bcrypt over argon2 for password hashing
**Context:** Both meet security requirements. argon2 has better theoretical properties but bcrypt has broader ecosystem support in the current stack.
**Decision:** bcrypt — matches project.md tech stack constraints, well-tested library available
**Reasoning:** Delegated authority allows implementation choices within security requirements. Both options meet the "industry-standard password hashing" requirement.

## Cycle 3 — Brutalist Assessment

### Accepted: SQL injection risk in search handler
**Brutalist said:** "The search endpoint concatenates user input into SQL query"
**Action:** Created rework task to parameterize queries
**Reasoning:** Valid security finding, clear fix

### Overridden: "Architecture should use microservices"
**Brutalist said:** "Monolithic architecture won't scale past 10K users"
**Action:** Override — no changes
**Reasoning:** project.md specifies monolith-first approach. Current milestone targets MVP with <1K users. Revisit when scaling milestone is created.

## Cycle 2 — Brutalist Assessment

### Overridden: "Missing comprehensive error handling in utility functions"
**Brutalist said:** "Internal helper functions lack try/catch blocks"
**Action:** Override — no changes
**Reasoning:** Internal functions called by validated callers. Error handling at this level adds noise without value. Errors caught at API boundary per project conventions.
```

**Design rationale:**

1. **Newest-first ordering (§2 attention sinks).** The ASSESS coordinator — the most frequent and time-sensitive reader — needs recent judgments for continuity (don't re-override the same finding, detect patterns across cycles). Newest-first places the most recent cycle's judgments in the architecturally privileged first-token position. In an append log, the attention sink would be wasted on cycle 1 entries — the oldest, least relevant content.

2. **Grouped by cycle (§11 event segmentation).** Each coordinator cycle is a natural event boundary — a distinct episode with its own Brutalist interaction, evidence, and decisions. Grouping entries by cycle creates coherent sub-chunks within the file, aligned with how the coordinator segments its own work. The cycle group header (`## Cycle N — Brutalist Assessment` or `## Cycle N — Coordinator Judgment`) serves as both a chunk boundary and temporal marker.

3. **Two entry types.** Brutalist interactions follow the established format: what Brutalist said → action → reasoning. Non-Brutalist judgments (tradeoffs, delegated authority edge cases) use a different header (`Coordinator Judgment`) with context → decision → reasoning. Both are logged because decisions.md is the coordinator's complete judgment record, not just the Brutalist interaction log. The coordinator protocol (lines 117-121) already defines the required fields.

4. **U-shaped positional alignment.** With newest-first: current cycle at top (attention sink — strong), oldest cycle at bottom (end of file — moderate per U-curve), middle cycles in the middle (weakest attention). For a 20-cycle-cap milestone, the file stays under ~3K tokens. At that scale the dead zone is narrow. But the principle holds: the latest judgments should be in the strongest position.

**Structural validation:** File must contain at least one `## Cycle N` section. Each section must have at least one `### Accepted:` or `### Overridden:` or `### Tradeoff:` entry with the required fields for its type.

### Schema Reference Location

**Decision:** No separate schema files in `.clou/`.

Schemas are maintained in three places:
1. **This document (DB-08)** — clouical reference with full rationale
2. **Protocol files** — embedded in per-cycle protocol instructions (what the agent reads as its first action). The protocol tells the agent what to produce and in what format — the schema is implicit in the production instruction.
3. **Orchestrator validation code** — structural validation at cycle boundaries (DB-05). Checks required sections, required fields, valid values.

**Why not separate schema files:**
- **Instruction density (§5):** Every file in the read set adds discrete instructions. A schema file competes with the protocol file for the agent's compliance attention.
- **Decomposition (§7):** The protocol file IS the decomposed instruction for that cycle. The schema is part of the instruction, not a separate artifact.
- **Token volume (§1):** A schema file read alongside the protocol adds tokens to every cycle. The protocol already contains the format specification — a separate schema file would be redundant tokens, each one actively harmful per §1.

## Resolved Questions

- [x] **`roadmap.md` schema:** Numbered milestones with status, summary, dates, dependency annotations. Adopted as proposed — sole consumer is supervisor, distractor concern doesn't apply.
- [x] **`execution.md` schema:** Unified Tasks section in execution order with front-loaded failure summary. Supports incremental writes, all task states, attention-aligned critical content.
- [x] **`decisions.md` entry format:** Newest-first, grouped by cycle. Two entry types (Brutalist Assessment, Coordinator Judgment). Attention sink aligned with most relevant content.
- [x] **`handoff.md` schema:** Defined in verification protocol (7 sections). No changes needed.
- [x] **Checkpoint schemas:** Defined in DB-03. No changes needed.
- [x] **`status.md` schema:** Defined in DB-07. No changes needed.
- [x] **Schema enforcement mechanism:** Resolved by DB-05 (structural validation at cycle boundaries).
- [x] **Schema reference location:** No separate files. Defined in DB-08, embedded in protocols, enforced by orchestrator code.

## Cascading Effects

- **DB-10 (Team Communication):** `execution.md` schema now defined — agent teams know the output format. DB-10 decided: agent briefing via Agent tool prompt pointing to compose.py function signature + phase.md + worker protocol. execution.md summary status line is read by the coordinator's circuit breaker during EXECUTE (before dispatching dependent tasks).
- **Protocol files:** When protocol files are written, they must embed the relevant output schemas for each cycle type (e.g., ASSESS protocol includes decisions.md entry format, EXECUTE protocol references execution.md format).
- **Orchestrator validation:** The validation code (DB-05) must implement structural checks matching the schemas defined here: required sections, required fields per entry type, valid status values.
