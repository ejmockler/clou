"""PromptInput — multiline input with gold prompt character.

Replaces the bordered Input box with a Codex-style prompt character (›)
on a continuous surface. Typography does hierarchy, not borders.

Enter submits the message. Shift+Enter inserts a newline.
Multiline paste is preserved. Large pastes are collapsed to a
single-line marker with the full text stored for submission.

Starts hidden (CSS: display none) while the WakeIndicator runs.
``set_ready()`` is called when the supervisor's greeting arrives —
the felt moment when clou becomes present.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static, TextArea


class ChatInput(TextArea):
    """Multiline input with Enter-to-submit and paste block collapse."""

    # -- Custom message mirroring Input.Submitted shape ------------------

    @dataclass
    class Submitted(Message):
        """Posted when the user presses Enter to send."""

        input: ChatInput
        value: str

        @property
        def control(self) -> ChatInput:
            return self.input

    class RecallRequested(Message):
        """Posted when the user presses Up with an empty input."""

    # -- Compat shim so tests using .value / .action_submit() work -------

    @property
    def value(self) -> str:  # noqa: D401
        """Alias for .text — test compatibility with Input widget."""
        return self.text

    @value.setter
    def value(self, v: str) -> None:
        self.text = v

    # -- Init ------------------------------------------------------------

    _MAX_LINES = 8
    _MARKER_LEFT = "\u27ea"   # ⟪
    _MARKER_RIGHT = "\u27eb"  # ⟫

    def __init__(self, *, placeholder: str = "", **kwargs: object) -> None:
        super().__init__(
            "",
            compact=True,
            show_line_numbers=False,
            language=None,
            soft_wrap=True,
            tab_behavior="focus",
            theme="css",
            **kwargs,
        )
        self.placeholder = placeholder
        self._paste_buffer: str | None = None
        self._paste_marker: str = ""

    # -- Enter → submit, Shift+Enter → newline ----------------------------

    async def _on_key(self, event: events.Key) -> None:
        key = event.key

        if key == "shift+enter":
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return

        if key == "enter":
            event.stop()
            event.prevent_default()
            await self.action_submit()
            return

        if key == "up" and not self.text.strip():
            event.stop()
            event.prevent_default()
            self.post_message(self.RecallRequested())
            return

        # If paste buffer is active and user types/deletes, clear it.
        if self._paste_buffer is not None:
            if event.is_printable or key in (
                "backspace", "delete",
                "ctrl+w", "ctrl+f", "ctrl+u", "ctrl+k",
                "ctrl+shift+k",
            ):
                event.stop()
                event.prevent_default()
                self._clear_paste_buffer()
                # Re-insert the typed character (if printable, not deletion).
                if event.is_printable and event.character:
                    self._replace_via_keyboard(
                        event.character, *self.selection,
                    )
                return

        await super()._on_key(event)

    # -- Paste collapse --------------------------------------------------

    def _replace_via_keyboard(self, insert, start, end):  # type: ignore[override]
        """Intercept keyboard inserts to detect large pastes."""
        if "\n" in insert:
            lines = insert.split("\n")
            try:
                threshold = max(self.screen.size.height // 3, 8)
            except Exception:
                threshold = 8
            if len(lines) > threshold:
                self._paste_buffer = insert
                n = len(lines)
                marker = (
                    f"{self._MARKER_LEFT} {n} lines pasted {self._MARKER_RIGHT}"
                )
                self._paste_marker = marker
                # Clear existing content and insert marker.
                self.clear()
                return super()._replace_via_keyboard(
                    marker, (0, 0), (0, 0),
                )
        return super()._replace_via_keyboard(insert, start, end)

    def _clear_paste_buffer(self) -> None:
        """Discard the collapsed paste block and clear the input."""
        self._paste_buffer = None
        self._paste_marker = ""
        self.clear()

    # -- Submit ----------------------------------------------------------

    async def action_submit(self) -> None:
        """Send the input contents (expanding any paste block)."""
        if self._paste_buffer is not None:
            # Expand the collapsed block: anything the user typed around
            # the marker gets the marker replaced with the full text.
            value = self.text.replace(self._paste_marker, self._paste_buffer)
            self._paste_buffer = None
            self._paste_marker = ""
        else:
            value = self.text
        self.post_message(self.Submitted(input=self, value=value))

    # -- Auto-grow -------------------------------------------------------

    def _auto_resize(self) -> None:
        """Adjust height to fit content, clamped to [1, _MAX_LINES]."""
        try:
            visual = self.wrapped_document.height
        except Exception:
            visual = 1
        target = max(1, min(visual, self._MAX_LINES))
        self.styles.height = target

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._auto_resize()

    def on_resize(self, _event: events.Resize) -> None:
        self._auto_resize()

    def on_mount(self) -> None:
        self._auto_resize()


class PromptInput(Horizontal):
    """Gold › prompt character followed by a borderless input field."""

    DEFAULT_CSS = """
    PromptInput {
        height: auto;
        max-height: 10;
        background: transparent;
    }
    PromptInput > .prompt-char {
        width: 2;
        height: 1;
        background: transparent;
    }
    PromptInput > ChatInput {
        width: 1fr;
        height: auto;
        background: transparent;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u203a ", classes="prompt-char")
        yield ChatInput(placeholder="")

    def set_ready(self) -> None:
        """The system is present — show the invitation."""
        try:
            self.query_one(ChatInput).placeholder = "Talk to clou..."
        except LookupError:
            pass
