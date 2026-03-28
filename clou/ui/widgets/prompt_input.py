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


class _Input(Input):
    """Input with cursor-offset fix.

    Textual's ``_cursor_offset`` adds +1 when ``cursor_at_end`` is True,
    placing the terminal hardware cursor one cell *after* the styled cursor
    rendered in ``render_line``.  When the value is empty the styled cursor
    sits on the first placeholder character (cell 0) while the hardware
    cursor lands on cell 1 — a phantom duplicate.  Override removes the +1
    so both cursors coincide.
    """

    @property
    def _cursor_offset(self) -> int:  # type: ignore[override]
        return self._position_to_cell(self.cursor_position)


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
        yield _Input(placeholder="")

    def set_ready(self) -> None:
        """The system is present — show the invitation."""
        try:
            self.query_one(Input).placeholder = "Talk to clou..."
        except LookupError:
            pass
