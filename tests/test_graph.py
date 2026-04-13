"""Tests for compose.py call graph validation.

Each test uses a compose.py snippet to exercise a specific contract.
Tests go through the public validate() API only — no implementation
details are tested directly.
"""

from clou.graph import (
    MilestoneEntry,
    ResourceBounds,
    RoadmapGraph,
    ValidationResult,
    compute_independent_sets,
    compute_topology,
    extract_dag_data,
    extract_roadmap_data,
    get_colayer_tasks,
    parse_roadmap_annotations,
    validate,
    validate_roadmap,
    validate_roadmap_annotations,
)

# ---------------------------------------------------------------------------
# The canonical example from DB-02 — the golden test
# ---------------------------------------------------------------------------

VALID_COMPOSITION = """\
async def setup_database() -> Schema:
    \"\"\"Create tables and migrations.
    Criteria: migrations apply cleanly\"\"\"

async def implement_user_model(schema: Schema) -> UserModel:
    \"\"\"User with email/password, profile, soft delete.\"\"\"

async def implement_auth(user: UserModel) -> AuthService:
    \"\"\"JWT auth with refresh tokens.\"\"\"

async def scaffold_frontend() -> FrontendShell:
    \"\"\"React app with routing.\"\"\"

async def implement_api(user: UserModel, auth: AuthService) -> API:
    \"\"\"REST endpoints for CRUD.\"\"\"

async def wire_frontend(api: API, shell: FrontendShell) -> App:
    \"\"\"Connect UI to API.\"\"\"

async def verify(app: App, requirements: Requirements) -> Handoff:
    \"\"\"Walk golden paths.\"\"\"

async def execute():
    schema = await setup_database()
    user = await implement_user_model(schema)
    auth = await implement_auth(user)
    api, shell = await gather(
        implement_api(user, auth),
        scaffold_frontend(),
    )
    app = await wire_frontend(api, shell)
    handoff = await verify(app, load_requirements())
"""


def test_valid_composition() -> None:
    """The DB-02 canonical example validates cleanly."""
    assert validate(VALID_COMPOSITION) == []


# ---------------------------------------------------------------------------
# Well-formedness
# ---------------------------------------------------------------------------


def test_syntax_error() -> None:
    errors = validate("def foo(")
    assert len(errors) == 1
    assert errors[0].message.startswith("Syntax error:")


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------


def test_missing_execute() -> None:
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"
"""
    errors = validate(code)
    assert len(errors) == 1
    assert errors[0].message == "Missing execute() entry point"
    assert errors[0].severity == "error"


def test_execute_milestone_entry_point() -> None:
    """execute_milestone() is also accepted as an entry point."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute_milestone():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
"""
    assert validate(code) == []


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def test_undefined_function() -> None:
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
"""
    errors = validate(code)
    assert any("Undefined: task_b" in e.message for e in errors)


def test_imported_name_not_flagged() -> None:
    code = """\
from project import load_requirements

async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
"""
    assert not any("Undefined" in e.message for e in validate(code))


# ---------------------------------------------------------------------------
# Unused tasks
# ---------------------------------------------------------------------------


def test_unused_function() -> None:
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def execute():
    a = await task_a()
"""
    errors = validate(code)
    assert any("Unused: task_b" in e.message for e in errors)


def test_helper_function_not_flagged_as_unused() -> None:
    """Non-async helpers are not task functions -- not flagged as unused."""
    code = """\
def load_config():
    pass

async def task_a() -> A:
    \"\"\"Uses config.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
"""
    assert validate(code) == []


# ---------------------------------------------------------------------------
# Acyclicity
# ---------------------------------------------------------------------------


def test_cycle() -> None:
    code = """\
async def task_a(x: B) -> A:
    \"\"\"Needs B, produces A.\"\"\"

async def task_b(y: A) -> B:
    \"\"\"Needs A, produces B.\"\"\"

async def execute():
    a = await task_a(b)
    b = await task_b(a)
"""
    errors = validate(code)
    assert any("Cycle:" in e.message for e in errors)


def test_same_return_type_no_false_cycle() -> None:
    """Two functions returning the same type must not trigger a false cycle."""
    code = """\
async def produce() -> A:
    \"\"\"Initial A.\"\"\"

async def transform(x: A) -> A:
    \"\"\"Transform A into better A.\"\"\"

async def consume(x: A) -> Done:
    \"\"\"Use A.\"\"\"

async def execute():
    a = await produce()
    a2 = await transform(a)
    d = await consume(a2)
"""
    assert validate(code) == []


# ---------------------------------------------------------------------------
# Type compatibility
# ---------------------------------------------------------------------------


def test_type_mismatch() -> None:
    code = """\
async def task_a() -> TypeA:
    \"\"\"Produce A.\"\"\"

async def task_b(x: TypeB) -> TypeC:
    \"\"\"Expects B but receives A.\"\"\"

async def execute():
    a = await task_a()
    c = await task_b(a)
"""
    errors = validate(code)
    assert any(
        "Type mismatch" in e.message and "expects TypeB" in e.message and "got TypeA" in e.message
        for e in errors
    )


def test_types_match() -> None:
    code = """\
async def produce() -> Widget:
    \"\"\"Make a widget.\"\"\"

async def transform(w: Widget) -> Gadget:
    \"\"\"Transform widget into gadget.\"\"\"

async def consume(g: Gadget) -> Done:
    \"\"\"Use a gadget.\"\"\"

async def execute():
    w = await produce()
    g = await transform(w)
    d = await consume(g)
"""
    assert validate(code) == []


def test_no_annotations_no_type_errors() -> None:
    """Functions without type annotations should not produce type errors."""
    code = """\
async def task_a():
    \"\"\"No types.\"\"\"

async def task_b(x):
    \"\"\"No types.\"\"\"

async def task_c(y):
    \"\"\"No types.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
"""
    assert validate(code) == []


# ---------------------------------------------------------------------------
# Structural patterns
# ---------------------------------------------------------------------------


def test_gather() -> None:
    code = """\
async def task_a() -> A:
    \"\"\"Parallel A.\"\"\"

async def task_b() -> B:
    \"\"\"Parallel B.\"\"\"

async def combine(a: A, b: B) -> C:
    \"\"\"Join.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await combine(a, b)
"""
    assert validate(code) == []


def test_no_params() -> None:
    code = """\
async def standalone() -> Result:
    \"\"\"No dependencies.\"\"\"

async def enhance(r: Result) -> Better:
    \"\"\"Enhance result.\"\"\"

async def finalize(b: Better) -> Done:
    \"\"\"Finalize.\"\"\"

async def execute():
    r = await standalone()
    b = await enhance(r)
    d = await finalize(b)
"""
    assert validate(code) == []


def test_standalone_await_no_assignment() -> None:
    """await without assignment (e.g., cleanup tasks)."""
    code = """\
async def setup() -> Env:
    \"\"\"Set up.\"\"\"

async def build(e: Env) -> App:
    \"\"\"Build app.\"\"\"

async def cleanup(a: App) -> None:
    \"\"\"Clean up.\"\"\"

async def execute():
    e = await setup()
    a = await build(e)
    await cleanup(a)
"""
    assert validate(code) == []


def test_sync_execute_entry_point() -> None:
    """A sync def execute() is rejected — entry points must be async."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

def execute(tasks):
    a = task_a()
"""
    errors = validate(code)
    assert any("Entry point must be async" in e.message for e in errors)


def test_multiple_errors_reported() -> None:
    """All structural problems are reported, not just the first one."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B \u2014 but never called.\"\"\"

async def execute():
    a = await task_a()
    c = await task_c(a)
"""
    errors = validate(code)
    assert any("Undefined: task_c" in e.message for e in errors)
    assert any("Unused: task_b" in e.message for e in errors)
    assert len(errors) >= 2


# ---------------------------------------------------------------------------
# Duplicate entry points
# ---------------------------------------------------------------------------


def test_duplicate_execute() -> None:
    """Two execute() definitions should produce an error."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()

async def execute():
    a = await task_a()
"""
    errors = validate(code)
    assert any("Duplicate entry point 'execute'" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Sync entry point rejection
# ---------------------------------------------------------------------------


def test_sync_execute_error_message() -> None:
    """Sync execute() error includes the function name and line number."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

def execute(tasks):
    a = task_a()
"""
    errors = validate(code)
    assert len(errors) >= 1
    assert "Entry point must be async: 'execute'" in errors[0].message
    assert "line" in errors[0].message


# ---------------------------------------------------------------------------
# Control flow rejection
# ---------------------------------------------------------------------------


def test_control_flow_in_execute_body() -> None:
    """Control flow wrapping task calls should be rejected."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def execute():
    a = await task_a()
    if some_condition:
        b = await task_b(a)
"""
    errors = validate(code)
    assert any("Control flow in execute body not supported: If" in e.message for e in errors)


def test_for_loop_in_execute_body() -> None:
    """For loops in execute body should be rejected."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    for item in items:
        pass
"""
    errors = validate(code)
    assert any("Control flow in execute body not supported: For" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Line numbers in errors
# ---------------------------------------------------------------------------


def test_undefined_error_includes_line_number() -> None:
    """Undefined function errors should include a line number."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
"""
    errors = validate(code)
    undefined = [e for e in errors if "Undefined: task_b" in e.message]
    assert len(undefined) == 1
    assert "line" in undefined[0].message


def test_unused_error_includes_line_number() -> None:
    """Unused function errors should include a line number."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def execute():
    a = await task_a()
"""
    errors = validate(code)
    unused = [e for e in errors if "Unused: task_b" in e.message]
    assert len(unused) == 1
    assert "line" in unused[0].message


def test_cycle_error_includes_line_number() -> None:
    """Cycle errors should include a line number."""
    code = """\
async def task_a(x: B) -> A:
    \"\"\"Needs B, produces A.\"\"\"

async def task_b(y: A) -> B:
    \"\"\"Needs A, produces B.\"\"\"

async def execute():
    a = await task_a(b)
    b = await task_b(a)
"""
    errors = validate(code)
    cycle = [e for e in errors if "Cycle:" in e.message]
    assert len(cycle) == 1
    assert "line" in cycle[0].message


# ---------------------------------------------------------------------------
# extract_dag_data
# ---------------------------------------------------------------------------


def test_extract_dag_data_basic() -> None:
    """extract_dag_data returns tasks and dependencies from compose.py."""
    code = """\
async def setup() -> Schema:
    \"\"\"Setup.\"\"\"

async def build(s: Schema) -> App:
    \"\"\"Build.\"\"\"

async def execute():
    s = await setup()
    app = await build(s)
"""
    tasks, deps = extract_dag_data(code)
    names = [t["name"] for t in tasks]
    assert "setup" in names
    assert "build" in names
    assert all(t["status"] == "pending" for t in tasks)
    assert deps["build"] == ["setup"]
    assert deps["setup"] == []


def test_extract_dag_data_no_entry() -> None:
    """extract_dag_data works even without an execute() entry point."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"
"""
    tasks, deps = extract_dag_data(code)
    assert len(tasks) == 1
    assert tasks[0]["name"] == "task_a"
    assert deps["task_a"] == []


def test_extract_dag_data_gather() -> None:
    """extract_dag_data handles gather() parallel tasks."""
    code = """\
async def task_a() -> A:
    \"\"\"A.\"\"\"

async def task_b() -> B:
    \"\"\"B.\"\"\"

async def combine(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await combine(a, b)
"""
    tasks, deps = extract_dag_data(code)
    names = {t["name"] for t in tasks}
    assert names == {"task_a", "task_b", "combine"}
    assert set(deps["combine"]) == {"task_a", "task_b"}
    assert deps["task_a"] == []
    assert deps["task_b"] == []


# ---------------------------------------------------------------------------
# @requires decorator edges
# ---------------------------------------------------------------------------


def test_requires_decorator_edges() -> None:
    """@requires decorator creates ordering edges in the DAG."""
    code = """\
async def setup() -> Schema:
    \"\"\"Create schema.\"\"\"

@requires("setup")
async def migrate(s: Schema) -> Migrated:
    \"\"\"Run migrations after setup.\"\"\"

async def seed(m: Migrated) -> Seeded:
    \"\"\"Seed data.\"\"\"

async def execute():
    s = await setup()
    m = await migrate(s)
    d = await seed(m)
"""
    tasks, deps = extract_dag_data(code)
    # @requires("setup") adds setup as a dependency of migrate
    assert "setup" in deps["migrate"]


def test_requires_decorator_validates_cleanly() -> None:
    """A well-formed compose.py with @requires validates without errors."""
    code = """\
async def setup() -> Schema:
    \"\"\"Create schema.\"\"\"

@requires("setup")
async def migrate(s: Schema) -> Migrated:
    \"\"\"Run migrations.\"\"\"

async def seed(m: Migrated) -> Seeded:
    \"\"\"Seed data.\"\"\"

async def execute():
    s = await setup()
    m = await migrate(s)
    d = await seed(m)
"""
    assert validate(code) == []


# ---------------------------------------------------------------------------
# @needs decorator
# ---------------------------------------------------------------------------


def test_needs_decorator_parses_without_error() -> None:
    """@needs decorator is parsed without causing validation errors."""
    code = """\
async def setup() -> Schema:
    \"\"\"Create schema.\"\"\"

@needs("config/database.yml")
async def migrate(s: Schema) -> Migrated:
    \"\"\"Run migrations.\"\"\"

async def seed(m: Migrated) -> Seeded:
    \"\"\"Seed data.\"\"\"

async def execute():
    s = await setup()
    m = await migrate(s)
    d = await seed(m)
"""
    assert validate(code) == []


# ---------------------------------------------------------------------------
# Width check
# ---------------------------------------------------------------------------


def test_width_warning_no_gather() -> None:
    """4-task graph with no gather() emits a width warning."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def task_c() -> C:
    \"\"\"Do C.\"\"\"

async def task_d() -> D:
    \"\"\"Do D.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b()
    c = await task_c()
    d = await task_d()
"""
    errors = validate(code)
    assert any("No concurrent phases" in e.message and "4-task graph" in e.message for e in errors)


def test_width_no_warning_with_gather() -> None:
    """4-task graph with gather() does not emit a width warning."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def combine(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def finalize(c: C) -> D:
    \"\"\"Finalize.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await combine(a, b)
    d = await finalize(c)
"""
    errors = validate(code)
    assert not any("No concurrent phases" in e.message for e in errors)


def test_width_serial_data_flow_no_warning() -> None:
    """4-task fully serial graph where each takes previous output -- no warning."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def task_d(c: C) -> D:
    \"\"\"Do D.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
    d = await task_d(c)
"""
    errors = validate(code)
    assert not any("No concurrent phases" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Minimum decomposition
# ---------------------------------------------------------------------------


def test_min_decomposition_error() -> None:
    """2-task graph (excluding verify) emits under-decomposed error."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def verify(b: B) -> Handoff:
    \"\"\"Verify.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    h = await verify(b)
"""
    errors = validate(code)
    assert any("Under-decomposed milestone" in e.message for e in errors)


def test_min_decomposition_passes_with_enough_tasks() -> None:
    """3-task graph (excluding verify) passes minimum decomposition."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def verify(c: C) -> Handoff:
    \"\"\"Verify.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
    h = await verify(c)
"""
    errors = validate(code)
    assert not any("Under-decomposed" in e.message for e in errors)


# ---------------------------------------------------------------------------
# @resource_bounds decorator
# ---------------------------------------------------------------------------


def test_resource_bounds_extraction() -> None:
    """@resource_bounds decorator is parsed into Sig.resource_bounds."""
    import ast
    from clou.graph import _extract_sigs

    code = """\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def heavy_task() -> Result:
    \"\"\"A resource-intensive task.\"\"\"

async def light_task() -> Other:
    \"\"\"A lightweight task.\"\"\"
"""
    tree = ast.parse(code)
    sigs = _extract_sigs(tree)
    assert sigs["heavy_task"].resource_bounds == ResourceBounds(
        tokens=50000, timeout_seconds=300
    )
    assert sigs["light_task"].resource_bounds is None


def test_resource_bounds_optional() -> None:
    """Functions without @resource_bounds produce no resource_bounds key in DAG data."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
"""
    tasks, _ = extract_dag_data(code)
    for task in tasks:
        assert "resource_bounds" not in task


def test_resource_bounds_partial() -> None:
    """Only tokens or only timeout_seconds can be specified."""
    import ast
    from clou.graph import _extract_sigs

    code_tokens_only = """\
@resource_bounds(tokens=80000)
async def tokens_task() -> A:
    \"\"\"Tokens only.\"\"\"
"""
    tree = ast.parse(code_tokens_only)
    sigs = _extract_sigs(tree)
    assert sigs["tokens_task"].resource_bounds == ResourceBounds(
        tokens=80000, timeout_seconds=None
    )

    code_timeout_only = """\
@resource_bounds(timeout_seconds=600)
async def timeout_task() -> B:
    \"\"\"Timeout only.\"\"\"
"""
    tree = ast.parse(code_timeout_only)
    sigs = _extract_sigs(tree)
    assert sigs["timeout_task"].resource_bounds == ResourceBounds(
        tokens=None, timeout_seconds=600
    )


def test_resource_bounds_validation() -> None:
    """Negative values produce a validation error."""
    code = """\
@resource_bounds(tokens=-100, timeout_seconds=300)
async def bad_task() -> A:
    \"\"\"Negative tokens.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a = await bad_task()
    b = await task_b(a)
    c = await task_c(b)
"""
    errors = validate(code)
    assert any("tokens must be positive" in e.message for e in errors)

    code2 = """\
@resource_bounds(tokens=100, timeout_seconds=-10)
async def bad_task2() -> A:
    \"\"\"Negative timeout.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a = await bad_task2()
    b = await task_b(a)
    c = await task_c(b)
"""
    errors2 = validate(code2)
    assert any("timeout_seconds must be positive" in e.message for e in errors2)


def test_resource_bounds_in_dag_data() -> None:
    """extract_dag_data includes bounds in task dicts when present."""
    code = """\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def heavy_task() -> A:
    \"\"\"Heavy.\"\"\"

async def light_task(a: A) -> B:
    \"\"\"Light.\"\"\"

async def final_task(b: B) -> C:
    \"\"\"Final.\"\"\"

async def execute():
    a = await heavy_task()
    b = await light_task(a)
    c = await final_task(b)
"""
    tasks, _ = extract_dag_data(code)
    task_map = {t["name"]: t for t in tasks}
    assert task_map["heavy_task"]["resource_bounds"] == {
        "tokens": 50000,
        "timeout_seconds": 300,
    }
    assert "resource_bounds" not in task_map["light_task"]
    assert "resource_bounds" not in task_map["final_task"]


def test_resource_bounds_no_validation_error() -> None:
    """Valid @resource_bounds does not produce validation errors."""
    code = """\
@resource_bounds(tokens=50000, timeout_seconds=300)
async def heavy_task() -> A:
    \"\"\"Heavy.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a = await heavy_task()
    b = await task_b(a)
    c = await task_c(b)
"""
    errors = validate(code)
    assert not any("resource_bounds" in e.message for e in errors)


# ---------------------------------------------------------------------------
# compute_topology
# ---------------------------------------------------------------------------


def test_topology_single_task() -> None:
    """Single task: width=1, depth=1, one layer."""
    code = """\
async def only_task() -> Result:
    \"\"\"The only task.\"\"\"

async def execute():
    r = await only_task()
"""
    topo = compute_topology(code)
    assert topo["width"] == 1
    assert topo["depth"] == 1
    assert topo["layer_count"] == 1
    assert topo["gather_groups"] == []
    assert topo["layers"] == [["only_task"]]


def test_topology_linear_chain() -> None:
    """Linear chain: width=1, depth=3, three layers."""
    code = """\
async def step_a() -> A:
    \"\"\"First.\"\"\"

async def step_b(a: A) -> B:
    \"\"\"Second.\"\"\"

async def step_c(b: B) -> C:
    \"\"\"Third.\"\"\"

async def execute():
    a = await step_a()
    b = await step_b(a)
    c = await step_c(b)
"""
    topo = compute_topology(code)
    assert topo["width"] == 1
    assert topo["depth"] == 3
    assert topo["layer_count"] == 3
    assert topo["gather_groups"] == []
    assert topo["layers"] == [["step_a"], ["step_b"], ["step_c"]]


def test_topology_wide_gather() -> None:
    """Wide gather: width=3, gather_groups=[3]."""
    code = """\
async def task_a() -> A:
    \"\"\"A.\"\"\"

async def task_b() -> B:
    \"\"\"B.\"\"\"

async def task_c() -> C:
    \"\"\"C.\"\"\"

async def combine(a: A, b: B, c: C) -> Result:
    \"\"\"Combine.\"\"\"

async def execute():
    a, b, c = await gather(task_a(), task_b(), task_c())
    r = await combine(a, b, c)
"""
    topo = compute_topology(code)
    assert topo["width"] == 3
    assert topo["depth"] == 2
    assert topo["layer_count"] == 2
    assert topo["gather_groups"] == [3]
    assert topo["layers"] == [["task_a", "task_b", "task_c"], ["combine"]]


def test_topology_diamond() -> None:
    """Diamond: two paths converge -- task with multiple deps in correct layer."""
    code = """\
async def start() -> S:
    \"\"\"Start.\"\"\"

async def left(s: S) -> L:
    \"\"\"Left path.\"\"\"

async def right(s: S) -> R:
    \"\"\"Right path.\"\"\"

async def join(l: L, r: R) -> J:
    \"\"\"Join.\"\"\"

async def execute():
    s = await start()
    l, r = await gather(left(s), right(s))
    j = await join(l, r)
"""
    topo = compute_topology(code)
    assert topo["width"] == 2
    assert topo["depth"] == 3
    assert topo["layer_count"] == 3
    assert topo["gather_groups"] == [2]
    # start at layer 0, left+right at layer 1, join at layer 2
    assert topo["layers"] == [["start"], ["left", "right"], ["join"]]


def test_topology_mixed_gather_then_serial() -> None:
    """Mixed: gather followed by serial steps."""
    code = """\
async def task_a() -> A:
    \"\"\"A.\"\"\"

async def task_b() -> B:
    \"\"\"B.\"\"\"

async def merge(a: A, b: B) -> M:
    \"\"\"Merge.\"\"\"

async def final(m: M) -> F:
    \"\"\"Final.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    m = await merge(a, b)
    f = await final(m)
"""
    topo = compute_topology(code)
    assert topo["width"] == 2
    assert topo["depth"] == 3
    assert topo["layer_count"] == 3
    assert topo["gather_groups"] == [2]
    assert topo["layers"] == [["task_a", "task_b"], ["merge"], ["final"]]


def test_topology_empty_source() -> None:
    """Empty source returns zeros."""
    topo = compute_topology("")
    assert topo["width"] == 0
    assert topo["depth"] == 0
    assert topo["layer_count"] == 0
    assert topo["gather_groups"] == []
    assert topo["layers"] == []


def test_topology_syntax_error() -> None:
    """Malformed source returns zeros gracefully."""
    topo = compute_topology("def foo(")
    assert topo["width"] == 0
    assert topo["depth"] == 0
    assert topo["gather_groups"] == []
    assert topo["layers"] == []


def test_topology_alphabetical_within_layers() -> None:
    """Tasks are sorted alphabetically within each layer."""
    code = """\
async def zebra() -> Z:
    \"\"\"Z.\"\"\"

async def alpha() -> A:
    \"\"\"A.\"\"\"

async def middle() -> M:
    \"\"\"M.\"\"\"

async def finish(z: Z, a: A, m: M) -> F:
    \"\"\"Finish.\"\"\"

async def execute():
    z, a, m = await gather(zebra(), alpha(), middle())
    f = await finish(z, a, m)
"""
    topo = compute_topology(code)
    # Layer 0 should be alphabetically sorted
    assert topo["layers"][0] == ["alpha", "middle", "zebra"]
    assert topo["layers"][1] == ["finish"]


def test_topology_no_entry_point() -> None:
    """Source without execute() still computes layers from sigs/deps."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"
"""
    topo = compute_topology(code)
    # Without entry point, extract_dag_data still returns sigs but
    # deps have no call-graph edges (only @requires edges if present).
    # Both tasks have no deps resolved from call graph, so both in layer 0.
    assert topo["width"] == 2
    assert topo["depth"] == 1
    assert topo["gather_groups"] == []
    assert topo["layers"] == [["task_a", "task_b"]]


def test_topology_requires_decorator_ordering() -> None:
    """@requires edges affect layer assignment."""
    code = """\
async def setup() -> Schema:
    \"\"\"Setup.\"\"\"

@requires("setup")
async def migrate() -> Migrated:
    \"\"\"Migrate after setup.\"\"\"

async def seed(m: Migrated) -> Seeded:
    \"\"\"Seed.\"\"\"

async def execute():
    s = await setup()
    m = await migrate()
    d = await seed(m)
"""
    topo = compute_topology(code)
    # setup in layer 0, migrate in layer 1 (due to @requires("setup")),
    # seed in layer 2 (due to data dep on migrate via call graph)
    assert "setup" in topo["layers"][0]
    assert "migrate" in topo["layers"][1]


def test_topology_multiple_gather_groups() -> None:
    """Multiple gather() calls produce multiple group sizes."""
    code = """\
async def a1() -> A1:
    \"\"\"A1.\"\"\"

async def a2() -> A2:
    \"\"\"A2.\"\"\"

async def b1(x: A1) -> B1:
    \"\"\"B1.\"\"\"

async def b2(x: A2) -> B2:
    \"\"\"B2.\"\"\"

async def b3(x: A2) -> B3:
    \"\"\"B3.\"\"\"

async def final(x: B1, y: B2, z: B3) -> F:
    \"\"\"Final.\"\"\"

async def execute():
    x, y = await gather(a1(), a2())
    p, q, r = await gather(b1(x), b2(y), b3(y))
    f = await final(p, q, r)
"""
    topo = compute_topology(code)
    assert topo["gather_groups"] == [2, 3]


def test_topology_canonical_composition() -> None:
    """The canonical DB-02 example produces expected topology."""
    topo = compute_topology(VALID_COMPOSITION)
    # verify is excluded from _extract_sigs? No -- verify IS a task sig
    # (it's an async def that's not in _ENTRY_NAMES).
    # Layer 0: setup_database, scaffold_frontend (no deps)
    # Layer 1: implement_user_model (deps: setup_database)
    # Layer 2: implement_auth (deps: implement_user_model)
    # Layer 3: implement_api (deps: implement_user_model, implement_auth)
    # Layer 4: wire_frontend (deps: implement_api, scaffold_frontend)
    # Layer 5: verify (deps: wire_frontend)
    assert topo["width"] == 2  # scaffold_frontend + setup_database
    assert topo["depth"] >= 4
    assert topo["gather_groups"] == [2]  # one gather with 2 args


# ---------------------------------------------------------------------------
# get_colayer_tasks
# ---------------------------------------------------------------------------


def test_get_colayer_tasks_gather_group() -> None:
    """get_colayer_tasks returns all tasks in the same DAG layer."""
    code = """\
async def task_a() -> A:
    \"\"\"A.\"\"\"

async def task_b() -> B:
    \"\"\"B.\"\"\"

async def combine(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await combine(a, b)
"""
    peers = get_colayer_tasks(code, "task_a")
    assert set(peers) == {"task_a", "task_b"}
    peers_b = get_colayer_tasks(code, "task_b")
    assert set(peers_b) == {"task_a", "task_b"}


def test_get_colayer_tasks_single_task_layer() -> None:
    """A task alone in its layer returns just itself."""
    code = """\
async def task_a() -> A:
    \"\"\"A.\"\"\"

async def task_b() -> B:
    \"\"\"B.\"\"\"

async def combine(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await combine(a, b)
"""
    peers = get_colayer_tasks(code, "combine")
    assert peers == ["combine"]


def test_get_colayer_tasks_unknown_task() -> None:
    """Unknown task_name returns [task_name] as fallback."""
    code = """\
async def task_a() -> A:
    \"\"\"A.\"\"\"

async def execute():
    a = await task_a()
"""
    peers = get_colayer_tasks(code, "nonexistent")
    assert peers == ["nonexistent"]


def test_get_colayer_tasks_parse_error() -> None:
    """Syntax errors return [task_name] as fallback."""
    peers = get_colayer_tasks("this is not valid python {{{}}", "some_task")
    assert peers == ["some_task"]


def test_get_colayer_tasks_serial_graph() -> None:
    """In a fully serial graph, each task is alone in its layer."""
    code = """\
async def setup() -> Schema:
    \"\"\"Setup.\"\"\"

async def build(s: Schema) -> App:
    \"\"\"Build.\"\"\"

async def deploy(a: App) -> Result:
    \"\"\"Deploy.\"\"\"

async def execute():
    s = await setup()
    a = await build(s)
    r = await deploy(a)
"""
    assert get_colayer_tasks(code, "setup") == ["setup"]
    assert get_colayer_tasks(code, "build") == ["build"]
    assert get_colayer_tasks(code, "deploy") == ["deploy"]


# ---------------------------------------------------------------------------
# Gather consumption advisory
# ---------------------------------------------------------------------------


def test_gather_unconsumed_warning() -> None:
    """gather() results assigned but never used downstream emit a warning."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def task_c() -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await task_c()
"""
    errors = validate(code)
    assert any("gather() results not consumed" in e.message for e in errors)


def test_gather_consumed_no_warning() -> None:
    """gather() results used by a downstream task do not emit a warning."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def combine(a: A, b: B) -> C:
    \"\"\"Combine.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    c = await combine(a, b)
"""
    errors = validate(code)
    assert not any("gather() results not consumed" in e.message for e in errors)


def test_no_gather_no_consumption_warning() -> None:
    """Compose with no gather() does not emit a gather consumption warning."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b(a: A) -> B:
    \"\"\"Do B.\"\"\"

async def task_c(b: B) -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
    c = await task_c(b)
"""
    errors = validate(code)
    assert not any("gather() results not consumed" in e.message for e in errors)


def test_gather_consumed_in_later_gather() -> None:
    """gather() results used in a subsequent gather() count as consumed."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def enhance_a(a: A) -> EA:
    \"\"\"Enhance A.\"\"\"

async def enhance_b(b: B) -> EB:
    \"\"\"Enhance B.\"\"\"

async def finalize(ea: EA, eb: EB) -> Done:
    \"\"\"Finalize.\"\"\"

async def execute():
    a, b = await gather(task_a(), task_b())
    ea, eb = await gather(enhance_a(a), enhance_b(b))
    d = await finalize(ea, eb)
"""
    errors = validate(code)
    assert not any("gather() results not consumed" in e.message for e in errors)


def test_gather_bare_await_no_warning() -> None:
    """await gather(...) with no assignment does not trigger the check."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def task_c() -> C:
    \"\"\"Do C.\"\"\"

async def execute():
    await gather(task_a(), task_b())
    c = await task_c()
"""
    errors = validate(code)
    assert not any("gather() results not consumed" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Typed ValidationResult contract
# ---------------------------------------------------------------------------


def test_validate_returns_typed_results() -> None:
    """Every validate() result is a ValidationResult with valid severity."""
    results = validate(VALID_COMPOSITION)
    for r in results:
        assert isinstance(r, ValidationResult)
        assert r.severity in ("error", "advisory")


def test_validation_result_str_returns_message() -> None:
    """ValidationResult.__str__ returns the message for backward compat."""
    r = ValidationResult("error", "some error message")
    assert str(r) == "some error message"


def test_errors_have_error_severity() -> None:
    """Hard errors carry severity='error'."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
"""
    results = validate(code)
    undefined = [r for r in results if "Undefined" in r.message]
    assert len(undefined) >= 1
    assert all(r.severity == "error" for r in undefined)


def test_advisories_have_advisory_severity() -> None:
    """Width warnings carry severity='advisory'."""
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def task_b() -> B:
    \"\"\"Do B.\"\"\"

async def task_c() -> C:
    \"\"\"Do C.\"\"\"

async def task_d() -> D:
    \"\"\"Do D.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b()
    c = await task_c()
    d = await task_d()
"""
    results = validate(code)
    width_warnings = [r for r in results if "No concurrent phases" in r.message]
    assert len(width_warnings) >= 1
    assert all(r.severity == "advisory" for r in width_warnings)


# ---------------------------------------------------------------------------
# Roadmap annotation parsing (DB-08 markdown format)
# ---------------------------------------------------------------------------

VALID_ROADMAP_MD = """\
# Roadmap

## Milestones

### 1. project-setup
**Status:** completed
**Summary:** Initialize project structure, CI/CD, and development environment
**Completed:** 2026-03-18

### 2. user-authentication
**Status:** in_progress
**Summary:** User registration, login, session management
**Started:** 2026-03-19

### 3. dashboard
**Status:** pending
**Summary:** Main dashboard with analytics widgets
**Depends on:** user-authentication

### 4. payment-integration
**Status:** pending
**Summary:** Stripe integration for subscription billing
**Depends on:** user-authentication
**Independent of:** dashboard (candidate for parallel coordinator)

## Ordering
Default: sequential.
"""

ROADMAP_NO_ANNOTATIONS = """\
# Roadmap

## Milestones

### 1. alpha
**Status:** pending
**Summary:** First milestone

### 2. beta
**Status:** pending
**Summary:** Second milestone

### 3. gamma
**Status:** pending
**Summary:** Third milestone
"""


class TestParseRoadmapAnnotations:
    """Tests for parse_roadmap_annotations()."""

    def test_valid_roadmap_parses_all_milestones(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        assert set(graph.milestones) == {
            "project-setup",
            "user-authentication",
            "dashboard",
            "payment-integration",
        }
        assert graph.order == (
            "project-setup",
            "user-authentication",
            "dashboard",
            "payment-integration",
        )

    def test_status_values_extracted(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        assert graph.milestones["project-setup"].status == "completed"
        assert graph.milestones["user-authentication"].status == "in_progress"
        assert graph.milestones["dashboard"].status == "pending"

    def test_depends_on_extracted(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        assert graph.milestones["dashboard"].depends_on == ("user-authentication",)
        assert graph.milestones["payment-integration"].depends_on == (
            "user-authentication",
        )
        assert graph.milestones["project-setup"].depends_on == ()

    def test_independent_of_extracted(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        assert graph.milestones["payment-integration"].independent_of == ("dashboard",)
        assert graph.milestones["dashboard"].independent_of == ()

    def test_independent_of_strips_parenthetical(self) -> None:
        md = """\
### 1. a
**Status:** pending
**Independent of:** b (some note), c (another note)
### 2. b
**Status:** pending
### 3. c
**Status:** pending
"""
        graph = parse_roadmap_annotations(md)
        assert graph.milestones["a"].independent_of == ("b", "c")

    def test_no_annotations_all_empty(self) -> None:
        graph = parse_roadmap_annotations(ROADMAP_NO_ANNOTATIONS)
        assert len(graph.milestones) == 3
        for entry in graph.milestones.values():
            assert entry.depends_on == ()
            assert entry.independent_of == ()

    def test_empty_input(self) -> None:
        graph = parse_roadmap_annotations("")
        assert graph.milestones == {}
        assert graph.order == ()

    def test_no_milestone_headers(self) -> None:
        graph = parse_roadmap_annotations("# Roadmap\nJust some text.\n")
        assert graph.milestones == {}

    def test_summary_extracted(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        assert "analytics" in graph.milestones["dashboard"].summary.lower()

    def test_multiple_depends_on(self) -> None:
        md = """\
### 1. alpha
**Status:** completed
### 2. beta
**Status:** completed
### 3. gamma
**Status:** pending
**Depends on:** alpha, beta
"""
        graph = parse_roadmap_annotations(md)
        assert graph.milestones["gamma"].depends_on == ("alpha", "beta")

    def test_milestone_entry_is_frozen(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        entry = graph.milestones["dashboard"]
        assert isinstance(entry, MilestoneEntry)
        try:
            entry.name = "changed"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_roadmap_graph_is_frozen(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        try:
            graph.order = ()  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestValidateRoadmapAnnotations:
    """Tests for validate_roadmap_annotations()."""

    def test_valid_graph_no_errors(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        errors = validate_roadmap_annotations(graph)
        assert errors == []

    def test_dangling_depends_on(self) -> None:
        md = """\
### 1. alpha
**Status:** pending
**Depends on:** nonexistent
"""
        graph = parse_roadmap_annotations(md)
        errors = validate_roadmap_annotations(graph)
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert "nonexistent" in errors[0].message
        assert "Dangling dependency" in errors[0].message

    def test_dangling_independent_of(self) -> None:
        md = """\
### 1. alpha
**Status:** pending
**Independent of:** ghost
"""
        graph = parse_roadmap_annotations(md)
        errors = validate_roadmap_annotations(graph)
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert "ghost" in errors[0].message
        assert "Dangling reference" in errors[0].message

    def test_direct_cycle(self) -> None:
        md = """\
### 1. alpha
**Status:** pending
**Depends on:** beta
### 2. beta
**Status:** pending
**Depends on:** alpha
"""
        graph = parse_roadmap_annotations(md)
        errors = validate_roadmap_annotations(graph)
        cycle_errors = [e for e in errors if "cycle" in e.message.lower()]
        assert len(cycle_errors) == 1
        assert cycle_errors[0].severity == "error"

    def test_transitive_cycle(self) -> None:
        md = """\
### 1. alpha
**Status:** pending
**Depends on:** gamma
### 2. beta
**Status:** pending
**Depends on:** alpha
### 3. gamma
**Status:** pending
**Depends on:** beta
"""
        graph = parse_roadmap_annotations(md)
        errors = validate_roadmap_annotations(graph)
        cycle_errors = [e for e in errors if "cycle" in e.message.lower()]
        assert len(cycle_errors) == 1

    def test_consistency_contradiction(self) -> None:
        md = """\
### 1. alpha
**Status:** pending
**Depends on:** beta
**Independent of:** beta
### 2. beta
**Status:** pending
"""
        graph = parse_roadmap_annotations(md)
        errors = validate_roadmap_annotations(graph)
        contradiction_errors = [
            e for e in errors if "Contradiction" in e.message
        ]
        assert len(contradiction_errors) == 1
        assert contradiction_errors[0].severity == "error"
        assert "alpha" in contradiction_errors[0].message
        assert "beta" in contradiction_errors[0].message

    def test_name_resolution_with_dirs(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        dirs = ["project-setup", "user-authentication"]
        errors = validate_roadmap_annotations(graph, milestone_dirs=dirs)
        # dashboard and payment-integration have no dirs.
        advisory = [e for e in errors if e.severity == "advisory"]
        assert len(advisory) == 2
        names_in_messages = [e.message for e in advisory]
        assert any("dashboard" in m for m in names_in_messages)
        assert any("payment-integration" in m for m in names_in_messages)

    def test_name_resolution_all_present(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        dirs = [
            "project-setup",
            "user-authentication",
            "dashboard",
            "payment-integration",
        ]
        errors = validate_roadmap_annotations(graph, milestone_dirs=dirs)
        assert errors == []

    def test_name_resolution_none_skips_check(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        errors = validate_roadmap_annotations(graph, milestone_dirs=None)
        assert errors == []

    def test_no_annotations_is_valid(self) -> None:
        graph = parse_roadmap_annotations(ROADMAP_NO_ANNOTATIONS)
        errors = validate_roadmap_annotations(graph)
        assert errors == []


class TestComputeIndependentSets:
    """Tests for compute_independent_sets()."""

    def test_valid_graph_with_independence(self) -> None:
        graph = parse_roadmap_annotations(VALID_ROADMAP_MD)
        layers = compute_independent_sets(graph)
        # project-setup and user-authentication have no deps -> layer 0.
        assert sorted(layers[0]) == ["project-setup", "user-authentication"]
        # dashboard and payment-integration both depend on
        # user-authentication. With independence annotation, they
        # should share a layer.
        found_parallel = False
        for layer in layers:
            if "dashboard" in layer and "payment-integration" in layer:
                found_parallel = True
                break
        assert found_parallel, f"Expected parallel layer, got {layers}"

    def test_no_annotations_fully_serial(self) -> None:
        graph = parse_roadmap_annotations(ROADMAP_NO_ANNOTATIONS)
        layers = compute_independent_sets(graph)
        # Without independence annotations -> one per layer.
        assert all(len(layer) == 1 for layer in layers)
        assert len(layers) == 3
        names = [layer[0] for layer in layers]
        assert set(names) == {"alpha", "beta", "gamma"}

    def test_single_milestone(self) -> None:
        md = """\
### 1. solo
**Status:** pending
**Summary:** Only one milestone
"""
        graph = parse_roadmap_annotations(md)
        layers = compute_independent_sets(graph)
        assert layers == [["solo"]]

    def test_all_independent(self) -> None:
        md = """\
### 1. alpha
**Status:** pending
**Independent of:** beta, gamma
### 2. beta
**Status:** pending
**Independent of:** alpha, gamma
### 3. gamma
**Status:** pending
**Independent of:** alpha, beta
"""
        graph = parse_roadmap_annotations(md)
        layers = compute_independent_sets(graph)
        # All should be in one layer since none depends on any other.
        assert len(layers) == 1
        assert sorted(layers[0]) == ["alpha", "beta", "gamma"]

    def test_empty_graph(self) -> None:
        graph = parse_roadmap_annotations("")
        layers = compute_independent_sets(graph)
        assert layers == []

    def test_chain_dependency(self) -> None:
        md = """\
### 1. alpha
**Status:** completed
**Independent of:** gamma
### 2. beta
**Status:** pending
**Depends on:** alpha
**Independent of:** gamma
### 3. gamma
**Status:** pending
**Depends on:** beta
"""
        graph = parse_roadmap_annotations(md)
        layers = compute_independent_sets(graph)
        # alpha -> beta -> gamma: strict chain.
        assert layers[0] == ["alpha"]
        assert layers[1] == ["beta"]
        assert layers[2] == ["gamma"]

    def test_diamond_dependency(self) -> None:
        md = """\
### 1. root
**Status:** completed
### 2. left
**Status:** pending
**Depends on:** root
**Independent of:** right
### 3. right
**Status:** pending
**Depends on:** root
**Independent of:** left
### 4. join
**Status:** pending
**Depends on:** left, right
"""
        graph = parse_roadmap_annotations(md)
        layers = compute_independent_sets(graph)
        assert layers[0] == ["root"]
        assert sorted(layers[1]) == ["left", "right"]
        assert layers[2] == ["join"]


class TestValidateRoadmapPublicAPI:
    """Tests for the updated validate_roadmap() public API."""

    def test_valid_markdown_roadmap(self) -> None:
        errors = validate_roadmap(VALID_ROADMAP_MD)
        assert errors == []

    def test_markdown_with_cycle(self) -> None:
        md = """\
### 1. a
**Status:** pending
**Depends on:** b
### 2. b
**Status:** pending
**Depends on:** a
"""
        errors = validate_roadmap(md)
        assert any("cycle" in e.message.lower() for e in errors)

    def test_empty_roadmap(self) -> None:
        errors = validate_roadmap("")
        # Legacy fallback or error.
        assert len(errors) >= 1


class TestExtractRoadmapDataUpdated:
    """Tests for the updated extract_roadmap_data()."""

    def test_markdown_extraction(self) -> None:
        tasks, deps = extract_roadmap_data(VALID_ROADMAP_MD)
        names = {t["name"] for t in tasks}
        assert "dashboard" in names
        assert "payment-integration" in names
        assert deps["dashboard"] == ["user-authentication"]

    def test_status_in_tasks(self) -> None:
        tasks, _ = extract_roadmap_data(VALID_ROADMAP_MD)
        status_map = {t["name"]: t["status"] for t in tasks}
        assert status_map["project-setup"] == "completed"
        assert status_map["user-authentication"] == "in_progress"
