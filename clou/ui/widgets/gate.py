"""GateWidget — live question surface above the input.

When the supervisor calls ``ask_user``, the question and numbered choices
surface here as a dedicated focal zone sitting just above the chat input.
This creates a consistent place for every question — users learn that
questions always appear here, forming a tight question→answer spatial
coupling with the input below.

The widget is mode-independent: it shows whenever a gate is open,
regardless of whether the app is in DIALOGUE, HANDOFF, or BREATH. The
question's context (handoff content, conversation history, task graph)
remains visible in whatever mode is active; the gate is layered on top
as an ambient presence above the input.

Interaction: the user answers by typing a number (resolved to the
corresponding choice label before being sent to the model) or free
text. On submission the widget clears and the Q+A exchange commits
to conversation history.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from clou.ui.theme import PALETTE

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_TEXT_HEX = PALETTE["text"].to_hex()
_TEXT_BRIGHT_HEX = PALETTE["text-bright"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()


class GateWidget(Vertical):
    """Presents a live question with numbered choices, above the input."""

    DEFAULT_CSS = f"""
    GateWidget {{
        display: none;
        height: auto;
        background: {_SURFACE_RAISED_HEX};
        border-top: hkey {_GOLD_HEX};
        padding: 1 2 1 2;
    }}
    GateWidget.active {{
        display: block;
    }}
    GateWidget #gate-question {{
        height: auto;
        margin: 0 0 1 0;
    }}
    GateWidget #gate-choices {{
        height: auto;
    }}
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._question: str = ""
        self._choices: list[str] | None = None
        self._active: bool = False

    def compose(self) -> ComposeResult:
        yield Static("", id="gate-question")
        yield Static("", id="gate-choices")

    # -- State -----------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True while a gate is waiting for the user's response."""
        return self._active

    @property
    def question(self) -> str:
        """The question text currently displayed, or '' if inactive."""
        return self._question

    @property
    def choices(self) -> list[str] | None:
        """The choices currently displayed, or None if inactive/open-ended."""
        return self._choices

    # -- Lifecycle -------------------------------------------------------

    def show(self, question: str, choices: list[str] | None) -> None:
        """Surface a new question + choices as the live gate.

        Idempotent across successive opens — each call replaces the
        currently displayed question.
        """
        self._question = question
        self._choices = choices
        self._active = True
        self._refresh_render()
        self.add_class("active")

    def hide(self) -> None:
        """Clear the gate after the user has answered."""
        self._active = False
        self._question = ""
        self._choices = None
        self._refresh_render()
        self.remove_class("active")

    # -- Input resolution ------------------------------------------------

    def resolve_input(self, text: str) -> str:
        """Resolve a bare numeric answer to its choice label.

        When the user types a number matching a displayed choice, the
        resolved label is what gets sent to the model — no ambiguity
        about which option "2" meant. Any non-numeric or out-of-range
        input passes through as free text.
        """
        if not self._choices:
            return text
        stripped = text.strip()
        try:
            n = int(stripped)
        except ValueError:
            return text
        if 1 <= n <= len(self._choices):
            return self._choices[n - 1]
        return text

    # -- Rendering -------------------------------------------------------

    def _refresh_render(self) -> None:
        """Update the internal Static widgets with current state."""
        try:
            q_static = self.query_one("#gate-question", Static)
            c_static = self.query_one("#gate-choices", Static)
        except Exception:
            # Not mounted yet — on_mount will call us again.
            return
        q_static.update(self._render_question())
        c_static.update(self._render_choices())

    def _render_question(self) -> Text:
        if not self._question:
            return Text("")
        return Text(self._question, style=f"bold {_TEXT_BRIGHT_HEX}")

    def _render_choices(self) -> Text:
        if not self._choices:
            return Text("")
        t = Text()
        total = len(self._choices)
        for i, choice in enumerate(self._choices, 1):
            t.append(f"  {i}  ", style=f"bold {_GOLD_HEX}")
            t.append(choice, style=_TEXT_HEX)
            if i < total:
                t.append("\n")
        return t

    def on_mount(self) -> None:
        """Initial render — state may have been set before mount completed."""
        self._refresh_render()
        if self._active:
            self.add_class("active")
