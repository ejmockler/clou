"""DAG viewer — task dependency graph with box-drawing characters.

Renders the task dependency graph from a milestone's compose.py as
a Rich Text display with box-drawing connections and status indicators.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from clou.ui.theme import PALETTE

# Semantic hex colors.
_GREEN_DIM_HEX = PALETTE["accent-green"].dim().to_hex()
_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_TEXT_MUTED_HEX = PALETTE["text-muted"].to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_TEXT_HEX = PALETTE["text"].to_hex()
_TEXT_DIM_HEX = PALETTE["text-dim"].to_hex()

# Status indicators.
_STATUS_ICONS: dict[str, str] = {
    "complete": "\u2713",  # ✓
    "active": "\u25c9",  # ◉
    "pending": "\u25cb",  # ○
    "failed": "\u2717",  # ✗
}

_STATUS_COLORS: dict[str, str] = {
    "complete": _GREEN_DIM_HEX,
    "active": _TEAL_HEX,
    "pending": _TEXT_MUTED_HEX,
    "failed": _ROSE_HEX,
}


def render_dag(
    tasks: list[dict[str, str]],
    deps: dict[str, list[str]],
) -> Text:
    """Render a task dependency graph as Rich Text with box-drawing.

    Parameters
    ----------
    tasks:
        List of task dicts, each with ``name`` and ``status`` keys.
        Status is one of: ``complete``, ``active``, ``pending``, ``failed``.
    deps:
        Mapping of task name to list of dependency task names.

    Returns
    -------
    Rich Text with box-drawing characters and status-colored task names.
    """
    result = Text()

    if not tasks:
        result.append("  No tasks defined.\n", style=f"{_TEXT_MUTED_HEX}")
        return result

    # Build lookup.
    task_map: dict[str, dict[str, str]] = {t["name"]: t for t in tasks}

    # Compute topological layers by dependency depth.
    layers = _compute_layers(tasks, deps)

    for layer_idx, layer in enumerate(layers):
        # Draw connections from previous layer.
        if layer_idx > 0:
            _draw_connections(result, layers[layer_idx - 1], layer, deps)

        # Draw task boxes in this layer.
        for task_idx, task_name in enumerate(layer):
            task = task_map.get(task_name, {"name": task_name, "status": "pending"})
            status = task.get("status", "pending")
            icon = _STATUS_ICONS.get(status, "\u25cb")
            color = _STATUS_COLORS.get(status, _TEXT_MUTED_HEX)

            display_name = task_name[:37] + "..." if len(task_name) > 40 else task_name
            box_width = max(len(display_name) + 4, 16)
            top = "\u250c" + "\u2500" * (box_width - 2) + "\u2510"
            mid = "\u2502 " + display_name.center(box_width - 4) + " \u2502"
            bot = "\u2514" + "\u2500" * (box_width - 2) + "\u2518"

            indent = "  "
            if task_idx > 0:
                result.append("\n")

            result.append(f"{indent}{top}\n", style=f"{_TEXT_DIM_HEX}")
            result.append(f"{indent}{mid}", style=f"{color}")
            result.append(f" {icon}\n", style=f"{color}")
            result.append(f"{indent}{bot}\n", style=f"{_TEXT_DIM_HEX}")

    return result


from clou.graph import compute_layers as _compute_layers  # noqa: E402


def _draw_connections(
    result: Text,
    prev_layer: list[str],
    curr_layer: list[str],
    deps: dict[str, list[str]],
) -> None:
    """Draw box-drawing connection lines between layers."""
    # Check if any task in current layer depends on tasks in previous layer.
    has_connection = False
    for task_name in curr_layer:
        task_deps = deps.get(task_name, [])
        if any(d in prev_layer for d in task_deps):
            has_connection = True
            break

    if not has_connection:
        return

    if len(curr_layer) == 1:
        result.append("           \u2502\n", style=f"{_TEXT_DIM_HEX}")
    elif len(curr_layer) > 1:
        result.append("      \u250c", style=f"{_TEXT_DIM_HEX}")
        result.append("\u2500" * 4, style=f"{_TEXT_DIM_HEX}")
        result.append("\u2534", style=f"{_TEXT_DIM_HEX}")
        result.append("\u2500" * 4, style=f"{_TEXT_DIM_HEX}")
        result.append("\u2510\n", style=f"{_TEXT_DIM_HEX}")


class DagWidget(Static):
    """Displays the task dependency graph with box-drawing and status colors."""

    def __init__(
        self,
        tasks: list[dict[str, str]] | None = None,
        deps: dict[str, list[str]] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._tasks = tasks or []
        self._deps = deps or {}

    def on_mount(self) -> None:
        """Render the DAG on mount."""
        if self._tasks:
            self.update(render_dag(self._tasks, self._deps))

    def update_dag(
        self,
        tasks: list[dict[str, str]],
        deps: dict[str, list[str]],
    ) -> None:
        """Update the DAG display with new task data."""
        self._tasks = tasks
        self._deps = deps
        self.update(render_dag(tasks, deps))
