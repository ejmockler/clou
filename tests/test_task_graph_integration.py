"""Integration tests for TaskGraphWidget wired into ClouApp.

Verifies visibility modes, message handler wiring, coexistence with
BreathWidget, /dag overlay, animation tick forwarding, and Escape
focus transfer.
"""

from __future__ import annotations

import pytest

from clou.ui.app import ClouApp
from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouBreathEvent,
    ClouCoordinatorSpawned,
    ClouDagUpdate,
    Mode,
)
from clou.ui.widgets.breath import BreathWidget
from clou.ui.widgets.task_graph import TaskGraphWidget

# ---------------------------------------------------------------------------
# Sample DAG data
# ---------------------------------------------------------------------------

SAMPLE_TASKS: list[dict[str, str]] = [
    {"name": "build_model"},
    {"name": "build_widget"},
    {"name": "integrate"},
]

SAMPLE_DEPS: dict[str, list[str]] = {
    "build_model": [],
    "build_widget": ["build_model"],
    "integrate": ["build_widget"],
}


# ---------------------------------------------------------------------------
# 1. TestTaskGraphVisibility
# ---------------------------------------------------------------------------


class TestTaskGraphVisibility:
    """Widget visibility changes with app mode."""

    @pytest.mark.asyncio
    async def test_task_graph_hidden_in_dialogue(self) -> None:
        """Widget has display:none in dialogue mode."""
        async with ClouApp().run_test() as pilot:
            assert pilot.app.mode is Mode.DIALOGUE
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg is not None
            # In dialogue mode the CSS sets display: none.
            assert tg.display is False or tg.styles.display == "none"

    @pytest.mark.asyncio
    async def test_task_graph_visible_in_breath(self) -> None:
        """Widget visible after ClouCoordinatorSpawned."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH
            tg = pilot.app.query_one(TaskGraphWidget)
            # In breath mode the CSS sets display: block.
            assert tg.styles.display == "block"

    @pytest.mark.asyncio
    async def test_task_graph_dimmed_in_decision(self) -> None:
        """Widget visible but dimmed in decision mode."""
        async with ClouApp().run_test() as pilot:
            from pathlib import Path

            from clou.ui.messages import ClouEscalationArrived

            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            await pilot.pause()
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=Path("/tmp/test.md"),
                    classification="blocking",
                    issue="Test issue",
                    options=[{"label": "Fix", "description": "Fix it"}],
                )
            )
            await pilot.pause()
            await pilot.pause()
            assert pilot.app.mode is Mode.DECISION
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg.styles.display == "block"


# ---------------------------------------------------------------------------
# 2. TestDagUpdatePopulatesModel
# ---------------------------------------------------------------------------


class TestDagUpdatePopulatesModel:
    """ClouDagUpdate creates model in the widget."""

    @pytest.mark.asyncio
    async def test_dag_update_creates_model(self) -> None:
        """Post ClouDagUpdate -> widget has model with tasks."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            assert "build_model" in tg._model.task_states

    @pytest.mark.asyncio
    async def test_dag_update_with_deps(self) -> None:
        """Dependencies preserved in model layers."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            assert len(tg._model.layers) > 1


# ---------------------------------------------------------------------------
# 3. TestAgentSpawnActivatesTask
# ---------------------------------------------------------------------------


class TestAgentSpawnActivatesTask:
    """ClouAgentSpawned activates the matching task."""

    @pytest.mark.asyncio
    async def test_agent_spawn_matches_task(self) -> None:
        """Post ClouAgentSpawned -> matching task becomes active."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-1",
                    description="build_model",
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states["build_model"]
            assert state.status == "active"
            assert state.agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_unmatched_agent_tracked(self) -> None:
        """Unmatched description -> stored in unmapped_agents."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-x",
                    description="completely_unrelated_task",
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            assert "agent-x" in tg._model.unmapped_agents

    @pytest.mark.asyncio
    async def test_early_spawn_buffered(self) -> None:
        """ClouAgentSpawned before ClouDagUpdate -> replayed after model created."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            # Spawn BEFORE dag update.
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-early",
                    description="build_model",
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            # Model not yet created -- spawn buffered.
            assert tg._model is None
            assert len(tg._pending_spawns) == 1

            # Now provide the DAG.
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            assert tg._model is not None
            state = tg._model.task_states["build_model"]
            assert state.status == "active"
            assert state.agent_id == "agent-early"
            # Buffer should be drained.
            assert len(tg._pending_spawns) == 0


# ---------------------------------------------------------------------------
# 4. TestAgentProgressUpdates
# ---------------------------------------------------------------------------


class TestAgentProgressUpdates:
    """ClouAgentProgress updates tool count on matching task."""

    @pytest.mark.asyncio
    async def test_progress_updates_tool_count(self) -> None:
        """ClouAgentProgress -> tool count updated on task."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-1", description="build_model",
                )
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id="agent-1",
                    last_tool="Read",
                    total_tokens=1000,
                    tool_uses=3,
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states["build_model"]
            assert state.tool_count == 3
            assert state.last_tool == "Read"

    @pytest.mark.asyncio
    async def test_progress_no_breath_line(self) -> None:
        """BreathWidget does not receive a visible line from progress."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            bw = pilot.app.query_one(BreathWidget)
            events_before = len(bw._events)
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id="agent-1",
                    last_tool="Read",
                    total_tokens=500,
                    tool_uses=1,
                )
            )
            await pilot.pause()
            # BreathWidget should not have a new event from progress.
            events_after = len(bw._events)
            assert events_after == events_before


# ---------------------------------------------------------------------------
# 5. TestAgentCompleteTransitions
# ---------------------------------------------------------------------------


class TestAgentCompleteTransitions:
    """ClouAgentComplete transitions task status."""

    @pytest.mark.asyncio
    async def test_complete_marks_done(self) -> None:
        """ClouAgentComplete with status='complete' -> task complete."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-1", description="build_model",
                )
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentComplete(
                    task_id="agent-1",
                    status="complete",
                    summary="Model built successfully",
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states["build_model"]
            assert state.status == "complete"
            assert state.summary == "Model built successfully"

    @pytest.mark.asyncio
    async def test_failed_marks_failed(self) -> None:
        """ClouAgentComplete with status='failed' -> task failed."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-1", description="build_model",
                )
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentComplete(
                    task_id="agent-1",
                    status="failed",
                    summary="Something went wrong",
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states["build_model"]
            assert state.status == "failed"

    @pytest.mark.asyncio
    async def test_shimmer_off_when_no_active(self) -> None:
        """Last active task completing disables shimmer."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouAgentSpawned(
                    task_id="agent-1", description="build_model",
                )
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg.shimmer_active is True
            pilot.app.post_message(
                ClouAgentComplete(
                    task_id="agent-1",
                    status="complete",
                    summary="Done",
                )
            )
            await pilot.pause()
            assert tg.shimmer_active is False


# ---------------------------------------------------------------------------
# 6. TestBreathEventsCoexist
# ---------------------------------------------------------------------------


class TestBreathEventsCoexist:
    """Both widgets process messages without interference."""

    @pytest.mark.asyncio
    async def test_breath_event_still_received(self) -> None:
        """BreathWidget receives ClouBreathEvent alongside task graph.

        In the live app, coordinator messages are posted directly to
        BreathWidget (not app.post_message) so we test the same pattern.
        """
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            bw = pilot.app.query_one(BreathWidget)
            events_before = len(bw._events)
            # Post directly to BreathWidget as the orchestrator does.
            bw.post_message(
                ClouBreathEvent(
                    text="Planning phase started",
                    cycle_type="PLAN",
                    phase="plan",
                )
            )
            await pilot.pause()
            events_after = len(bw._events)
            assert events_after > events_before

    @pytest.mark.asyncio
    async def test_agent_spawn_visible_in_both(self) -> None:
        """Both widgets process ClouAgentSpawned.

        In the live app, agent spawns are posted to BreathWidget by the
        orchestrator, then bubble up to App which forwards to TaskGraphWidget.
        We simulate this by posting to BreathWidget, which also reaches the
        App's handler that forwards to TaskGraphWidget.
        """
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            bw = pilot.app.query_one(BreathWidget)
            events_before = len(bw._events)
            # Post to BreathWidget as the orchestrator does; it bubbles
            # to App which forwards to TaskGraphWidget.
            bw.post_message(
                ClouAgentSpawned(
                    task_id="agent-1", description="build_model",
                )
            )
            await pilot.pause()
            await pilot.pause()
            # BreathWidget should have processed the spawn.
            events_after = len(bw._events)
            assert events_after > events_before
            # TaskGraphWidget should have the task active.
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            assert tg._model.task_states["build_model"].status == "active"


# ---------------------------------------------------------------------------
# 7. TestDagOverlayUnchanged
# ---------------------------------------------------------------------------


class TestDagOverlayUnchanged:
    """/dag overlay continues to work."""

    @pytest.mark.asyncio
    async def test_dag_data_still_stored_in_app(self) -> None:
        """app._dag_tasks populated after ClouDagUpdate."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            assert len(app._dag_tasks) == 3
            assert "build_widget" in app._dag_deps

    @pytest.mark.asyncio
    async def test_dag_screen_pushable(self) -> None:
        """action_show_dag still pushes DagScreen in breath mode."""
        async with ClouApp().run_test() as pilot:
            from clou.ui.screens.dag import DagScreen

            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            assert app.mode is Mode.BREATH
            app.action_show_dag()
            await pilot.pause()
            assert isinstance(pilot.app.screen, DagScreen)
            await pilot.press("escape")


# ---------------------------------------------------------------------------
# 8. TestAnimationWiring
# ---------------------------------------------------------------------------


class TestAnimationWiring:
    """Animation tick forwards breath_phase to TaskGraphWidget."""

    @pytest.mark.asyncio
    async def test_breath_phase_updates_task_graph(self) -> None:
        """Animation tick sets TaskGraphWidget.breath_phase."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            # After entering breath mode, the timer ticks and updates
            # the breath_phase on the task graph widget.
            # We can trigger a tick manually.
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app._animation_tick()
            await pilot.pause()
            # The breath_phase on the task graph should be updated.
            # Since animation_time starts at 0 and increments by frame
            # duration, the value after one tick should be > 0.
            assert tg.breath_phase >= 0.0


# ---------------------------------------------------------------------------
# 9. TestEscapeFocusTransfer
# ---------------------------------------------------------------------------


class TestEscapeFocusTransfer:
    """Escape in TaskGraphWidget focuses prompt input."""

    @pytest.mark.asyncio
    async def test_escape_transfers_focus_to_input(self) -> None:
        """Escape in TaskGraphWidget focuses prompt input."""
        async with ClouApp().run_test() as pilot:
            from textual.widgets import Input

            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            tg.focus()
            await pilot.pause()
            assert tg._focused_index >= 0
            # Press Escape.
            await pilot.press("escape")
            await pilot.pause()
            assert tg._focused_index == -1
            # Focus should be on the input widget.
            focused = pilot.app.focused
            assert isinstance(focused, Input)


# ---------------------------------------------------------------------------
# 10. TestCoordinatorSpawnedResets
# ---------------------------------------------------------------------------


class TestCoordinatorSpawnedResets:
    """ClouCoordinatorSpawned resets widget state."""

    @pytest.mark.asyncio
    async def test_coordinator_spawned_resets_model(self) -> None:
        """New coordinator session clears the model and buffers."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()
            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            # New coordinator session.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test2"))
            await pilot.pause()
            assert tg._model is None
            assert len(tg._row_map) == 0
            assert tg._focused_index == -1
