"""Tests for clou.transcript -- agent transcript store data layer."""

from __future__ import annotations

from clou.transcript import (
    MAX_OUTPUT_LENGTH,
    TranscriptEntry,
    TranscriptStore,
    get_store,
    reset_store,
    strip_ansi,
    truncate_input,
    truncate_output,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    tool_name: str = "Read",
    tool_input: dict[str, object] | None = None,
    tool_response: str = "",
    tool_use_id: str = "",
) -> TranscriptEntry:
    return TranscriptEntry(
        tool_name=tool_name,
        tool_input=tool_input if tool_input is not None else {},
        tool_response=tool_response,
        tool_use_id=tool_use_id,
    )


# ---------------------------------------------------------------------------
# T1: Record and retrieve entries
# ---------------------------------------------------------------------------


class TestRecordAndGet:
    def test_record_single_entry(self) -> None:
        store = TranscriptStore()
        e = _entry("Read", {"file_path": "/tmp/a.py"}, "contents")
        store.record("agent-1", e)
        result = store.get_entries("agent-1")
        assert len(result) == 1
        assert result[0].tool_name == "Read"
        assert result[0].tool_response == "contents"

    def test_record_multiple_entries(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.record("agent-1", _entry("Edit"))
        store.record("agent-1", _entry("Bash"))
        result = store.get_entries("agent-1")
        assert len(result) == 3
        assert [e.tool_name for e in result] == ["Read", "Edit", "Bash"]

    def test_get_entries_returns_copy(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        copy1 = store.get_entries("agent-1")
        copy2 = store.get_entries("agent-1")
        assert copy1 is not copy2
        assert copy1[0] is copy2[0]  # Same entry objects, different lists

    def test_separate_agents(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.record("agent-2", _entry("Edit"))
        assert len(store.get_entries("agent-1")) == 1
        assert len(store.get_entries("agent-2")) == 1
        assert store.get_entries("agent-1")[0].tool_name == "Read"
        assert store.get_entries("agent-2")[0].tool_name == "Edit"


# ---------------------------------------------------------------------------
# T2: Bounded history
# ---------------------------------------------------------------------------


class TestBoundedHistory:
    def test_entries_capped_at_max(self) -> None:
        store = TranscriptStore()
        for i in range(600):
            store.record("agent-1", _entry("Bash", tool_response=str(i)))
        result = store.get_entries("agent-1")
        assert len(result) == TranscriptStore.MAX_ENTRIES_PER_AGENT

    def test_oldest_entries_dropped(self) -> None:
        store = TranscriptStore()
        for i in range(600):
            store.record("agent-1", _entry("Bash", tool_response=str(i)))
        result = store.get_entries("agent-1")
        # The first entry should be index 100 (600 - 500)
        assert result[0].tool_response == "100"
        assert result[-1].tool_response == "599"


# ---------------------------------------------------------------------------
# T3: Task mapping
# ---------------------------------------------------------------------------


class TestTaskMapping:
    def test_register_and_lookup(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.register_task_mapping("agent-1", "build_model")
        result = store.get_by_task("build_model")
        assert len(result) == 1
        assert result[0].tool_name == "Read"

    def test_multiple_agents_same_task(self) -> None:
        """Retried agents: multiple agent_ids map to the same task_name."""
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.record("agent-2", _entry("Edit"))
        store.register_task_mapping("agent-1", "build_model")
        store.register_task_mapping("agent-2", "build_model")
        result = store.get_by_task("build_model")
        assert len(result) == 2
        assert result[0].tool_name == "Read"
        assert result[1].tool_name == "Edit"

    def test_register_same_agent_twice_no_duplicate(self) -> None:
        store = TranscriptStore()
        store.register_task_mapping("agent-1", "build_model")
        store.register_task_mapping("agent-1", "build_model")
        # Internal list should not have duplicates
        assert store._task_to_agents["build_model"].count("agent-1") == 1


# ---------------------------------------------------------------------------
# T4: Unmapped task returns empty
# ---------------------------------------------------------------------------


class TestUnmappedTask:
    def test_get_by_task_unknown(self) -> None:
        store = TranscriptStore()
        result = store.get_by_task("nonexistent_task")
        assert result == []

    def test_get_by_task_no_entries(self) -> None:
        """Mapping exists but no entries recorded yet."""
        store = TranscriptStore()
        store.register_task_mapping("agent-1", "build_model")
        result = store.get_by_task("build_model")
        assert result == []


# ---------------------------------------------------------------------------
# T5: Unknown agent returns empty
# ---------------------------------------------------------------------------


class TestUnknownAgent:
    def test_get_entries_unknown(self) -> None:
        store = TranscriptStore()
        result = store.get_entries("nonexistent-agent")
        assert result == []


# ---------------------------------------------------------------------------
# T6: Clear resets all state
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_entries(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.register_task_mapping("agent-1", "build_model")
        store.clear()
        assert store.get_entries("agent-1") == []
        assert store.get_by_task("build_model") == []
        assert store.agent_ids() == []


# ---------------------------------------------------------------------------
# T7: Truncate output -- short text
# ---------------------------------------------------------------------------


class TestTruncateOutputShort:
    def test_short_text_unchanged(self) -> None:
        assert truncate_output("hello") == "hello"

    def test_exact_length_unchanged(self) -> None:
        text = "a" * MAX_OUTPUT_LENGTH
        assert truncate_output(text) == text


# ---------------------------------------------------------------------------
# T8: Truncate output -- long text
# ---------------------------------------------------------------------------


class TestTruncateOutputLong:
    def test_long_text_truncated(self) -> None:
        text = "x" * (MAX_OUTPUT_LENGTH + 100)
        result = truncate_output(text)
        assert result.endswith("... (truncated)")
        assert len(result) == MAX_OUTPUT_LENGTH + len("... (truncated)")

    def test_custom_max_length(self) -> None:
        result = truncate_output("abcdefghij", max_length=5)
        assert result == "abcde... (truncated)"


# ---------------------------------------------------------------------------
# T9: Truncate output -- empty string
# ---------------------------------------------------------------------------


class TestTruncateOutputEmpty:
    def test_empty_string_passthrough(self) -> None:
        assert truncate_output("") == ""


# ---------------------------------------------------------------------------
# T10: Singleton accessor
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_store_returns_same_instance(self) -> None:
        reset_store()
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2

    def test_get_store_creates_on_first_call(self) -> None:
        reset_store()
        s = get_store()
        assert isinstance(s, TranscriptStore)


# ---------------------------------------------------------------------------
# T11: Reset store
# ---------------------------------------------------------------------------


class TestResetStore:
    def test_reset_creates_fresh_instance(self) -> None:
        reset_store()
        s1 = get_store()
        reset_store()
        s2 = get_store()
        assert s1 is not s2

    def test_reset_clears_data(self) -> None:
        reset_store()
        s = get_store()
        s.record("agent-1", _entry("Read"))
        reset_store()
        s_new = get_store()
        assert s_new.get_entries("agent-1") == []


# ---------------------------------------------------------------------------
# T12: agent_ids
# ---------------------------------------------------------------------------


class TestAgentIds:
    def test_agent_ids_empty(self) -> None:
        store = TranscriptStore()
        assert store.agent_ids() == []

    def test_agent_ids_populated(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.record("agent-2", _entry("Edit"))
        ids = store.agent_ids()
        assert set(ids) == {"agent-1", "agent-2"}


# ---------------------------------------------------------------------------
# T13: TranscriptEntry defaults
# ---------------------------------------------------------------------------


class TestTranscriptEntryDefaults:
    def test_default_tool_input(self) -> None:
        e = TranscriptEntry(tool_name="Read")
        assert e.tool_input == {}

    def test_default_tool_response(self) -> None:
        e = TranscriptEntry(tool_name="Read")
        assert e.tool_response == ""

    def test_default_tool_use_id(self) -> None:
        e = TranscriptEntry(tool_name="Read")
        assert e.tool_use_id == ""

    def test_timestamp_auto_set(self) -> None:
        e = TranscriptEntry(tool_name="Read")
        assert isinstance(e.timestamp, float)
        assert e.timestamp > 0


# ---------------------------------------------------------------------------
# T14: Truncate input -- string values bounded
# ---------------------------------------------------------------------------


class TestTruncateInput:
    def test_short_strings_unchanged(self) -> None:
        d = {"file_path": "/tmp/a.py", "command": "ls"}
        result = truncate_input(d)
        assert result == d

    def test_long_string_truncated(self) -> None:
        big = "x" * (MAX_OUTPUT_LENGTH + 500)
        result = truncate_input({"content": big, "file_path": "/tmp/a.py"})
        assert result["content"].endswith("... (truncated)")
        assert len(result["content"]) == MAX_OUTPUT_LENGTH + len("... (truncated)")
        assert result["file_path"] == "/tmp/a.py"

    def test_non_string_values_passthrough(self) -> None:
        d: dict[str, object] = {
            "count": 42,
            "items": [1, 2, 3],
            "nested": {"a": "b"},
            "flag": True,
        }
        result = truncate_input(d)
        assert result == d

    def test_empty_dict(self) -> None:
        assert truncate_input({}) == {}

    def test_returns_shallow_copy(self) -> None:
        original: dict[str, object] = {"file_path": "/tmp/a.py"}
        result = truncate_input(original)
        assert result is not original
        assert result == original


# ---------------------------------------------------------------------------
# T15: get_by_task returns chronological order
# ---------------------------------------------------------------------------


class TestGetByTaskChronological:
    def test_interleaved_timestamps_sorted(self) -> None:
        """Two agents mapped to same task with interleaved timestamps.

        agent-1 entries: t=10, t=30
        agent-2 entries: t=20, t=40
        Expected order: t=10, t=20, t=30, t=40
        """
        store = TranscriptStore()

        e1a = TranscriptEntry(tool_name="Read", timestamp=10.0)
        e1b = TranscriptEntry(tool_name="Edit", timestamp=30.0)
        e2a = TranscriptEntry(tool_name="Bash", timestamp=20.0)
        e2b = TranscriptEntry(tool_name="Write", timestamp=40.0)

        store.record("agent-1", e1a)
        store.record("agent-1", e1b)
        store.record("agent-2", e2a)
        store.record("agent-2", e2b)

        store.register_task_mapping("agent-1", "build_model")
        store.register_task_mapping("agent-2", "build_model")

        result = store.get_by_task("build_model")
        assert len(result) == 4
        assert [e.timestamp for e in result] == [10.0, 20.0, 30.0, 40.0]
        assert [e.tool_name for e in result] == ["Read", "Bash", "Edit", "Write"]

    def test_single_agent_already_sorted(self) -> None:
        """Single agent entries are already chronological -- no reordering."""
        store = TranscriptStore()
        e1 = TranscriptEntry(tool_name="Read", timestamp=1.0)
        e2 = TranscriptEntry(tool_name="Edit", timestamp=2.0)
        store.record("agent-1", e1)
        store.record("agent-1", e2)
        store.register_task_mapping("agent-1", "build_model")

        result = store.get_by_task("build_model")
        assert [e.timestamp for e in result] == [1.0, 2.0]


# ---------------------------------------------------------------------------
# T16: strip_ansi -- unit tests
# ---------------------------------------------------------------------------


class TestStripAnsi:
    def test_plain_text_unchanged(self) -> None:
        assert strip_ansi("hello world") == "hello world"

    def test_empty_string(self) -> None:
        assert strip_ansi("") == ""

    def test_csi_color_stripped(self) -> None:
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_bold_and_color_stripped(self) -> None:
        assert strip_ansi("\x1b[1m\x1b[32mgreen\x1b[0m") == "green"

    def test_osc_title_stripped(self) -> None:
        assert strip_ansi("\x1b]0;title\x07rest") == "rest"

    def test_lone_esc_stripped(self) -> None:
        assert strip_ansi("before\x1b") == "before"

    def test_consecutive_esc_stripped(self) -> None:
        """Belt-and-suspenders: even if regex misses one ESC, replace catches it."""
        assert strip_ansi("\x1b\x1b[31mtext\x1b[0m") == "text"

    def test_8bit_c1_stripped(self) -> None:
        """8-bit C1 control codes (0x80-0x9F) are removed."""
        assert strip_ansi("a\x90b\x9cc") == "abc"


# ---------------------------------------------------------------------------
# T17: truncate_output strips ANSI before measuring length
# ---------------------------------------------------------------------------


class TestTruncateOutputAnsi:
    def test_ansi_stripped_before_truncation(self) -> None:
        """ANSI codes don't count toward the length budget."""
        # Text is exactly MAX_OUTPUT_LENGTH visible chars, but with ANSI it's longer.
        visible = "x" * MAX_OUTPUT_LENGTH
        ansi_text = f"\x1b[31m{visible}\x1b[0m"
        result = truncate_output(ansi_text)
        # After stripping, visible text fits within limit -- no truncation.
        assert result == visible

    def test_ansi_stripped_then_truncated(self) -> None:
        """Long visible text with ANSI is stripped then truncated."""
        visible = "y" * (MAX_OUTPUT_LENGTH + 100)
        ansi_text = f"\x1b[1m{visible}\x1b[0m"
        result = truncate_output(ansi_text)
        assert result.endswith("... (truncated)")
        assert "\x1b" not in result


# ---------------------------------------------------------------------------
# T18: truncate_input strips ANSI from string values
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T19: get_latest_entry
# ---------------------------------------------------------------------------


class TestGetLatestEntry:
    def test_returns_most_recent_entry(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.record("agent-1", _entry("Edit"))
        store.record("agent-1", _entry("Bash"))
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        assert latest.tool_name == "Bash"

    def test_single_entry(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        assert latest.tool_name == "Read"

    def test_nonexistent_agent_returns_none(self) -> None:
        store = TranscriptStore()
        assert store.get_latest_entry("nonexistent") is None

    def test_after_clear_returns_none(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.clear()
        assert store.get_latest_entry("agent-1") is None

    def test_returns_same_entry_object(self) -> None:
        """Returns the actual entry, not a copy."""
        store = TranscriptStore()
        e = _entry("Read", tool_response="contents")
        store.record("agent-1", e)
        latest = store.get_latest_entry("agent-1")
        assert latest is e

    def test_separate_agents_independent(self) -> None:
        store = TranscriptStore()
        store.record("agent-1", _entry("Read"))
        store.record("agent-2", _entry("Edit"))
        assert store.get_latest_entry("agent-1") is not None
        assert store.get_latest_entry("agent-1").tool_name == "Read"
        assert store.get_latest_entry("agent-2") is not None
        assert store.get_latest_entry("agent-2").tool_name == "Edit"

    def test_after_eviction_returns_latest(self) -> None:
        """After exceeding MAX_ENTRIES_PER_AGENT, get_latest_entry returns the final entry."""
        store = TranscriptStore()
        limit = TranscriptStore.MAX_ENTRIES_PER_AGENT
        for i in range(limit + 10):
            store.record("agent-1", _entry("Bash", tool_response=str(i)))
        latest = store.get_latest_entry("agent-1")
        assert latest is not None
        assert latest.tool_response == str(limit + 9)


class TestTruncateInputAnsi:
    def test_ansi_stripped_from_short_string(self) -> None:
        d: dict[str, object] = {"cmd": "\x1b[32mls\x1b[0m"}
        result = truncate_input(d)
        assert result["cmd"] == "ls"

    def test_non_string_unaffected(self) -> None:
        d: dict[str, object] = {"timeout": 5000, "flag": True}
        result = truncate_input(d)
        assert result == d
