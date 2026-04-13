"""Tests for transcript capture hook and coordinator wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

from clou.hooks import HookConfig, build_hooks
from clou.transcript import MAX_OUTPUT_LENGTH, TranscriptStore, get_store, reset_store


def _run(coro: object) -> dict[str, object]:
    """Run an async coroutine and return the result."""
    result: object = asyncio.run(coro)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# Helper: extract the transcript hook from build_hooks
# ---------------------------------------------------------------------------


def _get_transcript_hook() -> object:
    """Return the transcript PostToolUse hook callback for coordinator tier."""
    hooks = build_hooks("coordinator", Path("/tmp/project"))
    # Transcript hook is the second PostToolUse entry (matcher=None).
    post_hooks = hooks["PostToolUse"]
    transcript_cfg = [c for c in post_hooks if c.matcher is None]
    assert len(transcript_cfg) == 1, "Expected exactly one matcher=None PostToolUse"
    return transcript_cfg[0].hooks[0]


# ---------------------------------------------------------------------------
# T1: Hook captures subagent tool calls
# ---------------------------------------------------------------------------


class TestTranscriptHookCaptures:
    def setup_method(self) -> None:
        reset_store()

    def test_captures_subagent_tool(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-abc",
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/tmp/a.py"},
                    "tool_response": "file contents here",
                },
                "tool-use-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-abc")
        assert len(entries) == 1
        assert entries[0].tool_name == "Read"
        assert entries[0].tool_input == {"file_path": "/tmp/a.py"}
        assert entries[0].tool_response == "file contents here"
        assert entries[0].tool_use_id == "tool-use-1"

    def test_captures_multiple_tools(self) -> None:
        hook = _get_transcript_hook()
        for name in ("Read", "Edit", "Bash"):
            _run(
                hook(
                    {
                        "agent_id": "agent-abc",
                        "tool_name": name,
                        "tool_input": {},
                        "tool_response": f"output-{name}",
                    },
                    f"tool-{name}",
                    {},
                )
            )
        entries = get_store().get_entries("agent-abc")
        assert len(entries) == 3
        assert [e.tool_name for e in entries] == ["Read", "Edit", "Bash"]

    def test_separates_agents(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": "",
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
                    "tool_response": "",
                },
                "t2",
                {},
            )
        )
        assert len(get_store().get_entries("agent-1")) == 1
        assert len(get_store().get_entries("agent-2")) == 1
        assert get_store().get_entries("agent-1")[0].tool_name == "Read"
        assert get_store().get_entries("agent-2")[0].tool_name == "Edit"


# ---------------------------------------------------------------------------
# T2: Hook skips main thread (no agent_id)
# ---------------------------------------------------------------------------


class TestTranscriptHookSkipsMainThread:
    def setup_method(self) -> None:
        reset_store()

    def test_skips_when_no_agent_id(self) -> None:
        hook = _get_transcript_hook()
        result = _run(
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
        # Should return PASS without recording anything.
        assert result.get("hookSpecificOutput") is not None
        assert get_store().agent_ids() == []

    def test_skips_when_agent_id_empty_string(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "",
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": "",
                },
                "tool-1",
                {},
            )
        )
        assert get_store().agent_ids() == []

    def test_skips_when_agent_id_not_string(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": 42,
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": "",
                },
                "tool-1",
                {},
            )
        )
        assert get_store().agent_ids() == []


# ---------------------------------------------------------------------------
# T3: Response normalization -- None
# ---------------------------------------------------------------------------


class TestNormalizeNoneResponse:
    def setup_method(self) -> None:
        reset_store()

    def test_none_response_becomes_empty_string(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {"command": "true"},
                    "tool_response": None,
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        assert entries[0].tool_response == ""


# ---------------------------------------------------------------------------
# T4: Response normalization -- list
# ---------------------------------------------------------------------------


class TestNormalizeListResponse:
    def setup_method(self) -> None:
        reset_store()

    def test_list_response_stringified(self) -> None:
        hook = _get_transcript_hook()
        response_list = [{"type": "text", "text": "hello"}]
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": response_list,
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        assert entries[0].tool_response == str(response_list)

    def test_dict_response_stringified(self) -> None:
        hook = _get_transcript_hook()
        response_dict = {"status": "ok", "data": [1, 2, 3]}
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "tool_response": response_dict,
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_response == str(response_dict)


# ---------------------------------------------------------------------------
# T5: Long response truncation
# ---------------------------------------------------------------------------


class TestTruncateLongResponse:
    def setup_method(self) -> None:
        reset_store()

    def test_long_response_truncated(self) -> None:
        hook = _get_transcript_hook()
        long_text = "x" * 5000
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "tool_response": long_text,
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        assert entries[0].tool_response.endswith("... (truncated)")
        assert len(entries[0].tool_response) < len(long_text)


# ---------------------------------------------------------------------------
# T6: build_hooks includes transcript for coordinator
# ---------------------------------------------------------------------------


class TestBuildHooksTranscript:
    def test_coordinator_has_transcript_hook(self) -> None:
        hooks = build_hooks("coordinator", Path("/tmp/project"))
        post_hooks = hooks["PostToolUse"]
        # Should have 2 PostToolUse entries: artifact validation + transcript.
        assert len(post_hooks) == 2
        # First is artifact validation (matcher = "Write|Edit|MultiEdit").
        assert post_hooks[0].matcher == "Write|Edit|MultiEdit"
        # Second is transcript capture (matcher = None for all tools).
        assert post_hooks[1].matcher is None


# ---------------------------------------------------------------------------
# T7: build_hooks excludes transcript for non-coordinator tiers
# ---------------------------------------------------------------------------


class TestBuildHooksNoTranscriptForOtherTiers:
    def test_worker_has_no_transcript_hook(self) -> None:
        hooks = build_hooks("worker", Path("/tmp/project"))
        post_hooks = hooks["PostToolUse"]
        assert len(post_hooks) == 1
        assert post_hooks[0].matcher == "Write|Edit|MultiEdit"

    def test_supervisor_has_no_transcript_hook(self) -> None:
        hooks = build_hooks("supervisor", Path("/tmp/project"))
        post_hooks = hooks["PostToolUse"]
        assert len(post_hooks) == 1

    def test_verifier_has_no_transcript_hook(self) -> None:
        hooks = build_hooks("verifier", Path("/tmp/project"))
        post_hooks = hooks["PostToolUse"]
        assert len(post_hooks) == 1


# ---------------------------------------------------------------------------
# T8: Coordinator registers agent_id -> task_name mapping
# ---------------------------------------------------------------------------


class TestAgentMappingRegistered:
    def setup_method(self) -> None:
        reset_store()

    def test_register_task_mapping_via_store(self) -> None:
        """Verify the TranscriptStore mapping API works as expected when
        called with the same pattern the coordinator uses."""
        store = get_store()
        # Simulate what the coordinator does: record entries, then register mapping.
        from clou.transcript import TranscriptEntry

        entry = TranscriptEntry(
            tool_name="Read",
            tool_input={"file_path": "/tmp/a.py"},
            tool_response="contents",
        )
        store.record("task-id-abc", entry)
        store.register_task_mapping("task-id-abc", "implement_model")

        # Lookup by task name should find the entry.
        by_task = store.get_by_task("implement_model")
        assert len(by_task) == 1
        assert by_task[0].tool_name == "Read"


# ---------------------------------------------------------------------------
# T9: Hook handles missing/malformed tool_input gracefully
# ---------------------------------------------------------------------------


class TestHookEdgeCases:
    def setup_method(self) -> None:
        reset_store()

    def test_missing_tool_input_defaults_to_empty_dict(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    # tool_input missing entirely
                    "tool_response": "output",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        assert entries[0].tool_input == {}

    def test_non_dict_tool_input_defaults_to_empty_dict(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Read",
                    "tool_input": "not-a-dict",
                    "tool_response": "",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_input == {}

    def test_none_tool_use_id_becomes_empty_string(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": "",
                },
                None,  # tool_use_id is None
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_use_id == ""

    def test_missing_tool_name_defaults_to_empty_string(self) -> None:
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    # tool_name missing
                    "tool_input": {},
                    "tool_response": "",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_name == ""


# ---------------------------------------------------------------------------
# T10: Large tool_input string values are truncated
# ---------------------------------------------------------------------------


class TestLargeToolInputTruncated:
    def setup_method(self) -> None:
        reset_store()

    def test_large_tool_input_string_truncated(self) -> None:
        """Hook with large tool_input string value produces a truncated entry."""
        hook = _get_transcript_hook()
        large_content = "a" * (MAX_OUTPUT_LENGTH + 3000)
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": "/tmp/big.py",
                        "content": large_content,
                    },
                    "tool_response": "ok",
                },
                "tool-write-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        stored_input = entries[0].tool_input
        # file_path is short -- should pass through unchanged.
        assert stored_input["file_path"] == "/tmp/big.py"
        # content was large -- should be truncated.
        assert len(stored_input["content"]) < len(large_content)
        assert stored_input["content"].endswith("... (truncated)")
        # Truncated length = MAX_OUTPUT_LENGTH + len("... (truncated)")
        assert len(stored_input["content"]) == MAX_OUTPUT_LENGTH + len("... (truncated)")


# ---------------------------------------------------------------------------
# T11: ANSI escape sequences stripped from tool_response
# ---------------------------------------------------------------------------


class TestAnsiStrippedFromResponse:
    def setup_method(self) -> None:
        reset_store()

    def test_ansi_color_codes_stripped_from_response(self) -> None:
        """ANSI color codes in tool_response are removed before storage."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls --color"},
                    "tool_response": "\x1b[31mERROR\x1b[0m some output",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        assert entries[0].tool_response == "ERROR some output"
        assert "\x1b" not in entries[0].tool_response

    def test_multiple_ansi_sequences_stripped(self) -> None:
        """Multiple different ANSI sequences are all removed."""
        hook = _get_transcript_hook()
        # Bold + color + reset
        ansi_text = "\x1b[1m\x1b[32mSUCCESS\x1b[0m: all tests passed"
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "tool_response": ansi_text,
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_response == "SUCCESS: all tests passed"

    def test_osc_sequence_stripped(self) -> None:
        """OSC (Operating System Command) sequences are stripped."""
        hook = _get_transcript_hook()
        # OSC title-set sequence: ESC ] 0 ; title BEL
        osc_text = "\x1b]0;my-terminal\x07actual output"
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "tool_response": osc_text,
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_response == "actual output"

    def test_clean_response_unchanged(self) -> None:
        """Response without ANSI sequences passes through unchanged."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Read",
                    "tool_input": {},
                    "tool_response": "clean text with no escapes",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_response == "clean text with no escapes"


# ---------------------------------------------------------------------------
# T12: ANSI escape sequences stripped from tool_input string values
# ---------------------------------------------------------------------------


class TestAnsiStrippedFromInput:
    def setup_method(self) -> None:
        reset_store()

    def test_ansi_stripped_from_input_string_values(self) -> None:
        """ANSI escape sequences in tool_input string values are stripped."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": "/tmp/out.txt",
                        "content": "\x1b[34mblue text\x1b[0m and normal",
                    },
                    "tool_response": "ok",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert len(entries) == 1
        assert entries[0].tool_input["file_path"] == "/tmp/out.txt"
        assert entries[0].tool_input["content"] == "blue text and normal"
        assert "\x1b" not in str(entries[0].tool_input["content"])

    def test_non_string_input_values_unaffected(self) -> None:
        """Non-string values in tool_input are not altered by ANSI stripping."""
        hook = _get_transcript_hook()
        _run(
            hook(
                {
                    "agent_id": "agent-1",
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": "\x1b[1mls\x1b[0m",
                        "timeout": 5000,
                    },
                    "tool_response": "output",
                },
                "tool-1",
                {},
            )
        )
        entries = get_store().get_entries("agent-1")
        assert entries[0].tool_input["command"] == "ls"
        assert entries[0].tool_input["timeout"] == 5000
