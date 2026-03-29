"""Tests for the CommandPalette widget."""

from __future__ import annotations

import pytest

from clou.ui.app import ClouApp
from clou.ui.widgets.command_palette import CommandPalette


class TestCommandPalette:
    @pytest.mark.asyncio
    async def test_palette_hidden_by_default(self) -> None:
        async with ClouApp().run_test() as pilot:
            palette = pilot.app.query_one(CommandPalette)
            assert "visible" not in palette.classes

    @pytest.mark.asyncio
    async def test_palette_appears_on_slash(self) -> None:
        async with ClouApp().run_test() as pilot:
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "/"
            await pilot.pause()
            palette = pilot.app.query_one(CommandPalette)
            assert "visible" in palette.classes

    @pytest.mark.asyncio
    async def test_palette_filters_on_partial(self) -> None:
        async with ClouApp().run_test() as pilot:
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "/he"
            await pilot.pause()
            palette = pilot.app.query_one(CommandPalette)
            assert "visible" in palette.classes
            rendered = str(palette.render())
            assert "help" in rendered

    @pytest.mark.asyncio
    async def test_palette_hides_on_no_match(self) -> None:
        async with ClouApp().run_test() as pilot:
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "/xyzzy_nomatch"
            await pilot.pause()
            palette = pilot.app.query_one(CommandPalette)
            assert "visible" not in palette.classes

    @pytest.mark.asyncio
    async def test_palette_hides_on_non_slash(self) -> None:
        async with ClouApp().run_test() as pilot:
            inp = pilot.app.query_one("#user-input ChatInput")
            # First show it.
            inp.value = "/"
            await pilot.pause()
            palette = pilot.app.query_one(CommandPalette)
            assert "visible" in palette.classes
            # Then clear.
            inp.value = "hello"
            await pilot.pause()
            assert "visible" not in palette.classes

    @pytest.mark.asyncio
    async def test_palette_hides_on_submit(self) -> None:
        async with ClouApp().run_test() as pilot:
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "/help"
            await pilot.pause()
            palette = pilot.app.query_one(CommandPalette)
            assert "visible" in palette.classes
            # Submit clears input → triggers on_input_changed with "".
            await inp.action_submit()
            await pilot.pause()
            assert "visible" not in palette.classes
