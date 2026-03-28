# Clou Design Principles

These are the invariants that govern all Clou decisions. When the specification is ambiguous or a decision boundary has multiple valid options, these principles are the tiebreakers. These principles are grounded in research — see [Research Foundations](./research-foundations.md) for the empirical basis.

## 1. The golden context is the product

The protocols, the file structure, the ownership boundaries — these are Clou. Clou is not a tool that uses Claude. It configures Claude to be the tool.

**Implication:** Engineering effort goes into the `.clou/` structure, the prompts, the protocols, and the presentation layer that makes the system's state perceivable. The presentation layer is a breathing conversation surface — not a dashboard, not a wrapper CLI, not a secondary UI. See [Interface](./interface.md).

## 2. Mock at the boundary of your control, never within it

Your own services run real. Third-party services use their sandbox infrastructure (Stripe test mode, Auth0 dev tenants, AWS sandbox accounts). Mocks are a last resort for services with no testing mode.

**Implication:** The verification phase starts real servers, runs real migrations, seeds real data. `docker compose up`, not `jest.mock()`. The `services/` protocol exists specifically to handle the credential boundary where automation meets authorization.

## 3. The agent experiences the output before the user does

Verification is not automated checks passing. It's the agent perceiving the output as a user would and handing off a prepared room, not a construction site. For software: the dev environment is left running, user flows are walked. For other domains: the output is experienced through the appropriate verification modalities (DB-09, DB-11).

**Implication:** Every milestone ends with `handoff.md` — a document written by the verification agent that includes what was produced, walk-through steps, and what to look for. The user's first interaction with completed work is structured and prepared.

## 4. Escalations are engineered artifacts, not cries for help

Every escalation includes analysis, options with tradeoffs, and a recommendation. The coordinator does the work of framing decisions. The supervisor makes the call.

**Implication:** The escalation schema is rigid: classification, context, issue, evidence, options, recommendation, disposition. No unstructured "I'm stuck" messages. The coordinator's job is to present a decision, not to delegate thinking upward.

## 5. Ownership boundaries are write boundaries

Each tier writes only to its own files. This prevents coherence problems and makes the audit trail unambiguous.

**Implication:** The ownership annotations on the file tree (`[supervisor writes]`, `[coordinator writes]`, `[agent team writes]`) are enforced, not advisory. No tier writes upward. Reads flow freely downward.

## 6. Start serial, earn parallel

Milestones are sequential by default. Parallel coordinators are a future capability that the architecture supports but does not require. Complexity is added only when the serial model proves insufficient.

**Implication:** The initial implementation treats `roadmap.md` as a linked list. Independence annotations and parallel coordinator spawning are designed for but not built. The architecture must not preclude parallelism, but it must not require it.

## 7. The coordinator is a judgment loop, not a dispatcher

The coordinator evaluates the evaluator (the quality gate), makes calls within its delegated authority, and escalates what it can't resolve. The loop runs until the milestone is genuinely complete, not until the first green signal.

**Implication:** The coordinator's exit condition is conjunctive (ALL criteria must be met). The coordinator has a defined authority boundary (from `milestone.md`) and logs every exercise of judgment in `decisions.md`. It is accountable for its decisions.

## 8. The golden context is the sole compaction mechanism

Each coordinator cycle is a fresh session. The golden context is the only way state transfers between cycles — no reliance on SDK context compression, session persistence, or conversational continuity. The task DAG is expressed as a typed-function call graph in `compose.py`, validated by the orchestrator via AST parsing. `active/coordinator.md` is a pointer that tells the next session where to pick up. `decisions.md`, `execution.md`, and `compose.py` hold the reasoning, results, and plan.

**Implication:** The golden context unifies four roles: human-legibility surface, crash recovery, inter-cycle state transfer, and compaction. Same files, same format, four purposes. This means every piece of state that matters across cycles MUST be externalized to golden context before the session exits. If it's not in golden context, it doesn't survive.

## 9. Minimum viable human involvement, maximum clarity at each touchpoint

The agent maximizes the surface area of what it can do autonomously while being surgically precise about what it needs from the human. Every human touchpoint is a well-defined, well-documented, verifiable action.

**Implication:** The agent writes the setup guide, the verification command, the expected environment variables. The human provides the one thing the agent can't: authorization. Then the agent confirms it worked and moves on. No vague requests. No "please set up Stripe."

## 10. Fail loud, not silent

Crashes, infrastructure failures, and structural validation errors escalate to the supervisor and user. The system does not silently retry operations that may indicate systemic issues. The 20-cycle milestone cap, structural validation at cycle boundaries, and required quality gates as essential infrastructure (not advisory) all enforce this principle: the system surfaces problems rather than masking them.

**Implication:** Agent team crashes exit the coordinator loop and escalate. Required quality gate unavailability is a blocking error (DB-11). Malformed golden context triggers revert-and-retry with explicit error feedback, then escalation after 3 failures. The user always knows when something is wrong.

## 11. The breathing conversation is the interface; the orchestrator is invisible

The user runs Clou. The interface is a single conversation surface that modulates its atmosphere based on the current mode of inhabitation. Four modes: **dialogue** (conversing with the supervisor), **breath** (ambient awareness during autonomous coordinator runs), **decision** (structured escalation resolution), and **handoff** (guided walk-through of completed work). The conversation surface is always the same surface — it changes character, not identity.

The orchestrator manages session lifecycles, hook enforcement, cost tracking. The user never sees it. The presentation layer (Textual + Rich) runs in the same Python process as the orchestrator, consuming SDK message events and translating them into atmospheric changes.

**Implication:** The orchestrator is infrastructure, not product. The presentation layer is a consumer of the orchestrator's event stream, not a wrapper around it. Engineering effort goes into the visual language (`.tcss` stylesheet), the mode transitions, and the atmospheric qualities — not into dashboards, panels, or secondary UIs. See [Interface](./interface.md) for the full design.
