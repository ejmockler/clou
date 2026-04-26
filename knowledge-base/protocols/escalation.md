# Escalation Protocol

## Principle

Escalations are **agent-to-agent** engineered artifacts, not cries for help and not user-facing decision cards. Every escalation includes analysis, options with tradeoffs, and a recommendation. The coordinator frames the decision; the supervisor makes the call. User-facing decisions flow through a different channel (`ask_user_mcp`). See `memory/project_escalations_are_agent_to_agent.md`.

## Canonical Schema

Escalations are written **only** via the MCP tool `mcp__clou_coordinator__clou_file_escalation` (coordinator-tier) or the in-process recovery paths in `clou/recovery_escalation.py` (system-authored). Direct `Write`/`Edit`/`MultiEdit` to `milestones/*/escalations/*.md` is denied by the PreToolUse hook — the tool is the only write path.

The canonical format on disk matches what `clou/escalation.py:render_escalation` emits and what `clou/escalation.py:parse_escalation` expects. The authoritative schema lives in `clou/escalation.py:EscalationForm` (frozen dataclass); the bullet list below tracks that module, not the reverse.

Each escalation is a file at `milestones/<milestone-name>/escalations/<timestamp>-<slug>.md`:

```markdown
# Escalation: <title>

**Classification:** <blocking | degraded | informational | architectural | …>
**Filed:** <YYYY-MM-DD>

## Context
<What the coordinator was doing when this arose. Which phase, which task,
what the agent team was attempting.>

## Issue
<Precise description of the problem. Not "something went wrong" but exactly
what the conflict, ambiguity, or blocker is.>

## Evidence
<Brutalist feedback, code references, conflicting requirements, error output —
whatever prompted this escalation. Concrete, not interpretive.>

## Options
1. **Option A label** --- tradeoffs — what you gain, what you lose, what costs.
2. **Option B label** --- tradeoffs.
3. **Option C label (coordinator's recommendation)** --- tradeoffs.

## Recommendation
<What the coordinator would do if it had the authority, and why.
References to requirements, project.md, or prior decisions that support this.>

## Disposition
status: [open | investigating | deferred | resolved | overridden]
resolved_by: [supervisor | coordinator_timeout]
resolution: <what was decided and why>
```

### Field notes

- **Classification** is a *single-line preamble string* (`**Classification:** <value>`), not a `## Classification` section. The `EscalationForm.classification` field accepts an open set of strings; the tuple `clou.escalation.VALID_CLASSIFICATIONS = ("blocking", "degraded", "informational", "architectural", "trajectory_halt")` is advisory, not enforced. Callers MUST NOT branch on `classification == "blocking"` for routing; use explicit status fields (`disposition_status`) for control flow.
  - **Narrow exception (M49b):** the `clou.escalation.ENGINE_GATED_CLASSIFICATIONS` frozenset whitelists classifications that engine control flow IS permitted to branch on. Membership is code-written in the MCP tool handler (not LLM-authored), so it's immune to the drift the general "don't-branch" contract guards against. Currently contains `{"trajectory_halt"}` — the engine's pre-dispatch halt gate reads this constant rather than a hardcoded string literal, so additional engine-gated classifications join the frozenset rather than needing new typed fields on `EscalationForm`.
- **Filed** is a date string in the preamble, not a section.
- **Options** are numbered `1. **Label** --- description` items in `## Options`. The parser also tolerates legacy layouts (`### Option A: Label`, `### (a) Label`) but new authorship must use the bold-numbered form.
- **Disposition** is the only multi-key section. The `status` key is the authoritative control signal: `open` / `investigating` / `deferred` keep the escalation visible via `clou_status`, while `resolved` / `overridden` retire it to historical record.

### Legacy tolerance

The parser (`parse_escalation`) additionally accepts older in-tree layouts so files dated before 41-escalation-remolding continue to populate fields without mass rewrite:

- `## Analysis` / `## Problem` / `## Finding` fall back to `issue` / `evidence`.
- `## Severity` (body) falls back to `classification` (remember: open-set string, not enum).
- `## Fix` / `## Target content` fall back to `recommendation`.

**New escalations** should use the canonical layout; legacy layouts exist for read-only compatibility and are not re-emitted by `render_escalation`.

## Classification Types

### `ambiguity`
A requirement or spec is unclear and the coordinator can't determine the intended behavior from available context.

**Example:** "Requirements say 'support authentication' but don't specify OAuth vs. email/password vs. both."

### `conflict`
Two requirements, constraints, or existing code patterns are incompatible. Meeting one means violating the other.

**Example:** "Requirement says 'use Server Components' but the charting library requires client-side rendering."

### `scope_change`
The coordinator has discovered that the milestone is larger, different, or requires work outside the specified scope.

**Example:** "Implementing the payment flow requires a new database table and migration not mentioned in requirements."

### `blocked`
Technical blocker that the coordinator cannot resolve with available tools and authority.

**Example:** "CI pipeline is failing on a pre-existing test unrelated to our changes. Cannot merge or verify."

### `authority_exceeded`
The coordinator has identified a solution but it falls outside its delegated authority.

**Example:** "Best fix requires changing the public API contract, which affects other milestones."

### `credential_request`
A third-party service needs human setup — credentials, CLI authentication, sandbox account creation.

**Example:** "Stripe integration requires test mode API keys. Setup guide written to `services/stripe/setup.md`."

### `agent_team_crash`
An agent team member died unexpectedly during execution. The orchestrator killed remaining teammates and preserved `execution.md`. Requires supervisor awareness and user notification.

**Example:** "Teammate implementing auth service crashed (context exhaustion). 2/4 tasks completed in execution.md. Remaining: session management, password reset."

### `infrastructure_failure`
Essential Clou infrastructure (Playwright MCP, other required services) is unavailable. The coordinator cannot proceed without it.

**Note:** Brutalist MCP unavailability is **not** an infrastructure_failure escalation. The assessor automatically falls back to degraded internal review (spawning subagents across implementation verticals). The coordinator proceeds with `status: degraded` findings. See [brutalist-mcp.md](../integration/brutalist-mcp.md) for the degraded fallback design.

**Example:** "Playwright MCP returned connection error during VERIFY cycle. Cannot perform perceptual verification."

### `validation_failure`
The orchestrator detected malformed golden context after 3 consecutive retry attempts for the same cycle. Structural validation failed repeatedly.

**Example:** "active/coordinator.md missing required ## Phase Status section after 3 cycle retries."

## Severity Semantics

### `blocking`
Coordinator halts progress on the affected work. The supervisor must resolve before the coordinator can continue on this branch. Other independent work may continue.

**Supervisor response required:** Yes, before the coordinator can proceed on affected tasks.

### `degraded`
Coordinator parks the affected branch and continues with independent work. The supervisor should resolve soon but the coordinator isn't fully stopped.

**Supervisor response required:** Yes, but the coordinator continues on other work in the meantime.

**Most common for:** `credential_request` escalations where the coordinator can proceed with non-service-dependent tasks.

### `advisory`
The coordinator already made a call within its delegated authority but wants the supervisor informed. No response needed.

**Supervisor response required:** No. The disposition section is pre-filled by the coordinator.

**Example:** "Brutalist flagged our use of `any` types in three places. Overrode because these are FFI boundaries with no type information available. Logging for awareness."

## Supervisor Handling

The supervisor checks for open escalations at the top of its loop:

1. **Scan** `milestones/*/escalations/` for files where `status: open`
2. **Triage** by severity: blocking first, degraded second, advisory noted
3. **For blocking escalations:**
   - Read the full escalation
   - Evaluate options against project goals and constraints
   - May consult the user for input on the decision
   - Write disposition: status, resolved_by, resolution
4. **For degraded escalations:**
   - Same process but lower urgency
   - May batch-resolve several degraded escalations
5. **For advisory escalations:**
   - Acknowledge awareness
   - Flag for review if the pattern seems concerning

## Engine-Gated Escalations (M49b)

A subset of classifications — currently `{trajectory_halt}`, defined by `clou.escalation.ENGINE_GATED_CLASSIFICATIONS` — trigger the coordinator's pre-dispatch halt gate. When such an escalation has an open disposition, the engine refuses to run any cycle for that milestone until the disposition reaches a terminal status.

**Wedge semantics (deliberate).** "Open" for the engine gate means *any* status in `OPEN_DISPOSITION_STATUSES = (open, investigating, deferred)`. `deferred` is included on purpose: bypassing it would let the engine dispatch a halt the supervisor explicitly parked. There is no "park this halt without freezing the milestone" affordance — engine-gated halts are meant to be answered, not shelved.

**Supervisor UX contract.** Because the wedge is real and the engine offers no defer-and-continue path, the supervisor's disposition flow for engine-gated classifications MUST always terminate in `resolved` or `overridden` before the supervisor exits the disposition loop. Intermediate states (`investigating`, `deferred`) are valid mid-flow but invalid as final dispositions for this class. The supervisor UX should not present a "save and exit" affordance for engine-gated escalations until the disposition is terminal.

**Operator escape hatch.** If an operator needs the engine to resume despite an open engine-gated halt (e.g. for debugging), the path is: rewrite the disposition file to `resolved` with a resolution note explaining the override AND rewrite the checkpoint's `next_step` away from `HALTED`. Both edits are required — the engine gate would otherwise re-fire on the next iteration, and `determine_next_cycle` raises `RuntimeError` (M49b C1) on `next_step=HALTED` to prevent silent coercion.

**Adding a new engine-gated classification.** Code-write the literal in the relevant MCP tool handler (so it's immune to LLM drift), add it to `ENGINE_GATED_CLASSIFICATIONS`, and ensure its supervisor disposition path also terminates in `resolved`/`overridden`. The `tests/test_halted_checkpoint.py::test_no_classification_routing_branch_outside_allowlist` AST scan enforces that callers do not branch on the literal directly.

## Escalation Flow

```
Coordinator encounters issue
    ↓
Coordinator analyzes: within delegated authority?
    ├─ Yes → decide, log in decisions.md (no escalation)
    └─ No:
        ├─ Cross-cutting (belongs to future milestone)?
        │     → file proposal via `clou_propose_milestone` (default,
        │       Stream C / zero-escalations)
        │       ↓
        │   Supervisor reads via `clou_list_proposals` on orient
        │       ↓
        │   Supervisor accepts (→ `clou_create_milestone`), rejects,
        │   or defers via `clou_dispose_proposal`
        │
        └─ True in-milestone blocker needing human decision?
              → write escalation file (fallback, not default)
              ↓
         Supervisor detects open escalation
              ↓
         Supervisor evaluates options + recommendation
              ↓
         Supervisor writes disposition
              ↓
         Coordinator reads disposition on next cycle
              ↓
         Coordinator acts on resolution
```

## Credential Request Flow (Special Case)

Credential requests have a lifecycle distinct from other escalations:

```
Coordinator discovers service dependency
    ↓
Coordinator checks services/<name>/status.md
    ├─ Configured → proceed
    └─ Not found/unconfigured →
         ↓
    Write services/<name>/setup.md  (precise instructions)
    Write services/<name>/.env.example  (expected vars)
    File credential_request escalation (degraded)
    Park dependent tasks, continue independent work
         ↓
    User follows setup.md
    User provides credentials
         ↓
    Coordinator runs verification command from setup.md
    ├─ Passes → update status.md, unblock tasks
    └─ Fails → re-escalate with specific error
```

## Naming Convention

Escalation files: `<timestamp>-<slug>.md`
- Timestamp: `YYYYMMDD-HHMMSS` (UTC)
- Slug: lowercase, hyphenated, descriptive (e.g., `stripe-credentials-needed`, `auth-pattern-conflict`)

Example: `20260319-041500-stripe-credentials-needed.md`

## Escalation vs. Decision

The boundary between an escalation and a `decisions.md` entry is the coordinator's delegated authority:

| Situation | Action | Logged in |
|---|---|---|
| Brutalist flags a code style issue → coordinator overrides | Decision | `decisions.md` |
| Brutalist flags a security vulnerability → coordinator fixes | Decision | `decisions.md` |
| Brutalist flags a security vulnerability → fix requires API change | Escalation | `escalations/` |
| Requirements are ambiguous → coordinator picks reasonable interpretation | Decision | `decisions.md` |
| Requirements conflict with each other | Escalation | `escalations/` |
| Service needs credentials | Escalation | `escalations/` |
| Implementation approach differs from what supervisor might expect | Decision (with advisory escalation) | Both |
| Agent team member crashes during execution | Escalation | `escalations/` |
| Brutalist MCP is unavailable | Escalation | `escalations/` |
| Golden context validation fails 3 times | Escalation | `escalations/` |
| Coordinator hits 20-cycle limit | Escalation | `escalations/` |
