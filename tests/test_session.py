"""Tests for clou.session — JSONL session persistence."""

from __future__ import annotations

import json
from pathlib import Path

from clou.session import (
    Session,
    SessionEntry,
    SessionInfo,
    latest_session_id,
    list_sessions,
    read_transcript,
    session_path,
    session_summary,
    sessions_dir,
)

# ---------------------------------------------------------------------------
# SessionEntry
# ---------------------------------------------------------------------------


class TestSessionEntry:
    def test_round_trip(self) -> None:
        entry = SessionEntry(role="user", content="hello", timestamp=1000.0)
        json_str = entry.to_json()
        restored = SessionEntry.from_json(json_str)
        assert restored.role == "user"
        assert restored.content == "hello"
        assert restored.timestamp == 1000.0

    def test_meta_preserved(self) -> None:
        entry = SessionEntry(role="tool", content="output", meta={"tool_name": "Read"})
        restored = SessionEntry.from_json(entry.to_json())
        assert restored.meta["tool_name"] == "Read"

    def test_from_json_missing_meta(self) -> None:
        raw = json.dumps({"role": "user", "content": "hi", "timestamp": 1.0})
        entry = SessionEntry.from_json(raw)
        assert entry.meta == {}

    def test_unicode_content(self) -> None:
        entry = SessionEntry(role="user", content="こんにちは 🎉")
        restored = SessionEntry.from_json(entry.to_json())
        assert restored.content == "こんにちは 🎉"


# ---------------------------------------------------------------------------
# SessionInfo
# ---------------------------------------------------------------------------


class TestSessionInfo:
    def test_to_entry_and_back(self) -> None:
        info = SessionInfo(
            session_id="abc123",
            project_dir="/tmp/proj",
            started_at=1000.0,
            model="sonnet",
        )
        entry = info.to_entry()
        assert entry.role == "system"
        assert entry.content == "session_start"

        restored = SessionInfo.from_entry(entry)
        assert restored.session_id == "abc123"
        assert restored.project_dir == "/tmp/proj"
        assert restored.model == "sonnet"
        assert restored.started_at == 1000.0


# ---------------------------------------------------------------------------
# Session (live writer)
# ---------------------------------------------------------------------------


class TestSession:
    def test_creates_jsonl_file(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, model="opus")
        assert sess.path.exists()
        assert sess.path.suffix == ".jsonl"

    def test_header_is_first_line(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="test123")
        lines = sess.path.read_text().strip().split("\n")
        first = json.loads(lines[0])
        assert first["role"] == "system"
        assert first["content"] == "session_start"
        assert first["meta"]["session_id"] == "test123"

    def test_append_writes_lines(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path)
        sess.append("user", "hello")
        sess.append("assistant", "hi there")
        lines = sess.path.read_text().strip().split("\n")
        assert len(lines) == 3  # header + 2 messages

    def test_message_count(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path)
        assert sess.message_count == 0
        sess.append("user", "q1")
        sess.append("assistant", "a1")
        assert sess.message_count == 2

    def test_append_with_meta(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path)
        sess.append("tool", "result", tool_name="Read", file_path="/tmp/x")
        entries = read_transcript(tmp_path, sess.session_id)
        tool_entry = next(e for e in entries if e.role == "tool")
        assert tool_entry.meta["tool_name"] == "Read"

    def test_session_id_auto_generated(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path)
        assert len(sess.session_id) == 12

    def test_explicit_session_id(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="custom-id")
        assert sess.session_id == "custom-id"


# ---------------------------------------------------------------------------
# Read transcript
# ---------------------------------------------------------------------------


class TestReadTranscript:
    def test_read_back_entries(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1")
        sess.append("user", "question")
        sess.append("assistant", "answer")

        entries = read_transcript(tmp_path, "s1")
        assert len(entries) == 3  # header + 2
        assert entries[1].role == "user"
        assert entries[1].content == "question"
        assert entries[2].role == "assistant"
        assert entries[2].content == "answer"

    def test_nonexistent_session(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        entries = read_transcript(tmp_path, "nonexistent")
        assert entries == []


# ---------------------------------------------------------------------------
# List sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_lists_sessions_newest_first(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        s1 = Session(tmp_path, session_id="older")
        s1.append("user", "first session")

        # Ensure second session has a later timestamp.
        s2 = Session(tmp_path, session_id="newer")
        s2.append("user", "second session")

        sessions = list_sessions(tmp_path)
        assert len(sessions) >= 2
        # Most recent first.
        ids = [s.session_id for s in sessions]
        assert ids.index("newer") < ids.index("older")

    def test_empty_project(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        assert list_sessions(tmp_path) == []

    def test_corrupt_file_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        d = sessions_dir(tmp_path)
        (d / "bad.jsonl").write_text("not json\n")
        Session(tmp_path, session_id="good")
        sessions = list_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0].session_id == "good"


# ---------------------------------------------------------------------------
# Latest session ID
# ---------------------------------------------------------------------------


class TestLatestSessionId:
    def test_returns_most_recent(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        Session(tmp_path, session_id="old")
        Session(tmp_path, session_id="new")
        assert latest_session_id(tmp_path) == "new"

    def test_none_when_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        assert latest_session_id(tmp_path) is None


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------


class TestSessionSummary:
    def test_summary_fields(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1", model="sonnet")
        sess.append("user", "q1")
        sess.append("assistant", "a1")
        sess.append("user", "q2")

        summary = session_summary(tmp_path, "s1")
        assert summary["session_id"] == "s1"
        assert summary["model"] == "sonnet"
        assert summary["message_count"] == 3
        assert summary["user_messages"] == 2
        assert summary["assistant_messages"] == 1

    def test_empty_session(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        summary = session_summary(tmp_path, "nonexistent")
        assert summary["message_count"] == 0


# ---------------------------------------------------------------------------
# sessions_dir / session_path
# ---------------------------------------------------------------------------


class TestPaths:
    def test_sessions_dir_created(self, tmp_path: Path) -> None:
        d = sessions_dir(tmp_path)
        assert d.exists()
        assert d.name == "sessions"

    def test_session_path(self, tmp_path: Path) -> None:
        p = session_path(tmp_path, "abc123")
        assert p.name == "abc123.jsonl"
        assert p.parent.name == "sessions"
