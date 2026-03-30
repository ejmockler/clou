<cycle type="PLAN">

<objective>
Read milestone requirements and project context. Decompose the work
into phases with a typed-function call graph in compose.py. Write
phase specifications and initial status.
</objective>

<procedure>
1. Read milestone.md for scope and boundaries.
2. Read intents.md for observable outcomes — these orient your
   decomposition toward what a person sees when the milestone succeeds.
3. Read requirements.md for implementation constraints.
4. Read project.md for coding conventions, tech stack, existing code.

5. Decompose into phases. Each phase is a coherent unit of work:
   - Keep phases small enough to complete in one EXECUTE cycle.
   - Dependent phases are sequential — typed inputs from prior phases.

   Determine graph width — how many phases run in parallel:
   a. Identify independent workstreams: changes to different files or
      modules that don't read each other's outputs. Each is a candidate
      for parallel execution via gather().
   b. Reason about critical path: which sequential chain determines
      wall-clock time? Pull work out of that chain when possible.
   c. Balance gather() groups: tasks in a group should be roughly
      equal effort — one dominant task makes the others wait.
   d. When NOT to parallelize — a narrow graph is correct when:
      - The scope is single-dimensional (one file, one concern).
      - All changes depend on each other serially.
      - The milestone is simple enough that multi-agent overhead
        outweighs the parallelism benefit.
      A narrow graph for a simple milestone is a feature, not a failure.

6. Write compose.py — typed-function call graph.
   Narrow graph (serial dependencies):
   ```python
   async def implement_auth(user_model: UserModel) -> AuthService:
       """Implement authentication service.
       Criteria: login, registration, session management working."""

   async def implement_api(auth: AuthService) -> APILayer:
       """Build API endpoints.
       Criteria: all endpoints from intents.md observable and functional."""

   async def execute():
       user_model = await setup_schema()
       auth = await implement_auth(user_model)
       api = await implement_api(auth)
   ```
   Wide graph (independent workstreams):
   ```python
   async def execute():
       schema = await setup_schema()
       api, frontend = await gather(
           implement_api(schema),
           scaffold_frontend(),
       )
       app = await integrate(api, frontend)
   ```
   - Every async function is a task dispatched to an agent.
   - Docstrings contain success criteria (agent reads these).
   - Type annotations express dependencies between tasks.
   - gather() expresses independence — tasks in a group run in parallel.
   - Only awaited calls in execute() are dispatched — helpers are structural.

7. Write phase specs: .clou/milestones/{milestone}/phases/{phase}/phase.md
   for each phase. Include: scope, relevant context, what the agent
   needs to know about the domain.

8. Write initial status.md with all phases listed as pending.

9. Write decisions.md entry for PLAN cycle with decomposition reasoning.

10. Write checkpoint (path in cycle prompt above):
   - cycle: 1
   - step: PLAN
   - next_step: EXECUTE
   - current_phase: {first phase name}
   - phases_completed: 0
   - phases_total: {count}
</procedure>

<schemas>

compose.py: typed async functions + execute() entry point.
Validated by orchestrator via AST parsing (graph.py). Checks:
well-formedness, completeness, acyclicity, type compatibility.

status.md:
```
# Status: {milestone}
## Current State
phase: {name}
cycle: 1
last_updated: {ISO timestamp}
## Phase Progress
| Phase | Status | Summary |
|---|---|---|
| {name} | pending | — |
## Notes
- Plan created cycle 1
```

</schemas>

</cycle>
