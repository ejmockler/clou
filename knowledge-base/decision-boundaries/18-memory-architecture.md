# DB-18: Memory Architecture

**Status:** DECIDED
**Decided:** 2026-03-30
**Severity:** High — determines whether operational learning accumulates across milestones or resets each time; affects context quality, cost, and the system's ability to improve through use
**Question:** How does Clou transition from a filing system (static cycle-type → file-list mappings) to a memory system (dynamic salience resolution that surfaces the right context for any moment)?

**Decision:** Three mechanisms — consolidation (`memory.md` at milestone boundaries, orchestrator-primary), forgetting (milestone-distance decay with temporal invalidation), and rule-based scored retrieval (content-type-first, extending existing read sets). The semantic memory artifact is `.clou/memory.md` — distinct from `understanding.md` (user-intent) and owned by the orchestrator for structural patterns, annotatable by the supervisor for conceptual patterns.

## The Problem

Clou's golden context is append-only. Five milestones in, the `.clou/` directory contains:
- 5 milestone directories with full episodic records (decisions.md, execution.md, assessment.md, escalations, metrics.md)
- Telemetry JSONL files (one per coordinator session, one per supervisor session) — never pruned
- Session transcripts — never pruned
- understanding.md growing with each validated user exchange — no compaction

The coordinator doesn't feel this — per-cycle read sets (DB-03) scope its context to its own milestone. But the system as a whole has no mechanism for:

1. **Cross-milestone learning.** Recurring quality gate findings, fragile code areas, successful decomposition strategies — none of this survives milestone completion in a form the system can retrieve.
2. **Principled forgetting.** Nothing decays. Early-session constraints that were later revised remain at the same priority as current constraints. Superseded decisions are never invalidated.
3. **Salience-based retrieval.** All files in a read set are treated equally. The orchestrator cannot distinguish between a highly relevant decision from the previous milestone and a stale observation from 10 milestones ago.

The research (§16) establishes that this gap has measurable cost: agents without active memory drop from 80%+ to 45% task completion (MemoryArena), while memory improvements often yield larger performance gains than model scaling (2025 survey §11).

## Research Grounding

### From Existing Foundations

| Finding | Source | Implication |
|---|---|---|
| Every unnecessary token is actively harmful, not neutral | §1 (context degradation) | Accumulated stale context degrades cycle quality |
| Attention IS associative memory retrieval | §2 (Hopfield equivalence) | The orchestrator builds the pattern library the transformer retrieves from |
| Observation masking > LLM summarization | §4 (JetBrains) | Structural compaction, not prose summaries |
| Memory > model scaling | §11 (2025 survey) | Memory architecture is a higher-leverage investment than model upgrades |
| Forgetting is computationally useful | §11 (cognitive science) | Principled decay is a feature, not a loss |

### From §16 (Memory Systems)

| Finding | Source | Implication |
|---|---|---|
| Content type prior dominates memory value | A-MAC (arXiv:2603.04549) | Score by type first (decision, constraint, observation), then relevance |
| Consolidation from episodic → semantic is the critical gap | 2025 survey (arXiv:2512.13564) | Milestone-boundary extraction of cross-milestone patterns |
| Selective forgetting is the weakest competency | MemoryAgentBench (arXiv:2507.05257) | Explicit forgetting mechanism needed |
| Optimal pattern library is maximally diverse | Spherical codes (arXiv:2410.23126) | Merge near-duplicates during consolidation |
| Sparse retrieval outperforms soft averaging | HFY (arXiv:2411.08590) | Inject few, sharp memories — not broad context |
| Bi-temporal edges enable "what was true when" | Graphiti (arXiv:2501.13956) | Temporal invalidation, not deletion |
| Goal-conditioned gating prevents memory pollution | CraniMem (arXiv:2603.15642) | Admission control: not all observations become memories |
| Intent-driven retrieval > similarity-driven | MemGuide (arXiv:2505.20231) | Cycle type determines what kind of memory is needed |
| Summarization destroys 60% of facts silently | KOs (arXiv:2603.17781) | Structured external storage, never conversation compression |

### From Milestones 12–16 (Empirical Validation)

Consolidation of five completed milestones revealed concrete patterns that would have been valuable earlier:

| Pattern | Evidence | Value if surfaced earlier |
|---|---|---|
| Single-phase rule for prompt engineering | M12–M15: all used 1 implementation phase + verification when modifying same file | Calibrates PLAN decomposition for same-file milestones |
| Parallel decomposition when files are independent | M16: gather() for research ∥ guidance worked cleanly | Validates width-aware planning (§15) from operational data |
| Pre-existing findings converge to override | Same findings (split-brain, UserGate race) across 4+ milestones, always overridden | Skip rework; override decisively after 3 recurrences |
| Quality gate degraded for non-code artifacts | Brutalist MCP unavailable or low-signal on M13–M16 (prompt-only) | Skip brutalist for prompt-only milestones |
| Validation failures are format noise | ~3 per milestone, all checkpoint schema issues, never substantive | Lower validation severity for known-noisy fields |
| 5-cycle protocol is stable | M13–M16 all converged on PLAN→EXECUTE→ASSESS→VERIFY→EXIT | Cost calibration: ~35 min, ~50K output tokens per prompt milestone |

None of this was available to the system. The supervisor planned each milestone without knowledge of prior operational outcomes. The orchestrator applied the same validation stringency despite evidence of systematic false positives.

## Decision

### D1: Semantic Memory Artifact — `memory.md`

**Decision: Option B — a new `.clou/memory.md` file, distinct from understanding.md.**

**Why not Option A (extend understanding.md):**
DB-13 requires bidirectional user validation for every understanding.md write. System-derived operational patterns (cycle counts, decomposition outcomes, quality gate convergence) don't need user validation — they're empirical, not interpretive. Mixing the two protocols in one file either violates DB-13 (silent writes to user-validated artifact) or imposes unnecessary friction (asking the user to validate "milestones averaged 5 cycles").

**Why not Option C (patterns/ directory):**
Five milestones produced 5 pattern categories with 3–5 entries each — roughly 15 patterns totaling ~800–1200 tokens. This fits comfortably in a single file. The Zettelkasten approach adds complexity (directory management, file discovery, cross-file linking) that would be justified at 100+ patterns but is premature at 15. If the memory store grows beyond a single file's useful range, migration from flat file to directory is a one-time structural change, not an architectural rewrite.

**Schema:**

```markdown
# Operational Memory

## Patterns

### <pattern-name>
type: <decomposition | quality-gate | cost-calibration | escalation | debt>
observed: <comma-separated milestone names>
reinforced: <count>
last_active: <milestone name>
invalidated: <milestone name, or empty>

<pattern description — 1-3 sentences>
```

Each pattern entry is ~50–100 tokens. Content type (`type:` field) is the primary retrieval signal (A-MAC). Provenance (`observed:`) enables audit. Reinforcement count tracks how many consolidation passes confirmed this pattern. `invalidated:` implements Graphiti's temporal invalidation — the pattern remains in the file for historical query but is excluded from active context assembly.

**Ownership:** `[orchestrator writes]` for structural patterns extracted during consolidation. `[supervisor annotates]` for conceptual patterns added during re-entry conversation (DB-16). Clean dual-ownership: the orchestrator creates entries, the supervisor may add entries or annotate existing ones during user-facing sessions. No other tier writes to memory.md.

**Read set membership:**
- Supervisor reads memory.md at orient (alongside understanding.md and project.md)
- Coordinator PLAN cycle reads memory.md (calibrates decomposition and cost expectations)
- Other coordinator cycles do not read memory.md (they operate within the current milestone)

**Validation tier:** Narrative (DB-12) — required sections checked, content quality via reinforcement count (patterns with reinforcement > 3 are high-confidence; patterns with reinforcement 1 are provisional).

### D2: Consolidation — Orchestrator Reads Its Own Telemetry

**Decision: The orchestrator performs structural consolidation by reading data it wrote itself — metrics.md and compose.py (AST). It never parses LLM-written prose. The supervisor adds conceptual annotations during re-entry.**

#### The parseability constraint

Golden context files written by LLM agents (decisions.md, assessment.md, execution.md, escalation files) are **narrative tier** (DB-12) — form-checked only, not structurally parsed. Their internal format is not guaranteed. Attempting to extract finding categories from decisions.md or tool availability from assessment.md means parsing LLM output, which §9 establishes as unreliable. `compact_decisions()` already does heuristic regex parsing for this and it is fragile by nature.

**Principle: consolidation reads from orchestrator-written data, not from agent-written prose.**

The orchestrator observes everything that happens during a milestone through the SDK message stream and its own control flow. These observations are already captured in the telemetry system (`clou.telemetry` → JSONL span logs → `metrics.md`). Consolidation reads from `metrics.md` (which the orchestrator wrote) and `compose.py` (which is AST-validated). No LLM output parsing.

#### Data sources for structural consolidation

| Signal | Source | Reliability |
|---|---|---|
| Cycle count and type distribution | `metrics.md` cycle table | Deterministic (orchestrator-written) |
| Token usage per cycle and total | `metrics.md` token table | Deterministic (from `ResultMessage.usage`) |
| Phase count and topology (sequential vs. gather) | `compose.py` AST analysis | AST-parsed (DB-02, DB-12 tier: AST) |
| Rework triggered (ASSESS → EXECUTE rework) | `metrics.md` cycle table (cycle type sequence) | Deterministic (orchestrator observes checkpoint transitions) |
| Escalation count and classification | `metrics.md` incident log | Deterministic (orchestrator handles escalation flow) |
| Agent spawn/complete/fail rates | `metrics.md` agent table | Deterministic (orchestrator manages agent lifecycle) |
| Quality gate tool invocation vs. unavailability | **Extended telemetry** (see below) | Deterministic (observable from SDK tool call results) |
| Validation failure count and retry count | `metrics.md` incident log | Deterministic (orchestrator runs validation) |

#### Telemetry extension

The telemetry system (`clou.telemetry`) needs three additional event types to support consolidation:

```python
# Emitted by orchestrator after each ASSESS cycle
telemetry.event("quality_gate.result",
    milestone=milestone,
    cycle=cycle_num,
    tools_invoked=["roast_codebase", "roast_security"],   # which tools were called
    tools_unavailable=["roast_architecture"],               # which returned errors/403
    finding_count=7,                                        # total findings reported
)

# Emitted by orchestrator when checkpoint shows rework
telemetry.event("cycle.rework",
    milestone=milestone,
    cycle=cycle_num,
    from_step="ASSESS",
    to_step="EXECUTE",
    phase=current_phase,
)

# Emitted by orchestrator when escalation file is created
telemetry.event("escalation.created",
    milestone=milestone,
    cycle=cycle_num,
    classification="validation_failure",  # from escalation file header
    severity="blocking",
)
```

These events are observable from the SDK message stream and orchestrator control flow — no parsing of LLM-written files. `write_milestone_summary()` aggregates them into `metrics.md` alongside existing token and agent data.

#### The consolidation pass

Runs after `metrics.md` is written (existing milestone completion hook), before the supervisor presents handoff.md:

1. **Read `metrics.md`** — cycle table, token table, incident log, agent table. All orchestrator-written.
2. **Read `compose.py` via AST** — phase count, gather() groups, function signatures. Already validated by DB-02 infrastructure.
3. **Read existing `memory.md`** — current pattern entries.
4. **Extract or reinforce patterns:**
   - Match structural signals against existing patterns (e.g., cycle count → `cost-calibration` pattern for this milestone type)
   - Increment `reinforced:` and append milestone to `observed:` for matching patterns
   - Create new entries when structural signals cross thresholds (e.g., quality gate unavailable in 3+ consecutive milestones → new `quality-gate` pattern)
   - Mark `invalidated:` when structural signals contradict existing patterns (e.g., rework rate dropped to 0 for a pattern that said "this area requires rework")
5. **Write `memory.md`** — append new entries, update existing entries.

**What the orchestrator extracts (structural, deterministic):**

| Pattern type | Extracted from | Example |
|---|---|---|
| `cost-calibration` | metrics.md cycle/token tables | "Prompt milestones: 5 cycles, ~50K output tokens, ~35 min" |
| `decomposition` | compose.py AST (phase count, gather usage) | "2-phase milestones (impl + verify) are the stable topology" |
| `quality-gate` | Extended telemetry (tool invocation/unavailability) | "Brutalist tools unavailable in 3 of 5 milestones" |
| `escalation` | metrics.md incident log (escalation count/classification) | "validation_failure is the dominant escalation type (80%)" |
| `debt` | metrics.md incident log (validation retry rates) | "Checkpoint schema validation fails ~3x per milestone" |

**What the orchestrator CANNOT extract (semantic, requires judgment):**

| Pattern | Why it can't be structural | Who provides it |
|---|---|---|
| "Skip brutalist for prompt-only milestones" | Requires classifying milestone scope | Supervisor, during re-entry |
| "Single-phase when concerns touch the same file" | Requires understanding file coupling | Supervisor, during re-entry |
| "Override after 3 recurrences of the same finding" | Requires matching finding identity across milestones | Supervisor, during re-entry |
| "Width-aware decomposition proven for independent files" | Requires understanding what made files independent | Supervisor, during re-entry |

**The supervisor's role:** During re-entry (DB-16), the supervisor reads memory.md (with the orchestrator's structural entries) and the user's feedback. The structural data provides evidence; the supervisor draws conclusions. "Brutalist tools unavailable in 3 of 5 milestones" (structural) → "Skip brutalist for prompt-only milestones" (conceptual, supervisor-authored after user confirms). This mirrors DB-13's bidirectional grounding: the supervisor presents the inference, the user evaluates, the supervisor writes on confirmation.

The schema distinguishes authorship via content type: `cost-calibration`, `decomposition` (topology statistics), `escalation` (frequency data), and `debt` (validation noise rates) are orchestrator-authored. `quality-gate` entries with behavioral guidance and `decomposition` entries with heuristic reasoning are supervisor-authored.

**Archival after consolidation:**

Completed milestone directories retain:
- `milestone.md` (immutable spec — reference value)
- `status.md` (final state — one-line summary per phase)
- `metrics.md` (aggregated telemetry — already a summary)
- `compose.py` (plan artifact — reference for decomposition patterns)

Archived (preserved in git history only):
- `decisions.md` (served the coordinator during the milestone; structural signals captured in telemetry)
- `assessment.md` (quality gate raw output; tool availability captured in telemetry)
- `execution.md` per phase (task-level detail; agent lifecycle captured in telemetry)
- `escalations/` (classification and severity captured in telemetry events)
- `active/coordinator.md` (terminal checkpoint — no recovery value after completion)

The archival pass is a `git rm` of the archived files after consolidation. The git history preserves full episodic detail for forensics. The working tree carries only what has ongoing value.

### D3: Forgetting — Milestone-Distance Decay with Temporal Invalidation

**Decision: Memories decay based on milestone distance. Superseded facts are invalidated, not deleted. Operational files (telemetry, sessions) are purged after consolidation.**

**Salience decay:**
Each pattern entry in memory.md carries `reinforced:` and `last_active:` fields. The orchestrator evaluates these at consolidation time:

- **Active** (default): `last_active` within 5 milestones. Entry is included in scored retrieval.
- **Fading**: `last_active` is 5–10 milestones ago and `reinforced:` < 3. Entry remains in memory.md but is excluded from active context assembly. Marked with `status: fading`.
- **Archived**: `last_active` > 10 milestones ago, or explicitly invalidated. Entry is moved to a `## Archived` section at the bottom of memory.md. Preserved for historical query but never enters context.

The thresholds (5, 10) are initial values. They should be calibrated against real milestone cadence — a project completing 2 milestones/day has different decay needs than one completing 2/month. FOREVER's model-centric time principle: calibrate to the system's internal evolution rate.

**Temporal invalidation:**
When consolidation detects a pattern contradicted by new evidence, the pattern gets:
```
invalidated: <milestone-name>
invalidation_reason: <one-line explanation>
```

The pattern moves to `## Archived` but is not deleted. Graphiti's principle: "what was true at time T" queries remain answerable. If a future milestone re-confirms the pattern, the invalidation can be reversed (increment `reinforced:`, clear `invalidated:`).

**Operational cleanup:**
- **Telemetry JSONL** (`.clou/telemetry/`): After consolidation writes metrics.md, raw JSONL files for that milestone's sessions are eligible for deletion. The orchestrator deletes them at consolidation time.
- **Session transcripts** (`.clou/sessions/`): Same policy — archived after consolidation.
- **`.coordinator-milestone`**: Cleared when no active milestone.

**What is NOT forgotten:**
- `project.md` — project-level, never decays
- `roadmap.md` — supervisor-managed, never decays
- `understanding.md` — user-validated, never auto-decayed (the supervisor updates it explicitly)
- `memory.md` patterns with `reinforced:` ≥ 5 — high-confidence patterns are durable regardless of recency

### D4: Scored Retrieval — Rule-Based, Content-Type-First

**Decision: Rule-based scoring extending existing per-cycle read sets. Content type is the primary signal. No embedding infrastructure required.**

**Why rule-based over embedding-based:**

A-MAC's ablation studies showed content type prior was the single most influential factor in memory value scoring — stronger than recency, relevance, or novelty. Rule-based scoring IS content-type-first scoring: the cycle type determines what *kinds* of memory are needed, which maps directly to the `type:` field on memory.md entries.

Embedding-based scoring adds:
- A vector store dependency (FAISS, ChromaDB, or similar)
- Embedding computation at write time (per consolidation pass) and read time (per cycle boundary)
- A similarity metric that A-MAC showed is weaker than content type for value prediction

The pragmatic path: rule-based scoring that extends the existing per-cycle read sets. If the memory store grows beyond what rule-based can handle (50+ patterns, multiple content types per cycle), embedding-based scoring can be added as a refinement layer — but the content-type-first structure remains the primary signal.

**Retrieval rules:**

The static per-cycle read sets (DB-03) become the *minimum* set. memory.md is added to specific cycles as a supplementary source:

| Cycle | Reads memory.md? | Pattern types surfaced |
|---|---|---|
| PLAN | Yes | `decomposition`, `cost-calibration`, `debt` |
| EXECUTE | No | (operates within current milestone) |
| ASSESS | Conditionally — if quality gate findings reference known patterns | `quality-gate`, `escalation` |
| VERIFY | No | (reads intents.md and compose.py) |
| EXIT | No | (reads handoff.md and decisions.md) |
| Supervisor orient | Yes (full file) | All types |

For the supervisor, memory.md is always in the read set — the supervisor needs the full operational picture to plan milestones and calibrate expectations.

For the coordinator, PLAN is the primary consumer. The coordinator reading memory.md during PLAN gets:
- Decomposition precedents: "single-phase for same-file work, parallel for independent files"
- Cost calibration: "prompt milestones average 5 cycles, ~50K output tokens"
- Known debt: "validation is noisy on checkpoint schema fields"

The ASSESS cycle reads memory.md only when the quality gate produces findings that match known recurring patterns — the orchestrator can check this by comparing finding categories against memory.md `quality-gate` entries. This prevents the coordinator from wasting cycles on findings already established as pre-existing.

**Scoring within type:**

When multiple patterns of the same type exist, the orchestrator ranks by:
1. `reinforced:` count (higher = more confident)
2. `last_active:` milestone distance (closer = more relevant)
3. `status:` (active > fading; archived is excluded)

Only active patterns enter context. The 7-9 chunk budget (§1) applies to the total read set including memory.md — if the static read set already fills 6 chunks, memory.md contributes at most 2-3 chunks (the highest-scoring patterns).

## Relationship to Existing Decisions

### Extends DB-03: Context Window Lifecycle

DB-03 established golden context as the sole compaction mechanism and defined static per-cycle read sets. DB-18 extends this with:
- Consolidation as a new compaction mechanism (episodic → semantic, operating on golden context files themselves)
- Scored retrieval as a dynamic extension of the static read sets (supplementary memories from the semantic layer)
- Forgetting as a lifecycle operation that DB-03 implicitly deferred ("session-per-cycle implicitly forgets within-cycle reasoning" — but has no mechanism for cross-milestone forgetting)

The core DB-03 guarantee is preserved: each cycle starts with a fresh context window populated from golden context. memory.md is simply another golden context file, following the same read-forward protocol.

### Extends DB-13: Supervisor Disposition

Understanding.md is user-intent memory (bidirectional grounding required). memory.md is operational memory (system-derived, supervisor-annotatable). The supervisor reads both at orient but writes them through different protocols:
- understanding.md: supervisor presents understanding → user evaluates → write on confirmation
- memory.md: orchestrator writes structural patterns automatically; supervisor adds conceptual patterns after user feedback during re-entry (DB-16)

### Extends DB-15: Architectural Tensions

DB-15 D3 (structural compaction of decisions.md) compacts within a milestone. DB-18 consolidation compacts across milestones. The two are complementary and orthogonal — D3 keeps per-cycle context manageable during a milestone; consolidation keeps cross-milestone context manageable after milestones complete.

### Extends DB-16: Autonomous Cycle Model

DB-16's re-entry point (supervisor presents handoff.md, captures what the human learned through use) becomes the natural moment for supervisor annotations to memory.md. The user's feedback ("the decomposition was good," "the quality gate was useless on this one") surfaces patterns the supervisor should persist.

### Informs DB-06: Token Economics

Structural consolidation has **zero LLM token cost** — it is pure Python code reading orchestrator-written files. The only token cost is the supervisor's conceptual annotations during re-entry, which are part of the existing re-entry conversation (DB-16) and not an additional expense. The savings from better-calibrated PLAN cycles (fewer rework loops due to decomposition precedents, fewer wasted quality gate invocations due to known convergence patterns) are pure gain.

### Extends DB-07: Milestone Ownership

memory.md ownership: `[orchestrator writes, supervisor annotates]`. This is a new dual-ownership pattern — the orchestrator creates and updates structural entries, the supervisor adds conceptual entries. The two write paths are distinguishable by content type (structural types like `cost-calibration` are orchestrator-authored; conceptual types like `quality-gate` with judgment about when to skip are supervisor-authored).

### Extends DB-08: File Schemas

memory.md schema defined in D1 above. Validation tier: narrative (DB-12) — required sections checked (`# Operational Memory`, `## Patterns`), entry structure validated (required fields: `type`, `observed`, `reinforced`, `last_active`).

## Golden Context File Tree Update

```
.clou/
├── ...
├── memory.md                          # [orchestrator writes, supervisor annotates]
├── understanding.md                    # [supervisor writes] — user-intent memory (DB-13)
└── ...
```

memory.md joins understanding.md in the top-level `.clou/` directory — both are cross-milestone memory artifacts, both read by the supervisor at orient.

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-03 (Context)** | memory.md added to PLAN read set and supervisor orient. Archival removes episodic files from completed milestones. |
| **DB-06 (Economics)** | Zero LLM token cost for structural consolidation. Savings from calibrated PLAN cycles. |
| **DB-07 (Ownership)** | memory.md dual ownership: orchestrator writes, supervisor annotates. |
| **DB-08 (Schemas)** | memory.md entry schema: type, observed, reinforced, last_active, invalidated, description. metrics.md extended with quality gate, rework, and escalation sections. |
| **DB-09 (Verification)** | No direct impact — verification artifacts are episodic, archived after consolidation. |
| **DB-12 (Validation)** | memory.md is narrative tier. The no-LLM-parsing constraint strengthens DB-12's tiering: narrative files are for humans and LLM agents to read, not for the orchestrator to parse. |
| **DB-13 (Disposition)** | understanding.md (user-intent) vs. memory.md (operational) separation confirmed. Supervisor annotates memory.md during re-entry using same bidirectional grounding pattern. |
| **DB-15 (Tensions)** | D3 compaction (intra-milestone) complemented by consolidation (cross-milestone). |
| **DB-16 (Autonomous Cycles)** | Re-entry conversation is the natural moment for supervisor memory.md annotations. Structural patterns from orchestrator provide evidence base for supervisor's conceptual conclusions. |

## Design Constraint: No LLM Output Parsing

Consolidation reads exclusively from:
1. **`metrics.md`** — written by `clou.telemetry.write_milestone_summary()`, which the orchestrator controls
2. **`compose.py`** — validated via AST parsing (DB-02, DB-12 tier: AST)
3. **`memory.md`** itself — to merge/reinforce existing entries

It does NOT read or parse:
- `decisions.md` (coordinator-written, narrative tier)
- `assessment.md` (assessor-written, narrative tier)
- `execution.md` (agent-team-written, narrative tier)
- Escalation files (coordinator-written, narrative tier — classification is captured in telemetry events instead)

This constraint follows from §9 (LLMs cannot self-verify) and DB-12 (narrative files are form-checked only). If consolidation parsed LLM output, it would inherit the LLM's structural unreliability. By reading only orchestrator-written data, consolidation is deterministic and testable.

Semantic patterns that require understanding LLM-written prose are the supervisor's responsibility — provided during re-entry conversation, grounded in user feedback, following the same bidirectional validation pattern as DB-13.

## Implementation Scope

| Component | Change |
|---|---|
| `telemetry.py` | Three new event types: `quality_gate.result`, `cycle.rework`, `escalation.created`. Emitted from orchestrator control flow. |
| `telemetry.py` | `write_milestone_summary()` extended to aggregate new events into metrics.md sections. |
| `recovery.py` | `consolidate_milestone()` function: reads metrics.md + compose.py AST, writes/updates memory.md, archives episodic files. |
| `orchestrator.py` | Call `consolidate_milestone()` after `write_milestone_summary()`. Emit new telemetry events at cycle boundary and escalation handling. |
| `orchestrator.py` | Add memory.md to PLAN cycle read set in `determine_next_cycle()`. |
| `hooks.py` | Write permission for memory.md (orchestrator + supervisor). |
| `_prompts/supervisor.md` | memory.md added to orient read set. Annotation guidance during re-entry. |
| `_prompts/coordinator-plan.md` | Guidance to read memory.md for decomposition and cost calibration. |
| `golden-context.md` | Already updated: memory.md in file tree, ownership table, read sets. |
| Tests | Telemetry event emission, metrics.md aggregation, consolidation extraction, forgetting decay, archival, scored retrieval. |
