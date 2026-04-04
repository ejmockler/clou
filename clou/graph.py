"""Compose.py call graph validation.

Validates milestone composition files against seven structural properties:

1. Well-formedness \u2014 valid Python syntax
2. Completeness \u2014 every called function is defined
3. Acyclicity \u2014 no circular dependencies (type-based DFS)
4. Type compatibility \u2014 output types match downstream input types
5. Convergence \u2014 execute() entry point exists, all tasks are reachable
6. Width \u2014 graphs with >2 tasks should use gather() for concurrency
7. Minimum decomposition \u2014 at least 3 substantive phases required

The compose.py format (DB-02) encodes tasks as async function definitions
and the execution plan as the body of execute(). This module validates the
call graph using Python's ast module \u2014 no runtime execution, no external
dependencies.

Decorator edges:
    @requires("phase_name") \u2014 ordering constraint (no data flow)
    @needs("path/to/artifact") \u2014 artifact dependency (environmental)

Public API:
    validate(source: str) -> list[str]
    compute_topology(source: str) -> dict[str, Any]
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResourceBounds:
    """Per-task resource limits from @resource_bounds decorator."""

    tokens: int | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class Sig:
    """A task function's signature extracted from compose.py."""

    name: str
    params: tuple[tuple[str, str | None], ...]
    return_type: str | None
    resource_bounds: ResourceBounds | None = None


# (func_name, [(position, variable_name)], [target_variable])
type _Call = tuple[str, list[tuple[int, str]], list[str]]

# gather() is a concurrency primitive, not a task definition.
_BUILTINS = frozenset({"gather"})

_ENTRY_NAMES = ("execute", "execute_milestone")

# Functions excluded from the task count for minimum decomposition.
_NON_TASK_NAMES = frozenset({*_ENTRY_NAMES, "verify"})

_CONTROL_FLOW = (
    ast.If,
    ast.For,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.AsyncFor,
    ast.Match,
)


def extract_dag_data(
    source: str,
) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    """Extract task names and dependency info from compose.py source.

    Returns ``(tasks, deps)`` where *tasks* is a list of dicts with
    ``"name"`` and ``"status"`` keys, and *deps* maps each task name to the
    names of tasks it calls (its dependencies in the call graph).
    """
    tree = ast.parse(source)
    sigs = _extract_sigs(tree)
    entry = _find_entry(tree)

    tasks: list[dict[str, str]] = []
    for name, sig in sigs.items():
        task: dict[str, str] = {"name": name, "status": "pending"}
        if sig.resource_bounds is not None:
            bounds_dict: dict[str, int] = {}
            if sig.resource_bounds.tokens is not None:
                bounds_dict["tokens"] = sig.resource_bounds.tokens
            if sig.resource_bounds.timeout_seconds is not None:
                bounds_dict["timeout_seconds"] = sig.resource_bounds.timeout_seconds
            task["resource_bounds"] = bounds_dict  # type: ignore[assignment]
        tasks.append(task)

    deps: dict[str, list[str]] = {name: [] for name in sigs}
    if entry is not None:
        calls, _ = _walk_entry(entry, sigs)
        # Build a map: for each variable, which task produced it
        var_producer: dict[str, str] = {}
        for func, _, targets in calls:
            if func in sigs:
                for var in targets:
                    var_producer[var] = func
        # For each call, find which tasks produced its arguments
        for func, args, _ in calls:
            if func not in sigs:
                continue
            for _, var in args:
                producer = var_producer.get(var)
                if producer and producer != func and producer not in deps[func]:
                    deps[func].append(producer)

    # Add @requires decorator edges (ordering constraints)
    requires_edges, _ = _extract_decorator_edges(tree)
    for func_name, dep_name in requires_edges:
        if func_name in deps and dep_name in sigs:
            if dep_name not in deps[func_name]:
                deps[func_name].append(dep_name)

    return tasks, deps


def validate(source: str) -> list[str]:
    """Validate a compose.py call graph. Returns errors (empty = valid)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    sigs = _extract_sigs(tree)
    errors: list[str] = []

    # Reject sync entry points (must be async)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _ENTRY_NAMES:
            errors.append(
                f"Entry point must be async: '{node.name}' at line {node.lineno}"
            )

    # Reject duplicate entry point definitions
    entry_defs: dict[str, list[int]] = {}
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in _ENTRY_NAMES
        ):
            entry_defs.setdefault(node.name, []).append(node.lineno)
    for name, lines in entry_defs.items():
        if len(lines) > 1:
            line_str = ", ".join(str(ln) for ln in lines)
            errors.append(f"Duplicate entry point '{name}' defined at lines {line_str}")

    entry = _find_entry(tree)
    if entry is None:
        errors.append("Missing execute() entry point")
        return errors

    if errors:
        return errors

    # Reject control flow in execute body
    for stmt in entry.body:
        if isinstance(stmt, _CONTROL_FLOW):
            errors.append(
                f"Control flow in execute body not supported: "
                f"{type(stmt).__name__} at line {stmt.lineno}"
            )

    calls, var_types = _walk_entry(entry, sigs)

    # Track called names -> line numbers
    called: dict[str, int] = {}
    for func, _, _ in calls:
        if func not in called:
            called[func] = _call_lineno(entry, func)
    called_names = set(called)
    known = _module_names(tree) | _BUILTINS

    # Build lineno map for defined task sigs
    sig_linenos: dict[str, int] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in sigs:
            sig_linenos[node.name] = node.lineno

    # Completeness: every called function is defined or imported
    for name in sorted(called_names - known):
        ln = called[name]
        errors.append(f"Undefined: {name} (line {ln})")

    # Convergence: every defined task function is called
    for name in sorted(set(sigs) - called_names):
        ln = sig_linenos.get(name, 0)
        errors.append(f"Unused: {name} (line {ln})")

    # Acyclicity: no circular type dependencies
    cycle = _find_cycle(_type_deps(sigs))
    if cycle:
        first = cycle[0]
        ln = sig_linenos.get(first, 0)
        errors.append(f"Cycle: {' \u2192 '.join(cycle)} (line {ln})")

    # Type compatibility: argument types match parameter annotations
    errors += _check_types(sigs, calls, var_types)

    # Parse decorator edges (validates without erroring on their presence)
    _extract_decorator_edges(tree)

    # Resource bounds: values must be positive integers when present
    errors += _check_resource_bounds(sigs, sig_linenos)

    # Width: multi-task graphs should use gather() for concurrency
    errors += _check_width(sigs, entry, calls)

    # Minimum decomposition: at least 3 substantive phases
    errors += _check_min_decomposition(sigs)

    return errors


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------


def _extract_sigs(tree: ast.Module) -> dict[str, Sig]:
    """Extract task function signatures from module-level async defs."""
    sigs: dict[str, Sig] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name not in _ENTRY_NAMES:
            params = tuple(
                (
                    a.arg,
                    ast.unparse(a.annotation) if a.annotation else None,
                )
                for a in node.args.args
            )
            ret = ast.unparse(node.returns) if node.returns else None
            bounds = _extract_resource_bounds(node)
            sigs[node.name] = Sig(node.name, params, ret, bounds)
    return sigs


def _extract_resource_bounds(
    node: ast.AsyncFunctionDef,
) -> ResourceBounds | None:
    """Extract @resource_bounds(...) decorator kwargs from a function def."""
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if _call_name(dec) != "resource_bounds":
            continue
        tokens: int | None = None
        timeout_seconds: int | None = None
        for kw in dec.keywords:
            val = _const_int(kw.value)
            if val is None:
                continue
            if kw.arg == "tokens":
                tokens = val
            elif kw.arg == "timeout_seconds":
                timeout_seconds = val
        if tokens is not None or timeout_seconds is not None:
            return ResourceBounds(tokens=tokens, timeout_seconds=timeout_seconds)
    return None


def _const_int(node: ast.expr) -> int | None:
    """Extract an integer constant from an AST node, handling unary minus."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
    ):
        return -node.operand.value
    return None


def _extract_decorator_edges(
    tree: ast.Module,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Extract @requires and @needs decorator edges from async defs.

    Returns ``(requires_edges, needs_edges)`` where each edge is
    ``(function_name, dependency_name_or_path)``.
    """
    requires: list[tuple[str, str]] = []
    needs: list[tuple[str, str]] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            dec_name = _call_name(dec)
            if dec_name == "requires" and dec.args:
                arg = dec.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    requires.append((node.name, arg.value))
            elif dec_name == "needs" and dec.args:
                arg = dec.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    needs.append((node.name, arg.value))
    return requires, needs


def _has_gather(entry: ast.AsyncFunctionDef) -> bool:
    """Check whether execute() contains any gather() calls."""
    for node in ast.walk(entry):
        if isinstance(node, ast.Call) and _call_name(node) == "gather":
            return True
    return False


def _check_width(
    sigs: dict[str, Sig],
    entry: ast.AsyncFunctionDef,
    calls: list[_Call],
) -> list[str]:
    """Warn if a multi-task graph has no concurrent phases.

    If there are more than 2 task functions and no gather() call in
    execute(), emit a warning -- unless every task consumes the output of
    the previous one (fully serial data flow).
    """
    task_names = [n for n in sigs if n not in _NON_TASK_NAMES]
    if len(task_names) <= 2:
        return []
    if _has_gather(entry):
        return []

    # Check for fully serial data flow: every call (after the first) takes
    # a variable produced by the immediately preceding call.
    task_calls = [c for c in calls if c[0] in sigs and c[0] not in _NON_TASK_NAMES]
    if len(task_calls) >= 2:
        all_serial = True
        for i in range(1, len(task_calls)):
            prev_targets = set(task_calls[i - 1][2])
            curr_arg_vars = {var for _, var in task_calls[i][1]}
            if not curr_arg_vars or not (curr_arg_vars & prev_targets):
                all_serial = False
                break
        if all_serial:
            return []

    n = len(task_names)
    return [
        f"No concurrent phases in a {n}-task graph "
        f"\u2014 verify all tasks have sequential data dependencies"
    ]


def _check_min_decomposition(sigs: dict[str, Sig]) -> list[str]:
    """Error if there are fewer than 3 substantive task phases."""
    task_count = sum(1 for n in sigs if n not in _NON_TASK_NAMES)
    if task_count <= 2:
        return [
            "Under-decomposed milestone "
            "\u2014 identify at least 3 substantive phases"
        ]
    return []


def _check_resource_bounds(
    sigs: dict[str, Sig],
    sig_linenos: dict[str, int],
) -> list[str]:
    """Validate @resource_bounds values are positive when present."""
    errors: list[str] = []
    for sig in sigs.values():
        if sig.resource_bounds is None:
            continue
        ln = sig_linenos.get(sig.name, 0)
        if sig.resource_bounds.tokens is not None and sig.resource_bounds.tokens <= 0:
            errors.append(
                f"Invalid resource_bounds on {sig.name}: "
                f"tokens must be positive (line {ln})"
            )
        if (
            sig.resource_bounds.timeout_seconds is not None
            and sig.resource_bounds.timeout_seconds <= 0
        ):
            errors.append(
                f"Invalid resource_bounds on {sig.name}: "
                f"timeout_seconds must be positive (line {ln})"
            )
    return errors


def _find_entry(tree: ast.Module) -> ast.AsyncFunctionDef | None:
    """Find the async execute() or execute_milestone() entry point."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _ENTRY_NAMES:
            return node
    return None


def _call_lineno(entry: ast.AsyncFunctionDef, func_name: str) -> int:
    """Find the line number where func_name is called in the entry body."""
    for node in ast.walk(entry):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == func_name:
                return node.lineno
    return entry.lineno


def _module_names(tree: ast.Module) -> set[str]:
    """All names defined or imported at module level."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


# ---------------------------------------------------------------------------
# Call graph extraction
# ---------------------------------------------------------------------------


def _walk_entry(
    entry: ast.AsyncFunctionDef,
    sigs: dict[str, Sig],
) -> tuple[list[_Call], dict[str, str | None]]:
    """Extract calls and variable type map from execute().

    Returns (calls, var_types) where var_types maps variable names to the
    return type of the function that produced them.
    """
    calls: list[_Call] = []
    var_types: dict[str, str | None] = {}

    for stmt in entry.body:
        for call in _stmt_calls(stmt):
            calls.append(call)
            func, _, targets = call
            if func in sigs:
                for var in targets:
                    var_types[var] = sigs[func].return_type

    return calls, var_types


def _stmt_calls(stmt: ast.stmt) -> list[_Call]:
    """Extract task function calls from a single statement.

    Only awaited calls are considered task dispatches. Non-awaited calls
    (helpers like load_requirements()) are ignored \u2014 they are not part of
    the task graph.
    """
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Await):
        val = stmt.value.value
        if not isinstance(val, ast.Call):
            return []
        name = _call_name(val)
        if name == "gather":
            targets = _target_names(stmt.targets[0])
            return [
                (
                    _call_name(arg),
                    _arg_vars(arg),
                    [targets[i] if i < len(targets) else f"_g{i}"],
                )
                for i, arg in enumerate(val.args)
                if isinstance(arg, ast.Call)
            ]
        return [(name, _arg_vars(val), _target_names(stmt.targets[0]))]

    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await):
        val = stmt.value.value
        if isinstance(val, ast.Call):
            return [(_call_name(val), _arg_vars(val), [])]

    return []


def _call_name(node: ast.Call) -> str:
    """Extract the function name from a Call node."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return "<unknown>"


def _arg_vars(node: ast.Call) -> list[tuple[int, str]]:
    """Extract (position, variable_name) for positional Name arguments.

    Call arguments (like load_requirements()) are skipped \u2014 only variable
    references are tracked for type flow.
    """
    return [(i, arg.id) for i, arg in enumerate(node.args) if isinstance(arg, ast.Name)]


def _target_names(node: ast.expr) -> list[str]:
    """Extract variable names from an assignment target."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple):
        return [elt.id for elt in node.elts if isinstance(elt, ast.Name)]
    return []


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def _type_deps(sigs: dict[str, Sig]) -> dict[str, set[str]]:
    """Build dependency graph from type signatures.

    A function depends on another if it consumes a type that the other
    produces. Self-dependencies are excluded \u2014 a function that takes and
    returns the same type depends on a *different* producer of that type.
    """
    producers: dict[str, set[str]] = {}
    for sig in sigs.values():
        if sig.return_type:
            producers.setdefault(sig.return_type, set()).add(sig.name)

    deps: dict[str, set[str]] = {}
    for sig in sigs.values():
        func_deps: set[str] = set()
        for _, param_type in sig.params:
            if param_type and param_type in producers:
                func_deps |= producers[param_type] - {sig.name}
        deps[sig.name] = func_deps
    return deps


def _find_cycle(deps: dict[str, set[str]]) -> list[str] | None:
    """Detect a cycle via DFS. Returns the cycle path or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    for node in deps:
        color[node] = WHITE
        for dep in deps[node]:
            color.setdefault(dep, WHITE)

    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for dep in deps.get(node, set()):
            if color.get(dep) == GRAY:
                cycle_start = path.index(dep)
                return [*path[cycle_start:], dep]
            if color.get(dep) == WHITE:
                result = dfs(dep)
                if result:
                    return result
        path.pop()
        color[node] = BLACK
        return None

    for node in list(color):
        if color[node] == WHITE:
            result = dfs(node)
            if result:
                return result
    return None


# ---------------------------------------------------------------------------
# Type checking
# ---------------------------------------------------------------------------


def _check_types(
    sigs: dict[str, Sig],
    calls: list[_Call],
    var_types: dict[str, str | None],
) -> list[str]:
    """Check that argument types match parameter annotations."""
    errors: list[str] = []
    for func, args, _ in calls:
        if func not in sigs:
            continue
        sig = sigs[func]
        for pos, var in args:
            if pos >= len(sig.params):
                continue
            expected = sig.params[pos][1]
            actual = var_types.get(var)
            if expected and actual and expected != actual:
                errors.append(
                    f"Type mismatch: {func}() param '{sig.params[pos][0]}' "
                    f"expects {expected}, got {actual} (from '{var}')"
                )
    return errors


# ---------------------------------------------------------------------------
# Topology computation
# ---------------------------------------------------------------------------


def compute_topology(source: str) -> dict[str, Any]:
    """Compute topology metrics from compose.py source.

    Returns dict with keys:
        width: int -- max tasks at any layer (max parallelism)
        depth: int -- longest dependency chain (number of layers)
        layer_count: int -- same as depth (explicit alias)
        gather_groups: list[int] -- sizes of each gather() call
        layers: list[list[str]] -- task names grouped by layer
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {
            "width": 0,
            "depth": 0,
            "layer_count": 0,
            "gather_groups": [],
            "layers": [],
        }

    sigs = _extract_sigs(tree)
    if not sigs:
        return {
            "width": 0,
            "depth": 0,
            "layer_count": 0,
            "gather_groups": [],
            "layers": [],
        }

    _, deps = extract_dag_data(source)
    entry = _find_entry(tree)

    layers = _compute_layers(sigs, deps)
    gather_groups = _extract_gather_groups(entry) if entry is not None else []

    depth = len(layers)
    width = max(len(layer) for layer in layers) if layers else 0

    return {
        "width": width,
        "depth": depth,
        "layer_count": depth,
        "gather_groups": gather_groups,
        "layers": layers,
    }


def _compute_layers(
    sigs: dict[str, Sig],
    deps: dict[str, list[str]],
) -> list[list[str]]:
    """Compute layers via Kahn's algorithm on the dependency graph.

    Tasks with no dependencies go in layer 0. Each subsequent layer
    contains tasks whose dependencies are all in prior layers. Tasks
    are sorted alphabetically within each layer for determinism.
    """
    task_names = set(sigs)
    if not task_names:
        return []

    # Build in-degree map restricted to task functions
    remaining_deps: dict[str, set[str]] = {}
    for name in task_names:
        # Only count dependencies that are themselves task functions
        remaining_deps[name] = {d for d in deps.get(name, []) if d in task_names}

    layers: list[list[str]] = []
    assigned: set[str] = set()

    while len(assigned) < len(task_names):
        # Find tasks whose remaining dependencies are all satisfied
        layer = sorted(
            name
            for name in task_names - assigned
            if remaining_deps[name] <= assigned
        )
        if not layer:
            # Remaining tasks form a cycle -- put them all in one layer
            # to avoid infinite loop. (Cycle detection is validate()'s job.)
            layer = sorted(task_names - assigned)
        layers.append(layer)
        assigned.update(layer)

    return layers


def _extract_gather_groups(entry: ast.AsyncFunctionDef) -> list[int]:
    """Extract gather() group sizes from execute() body.

    Walks the entry function looking for gather() calls and counts
    the number of arguments in each. Returns a list of ints.
    """
    groups: list[int] = []
    for node in ast.walk(entry):
        if isinstance(node, ast.Call) and _call_name(node) == "gather":
            groups.append(len(node.args))
    return groups
