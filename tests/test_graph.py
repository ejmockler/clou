"""Tests for compose.py call graph validation.

Each test uses a compose.py snippet to exercise a specific contract.
Tests go through the public validate() API only — no implementation
details are tested directly.
"""

from clou.graph import extract_dag_data, validate

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

async def execute_milestone():
    a = await task_a()
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
    """Non-async helpers are not task functions — not flagged as unused."""
    code = """\
def load_config():
    pass

async def task_a() -> A:
    \"\"\"Uses config.\"\"\"

async def execute():
    a = await task_a()
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

async def consume(w: Widget) -> Done:
    \"\"\"Use a widget.\"\"\"

async def execute():
    w = await produce()
    d = await consume(w)
"""
    assert validate(code) == []


def test_no_annotations_no_type_errors() -> None:
    """Functions without type annotations should not produce type errors."""
    code = """\
async def task_a():
    \"\"\"No types.\"\"\"

async def task_b(x):
    \"\"\"No types.\"\"\"

async def execute():
    a = await task_a()
    b = await task_b(a)
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

async def execute():
    r = await standalone()
"""
    assert validate(code) == []


def test_standalone_await_no_assignment() -> None:
    """await without assignment (e.g., cleanup tasks)."""
    code = """\
async def cleanup() -> None:
    \"\"\"Clean up.\"\"\"

async def execute():
    await cleanup()
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
