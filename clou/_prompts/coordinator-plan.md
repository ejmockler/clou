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

6. Decompose into phases. Each phase is a coherent unit of work:
   - Keep phases small enough to complete in one EXECUTE cycle.
   - Dependent phases are sequential — typed inputs from prior phases.
   - Minimum 3 substantive phases (excluding verification) — no
     two-node milestones. If the work looks like two phases, you
     haven't decomposed far enough.

   Width-first decomposition — start with "what work is independent?"
   not "what order should things happen?"

   a. Identify independent workstreams first: changes to different
      files or modules that don't read each other's outputs. Each is
      a candidate for parallel execution via gather().
   b. Then add ordering constraints only where data flow demands it:
      a phase needs the typed output of another, or an environmental
      requirement (service must be running, database must exist).
   c. gather() is the default — serialization requires explicit
      justification (data dependency or environmental requirement).
      If you cannot name what data flows between two phases, they
      belong in a gather() group.
   d. Reason about critical path: which sequential chain determines
      wall-clock time? Pull work out of that chain when possible.
   e. Balance gather() groups: tasks in a group should be roughly
      equal effort — one dominant task makes the others wait.
   f. When NOT to parallelize — a narrow graph is correct when:
      - The scope is single-dimensional (one file, one concern).
      - All changes depend on each other serially.
      - The milestone is simple enough that multi-agent overhead
        outweighs the parallelism benefit.
      A narrow graph for a simple milestone is a feature, not a failure.

7. Write compose.py — typed-function call graph.
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

8. Write phase specs: .clou/milestones/{milestone}/phases/{phase}/phase.md
   for each phase. Include: scope, relevant context, what the agent
   needs to know about the domain.

9. Call clou_update_status to write initial status.md:
   - phase: {first phase name}
   - cycle: 1
   - next_step: EXECUTE
   - phase_progress: {all phases mapped to "pending"}

10. Write decisions.md entry for PLAN cycle with decomposition reasoning.

11. Call clou_write_checkpoint:
   - cycle: 1
   - step: PLAN
   - next_step: EXECUTE
   - current_phase: {first phase name}
   - phases_completed: 0
   - phases_total: {count}

Protocol tools: Use clou_write_checkpoint and clou_update_status for
checkpoint and status files. These tools guarantee correct format.
Use Write/Edit only for narrative files (decisions.md, phase specs).
</procedure>

<schemas>

compose.py: typed async functions + execute() entry point.
Validated by orchestrator via AST parsing (graph.py). Checks:
well-formedness, completeness, acyclicity, type compatibility.

</schemas>

</cycle>
