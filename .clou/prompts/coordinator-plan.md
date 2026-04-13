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

6. Identify tasks. List every distinct unit of work the milestone
   requires. Each task is a function with a name, one-line success
   criterion, and resource estimate. Do NOT think about ordering yet.
   Just: what work exists?

   Constraints on each task:
   - Small enough to complete in one EXECUTE cycle.
   - Context budget: 200k token window per agent. If a task requires
     reading >15 source files or running >3 test suites, split it.
   - Minimum 3 substantive tasks (excluding verification). If the
     work looks like two tasks, you haven't decomposed far enough.

7. Determine dependencies. For each pair of tasks, ask: does task B
   need to READ the output of task A to do its work? Not "does B
   come after A" — that is ordering bias. Does B literally require
   an artifact that A produces?

   a. If B does not need A's output, they are independent. They
      belong in a gather() group.
   b. If B needs A's output, the dependency is genuine. B takes A's
      return type as a parameter.
   c. If you cannot name the specific artifact that flows from A to
      B, they are independent. Do not create a wrapper type to
      justify serialization.
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
   g. Density awareness: the sparsest adequate graph is optimal.
      Each unnecessary serial edge adds a full EXECUTE→ASSESS cycle
      and multiplies the failure surface. Over-decomposition degrades
      performance as severely as under-decomposition.

   The validator rejects serial chains where every intermediate type
   is single-use (produced once, consumed once). This is the
   structural signature of fabricated dependencies. If the validator
   rejects your topology, restructure independent work into gather().

8. Write compose.py — typed-function call graph.
   Narrow graph (serial dependencies):
   ```python
   @resource_bounds(tokens=120_000, timeout_seconds=300)
   async def implement_auth(user_model: UserModel) -> AuthService:
       """Login, registration, and session management working."""

   @resource_bounds(tokens=150_000, timeout_seconds=360)
   async def implement_api(auth: AuthService) -> APILayer:
       """All endpoints from intents.md observable and functional."""

   async def execute():
       user_model = await setup_schema()
       auth = await implement_auth(user_model)
       api = await implement_api(auth)
   ```
   Wide graph (independent workstreams):
   ```python
   @resource_bounds(tokens=120_000, timeout_seconds=300)
   async def implement_api(schema: Schema) -> APILayer:
       """All API routes return correct responses per intents.md."""

   @resource_bounds(tokens=80_000, timeout_seconds=240)
   async def scaffold_frontend() -> FrontendShell:
       """Component tree renders with placeholder data."""

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
   @resource_bounds(tokens=100_000, timeout_seconds=300)
   async def implement_api_tests() -> TestSuite:
       """Tests exercise all endpoints against live database."""

   @needs("services/api-server")
   @resource_bounds(tokens=90_000, timeout_seconds=240)
   async def wire_frontend(shell: FrontendShell) -> App:
       """All user-facing operations work through the UI."""
   ```
   - @requires("phase_name") — ordering constraint without data flow.
     The named phase must complete before this phase starts.
   - @needs("path") — environmental dependency. The named file or
     service must exist (produced by a prior phase).

   Rules:
   - Every async function is a task dispatched to an agent.
   - Docstrings are one-line criteria summaries. The detailed
     specification lives in phase.md — compose.py is topology and
     contracts, not the full spec.
   - Every task function declares @resource_bounds(tokens=N,
     timeout_seconds=N) — the planner's effort estimate for budget
     allocation and abort decisions.
   - When not obvious from context, docstrings should reference which
     intent(s) the task addresses (e.g. "Covers intent: user can
     export data").
   - Type annotations express dependencies between tasks.
   - gather() expresses independence — tasks in a group run in parallel.
   - Only awaited calls in execute() are dispatched — helpers are structural.

8. Intent-coverage check: verify that every intent in intents.md has
   at least one task in compose.py whose criteria address that outcome.
   If an intent has no covering task, the decomposition is incomplete —
   add tasks or broaden existing criteria before proceeding.

9. Write phase specs: .clou/milestones/{milestone}/phases/{phase}/phase.md
   for each phase. Include: scope, files to read and modify, patterns
   to follow, edge cases to handle, and the full criteria that the
   one-line compose.py docstring summarizes. phase.md is the agent's
   complete briefing — compose.py docstrings are criteria summaries,
   not specifications.

10. Call clou_update_status to write initial status.md:
   - phase: {first phase name}
   - cycle: 1
   - next_step: EXECUTE
   - phase_progress: {all phases mapped to "pending"}

11. Write decisions.md entry for PLAN cycle with decomposition reasoning.

12. Call clou_write_checkpoint:
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
well-formedness, completeness, acyclicity, type compatibility,
resource_bounds presence, intent-coverage, and topology quality
(serial chains with single-use types are rejected as fabricated
dependencies — restructure into gather() or demonstrate genuine
data flow through shared types).

</schemas>

</cycle>
