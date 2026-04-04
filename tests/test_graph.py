"""Tests for compose.py call graph validation.

Each test uses a compose.py snippet to exercise a specific contract.
Tests go through the public validate() API only — no implementation
details are tested directly.
"""

from clou.graph import ResourceBounds, extract_dag_data, validate

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
    assert errors[0].startswith("Syntax error:")


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------


def test_missing_execute() -> None:
    code = """\
async def task_a() -> A:
    \"\"\"Do A.\"\"\"
"""
    errors = validate(code)
    assert errors == ["Missing execute() entry point"]


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
    assert any("Undefined: task_b" in e for e in errors)


def test_imported_name_not_flagged() -> None:
    code = """\
from project import load_requirements

async def task_a() -> A:
    \"\"\"Do A.\"\"\"

async def execute():
    a = await task_a()
"""
    assert not any("Undefined" in e for e in validate(code))


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
    assert any("Unused: task_b" in e for e in errors)


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
    assert any("Cycle:" in e for e in errors)


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
        "Type mismatch" in e and "expects TypeB" in e and "got TypeA" in e
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
    assert any("Entry point must be async" in e for e in errors)


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
    assert any("Undefined: task_c" in e for e in errors)
    assert any("Unused: task_b" in e for e in errors)
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
    assert any("Duplicate entry point 'execute'" in e for e in errors)


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
    assert "Entry point must be async: 'execute'" in errors[0]
    assert "line" in errors[0]


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
    assert any("Control flow in execute body not supported: If" in e for e in errors)


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
    assert any("Control flow in execute body not supported: For" in e for e in errors)


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
    undefined = [e for e in errors if "Undefined: task_b" in e]
    assert len(undefined) == 1
    assert "line" in undefined[0]


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
    unused = [e for e in errors if "Unused: task_b" in e]
    assert len(unused) == 1
    assert "line" in unused[0]


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
    cycle = [e for e in errors if "Cycle:" in e]
    assert len(cycle) == 1
    assert "line" in cycle[0]


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
    assert any("No concurrent phases" in e and "4-task graph" in e for e in errors)


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
    assert not any("No concurrent phases" in e for e in errors)


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
    assert not any("No concurrent phases" in e for e in errors)


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
    assert any("Under-decomposed milestone" in e for e in errors)


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
    assert not any("Under-decomposed" in e for e in errors)


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
    assert any("tokens must be positive" in e for e in errors)

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
    assert any("timeout_seconds must be positive" in e for e in errors2)


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
    assert not any("resource_bounds" in e for e in errors)
