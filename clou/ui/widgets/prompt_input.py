"""PromptInput — borderless input with gold prompt character.

Replaces the bordered Input box with a Codex-style prompt character (›)
on a continuous surface. Typography does hierarchy, not borders.

Starts hidden (CSS: display none) while the WakeIndicator runs.
``set_ready()`` is called when the supervisor's greeting arrives —
the felt moment when clou becomes present.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static


class PromptInput(Horizontal):
    """Gold › prompt character followed by a borderless input field."""

    DEFAULT_CSS = """
    PromptInput {
        height: auto;
        max-height: 5;
        background: transparent;
    }
    PromptInput > .prompt-char {
        width: 2;
        height: 1;
        background: transparent;
    }
    PromptInput > Input {
        width: 1fr;
        height: auto;
        background: transparent;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("› ", classes="prompt-char")
        yield Input(placeholder="")

    def set_ready(self) -> None:
        """The system is present — show the invitation."""
        try:
            self.query_one(Input).placeholder = "Talk to clou..."
        except LookupError:
            pass
