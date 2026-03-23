"""Tests for mode transitions in ClouApp — atmospheric shifts between all four modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from clou.ui.app import ClouApp
from clou.ui.messages import (
    ClouCoordinatorComplete,
    ClouCoordinatorSpawned,
    ClouEscalationArrived,
    ClouEscalationResolved,
    ClouHandoff,
    ClouMetrics,
    ClouRateLimit,
    ClouTurnComplete,
    Mode,
)
from clou.ui.mode import BreathState
from clou.ui.widgets.breath import BreathWidget
from clou.ui.widgets.handoff import HandoffWidget
from clou.ui.widgets.conversation import ConversationWidget
from clou.ui.widgets.status_bar import ClouStatusBar

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ESCALATION_PATH = Path("/tmp/test-escalation.md")
_ESCALATION_OPTIONS: list[dict[str, object]] = [
    {"label": "Retry", "description": "Try again"},
    {"label": "Skip", "description": "Skip this step"},
]


# ---------------------------------------------------------------------------
# DIALOGUE -> BREATH
# ---------------------------------------------------------------------------


class TestDialogueToBreath:
    @pytest.mark.asyncio
    async def test_coordinator_spawned_triggers_breath_mode(self) -> None:
        async with ClouApp().run_test() as pilot:
            assert pilot.app.mode is Mode.DIALOGUE
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth-system"))
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH

    @pytest.mark.asyncio
    async def test_breath_css_class_applied(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth-system"))
            await pilot.pause()
            assert "breath" in pilot.app.classes
            assert "dialogue" not in pilot.app.classes

    @pytest.mark.asyncio
    async def test_animation_timer_starts(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app._animation_timer is None
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth-system"))
            await pilot.pause()
            assert app._animation_timer is not None

    @pytest.mark.asyncio
    async def test_breath_machine_in_breathing_state(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth-system"))
            await pilot.pause()
            assert app._breath_machine.state is BreathState.BREATHING

    @pytest.mark.asyncio
    async def test_status_bar_milestone_set(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth-system"))
            await pilot.pause()
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.milestone == "auth-system"

    @pytest.mark.asyncio
    async def test_breath_widget_exists(self) -> None:
        async with ClouApp().run_test() as pilot:
            bw = pilot.app.query_one(BreathWidget)
            assert bw is not None


# ---------------------------------------------------------------------------
# BREATH -> DIALOGUE
# ---------------------------------------------------------------------------


class TestBreathToDialogue:
    @pytest.mark.asyncio
    async def test_transition_restores_dialogue(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Enter breath mode first.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH

            # Transition back to dialogue.
            app.transition_mode(Mode.DIALOGUE)
            await pilot.pause()
            assert pilot.app.mode is Mode.DIALOGUE
            assert "dialogue" in pilot.app.classes
            assert "breath" not in pilot.app.classes

    @pytest.mark.asyncio
    async def test_timer_keeps_running_for_graceful_shutdown(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app._animation_timer is not None

            app.transition_mode(Mode.DIALOGUE)
            await pilot.pause()
            # Timer stays running during RELEASING/SETTLING graceful shutdown.
            assert app._animation_timer is not None

    @pytest.mark.asyncio
    async def test_breath_machine_enters_releasing(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            app.transition_mode(Mode.DIALOGUE)
            await pilot.pause()
            # Graceful shutdown starts with RELEASING, not immediate IDLE.
            assert app._breath_machine.state is BreathState.RELEASING


# ---------------------------------------------------------------------------
# BREATH -> DECISION
# ---------------------------------------------------------------------------


class TestBreathToDecision:
    @pytest.mark.asyncio
    async def test_escalation_triggers_decision_mode(self) -> None:
        async with ClouApp().run_test() as pilot:
            # Enter breath mode first.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            # Post escalation.
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="blocking",
                    issue="Need credentials",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.DECISION

    @pytest.mark.asyncio
    async def test_escalation_modal_pushed(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="warning",
                    issue="Rate limit approaching",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            # The screen stack should have more than one screen.
            assert len(pilot.app.screen_stack) > 1

    @pytest.mark.asyncio
    async def test_breath_machine_holding(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="warning",
                    issue="Issue",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert app._breath_machine.state is BreathState.HOLDING


# ---------------------------------------------------------------------------
# DECISION -> BREATH
# ---------------------------------------------------------------------------


class TestDecisionToBreath:
    @pytest.mark.asyncio
    async def test_escalation_resolved_returns_to_breath(self) -> None:
        async with ClouApp().run_test() as pilot:
            # DIALOGUE -> BREATH.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            # BREATH -> DECISION.
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="warning",
                    issue="Issue",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.DECISION

            # DECISION -> BREATH.
            pilot.app.post_message(
                ClouEscalationResolved(
                    path=_ESCALATION_PATH,
                    disposition="Retry",
                )
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH
            assert "breath" in pilot.app.classes


# ---------------------------------------------------------------------------
# Illegal transitions
# ---------------------------------------------------------------------------


class TestHandoffTransitions:
    @pytest.mark.asyncio
    async def test_handoff_to_breath_legal(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Get to handoff via legal path.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.HANDOFF

            result = app.transition_mode(Mode.BREATH)
            await pilot.pause()
            assert result is True
            assert pilot.app.mode is Mode.BREATH


class TestIllegalTransitions:
    """Self-transitions are the only illegal mode transitions."""

    @pytest.mark.asyncio
    async def test_dialogue_to_dialogue_illegal(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.mode is Mode.DIALOGUE
            result = app.transition_mode(Mode.DIALOGUE)
            assert result is False
            assert app.mode is Mode.DIALOGUE

    @pytest.mark.asyncio
    async def test_breath_to_breath_illegal(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app.mode is Mode.BREATH
            result = app.transition_mode(Mode.BREATH)
            assert result is False
            assert app.mode is Mode.BREATH

    @pytest.mark.asyncio
    async def test_decision_to_decision_illegal(self) -> None:
        async with ClouApp().run_test(size=(0, 0)) as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.transition_mode(Mode.DECISION)
            await pilot.pause()
            assert app.mode is Mode.DECISION
            result = app.transition_mode(Mode.DECISION)
            assert result is False
            assert app.mode is Mode.DECISION

    @pytest.mark.asyncio
    async def test_handoff_to_handoff_illegal(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.transition_mode(Mode.HANDOFF)
            await pilot.pause()
            assert app.mode is Mode.HANDOFF
            result = app.transition_mode(Mode.HANDOFF)
            assert result is False
            assert app.mode is Mode.HANDOFF


# ---------------------------------------------------------------------------
# BREATH -> HANDOFF (via coordinator complete)
# ---------------------------------------------------------------------------


class TestBreathToHandoff:
    @pytest.mark.asyncio
    async def test_coordinator_complete_triggers_handoff(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH

            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.HANDOFF
            assert "handoff" in pilot.app.classes

    @pytest.mark.asyncio
    async def test_coordinator_failed_returns_to_dialogue(self) -> None:
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="failed")
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.DIALOGUE

    @pytest.mark.asyncio
    async def test_animation_timer_keeps_running_on_handoff(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app._animation_timer is not None

            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            # Timer stays running during graceful RELEASING/SETTLING shutdown.
            assert app._animation_timer is not None
            assert app._breath_machine.state is BreathState.RELEASING


# ---------------------------------------------------------------------------
# Input during BREATH mode
# ---------------------------------------------------------------------------


class TestInputDuringBreath:
    @pytest.mark.asyncio
    async def test_input_visible_during_breath(self) -> None:
        """PromptInput stays interactable when conversation recedes in breath mode."""
        async with ClouApp().run_test() as pilot:
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH

            # Input should be queryable and focusable inside #conversation.
            inp = pilot.app.query_one("#user-input Input")
            assert inp.display is True
            inp.value = "typing in breath"
            assert inp.value == "typing in breath"

    @pytest.mark.asyncio
    async def test_input_triggers_breath_to_dialogue(self) -> None:
        async with ClouApp().run_test() as pilot:
            # Enter breath mode.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert pilot.app.mode is Mode.BREATH

            # Submit input.
            inp = pilot.app.query_one("#user-input Input")
            inp.value = "hello from breath"  # type: ignore[union-attr]
            await inp.action_submit()  # type: ignore[union-attr]
            await pilot.pause()
            assert pilot.app.mode is Mode.DIALOGUE


# ---------------------------------------------------------------------------
# DIALOGUE -> HANDOFF (via coordinator complete while in dialogue)
# ---------------------------------------------------------------------------


class TestDialogueToHandoff:
    @pytest.mark.asyncio
    async def test_coordinator_complete_in_dialogue_triggers_handoff(self) -> None:
        """User types during BREATH (returns to DIALOGUE), then coordinator
        completes successfully — mode should transition to HANDOFF."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # DIALOGUE -> BREATH.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app.mode is Mode.BREATH

            # User types, returning to DIALOGUE.
            inp = pilot.app.query_one("#user-input Input")
            inp.value = "hello"  # type: ignore[union-attr]
            await inp.action_submit()  # type: ignore[union-attr]
            await pilot.pause()
            assert app.mode is Mode.DIALOGUE

            # Coordinator completes while still in DIALOGUE.
            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF
            assert "handoff" in pilot.app.classes
            assert "dialogue" not in pilot.app.classes

    @pytest.mark.asyncio
    async def test_dialogue_to_handoff_css_class(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.mode is Mode.DIALOGUE
            result = app.transition_mode(Mode.HANDOFF)
            await pilot.pause()
            assert result is True
            assert app.mode is Mode.HANDOFF
            assert "handoff" in pilot.app.classes
            assert "dialogue" not in pilot.app.classes


# ---------------------------------------------------------------------------
# DIALOGUE -> DECISION (new direct transition)
# ---------------------------------------------------------------------------


class TestDialogueToDecision:
    @pytest.mark.asyncio
    async def test_dialogue_to_decision_succeeds(self) -> None:
        # Use size=(0, 0) to avoid Textual opacity rendering bug in decision CSS.
        async with ClouApp().run_test(size=(0, 0)) as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert pilot.app.mode is Mode.DIALOGUE
            result = app.transition_mode(Mode.DECISION)
            await pilot.pause()
            assert result is True
            assert pilot.app.mode is Mode.DECISION
            assert "decision" in pilot.app.classes
            assert "dialogue" not in pilot.app.classes

    @pytest.mark.asyncio
    async def test_escalation_from_dialogue_pushes_modal(self) -> None:
        async with ClouApp().run_test(size=(0, 0)) as pilot:
            # Post escalation directly from DIALOGUE (no BREATH first).
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="blocking",
                    issue="Need credentials",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert pilot.app.mode is Mode.DECISION
            # Escalation modal should be pushed.
            assert len(pilot.app.screen_stack) > 1


# ---------------------------------------------------------------------------
# DECISION -> DIALOGUE (new direct transition)
# ---------------------------------------------------------------------------


class TestDecisionToDialogue:
    @pytest.mark.asyncio
    async def test_decision_to_dialogue_succeeds(self) -> None:
        async with ClouApp().run_test(size=(0, 0)) as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Get to DECISION first.
            app.transition_mode(Mode.DECISION)
            await pilot.pause()
            assert pilot.app.mode is Mode.DECISION

            result = app.transition_mode(Mode.DIALOGUE)
            await pilot.pause()
            assert result is True
            assert pilot.app.mode is Mode.DIALOGUE
            assert "dialogue" in pilot.app.classes
            assert "decision" not in pilot.app.classes

    @pytest.mark.asyncio
    async def test_breath_decision_dialogue_starts_graceful_shutdown(self) -> None:
        """BREATH -> DECISION -> DIALOGUE starts graceful breathing shutdown."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # DIALOGUE -> BREATH (starts timer).
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app.mode is Mode.BREATH
            assert app._animation_timer is not None

            # BREATH -> DECISION (timer still running).
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="warning",
                    issue="Issue",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION

            # DECISION -> DIALOGUE — graceful shutdown starts (RELEASING).
            app.transition_mode(Mode.DIALOGUE)
            await pilot.pause()
            assert app.mode is Mode.DIALOGUE
            # Timer stays running during graceful RELEASING/SETTLING shutdown.
            assert app._animation_timer is not None
            assert app._breath_machine.state is BreathState.RELEASING

    @pytest.mark.asyncio
    async def test_dialogue_decision_dialogue_no_crash(self) -> None:
        """DIALOGUE -> DECISION -> DIALOGUE must not crash (no timer was running)."""
        async with ClouApp().run_test(size=(0, 0)) as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.mode is Mode.DIALOGUE
            assert app._animation_timer is None

            # DIALOGUE -> DECISION.
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="info",
                    issue="Test",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION

            # DECISION -> DIALOGUE (no timer was ever started).
            pilot.app.post_message(
                ClouEscalationResolved(
                    path=_ESCALATION_PATH,
                    disposition="Retry",
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DIALOGUE
            assert app._animation_timer is None


# ---------------------------------------------------------------------------
# DECISION -> HANDOFF (timer cleanup)
# ---------------------------------------------------------------------------


class TestDecisionToHandoff:
    @pytest.mark.asyncio
    async def test_breath_decision_handoff_starts_graceful_shutdown(self) -> None:
        """BREATH -> DECISION -> HANDOFF starts graceful breathing shutdown."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # DIALOGUE -> BREATH (starts timer).
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app.mode is Mode.BREATH
            assert app._animation_timer is not None

            # BREATH -> DECISION.
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="warning",
                    issue="Issue",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION
            assert app._animation_timer is not None  # still running

            # DECISION -> HANDOFF — graceful shutdown starts.
            app.transition_mode(Mode.HANDOFF)
            await pilot.pause()
            assert app.mode is Mode.HANDOFF
            # Timer stays running during graceful RELEASING/SETTLING shutdown.
            assert app._animation_timer is not None
            assert app._breath_machine.state is BreathState.RELEASING


# ---------------------------------------------------------------------------
# HANDOFF -> DECISION (escalation during handoff)
# ---------------------------------------------------------------------------


class TestHandoffToDecision:
    @pytest.mark.asyncio
    async def test_escalation_during_handoff_transitions_to_decision(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # DIALOGUE -> BREATH -> HANDOFF.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            # Escalation during HANDOFF -> DECISION.
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="blocking",
                    issue="Need credentials",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION
            # Escalation modal should be pushed.
            assert len(pilot.app.screen_stack) > 1

    @pytest.mark.asyncio
    async def test_escalation_resolved_returns_to_handoff(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # DIALOGUE -> BREATH -> HANDOFF.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            # HANDOFF -> DECISION.
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=_ESCALATION_PATH,
                    classification="warning",
                    issue="Issue",
                    options=_ESCALATION_OPTIONS,
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION

            # DECISION -> HANDOFF (resolved returns to pre-decision mode).
            pilot.app.post_message(
                ClouEscalationResolved(
                    path=_ESCALATION_PATH,
                    disposition="Retry",
                )
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF


# ---------------------------------------------------------------------------
# Rate limit handler
# ---------------------------------------------------------------------------


class TestRateLimitHandler:
    @pytest.mark.asyncio
    async def test_rate_limit_sets_status_bar(self) -> None:
        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)
            assert bar.rate_limited is False

            pilot.app.post_message(ClouRateLimit(status="rate_limited", resets_at=None))
            await pilot.pause()
            assert bar.rate_limited is True

    @pytest.mark.asyncio
    async def test_rate_limit_clears_status_bar(self) -> None:
        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)

            # Set rate limited.
            pilot.app.post_message(ClouRateLimit(status="rate_limited", resets_at=None))
            await pilot.pause()
            assert bar.rate_limited is True

            # Clear rate limited.
            pilot.app.post_message(ClouRateLimit(status="", resets_at=None))
            await pilot.pause()
            assert bar.rate_limited is False


# ---------------------------------------------------------------------------
# Handoff handler (on_clou_handoff)
# ---------------------------------------------------------------------------


class TestHandoffHandler:
    @pytest.mark.asyncio
    async def test_handoff_loads_content(self, tmp_path: Path) -> None:
        """ClouHandoff with valid path loads content into HandoffWidget."""
        clou_dir = tmp_path / ".clou" / "handoffs"
        clou_dir.mkdir(parents=True)
        handoff_file = clou_dir / "handoff.md"
        handoff_file.write_text("# Handoff: auth\n\n## Summary\n\nAll good.\n")

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Transition to HANDOFF first.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="auth"))
            await pilot.pause()
            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="auth", result="completed")
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            # Post handoff message with valid .clou path.
            pilot.app.post_message(
                ClouHandoff(milestone="auth", handoff_path=handoff_file)
            )
            await pilot.pause()

            hw = pilot.app.query_one(HandoffWidget)
            assert "All good." in hw._content

    @pytest.mark.asyncio
    async def test_handoff_oserror_fallback(self, tmp_path: Path) -> None:
        """ClouHandoff with nonexistent .clou path uses fallback content."""
        clou_dir = tmp_path / ".clou" / "handoffs"
        clou_dir.mkdir(parents=True)
        bad_path = clou_dir / "nonexistent.md"  # Under .clou but doesn't exist

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Transition to HANDOFF.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(
                ClouCoordinatorComplete(milestone="test", result="completed")
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            # Post handoff with a path that doesn't exist.
            pilot.app.post_message(
                ClouHandoff(milestone="test", handoff_path=bad_path)
            )
            await pilot.pause()

            hw = pilot.app.query_one(HandoffWidget)
            assert "Could not read handoff file" in hw._content

    @pytest.mark.asyncio
    async def test_handoff_from_breath_mode(self, tmp_path: Path) -> None:
        """ClouHandoff while in BREATH mode transitions BREATH→HANDOFF."""
        clou_dir = tmp_path / ".clou" / "handoffs"
        clou_dir.mkdir(parents=True)
        handoff_file = clou_dir / "handoff.md"
        handoff_file.write_text("# Breath Handoff\n\nContent here.\n")

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Enter BREATH mode via coordinator spawn.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app.mode is Mode.BREATH

            # Post ClouHandoff while in BREATH (not via ClouCoordinatorComplete).
            pilot.app.post_message(
                ClouHandoff(milestone="test", handoff_path=handoff_file)
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

    @pytest.mark.asyncio
    async def test_handoff_transitions_to_handoff_mode(self) -> None:
        """ClouHandoff transitions to HANDOFF if not already there."""
        # Use a path outside .clou so we hit the invalid-path fallback
        # but the mode transition still happens.
        outside_path = Path("/tmp/not-clou/handoff.md")

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.mode is Mode.DIALOGUE

            pilot.app.post_message(
                ClouHandoff(milestone="test", handoff_path=outside_path)
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            hw = pilot.app.query_one(HandoffWidget)
            assert "Invalid handoff path" in hw._content

    @pytest.mark.asyncio
    async def test_handoff_invalid_path_strips_ansi_from_milestone(self, tmp_path: Path) -> None:
        """ClouHandoff with ANSI in milestone and path outside .clou/ strips escapes."""
        outside_path = Path("/tmp/not-clou/handoff.md")

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            pilot.app.post_message(
                ClouHandoff(milestone="\x1b[31mevil\x1b[0m", handoff_path=outside_path)
            )
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            hw = pilot.app.query_one(HandoffWidget)
            assert "\x1b" not in hw._content
            assert "Invalid handoff path" in hw._content


# ---------------------------------------------------------------------------
# ClouMetrics / ClouTurnComplete with cost_usd=None
# ---------------------------------------------------------------------------


class TestMetricsNoneCost:
    @pytest.mark.asyncio
    async def test_clou_metrics_none_cost(self) -> None:
        """ClouMetrics with cost_usd=None doesn't crash or change cost."""
        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)
            initial_cost = bar.cost_usd
            pilot.app.post_message(
                ClouMetrics(
                    tier="test",
                    milestone=None,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=None,
                )
            )
            await pilot.pause()
            assert bar.cost_usd == initial_cost  # unchanged
            assert bar.input_tokens == 10  # tokens still updated

    @pytest.mark.asyncio
    async def test_clou_turn_complete_none_cost(self) -> None:
        """ClouTurnComplete with cost_usd=None doesn't crash or change cost."""
        async with ClouApp().run_test() as pilot:
            bar = pilot.app.query_one(ClouStatusBar)
            initial_cost = bar.cost_usd
            pilot.app.post_message(
                ClouTurnComplete(
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=None,
                    duration_ms=100,
                )
            )
            await pilot.pause()
            assert bar.cost_usd == initial_cost  # unchanged
            assert bar.input_tokens == 10  # tokens still updated


# ---------------------------------------------------------------------------
# _animation_tick HOLDING / SETTLING branches
# ---------------------------------------------------------------------------


class TestAnimationTickBranches:
    @pytest.mark.asyncio
    async def test_holding_sets_breath_phase_to_one(self) -> None:
        """When breath_machine is HOLDING, _animation_tick sets phase to 1.0."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Enter BREATH so the widget is live.
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app._breath_machine.state is BreathState.BREATHING

            # BREATHING -> HOLDING.
            app._breath_machine.transition(BreathState.HOLDING)
            assert app._breath_machine.state is BreathState.HOLDING

            app._animation_tick()
            bw = app.query_one(BreathWidget)
            assert bw.breath_phase == 1.0

    @pytest.mark.asyncio
    async def test_settling_sets_breath_phase_to_zero(self) -> None:
        """When breath_machine is SETTLING, _animation_tick sets phase to 0.0."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            # Enter BREATH so the widget is live.
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            # BREATHING -> SETTLING.
            app._breath_machine.transition(BreathState.SETTLING)
            assert app._breath_machine.state is BreathState.SETTLING

            app._animation_tick()
            bw = app.query_one(BreathWidget)
            assert bw.breath_phase == 0.0


# ---------------------------------------------------------------------------
# action_clear
# ---------------------------------------------------------------------------


class TestActionClear:
    @pytest.mark.asyncio
    async def test_action_clear_clears_history(self) -> None:
        """action_clear empties the conversation history."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            conv = app.query_one(ConversationWidget)
            conv.add_command_output("test message")
            await pilot.pause()
            assert len(conv.query(".msg")) > 0

            app.action_clear()
            await pilot.pause()
            assert len(conv.query(".msg")) == 0


# ---------------------------------------------------------------------------
# action_show_costs
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Escalation dropped when DECISION→DECISION is blocked
# ---------------------------------------------------------------------------


class TestEscalationDropOnDecision:
    @pytest.mark.asyncio
    async def test_escalation_dropped_when_already_in_decision(self, tmp_path: Path) -> None:
        """ClouEscalationArrived during DECISION mode is dropped (transition fails)."""
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Get into DECISION mode via a first escalation.
            esc_dir = tmp_path / ".clou" / "escalations"
            esc_dir.mkdir(parents=True)
            esc_file1 = esc_dir / "first.md"
            esc_file1.write_text("# First\n")

            pilot.app.post_message(
                ClouEscalationArrived(
                    path=esc_file1,
                    classification="blocking",
                    issue="First issue",
                    options=[{"label": "Fix", "description": "Fix it"}],
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION

            # Now send a second escalation while still in DECISION.
            esc_file2 = esc_dir / "second.md"
            esc_file2.write_text("# Second\n")
            pilot.app.post_message(
                ClouEscalationArrived(
                    path=esc_file2,
                    classification="error",
                    issue="Second issue",
                    options=[{"label": "Retry", "description": "Try again"}],
                )
            )
            await pilot.pause()

            # Should still be in DECISION, and _pending_escalation should be None
            # (the second was dropped because transition failed).
            assert app.mode is Mode.DECISION
            assert app._pending_escalation is None


# ---------------------------------------------------------------------------
# _format_costs content assertion
# ---------------------------------------------------------------------------


class TestFormatCosts:
    @pytest.mark.asyncio
    async def test_format_costs_content(self) -> None:
        """_format_costs returns correctly formatted token/cost string."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            bar = app.query_one(ClouStatusBar)
            bar.input_tokens = 1500
            bar.output_tokens = 500
            bar.cost_usd = 0.42

            result = app._format_costs()
            assert "1,500" in result  # Comma-formatted input tokens
            assert "500" in result    # Output tokens
            assert "$0.42" in result  # Dollar-formatted cost


# ---------------------------------------------------------------------------
# action_show_costs
# ---------------------------------------------------------------------------


class TestActionShowCosts:
    @pytest.mark.asyncio
    async def test_action_show_costs_pushes_detail_screen(self) -> None:
        """action_show_costs pushes a DetailScreen onto the screen stack."""
        from clou.ui.screens.detail import DetailScreen

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.action_show_costs()
            await pilot.pause()
            assert any(
                isinstance(s, DetailScreen) for s in app.screen_stack
            )


# ---------------------------------------------------------------------------
# Rate-limit status variants
# ---------------------------------------------------------------------------


class TestRateLimitVariants:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,expected", [
        ("active", True),
        ("rate_limited", True),
        ("limited", True),
        ("resolved", False),
        ("cleared", False),
        ("", False),
    ])
    async def test_rate_limit_status_variants(self, status: str, expected: bool) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app
            pilot.app.post_message(ClouRateLimit(status=status, resets_at=None))
            await pilot.pause()
            bar = app.query_one(ClouStatusBar)
            assert bar.rate_limited is expected


# ---------------------------------------------------------------------------
# Coordinator complete resets status bar metadata
# ---------------------------------------------------------------------------


class TestCoordinatorCompleteReset:
    @pytest.mark.asyncio
    async def test_coordinator_complete_resets_bar_metadata(self) -> None:
        """on_clou_coordinator_complete resets cycle_type, cycle_num, phase."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app
            bar = app.query_one(ClouStatusBar)

            # Set some cycle metadata.
            bar.cycle_type = "EXECUTE"
            bar.cycle_num = 3
            bar.phase = "testing"

            # Enter BREATH, then complete coordinator.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(ClouCoordinatorComplete(milestone="test", result="completed"))
            await pilot.pause()

            assert bar.cycle_type == ""
            assert bar.cycle_num == 0
            assert bar.phase == ""


# ---------------------------------------------------------------------------
# Input during HANDOFF transitions to DIALOGUE
# ---------------------------------------------------------------------------


class TestInputDuringHandoff:
    @pytest.mark.asyncio
    async def test_input_during_handoff_transitions_to_dialogue(self) -> None:
        """User input during HANDOFF mode returns to DIALOGUE."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app

            # Get to HANDOFF mode.
            pilot.app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            pilot.app.post_message(ClouCoordinatorComplete(milestone="test", result="completed"))
            await pilot.pause()
            assert app.mode is Mode.HANDOFF

            # Submit input.
            input_widget = app.query_one("#user-input Input")
            input_widget.value = "hello"
            await input_widget.action_submit()
            await pilot.pause()

            assert app.mode is Mode.DIALOGUE


# ---------------------------------------------------------------------------
# RELEASING animation tick — decay and transition to SETTLING
# ---------------------------------------------------------------------------


class TestReleasingAnimationTick:
    @pytest.mark.asyncio
    async def test_releasing_tick_decays_breath_value(self) -> None:
        """During RELEASING, breath value decays from start value toward 0."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app._breath_machine.state is BreathState.BREATHING

            # Trigger graceful stop (BREATHING → RELEASING).
            app._stop_breathing()
            assert app._breath_machine.state is BreathState.RELEASING

            # First tick — should produce a value between 0 and the start value.
            start_val = app._release_start_value
            app._animation_tick()
            bw = app.query_one(BreathWidget)
            assert bw.breath_phase < start_val or start_val == 0.0

    @pytest.mark.asyncio
    async def test_releasing_completes_to_settling(self) -> None:
        """After enough ticks, RELEASING transitions to SETTLING."""
        from clou.ui.mode import TIMING

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            app._stop_breathing()
            assert app._breath_machine.state is BreathState.RELEASING

            # Advance time past the releasing duration.
            releasing_s = TIMING["releasing"] / 1000.0
            ticks_needed = int(releasing_s / (1.0 / 24)) + 2
            for _ in range(ticks_needed):
                app._animation_tick()

            assert app._breath_machine.state is BreathState.SETTLING

    @pytest.mark.asyncio
    async def test_settling_completes_to_idle_and_stops_timer(self) -> None:
        """After SETTLING duration elapses, state reaches IDLE and timer stops."""
        from clou.ui.mode import TIMING

        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            app._stop_breathing()

            # Advance through RELEASING.
            releasing_s = TIMING["releasing"] / 1000.0
            releasing_ticks = int(releasing_s / (1.0 / 24)) + 2
            for _ in range(releasing_ticks):
                app._animation_tick()
            assert app._breath_machine.state is BreathState.SETTLING

            # Advance through SETTLING.
            settle_s = TIMING["settle"] / 1000.0
            settle_ticks = int(settle_s / (1.0 / 24)) + 2
            for _ in range(settle_ticks):
                if app._breath_machine.state is BreathState.IDLE:
                    break
                app._animation_tick()

            assert app._breath_machine.state is BreathState.IDLE
            assert app._animation_timer is None


# ---------------------------------------------------------------------------
# push_screen guard — double escalation produces only one modal
# ---------------------------------------------------------------------------


class TestPushScreenGuard:
    @pytest.mark.asyncio
    async def test_double_escalation_produces_one_modal(self, tmp_path: Path) -> None:
        """Pushing two escalations in quick succession produces only one modal."""
        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            esc_dir = tmp_path / ".clou" / "escalations"
            esc_dir.mkdir(parents=True)
            esc1 = esc_dir / "esc1.md"
            esc1.write_text("# First\n")

            # First escalation — enters DECISION and pushes modal.
            app.post_message(
                ClouEscalationArrived(
                    path=esc1,
                    classification="warning",
                    issue="Issue 1",
                    options=[{"label": "Fix", "description": "Fix it"}],
                )
            )
            await pilot.pause()
            assert app.mode is Mode.DECISION

            from clou.ui.widgets.escalation import EscalationModal

            modal_count = sum(
                1 for s in app.screen_stack if isinstance(s, EscalationModal)
            )
            assert modal_count == 1

            # Manually try to push another escalation via _push_pending_escalation.
            app._pending_escalation = (
                esc1, "error", "Issue 2",
                [{"label": "Retry", "description": "Try again"}],
            )
            app._push_pending_escalation()
            await pilot.pause()

            # Guard should prevent a second modal.
            modal_count = sum(
                1 for s in app.screen_stack if isinstance(s, EscalationModal)
            )
            assert modal_count == 1


# ---------------------------------------------------------------------------
# _stop_breathing edge cases
# ---------------------------------------------------------------------------


class TestStopBreathingEdgeCases:
    @pytest.mark.asyncio
    async def test_stop_breathing_during_releasing_keeps_timer(self) -> None:
        """Calling _stop_breathing while already RELEASING keeps the timer running."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            app._stop_breathing()
            assert app._breath_machine.state is BreathState.RELEASING
            assert app._animation_timer is not None

            # Call again — should stay in RELEASING, timer still alive.
            app._stop_breathing()
            assert app._breath_machine.state is BreathState.RELEASING
            assert app._animation_timer is not None

    @pytest.mark.asyncio
    async def test_stop_breathing_when_idle_stops_orphan_timer(self) -> None:
        """Calling _stop_breathing when IDLE stops any orphaned timer."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]

            # Simulate an orphan timer: machine is IDLE but timer exists.
            app._breath_machine.reset()
            assert app._breath_machine.state is BreathState.IDLE
            app._animation_timer = app.set_interval(
                1.0 / 24, app._animation_tick
            )
            assert app._animation_timer is not None

            app._stop_breathing()
            assert app._animation_timer is None

    @pytest.mark.asyncio
    async def test_start_breathing_during_releasing_is_noop(self) -> None:
        """_start_breathing while RELEASING does NOT reset animation time."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()

            # Advance animation time.
            for _ in range(10):
                app._animation_tick()
            time_before = app._animation_time

            app._stop_breathing()
            assert app._breath_machine.state is BreathState.RELEASING

            # Attempting to restart should be a no-op.
            app._start_breathing()
            assert app._breath_machine.state is BreathState.RELEASING
            # Animation time should NOT have been reset to 0.
            assert app._animation_time >= time_before


# ---------------------------------------------------------------------------
# _force_stop_breathing
# ---------------------------------------------------------------------------


class TestForceStopBreathing:
    @pytest.mark.asyncio
    async def test_force_stop_from_breathing(self) -> None:
        """_force_stop_breathing kills the timer and resets to IDLE."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            assert app._breath_machine.state is BreathState.BREATHING
            assert app._animation_timer is not None

            app._force_stop_breathing()
            assert app._breath_machine.state is BreathState.IDLE
            assert app._animation_timer is None

    @pytest.mark.asyncio
    async def test_force_stop_from_releasing(self) -> None:
        """_force_stop_breathing works even during RELEASING."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouCoordinatorSpawned(milestone="test"))
            await pilot.pause()
            app._stop_breathing()
            assert app._breath_machine.state is BreathState.RELEASING

            app._force_stop_breathing()
            assert app._breath_machine.state is BreathState.IDLE
            assert app._animation_timer is None

    @pytest.mark.asyncio
    async def test_force_stop_when_already_idle(self) -> None:
        """_force_stop_breathing is safe to call when already IDLE with no timer."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app._breath_machine.state is BreathState.IDLE
            assert app._animation_timer is None

            # Should not raise.
            app._force_stop_breathing()
            assert app._breath_machine.state is BreathState.IDLE
            assert app._animation_timer is None
