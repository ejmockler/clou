"""Tests for live enrichment flow -- end-to-end coverage of all enrichment paths.

Exercises the full data path from transcript capture through to enriched
ToolInvocations: live recording via add_tool_call(output_summary=...),
post-hoc enrichment via enrich_invocations, and fallback/edge cases.

Covers intent 3 from milestone 28-realtime-enrichment compose.py.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from clou.hooks import build_hooks
from clou.transcript import (
    TranscriptEntry,
    TranscriptStore,
    get_store,
    reset_store,
    truncate_output,
)
from clou.ui.enrich import enrich_invocations
from clou.ui.task_graph import (
    TaskGraphModel,
    TaskState,
    ToolInvocation,
    categorize_tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SAMPLE_TASKS = [
    {"name": "build_model", "status": "pending"},
    {"name": "build_widget", "status": "pending"},
]
_SAMPLE_DEPS: dict[str, list[str]] = {
    "build_model": [],
    "build_widget": ["build_model"],
}


def _make_model(
    tasks: list[dict[str, str]] | None = None,
    deps: dict[str, list[str]] | None = None,
) -> TaskGraphModel:
    return TaskGraphModel(
        tasks if tasks is not None else _SAMPLE_TASKS,
        deps if deps is not None else _SAMPLE_DEPS,
    )


def _entry(
    tool_name: str = "Read",
    tool_input: dict[str, object] | None = None,
    tool_response: str = "",
    timestamp: float | None = None,
    tool_use_id: str = "",
) -> TranscriptEntry:
    return TranscriptEntry(
        tool_name=tool_name,
        tool_input=tool_input if tool_input is not None else {},
        tool_response=tool_response,
        timestamp=timestamp if timestamp is not None else time.monotonic(),
        tool_use_id=tool_use_id,
    )


def _run(coro: object) -> dict[str, object]:
    """Run an async coroutine and return the result."""
    result: object = asyncio.run(coro)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result


def _get_transcript_hook() -> object:
    """Return the transcript PostToolUse hook callback for coordinator tier."""
    hooks = build_hooks("coordinator", Path("/tmp/project"))
    post_hooks = hooks["PostToolUse"]
    transcript_cfg = [c for c in post_hooks if c.matcher is None]
    assert len(transcript_cfg) == 1
    return transcript_cfg[0].hooks[0]


# ---------------------------------------------------------------------------
# T1: Live recording -- add_tool_call with output_summary from transcript
# ---------------------------------------------------------------------------


class TestLiveRecordingEnrichment:
    """Integration: transcript data flows into add_tool_call output_summary
    during live agent progress, simulating the bridge enrichment path."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_live_recording_with_output_summary(self) -> None:
        """Simulate the bridge: record to transcript, get_latest_entry,
        pass output_summary to add_tool_call."""
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")

        # Simulate PostToolUse hook recording
        entry = _entry(
            "Read",
            {"file_path": "/src/app.py"},
            "def main(): pass",
            100.0,
        )
        store.record("agent-1", entry)

        # Simulate bridge: get latest entry, extract output_summary
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        inv = model.add_tool_call(
            "build_model",
            latest.tool_name,
            "",
            output_summary=latest.tool_response,
        )

        assert inv is not None
        assert inv.name == "Read"
        assert inv.output_summary == "def main(): pass"
        assert inv.category == "reads"

    def test_live_recording_sequential_tools(self) -> None:
        """Multiple tool calls accumulate in both transcript and model."""
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")

        tools = [
            ("Read", {"file_path": "/a.py"}, "contents-a"),
            ("Edit", {"file_path": "/a.py", "old_string": "x", "new_string": "y"}, "ok"),
            ("Bash", {"command": "pytest"}, "3 passed"),
        ]

        for i, (name, inp, resp) in enumerate(tools):
            entry = _entry(name, inp, resp, 100.0 + i)
            store.record("agent-1", entry)
            latest = store.get_latest_entry("agent-1")
            assert latest is not None
            model.add_tool_call(
                "build_model", latest.tool_name, "",
                output_summary=latest.tool_response,
            )

        invs = model.task_states["build_model"].tool_invocations
        assert len(invs) == 3
        assert invs[0].output_summary == "contents-a"
        assert invs[1].output_summary == "ok"
        assert invs[2].output_summary == "3 passed"

    def test_live_recording_empty_response(self) -> None:
        """Empty tool response produces empty output_summary."""
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")

        store.record("agent-1", _entry("Glob", {"pattern": "*.py"}, ""))
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        inv = model.add_tool_call(
            "build_model", latest.tool_name, "",
            output_summary=latest.tool_response,
        )
        assert inv is not None
        assert inv.output_summary == ""

    def test_get_latest_entry_returns_most_recent(self) -> None:
        """get_latest_entry always returns the most recent entry for correlation."""
        store = get_store()
        store.record("agent-1", _entry("Read", {}, "first", 100.0))
        store.record("agent-1", _entry("Edit", {}, "second", 101.0))
        store.record("agent-1", _entry("Bash", {}, "third", 102.0))

        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        assert latest.tool_name == "Bash"
        assert latest.tool_response == "third"

    def test_live_recording_parallel_agents(self) -> None:
        """Two agents recording simultaneously keep independent transcripts."""
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.activate_task("build_widget", "agent-2")

        store.record("agent-1", _entry("Read", {}, "model-data", 100.0))
        store.record("agent-2", _entry("Bash", {}, "widget-data", 100.5))

        latest_1 = store.get_latest_entry("agent-1")
        latest_2 = store.get_latest_entry("agent-2")
        assert latest_1 is not None
        assert latest_2 is not None

        inv1 = model.add_tool_call(
            "build_model", latest_1.tool_name, "",
            output_summary=latest_1.tool_response,
        )
        inv2 = model.add_tool_call(
            "build_widget", latest_2.tool_name, "",
            output_summary=latest_2.tool_response,
        )

        assert inv1 is not None
        assert inv1.output_summary == "model-data"
        assert inv2 is not None
        assert inv2.output_summary == "widget-data"


# ---------------------------------------------------------------------------
# T2: Post-hoc regression -- enrich_invocations produces same data
# ---------------------------------------------------------------------------


class TestPostHocRegression:
    """Verify that post-hoc enrichment via enrich_invocations produces
    data equivalent to what live recording would produce."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_posthoc_matches_live_output_summary(self) -> None:
        """Post-hoc enrichment populates output_summary from tool_response."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")
        store.record("agent-1", _entry("Read", {"file_path": "/a.py"}, "contents", 100.0))
        store.record("agent-1", _entry("Bash", {"command": "test"}, "ok", 101.0))

        state = TaskState(status="complete")
        enrich_invocations("build_model", state)

        assert len(state.tool_invocations) == 2
        assert state.tool_invocations[0].output_summary == "contents"
        assert state.tool_invocations[1].output_summary == "ok"

    def test_posthoc_matches_live_categories(self) -> None:
        """Post-hoc enrichment assigns same categories as live recording."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")

        tools = ["Read", "Edit", "Bash", "Grep", "Write"]
        for i, name in enumerate(tools):
            store.record("agent-1", _entry(name, {}, "", 100.0 + i))

        state = TaskState(status="complete")
        enrich_invocations("build_model", state)

        for inv in state.tool_invocations:
            assert inv.category == categorize_tool(inv.name)

    def test_live_and_posthoc_produce_equivalent_data(self) -> None:
        """Full comparison: build live invocations, then enrich post-hoc,
        verify key fields match."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")

        entries = [
            _entry("Read", {"file_path": "/src/main.py"}, "source code", 100.0),
            _entry("Edit", {"file_path": "/src/main.py", "old_string": "a", "new_string": "b"}, "ok", 101.0),
            _entry("Bash", {"command": "pytest"}, "5 passed", 102.0),
        ]
        for e in entries:
            store.record("agent-1", e)

        # Build live invocations
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        for e in entries:
            model.add_tool_call(
                "build_model", e.tool_name, "",
                output_summary=e.tool_response,
            )
        live_invs = model.task_states["build_model"].tool_invocations

        # Build post-hoc invocations
        posthoc_state = TaskState(status="complete")
        enrich_invocations("build_model", posthoc_state)
        posthoc_invs = posthoc_state.tool_invocations

        assert len(live_invs) == len(posthoc_invs)
        for live, posthoc in zip(live_invs, posthoc_invs):
            assert live.name == posthoc.name
            assert live.category == posthoc.category
            assert live.output_summary == posthoc.output_summary

    def test_posthoc_preserves_timestamp_ordering(self) -> None:
        """Post-hoc enrichment preserves chronological order from transcript."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")
        store.record("agent-1", _entry("Read", {}, "r1", 50.0))
        store.record("agent-1", _entry("Edit", {}, "e1", 60.0))
        store.record("agent-1", _entry("Bash", {}, "b1", 70.0))

        state = TaskState(status="complete")
        enrich_invocations("build_model", state)

        timestamps = [inv.timestamp for inv in state.tool_invocations]
        assert timestamps == sorted(timestamps)
        assert timestamps == [50.0, 60.0, 70.0]


# ---------------------------------------------------------------------------
# T3: Fallback and edge cases
# ---------------------------------------------------------------------------


class TestFallbackEdgeCases:
    """Edge cases: missing data, bounded eviction, clear between cycles."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_get_latest_entry_nonexistent_agent(self) -> None:
        """get_latest_entry returns None for unknown agent_id."""
        store = get_store()
        assert store.get_latest_entry("nonexistent") is None

    def test_get_latest_entry_after_clear(self) -> None:
        """After store.clear(), get_latest_entry returns None."""
        store = get_store()
        store.record("agent-1", _entry("Read", {}, "data"))
        store.clear()
        assert store.get_latest_entry("agent-1") is None

    def test_add_tool_call_unknown_task_returns_none(self) -> None:
        """add_tool_call with output_summary on unknown task returns None."""
        model = _make_model()
        result = model.add_tool_call(
            "nonexistent", "Read", "brief", output_summary="data"
        )
        assert result is None

    def test_enrich_after_clear_preserves_existing(self) -> None:
        """If transcript store is cleared before enrichment, existing
        invocations are preserved."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")
        store.record("agent-1", _entry("Read", {}, "data", 100.0))
        store.clear()  # Simulate cycle boundary

        existing = ToolInvocation(
            name="Read", timestamp=100.0, category="reads",
            input_summary="old", output_summary="old-data",
        )
        state = TaskState(status="complete", tool_invocations=[existing])
        enrich_invocations("build_model", state)

        # Existing invocations preserved since store is empty
        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0] is existing

    def test_bounded_eviction_latest_still_correct(self) -> None:
        """After bounded eviction, get_latest_entry still returns the
        most recent entry (not an evicted one)."""
        store = TranscriptStore()
        for i in range(store.MAX_ENTRIES_PER_AGENT + 100):
            store.record("agent-1", _entry("Bash", {}, f"output-{i}"))
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        expected_last = store.MAX_ENTRIES_PER_AGENT + 99
        assert latest.tool_response == f"output-{expected_last}"

    def test_bounded_eviction_enrich_still_works(self) -> None:
        """After bounded eviction, enrichment uses surviving entries."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")
        total = TranscriptStore.MAX_ENTRIES_PER_AGENT + 50
        for i in range(total):
            store.record("agent-1", _entry("Read", {}, f"resp-{i}", float(i)))

        state = TaskState(status="complete")
        enrich_invocations("build_model", state)

        # Should have exactly MAX_ENTRIES_PER_AGENT entries
        assert len(state.tool_invocations) == TranscriptStore.MAX_ENTRIES_PER_AGENT
        # First surviving entry should be index 50
        assert state.tool_invocations[0].output_summary == "resp-50"

    def test_unmapped_agent_fallback_with_colon_key(self) -> None:
        """Unmapped agents with 'describe:agent-id' keys fall back
        to direct agent_id lookup."""
        store = get_store()
        store.record("agent-42", _entry("Read", {"file_path": "/x.py"}, "code", 100.0))

        state = TaskState(status="complete")
        enrich_invocations("describe:agent-42", state)

        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0].name == "Read"
        assert state.tool_invocations[0].output_summary == "code"

    def test_no_colon_unmapped_preserves_existing(self) -> None:
        """Task name without colon and no mapping preserves existing invocations."""
        store = get_store()
        # No mapping registered, no entries
        existing = ToolInvocation(name="Edit", timestamp=1.0, category="writes")
        state = TaskState(status="complete", tool_invocations=[existing])
        enrich_invocations("unknown_task", state)
        assert state.tool_invocations[0] is existing


# ---------------------------------------------------------------------------
# T4: Live recording via add_tool_call backward compatibility
# ---------------------------------------------------------------------------


class TestAddToolCallBackwardCompat:
    """Existing callers of add_tool_call without output_summary still work."""

    def test_three_arg_call_defaults_output_summary_empty(self) -> None:
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        inv = model.add_tool_call("build_model", "Read", "Read file.py")
        assert inv is not None
        assert inv.output_summary == ""
        assert inv.input_summary == "Read file.py"

    def test_mixed_calls_with_and_without_output_summary(self) -> None:
        """Some calls with output_summary, some without -- all coexist."""
        model = _make_model()
        model.activate_task("build_model", "agent-1")

        inv1 = model.add_tool_call("build_model", "Read", "Read a.py")
        inv2 = model.add_tool_call(
            "build_model", "Bash", "run tests",
            output_summary="5 passed",
        )
        inv3 = model.add_tool_call("build_model", "Edit", "Edit b.py")

        assert inv1 is not None and inv1.output_summary == ""
        assert inv2 is not None and inv2.output_summary == "5 passed"
        assert inv3 is not None and inv3.output_summary == ""

    def test_unmapped_agent_receives_output_summary(self) -> None:
        """add_tool_call on unmapped_agents also stores output_summary."""
        model = _make_model()
        model.unmapped_agents["agent-99"] = TaskState(
            status="active", agent_id="agent-99",
        )
        inv = model.add_tool_call(
            "agent-99", "Read", "Read c.py", output_summary="42 lines",
        )
        assert inv is not None
        assert inv.output_summary == "42 lines"

    def test_tool_calls_property_backward_compat(self) -> None:
        """The tool_calls property returns (name, input_summary) tuples,
        ignoring output_summary -- backward-compatible."""
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        model.add_tool_call(
            "build_model", "Read", "Read a.py", output_summary="contents",
        )
        model.add_tool_call("build_model", "Edit", "Edit b.py")

        calls = model.task_states["build_model"].tool_calls
        assert calls == [("Read", "Read a.py"), ("Edit", "Edit b.py")]


# ---------------------------------------------------------------------------
# T5: get_latest_entry correlation with transcript hook
# ---------------------------------------------------------------------------


class TestGetLatestEntryCorrelation:
    """Tests that get_latest_entry correctly correlates with hook-recorded
    entries for the live enrichment bridge."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_hook_recorded_entry_available_via_get_latest(self) -> None:
        """Entry recorded by transcript hook is immediately available
        via get_latest_entry for bridge correlation."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-abc",
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hello"},
                    "tool_response": "hello",
                },
                "tool-1",
                {},
            )
        )
        store = get_store()
        latest = store.get_latest_entry("agent-abc")
        assert latest is not None
        assert latest.tool_name == "Bash"
        assert latest.tool_response == "hello"

    def test_hook_successive_calls_latest_updates(self) -> None:
        """After multiple hook calls, get_latest_entry returns the last one."""
        hook = _get_transcript_hook()
        for name in ("Read", "Edit", "Bash"):
            _run(
                hook(
                    {
                        "agent_id": "agent-abc",
                        "tool_name": name,
                        "tool_input": {},
                        "tool_response": f"resp-{name}",
                    },
                    f"tool-{name}",
                    {},
                )
            )
        latest = get_store().get_latest_entry("agent-abc")
        assert latest is not None
        assert latest.tool_name == "Bash"
        assert latest.tool_response == "resp-Bash"

    def test_hook_truncates_before_latest(self) -> None:
        """Hook truncates long responses; get_latest_entry returns truncated version."""
        hook = _get_transcript_hook()
        long_output = "x" * 5000
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "tool_response": long_output,
                },
                "tool-1",
                {},
            )
        )
        latest = get_store().get_latest_entry("agent-1")
        assert latest is not None
        assert latest.tool_response.endswith("... (truncated)")
        assert len(latest.tool_response) < len(long_output)

    def test_hook_skips_non_agent_no_latest(self) -> None:
        """Coordinator tool calls (no agent_id) do not appear in get_latest_entry."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/tmp/x.py"},
                    "tool_response": "ok",
                },
                "tool-1",
                {},
            )
        )
        # No agent_id in the hook call -> nothing recorded
        store = get_store()
        assert store.get_latest_entry("coordinator") is None
        assert store.agent_ids() == []

    def test_two_agents_independent_latest(self) -> None:
        """get_latest_entry returns independent results per agent."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": "first-agent",
                },
                "t1",
                {},
            )
        )
        _run(
            hook(
                {
                    "agent_id": "agent-2",
                    "tool_name": "Edit",
                    "tool_input": {},
                    "tool_response": "second-agent",
                },
                "t2",
                {},
            )
        )
        store = get_store()
        assert store.get_latest_entry("agent-1").tool_response == "first-agent"
        assert store.get_latest_entry("agent-2").tool_response == "second-agent"


# ---------------------------------------------------------------------------
# T6: End-to-end flow -- hook -> store -> bridge -> model -> enrich
# ---------------------------------------------------------------------------


class TestEndToEndEnrichmentFlow:
    """Full pipeline: transcript hook records data, bridge reads get_latest_entry
    and passes output_summary to add_tool_call, then post-hoc enrichment
    produces equivalent results."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_full_pipeline_live_then_posthoc(self) -> None:
        """Record via hook, enrich live, complete task, enrich post-hoc.
        Both paths should produce compatible data."""
        hook = _get_transcript_hook()
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        store.register_task_mapping("agent-1", "build_model")

        # Simulate 3 tool calls through the hook
        tool_calls = [
            {"tool_name": "Read", "tool_input": {"file_path": "/src/a.py"}, "tool_response": "code"},
            {"tool_name": "Edit", "tool_input": {"file_path": "/src/a.py", "old_string": "x", "new_string": "y"}, "tool_response": "ok"},
            {"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": "2 passed"},
        ]

        for tc in tool_calls:
            _run(
                hook(
                    {"agent_id": "agent-1", **tc},
                    f"tool-{tc['tool_name']}",
                    {},
                )
            )
            # Live enrichment: read latest entry, add to model
            latest = store.get_latest_entry("agent-1")
            assert latest is not None
            model.add_tool_call(
                "build_model", latest.tool_name, "",
                output_summary=latest.tool_response,
            )

        live_invs = model.task_states["build_model"].tool_invocations
        assert len(live_invs) == 3

        # Now complete the task and do post-hoc enrichment
        model.complete_task("build_model", "complete", "All done")
        posthoc_state = TaskState(status="complete")
        enrich_invocations("build_model", posthoc_state)

        assert len(posthoc_state.tool_invocations) == 3

        # Verify key fields match between live and post-hoc
        for live, posthoc in zip(live_invs, posthoc_state.tool_invocations):
            assert live.name == posthoc.name
            assert live.category == posthoc.category
            assert live.output_summary == posthoc.output_summary

    def test_full_pipeline_with_ansi_stripping(self) -> None:
        """ANSI codes are stripped at capture time, so both live and post-hoc
        see clean data."""
        hook = _get_transcript_hook()
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        store.register_task_mapping("agent-1", "build_model")

        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls --color"},
                    "tool_response": "\x1b[32mSUCCESS\x1b[0m: tests passed",
                },
                "tool-1",
                {},
            )
        )

        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        assert "\x1b" not in latest.tool_response

        inv = model.add_tool_call(
            "build_model", latest.tool_name, "",
            output_summary=latest.tool_response,
        )
        assert inv is not None
        assert inv.output_summary == "SUCCESS: tests passed"

        # Post-hoc should match
        posthoc_state = TaskState(status="complete")
        enrich_invocations("build_model", posthoc_state)
        assert posthoc_state.tool_invocations[0].output_summary == "SUCCESS: tests passed"

    def test_full_pipeline_with_truncation(self) -> None:
        """Long outputs are truncated at capture time, consistent across paths."""
        hook = _get_transcript_hook()
        store = get_store()
        model = _make_model()
        model.activate_task("build_model", "agent-1")
        store.register_task_mapping("agent-1", "build_model")

        long_output = "z" * 5000
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "tool_response": long_output,
                },
                "tool-1",
                {},
            )
        )

        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        assert latest.tool_response.endswith("... (truncated)")

        inv = model.add_tool_call(
            "build_model", latest.tool_name, "",
            output_summary=latest.tool_response,
        )
        assert inv is not None
        assert inv.output_summary == latest.tool_response

        # Post-hoc enrichment should produce same truncated value
        posthoc_state = TaskState(status="complete")
        enrich_invocations("build_model", posthoc_state)
        assert posthoc_state.tool_invocations[0].output_summary == latest.tool_response

    def test_cycle_boundary_clear_and_new_cycle(self) -> None:
        """After store.clear() at cycle boundary, new cycle starts fresh."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")
        store.record("agent-1", _entry("Read", {}, "old-data", 100.0))

        # Simulate cycle boundary
        store.clear()

        # New cycle
        store.register_task_mapping("agent-2", "build_model")
        store.record("agent-2", _entry("Bash", {}, "new-data", 200.0))

        state = TaskState(status="complete")
        enrich_invocations("build_model", state)

        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0].name == "Bash"
        assert state.tool_invocations[0].output_summary == "new-data"

    def test_multiple_agents_same_task_sorted(self) -> None:
        """Multiple agents for the same task produce chronologically sorted
        enrichment."""
        store = get_store()
        store.register_task_mapping("agent-1", "build_model")
        store.register_task_mapping("agent-2", "build_model")

        store.record("agent-1", _entry("Read", {}, "a1-first", 10.0))
        store.record("agent-2", _entry("Edit", {}, "a2-first", 15.0))
        store.record("agent-1", _entry("Bash", {}, "a1-second", 20.0))
        store.record("agent-2", _entry("Grep", {}, "a2-second", 25.0))

        state = TaskState(status="complete")
        enrich_invocations("build_model", state)

        assert len(state.tool_invocations) == 4
        names = [inv.name for inv in state.tool_invocations]
        assert names == ["Read", "Edit", "Bash", "Grep"]
        summaries = [inv.output_summary for inv in state.tool_invocations]
        assert summaries == ["a1-first", "a2-first", "a1-second", "a2-second"]
