# DB-12: Golden Context Validation Tiers

**Status:** DECIDED
**Decided:** 2026-03-25
**Severity:** Medium — under-validation risks silent state corruption; over-validation constrains artifact evolution
**Question:** What level of structural validation is appropriate for each golden context artifact, and how should validation tiers map to error recovery?

## Context

The brutalist debate (2026-03-25) identified a gap: `compose.py` gets AST validation (graph.py checks syntax, completeness, acyclicity, type compatibility, convergence), but all other golden context files get regex heading checks (validation.py confirms section headers exist with string matching). The architecture claims golden context is the sole compaction mechanism and source of truth (DB-03, Design Principle §8), but the structural guarantees are tiered without that tiering being explicit or justified.

The existing validation module's own docstring states: "Checks form, not content — the orchestrator doesn't judge whether decisions are good, only whether files are well-formed" (validation.py:6-7). The question is whether "well-formed" means the right thing for each artifact type.

## Design Constraints from Research

- **§9 (Intermediate Tokens Are Not Reasoning):** Chain-of-thought traces are statistical scaffolding, not interpretable reasoning. This means decisions.md — the coordinator's reasoning log — cannot be validated for correctness by inspection. Form-only validation is appropriate for narrative artifacts whose content quality is assessed by the quality gate, not the orchestrator.
- **§9 (LLM-Modulo):** External verification is non-negotiable for plan structure. compose.py validation via AST IS the LLM-Modulo pattern applied to plans. This level of validation is justified by the research.
- **§1 (Context Degradation):** Golden context files that drive the cycle loop (active/coordinator.md, status.md) must be machine-parseable. If checkpoint parsing fails, the orchestrator can't determine what cycle to run next. This is a different failure mode from malformed narrative files.
- **§10 (Multi-Agent Failures, Otto's Gap 2):** Multiple state representations that diverge under stress cause coordination failures. The checkpoint files are the cycle loop's source of truth. Their validation should be stronger than form checking.

## Decisions

### D1: Three Validation Tiers

Golden context artifacts fall into three tiers based on what consumes them and what breaks if they're wrong:

| Tier | Artifacts | Consumer | Failure mode | Validation |
|---|---|---|---|---|
| **Structural** | `compose.py` | Orchestrator (AST parser), coordinator dispatch | Invalid plan → wrong agent dispatch, cycles, type mismatches | AST: syntax, completeness, acyclicity, type compatibility, convergence |
| **Checkpoint** | `active/coordinator.md`, `status.md` | Orchestrator (`determine_next_cycle`), coordinator (cycle entry) | Wrong cycle type, lost phase position, repeated/skipped work | Key-value parsing: required keys present, values from allowed sets, phase references resolve to existing directories |
| **Narrative** | `execution.md`, `decisions.md`, `assessment.md`, `handoff.md`, `phase.md`, `metrics.md` | Agents (read during cycles), quality gates (assess content), humans (legibility surface) | Agent reads malformed input → degraded but recoverable (agent adapts or ASSESS catches) | Form: required section headers present, status values from valid set |

### D2: Checkpoint Validation — Required vs Optional Keys

`active/coordinator.md` parsed by regex `key: value` extraction (recovery.py). Validation is strict for control-flow keys, tolerant for fields the parser can safely default.

**Required keys** (ERROR — orchestrator cannot route without them):
- `cycle` — non-negative integer
- `next_step` — value from allowed enum (PLAN, EXECUTE, EXECUTE (rework), EXECUTE (additional verification), ASSESS, VERIFY, EXIT, COMPLETE)

**Optional keys** (WARNING — `parse_checkpoint()` defaults them safely):
- `step` — defaults to PLAN
- `current_phase` — defaults to "". Alias accepted: `phase`.
- `phases_completed` — defaults to 0
- `phases_total` — defaults to 0

**Self-heal normalisation:** The self-heal pipeline resolves aliases (`phase` → `current_phase`, `current_cycle` → `cycle`) and injects missing optional fields before validation retry. This prevents escalation from cosmetic format variation.

**Rationale:** The original D2 required all 6 keys as ERROR. In practice, agents write checkpoints from fresh sessions where only the PLAN cycle prompt showed the full format. Non-PLAN prompts mentioned only `next_step`. This caused repeated blocking escalations for missing optional fields that the parser handled gracefully. The required set was reduced to the two keys that actually drive `determine_next_cycle()` control flow. All cycle prompts now include the full 6-field format.

Values validated against allowed enums (cycle types, step names). Parse failure triggers the existing DB-05 recovery path (revert golden context, retry cycle, escalate after 3 failures).

### D3: Narrative Validation Stays Form-Only

Narrative artifacts (execution.md, decisions.md, assessment.md) are consumed by agents and quality gates, not by the orchestrator's control flow. Their content quality is assessed by the quality gate (ASSESS cycle), not by structural validation. Strengthening their validation would constrain their format without preventing the failure that matters (bad content), which only the quality gate can catch.

The form checks (required sections, valid status values) remain as specified in DB-08. These catch gross malformation (agent wrote to wrong file, output is empty, format is completely wrong) without trying to validate content.

### D4: Tier Boundaries Are Stable

The tier assignment follows from what consumes the artifact:
- Orchestrator control flow → Structural or Checkpoint (machine-parseable)
- Agent + quality gate → Narrative (form-checked, content-assessed by gate)

If a new golden context artifact is added, its tier is determined by whether the orchestrator's control flow depends on it. This prevents tier proliferation.

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-05 (Error Recovery)** | Decision 5 (structural validation at cycle boundaries) now has tiered validation. Checkpoint parse failure triggers the same revert-retry-escalate path. Narrative form failure also triggers it (unchanged). |
| **DB-08 (File Schemas)** | Schemas are unchanged. Validation tiers determine how strictly schemas are enforced, not what schemas contain. |
| **DB-03 (Context Lifecycle)** | `determine_next_cycle()` reads checkpoint tier artifacts. Strengthened parsing reduces the risk of control-flow corruption from malformed checkpoints. |

## Implementation

| Component | Change |
|---|---|
| `validation.py` | Add `validate_checkpoint()` function with strict key-value parsing, enum validation, phase directory resolution. Existing `validate_golden_context()` calls it for checkpoint files, existing form checks for narrative files. |
| `recovery.py` | `parse_checkpoint()` uses the new strict parser. Parse failure returns a sentinel that `determine_next_cycle()` handles as a validation failure (existing recovery path). |
| `tests/test_validation.py` | Add tests for checkpoint validation: missing keys, invalid enum values, nonexistent phase references. |
