"""Tests for clou.ui.app — ClouApp dialogue mode shell."""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.ui.app import ClouApp
from clou.ui.messages import (
    ClouCoordinatorSpawned,
    ClouEscalationArrived,
    ClouEscalationResolved,
    ClouHandoff,
    ClouMetrics,
    ClouProcessingStarted,
    ClouStatusUpdate,
    ClouTurnComplete,
    Mode,
)
from clou.ui.widgets.conversation import ConversationWidget
from clou.ui.widgets.handoff import HandoffWidget
from clou.ui.widgets.status_bar import ClouStatusBar

# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------


class TestMounting:
    @pytest.mark.asyncio
    async def test_app_mounts_successfully(self) -> None:
        async with ClouApp().run_test() as pilot:
            assert pilot.app is not None

    @pytest.mark.asyncio
    async def test_default_mode_is_dialogue(self) -> None:
        async with ClouApp().run_test() as pilot:
            assert pilot.app.mode == Mode.DIALOGUE

    @pytest.mark.asyncio
    async def test_dialogue_css_class_on_mount(self) -> None:
        async with ClouApp().run_test() as pilot:
            assert "dialogue" in pilot.app.classes


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


class TestInputSubmission:
    @pytest.mark.asyncio
    async def test_input_clears_on_submit(self) -> None:
        async with ClouApp().run_test() as pilot:
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "hello"
            await inp.action_submit()
            await pilot.pause()
            assert inp.value == ""

    @pytest.mark.asyncio
    async def test_input_queues_message(self) -> None:
        """All messages start as queued.
        ClouProcessingStarted transitions queued messages to active."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            inp = pilot.app.query_one("#user-input ChatInput")
            conv = pilot.app.query_one(ConversationWidget)

            # First message: starts queued.
            inp.value = "first message"
            await inp.action_submit()
            await pilot.pause()
            msgs = conv.query(".msg")
            assert len(msgs) >= 1
            first_rendered = str(msgs.last().render())
            assert "first message" in first_rendered
            assert "queued" in first_rendered
            assert app._queue_count == 1

            # Second message: also queued.
            inp.value = "second message"
            await inp.action_submit()
            await pilot.pause()
            msgs = conv.query(".msg")
            assert len(msgs) >= 2
            second_rendered = str(msgs.last().render())
            assert "second message" in second_rendered
            assert "queued" in second_rendered
            assert app._queue_count == 2

            # Simulate the orchestrator picking up the first message.
            conv.post_message(ClouProcessingStarted(text="first message"))
            await pilot.pause()
            assert app._queue_count == 1
            # First message should now be active (no "queued" badge).
            first_rendered = str(msgs.first().render())
            assert "queued" not in first_rendered

    @pytest.mark.asyncio
    async def test_empty_input_ignored(self) -> None:
        async with ClouApp().run_test() as pilot:
            conv = pilot.app.query_one(ConversationWidget)
            initial_count = len(conv.query(".msg"))
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "   "
            await inp.action_submit()
            await pilot.pause()
            # No new content should have been added.
            assert len(conv.query(".msg")) == initial_count
            # Verify input field was cleared even though message was ignored.
            assert inp.value == ""

    @pytest.mark.asyncio
    async def test_prompt_input_mounts_with_prompt_char(self) -> None:
        """PromptInput contains a gold › prompt character."""
        async with ClouApp().run_test() as pilot:
            prompt_char = pilot.app.query_one("#user-input .prompt-char")
            assert "›" in str(prompt_char.render())

    @pytest.mark.asyncio
    async def test_input_during_decision_stays_in_decision(self) -> None:
        """Submitting input during DECISION queues it but keeps mode DECISION."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Trigger DECISION mode via escalation.
            app.post_message(
                ClouEscalationArrived(
                    path=Path("/tmp/test.md"),
                    classification="info",
                    issue="test",
                    options=[{"label": "Ok", "description": "ok"}],
                )
            )
            await pilot.pause()
            assert app.mode == Mode.DECISION

            # Submit input while in DECISION.
            inp = pilot.app.query_one("#user-input ChatInput")
            inp.value = "message during decision"
            await inp.action_submit()
            await pilot.pause()

            # Mode should stay DECISION — input doesn't dismiss escalation.
            assert app.mode == Mode.DECISION

            # Message is queued, not yet displayed.
            assert app._queue_count == 1

            # Simulate orchestrator picking it up — message appears.
            conv = pilot.app.query_one(ConversationWidget)
            conv.post_message(ClouProcessingStarted(text="message during decision"))
            await pilot.pause()
            msgs = conv.query(".msg")
            assert len(msgs) >= 1
            all_text = "".join(str(w.render()) for w in msgs)
            assert "message during decision" in all_text


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    @pytest.mark.asyncio
    async def test_clou_metrics_updates_status_bar(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(
                ClouMetrics(
                    tier="supervisor",
                    milestone=None,
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.01,
                )
            )
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.input_tokens == 100
            assert bar.output_tokens == 50
            assert bar.cost_usd == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_metrics_accumulate(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(
                ClouMetrics(
                    tier="supervisor",
                    milestone=None,
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.01,
                )
            )
            await pilot.pause()
            pilot.app.post_message(
                ClouMetrics(
                    tier="coordinator",
                    milestone="m1",
                    input_tokens=200,
                    output_tokens=100,
                    cost_usd=0.02,
                )
            )
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.input_tokens == 300
            assert bar.output_tokens == 150
            assert bar.cost_usd == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# Coordinator spawned
# ---------------------------------------------------------------------------


class TestCoordinatorSpawned:
    @pytest.mark.asyncio
    async def test_updates_status_bar_milestone(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth-flow"))
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.milestone == "auth-flow"


# ---------------------------------------------------------------------------
# Status update
# ---------------------------------------------------------------------------


class TestStatusUpdate:
    @pytest.mark.asyncio
    async def test_status_update_sets_bar_fields(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(
                ClouStatusUpdate(cycle_type="PLAN", cycle_num=2, phase="design")
            )
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.cycle_type == "PLAN"
            assert bar.cycle_num == 2
            assert bar.phase == "design"


# ---------------------------------------------------------------------------
# Mode transitions
# ---------------------------------------------------------------------------


class TestModeTransitions:
    @pytest.mark.asyncio
    async def test_mode_change_swaps_css_class(self) -> None:
        async with ClouApp().run_test() as pilot:
            assert "dialogue" in pilot.app.classes
            pilot.app.mode = Mode.BREATH
            await pilot.pause()
            assert "dialogue" not in pilot.app.classes
            assert "breath" in pilot.app.classes

    @pytest.mark.asyncio
    async def test_mode_change_to_handoff(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.mode = Mode.HANDOFF
            await pilot.pause()
            assert "handoff" in pilot.app.classes
            assert "dialogue" not in pilot.app.classes


# ---------------------------------------------------------------------------
# Screen stacking guard
# ---------------------------------------------------------------------------


class TestScreenStackingGuard:
    @pytest.mark.asyncio
    async def test_double_push_context_prevented(self) -> None:
        from clou.ui.screens.context import ContextScreen

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.action_show_context()
            await pilot.pause()
            assert isinstance(app.screen, ContextScreen)
            stack_before = len(app.screen_stack)
            # Second push should be a no-op.
            app.action_show_context()
            await pilot.pause()
            assert len(app.screen_stack) == stack_before
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_double_push_costs_prevented(self) -> None:
        from clou.ui.screens.detail import DetailScreen

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.action_show_costs()
            await pilot.pause()
            assert isinstance(app.screen, DetailScreen)
            stack_before = len(app.screen_stack)
            app.action_show_costs()
            await pilot.pause()
            assert len(app.screen_stack) == stack_before
            await pilot.press("escape")


# ---------------------------------------------------------------------------
# Worker crash recovery
# ---------------------------------------------------------------------------


class TestWorkerCrashRecovery:
    @pytest.mark.asyncio
    async def test_worker_error_shows_error_message(self) -> None:
        from textual.worker import WorkerState

        async with ClouApp().run_test() as pilot:
            app = pilot.app
            conv = app.query_one(ConversationWidget)
            initial_count = len(conv.query(".msg"))

            fake_worker = type(
                "FakeWorker",
                (),
                {
                    "name": "run_supervisor_worker",
                    "error": RuntimeError("connection lost"),
                },
            )()
            fake_event = type(
                "FakeStateChanged",
                (),
                {"worker": fake_worker, "state": WorkerState.ERROR},
            )()
            app.on_worker_state_changed(fake_event)  # type: ignore[arg-type]
            await pilot.pause()

            assert len(conv.query(".msg")) > initial_count

    @pytest.mark.asyncio
    async def test_worker_error_with_none_error(self) -> None:
        from textual.worker import WorkerState

        async with ClouApp().run_test() as pilot:
            app = pilot.app
            conv = app.query_one(ConversationWidget)
            initial_count = len(conv.query(".msg"))

            fake_worker = type(
                "FakeWorker", (), {"name": "run_supervisor_worker", "error": None}
            )()
            fake_event = type(
                "FakeStateChanged",
                (),
                {"worker": fake_worker, "state": WorkerState.ERROR},
            )()
            app.on_worker_state_changed(fake_event)  # type: ignore[arg-type]
            await pilot.pause()

            msgs = conv.query(".msg")
            assert len(msgs) > initial_count
            new_text = "".join(str(w.render()) for w in list(msgs)[initial_count:])
            assert "Supervisor session ended unexpectedly" in new_text
            # No colon suffix when error is None
            assert "Supervisor session ended unexpectedly:" not in new_text

    @pytest.mark.asyncio
    async def test_worker_error_widget_not_mounted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from textual.worker import WorkerState

        async with ClouApp().run_test() as pilot:
            app = pilot.app

            monkeypatch.setattr(
                app,
                "query_one",
                lambda *a, **kw: (_ for _ in ()).throw(LookupError("No matches")),
            )

            fake_worker = type(
                "FakeWorker",
                (),
                {"name": "run_supervisor_worker", "error": RuntimeError("boom")},
            )()
            fake_event = type(
                "FakeStateChanged",
                (),
                {"worker": fake_worker, "state": WorkerState.ERROR},
            )()
            # Should not raise — LookupError is caught and swallowed
            app.on_worker_state_changed(fake_event)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DECISION mode return target
# ---------------------------------------------------------------------------


class TestDecisionModeReturn:
    """After DECISION resolves, mode should return to the pre-decision mode."""

    @pytest.mark.asyncio
    async def test_dialogue_to_decision_returns_to_dialogue(self) -> None:
        """DIALOGUE -> DECISION -> resolved should return to DIALOGUE."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.mode == Mode.DIALOGUE

            # Simulate escalation arriving while in DIALOGUE.
            app.post_message(
                ClouEscalationArrived(
                    path=Path("/tmp/test.md"),
                    classification="info",
                    issue="test",
                    options=[{"label": "Ok", "description": "ok"}],
                )
            )
            await pilot.pause()
            assert app.mode == Mode.DECISION

            # Resolve the escalation.
            app.post_message(
                ClouEscalationResolved(path=Path("/tmp/test.md"), disposition="Ok")
            )
            await pilot.pause()
            assert app.mode == Mode.DIALOGUE

    @pytest.mark.asyncio
    async def test_breath_to_decision_returns_to_breath(self) -> None:
        """BREATH -> DECISION -> resolved should return to BREATH."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Move to BREATH mode first.
            app.post_message(ClouCoordinatorSpawned(milestone="m1"))
            await pilot.pause()
            assert app.mode == Mode.BREATH

            # Simulate escalation arriving while in BREATH.
            app.post_message(
                ClouEscalationArrived(
                    path=Path("/tmp/test.md"),
                    classification="info",
                    issue="test",
                    options=[{"label": "Ok", "description": "ok"}],
                )
            )
            await pilot.pause()
            assert app.mode == Mode.DECISION

            # Resolve the escalation.
            app.post_message(
                ClouEscalationResolved(path=Path("/tmp/test.md"), disposition="Ok")
            )
            await pilot.pause()
            assert app.mode == Mode.BREATH


# ---------------------------------------------------------------------------
# DECISION → BREATH phase alignment
# ---------------------------------------------------------------------------


class TestDecisionToBreathPhaseAlignment:
    """After DECISION resolves back to BREATH, _animation_time should be
    set to the quarter-period (peak of sin) for a smooth visual transition."""

    @pytest.mark.asyncio
    async def test_animation_time_set_to_quarter_period(self) -> None:
        """DECISION → BREATH sets _animation_time to 4.5 * 0.25."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Move to BREATH mode first.
            app.post_message(ClouCoordinatorSpawned(milestone="m1"))
            await pilot.pause()
            assert app.mode == Mode.BREATH

            # Escalation arrives — enters DECISION.
            app.post_message(
                ClouEscalationArrived(
                    path=Path("/tmp/test.md"),
                    classification="info",
                    issue="test",
                    options=[{"label": "Ok", "description": "ok"}],
                )
            )
            await pilot.pause()
            assert app.mode == Mode.DECISION

            # Resolve — returns to BREATH.
            app.post_message(
                ClouEscalationResolved(path=Path("/tmp/test.md"), disposition="Ok")
            )
            await pilot.pause()
            assert app.mode == Mode.BREATH
            # Stop the timer so ticks don't drift the value further.
            app._force_stop_breathing()
            # Allow a small tolerance for timer ticks between set and assert.
            assert app._animation_time == pytest.approx(4.5 * 0.25, abs=0.2)


# ---------------------------------------------------------------------------
# Handoff handler
# ---------------------------------------------------------------------------


class TestHandoff:
    @pytest.mark.asyncio
    async def test_valid_handoff_loads_content(self, tmp_path: Path) -> None:
        """Handoff with a valid path under .clou/ loads content into widget."""
        clou_dir = tmp_path / ".clou" / "milestones" / "test"
        clou_dir.mkdir(parents=True)
        handoff_file = clou_dir / "handoff.md"
        handoff_file.write_text("# Test Handoff\n\nAll tasks completed.\n")

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(
                ClouHandoff(
                    milestone="test",
                    handoff_path=handoff_file,
                )
            )
            await pilot.pause()
            hw = app.query_one(HandoffWidget)
            assert "Test Handoff" in hw._content

    @pytest.mark.asyncio
    async def test_path_outside_clou_rejected(self, tmp_path: Path) -> None:
        """Handoff with a path outside .clou/ shows invalid-path fallback."""
        evil_file = tmp_path / "evil.md"
        evil_file.write_text("should not be read")

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouHandoff(milestone="evil", handoff_path=evil_file))
            await pilot.pause()
            hw = app.query_one(HandoffWidget)
            assert "(Invalid handoff path)" in hw._content

    @pytest.mark.asyncio
    async def test_missing_file_shows_error(self, tmp_path: Path) -> None:
        """Handoff with a nonexistent file under .clou/ shows read-error fallback."""
        missing = tmp_path / ".clou" / "milestones" / "gone" / "handoff.md"

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouHandoff(milestone="gone", handoff_path=missing))
            await pilot.pause()
            hw = app.query_one(HandoffWidget)
            assert "(Could not read handoff file)" in hw._content


# ---------------------------------------------------------------------------
# cost_usd=None handling
# ---------------------------------------------------------------------------


class TestCostUsdNone:
    @pytest.mark.asyncio
    async def test_metrics_cost_usd_none_no_typeerror(self) -> None:
        """ClouMetrics with cost_usd=None should not raise TypeError."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(
                ClouMetrics(
                    tier="supervisor",
                    milestone=None,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=None,
                )
            )
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.cost_usd == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_turn_complete_cost_usd_none_no_typeerror(self) -> None:
        """ClouTurnComplete with cost_usd=None should not raise TypeError."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(
                ClouTurnComplete(
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=None,
                    duration_ms=100,
                )
            )
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.cost_usd == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    @pytest.mark.asyncio
    async def test_session_created_on_mount(self, tmp_path: Path) -> None:
        """ClouApp creates a Session on mount."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app._session is not None
            assert app._session.path.exists()

    @pytest.mark.asyncio
    async def test_session_records_assistant_on_turn_complete(
        self, tmp_path: Path
    ) -> None:
        """Assistant content is appended to session on turn complete."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Simulate a stream.
            from clou.ui.messages import ClouStreamChunk

            conv = app.query_one(ConversationWidget)
            conv.post_message(ClouStreamChunk(text="hello world", uuid="u1"))
            await pilot.pause()
            # Turn complete triggers recording.
            conv.post_message(
                ClouTurnComplete(
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.01,
                    duration_ms=100,
                )
            )
            await pilot.pause()
            assert app._session is not None
            assert app._session.message_count >= 1

    @pytest.mark.asyncio
    async def test_session_records_user_on_processing(self, tmp_path: Path) -> None:
        """User messages are appended to session on processing started."""
        (tmp_path / ".clou").mkdir()
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            conv = app.query_one(ConversationWidget)
            conv.post_message(ClouProcessingStarted(text="my question"))
            await pilot.pause()
            assert app._session is not None
            assert app._session.message_count >= 1

    @pytest.mark.asyncio
    async def test_resume_session_id_stored(self) -> None:
        """resume_session_id parameter is stored on the app."""
        async with ClouApp(resume_session_id="test-id").run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app._resume_session_id == "test-id"
