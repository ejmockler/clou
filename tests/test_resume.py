"""Tests for clou.resume — resumption context builder."""

from __future__ import annotations

from pathlib import Path

from clou.resume import (
    _truncate,
    build_resumption_context,
)
from clou.session import Session

# ---------------------------------------------------------------------------
# build_resumption_context
# ---------------------------------------------------------------------------


class TestBuildResumptionContext:
    def test_empty_session(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        assert build_resumption_context(tmp_path, "nonexistent") == ""

    def test_header_only_session(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        Session(tmp_path, session_id="empty")  # creates header-only session
        # No messages appended — only the system header.
        result = build_resumption_context(tmp_path, "empty")
        assert result == ""

    def test_contains_session_metadata(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1", model="sonnet")
        sess.append("user", "hello")
        sess.append("assistant", "hi")

        ctx = build_resumption_context(tmp_path, "s1")
        assert "s1" in ctx
        assert "sonnet" in ctx
        assert "Resuming session" in ctx

    def test_verbatim_tail_included(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1")
        sess.append("user", "my question")
        sess.append("assistant", "my answer")

        ctx = build_resumption_context(tmp_path, "s1")
        assert "my question" in ctx
        assert "my answer" in ctx
        assert "**User:**" in ctx
        assert "**Clou:**" in ctx

    def test_older_turns_summarized(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1")
        # Add more turns than the verbatim tail.
        for i in range(30):
            sess.append("user", f"question {i}")
            sess.append("assistant", f"answer {i}")

        ctx = build_resumption_context(tmp_path, "s1", verbatim_tail=10)
        # Should have both summary and verbatim sections.
        assert "Earlier conversation summary" in ctx
        assert "Recent conversation (verbatim)" in ctx
        # Oldest turn should appear in summary, not verbatim.
        assert "**User asked:** question 0" in ctx
        # Most recent turns should appear verbatim.
        assert "**User:** question 29" in ctx

    def test_tool_output_truncated_in_tail(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1")
        sess.append("tool", "x" * 500)

        ctx = build_resumption_context(tmp_path, "s1")
        assert "x" * 500 not in ctx
        assert "[truncated]" in ctx

    def test_instructions_to_continue(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1")
        sess.append("user", "hello")

        ctx = build_resumption_context(tmp_path, "s1")
        assert "Continue the conversation" in ctx
        assert "protocol file" in ctx

    def test_small_session_no_summary(self, tmp_path: Path) -> None:
        """Sessions smaller than verbatim_tail have no summary section."""
        (tmp_path / ".clou").mkdir()
        sess = Session(tmp_path, session_id="s1")
        sess.append("user", "hello")
        sess.append("assistant", "hi")

        ctx = build_resumption_context(tmp_path, "s1")
        assert "Earlier conversation summary" not in ctx
        assert "Recent conversation (verbatim)" in ctx


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated(self) -> None:
        result = _truncate("a" * 200, 50)
        assert len(result) == 53  # 50 + "..."
        assert result.endswith("...")

    def test_newlines_replaced(self) -> None:
        result = _truncate("line1\nline2\nline3", 100)
        assert "\n" not in result

    def test_whitespace_stripped(self) -> None:
        result = _truncate("  hello  ", 100)
        assert result == "hello"
