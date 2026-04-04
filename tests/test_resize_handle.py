"""Tests for the ResizeHandle widget and its integration with ClouApp."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clou.ui.app import ClouApp
from clou.ui.messages import Mode
from clou.ui.widgets.resize_handle import ResizeHandle


def _fake_event(screen_y: int) -> SimpleNamespace:
    """Synthesise enough of a mouse event for the handle's handlers."""
    stopped = {"value": False}
    return SimpleNamespace(
        screen_y=screen_y,
        stop=lambda: stopped.__setitem__("value", True),
        _stopped=stopped,
    )


class TestResizeHandle:
    @pytest.mark.asyncio
    async def test_handle_mounted_in_app(self) -> None:
        async with ClouApp().run_test() as pilot:
            handle = pilot.app.query_one("#resize-handle", ResizeHandle)
            assert handle is not None

    @pytest.mark.asyncio
    async def test_drag_updates_conversation_height(self) -> None:
        """Drag down → conversation height increases and override persists on app."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.mode = Mode.BREATH
            await pilot.pause()

            handle = app.query_one("#resize-handle", ResizeHandle)
            conv = app.query_one("#conversation")
            start_h = conv.size.height

            handle.on_mouse_down(_fake_event(screen_y=10))  # type: ignore[arg-type]
            handle.on_mouse_move(_fake_event(screen_y=15))  # type: ignore[arg-type]
            handle.on_mouse_up(_fake_event(screen_y=15))  # type: ignore[arg-type]
            await pilot.pause()

            assert app._conversation_override_height is not None
            assert app._conversation_override_height == start_h + 5
            assert not handle._dragging

    @pytest.mark.asyncio
    async def test_move_without_down_is_noop(self) -> None:
        """Mouse move without a prior mouse_down must not change state."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.mode = Mode.BREATH
            await pilot.pause()

            handle = app.query_one("#resize-handle", ResizeHandle)
            handle.on_mouse_move(_fake_event(screen_y=20))  # type: ignore[arg-type]
            assert app._conversation_override_height is None

    @pytest.mark.asyncio
    async def test_override_clamped_to_minimum(self) -> None:
        """Dragging way up clamps conversation height to the minimum."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.mode = Mode.BREATH
            await pilot.pause()

            handle = app.query_one("#resize-handle", ResizeHandle)
            handle.on_mouse_down(_fake_event(screen_y=20))  # type: ignore[arg-type]
            # Drag far up past minimum.
            handle.on_mouse_move(_fake_event(screen_y=-500))  # type: ignore[arg-type]
            handle.on_mouse_up(_fake_event(screen_y=-500))  # type: ignore[arg-type]
            await pilot.pause()

            # _MIN_CONV_HEIGHT is 4
            assert app._conversation_override_height == 4


class TestOverrideLifecycle:
    """Override should persist within BREATH/DECISION but clear on return to DIALOGUE."""

    @pytest.mark.asyncio
    async def test_override_cleared_on_return_to_dialogue(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.mode = Mode.BREATH
            await pilot.pause()

            # Set an override by dragging.
            handle = app.query_one("#resize-handle", ResizeHandle)
            handle.on_mouse_down(_fake_event(screen_y=10))  # type: ignore[arg-type]
            handle.on_mouse_move(_fake_event(screen_y=14))  # type: ignore[arg-type]
            handle.on_mouse_up(_fake_event(screen_y=14))  # type: ignore[arg-type]
            await pilot.pause()
            assert app._conversation_override_height is not None

            # Return to DIALOGUE — override is cleared.
            app.mode = Mode.DIALOGUE
            await pilot.pause()
            assert app._conversation_override_height is None

    @pytest.mark.asyncio
    async def test_override_persists_across_breath_decision(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.mode = Mode.BREATH
            await pilot.pause()

            handle = app.query_one("#resize-handle", ResizeHandle)
            handle.on_mouse_down(_fake_event(screen_y=10))  # type: ignore[arg-type]
            handle.on_mouse_move(_fake_event(screen_y=13))  # type: ignore[arg-type]
            handle.on_mouse_up(_fake_event(screen_y=13))  # type: ignore[arg-type]
            await pilot.pause()
            snapshot = app._conversation_override_height
            assert snapshot is not None

            # Flip BREATH → DECISION — override survives.
            app.mode = Mode.DECISION
            await pilot.pause()
            assert app._conversation_override_height == snapshot
