"""Tests for /export and conversation history."""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.ui.app import ClouApp
from clou.ui.history import ConversationEntry, export_markdown
from clou.ui.widgets.conversation import ConversationWidget


class TestExportMarkdown:
    def test_empty_history(self) -> None:
        md = export_markdown([])
        assert "# Conversation Export" in md

    def test_user_and_assistant(self) -> None:
        entries = [
            ConversationEntry(role="user", content="hello"),
            ConversationEntry(role="assistant", content="hi there"),
        ]
        md = export_markdown(entries)
        assert "## You" in md
        assert "hello" in md
        assert "## Clou" in md
        assert "hi there" in md

    def test_tool_excluded_by_default(self) -> None:
        entries = [
            ConversationEntry(role="tool", content="tool output"),
        ]
        md = export_markdown(entries)
        assert "tool output" not in md

    def test_tool_included_with_flag(self) -> None:
        entries = [
            ConversationEntry(role="tool", content="tool output"),
        ]
        md = export_markdown(entries, include_tools=True)
        assert "## Tool" in md
        assert "tool output" in md

    def test_timestamps_present(self) -> None:
        entries = [
            ConversationEntry(role="user", content="test"),
        ]
        md = export_markdown(entries)
        # ISO timestamp format.
        assert "T" in md  # e.g. 2026-03-23T...


class TestExportCommand:
    @pytest.mark.asyncio
    async def test_export_registered(self) -> None:
        from clou.ui.commands import get

        assert get("export") is not None

    @pytest.mark.asyncio
    async def test_export_creates_file(self, tmp_path: Path) -> None:
        from clou.ui.commands import dispatch

        output = tmp_path / "test-export.md"
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app._conversation_history.append(
                ConversationEntry(role="user", content="test message")
            )
            await dispatch(app, f"/export {output}")
            assert output.exists()
            content = output.read_text()
            assert "test message" in content

    @pytest.mark.asyncio
    async def test_export_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from clou.ui.commands import dispatch

        monkeypatch.chdir(tmp_path)
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app._conversation_history.append(
                ConversationEntry(role="user", content="hello")
            )
            await dispatch(app, "/export")
            exports = list((tmp_path / ".clou" / "exports").glob("*.md"))
            assert len(exports) == 1
            assert "hello" in exports[0].read_text()

    @pytest.mark.asyncio
    async def test_export_empty_conversation(self, tmp_path: Path) -> None:
        from clou.ui.commands import dispatch

        output = tmp_path / "empty.md"
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, f"/export {output}")
            assert output.exists()
            assert "# Conversation Export" in output.read_text()

    @pytest.mark.asyncio
    async def test_export_full_includes_tools(self, tmp_path: Path) -> None:
        from clou.ui.commands import dispatch

        output = tmp_path / "full.md"
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app._conversation_history.append(
                ConversationEntry(role="tool", content="tool result")
            )
            await dispatch(app, f"/export --full {output}")
            content = output.read_text()
            assert "tool result" in content

    @pytest.mark.asyncio
    async def test_export_confirmation(self, tmp_path: Path) -> None:
        from clou.ui.commands import dispatch

        output = tmp_path / "confirm.md"
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, f"/export {output}")
            conv = pilot.app.query_one(ConversationWidget)
            text = "".join(str(w.render()) for w in conv.query(".msg"))
            assert "exported to" in text
