"""Tests for the slash command system — dispatch, registry, output."""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.ui.app import ClouApp
from clou.ui.commands import Command, all_commands, dispatch, get, register
from clou.ui.mode import Mode
from clou.ui.widgets.conversation import ConversationWidget


def _history_text(conv: ConversationWidget) -> str:
    """Extract all visible text from the conversation history."""
    parts: list[str] = []
    for widget in conv.query(".msg"):
        rendered = widget.render()
        parts.append(str(rendered))
    return "".join(parts)


def _msg_count(conv: ConversationWidget) -> int:
    """Count message widgets in conversation history."""
    return len(conv.query(".msg"))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_get(self) -> None:
        cmd = Command(name="__test_reg", description="test", handler=_noop)
        register(cmd)
        assert get("__test_reg") is cmd

    def test_get_unknown_returns_none(self) -> None:
        assert get("__definitely_not_registered") is None

    def test_all_commands_sorted(self) -> None:
        cmds = all_commands()
        names = [c.name for c in cmds]
        assert names == sorted(names)

    def test_builtin_help_registered(self) -> None:
        assert get("help") is not None

    def test_builtin_clear_registered(self) -> None:
        assert get("clear") is not None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    @pytest.mark.asyncio
    async def test_non_slash_returns_false(self) -> None:
        async with ClouApp().run_test() as pilot:
            result = await dispatch(pilot.app, "hello world")
            assert result is False

    @pytest.mark.asyncio
    async def test_known_command_returns_true(self) -> None:
        async with ClouApp().run_test() as pilot:
            result = await dispatch(pilot.app, "/help")
            assert result is True

    @pytest.mark.asyncio
    async def test_unknown_command_returns_true_with_error(self) -> None:
        async with ClouApp().run_test() as pilot:
            result = await dispatch(pilot.app, "/xyzzy")
            assert result is True
            conv = pilot.app.query_one(ConversationWidget)
            assert "unknown command: /xyzzy" in _history_text(conv)

    @pytest.mark.asyncio
    async def test_bare_slash_shows_help(self) -> None:
        async with ClouApp().run_test() as pilot:
            result = await dispatch(pilot.app, "/")
            assert result is True
            conv = pilot.app.query_one(ConversationWidget)
            assert "/help" in _history_text(conv)

    @pytest.mark.asyncio
    async def test_mode_restriction_rejects(self) -> None:
        """Command restricted to DIALOGUE should be rejected in BREATH."""
        _called = False

        async def _handler(app: ClouApp, args: str) -> None:
            nonlocal _called
            _called = True

        register(
            Command(
                name="__test_dialogue_only",
                description="test",
                handler=_handler,
                modes=frozenset({Mode.DIALOGUE}),
            )
        )

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Force BREATH mode.
            from clou.ui.messages import ClouCoordinatorSpawned

            app.post_message(ClouCoordinatorSpawned(milestone="m1"))
            await pilot.pause()
            assert app.mode == Mode.BREATH

            result = await dispatch(app, "/__test_dialogue_only")
            assert result is True
            assert _called is False  # Handler should NOT have been called.


# ---------------------------------------------------------------------------
# Input interception
# ---------------------------------------------------------------------------


class TestInputInterception:
    @pytest.mark.asyncio
    async def test_slash_does_not_enqueue_to_supervisor(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            inp = app.query_one("#user-input ChatInput")
            inp.value = "/help"
            await inp.action_submit()
            await pilot.pause()
            # Queue should be empty — command was intercepted.
            assert len(app._user_input_queue) == 0

    @pytest.mark.asyncio
    async def test_slash_does_not_add_user_message(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            conv = app.query_one(ConversationWidget)
            inp = app.query_one("#user-input ChatInput")
            inp.value = "/help"
            await inp.action_submit()
            await pilot.pause()

            # Some output added (help text), but NO gold user message block.
            assert "› /help" not in _history_text(conv)

    @pytest.mark.asyncio
    async def test_non_slash_still_enqueues(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            inp = app.query_one("#user-input ChatInput")
            inp.value = "hello"
            await inp.action_submit()
            await pilot.pause()
            assert len(app._user_input_queue) > 0


# ---------------------------------------------------------------------------
# Command output rendering
# ---------------------------------------------------------------------------


class TestCommandOutput:
    @pytest.mark.asyncio
    async def test_add_command_output_appears(self) -> None:
        from rich.text import Text

        async with ClouApp().run_test() as pilot:
            conv = pilot.app.query_one(ConversationWidget)
            initial = _msg_count(conv)
            conv.add_command_output(Text("test output"))
            assert _msg_count(conv) > initial

    @pytest.mark.asyncio
    async def test_add_command_error_appears(self) -> None:
        async with ClouApp().run_test() as pilot:
            conv = pilot.app.query_one(ConversationWidget)
            initial = _msg_count(conv)
            conv.add_command_error("bad command")
            assert _msg_count(conv) > initial
            assert "bad command" in _history_text(conv)


# ---------------------------------------------------------------------------
# C1: /help
# ---------------------------------------------------------------------------


class TestHelp:
    @pytest.mark.asyncio
    async def test_help_renders_all_commands(self) -> None:
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/help")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            for cmd in all_commands():
                assert f"/{cmd.name}" in text

    @pytest.mark.asyncio
    async def test_help_contains_descriptions(self) -> None:
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/help")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            for cmd in all_commands():
                assert cmd.description in text

    @pytest.mark.asyncio
    async def test_help_works_in_all_modes(self) -> None:
        async with ClouApp().run_test() as pilot:
            result = await dispatch(pilot.app, "/help")
            assert result is True


# ---------------------------------------------------------------------------
# C2: /clear
# ---------------------------------------------------------------------------


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_empties_history(self) -> None:
        async with ClouApp().run_test() as pilot:
            conv = pilot.app.query_one(ConversationWidget)
            conv.add_command_output("test content")
            assert _msg_count(conv) > 0
            await dispatch(pilot.app, "/clear")
            await pilot.pause()
            assert _msg_count(conv) == 0

    @pytest.mark.asyncio
    async def test_clear_stops_streaming(self) -> None:
        async with ClouApp().run_test() as pilot:
            conv = pilot.app.query_one(ConversationWidget)
            conv._tc.stream_buffer = "some streaming content"
            conv._tc.stream_dirty = True
            await dispatch(pilot.app, "/clear")
            assert conv._tc.stream_buffer == ""
            assert conv._tc.stream_dirty is False


# ---------------------------------------------------------------------------
# C3: /cost
# ---------------------------------------------------------------------------


class TestCost:
    @pytest.mark.asyncio
    async def test_cost_renders_token_counts(self) -> None:
        from clou.ui.widgets.status_bar import ClouStatusBar

        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)
            bar.input_tokens = 1234
            bar.output_tokens = 567
            bar.cost_usd = 0.42
            await dispatch(pilot.app, "/cost")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "1,234" in text
            assert "567" in text
            assert "$0.42" in text

    @pytest.mark.asyncio
    async def test_cost_shows_session_duration(self) -> None:
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/cost")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "session" in text.lower() or "m" in text

    @pytest.mark.asyncio
    async def test_cost_registered(self) -> None:
        assert get("cost") is not None


# ---------------------------------------------------------------------------
# C4: /dag
# ---------------------------------------------------------------------------


class TestDag:
    @pytest.mark.asyncio
    async def test_dag_registered(self) -> None:
        cmd = get("dag")
        assert cmd is not None
        assert Mode.BREATH in cmd.modes
        assert Mode.HANDOFF in cmd.modes

    @pytest.mark.asyncio
    async def test_dag_rejected_in_dialogue(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.mode == Mode.DIALOGUE
            result = await dispatch(app, "/dag")
            assert result is True
            conv = app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "breath" in text.lower() or "handoff" in text.lower()

    @pytest.mark.asyncio
    async def test_dag_allowed_in_breath(self) -> None:
        async with ClouApp().run_test() as pilot:
            from clou.ui.messages import ClouCoordinatorSpawned

            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="m1"))
            await pilot.pause()
            assert app.mode == Mode.BREATH
            result = await dispatch(app, "/dag")
            assert result is True


# ---------------------------------------------------------------------------
# C5: /context
# ---------------------------------------------------------------------------


class TestContext:
    @pytest.mark.asyncio
    async def test_context_registered(self) -> None:
        cmd = get("context")
        assert cmd is not None

    @pytest.mark.asyncio
    async def test_context_dispatches(self) -> None:
        async with ClouApp().run_test() as pilot:
            result = await dispatch(pilot.app, "/context")
            assert result is True


# ---------------------------------------------------------------------------
# C8: /status
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_registered(self) -> None:
        assert get("status") is not None

    @pytest.mark.asyncio
    async def test_status_shows_no_milestone(self) -> None:
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/status")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "no active milestone" in text

    @pytest.mark.asyncio
    async def test_status_shows_milestone_when_active(self) -> None:
        from clou.ui.widgets.status_bar import ClouStatusBar

        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)
            bar.milestone = "auth-flow"
            bar.cycle_type = "PLAN"
            bar.cycle_num = 2
            bar.phase = "design"
            await dispatch(pilot.app, "/status")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "auth-flow" in text
            assert "PLAN" in text
            assert "design" in text

    @pytest.mark.asyncio
    async def test_status_shows_mode(self) -> None:
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/status")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "dialogue" in text

    @pytest.mark.asyncio
    async def test_status_shows_token_counts(self) -> None:
        from clou.ui.widgets.status_bar import ClouStatusBar

        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)
            bar.input_tokens = 5000
            bar.output_tokens = 2000
            await dispatch(pilot.app, "/status")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "5,000" in text
            assert "2,000" in text


# ---------------------------------------------------------------------------
# C10: /model
# ---------------------------------------------------------------------------


class TestModel:
    @pytest.mark.asyncio
    async def test_model_registered(self) -> None:
        cmd = get("model")
        assert cmd is not None
        assert cmd.modes == frozenset({Mode.DIALOGUE})

    @pytest.mark.asyncio
    async def test_model_shows_current(self) -> None:
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/model")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "opus" in text

    @pytest.mark.asyncio
    async def test_model_switch_deferred(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            await dispatch(app, "/model sonnet")
            # Model should NOT change — switching is deferred.
            assert app._model == "opus"
            conv = app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "not yet implemented" in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /sessions
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_registered(self) -> None:
        cmd = get("resume")
        assert cmd is not None
        assert cmd.items_factory is not None

    @pytest.mark.asyncio
    async def test_sessions_removed(self) -> None:
        assert get("sessions") is None

    @pytest.mark.asyncio
    async def test_resume_no_args_lists_sessions(self, tmp_path: Path) -> None:
        """Bare /resume lists available sessions."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Create a past session with content.
            from clou.session import Session
            old = Session(tmp_path, session_id="old123")
            old.append("user", "hello from the past")
            await dispatch(app, "/resume")
            conv = app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "old123" in text

    @pytest.mark.asyncio
    async def test_resume_no_previous_sessions(self, tmp_path: Path) -> None:
        """Bare /resume with no past sessions shows error."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            await dispatch(pilot.app, "/resume")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "no previous sessions" in text

    @pytest.mark.asyncio
    async def test_resume_nonexistent_error(self, tmp_path: Path) -> None:
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            await dispatch(pilot.app, "/resume nonexistent")
            conv = pilot.app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "not found" in text

    @pytest.mark.asyncio
    async def test_resume_same_session_error(self, tmp_path: Path) -> None:
        """Cannot resume the current session."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            sid = app._session.session_id
            await dispatch(app, f"/resume {sid}")
            conv = app.query_one(ConversationWidget)
            text = _history_text(conv)
            assert "already in this session" in text

    @pytest.mark.asyncio
    async def test_resume_items_factory(self, tmp_path: Path) -> None:
        """items_factory returns SubItems for available sessions."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Create a past session.
            from clou.session import Session
            old = Session(tmp_path, session_id="factory1")
            old.append("user", "test message")
            cmd = get("resume")
            items = cmd.items_factory(app)
            assert len(items) >= 1
            assert any(sub.args == "factory1" for sub in items)

    @pytest.mark.asyncio
    async def test_resume_items_factory_excludes_current(self, tmp_path: Path) -> None:
        """items_factory excludes the current session."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            cmd = get("resume")
            items = cmd.items_factory(app)
            current_id = app._session.session_id
            assert not any(sub.args == current_id for sub in items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop(app: ClouApp, args: str) -> None:
    """No-op command handler for testing."""
