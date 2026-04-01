# DB-02: Task DAG Implementation

**Status:** DECIDED — Sequential phases + typed-function composition
**Severity:** High — affects coordinator's core loop
**Decided:** 2026-03-19, **updated** 2026-03-30 (typed edges, width enforcement)

## Decision

**Sequential phases for phase ordering. Within each milestone, work is expressed as a Python call graph (`compose.py`) that encodes the full dataflow — task definitions, dependencies, concurrency, and execution order. The orchestrator validates the call graph via AST parsing. Agent teams execute the functions.**

This decision was informed by deep research into how transformers reason about graphs (NLGraph, GraphQA), grounding strategies from adjacent fields (compiler scheduling, HTN planning, stigmergic coordination, blackboard architectures), and fundamental properties of representations for transformer comprehension (structural isomorphism, prior activation density, semantic density, locality preservation, operational affordance).

## The Core Abstraction: Agents as Typed Functions

Every agent interaction in Clou follows the same shape:

```python
async def agent_task(input_artifact: InputType) -> OutputType:
    """What the agent does.
    Criteria: how to know it succeeded"""
```

- **Dependencies** are function arguments — `implement_auth(user_model)` depends on `user_model`
- **Concurrency** is `gather()` — `gather(task_a(x), task_b(x))` means independent, parallel
- **Readiness** is input existence — can this function be called? Do its arguments exist as artifacts?
- **Convergence** is multiple arguments — `implement_api(user, product, auth)` joins three flows
- **Completion** is type satisfaction — does the output artifact match the return type's criteria?

This representation is structurally isomorphic to the computation the coordinator must reason about. The transformer recognizes the structure through its deep code-trained priors rather than reconstructing it from prose descriptions.

## Edge Types

compose.py encodes three types of dependency edges:

### Data Dependency (function arguments)

The primary and strongest edge type. `implement_auth(user_model)` requires `user_model` as input — the downstream function cannot start until the upstream function has produced the artifact. Expressed through Python's type system. The AST validator can verify these structurally.

### Ordering Constraint (annotation)

A phase must complete before another starts, but no data flows between them. Example: database migrations must run before API implementation, even though the API doesn't take the migration output as a typed argument. Expressed as a decorator or comment annotation:

```python
@requires("setup_database")
async def implement_api_tests() -> TestSuite:
    """API integration tests.
    Criteria: tests exercise all endpoints"""
```

### Artifact Dependency (annotation)

A phase needs a file or service produced by another phase, but the dependency is environmental rather than typed. Example: frontend tests need a running API server. Expressed as an annotation:

```python
@needs("services/api-server")
async def wire_frontend(shell: FrontendShell) -> App:
    """Connect UI to API.
    Criteria: operations work through UI"""
```

The typed data dependency is the strongest — Python's type checker and the AST validator verify it structurally. Ordering and artifact dependencies are weaker (annotation-level) but explicit — they're visible in the call graph and the validator parses them.

## Width Enforcement

compose.py must express the natural concurrency of the milestone's work. The default is width (parallel phases via `gather()`), not depth (serial chain).

### Minimum Width Rule

A compose.py with more than two task functions must use at least one `gather()` call, unless every function has a data dependency on the previous function's output (fully serial data flow is the only exemption). A serial chain of independent functions is an under-decomposed graph — the planner failed to identify parallelism.

### No Two-Node Milestones

A milestone with only two phases (implement + verify) is under-decomposed. The planner must identify at least three substantive phases (plus verification). This forces the planner to reason about the work's internal structure rather than treating it as a monolithic block.

### Concurrency as Default

The coordinator-plan.md prompt guides the planner to start with "what work is independent?" rather than "what order should things happen in?" The planner identifies independent workstreams first, then adds ordering constraints where data flow or environmental requirements demand serialization.

The AST validator enforces this:
- **Width check:** If `len(task_functions) > 2` and `gather_count == 0`, emit warning: "No concurrent phases in a {N}-task graph — verify all tasks have sequential data dependencies"
- **Two-node check:** If `len(task_functions) <= 2` (excluding verify), emit error: "Under-decomposed milestone — identify at least 3 substantive phases"

## The Composition File: `compose.py`

Each milestone has a single `compose.py` at the milestone level — not per-phase plan files. The full dataflow from inputs to handoff is one composition:

```python
# .clou/milestones/<name>/compose.py
"""
Milestone: <name>
"""

# Task definitions
async def setup_database() -> Schema:
    """Create tables and migrations.
    Criteria: migrations apply cleanly"""

async def implement_user_model(schema: Schema) -> UserModel:
    """User with email/password, profile, soft delete.
    Criteria: model tests pass"""

async def implement_auth(user: UserModel) -> AuthService:
    """JWT auth with refresh tokens.
    Criteria: auth flow works e2e"""

async def scaffold_frontend() -> FrontendShell:
    """React app with routing.
    Criteria: app renders, routes work"""

async def implement_api(user: UserModel, auth: AuthService) -> API:
    """REST endpoints for CRUD.
    Criteria: correct status codes, auth enforced"""

async def wire_frontend(api: API, shell: FrontendShell) -> App:
    """Connect UI to API.
    Criteria: operations work through UI"""

async def verify(app: App, requirements: Requirements) -> Handoff:
    """Walk golden paths against live environment.
    Criteria: all acceptance criteria verified"""

# The composition — this IS the execution plan
async def execute():
    # phase: foundation
    schema = await setup_database()

    # phase: core (concurrent)
    user = await implement_user_model(schema)

    # phase: services
    auth = await implement_auth(user)

    # phase: integration (concurrent flows)
    api, shell = await gather(
        implement_api(user, auth),
        scaffold_frontend(),
    )

    # phase: assembly
    app = await wire_frontend(api, shell)

    # phase: verification
    handoff = await verify(app, load_requirements())
```

Phases are visible in the comments. Concurrency is visible in `gather()`. Data flow is visible in arguments. The coordinator writes this once after reading `milestone.md` and `requirements.md`, then executes phase by phase.

## Call Graph Validation

The orchestrator validates `compose.py` via a PostToolUse hook when the coordinator writes it. Validation uses Python's `ast` module (~100 lines):

### What's validated

1. **Well-formedness** — valid Python syntax (`ast.parse`)
2. **Completeness** — every called function is defined (no undefined tasks)
3. **Acyclicity** — no circular dependencies (cycle detection via DFS)
4. **Type compatibility** — output types match downstream input types
5. **Convergence** — `execute()` entry point exists and all branches reach it
6. **Edge parsing** — `@requires` and `@needs` annotations resolved to dependency edges
7. **Width** — graphs with >2 task functions must contain `gather()` unless all edges are serial data dependencies
8. **Minimum decomposition** — milestones must have ≥3 substantive task functions (excluding verify)

### Implementation

New module in the orchestrator: `graph.py`

```python
# clou/graph.py
import ast

def validate(python_code: str) -> list[str]:
    """Validate composition call graph. Returns errors (empty = valid)."""
    try:
        tree = ast.parse(python_code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    sigs = extract_signatures(tree)
    calls = extract_call_graph(tree)
    errors = []

    # All called functions defined
    defined = set(sigs.keys())
    for caller, callees in calls.items():
        for callee in callees:
            if callee not in defined:
                errors.append(f"Undefined: {callee} (called by {caller})")

    # No cycles
    cycle = detect_cycle(calls)
    if cycle:
        errors.append(f"Cycle: {' → '.join(cycle)}")

    # Types compose
    errors += check_type_compatibility(sigs, calls)

    # Entry point exists
    if not ({"execute", "execute_milestone"} & defined):
        errors.append("Missing execute() entry point")

    return errors
```

### Hook integration

```python
# In hooks.py
async def validate_composition(input_data):
    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith("compose.py"):
        return {}

    code = Path(file_path).read_text()
    errors = graph.validate(code)
    if errors:
        return {"hookSpecificOutput": {
            "message": "Composition errors:\n"
                + "\n".join(f"  - {e}" for e in errors)
                + "\nFix the call graph."
        }}
    return {}
```

## Phase Execution

The coordinator executes the composition phase by phase:

1. **Read** `compose.py` and identify the current phase's tasks (the next uncommented `# phase:` section in `execute()`)
2. **Write** `phase.md` for the current phase (narrative scope, context for agent teams)
3. **Dispatch** tasks in the phase to agent teams — each task is a function from the composition
4. **Each agent** receives its function signature as its briefing: what it reads (arguments), what it produces (return type), success criteria (docstring)
5. **Agent teams** write results to `execution.md`
6. **Coordinator reads** `execution.md` and evaluates whether outputs satisfy the return type contracts
7. **If satisfied**, advance to next phase. **If not**, decide: retry, rework, or escalate.
8. **At phase boundaries**, run Brutalist assessment

## Within-Phase Concurrency

Tasks inside a `gather()` call are dispatched concurrently to agent teams. Tasks outside `gather()` within the same phase execute sequentially. The coordinator follows the call graph's structure.

At 3-7 tasks per phase, the transformer handles this natively through code comprehension. No MCP scheduling tools needed.

## Failure Handling

- **Task failure** → coordinator decides: retry with different instructions, rework (modify the task), or escalate
- **Type mismatch** → output doesn't satisfy contract → coordinator reworks the task
- **Phase failure** → if >30% of tasks fail, coordinator may revise the composition (re-plan)
- **Composition revision** → validated again by the hook before proceeding

## Golden Context Changes

```
milestones/<name>/
├── milestone.md          # [supervisor writes]
├── requirements.md       # [supervisor writes]
├── compose.py            # [coordinator writes] — the full call graph
├── decisions.md          # [coordinator writes]
├── handoff.md            # [verifier writes]
├── escalations/
└── phases/
    ├── <phase-name>/
    │   ├── phase.md      # [coordinator writes] — narrative scope
    │   └── execution.md  # [agent teams write] — results
    └── verification/
        ├── phase.md
        └── execution.md
```

`compose.py` replaces per-phase `plan.md` files. One composition per milestone. Phase directories retain `phase.md` (narrative) and `execution.md` (results).

## Orchestrator Changes

Updated file structure:
```
clou/
├── orchestrator.py       # Entry point, session lifecycle
├── prompts.py            # Prompt loading and parameterization
├── hooks.py              # Write boundaries + composition validation
├── graph.py              # Call graph parsing and validation  ← NEW (~100 lines)
├── tools.py              # MCP tools
├── tokens.py             # Token usage tracking
├── recovery.py           # Crash recovery
└── utils.py              # Shared utilities
```

## Why Not the Other Options

- **Option A (prompt-only scheduling)**: No structural enforcement. The coordinator could skip dependencies.
- **Option B (YAML in markdown)**: Mixing YAML in markdown is awkward. LLMs parse YAML unreliably. And YAML is not isomorphic to computation — it describes structure without being structure.
- **Option C (MCP DAG tools)**: Overengineered for 5-7 tasks per phase. The coordinator doesn't need `dag_get_ready_tasks()` — it reads the call graph and sees what's ready. Validation via hook is the targeted subset of Option C that provides structural enforcement without runtime state management.
- **Option D (original)**: The sequential phases concept survives, but per-phase `plan.md` in markdown is replaced by milestone-level `compose.py` in Python, informed by the five properties of effective transformer representations.

## Research Basis

### Transformer graph reasoning (NLGraph, NeurIPS 2023)
- Frontier models handle DAGs of 5-20 nodes at >95% accuracy
- Adjacency list with dependency semantics outperforms other formats
- Incremental "what's ready now?" outperforms one-shot topological sort

### Grounding strategies (cross-domain synthesis)
- **Program-of-Thought**: Code execution >30% more reliable than direct reasoning for graph tasks
- **Stigmergic coordination**: File system as coordination medium, artifact-gated readiness
- **Blackboard architecture**: Clou's golden context IS a blackboard system
- **Compiler scheduling**: Critical path priority, frontier focus, dispatch limits
- **HTN planning**: Plan repair over replanning, least commitment principle

### Representation properties (Zhang & Norman, Iverson, Peirce, information theory)
1. **Structural isomorphism**: Code's syntax mirrors computational structure
2. **Prior activation density**: Python activates deep code-trained circuits
3. **Semantic density + structural redundancy**: Every token carries meaning; patterns are predictable
4. **Locality preservation**: Dependencies are adjacent in token sequence (function arguments)
5. **Operational affordance**: Dependency tracing, concurrency detection, readiness checking are all syntactic inspections

### The meta-principle
A representation is effective when the transformer perceives structure through recognition rather than reconstruction. `compose.py` is constitutive — it IS the computation, not a description of it.
