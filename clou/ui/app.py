"""ClouApp — the Textual application shell.

Composes the conversation widget, breath widget, status bar, and input
field.  Manages mode transitions, breathing animation, and escalation
modal lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import TextArea

from clou.ui.widgets.prompt_input import ChatInput
from textual.worker import Worker, WorkerState

from clou import telemetry
from clou.session import Session
from clou.ui.bridge import _strip_ansi
from clou.ui.history import ConversationEntry
from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouAskUser,
    ClouCoordinatorComplete,
    ClouCoordinatorSpawned,
    ClouDagUpdate,
    ClouEscalationArrived,
    ClouEscalationResolved,
    ClouHandoff,
    ClouMetrics,
    ClouProcessingStarted,
    ClouRateLimit,
    ClouStatusUpdate,
    ClouTurnComplete,
    ClouTurnContentReady,
)
from clou.ui.mode import TIMING, BreathState, BreathStateMachine, Mode, get_transition
from clou.ui.screens.context import ContextScreen
from clou.ui.screens.dag import DagScreen
from clou.ui.screens.detail import DetailScreen
from clou.ui.task_graph import TaskGraphModel, TaskState
from clou.ui.widgets.breath import BreathWidget
from clou.ui.widgets.task_graph import TaskGraphWidget
from clou.ui.widgets.conversation import ConversationWidget
from clou.ui.widgets.escalation import EscalationModal
from clou.ui.widgets.handoff import HandoffWidget
from clou.ui.widgets.resize_handle import ResizeHandle
from clou.ui.widgets.status_bar import ClouStatusBar

_log = logging.getLogger(__name__)

#: Animation frame rate (frames per second).
_FPS: int = 24
_FRAME_DURATION: float = 1.0 / _FPS


@dataclass
class CompactionState:
    """State machine for context compaction.

    Groups the 6 coordinating variables that were previously scattered
    across ClouApp instance vars.  Manipulated by the orchestrator's
    ``_feed_user_input`` coroutine (request side) and the supervisor
    receive loop (completion side).
    """

    requested: asyncio.Event = field(default_factory=asyncio.Event)
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    instructions: str = ""
    pending: bool = False
    results_to_skip: int = 0
    count: int = 0

    def reset(self) -> None:
        """Reset to initial state (e.g., on new supervisor session)."""
        self.requested.clear()
        self.complete.clear()
        self.instructions = ""
        self.pending = False
        self.results_to_skip = 0
        self.count = 0


class ClouApp(App[None]):
    """The breathing conversation — Clou's terminal interface."""

    CSS_PATH = "clou.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        # Copy-on-select handles clipboard via pbcopy on drag release.
        # These priority no-ops prevent Screen.copy_text (non-priority)
        # and Textual's default ctrl+c → quit from overwriting.
        Binding("ctrl+c,super+c", "noop", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+g", "show_context", "Context", show=False),
        Binding("ctrl+d", "show_dag", "DAG", show=False),
        Binding("ctrl+t", "show_costs", "Costs", show=False),
    ]

    mode: reactive[Mode] = reactive(Mode.DIALOGUE)

    def __init__(
        self,
        project_dir: Path | None = None,
        work_dir: Path | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._project_dir = project_dir or Path.cwd()
        self._work_dir = work_dir or Path.cwd()
        self._resume_session_id = resume_session_id
        self._user_input_queue: deque[str] = deque()
        self._user_input_ready: asyncio.Event = asyncio.Event()
        self._animation_timer: Timer | None = None
        self._animation_time: float = 0.0
        self._breath_machine = BreathStateMachine()
        self._escalation_queue: deque[
            tuple[Path, str, str, list[dict[str, object]]]
        ] = deque()
        self._release_start_time: float = 0.0
        self._release_start_value: float = 0.0
        self._settle_start_time: float = 0.0
        self._pre_decision_mode: Mode = Mode.DIALOGUE
        self._dag_tasks: list[dict[str, str]] = []
        self._dag_deps: dict[str, list[str]] = {}
        self._task_graph_model: TaskGraphModel | None = None
        self._synthetic_dag: bool = False
        self._agent_task_map: dict[str, str] = {}  # task_id → task_name
        self._session_start_time: float = time.monotonic()
        self._conversation_history: list[ConversationEntry] = []
        # Compact state machine (request/completion handshake with orchestrator).
        self._compact = CompactionState()
        # Stop signaling: /stop command sets event; orchestrator checks
        # at cycle boundary (DB-15 Tension 1).
        self._stop_requested: asyncio.Event = asyncio.Event()
        # Model state.
        self._model: str = "opus"
        self._queue_count: int = 0
        # Session persistence — auto-append every turn to JSONL.
        self._session: Session | None = None
        # User-dragged override for the conversation/task-graph split height.
        # None means "use CSS default"; set by ResizeHandle during BREATH/DECISION.
        self._conversation_override_height: int | None = None

    def compose(self) -> ComposeResult:
        yield ConversationWidget(id="conversation")
        yield ResizeHandle(id="resize-handle")
        yield TaskGraphWidget(id="task-graph")
        yield BreathWidget(id="breath-widget")
        yield HandoffWidget(id="handoff-widget")
        yield ClouStatusBar(id="status-bar")

    def on_mount(self) -> None:
        """Apply the initial mode CSS class and start the supervisor."""
        self.add_class(self.mode.name.lower())
        self.query_one("#user-input ChatInput").focus()
        # Start session persistence and telemetry.
        self._session = Session(self._project_dir, model=self._model)
        telemetry.init(self._session.session_id, self._project_dir)
        self.run_supervisor_worker()

    @work(exclusive=True)
    async def run_supervisor_worker(self) -> None:
        """Run the supervisor session as a Textual async worker."""
        try:
            from clou.orchestrator import run_supervisor
        except ImportError:
            self.log.warning("Claude Agent SDK not available")
            return
        await run_supervisor(self._project_dir, app=self)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle supervisor worker terminal states."""
        if event.worker.name != "run_supervisor_worker":
            return
        if event.state == WorkerState.ERROR:
            error_msg = "Supervisor session ended unexpectedly"
            if event.worker.error:
                error_msg += f": {_strip_ansi(str(event.worker.error))}"
            try:
                conversation = self.query_one(ConversationWidget)
                conversation._end_initializing()
                conversation.add_error_message(error_msg)
                conversation.reset_turn_state()
            except LookupError:
                pass  # Widget may not be mounted yet
            # Drain the dead queue and stale escalations.
            self._user_input_queue.clear()
            self._user_input_ready.clear()
            self._queue_count = 0
            self._escalation_queue.clear()
            try:
                self.query_one(ConversationWidget).update_queue_count(0)
            except LookupError:
                pass
            # Return to dialogue mode if in an ambient or paused mode.
            if self.mode in (Mode.BREATH, Mode.DECISION, Mode.HANDOFF):
                self.transition_mode(Mode.DIALOGUE)
        elif event.state in (WorkerState.SUCCESS, WorkerState.CANCELLED):
            # Normal exit or cancellation — the input task is dead, so any
            # messages still in the queue will never be delivered.  Drain
            # them and reset the queue indicator to avoid "stuck queued".
            try:
                self.query_one(ConversationWidget).reset_turn_state()
            except LookupError:
                pass
            self._user_input_queue.clear()
            self._user_input_ready.clear()
            self._queue_count = 0
            try:
                self.query_one(ConversationWidget).update_queue_count(0)
            except LookupError:
                pass

    # ------------------------------------------------------------------
    # Mode transitions
    # ------------------------------------------------------------------

    def _push_pending_escalation(self) -> None:
        """Push the next queued escalation modal."""
        if not self._escalation_queue:
            return
        if self._has_screen(EscalationModal):
            return  # Already showing an escalation; next will fire on resolve
        path, classification, issue, options = self._escalation_queue.popleft()
        self.push_screen(
            EscalationModal(
                path=path,
                classification=classification,
                issue=issue,
                options=options,
            )
        )

    def transition_mode(self, target: Mode) -> bool:
        """Attempt a validated mode transition.

        Returns True if the transition is legal and mode was changed,
        False otherwise.
        """
        if get_transition(self.mode, target) is None:
            return False
        self.mode = target
        return True

    def watch_mode(self, old: Mode, new: Mode) -> None:
        """Swap CSS classes and perform atmospheric side-effects."""
        self.remove_class(old.name.lower())
        self.add_class(new.name.lower())
        self._apply_conversation_height_override(new)

        # --- DIALOGUE -> BREATH ---
        if old is Mode.DIALOGUE and new is Mode.BREATH:
            self._start_breathing()

        # --- BREATH -> DIALOGUE ---
        elif old is Mode.BREATH and new is Mode.DIALOGUE:
            self._stop_breathing()

        # --- BREATH -> DECISION ---
        elif old is Mode.BREATH and new is Mode.DECISION:
            self._pre_decision_mode = Mode.BREATH
            self._breath_machine.transition(BreathState.HOLDING)
            self._push_pending_escalation()

        # --- DIALOGUE -> DECISION ---
        elif old is Mode.DIALOGUE and new is Mode.DECISION:
            self._pre_decision_mode = Mode.DIALOGUE
            self._push_pending_escalation()

        # --- DIALOGUE -> HANDOFF ---
        elif old is Mode.DIALOGUE and new is Mode.HANDOFF:
            pass  # CSS class swap is sufficient.

        # --- DECISION -> BREATH ---
        elif old is Mode.DECISION and new is Mode.BREATH:
            self._breath_machine.transition(BreathState.BREATHING)
            # Start from peak (sin(π/2) = 1 → exp(1) = max) for smooth
            # transition from HOLDING.  Quarter period = peak of sin.
            self._animation_time = 4.5 * 0.25

        # --- DECISION -> DIALOGUE / DECISION -> HANDOFF / BREATH -> HANDOFF ---
        elif (
            (old is Mode.DECISION and new is Mode.DIALOGUE)
            or (old is Mode.DECISION and new is Mode.HANDOFF)
            or (old is Mode.BREATH and new is Mode.HANDOFF)
        ):
            self._stop_breathing()

        # --- HANDOFF -> DIALOGUE ---
        elif old is Mode.HANDOFF and new is Mode.DIALOGUE:
            pass  # Standard class swap is sufficient.

        # --- HANDOFF -> BREATH ---
        elif old is Mode.HANDOFF and new is Mode.BREATH:
            # Force-reset in case breath state is still RELEASING/SETTLING
            # from a prior _stop_breathing() — ensures IDLE→BREATHING is legal.
            self._force_stop_breathing()
            self._start_breathing()

        # --- HANDOFF -> DECISION ---
        elif old is Mode.HANDOFF and new is Mode.DECISION:
            self._pre_decision_mode = Mode.HANDOFF
            self._push_pending_escalation()

    def _apply_conversation_height_override(self, mode: Mode) -> None:
        """Apply or clear the user-dragged conversation height for *mode*.

        BREATH/DECISION split the screen — a dragged override is re-applied
        so the user's choice survives mode flips.  Any other mode reverts
        the conversation to its CSS defaults.
        """
        try:
            conv = self.query_one("#conversation")
        except LookupError:
            return
        if mode in (Mode.BREATH, Mode.DECISION):
            if self._conversation_override_height is not None:
                conv.styles.height = self._conversation_override_height
                conv.styles.max_height = self._conversation_override_height
        else:
            self._conversation_override_height = None
            conv.styles.height = None
            conv.styles.max_height = None

    # ------------------------------------------------------------------
    # Breathing animation
    # ------------------------------------------------------------------

    def _start_breathing(self) -> None:
        """Start the breathing animation timer."""
        if not self._breath_machine.transition(BreathState.BREATHING):
            return  # Already in RELEASING/SETTLING — don't reset timing.
        self._animation_time = 0.0
        if self._animation_timer is None:
            self._animation_timer = self.set_interval(
                _FRAME_DURATION, self._animation_tick
            )

    def _stop_breathing(self) -> None:
        """Begin the graceful breathing shutdown sequence.

        Instead of force-resetting to IDLE, transitions through
        RELEASING → SETTLING → IDLE so the animation fades out smoothly.
        """
        state = self._breath_machine.state
        if state in (BreathState.BREATHING, BreathState.HOLDING):
            # Capture the current breath value as the starting point for release decay.
            self._release_start_value = self._current_breath_value()
            self._release_start_time = self._animation_time
            self._breath_machine.transition(BreathState.RELEASING)
            # Keep timer running so RELEASING/SETTLING can animate.
            if self._animation_timer is None:
                self._animation_timer = self.set_interval(
                    _FRAME_DURATION, self._animation_tick
                )
        elif state in (BreathState.RELEASING, BreathState.SETTLING):
            # Already winding down — ensure timer is running.
            if self._animation_timer is None:
                self._animation_timer = self.set_interval(
                    _FRAME_DURATION, self._animation_tick
                )
        else:
            # IDLE — stop timer if it's somehow still running.
            if self._animation_timer is not None:
                self._animation_timer.stop()
                self._animation_timer = None

    def _force_stop_breathing(self) -> None:
        """Force-stop the animation timer and reset to IDLE (hard stop)."""
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None
        self._breath_machine.reset()

    def _current_breath_value(self) -> float:
        """Return the current breath luminance based on state."""
        state = self._breath_machine.state
        if state is BreathState.BREATHING:
            return BreathStateMachine.compute_breath(self._animation_time)
        if state is BreathState.HOLDING:
            return 1.0
        return 0.0

    def _animation_tick(self) -> None:
        """Advance the animation clock and update the breath widget."""
        self._animation_time += _FRAME_DURATION
        state = self._breath_machine.state

        if state is BreathState.BREATHING:
            # Wrap for the periodic waveform.
            wrapped = self._animation_time % 4.5
            breath_value = BreathStateMachine.compute_breath(wrapped)
        elif state is BreathState.HOLDING:
            breath_value = 1.0  # Peak luminance held.
        elif state is BreathState.RELEASING:
            # Decay from release_start_value toward 0 over releasing duration.
            releasing_s = TIMING["releasing"] / 1000.0
            elapsed = self._animation_time - self._release_start_time
            if elapsed >= releasing_s:
                # Releasing done — transition to SETTLING.
                breath_value = 0.0
                self._settle_start_time = self._animation_time
                self._breath_machine.transition(BreathState.SETTLING)
            else:
                t = elapsed / releasing_s
                breath_value = self._release_start_value * (1.0 - t)
        elif state is BreathState.SETTLING:
            # Settling phase: remain at 0 for the settle duration, then stop.
            settle_s = TIMING["settle"] / 1000.0
            elapsed = self._animation_time - self._settle_start_time
            breath_value = 0.0
            if elapsed >= settle_s:
                self._breath_machine.transition(BreathState.IDLE)
                if self._animation_timer is not None:
                    self._animation_timer.stop()
                    self._animation_timer = None
        else:
            breath_value = 0.0
        try:
            breath_widget = self.query_one(BreathWidget)
            breath_widget.breath_phase = breath_value
        except Exception:
            _log.debug("Breath widget not available", exc_info=True)
        try:
            task_graph = self.query_one(TaskGraphWidget)
            task_graph.breath_phase = breath_value
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_click(self) -> None:
        """Clicking anywhere in the app refocuses the input."""
        try:
            self.query_one("#user-input ChatInput").focus()
        except LookupError:
            pass

    def copy_to_clipboard(self, text: str, *, notify: bool = True) -> None:
        """Copy text to the system clipboard via platform tools.

        Textual's default uses OSC 52 which doesn't work on macOS
        Terminal.app.  We use pbcopy/xclip instead.
        """
        import subprocess
        import sys

        self._clipboard = text
        try:
            if sys.platform == "darwin":
                subprocess.run(
                    ["pbcopy"], input=text.encode("utf-8"),
                    check=True, timeout=3,
                )
            else:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"),
                    check=True, timeout=3,
                )
        except (
            subprocess.CalledProcessError, FileNotFoundError,
            OSError, subprocess.TimeoutExpired,
        ):
            super().copy_to_clipboard(text)
            return
        if notify:
            self.notify("Copied", timeout=1.5)

    def on_text_selected(self) -> None:
        """Copy-on-select — drag selection goes straight to the clipboard.

        Terminal emulators intercept Cmd+C before TUI apps see it.
        Rather than fighting the terminal, we copy the moment the
        user finishes dragging.  Cmd+V just works.
        """
        try:
            text = self.screen.get_selected_text()
        except (IndexError, TypeError):
            # Textual Selection.extract() can crash when the selection
            # has end=None (incomplete drag) or when start_line exceeds
            # the actual line count of a single-line widget.
            return
        if text:
            self.copy_to_clipboard(text, notify=False)

    def on_key(self, event) -> None:
        """Route navigation keys to the command palette when visible."""
        from clou.ui.widgets.command_palette import CommandPalette

        try:
            palette = self.query_one(CommandPalette)
        except LookupError:
            return
        if not palette.is_visible:
            return

        if event.key == "up":
            palette.navigate(-1)
            event.stop()
        elif event.key == "down":
            palette.navigate(1)
            event.stop()
        elif event.key == "escape":
            if not palette.back():
                palette.hide()
                try:
                    self.query_one("#user-input ChatInput").clear()
                except LookupError:
                    pass
            event.stop()

    def on_chat_input_recall_requested(self, event: ChatInput.RecallRequested) -> None:
        """Up-arrow with empty input — recall the last queued message for editing."""
        if not self._user_input_queue:
            return
        # Pop the most recent queued message.
        text = self._user_input_queue.pop()
        if not self._user_input_queue:
            self._user_input_ready.clear()
        self._queue_count = max(0, self._queue_count - 1)

        # Remove the corresponding UserMessage from the conversation.
        conversation = self.query_one(ConversationWidget)
        conversation.recall_last_queued()
        conversation.update_queue_count(
            self._queue_count, breath_mode=(self.mode is Mode.BREATH),
        )

        # Restore text to the input.
        try:
            chat_input = self.query_one("#user-input ChatInput")
            chat_input.text = text
            # Move cursor to end of restored text.
            lines = text.split("\n")
            chat_input.move_cursor((len(lines) - 1, len(lines[-1])))
        except LookupError:
            pass

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """Handle user input — transition from BREATH to DIALOGUE if needed."""
        text = event.value.strip()

        if not text:
            event.input.clear()
            return

        # Slash command dispatch — intercept before supervisor queue.
        if text.startswith("/"):
            from clou.ui.widgets.command_palette import CommandPalette

            try:
                palette = self.query_one(CommandPalette)
            except LookupError:
                palette = None

            # Palette-driven selection when no args are typed.
            if palette and palette.is_visible:
                parts = text[1:].split(None, 1)
                has_args = len(parts) > 1 and parts[1].strip()
                if not has_args:
                    result = palette.select()
                    if result is None:
                        # Submenu opened — keep input intact.
                        return
                    event.input.clear()
                    palette.hide()
                    self._dispatch_command(result)
                    return

            event.input.clear()
            if palette:
                palette.hide()
            self._dispatch_command(text)
            return

        event.input.clear()

        # In handoff mode, return to dialogue so the supervisor can respond.
        # In breath mode, stay — the coordinator is running and the supervisor
        # is blocked.  The message queues until the coordinator completes.
        if self.mode is Mode.HANDOFF:
            self.transition_mode(Mode.DIALOGUE)

        in_breath = self.mode is Mode.BREATH

        # Show user message as queued — it transitions to active when the
        # supervisor picks it up (ClouProcessingStarted).
        conversation = self.query_one(ConversationWidget)
        self._queue_count += 1
        conversation.add_user_message(text, queued=True)

        # Queue for model processing.
        self._user_input_queue.append(text)
        self._user_input_ready.set()
        conversation.update_queue_count(self._queue_count, breath_mode=in_breath)

        # Update placeholder — during BREATH, hint that the message is queued
        # and /stop is available.  Otherwise reset from any prior ask_user state.
        try:
            chat_input = self.query_one("#user-input ChatInput")
            if in_breath:
                chat_input.placeholder = "Message queued \u00b7 /stop to halt coordinator"
            else:
                chat_input.placeholder = "Talk to clou..."
        except LookupError:
            pass

    @work(exclusive=False)
    async def _dispatch_command(self, text: str) -> None:
        """Run slash command dispatch as an async worker."""
        from clou.ui.commands import dispatch

        await dispatch(self, text)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Show/hide command palette as the user types."""
        from clou.ui.widgets.command_palette import CommandPalette

        try:
            palette = self.query_one(CommandPalette)
        except LookupError:
            return

        text = event.text_area.text.strip()
        if text.startswith("/") and len(text) > 0:
            prefix = text[1:].split(None, 1)[0].lower() if len(text) > 1 else ""
            palette.update_filter(prefix)
        else:
            palette.hide()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _has_screen(self, screen_type: type) -> bool:
        """Check if a screen of the given type is already on the stack."""
        return any(isinstance(s, screen_type) for s in self.screen_stack)

    def action_noop(self) -> None:
        """Intentional no-op — absorbs SkipAction from ctrl+c copy."""

    def action_clear(self) -> None:
        """Clear the conversation history and any active stream."""
        conversation = self.query_one(ConversationWidget)
        conversation.reset_turn_state()
        # Remove all message widgets, keep #tail.
        for widget in conversation.query(".msg"):
            widget.remove()
        try:
            conversation._clear_tail()
        except LookupError:
            pass

    def resume_session(self, session_id: str) -> None:
        """Resume a previous session within the running TUI.

        Clears all UI state, sets the resume ID, creates a new persistence
        session, and restarts the supervisor worker.  The orchestrator reads
        ``_resume_session_id`` at startup and injects resumption context.
        """
        # Set the resume ID — orchestrator reads this via getattr.
        self._resume_session_id = session_id

        # Clear conversation UI.
        self.action_clear()
        self._conversation_history = []

        # New persistence session (the old transcript is read-only).
        self._session = Session(self._project_dir, model=self._model)
        telemetry.init(self._session.session_id, self._project_dir)

        # Reset status bar metrics.
        bar = self.query_one(ClouStatusBar)
        bar.input_tokens = 0
        bar.output_tokens = 0
        bar.cost_usd = 0.0
        bar.milestone = ""
        bar.cycle_type = ""
        bar.cycle_num = 0
        bar.phase = ""
        bar.rate_limited = False

        # Reset app-level state.
        self._session_start_time = time.monotonic()
        self._compact.reset()
        self._escalation_queue.clear()
        self._dag_tasks = []
        self._dag_deps = {}
        self._task_graph_model = None
        self._synthetic_dag = False
        self._agent_task_map = {}
        # Drain stale input from the previous session's queue.
        self._user_input_queue.clear()
        self._user_input_ready.clear()
        self._queue_count = 0

        # Return to dialogue mode.
        if self.mode is not Mode.DIALOGUE:
            self._force_stop_breathing()
            self.mode = Mode.DIALOGUE

        # Re-enter initializing state so the wake indicator shows during resume.
        try:
            conv = self.query_one(ConversationWidget)
            conv._initializing = True
            conv.add_class("initializing")
        except LookupError:
            pass

        # Restart the supervisor — exclusive=True cancels the current worker.
        self.run_supervisor_worker()

    def action_show_context(self) -> None:
        """Push the golden context tree screen."""
        if not self._has_screen(ContextScreen):
            self.push_screen(ContextScreen(self._project_dir))

    def action_show_dag(self) -> None:
        """Push the DAG viewer (only in breath/handoff mode)."""
        if self.mode in (Mode.BREATH, Mode.HANDOFF) and not self._has_screen(DagScreen):
            bar = self.query_one(ClouStatusBar)
            self.push_screen(
                DagScreen(
                    milestone=bar.milestone,
                    tasks=self._dag_tasks,
                    deps=self._dag_deps,
                )
            )

    def action_show_costs(self) -> None:
        """Push the cost/token detail screen."""
        if not self._has_screen(DetailScreen):
            content = self._format_costs()
            self.push_screen(DetailScreen(title="Token Usage", content=content))

    def _format_costs(self) -> str:
        """Format current token/cost metrics for the detail screen."""
        bar = self.query_one(ClouStatusBar)
        return (
            f"Input tokens:  {bar.input_tokens:,}\n"
            f"Output tokens: {bar.output_tokens:,}\n"
            f"Cost:          ${bar.cost_usd:.2f}"
        )

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_clou_status_update(self, msg: ClouStatusUpdate) -> None:
        """Update status bar with coordinator cycle status."""
        bar = self.query_one(ClouStatusBar)
        bar.cycle_type = msg.cycle_type
        bar.cycle_num = msg.cycle_num
        bar.phase = msg.phase

    def on_clou_metrics(self, msg: ClouMetrics) -> None:
        """Update status bar token/cost metrics."""
        bar = self.query_one(ClouStatusBar)
        bar.input_tokens += msg.input_tokens
        bar.output_tokens += msg.output_tokens
        if msg.cost_usd is not None:
            bar.cost_usd += msg.cost_usd

    def on_clou_turn_complete(self, msg: ClouTurnComplete) -> None:
        """Update status bar with turn metrics."""
        bar = self.query_one(ClouStatusBar)
        bar.input_tokens += msg.input_tokens
        bar.output_tokens += msg.output_tokens
        if msg.cost_usd is not None:
            bar.cost_usd += msg.cost_usd

    def on_clou_turn_content_ready(self, msg: ClouTurnContentReady) -> None:
        """Persist completed assistant content to history and session."""
        self._conversation_history.append(
            ConversationEntry(role="assistant", content=msg.content)
        )
        if self._session is not None:
            self._session.append("assistant", msg.content)

    def on_clou_processing_started(self, msg: ClouProcessingStarted) -> None:
        """Message picked up by model — record in history, update queue count."""
        self._conversation_history.append(
            ConversationEntry(role="user", content=msg.text)
        )
        if self._session is not None:
            self._session.append("user", msg.text)
        self._queue_count = max(0, self._queue_count - 1)
        try:
            conv = self.query_one(ConversationWidget)
            conv.update_queue_count(self._queue_count)
        except LookupError:
            pass

    def on_clou_ask_user(self, msg: ClouAskUser) -> None:
        """Gate opened — update placeholder so the user knows a response is expected."""
        try:
            chat_input = self.query_one("#user-input ChatInput")
            if msg.choices:
                chat_input.placeholder = "Pick a number or type your answer..."
            else:
                chat_input.placeholder = "Type your answer..."
            chat_input.focus()
        except LookupError:
            pass

    def on_clou_coordinator_spawned(self, msg: ClouCoordinatorSpawned) -> None:
        """Coordinator started — enter breath mode."""
        bar = self.query_one(ClouStatusBar)
        bar.milestone = msg.milestone
        self._dag_tasks = []
        self._dag_deps = {}
        self._task_graph_model = None
        self._synthetic_dag = False
        self._agent_task_map = {}
        try:
            self.query_one(TaskGraphWidget).reset()
        except LookupError:
            pass
        self.transition_mode(Mode.BREATH)

    def on_clou_dag_update(self, msg: ClouDagUpdate) -> None:
        """Store updated DAG data and push to live task graph."""
        self._dag_tasks = msg.tasks
        self._dag_deps = msg.deps
        if msg.tasks:
            was_synthetic = self._synthetic_dag
            self._task_graph_model = TaskGraphModel(msg.tasks, msg.deps)
            self._synthetic_dag = False
            try:
                widget = self.query_one(TaskGraphWidget)
                if was_synthetic:
                    # Synthetic-phase agents are done — stale spawns.
                    widget._pending_spawns.clear()
                else:
                    # Spawns buffered without a synthetic model — replay.
                    for task_id, description in widget._pending_spawns:
                        self._activate_agent(task_id, description)
                    widget._pending_spawns.clear()
                widget.update_model(self._task_graph_model)
            except LookupError:
                pass

    def _activate_agent(self, task_id: str, description: str) -> None:
        """Match an agent to a task and activate it, or track as unmapped."""
        model = self._task_graph_model
        if model is None:
            return
        task_name = model.match_agent(description)
        if task_name:
            self._agent_task_map[task_id] = task_name
            model.activate_task(task_name, task_id)
        else:
            # Use description as display name; store in agent map for
            # progress/completion lookup.
            desc = description.strip()[:60] or "agent"
            self._agent_task_map[task_id] = desc
            model.unmapped_agents[desc] = TaskState(
                status="active", agent_id=task_id,
            )

    def on_clou_agent_spawned(self, msg: ClouAgentSpawned) -> None:
        """Agent dispatched — match to task graph and activate."""
        if self._task_graph_model is None or self._synthetic_dag:
            # DAG hasn't arrived yet — buffer for later replay and build
            # a synthetic flat model so the task graph is visible now.
            try:
                widget = self.query_one(TaskGraphWidget)
                widget._pending_spawns.append((msg.task_id, msg.description))
            except LookupError:
                pass
            self._grow_synthetic_model(msg.task_id, msg.description)
            return
        self._activate_agent(msg.task_id, msg.description)
        try:
            self.query_one(TaskGraphWidget).update_model(
                self._task_graph_model
            )
        except LookupError:
            pass

    def _grow_synthetic_model(self, task_id: str, description: str) -> None:
        """Create or grow a flat task-graph model from pre-DAG agent spawns.

        Gives the task graph widget visible, breathing content even before
        compose.py exists (e.g. during PLAN #1).
        """
        desc = description.strip()[:60] or "agent"
        if self._task_graph_model is None:
            self._task_graph_model = TaskGraphModel(
                tasks=[{"name": desc}], deps={},
            )
            self._synthetic_dag = True
        elif desc not in self._task_graph_model.task_states:
            # Rebuild with the new task, preserving existing states.
            existing = list(self._task_graph_model.task_states.keys())
            existing.append(desc)
            old_states = dict(self._task_graph_model.task_states)
            self._task_graph_model = TaskGraphModel(
                tasks=[{"name": n} for n in existing], deps={},
            )
            for name, state in old_states.items():
                if name in self._task_graph_model.task_states:
                    self._task_graph_model.task_states[name] = state

        self._agent_task_map[task_id] = desc
        self._task_graph_model.activate_task(desc, task_id)
        try:
            self.query_one(TaskGraphWidget).update_model(
                self._task_graph_model
            )
        except LookupError:
            pass

    def on_clou_agent_progress(self, msg: ClouAgentProgress) -> None:
        """Agent mid-flight — update tool progress in task graph."""
        model = self._task_graph_model
        if model is None:
            return
        task_name = self._agent_task_map.get(msg.task_id)
        if not task_name:
            return
        # Look up in mapped tasks first, then unmapped agents.
        state = model.task_states.get(task_name)
        if state is None:
            state = model.unmapped_agents.get(task_name)
        if state is None:
            return
        if msg.tool_uses > state.tool_count and msg.last_tool:
            state.tool_calls.append((msg.last_tool, ""))
        state.tool_count = msg.tool_uses
        state.last_tool = msg.last_tool
        try:
            widget = self.query_one(TaskGraphWidget)
            has_active = any(
                ts.status == "active"
                for ts in list(model.task_states.values())
                + list(model.unmapped_agents.values())
            )
            widget.shimmer_active = has_active
            widget.refresh()
        except LookupError:
            pass

    def on_clou_agent_complete(self, msg: ClouAgentComplete) -> None:
        """Agent finished — mark task complete/failed in graph."""
        model = self._task_graph_model
        if model is None:
            return
        task_name = self._agent_task_map.pop(msg.task_id, None)
        if not task_name:
            return
        summary = msg.summary or ""
        if task_name in model.task_states:
            model.complete_task(task_name, msg.status, summary)
        elif task_name in model.unmapped_agents:
            state = model.unmapped_agents[task_name]
            state.status = msg.status
            state.summary = summary
        try:
            self.query_one(TaskGraphWidget).update_model(model)
        except LookupError:
            pass

    def on_clou_escalation_arrived(self, msg: ClouEscalationArrived) -> None:
        """Escalation arrived — queue and enter decision mode."""
        self._escalation_queue.append((
            msg.path,
            msg.classification,
            msg.issue,
            msg.options,
        ))
        # Already in DECISION — the queued escalation fires when the current one resolves.
        if self.mode is Mode.DECISION:
            return
        if not self.transition_mode(Mode.DECISION):
            _log.warning(
                "Cannot transition to DECISION from %s — escalation queued for later",
                self.mode,
            )

    def on_clou_escalation_resolved(self, msg: ClouEscalationResolved) -> None:
        """Escalation resolved — show next queued or return to pre-decision mode."""
        if self._escalation_queue:
            # More escalations waiting — push the next one immediately.
            self._push_pending_escalation()
            return
        target = (
            self._pre_decision_mode
            if self._pre_decision_mode != Mode.DECISION
            else Mode.DIALOGUE
        )
        self.transition_mode(target)

    def on_clou_rate_limit(self, msg: ClouRateLimit) -> None:
        """Update status bar rate-limit state."""
        bar = self.query_one(ClouStatusBar)
        # Only active/limited statuses turn on the indicator; anything else clears it.
        bar.rate_limited = msg.status in {"active", "rate_limited", "limited"}

    def on_clou_coordinator_complete(self, msg: ClouCoordinatorComplete) -> None:
        """Coordinator finished — enter handoff or return to dialogue."""
        if msg.result == "completed":
            self.transition_mode(Mode.HANDOFF)
        else:
            self.transition_mode(Mode.DIALOGUE)
        bar = self.query_one(ClouStatusBar)
        bar.cycle_type = ""
        bar.cycle_num = 0
        bar.phase = ""
        self._dag_tasks = []
        self._dag_deps = {}
        self._task_graph_model = None
        self._synthetic_dag = False
        self._agent_task_map = {}

        # Clean up stale conversation state from the coordinator period
        # and recover scroll position.  overflow-y: hidden during BREATH
        # swallows scroll_end() calls, so we must defer until the CSS
        # reflow restores overflow-y: auto.
        try:
            conv = self.query_one(ConversationWidget)
            conv._stop_working()
            conv._clear_tail()
            conv._stop_timer()
            # Refresh queue indicator without breath_mode flag so
            # coordinator-specific text clears.
            conv.update_queue_count(self._queue_count)
        except LookupError:
            pass

        # Reset placeholder from breath-mode hint.
        try:
            self.query_one("#user-input ChatInput").placeholder = "Talk to clou..."
        except LookupError:
            pass

        def _recover_scroll() -> None:
            from textual.containers import VerticalScroll

            try:
                history = self.query_one(
                    ConversationWidget
                ).query_one("#history", VerticalScroll)
                history.scroll_end(animate=False)
            except LookupError:
                pass

        self.call_after_refresh(_recover_scroll)

    def on_clou_handoff(self, msg: ClouHandoff) -> None:
        """Handoff ready — load content into the handoff widget."""
        if self.mode is not Mode.HANDOFF:
            self.transition_mode(Mode.HANDOFF)
        resolved = msg.handoff_path.resolve()
        clou_dir = (self._project_dir / ".clou").resolve()
        if not resolved.is_relative_to(clou_dir):
            _log.warning(
                "Refusing handoff read: path %s is not under %s", resolved, clou_dir
            )
            content = (
                f"# Handoff: {_strip_ansi(msg.milestone)}\n\n(Invalid handoff path)"
            )
        else:
            try:
                content = _strip_ansi(resolved.read_text(encoding="utf-8"))
            except OSError:
                milestone_clean = _strip_ansi(msg.milestone)
                content = (
                    f"# Handoff: {milestone_clean}\n\n(Could not read handoff file)"
                )
        handoff_widget = self.query_one(HandoffWidget)
        handoff_widget.update_content(content)
