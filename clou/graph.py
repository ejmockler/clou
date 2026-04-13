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
    get_colayer_tasks(source: str, task_name: str) -> list[str]
    compute_topology(source: str) -> dict[str, Any]

Roadmap (DB-08 markdown) API:
    parse_roadmap_annotations(markdown: str) -> RoadmapGraph
    validate_roadmap_annotations(graph: RoadmapGraph, ...) -> list[ValidationResult]
    compute_independent_sets(graph: RoadmapGraph) -> list[list[str]]
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """A typed validation result carrying severity and message.

    Severity is ``"error"`` (blocking) or ``"advisory"`` (informational).
    ``__str__`` returns the message for backward compatibility with code
    that converts results to strings.
    """

    severity: str  # "error" or "advisory"
    message: str

    def __str__(self) -> str:
        return self.message


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
    docstring: str | None = None
    resource_bounds: ResourceBounds | None = None


@dataclass(frozen=True, slots=True)
class MilestoneEntry:
    """A single milestone parsed from roadmap.md."""

    name: str
    status: str
    summary: str
    depends_on: tuple[str, ...]
    independent_of: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoadmapGraph:
    """Parsed roadmap.md milestone graph (DB-08 schema).

    ``milestones`` maps milestone name to its entry.
    ``order`` preserves the declaration order from roadmap.md.
    """

    milestones: dict[str, MilestoneEntry]
    order: tuple[str, ...]


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


def compute_layers(
    tasks: list[dict[str, str]],
    deps: dict[str, list[str]],
) -> list[list[str]]:
    """Group tasks into topological layers for parallel dispatch.

    Layer 0 = tasks with no dependencies. Layer N = tasks whose
    dependencies are all in layers < N. Tasks sorted alphabetically
    within each layer for determinism.

    Public API — used by prompts, UI, and telemetry modules.
    """
    from collections import deque

    task_names = {t["name"] for t in tasks}
    if not task_names:
        return []

    in_degree: dict[str, int] = {
        name: sum(1 for d in deps.get(name, []) if d in task_names)
        for name in task_names
    }
    dependents: dict[str, list[str]] = {name: [] for name in task_names}
    for name in task_names:
        for dep in deps.get(name, []):
            if dep in task_names:
                dependents[dep].append(name)

    layers: list[list[str]] = []
    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    assigned: set[str] = set()

    while queue:
        layer = sorted(queue)
        layers.append(layer)
        assigned.update(layer)
        next_queue: deque[str] = deque()
        for name in layer:
            for dep in dependents[name]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.append(dep)
        queue = next_queue

    # Handle cycles — append remaining tasks as final layer
    remaining = sorted(task_names - assigned)
    if remaining:
        layers.append(remaining)

    return layers


def get_colayer_tasks(source: str, task_name: str) -> list[str]:
    """Return all task names in the same DAG layer as *task_name*.

    Parses compose.py *source*, computes layers, and returns all task
    names sharing a layer with *task_name*.  Returns ``[task_name]`` on
    parse failure or if *task_name* is not found in any layer.
    """
    try:
        tasks, deps = extract_dag_data(source)
    except Exception:
        return [task_name]

    # _compute_layers expects sigs: dict[str, Sig] — build a minimal
    # mapping from the extract_dag_data task list.
    sigs_map: dict[str, Sig] = {
        t["name"]: Sig(t["name"], (), None, None) for t in tasks
    }
    if task_name not in sigs_map:
        return [task_name]

    layers = _compute_layers(sigs_map, deps)
    for layer in layers:
        if task_name in layer:
            return layer
    return [task_name]


def validate(
    source: str,
    *,
    intents_source: str | None = None,
) -> list[ValidationResult]:
    """Validate a compose.py call graph. Returns results (empty = valid).

    Each result carries a ``severity`` (``"error"`` or ``"advisory"``)
    so callers can distinguish blocking errors from informational warnings
    without substring matching.

    When *intents_source* is provided, also checks intent coverage
    via ``validate_coverage()``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [ValidationResult("error", f"Syntax error: {e}")]

    sigs = _extract_sigs(tree)
    errors: list[ValidationResult] = []

    # Reject sync entry points (must be async)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _ENTRY_NAMES:
            errors.append(ValidationResult(
                "error",
                f"Entry point must be async: '{node.name}' at line {node.lineno}",
            ))

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
            errors.append(ValidationResult(
                "error",
                f"Duplicate entry point '{name}' defined at lines {line_str}",
            ))

    entry = _find_entry(tree)
    if entry is None:
        errors.append(ValidationResult("error", "Missing execute() entry point"))
        return errors

    if errors:
        return errors

    # Reject control flow in execute body
    for stmt in entry.body:
        if isinstance(stmt, _CONTROL_FLOW):
            errors.append(ValidationResult(
                "error",
                f"Control flow in execute body not supported: "
                f"{type(stmt).__name__} at line {stmt.lineno}",
            ))

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
        errors.append(ValidationResult("error", f"Undefined: {name} (line {ln})"))

    # Convergence: every defined task function is called
    for name in sorted(set(sigs) - called_names):
        ln = sig_linenos.get(name, 0)
        errors.append(ValidationResult("error", f"Unused: {name} (line {ln})"))

    # Acyclicity: no circular type dependencies
    cycle = _find_cycle(_type_deps(sigs))
    if cycle:
        first = cycle[0]
        ln = sig_linenos.get(first, 0)
        errors.append(ValidationResult(
            "error", f"Cycle: {' \u2192 '.join(cycle)} (line {ln})",
        ))

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

    # Gather consumption: intent-aware convergence check
    errors += _check_gather_consumption(entry, sigs=sigs)

    # Advisory: pure serial chain with single-use types may be fabricated
    errors += _check_topology_consistency(sigs, calls)

    # Intent coverage (when intents source provided)
    if intents_source is not None:
        errors += validate_coverage(source, intents_source)

    return errors


def validate_roadmap(source: str) -> list[ValidationResult]:
    """Validate a roadmap.md milestone graph (DB-08 format).

    Parses the markdown *source* for milestone annotations, then runs
    structural validation (cycles, dangling refs, consistency).
    Returns an empty list when the roadmap is valid.

    Also accepts legacy roadmap.py Python source for backward
    compatibility: if the source fails markdown parsing (no milestones
    found) AND looks like Python, falls back to compose.py validation
    with relaxed decomposition.
    """
    graph = parse_roadmap_annotations(source)
    if graph.milestones:
        return validate_roadmap_annotations(graph)

    # Legacy fallback: try as Python source (roadmap.py format).
    try:
        all_results = validate(source)
        return [r for r in all_results if "Under-decomposed" not in r.message]
    except Exception:
        return [ValidationResult("error", "No milestones found in roadmap")]


def extract_roadmap_data(
    source: str,
) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    """Extract milestone graph from roadmap source.

    Tries DB-08 markdown format first. Falls back to legacy roadmap.py
    Python source if no milestones are found in markdown.
    """
    graph = parse_roadmap_annotations(source)
    if graph.milestones:
        tasks = [{"name": name, "status": entry.status}
                 for name, entry in graph.milestones.items()]
        deps: dict[str, list[str]] = {
            name: list(entry.depends_on)
            for name, entry in graph.milestones.items()
        }
        return tasks, deps
    return extract_dag_data(source)


# ---------------------------------------------------------------------------
# Roadmap annotation parsing (DB-08 markdown format)
# ---------------------------------------------------------------------------

_MILESTONE_RE = re.compile(
    r"^###\s+(\d+)\.\s+(.+)$",
    re.MULTILINE,
)
_FIELD_RE = re.compile(
    r"^\*\*(\w[\w\s]*?):\*\*\s*(.*)$",
    re.MULTILINE,
)


def parse_roadmap_annotations(markdown: str) -> RoadmapGraph:
    """Parse roadmap.md content and extract milestone annotations.

    Handles the DB-08 schema format::

        ### 3. dashboard
        **Status:** pending
        **Summary:** Main dashboard with analytics widgets
        **Depends on:** user-authentication
        **Independent of:** payment-integration

    Returns a ``RoadmapGraph`` with milestone names mapped to their
    parsed entries.  If the markdown contains no milestone headers,
    returns an empty graph.
    """
    # Find all milestone headers with their positions.
    headers: list[tuple[int, str, str]] = []  # (pos, number, name)
    for m in _MILESTONE_RE.finditer(markdown):
        headers.append((m.start(), m.group(1), m.group(2).strip()))

    if not headers:
        return RoadmapGraph(milestones={}, order=())

    milestones: dict[str, MilestoneEntry] = {}
    order: list[str] = []

    for i, (pos, _num, name) in enumerate(headers):
        # Extract the block between this header and the next (or end).
        end = headers[i + 1][0] if i + 1 < len(headers) else len(markdown)
        block = markdown[pos:end]

        # Parse fields from the block.
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(block):
            key = fm.group(1).strip().lower()
            val = fm.group(2).strip()
            fields[key] = val

        status = fields.get("status", "pending")
        summary = fields.get("summary", "")

        depends_raw = fields.get("depends on", "")
        depends_on = tuple(
            d.strip() for d in depends_raw.split(",") if d.strip()
        ) if depends_raw else ()

        independent_raw = fields.get("independent of", "")
        # Strip parenthetical notes like "(candidate for parallel coordinator)".
        independent_of = tuple(
            re.sub(r"\s*\(.*?\)\s*$", "", d.strip())
            for d in independent_raw.split(",")
            if d.strip()
        ) if independent_raw else ()

        entry = MilestoneEntry(
            name=name,
            status=status,
            summary=summary,
            depends_on=depends_on,
            independent_of=independent_of,
        )
        milestones[name] = entry
        order.append(name)

    return RoadmapGraph(milestones=milestones, order=tuple(order))


def validate_roadmap_annotations(
    graph: RoadmapGraph,
    milestone_dirs: list[str] | None = None,
) -> list[ValidationResult]:
    """Validate a parsed roadmap graph for structural consistency.

    Checks:
    - Cycle detection in the ``Depends on`` graph (DFS).
    - Dangling references: names in ``Depends on`` / ``Independent of``
      must exist as milestone entries.
    - Consistency: a milestone cannot both depend on and be independent
      of the same milestone.
    - Name resolution: when *milestone_dirs* is provided, milestone
      names must resolve to existing directory names.

    Returns a list of ``ValidationResult`` (empty = valid).
    """
    errors: list[ValidationResult] = []
    known = set(graph.milestones)

    # Dangling references.
    for name, entry in graph.milestones.items():
        for dep in entry.depends_on:
            if dep not in known:
                errors.append(ValidationResult(
                    "error",
                    f"Dangling dependency: '{name}' depends on "
                    f"'{dep}' which is not a milestone",
                ))
        for ind in entry.independent_of:
            if ind not in known:
                errors.append(ValidationResult(
                    "error",
                    f"Dangling reference: '{name}' declares independence "
                    f"from '{ind}' which is not a milestone",
                ))

    # Consistency: Depends on X AND Independent of X is a contradiction.
    for name, entry in graph.milestones.items():
        contradictions = set(entry.depends_on) & set(entry.independent_of)
        for c in sorted(contradictions):
            errors.append(ValidationResult(
                "error",
                f"Contradiction: '{name}' both depends on and is "
                f"independent of '{c}'",
            ))

    # Cycle detection via DFS on the depends_on graph.
    deps_graph: dict[str, set[str]] = {
        name: set(entry.depends_on) & known
        for name, entry in graph.milestones.items()
    }
    cycle = _find_cycle(deps_graph)
    if cycle:
        errors.append(ValidationResult(
            "error",
            f"Dependency cycle: {' -> '.join(cycle)}",
        ))

    # Name resolution against milestone directories.
    if milestone_dirs is not None:
        dir_set = set(milestone_dirs)
        for name in graph.milestones:
            if name not in dir_set:
                errors.append(ValidationResult(
                    "advisory",
                    f"Milestone '{name}' has no matching directory",
                ))

    return errors


def compute_independent_sets(graph: RoadmapGraph) -> list[list[str]]:
    """Compute layered sets of milestones for parallel dispatch.

    Uses the ``Depends on`` graph to compute topological layers
    (same Kahn's algorithm as ``compute_layers``).  Within each layer,
    milestones are candidates for concurrent dispatch.

    When no ``Independent of`` annotations exist anywhere in the graph,
    returns a fully serial ordering (one milestone per layer) to
    preserve backward compatibility.
    """
    known = set(graph.milestones)
    if not known:
        return []

    # Check if any independence annotations exist.
    has_independence = any(
        entry.independent_of
        for entry in graph.milestones.values()
    )

    # Build dependency graph restricted to known milestones.
    deps: dict[str, set[str]] = {
        name: set(entry.depends_on) & known
        for name, entry in graph.milestones.items()
    }

    # Kahn's algorithm for topological layers.
    in_degree: dict[str, int] = {name: len(d) for name, d in deps.items()}
    dependents: dict[str, list[str]] = {name: [] for name in known}
    for name, dep_set in deps.items():
        for dep in dep_set:
            dependents[dep].append(name)

    layers: list[list[str]] = []
    assigned: set[str] = set()

    from collections import deque

    queue: deque[str] = deque(
        n for n, d in in_degree.items() if d == 0
    )

    while queue:
        layer = sorted(queue)
        layers.append(layer)
        assigned.update(layer)
        next_queue: deque[str] = deque()
        for name in layer:
            for dep in dependents[name]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.append(dep)
        queue = next_queue

    # Handle cycles: append remaining as final layer.
    remaining = sorted(known - assigned)
    if remaining:
        layers.append(remaining)

    # If no independence annotations exist, serialize to one-per-layer.
    if not has_independence:
        serial: list[list[str]] = []
        for layer in layers:
            for name in layer:
                serial.append([name])
        return serial

    return layers


def validate_coverage(
    compose_source: str,
    intents_source: str,
) -> list[ValidationResult]:
    """Check that every intent in intents.md has a covering task in compose.py.

    Returns a list of typed results (empty = full coverage).
    Intent coverage gaps are errors; untraced tasks and missing
    docstrings are advisories.
    """
    import re

    # Extract intent identifiers from intents.md (lines like "## I1: ..."
    # or "- I1: ..." or numbered "1. ...").
    intent_pattern = re.compile(
        r"^(?:##?\s*|[-*]\s*)"          # heading or list marker
        r"(I\d+)\s*[:—–\-]?\s*(.+)",    # I<n>: description
        re.MULTILINE,
    )
    intents: dict[str, str] = {}
    for m in intent_pattern.finditer(intents_source):
        intents[m.group(1)] = m.group(2).strip()

    if not intents:
        # No structured intents found — cannot check coverage.
        return []

    # Extract task docstrings from compose.py.
    try:
        tree = ast.parse(compose_source)
    except SyntaxError:
        return []

    task_docs: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name not in _ENTRY_NAMES:
            doc = ast.get_docstring(node) or ""
            task_docs[node.name] = doc

    results: list[ValidationResult] = []

    # Check each intent has a covering task.
    for intent_id, intent_desc in intents.items():
        covered = False
        for _task_name, doc in task_docs.items():
            if intent_id in doc or intent_desc.lower()[:30] in doc.lower():
                covered = True
                break
        if not covered:
            results.append(ValidationResult(
                "error",
                f"Intent {intent_id} ({intent_desc[:50]}) "
                f"has no covering task in compose.py",
            ))

    # Check each task traces to at least one intent (advisory).
    for task_name, doc in task_docs.items():
        if task_name in _NON_TASK_NAMES:
            continue
        traces = any(iid in doc for iid in intents)
        if not traces:
            if not doc.strip():
                results.append(ValidationResult(
                    "advisory", f"Task '{task_name}' has no docstring",
                ))
            else:
                results.append(ValidationResult(
                    "advisory",
                    f"Task '{task_name}' does not trace to any intent",
                ))

    return results


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
            doc = ast.get_docstring(node)
            bounds = _extract_resource_bounds(node)
            sigs[node.name] = Sig(node.name, params, ret, doc, bounds)
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
) -> list[ValidationResult]:
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
    return [ValidationResult(
        "advisory",
        f"No concurrent phases in a {n}-task graph "
        f"\u2014 verify all tasks have sequential data dependencies",
    )]


def _check_min_decomposition(sigs: dict[str, Sig]) -> list[ValidationResult]:
    """Error if there are fewer than 3 substantive task phases."""
    task_count = sum(1 for n in sigs if n not in _NON_TASK_NAMES)
    if task_count <= 2:
        return [ValidationResult(
            "error",
            "Under-decomposed milestone "
            "\u2014 identify at least 3 substantive phases",
        )]
    return []


def _check_resource_bounds(
    sigs: dict[str, Sig],
    sig_linenos: dict[str, int],
) -> list[ValidationResult]:
    """Validate @resource_bounds values are positive when present."""
    errors: list[ValidationResult] = []
    for sig in sigs.values():
        if sig.resource_bounds is None:
            continue
        ln = sig_linenos.get(sig.name, 0)
        if sig.resource_bounds.tokens is not None and sig.resource_bounds.tokens <= 0:
            errors.append(ValidationResult(
                "error",
                f"Invalid resource_bounds on {sig.name}: "
                f"tokens must be positive (line {ln})",
            ))
        if (
            sig.resource_bounds.timeout_seconds is not None
            and sig.resource_bounds.timeout_seconds <= 0
        ):
            errors.append(ValidationResult(
                "error",
                f"Invalid resource_bounds on {sig.name}: "
                f"timeout_seconds must be positive (line {ln})",
            ))
    return errors


def _check_gather_consumption(
    entry: ast.AsyncFunctionDef,
    sigs: dict[str, Sig] | None = None,
) -> list[ValidationResult]:
    """Error if gather() results unused AND tasks share intent coverage.

    Advisory if gather() results unused but intents are disjoint.
    Hard error if gather() results unused AND intent IDs overlap
    across tasks in the gather group — shared intents require a
    convergence point to verify integration.
    """
    errors: list[ValidationResult] = []

    for stmt in entry.body:
        # Only check assigned gather() calls: `x, y = await gather(...)`
        if not (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Await)
            and isinstance(stmt.value.value, ast.Call)
            and _call_name(stmt.value.value) == "gather"
        ):
            continue

        # Get the target variable names from this gather assignment.
        targets = _target_names(stmt.targets[0])
        if not targets:
            continue

        # Check if any target variable is consumed by a later statement.
        consumed = False
        found_gather = False
        for later in entry.body:
            if later is stmt:
                found_gather = True
                continue
            if not found_gather:
                continue
            # Walk the later statement for Name references.
            for node in ast.walk(later):
                if isinstance(node, ast.Name) and node.id in targets:
                    consumed = True
                    break
            if consumed:
                break

        if consumed:
            continue

        # Gather results are not consumed — determine severity.
        gather_call = stmt.value.value
        task_names_in_gather: list[str] = []
        for arg in gather_call.args:
            if isinstance(arg, ast.Call):
                task_names_in_gather.append(_call_name(arg))

        # Extract intent IDs from docstrings if sigs are available.
        if sigs and task_names_in_gather:
            intent_re = re.compile(r"\bI\d+\b")
            task_intents: dict[str, set[str]] = {}
            has_any_intents = False
            for tname in task_names_in_gather:
                sig = sigs.get(tname)
                if sig and sig.docstring:
                    intents = set(intent_re.findall(sig.docstring))
                    if intents:
                        has_any_intents = True
                        task_intents[tname] = intents
                else:
                    task_intents[tname] = set()

            if has_any_intents:
                # Check for overlapping intents across tasks.
                all_tasks = list(task_intents.keys())
                overlaps: list[tuple[str, str, str]] = []
                for i in range(len(all_tasks)):
                    for j in range(i + 1, len(all_tasks)):
                        a, b = all_tasks[i], all_tasks[j]
                        shared = task_intents[a] & task_intents[b]
                        for intent_id in sorted(shared):
                            overlaps.append((a, b, intent_id))

                if overlaps:
                    # Hard error: shared intents without convergence.
                    for a, b, intent_id in overlaps:
                        errors.append(ValidationResult(
                            "error",
                            f"Tasks {a}, {b} share intent {intent_id} "
                            f"but gather() results are not consumed "
                            f"— add a convergence task",
                        ))
                    continue

        # No overlap (or no intents / no sigs) — advisory.
        errors.append(ValidationResult(
            "advisory",
            "gather() results not consumed "
            "— consider whether an integration phase is needed",
        ))

    return errors


def _check_topology_consistency(
    sigs: dict[str, Sig],
    calls: list[tuple[str, list[tuple[str, str]], list[str]]],
) -> list[ValidationResult]:
    """Advisory: flag if all tasks could be independent but are serialized.

    Heuristic: if every non-root, non-leaf task in a pure serial chain has
    exactly one producer and one consumer, and every intermediate type is
    used by only one consumer, the topology may be fabricated — the
    structural signature of autoregressive ordering bias.

    Requires at least 4 task calls to trigger (short chains are often
    genuinely serial).
    """
    task_calls = [c for c in calls if c[0] in sigs and c[0] not in _NON_TASK_NAMES]
    if len(task_calls) < 4:
        return []

    produced_by: dict[str, str] = {}
    for func, _, targets in task_calls:
        for var in targets:
            produced_by[var] = func

    var_consumer_count: dict[str, int] = {}
    for _, args, _ in task_calls:
        for _, var in args:
            if var in produced_by:
                var_consumer_count[var] = var_consumer_count.get(var, 0) + 1

    is_pure_chain = True
    for i, (func, args, targets) in enumerate(task_calls):
        if i == 0:
            continue
        task_args = [var for _, var in args if var in produced_by]
        if len(task_args) != 1:
            is_pure_chain = False
            break
        var = task_args[0]
        if var_consumer_count.get(var, 0) != 1:
            is_pure_chain = False
            break

    if not is_pure_chain:
        return []

    n = len(task_calls)
    return [ValidationResult(
        "advisory",
        f"Pure serial chain ({n} tasks) with single-use intermediate types "
        f"— verify dependencies are genuine, not ordering bias",
    )]


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
) -> list[ValidationResult]:
    """Check that argument types match parameter annotations."""
    errors: list[ValidationResult] = []
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
                errors.append(ValidationResult(
                    "error",
                    f"Type mismatch: {func}() param '{sig.params[pos][0]}' "
                    f"expects {expected}, got {actual} (from '{var}')",
                ))
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
