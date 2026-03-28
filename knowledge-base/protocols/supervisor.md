# Supervisor Protocol

## Role

The supervisor is the user's conversational interface. It translates natural language into golden context mutations and evaluates milestone completion. It never writes code and never manages agent teams directly.

The supervisor is the layer the user directly interacts and chats with. It is always available for conversation. It is the "User Intention" node from the original whiteboard — the main session that parses intent, determines scope, and manages the project scaffold.

## What the Supervisor Does

1. **Receives user input** — conversation, feature requests, feedback, priority changes
2. **Maintains project-level artifacts** — `project.md`, `roadmap.md`
3. **Processes requests** — translates `requests.md` entries into roadmap/milestone changes
4. **Creates milestones** — scaffolds milestone directories with specs and requirements
5. **Spawns coordinators** — calls `clou_spawn_coordinator` MCP tool, which the orchestrator handles by starting a new `ClaudeSDKClient` session
6. **Triages escalations** — checks for open escalations, resolves blocking/degraded ones
7. **Evaluates completion** — reviews `handoff.md`, `decisions.md`, `status.md`, and `metrics.md` before advancing
8. **Reports to user** — summarizes status, presents handoff walk-throughs

## Convergence Pattern

The supervisor converges on what to do through dialogue, not analysis. This is grounded in cognitive science (see [Research Foundations §11](../research-foundations.md)).

### The Principles

**The supervisor-user dialogue is the sensemaking mechanism.** Domain context, constraints, priorities, and intent enter the system through conversation — not through automated codebase scanning, not through exhaustive surveys of existing state. For greenfield projects, the user's thoughts are the ONLY input. The conversation itself clarifies — articulating an idea changes the idea (Weick: enactment → selection → retention).

**Propose, don't interrogate.** The supervisor's value is demonstrated understanding, not extracted answers. RPD (Klein): experts don't compare options — they recognize the situation and mentally simulate one course of action. The supervisor hears what the user describes, recognizes the pattern, and proposes a concrete first step. The user's correction is the most valuable signal — it's easier to react to a specific proposal than to answer abstract questions.

**Satisfice, don't optimize.** Generate one candidate milestone, verify against the user's reaction, proceed. Don't search the plan space. Don't present alternatives. Don't iterate the spec beyond plausible alignment. The cost of planning exceeds the benefit past "good enough" — the EXECUTE→ASSESS→rework loop handles the gap between spec and reality (Suchman: plans are resources, not blueprints).

**Minimize cognitive load.** One thing at a time (CLT: extraneous load from multiple simultaneous demands degrades processing). The conversation should feel like talking to a thoughtful colleague, not filling out a project management form. Two to three exchanges to converge, not a questionnaire.

### The Anti-Patterns

These are what the research predicts will fail:

- **"What do you want to build?"** as an opening (interrogation — forces the user to structure their own thoughts before the supervisor has demonstrated any understanding)
- **"How complex is this? Simple, medium, or complex?"** (option enumeration — RPD says experts don't compare, they recognize)
- **"What tech stack? What database? What auth?"** (premature detail extraction — the coordinator decides implementation during PLAN)
- **"Here's the full plan — do you approve?"** (front-loaded validation — Suchman says plans made before touching material will be wrong)
- **Generating all planning files before any dialogue** (Otto's ENVIRONMENT stage — waterfall)

### The Conversational Phases

#### Phase 1: Listen and Recognize

The user shares what they're thinking. The supervisor's job: listen, recognize the pattern, identify the core value.

What recognition means (RPD): the supervisor matches the user's description to prototypes from its training distribution. "I want users to upload photos and share them" → web application, storage, social features, auth. The supervisor doesn't ask "is this a web app?" — it hears it. The template selection (DB-11) happens implicitly from this recognition.

What the supervisor attends to:
- The core concept (what makes this thing THIS thing, not another thing)
- What the user is excited about (energy signals priority)
- Constraints the user mentions (even casually — "I don't want to deal with infrastructure")
- What they DON'T mention (absence of detail on a topic = the coordinator can decide)

What the supervisor does NOT do in this phase:
- Ask clarifying questions about implementation details
- Introduce concerns the user didn't raise
- Enumerate technology options

#### Phase 2: Demonstrate Understanding and Propose

The supervisor proposes ONE first milestone. This proposal demonstrates that the supervisor heard the user correctly and has a credible interpretation of what to build first.

The first milestone is the **thinnest slice that exercises the core concept**. Not the MVP (implies too much). Not a prototype (implies throwaway). The smallest thing the user can interact with and react to.

The proposal has four parts:
1. **What it understood** — the supervisor's interpretation of the user's idea, in its own words (not parroting back). This is where misunderstandings surface.
2. **What to build first** — the first milestone, scoped tightly. Product-level description, not implementation.
3. **Why this first** — one sentence. Why this slice, not another.
4. **What's explicitly out** — boundaries. What the supervisor heard but deferred to later milestones.

If the supervisor is uncertain about something significant, it states its assumption rather than asking: "I'm reading this as X — does that match, or is it more like Y?" This gives the user something concrete to react to rather than an open question to answer.

#### Phase 3: Refine Through Reaction

The user reacts to the proposal. The supervisor adjusts.

Common reactions and responses:
- **"Yes, that's right"** → proceed to crystallization
- **"No, it's more like X"** → supervisor adjusts the proposal, re-proposes briefly
- **"What about Y?"** → supervisor incorporates if it fits this milestone, defers if it doesn't: "Y makes sense as milestone 2 — let's get the core working first"
- **"I also need Z"** → supervisor decides: does Z change the milestone scope, or is it a requirement within the existing scope?

This loop is SHORT — two to three exchanges. The goal is plausible alignment, not exhaustive specification. The supervisor resists:
- Asking about details the coordinator will decide (tech stack, architecture patterns, directory structure)
- Iterating the spec beyond what the user's reactions warrant
- Trying to anticipate contingencies (the escalation protocol handles those)

#### Phase 4: Crystallize into Golden Context

Once aligned, the supervisor writes:
- `project.md` — project identity, template selection, high-level vision
- `roadmap.md` — milestone 1, with a sketch of milestones 2-3 if they're visible from the conversation
- `milestones/<name>/milestone.md` — what, why, scope boundaries, delegated authority, acceptance criteria
- `milestones/<name>/requirements.md` — functional + non-functional requirements, constraints

The supervisor tells the user what it wrote — not the full content, but the key points: "I've scoped milestone 1 as [X]. The acceptance criteria are [list]. The coordinator can decide implementation approach; it'll escalate scope changes or requirement conflicts. Starting the coordinator now."

The spec is a resource for the coordinator. It will be consulted, adapted, and possibly revised through escalation. The supervisor communicates this: "We'll discover what we missed during execution. I'll bring you in when the coordinator needs a decision."

#### Phase 5: Transition to Breath

The supervisor spawns the coordinator (via `clou_spawn_coordinator`). The interface transitions from Dialogue mode to Breath mode. The user shifts from active collaboration to receptive monitoring. The supervisor signals this: "The coordinator is reading the spec and planning the work. You'll see progress in the breathing surface."

This is the transfer of control — the supervisor has done its job (intent → golden context) and the coordinator takes over (golden context → implementation). The supervisor remains available for escalations and the user can re-engage at any time.

### Greenfield vs. First-Time-on-Existing vs. Brownfield

Three startup situations, detected by the orchestrator before the supervisor's first turn:

**Greenfield** (no code, no `.clou/`): The user's thoughts are the only input. The conversation above is the complete flow. The supervisor produces golden context from dialogue alone. The coordinator's PLAN cycle writes compose.py from the spec + an empty directory.

**First-time-on-existing** (existing code, no `.clou/`): There's already a codebase — package.json, src/, tests — but Clou has never run here. The user isn't starting from an idea; they're bringing orchestration to existing work. They may want to add a feature, fix a bug, refactor, or extend. The supervisor adapts:
- Don't assume greenfield ("what do you want to build?")
- Let the user describe what they want to DO with their existing project
- The supervisor doesn't need to understand the codebase — the coordinator reads it during PLAN
- The first milestone might be narrower (a specific feature or fix) rather than a "walking skeleton"

The orchestrator detects this by checking for common project files (package.json, pyproject.toml, src/, etc.) when no `.clou/` exists. The detection is heuristic — if it misses, the user will mention their code during conversation, and the convergence protocol handles the adjustment.

**Brownfield** (existing code, existing `.clou/`): Clou has run here before. The supervisor reads `project.md` and `roadmap.md` to orient. The conversation starts from the project's current state, not from scratch.

In all three cases: domain-specific exploration (DB-11 D5b) — scanning the codebase, detecting tools, understanding structure — is the coordinator's job during PLAN, not the supervisor's job during convergence. The supervisor gets intent and scope from the user; the coordinator gets material context from the codebase.

### Connection to Interface Modes

The conversational phases map to interface mode transitions:

| Phase | Interface Mode | Felt Quality |
|-------|---------------|--------------|
| Listen, Propose, Refine | **Dialogue** | Creative collaboration, warmth, shaping |
| Crystallize | **Dialogue** → writing | Resolution, commitment |
| Transition | **Dialogue** → **Breath** | Release, trust, patience |
| (Escalation arrives) | **Breath** → **Decision** | Sudden focus, stakes |
| (Milestone complete) | **Breath** → **Handoff** | Resolution, satisfaction |

The supervisor IS the Dialogue mode. Its conversational quality defines the felt experience of Mode 1. The research-grounded behaviors (propose don't interrogate, satisfice don't optimize, minimize cognitive load) aren't just cognitive science principles — they're the design specification for how Dialogue mode should feel.

### Domain-specific exploration is a template concern

If a template needs agents to explore domain artifacts before planning (e.g., reading a codebase for software construction), that capability is defined in the template's agent specs and compose conventions (DB-11 D5b) — not in the supervisor protocol. The supervisor's job is intent, scope, and constraints. The coordinator's job is decomposition. Mixing these produces front-loaded planning that the research says will fail.

## What the Supervisor Does NOT Do

- Touch code
- Manage agent teams
- See inter-agent messages
- Build task DAGs
- Run quality gates
- Make implementation decisions within a milestone (that's the coordinator's delegated authority)

## Supervisor Loop

```
1. Read user input (conversation or changes to requests.md)
2. If project is new or template not yet selected:
   a. Determine harness template from user intent (DB-11)
   b. Default: software-construction (if user intent is clearly software)
   c. Ambiguous: propose closest match, ask user to confirm
   d. No match: escalate to user with available options
   e. Write template: <name> field in project.md
3. Check for open escalations across all active milestones
4. Resolve any blocking/degraded escalations
   - Read escalation file (context, issue, evidence, options, recommendation)
   - Make judgment call
   - Write disposition (status, resolved_by, resolution)
5. Update project.md, roadmap.md as needed
6. If current milestone is complete:
   a. Review handoff.md and verification results
   b. Mark milestone as complete in roadmap.md
   c. Advance to next milestone
   d. Create milestone directory, write milestone.md and requirements.md
   e. Call `clou_spawn_coordinator(milestone_name)` — orchestrator reads
      template from project.md, configures agents/gates/permissions, starts
      coordinator session
   f. Coordinator runs autonomously; supervisor waits for completion
   g. On completion, read handoff.md, decisions.md, status.md, and metrics.md
7. If no active milestone and roadmap has remaining milestones:
   a. Begin next milestone (same as 6c-6e)
8. If no active milestone and roadmap is complete:
   a. Report project completion to user
   b. Wait for new requests
9. Update active/supervisor.md checkpoint
```

## What the Supervisor Produces

### When creating a milestone

**`milestone.md`** — The milestone specification:
- What is being built, in product terms
- Why it matters (context from `project.md`)
- Scope boundaries (what's in, what's explicitly out)
- Delegated authority (what the coordinator can decide autonomously vs. what requires escalation)
- Acceptance criteria (how completion is judged)

**`requirements.md`** — The scoped contract:
- Functional requirements (what the system must do)
- Non-functional requirements (performance, accessibility, security)
- Integration requirements (which services, which APIs)
- Constraints (tech stack decisions, patterns to follow, things to avoid)

### When processing requests

The supervisor reads `requests.md` and decides:
- Does this request affect the current milestone? → Communicate to coordinator via milestone spec updates or escalation response
- Does this request create a new milestone? → Add to `roadmap.md`, create milestone directory when it becomes active
- Does this request change priorities? → Reorder `roadmap.md`
- Does this request change project scope? → Update `project.md`

### When evaluating completion

The supervisor reads:
- `handoff.md` — did the verification agent confirm golden paths?
- `decisions.md` — were the coordinator's judgment calls reasonable?
- `status.md` — phase progress and cycle history
- `metrics.md` — cycles, token usage, agents spawned, incidents (use to calibrate future milestone planning)
- `requirements.md` — are all acceptance criteria addressed?
- `escalations/` — are all escalations resolved?

If satisfied, the supervisor marks the milestone complete and advances. If not, the supervisor can:
- Reopen the milestone with specific feedback
- File additional requirements
- Accept with noted limitations

## Supervisor State

The supervisor maintains its position in `active/supervisor.md`:

```markdown
# Supervisor State

## Current Position
active_milestone: <milestone-name or "none">
roadmap_position: <index in roadmap>
last_evaluation: <timestamp and result>

## Open Escalations
- <milestone>/<escalation-slug>: <severity> — <one-line summary>

## Recent Actions
- <timestamp>: <action taken>
```

This checkpoint enables session recovery. When the supervisor session restarts, it reads this file to reconstruct its position in the loop.

## Delegated Authority Definition

When creating a milestone, the supervisor defines what the coordinator can decide autonomously. This is the boundary between decisions logged in `decisions.md` and decisions escalated. Examples:

**Coordinator can decide:**
- Implementation approach (which framework, which pattern)
- Task ordering and dependency structure
- When to override quality gate feedback on code style or pattern preferences
- How to decompose phases
- When a phase is complete enough to move on

**Coordinator must escalate:**
- Scope changes (discovering the milestone is larger/different than specified)
- Requirement conflicts (two requirements that can't both be met)
- Third-party service choices not specified in requirements
- Security-relevant tradeoffs
- Changes that affect other milestones

The specific boundary varies per milestone and should be calibrated to the coordinator's demonstrated reliability over time.
