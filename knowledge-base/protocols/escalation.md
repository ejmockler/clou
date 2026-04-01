# Escalation Protocol

## Principle

Escalations are engineered artifacts, not cries for help. Every escalation includes analysis, options with tradeoffs, and a recommendation. The coordinator does the work of framing decisions. The supervisor makes the call.

## Schema

Each escalation is a file at `milestones/<milestone-name>/escalations/<timestamp>-<slug>.md`:

```markdown
# Escalation: <title>

## Classification
type: [ambiguity | conflict | scope_change | blocked | authority_exceeded | credential_request | agent_team_crash | infrastructure_failure | validation_failure]
severity: [blocking | degraded | advisory]
cycle: <coordinator cycle number>

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
1. <Option A>: <tradeoffs — what you gain, what you lose, what it costs>
2. <Option B>: <tradeoffs>
3. <Option C — coordinator's recommendation>: <tradeoffs>

## Recommendation
<What the coordinator would do if it had the authority, and why.
References to requirements, project.md, or prior decisions that support this.>

## Disposition
status: [open | resolved | overridden]
resolved_by: [supervisor | coordinator_timeout]
resolution: <what was decided and why>
```

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

## Escalation Flow

```
Coordinator encounters issue
    ↓
Coordinator analyzes: within delegated authority?
    ├─ Yes → decide, log in decisions.md (no escalation)
    └─ No → write escalation file
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
