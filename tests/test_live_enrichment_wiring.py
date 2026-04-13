"""Tests for live enrichment wiring in on_clou_agent_progress.

Verifies that the handler populates ToolInvocations with real input_summary
and output_summary from the TranscriptStore when a matching transcript entry
exists, and falls back to empty strings when it does not.
"""

from __future__ import annotations

import pytest

from clou.transcript import (
    TranscriptEntry,
    get_store,
    reset_store,
)
from clou.ui.app import ClouApp
from clou.ui.messages import (
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouCoordinatorSpawned,
    ClouDagUpdate,
)
from clou.ui.task_graph import ToolInvocation
from clou.ui.widgets.task_graph import TaskGraphWidget

# ---------------------------------------------------------------------------
# Sample DAG data
# ---------------------------------------------------------------------------

SAMPLE_TASKS: list[dict[str, str]] = [
    {"name": "build_model"},
    {"name": "build_widget"},
]

SAMPLE_DEPS: dict[str, list[str]] = {
    "build_model": [],
    "build_widget": ["build_model"],
}

AGENT_ID = "agent-1"
TASK_NAME = "build_model"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_dag(pilot: object) -> None:
    """Post the coordinator-spawned and DAG-update messages."""
    app = pilot.app  # type: ignore[attr-defined]
    app.post_message(ClouCoordinatorSpawned(milestone="test"))


async def _setup_active_agent(pilot: object) -> None:
    """Set up a DAG with an active agent and await message processing."""
    app = pilot.app  # type: ignore[attr-defined]
    app.post_message(ClouCoordinatorSpawned(milestone="test"))
    await pilot.pause()  # type: ignore[attr-defined]
    app.post_message(ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS))
    await pilot.pause()  # type: ignore[attr-defined]
    app.post_message(
        ClouAgentSpawned(task_id=AGENT_ID, description=TASK_NAME)
    )
    await pilot.pause()  # type: ignore[attr-defined]


def _record_transcript_entry(
    agent_id: str,
    tool_name: str,
    tool_input: dict[str, object] | None = None,
    tool_response: str = "",
) -> None:
    """Record a transcript entry in the singleton store."""
    store = get_store()
    store.record(
        agent_id,
        TranscriptEntry(
            tool_name=tool_name,
            tool_input=tool_input if tool_input is not None else {},
            tool_response=tool_response,
        ),
    )


# ---------------------------------------------------------------------------
# T1: Live enrichment with matching transcript entry
# ---------------------------------------------------------------------------


class TestLiveEnrichmentMatch:
    """When transcript entry matches the progress tool, enrichment is populated."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    @pytest.mark.asyncio
    async def test_read_tool_enriched_with_input_summary(self) -> None:
        """A Read tool call gets input_summary from tool_summary()."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            _record_transcript_entry(
                AGENT_ID, "Read",
                tool_input={"file_path": "/home/user/src/app.py"},
                tool_response="file contents here",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Read",
                    total_tokens=500, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1
            inv = state.tool_invocations[0]
            assert "app.py" in inv.input_summary
            assert inv.output_summary == "file contents here"

    @pytest.mark.asyncio
    async def test_edit_tool_enriched_with_stats(self) -> None:
        """An Edit tool call gets input_summary with file + stats."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            _record_transcript_entry(
                AGENT_ID, "Edit",
                tool_input={
                    "file_path": "/tmp/main.py",
                    "old_string": "foo",
                    "new_string": "bar\nbaz",
                },
                tool_response="ok",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Edit",
                    total_tokens=1000, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1
            inv = state.tool_invocations[0]
            assert "main.py" in inv.input_summary
            assert "Edit" in inv.input_summary
            assert inv.output_summary == "ok"

    @pytest.mark.asyncio
    async def test_bash_tool_enriched(self) -> None:
        """A Bash tool call gets command in input_summary."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            _record_transcript_entry(
                AGENT_ID, "Bash",
                tool_input={"command": "git status"},
                tool_response="On branch main",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Bash",
                    total_tokens=800, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1
            inv = state.tool_invocations[0]
            assert "git status" in inv.input_summary
            assert inv.output_summary == "On branch main"


# ---------------------------------------------------------------------------
# T2: Fallback to empty strings when no transcript entry
# ---------------------------------------------------------------------------


class TestLiveEnrichmentNoEntry:
    """Without a matching transcript entry, empty strings are used."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    @pytest.mark.asyncio
    async def test_no_transcript_entry_empty_strings(self) -> None:
        """When store has no entry for the agent, input/output_summary are empty."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            # No transcript entry recorded -- store is empty
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Read",
                    total_tokens=500, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1
            inv = state.tool_invocations[0]
            assert inv.input_summary == ""
            assert inv.output_summary == ""

    @pytest.mark.asyncio
    async def test_tool_name_mismatch_empty_strings(self) -> None:
        """When transcript entry tool_name differs from msg.last_tool, fallback."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            # Record an entry for "Bash" but progress says "Read"
            _record_transcript_entry(
                AGENT_ID, "Bash",
                tool_input={"command": "ls"},
                tool_response="output",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Read",
                    total_tokens=500, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1
            inv = state.tool_invocations[0]
            # Mismatch -- empty strings
            assert inv.input_summary == ""
            assert inv.output_summary == ""


# ---------------------------------------------------------------------------
# T3: Multiple progress events accumulate enriched invocations
# ---------------------------------------------------------------------------


class TestLiveEnrichmentMultiple:
    """Multiple progress events each get their own enriched invocation."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    @pytest.mark.asyncio
    async def test_sequential_tools_enriched(self) -> None:
        """Two sequential tool calls both get enrichment data."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            # First tool call
            _record_transcript_entry(
                AGENT_ID, "Read",
                tool_input={"file_path": "/src/a.py"},
                tool_response="contents of a",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Read",
                    total_tokens=500, tool_uses=1,
                )
            )
            await pilot.pause()

            # Second tool call
            _record_transcript_entry(
                AGENT_ID, "Edit",
                tool_input={
                    "file_path": "/src/a.py",
                    "old_string": "x",
                    "new_string": "y",
                },
                tool_response="edited",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Edit",
                    total_tokens=1000, tool_uses=2,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 2

            inv0 = state.tool_invocations[0]
            assert inv0.name == "Read"
            assert "a.py" in inv0.input_summary
            assert inv0.output_summary == "contents of a"

            inv1 = state.tool_invocations[1]
            assert inv1.name == "Edit"
            assert "a.py" in inv1.input_summary
            assert inv1.output_summary == "edited"


# ---------------------------------------------------------------------------
# T4: Backward compatibility -- existing behavior preserved
# ---------------------------------------------------------------------------


class TestLiveEnrichmentBackwardCompat:
    """Existing behavior is preserved: tool_count gating, category assignment."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    @pytest.mark.asyncio
    async def test_duplicate_tool_uses_not_double_recorded(self) -> None:
        """Same tool_uses count does not create duplicate invocations."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            _record_transcript_entry(
                AGENT_ID, "Read",
                tool_input={"file_path": "/a.py"},
                tool_response="data",
            )
            # First progress
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Read",
                    total_tokens=500, tool_uses=1,
                )
            )
            await pilot.pause()

            # Same tool_uses=1 again -- should not create another invocation
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Read",
                    total_tokens=600, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1

    @pytest.mark.asyncio
    async def test_category_assigned_correctly(self) -> None:
        """ToolInvocation still gets correct category from categorize_tool."""
        async with ClouApp().run_test() as pilot:
            await _setup_active_agent(pilot)

            _record_transcript_entry(
                AGENT_ID, "Grep",
                tool_input={"pattern": "def main"},
                tool_response="matches",
            )
            pilot.app.post_message(
                ClouAgentProgress(
                    task_id=AGENT_ID, last_tool="Grep",
                    total_tokens=500, tool_uses=1,
                )
            )
            await pilot.pause()

            tg = pilot.app.query_one(TaskGraphWidget)
            assert tg._model is not None
            state = tg._model.task_states[TASK_NAME]
            assert len(state.tool_invocations) == 1
            assert state.tool_invocations[0].category == "searches"


# ---------------------------------------------------------------------------
# T5: Unmapped agent enrichment through on_clou_agent_progress
# ---------------------------------------------------------------------------

UNMAPPED_AGENT_ID = "agent-unmapped"
UNMAPPED_DESCRIPTION = "unrelated_work"


class TestUnmappedAgentEnrichment:
    """When an agent does not match any DAG task, it lands in unmapped_agents.
    on_clou_agent_progress should still populate ToolInvocations with
    input_summary and output_summary from the transcript store."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    @pytest.mark.asyncio
    async def test_unmapped_agent_enriched_from_transcript(self) -> None:
        """Progress for an unmapped agent enriches via transcript lookup."""
        async with ClouApp().run_test() as pilot:
            app = pilot.app
            # 1. Set up DAG with tasks that will NOT match the agent.
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            app.post_message(
                ClouDagUpdate(tasks=SAMPLE_TASKS, deps=SAMPLE_DEPS)
            )
            await pilot.pause()

            # 2. Spawn agent with a description that doesn't match any task.
            #    _activate_agent will put it in model.unmapped_agents.
            app.post_message(
                ClouAgentSpawned(
                    task_id=UNMAPPED_AGENT_ID,
                    description=UNMAPPED_DESCRIPTION,
                )
            )
            await pilot.pause()

            # Verify the agent landed in unmapped_agents, not task_states.
            tg = app.query_one(TaskGraphWidget)
            assert tg._model is not None
            unmapped_key = f"{UNMAPPED_DESCRIPTION}:{UNMAPPED_AGENT_ID}"
            assert unmapped_key not in tg._model.task_states
            assert unmapped_key in tg._model.unmapped_agents

            # 3. Record a transcript entry for this agent with enrichment data.
            _record_transcript_entry(
                UNMAPPED_AGENT_ID,
                "Read",
                tool_input={"file_path": "/home/user/config.yaml"},
                tool_response="key: value\nhost: localhost",
            )

            # 4. Send progress -- handler should look up transcript and enrich.
            app.post_message(
                ClouAgentProgress(
                    task_id=UNMAPPED_AGENT_ID,
                    last_tool="Read",
                    total_tokens=600,
                    tool_uses=1,
                )
            )
            await pilot.pause()

            # 5. Verify the unmapped agent's ToolInvocation is enriched.
            state = tg._model.unmapped_agents[unmapped_key]
            assert len(state.tool_invocations) == 1
            inv = state.tool_invocations[0]
            assert inv.name == "Read"
            assert "config.yaml" in inv.input_summary
            assert inv.output_summary == "key: value\nhost: localhost"
