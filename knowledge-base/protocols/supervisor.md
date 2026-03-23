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
7. **Evaluates completion** — reviews `handoff.md` and verification results before advancing
8. **Reports to user** — summarizes status, presents handoff walk-throughs

## What the Supervisor Does NOT Do

- Touch code
- Manage agent teams
- See inter-agent messages
- Build task DAGs
- Run Brutalist
- Make implementation decisions within a milestone (that's the coordinator's delegated authority)

## Supervisor Loop

```
1. Read user input (conversation or changes to requests.md)
2. Check for open escalations across all active milestones
3. Resolve any blocking/degraded escalations
   - Read escalation file (context, issue, evidence, options, recommendation)
   - Make judgment call
   - Write disposition (status, resolved_by, resolution)
4. Update project.md, roadmap.md as needed
5. If current milestone is complete:
   a. Review handoff.md and verification results
   b. Mark milestone as complete in roadmap.md
   c. Advance to next milestone
   d. Create milestone directory, write milestone.md and requirements.md
   e. Call `clou_spawn_coordinator(milestone_name)` — orchestrator starts coordinator session
   f. Coordinator runs autonomously; supervisor waits for completion
   g. On completion, read status.md and handoff.md
6. If no active milestone and roadmap has remaining milestones:
   a. Begin next milestone (same as 5c-5e)
7. If no active milestone and roadmap is complete:
   a. Report project completion to user
   b. Wait for new requests
8. Update active/supervisor.md checkpoint
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
- When to override Brutalist on code style or pattern preferences
- How to decompose phases
- When a phase is complete enough to move on

**Coordinator must escalate:**
- Scope changes (discovering the milestone is larger/different than specified)
- Requirement conflicts (two requirements that can't both be met)
- Third-party service choices not specified in requirements
- Security-relevant tradeoffs
- Changes that affect other milestones

The specific boundary varies per milestone and should be calibrated to the coordinator's demonstrated reliability over time.
