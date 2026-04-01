# DB-16: Autonomous Cycle Model

**Status:** DECIDED
**Decided:** 2026-03-30
**Severity:** High — determines how long automation runs before human re-entry, and what the human's role is at re-entry points
**Question:** How long should the coordinator run autonomously, and what is the human's role between milestones?

## Decision

**The coordinator runs full milestones without human interruption. The human verifies by using outputs — not by approving plans. The supervisor presents completed work via structured choices (DB-13), captures what the human learned through use, and feeds that into the next milestone's scope. Cycle-boundary user-message checks (DB-15 D1) become optional — the default is uninterrupted execution.**

The human's role shifts from plan-approver to output-verifier. The automation runs longer cycles. The human:

- **Verifies** outputs by using them (not by reading plans)
- **Feels out** capabilities of what was built
- **Determines** what to work on next or what needs fixing
- **Re-enters** through structured choices that feed into the next milestone

## Design Rationale

### Plans Made Before Touching Material Will Be Wrong

Suchman (1987) established that plans are resources for situated action, not specifications to be followed. The plan's value is in structuring the initial approach — it must be adapted during execution as the agent encounters the actual environment. Asking the human to approve a plan before execution is asking them to evaluate a document that will change as soon as work begins.

The alternative: let the automation execute the plan, produce working output, and present that output for evaluation. The human evaluates material reality (working code, running servers, observable behavior) rather than projected reality (plan documents). This is where their judgment is most valuable — they can feel whether the output matches their intent in ways that plan review cannot surface.

### RPD Pattern: Simulate Through Use

Klein's Recognition-Primed Decision model shows that experts don't compare options — they recognize situations and mentally simulate their chosen course of action. When the human uses the output, they perform this simulation: "Does this feel right? Does it do what I expected? What's missing?" This is a richer feedback signal than plan approval, which asks the human to simulate execution in their head — a task humans are poor at for complex systems.

### The Re-Entry Point

The human re-enters at milestone boundaries through structured choices (dependent on DB-13: structured questioning). The supervisor presents:

1. What was built (from handoff.md)
2. What was verified (from verification execution.md)
3. What's known to be limited (from handoff.md known limitations)
4. Structured choices: "looks good / needs X fixed / rethink scope for next milestone"

The open-ended option (auto-appended by SDK per DB-13) ensures the human can always redirect. But the structured choices make the common paths fast — most re-entries are "continue" or "fix this specific thing."

### Cycle-Boundary Checks: Optional, Not Mandatory

DB-15 D1 established cycle-boundary user-message checks as the mechanism for human intervention during coordinator execution. This decision makes those checks optional:

- **Default behavior:** Coordinator runs uninterrupted through all cycles until milestone completion, escalation, or cycle limit.
- **/stop still works:** The stop event is checked at cycle boundaries. The human can always halt execution.
- **User messages queue:** Messages typed during coordinator execution queue for the supervisor after coordinator exits. They are not lost — they inform the next milestone's scope.
- **Pause-on-message disabled by default:** The orchestrator's cycle-boundary message check becomes opt-in (harness template flag or user preference), not the default path.

The rationale: interrupting the coordinator to route a user message to the supervisor breaks the session-per-cycle model (DB-03), adds latency, and forces context switching. The human's message will be more useful as input to the next milestone (after they've seen the current milestone's output) than as a mid-execution intervention.

## Supervisor Protocol Changes

The supervisor's role during and after coordinator execution:

1. **Not gating.** The supervisor does not ask "shall I proceed?" at every step.
2. **Not idle.** The supervisor can still be available for questions about prior milestones, project context, or roadmap reasoning.
3. **Presenting outputs.** When the coordinator completes, the supervisor walks the human through what was built, using handoff.md as the structured guide.
4. **Capturing learning.** After the human uses the output, the supervisor captures what they learned: what worked, what didn't, what surprised them. This feeds into understanding.md and shapes the next milestone.
5. **Arc sharpening.** The supervisor uses the human's feedback to sharpen the next milestone sketch (existing step 13 in supervisor protocol). The feedback is richer because the human has used the output, not just read a plan.

## Relationship to Existing Decisions

### Modifies DB-15 D1: Cycle-Boundary Message Check

D1 established cycle-boundary message checking as the primary mechanism for human intervention. This decision makes it optional (default: off). The /stop mechanism remains as the human's emergency brake.

### Extends DB-13: Supervisor Disposition

The re-entry point uses structured choices (ask_user with choices parameter). The supervisor's disposition at re-entry is typically converging (the human has used the output and has specific feedback), but may be exploring (the output revealed new possibilities the human hadn't considered).

### Consistent with DB-03: Session-Per-Cycle

Longer autonomous cycles are more aligned with session-per-cycle than the interrupted model. Each cycle runs to completion. The supervisor's re-entry between milestones is a natural session boundary.

### Consistent with DB-06: Token Economics

Longer cycles without human interruption reduce the overhead of supervisor context switches. The supervisor doesn't need to reload context for mid-milestone conversations that don't change the plan.

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-15 (Tensions)** | D1 (cycle-boundary message check) becomes optional. Default: uninterrupted execution. /stop remains. |
| **DB-13 (Disposition)** | Re-entry uses structured questioning (choices). Supervisor disposition at re-entry inferred from human's feedback. |
| **DB-03 (Context)** | Supervisor lifecycle simplifies: fewer mid-milestone context loads. Checkpoint at milestone boundary is the primary state transfer. |
| **DB-05 (Recovery)** | No change to escalation mechanics — escalations still halt the coordinator and return to supervisor. |

## Implementation Scope

| Component | Change |
|---|---|
| `orchestrator.py` | `_pause_on_message` flag (default: False). Cycle-boundary check skips message routing when False. /stop still checked. |
| `_prompts/supervisor.md` | Updated steps 11-12: present outputs for use, capture learning, structured re-entry via ask_user with choices. |
| `harness.py` | `pause_on_user_message: bool` field on HarnessTemplate (default: False). |
| Protocols: `supervisor.md` | Output presentation protocol: walk through handoff.md, structured re-entry choices, capture what human learned through use. |
