"""Tests for clou.ui.widgets.dag — DAG rendering and layer computation."""

from __future__ import annotations

from rich.text import Text

from clou.ui.widgets.dag import _compute_layers, render_dag


class TestComputeLayers:
    """Tests for _compute_layers()."""

    def test_no_tasks(self) -> None:
        layers = _compute_layers([], {})
        assert layers == []

    def test_single_task_no_deps(self) -> None:
        tasks = [{"name": "a", "status": "pending"}]
        layers = _compute_layers(tasks, {})
        assert layers == [["a"]]

    def test_linear_chain(self) -> None:
        tasks = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
        ]
        deps = {"b": ["a"]}
        layers = _compute_layers(tasks, deps)
        assert "a" in layers[0]
        assert "b" in layers[1]

    def test_orphaned_deps_do_not_crash(self) -> None:
        """If deps reference tasks not in the task list, max() on an empty
        sequence must not raise ValueError."""
        tasks = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
        ]
        # "b" depends on "ghost" which is not in tasks
        deps = {"b": ["ghost"]}
        layers = _compute_layers(tasks, deps)
        # Both tasks should appear (no crash), "b" has depth 0
        all_names = [name for layer in layers for name in layer]
        assert "a" in all_names
        assert "b" in all_names

    def test_all_deps_orphaned(self) -> None:
        """When every dependency of a task is outside the task list."""
        tasks = [{"name": "a", "status": "pending"}]
        deps = {"a": ["x", "y", "z"]}
        layers = _compute_layers(tasks, deps)
        all_names = [name for layer in layers for name in layer]
        assert "a" in all_names

    def test_cycle_does_not_hang(self) -> None:
        tasks = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
        ]
        deps = {"a": ["b"], "b": ["a"]}
        # Should not hang or crash
        layers = _compute_layers(tasks, deps)
        all_names = [name for layer in layers for name in layer]
        assert "a" in all_names
        assert "b" in all_names


class TestRenderDag:
    """Tests for render_dag()."""

    def test_empty_tasks(self) -> None:
        result = render_dag([], {})
        assert isinstance(result, Text)
        assert "No tasks defined" in result.plain

    def test_single_task_renders(self) -> None:
        tasks = [{"name": "build", "status": "complete"}]
        result = render_dag(tasks, {})
        assert isinstance(result, Text)
        assert "build" in result.plain

    def test_fan_out_multi_node_layer(self) -> None:
        """Fan-out connector (┌) appears when multiple nodes share a layer."""
        tasks = [
            {"name": "A", "status": "complete"},
            {"name": "B", "status": "complete"},
            {"name": "C", "status": "active"},
        ]
        deps = {"C": ["A", "B"]}
        result = render_dag(tasks, deps)
        assert isinstance(result, Text)
        assert "\u250c" in result.plain  # ┌ fan-out left corner

    def test_orphaned_deps_render_without_crash(self) -> None:
        """Full render path with orphaned dependencies should not crash."""
        tasks = [
            {"name": "a", "status": "active"},
            {"name": "b", "status": "pending"},
        ]
        deps = {"b": ["nonexistent"]}
        result = render_dag(tasks, deps)
        assert isinstance(result, Text)
        assert "a" in result.plain
        assert "b" in result.plain
