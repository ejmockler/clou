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
5. If memory.md is in the read set, read it for operational patterns:
   - cost-calibration: expected cycle count, token budget, duration
   - decomposition: topology precedents (single-phase vs parallel)
   - debt: known validation noise, recurring false positives
   Use active patterns to calibrate your decomposition.
   Ignore fading and archived patterns — only active patterns
   enter your planning context.

6. Identify tasks — FLAT SET, NO ORDERING.
   List every distinct unit of work the milestone requires.
   Output them as an UNORDERED set — do not reason about
   dependencies, ordering, or topology at this stage.
   
   For each task, specify:
   - A name (function name for compose.py)
   - A one-line success criterion
   - A resource estimate (tokens, timeout)
   - Which intent(s) it addresses
   
   Constraints on each task:
   - Small enough to complete in one EXECUTE cycle.
   - Context budget: 200k token window per agent. If a task requires
     reading >15 source files or running >3 test suites, split it.
   - Minimum 3 substantive tasks (excluding verification). If the
     work looks like two tasks, you haven't decomposed far enough.

   CRITICAL: Do not think about what depends on what yet.
   Just enumerate what work exists. The topology comes in step 7.

7. Determine topology — ANALYZE THE TASK SET from step 6.
   Now, and only now, reason about dependencies.
   
   For each PAIR of tasks from your step 6 list, ask:
   does task B need to READ an artifact that task A produces?
   Not "does B come after A" — that is ordering bias.
   Does B literally require a typed output from A?

   a. If B does not need A's output, they are independent.
      They belong in a gather() group.
   b. If B needs A's output, the dependency is genuine.
      B takes A's return type as a parameter.
   c. If you cannot name the specific artifact that flows
      from A to B, they are independent. Do not create a
      wrapper type to justify serialization.
   d. Reason about critical path: which sequential chain
      determines wall-clock time? Pull work out of that
      chain when possible.
   e. Balance gather() groups: tasks in a group should be
      roughly equal effort.
   f. When NOT to parallelize — a narrow graph is correct when:
      - The scope is single-dimensional (one file, one concern).
      - All changes depend on each other serially.
      - The milestone is simple enough that multi-agent overhead
        outweighs the parallelism benefit.
      A narrow graph for a simple milestone is a feature, not a failure.
   g. Density awareness: the sparsest adequate graph is optimal.

   SELF-CHECK: Compare your topology against your step 6 task set.
   If all tasks were independent in step 6 but you created serial
   dependencies in step 7, justify each dependency with a specific
   named artifact that flows between them. If you cannot, restructure
   into gather().
   
   The validator rejects serial chains where every intermediate type
   is single-use (produced once, consumed once). This is the
   structural signature of fabricated dependencies.

8. Write compose.py — typed-function call graph.
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

   Ordering and dependency annotations — when phases have no data
   dependency but must be ordered, or need files/services from another
   phase:
   ```python
   @requires("setup_database")
   async def implement_api_tests() -> TestSuite:
       """API integration tests.
       Criteria: tests exercise all endpoints"""

   @needs("services/api-server")
   async def wire_frontend(shell: FrontendShell) -> App:
       """Connect UI to API.
       Criteria: operations work through UI"""
   ```
   - @requires("phase_name") — ordering constraint without data flow.
     The named phase must complete before this phase starts.
   - @needs("path") — environmental dependency. The named file or
     service must exist (produced by a prior phase).

   Rules:
   - Every async function is a task dispatched to an agent.
   - Docstrings contain success criteria (agent reads these).
   - Type annotations express dependencies between tasks.
   - gather() expresses independence — tasks in a group run in parallel.
   - Only awaited calls in execute() are dispatched — helpers are structural.

9. Write phase specs: .clou/milestones/{milestone}/phases/{phase}/phase.md
   for each phase. Include: scope, relevant context, what the agent
   needs to know about the domain.

10. Write initial status.md with all phases listed as pending.

11. Write decisions.md entry for PLAN cycle with decomposition reasoning.

12. Write checkpoint (path in cycle prompt above):
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
