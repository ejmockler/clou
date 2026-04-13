"""Tests for clou.ui.enrich -- transcript enrichment of ToolInvocations."""

from __future__ import annotations

import time

from clou.transcript import TranscriptEntry, TranscriptStore, get_store, reset_store
from clou.ui.enrich import enrich_invocations
from clou.ui.rendering.tool_summary import tool_summary
from clou.ui.task_graph import (
    MAX_TOOL_HISTORY,
    TaskGraphModel,
    TaskState,
    ToolInvocation,
    categorize_tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    tool_name: str = "Read",
    tool_input: dict[str, object] | None = None,
    tool_response: str = "",
    timestamp: float | None = None,
) -> TranscriptEntry:
    return TranscriptEntry(
        tool_name=tool_name,
        tool_input=tool_input if tool_input is not None else {},
        tool_response=tool_response,
        timestamp=timestamp if timestamp is not None else time.monotonic(),
    )


def _setup_store_with_entries(
    task_name: str,
    entries: list[TranscriptEntry],
    agent_id: str = "agent-1",
) -> TranscriptStore:
    """Reset the singleton store, populate it, and return it."""
    reset_store()
    store = get_store()
    store.register_task_mapping(agent_id, task_name)
    for e in entries:
        store.record(agent_id, e)
    return store


# ---------------------------------------------------------------------------
# T1: enrich_invocations for completed agents
# ---------------------------------------------------------------------------


class TestEnrichCompletedAgent:
    """When an agent is completed, invocations are rebuilt from transcript."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_enrich_completed_agent(self) -> None:
        entries = [
            _entry("Read", {"file_path": "/tmp/a.py"}, "file contents", 100.0),
            _entry("Edit", {"file_path": "/tmp/a.py", "old_string": "x", "new_string": "y"}, "ok", 101.0),
        ]
        _setup_store_with_entries("my_task", entries)

        state = TaskState(status="complete")
        enrich_invocations("my_task", state)

        assert len(state.tool_invocations) == 2
        assert state.tool_invocations[0].name == "Read"
        assert state.tool_invocations[1].name == "Edit"

    def test_enrich_failed_agent(self) -> None:
        entries = [_entry("Bash", {"command": "ls"}, "output", 100.0)]
        _setup_store_with_entries("fail_task", entries)

        state = TaskState(status="failed")
        enrich_invocations("fail_task", state)

        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0].name == "Bash"

    def test_enrich_aborted_agent(self) -> None:
        entries = [_entry("Read", {}, "", 100.0)]
        _setup_store_with_entries("abort_task", entries)

        state = TaskState(status="aborted")
        enrich_invocations("abort_task", state)

        assert len(state.tool_invocations) == 1

    def test_enrich_completed_with_completed_status(self) -> None:
        """The 'completed' variant (not just 'complete') is also enriched."""
        entries = [_entry("Grep", {"pattern": "foo"}, "match", 100.0)]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="completed")
        enrich_invocations("t", state)

        assert len(state.tool_invocations) == 1


# ---------------------------------------------------------------------------
# T2: Active agents are NOT enriched
# ---------------------------------------------------------------------------


class TestEnrichActiveAgentSkipped:
    """Active (and pending) agents should NOT have invocations enriched."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_active_agent_not_enriched(self) -> None:
        entries = [_entry("Read", {}, "data", 100.0)]
        _setup_store_with_entries("active_task", entries)

        original_inv = ToolInvocation(name="Read", timestamp=99.0, category="reads")
        state = TaskState(status="active", tool_invocations=[original_inv])
        enrich_invocations("active_task", state)

        # Should still be the original single invocation
        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0] is original_inv

    def test_pending_agent_not_enriched(self) -> None:
        entries = [_entry("Read", {}, "data", 100.0)]
        _setup_store_with_entries("pending_task", entries)

        state = TaskState(status="pending")
        enrich_invocations("pending_task", state)

        assert len(state.tool_invocations) == 0


# ---------------------------------------------------------------------------
# T3: No transcript data -- fallback
# ---------------------------------------------------------------------------


class TestEnrichNoTranscriptData:
    """When TranscriptStore has no data, existing invocations are preserved."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_empty_store_preserves_invocations(self) -> None:
        original_inv = ToolInvocation(
            name="Read", timestamp=100.0, category="reads", input_summary="old"
        )
        state = TaskState(status="complete", tool_invocations=[original_inv])
        enrich_invocations("unmapped_task", state)

        # Existing invocations preserved -- not replaced with empty list
        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0] is original_inv

    def test_task_not_mapped_preserves_invocations(self) -> None:
        """Task name has no agent mapping in the store."""
        reset_store()
        store = get_store()
        # Register a different task
        store.register_task_mapping("agent-1", "other_task")
        store.record("agent-1", _entry("Read", {}, "data"))

        original_inv = ToolInvocation(name="Edit", timestamp=100.0, category="writes")
        state = TaskState(status="complete", tool_invocations=[original_inv])
        enrich_invocations("my_task", state)

        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0] is original_inv


# ---------------------------------------------------------------------------
# T4: input_summary uses tool_summary
# ---------------------------------------------------------------------------


class TestEnrichInputSummary:
    """Verify that input_summary is generated via tool_summary()."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_read_shows_filename(self) -> None:
        entries = [_entry("Read", {"file_path": "/home/user/src/app.py"}, "contents")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert "app.py" in state.tool_invocations[0].input_summary

    def test_edit_shows_file_and_stats(self) -> None:
        entries = [_entry("Edit", {
            "file_path": "/tmp/main.py",
            "old_string": "foo",
            "new_string": "bar\nbaz",
        }, "ok")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        summary = state.tool_invocations[0].input_summary
        assert "main.py" in summary
        assert "Edit" in summary

    def test_bash_shows_command(self) -> None:
        entries = [_entry("Bash", {"command": "git status"}, "output")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert "git status" in state.tool_invocations[0].input_summary

    def test_grep_shows_pattern(self) -> None:
        entries = [_entry("Grep", {"pattern": "def main"}, "matches")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert "def main" in state.tool_invocations[0].input_summary

    def test_empty_tool_input_returns_tool_name(self) -> None:
        entries = [_entry("Read", {}, "")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        # tool_summary("Read", {}) returns just "Read"
        assert state.tool_invocations[0].input_summary == "Read"


# ---------------------------------------------------------------------------
# T5: output_summary from tool_response
# ---------------------------------------------------------------------------


class TestEnrichOutputSummary:
    """Verify tool_response becomes output_summary."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_output_summary_from_response(self) -> None:
        entries = [_entry("Bash", {"command": "echo hello"}, "hello world")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert state.tool_invocations[0].output_summary == "hello world"

    def test_empty_response_preserved(self) -> None:
        entries = [_entry("Read", {}, "")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert state.tool_invocations[0].output_summary == ""

    def test_multiline_response_preserved(self) -> None:
        entries = [_entry("Bash", {"command": "ls"}, "file1\nfile2\nfile3")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert "file1\nfile2\nfile3" == state.tool_invocations[0].output_summary


# ---------------------------------------------------------------------------
# T6: Idempotency
# ---------------------------------------------------------------------------


class TestEnrichIdempotent:
    """Enriching twice produces the same result."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_double_enrichment_same_result(self) -> None:
        entries = [
            _entry("Read", {"file_path": "/a.py"}, "c1", 100.0),
            _entry("Bash", {"command": "test"}, "ok", 101.0),
        ]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)
        first_pass = [
            (inv.name, inv.input_summary, inv.output_summary)
            for inv in state.tool_invocations
        ]

        enrich_invocations("t", state)
        second_pass = [
            (inv.name, inv.input_summary, inv.output_summary)
            for inv in state.tool_invocations
        ]

        assert first_pass == second_pass


# ---------------------------------------------------------------------------
# T7: Category preservation
# ---------------------------------------------------------------------------


class TestEnrichPreservesCategory:
    """categorize_tool() is called for each entry, assigning correct categories."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_categories_assigned(self) -> None:
        entries = [
            _entry("Read", {}, ""),
            _entry("Edit", {}, ""),
            _entry("Bash", {}, ""),
            _entry("Grep", {}, ""),
            _entry("UnknownTool", {}, ""),
        ]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        expected_categories = ["reads", "writes", "shell", "searches", "other"]
        actual_categories = [inv.category for inv in state.tool_invocations]
        assert actual_categories == expected_categories

    def test_category_matches_categorize_tool(self) -> None:
        entries = [_entry("Write", {}, "")]
        _setup_store_with_entries("t", entries)

        state = TaskState(status="complete")
        enrich_invocations("t", state)

        assert state.tool_invocations[0].category == categorize_tool("Write")


# ---------------------------------------------------------------------------
# T8: Defensive -- fewer transcript entries than existing invocations
# ---------------------------------------------------------------------------


class TestEnrichDefensiveFewer:
    """When transcript has fewer entries than existing, keep existing."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_fewer_transcript_entries_keeps_existing(self) -> None:
        # Transcript has 1 entry
        entries = [_entry("Read", {}, "data")]
        _setup_store_with_entries("t", entries)

        # But state already has 3 invocations
        existing = [
            ToolInvocation(name="Read", timestamp=100.0, category="reads"),
            ToolInvocation(name="Edit", timestamp=101.0, category="writes"),
            ToolInvocation(name="Bash", timestamp=102.0, category="shell"),
        ]
        state = TaskState(status="complete", tool_invocations=existing)
        enrich_invocations("t", state)

        # Existing invocations preserved
        assert len(state.tool_invocations) == 3
        assert state.tool_invocations[0] is existing[0]


# ---------------------------------------------------------------------------
# T9: Fallback agent_id lookup for unmapped agents (F3 rework)
# ---------------------------------------------------------------------------


class TestEnrichFallbackAgentId:
    """Unmapped agents with synthetic keys like 'describe:agent-123' should
    fall back to looking up entries by the agent_id suffix."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_unmapped_agent_enriches_from_agent_id(self) -> None:
        """Synthetic key 'describe:agent-123' enriches via agent_id fallback."""
        reset_store()
        store = get_store()
        # Record entries directly by agent_id -- no task mapping registered.
        store.record("agent-123", _entry("Read", {"file_path": "/a.py"}, "contents", 100.0))
        store.record("agent-123", _entry("Bash", {"command": "ls"}, "files", 101.0))

        state = TaskState(status="complete")
        enrich_invocations("describe:agent-123", state)

        assert len(state.tool_invocations) == 2
        assert state.tool_invocations[0].name == "Read"
        assert state.tool_invocations[1].name == "Bash"
        # Verify real summaries are populated (not empty placeholder text)
        assert "a.py" in state.tool_invocations[0].input_summary
        assert state.tool_invocations[1].output_summary == "files"

    def test_unmapped_agent_no_data_preserves_invocations(self) -> None:
        """Synthetic key with no transcript data at all keeps existing invocations."""
        reset_store()
        original_inv = ToolInvocation(
            name="Edit", timestamp=100.0, category="writes", input_summary="old edit"
        )
        state = TaskState(status="complete", tool_invocations=[original_inv])
        enrich_invocations("describe:agent-999", state)

        # Existing invocations preserved -- not replaced with empty list
        assert len(state.tool_invocations) == 1
        assert state.tool_invocations[0] is original_inv


# ---------------------------------------------------------------------------
# B: add_tool_call with enrichment data (live path API)
# ---------------------------------------------------------------------------


class TestAddToolCallEnrichment:
    """Verify add_tool_call accepts and stores enrichment data."""

    def _make_model(self) -> TaskGraphModel:
        return TaskGraphModel(
            tasks=[{"name": "build"}, {"name": "test"}],
            deps={"test": ["build"]},
        )

    def test_add_tool_call_with_output_summary(self) -> None:
        """output_summary is stored on the ToolInvocation."""
        model = self._make_model()
        inv = model.add_tool_call("build", "Read", "Read app.py", output_summary="file contents here")
        assert inv is not None
        assert inv.output_summary == "file contents here"

    def test_add_tool_call_backward_compatible(self) -> None:
        """Without output_summary, it defaults to empty string."""
        model = self._make_model()
        inv = model.add_tool_call("build", "Bash", "Bash ls")
        assert inv is not None
        assert inv.output_summary == ""

    def test_add_tool_call_with_input_and_output(self) -> None:
        """Both input_summary (brief) and output_summary are stored."""
        model = self._make_model()
        inv = model.add_tool_call(
            "build", "Edit", "Edit main.py  +2 -1",
            output_summary="ok",
        )
        assert inv is not None
        assert inv.input_summary == "Edit main.py  +2 -1"
        assert inv.output_summary == "ok"


# ---------------------------------------------------------------------------
# C: Live enrichment integration
# ---------------------------------------------------------------------------


class TestLiveEnrichmentIntegration:
    """Simulate the live enrichment path end-to-end: record transcript,
    look up via get_latest_entry, compute tool_summary, pass to add_tool_call."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_live_enrichment_produces_input_summary(self) -> None:
        """Live path: transcript entry -> tool_summary -> add_tool_call input_summary."""
        store = get_store()
        entry = TranscriptEntry(
            tool_name="Read",
            tool_input={"file_path": "/home/user/src/app.py"},
            tool_response="file contents",
            timestamp=100.0,
        )
        store.record("agent-1", entry)

        # Simulate bridge lookup
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        summary = tool_summary(latest.tool_name, latest.tool_input)

        model = TaskGraphModel(
            tasks=[{"name": "build"}],
            deps={},
        )
        inv = model.add_tool_call(
            "build", latest.tool_name, summary,
            output_summary=latest.tool_response,
        )
        assert inv is not None
        assert "app.py" in inv.input_summary

    def test_live_enrichment_produces_output_summary(self) -> None:
        """Live path: tool_response from transcript becomes output_summary."""
        store = get_store()
        entry = TranscriptEntry(
            tool_name="Bash",
            tool_input={"command": "echo hello"},
            tool_response="hello world",
            timestamp=100.0,
        )
        store.record("agent-1", entry)

        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        summary = tool_summary(latest.tool_name, latest.tool_input)

        model = TaskGraphModel(
            tasks=[{"name": "build"}],
            deps={},
        )
        inv = model.add_tool_call(
            "build", latest.tool_name, summary,
            output_summary=latest.tool_response,
        )
        assert inv is not None
        assert inv.output_summary == "hello world"

    def test_live_enrichment_fallback_no_entry(self) -> None:
        """When get_latest_entry returns None, add_tool_call with empty strings."""
        store = get_store()
        latest = store.get_latest_entry("nonexistent-agent")
        assert latest is None

        model = TaskGraphModel(
            tasks=[{"name": "build"}],
            deps={},
        )
        # Fallback: no transcript data, pass empty summaries
        inv = model.add_tool_call("build", "Read", "", output_summary="")
        assert inv is not None
        assert inv.input_summary == ""
        assert inv.output_summary == ""

    def test_live_enrichment_fallback_tool_mismatch(self) -> None:
        """When entry.tool_name != last_tool, fall back to empty strings."""
        store = get_store()
        entry = TranscriptEntry(
            tool_name="Bash",
            tool_input={"command": "ls"},
            tool_response="files",
            timestamp=100.0,
        )
        store.record("agent-1", entry)

        latest = store.get_latest_entry("agent-1")
        assert latest is not None

        # Simulate bridge checking: last_tool reported is "Edit" but transcript has "Bash"
        last_tool = "Edit"
        if latest.tool_name != last_tool:
            # Mismatch -> fall back to empty
            input_summary = ""
            output_summary = ""
        else:
            input_summary = tool_summary(latest.tool_name, latest.tool_input)
            output_summary = latest.tool_response

        model = TaskGraphModel(
            tasks=[{"name": "build"}],
            deps={},
        )
        inv = model.add_tool_call(
            "build", last_tool, input_summary,
            output_summary=output_summary,
        )
        assert inv is not None
        assert inv.input_summary == ""
        assert inv.output_summary == ""


# ---------------------------------------------------------------------------
# D: Post-hoc enrichment regression
# ---------------------------------------------------------------------------


class TestPostHocEnrichmentRegression:
    """Verify post-hoc enrichment still works correctly after live enrichment
    has populated invocations."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_post_hoc_enrichment_still_works_after_live(self) -> None:
        """Live enrichment populates invocations, then post-hoc enrichment
        runs on completion. Post-hoc should produce correct results."""
        entries = [
            _entry("Read", {"file_path": "/tmp/a.py"}, "contents", 100.0),
            _entry("Edit", {"file_path": "/tmp/a.py", "old_string": "x", "new_string": "y"}, "ok", 101.0),
        ]
        _setup_store_with_entries("my_task", entries)

        # Simulate live enrichment: add_tool_call populates invocations
        model = TaskGraphModel(
            tasks=[{"name": "my_task"}],
            deps={},
        )
        model.activate_task("my_task", "agent-1")
        for e in entries:
            summary = tool_summary(e.tool_name, e.tool_input)
            model.add_tool_call(
                "my_task", e.tool_name, summary,
                output_summary=e.tool_response,
            )

        state = model.task_states["my_task"]
        assert len(state.tool_invocations) == 2

        # Agent completes -> post-hoc enrichment runs
        state.status = "complete"
        enrich_invocations("my_task", state)

        # Post-hoc produces correct results from transcript
        assert len(state.tool_invocations) == 2
        assert state.tool_invocations[0].name == "Read"
        assert state.tool_invocations[1].name == "Edit"
        assert "a.py" in state.tool_invocations[0].input_summary

    def test_post_hoc_replaces_live_invocations(self) -> None:
        """enrich_invocations rebuilds from transcript even when live invocations
        already have data. The rebuilt list should be equivalent."""
        entries = [
            _entry("Bash", {"command": "echo hello"}, "hello", 100.0),
            _entry("Read", {"file_path": "/tmp/b.py"}, "code", 101.0),
            _entry("Grep", {"pattern": "def main"}, "found", 102.0),
        ]
        _setup_store_with_entries("task_x", entries)

        # Simulate: live path already populated 3 invocations
        state = TaskState(
            status="complete",
            tool_invocations=[
                ToolInvocation(name="Bash", timestamp=100.0, category="shell",
                               input_summary="Bash echo hello", output_summary="hello"),
                ToolInvocation(name="Read", timestamp=101.0, category="reads",
                               input_summary="Read b.py", output_summary="code"),
                ToolInvocation(name="Grep", timestamp=102.0, category="searches",
                               input_summary="Grep /def main/", output_summary="found"),
            ],
        )

        enrich_invocations("task_x", state)

        # Post-hoc rebuilds from transcript -- same tool names, same count
        assert len(state.tool_invocations) == 3
        assert [inv.name for inv in state.tool_invocations] == ["Bash", "Read", "Grep"]
        # Output summaries come from transcript entries
        assert state.tool_invocations[0].output_summary == "hello"
        assert state.tool_invocations[1].output_summary == "code"
        assert state.tool_invocations[2].output_summary == "found"


# ---------------------------------------------------------------------------
# E: Edge cases
# ---------------------------------------------------------------------------


class TestEnrichmentEdgeCases:
    """Edge cases for the enrichment path."""

    def setup_method(self) -> None:
        reset_store()

    def teardown_method(self) -> None:
        reset_store()

    def test_enriched_invocation_categories_correct(self) -> None:
        """Live-enriched ToolInvocations have correct categories from categorize_tool."""
        model = TaskGraphModel(
            tasks=[{"name": "build"}],
            deps={},
        )
        tools = ["Read", "Edit", "Bash", "Grep", "Write", "WebSearch", "UnknownTool"]
        for t in tools:
            model.add_tool_call("build", t, f"{t} something", output_summary="out")

        invocations = model.task_states["build"].tool_invocations
        expected = [categorize_tool(t) for t in tools]
        actual = [inv.category for inv in invocations]
        assert actual == expected

    def test_max_tool_history_with_enrichment(self) -> None:
        """MAX_TOOL_HISTORY enforcement still works when invocations carry enrichment data."""
        model = TaskGraphModel(
            tasks=[{"name": "build"}],
            deps={},
        )
        for i in range(MAX_TOOL_HISTORY + 50):
            model.add_tool_call(
                "build", "Bash", f"cmd-{i}",
                output_summary=f"out-{i}",
            )

        invocations = model.task_states["build"].tool_invocations
        assert len(invocations) == MAX_TOOL_HISTORY
        # Oldest should have been evicted; last one is the most recent
        assert invocations[-1].input_summary == f"cmd-{MAX_TOOL_HISTORY + 49}"
        assert invocations[-1].output_summary == f"out-{MAX_TOOL_HISTORY + 49}"
        # First retained should be at index 50 (total - MAX)
        assert invocations[0].input_summary == "cmd-50"
        assert invocations[0].output_summary == "out-50"
