"""Tests for on-demand panels — DAG, context tree, and push-screens."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static

from clou.ui.screens.context import ContextScreen
from clou.ui.screens.dag import DagScreen
from clou.ui.screens.detail import DetailScreen
from clou.ui.widgets.context_tree import ContextTreeWidget
from clou.ui.widgets.dag import DagWidget, render_dag

# ---------------------------------------------------------------------------
# Sample DAG data
# ---------------------------------------------------------------------------

SAMPLE_TASKS: list[dict[str, str]] = [
    {"name": "setup_database", "status": "complete"},
    {"name": "auth", "status": "complete"},
    {"name": "api", "status": "active"},
    {"name": "frontend", "status": "pending"},
]

SAMPLE_DEPS: dict[str, list[str]] = {
    "auth": ["setup_database"],
    "api": ["setup_database"],
    "frontend": ["auth", "api"],
}


# ---------------------------------------------------------------------------
# DAG rendering
# ---------------------------------------------------------------------------


class TestRenderDag:
    """Tests for the render_dag function."""

    def test_renders_box_drawing_chars(self) -> None:
        result = render_dag(SAMPLE_TASKS, SAMPLE_DEPS)
        plain = result.plain
        assert "\u250c" in plain  # ┌
        assert "\u2510" in plain  # ┐
        assert "\u2514" in plain  # └
        assert "\u2518" in plain  # ┘

    def test_renders_task_names(self) -> None:
        result = render_dag(SAMPLE_TASKS, SAMPLE_DEPS)
        plain = result.plain
        assert "setup_database" in plain
        assert "auth" in plain
        assert "api" in plain
        assert "frontend" in plain

    def test_status_icons_present(self) -> None:
        result = render_dag(SAMPLE_TASKS, SAMPLE_DEPS)
        plain = result.plain
        assert "\u2713" in plain  # ✓ (complete)
        assert "\u25c9" in plain  # ◉ (active)
        assert "\u25cb" in plain  # ○ (pending)

    def test_failed_status_icon(self) -> None:
        tasks = [{"name": "broken", "status": "failed"}]
        result = render_dag(tasks, {})
        assert "\u2717" in result.plain  # ✗

    def test_empty_tasks(self) -> None:
        result = render_dag([], {})
        assert "No tasks" in result.plain

    def test_single_task_no_deps(self) -> None:
        tasks = [{"name": "solo", "status": "active"}]
        result = render_dag(tasks, {})
        assert "solo" in result.plain

    def test_connection_lines_drawn(self) -> None:
        result = render_dag(SAMPLE_TASKS, SAMPLE_DEPS)
        plain = result.plain
        # Should have vertical connection line.
        assert "\u2502" in plain  # │


class TestDagWidget:
    """Tests for the DagWidget."""

    def test_construction_empty(self) -> None:
        w = DagWidget()
        assert w._tasks == []
        assert w._deps == {}

    def test_construction_with_data(self) -> None:
        w = DagWidget(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
        assert len(w._tasks) == 4

    def test_update_dag_stores_data(self) -> None:
        """DagWidget constructed with data stores it correctly."""
        w = DagWidget(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
        assert w._tasks == SAMPLE_TASKS
        assert w._deps == SAMPLE_DEPS
        assert len(w._tasks) == 4

    @pytest.mark.asyncio
    async def test_update_dag_method_renders(self) -> None:
        """update_dag() within an app context stores data and re-renders."""
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(
                DagScreen(
                    milestone="init",
                    tasks=SAMPLE_TASKS[:2],
                    deps={},
                )
            )
            await pilot.pause()
            dag = pilot.app.screen.query_one(DagWidget)
            assert len(dag._tasks) == 2

            new_tasks = SAMPLE_TASKS
            new_deps = SAMPLE_DEPS
            dag.update_dag(new_tasks, new_deps)
            await pilot.pause()
            assert dag._tasks == new_tasks
            assert dag._deps == new_deps
            assert len(dag._tasks) == 4
            await pilot.press("escape")


# ---------------------------------------------------------------------------
# Context tree
# ---------------------------------------------------------------------------


class TestContextTreeWidget:
    """Tests for the ContextTreeWidget."""

    def test_construction(self) -> None:
        w = ContextTreeWidget()
        assert w._clou_dir is None

    def test_construction_with_dir(self, tmp_path: Path) -> None:
        w = ContextTreeWidget(clou_dir=tmp_path)
        assert w._clou_dir == tmp_path

    def test_refresh_missing_dir(self, tmp_path: Path) -> None:
        w = ContextTreeWidget()
        missing = tmp_path / "nonexistent"
        w.refresh_tree(missing)
        # Should show placeholder without raising.
        assert w._clou_dir == missing

    def test_refresh_with_files(self, tmp_path: Path) -> None:
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        milestone = clou_dir / "auth-system"
        milestone.mkdir()
        (milestone / "compose.py").write_text("# compose")
        (milestone / "handoff.md").write_text("# Handoff")

        w = ContextTreeWidget()
        w.refresh_tree(clou_dir)
        assert w._clou_dir == clou_dir

    def test_refresh_with_state_file(self, tmp_path: Path) -> None:
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        milestone = clou_dir / "my-milestone"
        milestone.mkdir()
        (milestone / "state.md").write_text("Status: active\n")

        w = ContextTreeWidget()
        w.refresh_tree(clou_dir)
        assert w._clou_dir == clou_dir

    def test_max_depth_limits_recursion(self, tmp_path: Path) -> None:
        """Directories deeper than max_depth are not traversed."""
        clou_dir = tmp_path / ".clou"
        # Create a nested structure 4 levels deep.
        d = clou_dir
        for i in range(4):
            d = d / f"level{i}"
        d.mkdir(parents=True)
        (d / "deep.txt").write_text("deep")

        w = ContextTreeWidget()
        # Use max_depth=2 via refresh_tree -> _build_tree.
        # We call _build_tree directly to pass max_depth=2.
        w._clou_dir = clou_dir
        w.clear()
        import time as _time

        w._build_tree(w.root, clou_dir, _time.time(), depth=0, max_depth=2)
        # Flatten all node data values.
        all_data: list[str] = []

        def _collect(node: object) -> None:
            all_data.append(str(getattr(node, "data", "")))
            for child in getattr(node, "children", []):
                _collect(child)

        _collect(w.root)
        # "deep.txt" at depth 4 should NOT appear.
        assert not any("deep.txt" in d for d in all_data)

    def test_markup_in_filenames_is_escaped(self, tmp_path: Path) -> None:
        """Filenames containing Rich markup chars are escaped, not interpreted."""
        from rich.markup import escape as _escape_markup

        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        # Use names with brackets that could be parsed as Rich markup.
        dir_name = "data[bold]"
        file_name = "notes[red].txt"
        bracket_dir = clou_dir / dir_name
        bracket_dir.mkdir()
        (bracket_dir / file_name).write_text("content")

        w = ContextTreeWidget()
        w.refresh_tree(clou_dir)

        # Collect raw markup strings from the tree labels.
        markups: list[str] = []

        def _collect(node: object) -> None:
            label = getattr(node, "_label", None)
            if label is not None and hasattr(label, "markup"):
                markups.append(label.markup)
            for child in getattr(node, "children", []):
                _collect(child)

        _collect(w.root)
        joined = "\n".join(markups)

        # The escaped form should appear in raw markup (brackets escaped as \[).
        assert _escape_markup(dir_name) in joined
        assert _escape_markup(file_name) in joined

    def test_symlinks_are_skipped(self, tmp_path: Path) -> None:
        """Symlinks inside .clou/ are not followed."""
        clou_dir = tmp_path / ".clou"
        clou_dir.mkdir()
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "secret.txt").write_text("secret")
        # Create a symlink inside .clou pointing to real_dir.
        (clou_dir / "link").symlink_to(real_dir)

        w = ContextTreeWidget()
        w.refresh_tree(clou_dir)
        all_data: list[str] = []

        def _collect(node: object) -> None:
            all_data.append(str(getattr(node, "data", "")))
            for child in getattr(node, "children", []):
                _collect(child)

        _collect(w.root)
        assert not any("secret.txt" in d for d in all_data)
        assert not any("link" in d for d in all_data)


# ---------------------------------------------------------------------------
# Screen tests
# ---------------------------------------------------------------------------


class _HostApp(App[None]):
    """Minimal app for testing push-screens."""

    def compose(self) -> ComposeResult:
        yield Static("placeholder")


class TestContextScreen:
    """Tests for the ContextScreen."""

    @pytest.mark.asyncio
    async def test_mounts_and_dismisses(self, tmp_path: Path) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(ContextScreen(tmp_path))
            await pilot.pause()
            # Screen should be mounted.
            header = pilot.app.screen.query_one("#context-header")
            assert header is not None
            # Dismiss with Escape.
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_shows_header(self, tmp_path: Path) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(ContextScreen(tmp_path))
            await pilot.pause()
            header = pilot.app.screen.query_one("#context-header")
            assert header is not None

    @pytest.mark.asyncio
    async def test_contains_context_tree_widget(self, tmp_path: Path) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(ContextScreen(tmp_path))
            await pilot.pause()
            tree = pilot.app.screen.query_one(ContextTreeWidget)
            assert tree is not None


class TestDagScreen:
    """Tests for the DagScreen."""

    @pytest.mark.asyncio
    async def test_mounts_with_data(self) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(
                DagScreen(
                    milestone="auth",
                    tasks=SAMPLE_TASKS,
                    deps=SAMPLE_DEPS,
                )
            )
            await pilot.pause()
            header = pilot.app.screen.query_one("#dag-header")
            assert header is not None

    @pytest.mark.asyncio
    async def test_dismisses_with_escape(self) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(DagScreen())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(pilot.app.screen, DagScreen)

    @pytest.mark.asyncio
    async def test_contains_dag_widget(self) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(
                DagScreen(
                    milestone="test",
                    tasks=SAMPLE_TASKS,
                    deps=SAMPLE_DEPS,
                )
            )
            await pilot.pause()
            dag = pilot.app.screen.query_one(DagWidget)
            assert dag is not None


class TestDetailScreen:
    """Tests for the DetailScreen."""

    @pytest.mark.asyncio
    async def test_renders_content(self) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(DetailScreen(title="Test", content="Hello world"))
            await pilot.pause()
            header = pilot.app.screen.query_one("#detail-header")
            assert header is not None
            log = pilot.app.screen.query_one("#detail-content", RichLog)
            assert len(log.lines) >= 1

    @pytest.mark.asyncio
    async def test_dismisses_with_escape(self) -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(DetailScreen(title="T", content="C"))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(pilot.app.screen, DetailScreen)


# ---------------------------------------------------------------------------
# App keybinding integration
# ---------------------------------------------------------------------------


class TestAppKeybindings:
    """Tests for keybinding-triggered panel actions."""

    @pytest.mark.asyncio
    async def test_action_show_context(self) -> None:
        from clou.ui.app import ClouApp

        async with ClouApp().run_test() as pilot:
            pilot.app.action_show_context()
            await pilot.pause()
            # Should have pushed a ContextScreen.
            assert isinstance(pilot.app.screen, ContextScreen)
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_action_show_costs(self) -> None:
        from clou.ui.app import ClouApp

        async with ClouApp().run_test() as pilot:
            pilot.app.action_show_costs()
            await pilot.pause()
            assert isinstance(pilot.app.screen, DetailScreen)
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_action_show_dag_requires_breath_mode(self) -> None:
        from clou.ui.app import ClouApp

        async with ClouApp().run_test() as pilot:
            # In dialogue mode, dag should not push.
            pilot.app.action_show_dag()
            await pilot.pause()
            assert not isinstance(pilot.app.screen, DagScreen)

    @pytest.mark.asyncio
    async def test_action_show_dag_passes_stored_data(self) -> None:
        from clou.ui.app import ClouApp
        from clou.ui.messages import ClouCoordinatorSpawned, ClouDagUpdate

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Enter breath mode.
            app.post_message(ClouCoordinatorSpawned(milestone="auth"))
            await pilot.pause()

            # Post DAG data.
            app.post_message(ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS))
            await pilot.pause()

            # Open the DAG screen.
            app.action_show_dag()
            await pilot.pause()
            assert isinstance(pilot.app.screen, DagScreen)
            screen: DagScreen = pilot.app.screen  # type: ignore[assignment]
            assert screen._tasks == SAMPLE_TASKS
            assert screen._deps == SAMPLE_DEPS
            assert screen._milestone == "auth"
            await pilot.press("escape")


    @pytest.mark.asyncio
    async def test_action_show_dag_in_handoff_mode(self) -> None:
        from clou.ui.app import ClouApp
        from clou.ui.messages import ClouDagUpdate, ClouHandoff

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Enter handoff mode.
            app.post_message(ClouHandoff(milestone="deploy", handoff_path=Path("/tmp/h.md")))
            await pilot.pause()

            # Post DAG data.
            app.post_message(ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS))
            await pilot.pause()

            # Open the DAG screen.
            app.action_show_dag()
            await pilot.pause()
            assert isinstance(pilot.app.screen, DagScreen)
            screen: DagScreen = pilot.app.screen  # type: ignore[assignment]
            assert screen._tasks == SAMPLE_TASKS
            assert screen._deps == SAMPLE_DEPS
            await pilot.press("escape")


class TestDagUpdateMessage:
    """Tests for ClouDagUpdate message wiring in ClouApp."""

    @pytest.mark.asyncio
    async def test_dag_update_stores_data(self) -> None:
        from clou.ui.app import ClouApp
        from clou.ui.messages import ClouDagUpdate

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app._dag_tasks == []
            assert app._dag_deps == {}

            app.post_message(ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS))
            await pilot.pause()
            assert app._dag_tasks == SAMPLE_TASKS
            assert app._dag_deps == SAMPLE_DEPS

    @pytest.mark.asyncio
    async def test_coordinator_spawned_resets_dag(self) -> None:
        from clou.ui.app import ClouApp
        from clou.ui.messages import ClouCoordinatorSpawned, ClouDagUpdate

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Store some DAG data.
            app.post_message(ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS))
            await pilot.pause()
            assert len(app._dag_tasks) == 4

            # New coordinator spawned should reset.
            app.post_message(ClouCoordinatorSpawned(milestone="new-milestone"))
            await pilot.pause()
            assert app._dag_tasks == []
            assert app._dag_deps == {}
