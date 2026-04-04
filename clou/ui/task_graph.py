"""Pure data layer for tracking live task state during EXECUTE cycles.

Agent-to-task fuzzy matching, status transition logic, tool call recording.
No UI dependencies -- consumed by TaskGraphWidget in the widget layer.

Public API:
    TaskState          -- per-task state dataclass
    TaskGraphModel     -- model holding all task states + dependency layers
    match_agent_to_task -- pure function for fuzzy agent-description matching
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TaskState:
    """Mutable state for a single compose.py task."""

    status: str = "pending"  # pending | active | complete | failed | aborted
    agent_id: str | None = None
    tool_count: int = 0
    last_tool: str | None = None
    summary: str | None = None
    tool_calls: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent-to-task fuzzy matching (pure, module-level)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Split on underscores and spaces, lowercase, drop empty tokens."""
    return {t for t in text.replace("_", " ").lower().split() if t}


def match_agent_to_task(
    description: str,
    task_names: list[str],
) -> str | None:
    """Match an agent description to a task name.

    Strategy (first hit wins):
    1. Exact match: ``description == task_name``
    2. Task name is a substring of description (case-insensitive)
    3. Description is a substring of task name (case-insensitive)
    4. Word-overlap Jaccard similarity > 0.5

    Returns the matched task name, or ``None`` if no match.
    """
    if not description or not task_names:
        return None

    # 1. Exact match.
    for name in task_names:
        if description == name:
            return name

    desc_lower = description.lower()

    # 2. Task name contained in description.
    for name in task_names:
        if name.lower() in desc_lower:
            return name

    # 3. Description contained in task name.
    for name in task_names:
        if desc_lower in name.lower():
            return name

    # 4. Word-overlap scoring (Jaccard > 0.5).
    desc_tokens = _tokenize(description)
    if not desc_tokens:
        return None

    best_name: str | None = None
    best_score: float = 0.0
    for name in task_names:
        name_tokens = _tokenize(name)
        if not name_tokens:
            continue
        intersection = desc_tokens & name_tokens
        union = desc_tokens | name_tokens
        score = len(intersection) / len(union)
        if score > best_score:
            best_score = score
            best_name = name

    if best_score > 0.5:
        return best_name

    return None


# ---------------------------------------------------------------------------
# Layer computation (replicated from dag.py to avoid widget coupling)
# ---------------------------------------------------------------------------


def _compute_layers(
    task_names: list[str],
    deps: dict[str, list[str]],
) -> list[list[str]]:
    """Group tasks into layers by dependency depth.

    Replicates the algorithm from ``clou/ui/widgets/dag.py`` without
    importing from the widget layer.
    """
    task_set = set(task_names)
    depth: dict[str, int] = {}
    visiting: set[str] = set()

    def _get_depth(name: str, level: int = 0) -> int:
        if level > 200:
            depth[name] = level
            return level
        if name in depth:
            return depth[name]
        if name in visiting:
            depth[name] = 0  # Break cycle.
            return 0
        visiting.add(name)
        task_deps = deps.get(name, [])
        valid_deps = [dep for dep in task_deps if dep in task_set]
        if not valid_deps:
            depth[name] = 0
            visiting.discard(name)
            return 0
        d = max(_get_depth(dep, level + 1) for dep in valid_deps) + 1
        depth[name] = d
        visiting.discard(name)
        return d

    for name in task_names:
        _get_depth(name)

    max_depth = max(depth.values()) if depth else 0
    layers: list[list[str]] = [[] for _ in range(max_depth + 1)]
    for name in task_names:
        layers[depth.get(name, 0)].append(name)

    return layers


# ---------------------------------------------------------------------------
# Task graph model
# ---------------------------------------------------------------------------


class TaskGraphModel:
    """Pure data model tracking live task state for a milestone's compose.py.

    Initialised from the ``(tasks, deps)`` format produced by
    ``extract_dag_data`` / ``ClouDagUpdate``.
    """

    def __init__(
        self,
        tasks: list[dict[str, str]],
        deps: dict[str, list[str]],
    ) -> None:
        task_names = [t["name"] for t in tasks]
        self.task_states: dict[str, TaskState] = {
            name: TaskState() for name in task_names
        }
        self.deps: dict[str, list[str]] = dict(deps)
        self.layers: list[list[str]] = _compute_layers(task_names, deps)
        self.unmapped_agents: dict[str, TaskState] = {}

    # -- Agent matching ----------------------------------------------------

    def match_agent(self, description: str) -> str | None:
        """Fuzzy-match an agent description to a known task name."""
        return match_agent_to_task(description, list(self.task_states.keys()))

    # -- State transitions -------------------------------------------------

    def activate_task(self, task_name: str, agent_id: str) -> None:
        """Transition a task to active and record the agent id."""
        state = self.task_states.get(task_name)
        if state is None:
            return
        state.status = "active"
        state.agent_id = agent_id

    def update_progress(
        self,
        task_name: str,
        tool_count: int,
        last_tool: str | None,
    ) -> None:
        """Update tool progress counters for a task."""
        state = self.task_states.get(task_name)
        if state is None:
            return
        state.tool_count = tool_count
        state.last_tool = last_tool

    def complete_task(
        self,
        task_name: str,
        status: str,
        summary: str,
    ) -> None:
        """Mark a task as complete, failed, or aborted."""
        state = self.task_states.get(task_name)
        if state is None:
            return
        state.status = status
        state.summary = summary

    def add_tool_call(
        self,
        task_name: str,
        tool_name: str,
        brief: str,
    ) -> None:
        """Append a tool call record to the task's history."""
        state = self.task_states.get(task_name)
        if state is None:
            return
        state.tool_calls.append((tool_name, brief))
