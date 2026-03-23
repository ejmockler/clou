<cycle type="PLAN">

<objective>
Read milestone requirements and project context. Decompose the work
into phases with a typed-function call graph in compose.py. Write
phase specifications and initial status.
</objective>

<procedure>
1. Read milestone.md for scope and boundaries.
2. Read requirements.md for acceptance criteria.
3. Read project.md for coding conventions, tech stack, existing code.

4. Decompose into phases. Each phase is a coherent unit of work:
   - Independent phases can use gather() for parallel execution.
   - Dependent phases are sequential — typed inputs from prior phases.
   - Keep phases small enough to complete in one EXECUTE cycle.

5. Write compose.py — typed-function call graph:
   ```python
   async def implement_auth(user_model: UserModel) -> AuthService:
       """Implement authentication service.
       Criteria: login, registration, session management working."""

   async def implement_api(auth: AuthService) -> APILayer:
       """Build API endpoints.
       Criteria: all endpoints in requirements.md implemented."""

   async def execute():
       user_model = await setup_schema()
       auth = await implement_auth(user_model)
       api = await implement_api(auth)
   ```
   - Every async function is a task dispatched to an agent.
   - Docstrings contain success criteria (agent reads these).
   - Type annotations express dependencies between tasks.
   - Only awaited calls in execute() are dispatched — helpers are structural.

6. Write phase specs: .clou/milestones/{milestone}/phases/{phase}/phase.md
   for each phase. Include: scope, relevant context, what the agent
   needs to know about the domain.

7. Write initial status.md with all phases listed as pending.

8. Write decisions.md entry for PLAN cycle with decomposition reasoning.

9. Write checkpoint to active/coordinator.md:
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
