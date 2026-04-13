"""Pure data layer for tracking live task state during EXECUTE cycles.

Agent-to-task fuzzy matching, status transition logic, tool call recording.
No UI dependencies -- consumed by TaskGraphWidget in the widget layer.

Public API:
    ToolInvocation     -- per-tool-call metadata dataclass
    TaskState          -- per-task state dataclass
    TaskGraphModel     -- model holding all task states + dependency layers
    match_agent_to_task -- pure function for fuzzy agent-description matching
    categorize_tool    -- map tool name to activity category
    MAX_TOOL_HISTORY   -- upper bound on retained tool invocations per task
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_HISTORY: int = 500

# ---------------------------------------------------------------------------
# Tool categorization
# ---------------------------------------------------------------------------

_TOOL_CATEGORIES: dict[str, str] = {
    "Read": "reads",
    "Glob": "reads",
    "Edit": "writes",
    "Write": "writes",
    "MultiEdit": "writes",
    "NotebookEdit": "writes",
    "Bash": "shell",
    "Grep": "searches",
    "WebSearch": "searches",
    "WebFetch": "searches",
}


def categorize_tool(name: str) -> str:
    """Return the activity category for a tool name."""
    return _TOOL_CATEGORIES.get(name, "other")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ToolInvocation:
    """A single tool call by an agent, with metadata."""

    name: str  # Tool name (Read, Edit, Bash, etc.)
    timestamp: float  # time.monotonic() when recorded
    category: str = "other"  # reads | writes | shell | searches | other
    input_summary: str = ""  # Brief description (for future SDK enrichment)
    output_summary: str = ""  # Brief result (for future SDK enrichment)
    duration_ms: float | None = None  # Optional duration


@dataclass
class TaskState:
    """Mutable state for a single compose.py task."""

    status: str = "pending"  # pending | active | complete | failed | aborted
    agent_id: str | None = None
    tool_count: int = 0  # SDK's cumulative count (may differ from len(tool_invocations) on batched events)
    last_tool: str | None = None
    summary: str | None = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)

    @property
    def tool_calls(self) -> list[tuple[str, str]]:
        """Backward-compatible accessor for legacy code."""
        return [(inv.name, inv.input_summary) for inv in self.tool_invocations]


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


from clou.graph import compute_layers as _compute_layers  # noqa: E402


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
        self.layers: list[list[str]] = _compute_layers(tasks, deps)
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
        if status == "completed":
            status = "complete"
        state.status = status
        state.summary = summary

    def add_tool_call(
        self,
        task_name: str,
        tool_name: str,
        brief: str,
        output_summary: str = "",
    ) -> ToolInvocation | None:
        """Append a tool invocation to the task's history.

        Returns the new invocation, or ``None`` if the task is unknown.
        Also checks ``unmapped_agents`` when the task is not in
        ``task_states``.
        """
        state = self.task_states.get(task_name)
        if state is None:
            state = self.unmapped_agents.get(task_name)
        if state is None:
            return None
        invocation = ToolInvocation(
            name=tool_name,
            timestamp=time.monotonic(),
            category=categorize_tool(tool_name),
            input_summary=brief,
            output_summary=output_summary,
        )
        state.tool_invocations.append(invocation)
        # Enforce bounded history.
        if len(state.tool_invocations) > MAX_TOOL_HISTORY:
            state.tool_invocations = state.tool_invocations[-MAX_TOOL_HISTORY:]
        return invocation
