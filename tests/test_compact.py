"""Tests for the /compact command."""

from __future__ import annotations

import asyncio

import pytest

from clou.ui.app import ClouApp
from clou.ui.commands import dispatch, get
from clou.ui.mode import Mode
from clou.ui.widgets.conversation import ConversationWidget


def _history_text(conv: ConversationWidget) -> str:
    return "".join(str(w.render()) for w in conv.query(".msg"))


class TestCompact:
    def test_compact_registered(self) -> None:
        cmd = get("compact")
        assert cmd is not None
        assert cmd.modes == frozenset({Mode.DIALOGUE})

    @pytest.mark.asyncio
    async def test_compact_rejected_in_breath(self) -> None:
        async with ClouApp().run_test() as pilot:
            from clou.ui.messages import ClouCoordinatorSpawned

            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="m1"))
            await pilot.pause()
            assert app.mode == Mode.BREATH
            result = await dispatch(app, "/compact")
            assert result is True
            conv = app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "dialogue" in text.lower()

    @pytest.mark.asyncio
    async def test_compact_signals_orchestrator(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Simulate the orchestrator completing compaction promptly.
            async def _fake_orchestrator() -> None:
                await app._compact_requested.wait()
                app._compaction_count += 1
                app._compact_complete.set()

            task = asyncio.create_task(_fake_orchestrator())
            await dispatch(app, "/compact")
            await pilot.pause()
            task.cancel()

            conv = app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "compact" in text.lower()

    @pytest.mark.asyncio
    async def test_compact_with_args_sets_instructions(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            async def _fake_orchestrator() -> None:
                await app._compact_requested.wait()
                app._compaction_count += 1
                app._compact_complete.set()

            task = asyncio.create_task(_fake_orchestrator())
            await dispatch(app, "/compact keep the auth discussion")
            task.cancel()

            assert app._compact_instructions == "keep the auth discussion"

    @pytest.mark.asyncio
    async def test_compaction_count_increments(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app._compaction_count == 0

            async def _fake_orchestrator() -> None:
                await app._compact_requested.wait()
                app._compaction_count += 1
                app._compact_complete.set()

            task = asyncio.create_task(_fake_orchestrator())
            await dispatch(app, "/compact")
            task.cancel()

            assert app._compaction_count == 1

    @pytest.mark.asyncio
    async def test_compact_persists_history(self, tmp_path, monkeypatch) -> None:
        from pathlib import Path

        from clou.ui.history import ConversationEntry

        monkeypatch.chdir(tmp_path)
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app._conversation_history.append(
                ConversationEntry(role="user", content="hello")
            )

            async def _fake_orchestrator() -> None:
                await app._compact_requested.wait()
                app._compaction_count += 1
                app._compact_complete.set()

            task = asyncio.create_task(_fake_orchestrator())
            await dispatch(app, "/compact")
            task.cancel()

            history_file = tmp_path / ".clou" / "active" / "supervisor-history.jsonl"
            assert history_file.exists()
            content = history_file.read_text()
            assert "hello" in content
