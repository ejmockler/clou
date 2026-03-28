"""Conversation surface — scrollable message widgets with animated tail.

Messages are mounted as individual ``Static`` widgets inside a
``VerticalScroll`` container.  The *tail* widget stays at the bottom
and shows the streaming Markdown preview; when a turn completes the
content is promoted to a permanent ``_MarkdownMessage`` and the tail
clears.  A separate ``_WorkingPulse`` widget renders a breathing teal
line at 24 FPS between the history and input to signal processing.

Rendering is debounced: stream chunks accumulate in ``_stream_buffer``
and the tail is only re-rendered at most every 100 ms.
"""

from __future__ import annotations

import math
import time
from pathlib import PurePosixPath

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from clou.ui.bridge import _shorten_url, _strip_ansi
from clou.ui.mixins.drag_scroll import DragScrollMixin
from clou.ui.diff import (
    compute_edit_stats,
    compute_multi_edit_stats,
    render_inline_diff,
)
from clou.ui.messages import (
    ClouProcessingStarted,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
    ClouTurnContentReady,
)
from clou.ui.theme import PALETTE, OklchColor
from clou.ui.widgets.prompt_input import PromptInput
from clou.ui.widgets.wake import WakeIndicator

_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_GOLD_DIM_HEX = PALETTE["accent-gold"].dim().to_hex()
_GREEN_HEX = PALETTE["accent-green"].to_hex()
_GREEN_DIM_HEX = PALETTE["accent-green"].dim().to_hex()
_TEAL_HEX = PALETTE["accent-teal"].to_hex()
_TEAL_DIM_HEX = PALETTE["accent-teal"].dim().to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_BLUE_DIM_HEX = PALETTE["accent-blue"].dim().to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_SURFACE_HEX = PALETTE["surface"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()
_BORDER_HEX = PALETTE["border"].to_hex()
_BORDER_SUBTLE_HEX = PALETTE["border-subtle"].to_hex()
_STREAM_FLUSH_INTERVAL: float = 0.1  # seconds
_MAX_STREAM_BUFFER: int = 500_000  # ~500KB — cap to prevent OOM on very long streaming turns
# LRU-ish cache for rendered Markdown.  Keyed on (source, width) so resize
# only re-renders messages whose available width actually changed.
_md_cache: dict[tuple[str, int], Text] = {}
_MD_CACHE_MAX = 200  # ~200 messages × avg 2KB = manageable


def _md_to_text(source: str, width: int) -> Text:
    """Render Markdown to a styled Text — preserves formatting, enables selection.

    Results are cached by (source, width) so that terminal resize only
    re-renders messages whose available width actually changed.
    """
    key = (source, width)
    cached = _md_cache.get(key)
    if cached is not None:
        # Promote to back of dict for LRU-style eviction.
        _md_cache[key] = cached
        return cached
    console = Console(width=width, force_terminal=True, highlight=False)
    with console.capture() as capture:
        console.print(Markdown(source), end="")
    result = Text.from_ansi(capture.get())
    _md_cache[key] = result
    # Evict oldest entries when cache grows too large.
    if len(_md_cache) > _MD_CACHE_MAX:
        # Remove the oldest ~quarter of entries.
        for evict_key in list(_md_cache)[:_MD_CACHE_MAX // 4]:
            _md_cache.pop(evict_key, None)
    return result


class _MarkdownMessage(Static):
    """Assistant message — the system's voice.

    Left teal edge identifies the speaker in peripheral vision.
    Re-renders Markdown on resize so text wrapping stays correct.
    Background lifts slightly off surface-deep to create figure/ground
    separation from tool activity substrate.
    """

    DEFAULT_CSS = f"""
    _MarkdownMessage {{
        border-left: tall {_TEAL_DIM_HEX};
        background: {_SURFACE_HEX};
        padding: 1 1 1 2;
        margin: 1 0 0 0;
    }}
    """

    def __init__(self, source: str, *, classes: str = "") -> None:
        super().__init__("", classes=classes)
        self._source = source

    def render(self) -> Text:
        parent = self.parent
        if parent is not None:
            # Account for border + padding in width calculation
            w = parent.content_size.width or parent.size.width
            w = max(40, (w or 80) - 4)  # border(1) + padding(2+1)
        else:
            w = 76
        return _md_to_text(self._source, w)



class _UserMessage(Static):
    """User message — the human's voice.

    Gold left edge mirrors the assistant's teal edge, creating a
    scannable dialogue rhythm: gold-teal-gold-teal in peripheral vision.
    Raised background distinguishes intent from response.
    """

    DEFAULT_CSS = f"""
    _UserMessage {{
        width: 100%;
        border-left: tall {_GOLD_DIM_HEX};
        background: {_SURFACE_RAISED_HEX};
        padding: 1 1 1 2;
        margin: 1 0 0 0;
    }}
    """

    def __init__(self, text: str, *, queued: bool = False, classes: str = "") -> None:
        super().__init__("", classes=classes)
        self._text = text
        self._queued = queued

    def mark_active(self) -> None:
        """Transition from queued to active — model picked up the message."""
        if self._queued:
            self._queued = False
            self.refresh()

    def render(self) -> Text:
        if self._queued:
            result = Text(f"\u203a {self._text}", style=f"bold {_GOLD_DIM_HEX}")
            result.append("  queued", style=f"italic {_DIM_HEX}")
            return result
        return Text(f"\u203a {self._text}", style=f"bold {_GOLD_HEX}")


# ── Lifecycle thresholds (parallel to breath.py) ────────────────────
_DISCLOSURE_SETTLE: float = 4.0   # seconds before collapse
_DISCLOSURE_PRUNE: float = 30.0   # seconds after collapse before DOM removal


class _EditDisclosure(Static):
    """Edit diff disclosure — compact summary + expandable diff body.

    Green left edge identifies edits in peripheral vision (parallel to
    teal for assistant, gold for user).
    """

    DEFAULT_CSS = f"""
    _EditDisclosure {{
        border-left: tall {_GREEN_DIM_HEX};
        padding: 0 0 0 2;
        margin: 0;
    }}
    """

    def __init__(
        self,
        summary: Text,
        diff_body: Text | None = None,
        *,
        classes: str = "",
    ) -> None:
        super().__init__("", classes=classes)
        self._summary = summary
        self._diff_body = diff_body
        self._expanded: bool = True
        self._pinned: bool = False
        self._birth: float = time.monotonic()
        self._collapsed_at: float = 0.0  # set when collapsed

    def render(self) -> Text:
        result = Text()
        result.append_text(self._summary)
        if self._expanded and self._diff_body and self._diff_body.plain:
            result.append("\n")
            result.append_text(self._diff_body)
        return result

    def update_lifecycle(self, now: float) -> bool:
        """Check age and collapse if past settle threshold.

        Returns True if visual state changed (needs refresh).
        """
        if self._pinned:
            return False
        age = now - self._birth
        if age >= _DISCLOSURE_SETTLE and self._expanded:
            self._expanded = False
            self._collapsed_at = now
            return True
        return False

    def on_click(self) -> None:
        """Toggle expansion on click; pin to prevent auto-collapse."""
        self._expanded = not self._expanded
        self._pinned = True
        self.refresh()


# ── Agent disclosure ───────────────────────────────────────────────
# Delegated work rendered as collapsible artifacts with a blue left edge.
# Collapsed by default — expand on click to see the agent's result.

_GLYPH_DELEGATE = "\u21b3"  # ↳
_GLYPH_SUCCESS = "\u2713"   # ✓
_GLYPH_ERROR = "\u2717"     # ✗
_GLYPH_RUNNING = "\u25cf"   # ●


class _AgentDisclosure(Static):
    """Collapsible agent result — blue left edge, starts collapsed.

    Blue identifies delegated work in peripheral vision (parallel to
    gold for user, teal for assistant, green for edits).
    """

    DEFAULT_CSS = f"""
    _AgentDisclosure {{
        border-left: tall {_BLUE_DIM_HEX};
        padding: 0 0 0 2;
        margin: 0;
    }}
    """

    _AGENT_BREATH_FPS: int = 12  # lower than main breath — subtle

    def __init__(
        self, tool_use_id: str, description: str, *, classes: str = "",
    ) -> None:
        super().__init__("", classes=classes)
        self._tool_use_id = tool_use_id
        self._description = description
        self._result: str | None = None
        self._expanded: bool = False
        self._status: str = "running"  # running | success | error
        self._phase: float = 0.0
        self._breath_timer: Timer | None = None

    def on_mount(self) -> None:
        if self._status == "running":
            self._breath_timer = self.set_interval(
                1.0 / self._AGENT_BREATH_FPS, self._breath_tick,
            )

    def _breath_tick(self) -> None:
        """Advance the running dot's breathing animation."""
        self._phase += 1.0 / self._AGENT_BREATH_FPS
        self.refresh()

    def _stop_breath(self) -> None:
        if self._breath_timer is not None:
            self._breath_timer.stop()
            self._breath_timer = None

    def complete(self, content: str, is_error: bool) -> None:
        """Fill in the agent's result and transition from running."""
        self._stop_breath()
        self._result = content
        self._status = "error" if is_error else "success"
        self.refresh()

    def on_click(self) -> None:
        """Toggle expansion — only after result arrives."""
        if self._result:
            self._expanded = not self._expanded
            self.refresh()

    def render(self) -> Text:
        if self._status == "running":
            lut = _ensure_breath_lut()
            raw = math.exp(math.sin(2.0 * math.pi * self._phase / _BREATH_PERIOD))
            breath = (raw - _EXP_NEG1) / _EXP_RANGE
            idx = max(0, min(63, round(breath * 63)))
            glyph = Text(_GLYPH_RUNNING, style=lut[idx])
        elif self._status == "error":
            glyph = Text(_GLYPH_ERROR, style=_ROSE_HEX)
        else:
            glyph = Text(_GLYPH_SUCCESS, style=_DIM_HEX)
        result = Text()
        result.append(f"{_GLYPH_DELEGATE} {self._description}  ", style=_DIM_HEX)
        result.append_text(glyph)
        if self._expanded and self._result:
            width = self.container_size.width - 4 if self.container_size.width > 8 else 60
            rendered = _md_to_text(self._result, width)
            result.append("\n")
            result.append_text(rendered)
        return result


# ── Tool glyph constants ──────────────────────────────────────────
# ▸ for local workspace tools, ↗ for web tools (reaching outward).

_GLYPH_LOCAL = "\u25b8"  # ▸
_GLYPH_WEB = "\u2197"    # ↗

_WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})



# ── Breathing indicator ─────────────────────────────────────────────
# Renders into #tail at 24 FPS.  Three dots modulate in teal luminance
# using the design system's exp(sin(t)) curve, with per-dot phase
# offset creating a gentle traveling pulse.

_BREATH_FPS: int = 24
_BREATH_PERIOD: float = 3.0     # seconds per full breath cycle
_BREATH_HUE: float = 180.0      # teal
_BREATH_CHROMA: float = 0.08
_BREATH_DIM_L: float = 0.18     # near surface-deep
_BREATH_BRIGHT_L: float = 0.50  # clearly visible teal
_EXP_NEG1: float = math.exp(-1)
_EXP_RANGE: float = math.exp(1) - _EXP_NEG1

# Pre-compute 64-step LUT for smooth breathing.
_BREATH_LUT: list[str] = []


def _ensure_breath_lut() -> list[str]:
    if not _BREATH_LUT:
        for i in range(64):
            t = i / 63
            l = _BREATH_DIM_L + (_BREATH_BRIGHT_L - _BREATH_DIM_L) * t
            _BREATH_LUT.append(OklchColor(l, _BREATH_CHROMA, _BREATH_HUE).to_hex())
    return _BREATH_LUT


def _breathing_text(phase: float) -> Text:
    """Build a centered breathing indicator — three teal dots pulsing."""
    lut = _ensure_breath_lut()
    TWO_PI = 2.0 * math.pi
    text = Text(justify="center")
    for i in range(3):
        p = phase + i * 0.3
        raw = math.exp(math.sin(TWO_PI * p / _BREATH_PERIOD))
        breath = (raw - _EXP_NEG1) / _EXP_RANGE
        idx = max(0, min(63, round(breath * 63)))
        if i > 0:
            text.append(" ", style="")
        text.append("●", style=lut[idx])
    return text


def _tool_glyph(name: str) -> str:
    """Return the activity-line glyph for a tool — ↗ for web, ▸ for local."""
    return _GLYPH_WEB if name in _WEB_TOOLS else _GLYPH_LOCAL


def _tool_summary(name: str, tool_input: dict[str, object]) -> str:
    """One-line ambient summary of a tool call — name + key context."""
    if name == "Edit":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        old = str(tool_input.get("old_string", ""))
        new = str(tool_input.get("new_string", ""))
        adds, rems = compute_edit_stats(old, new)
        stats = f"  +{adds} −{rems}" if adds or rems else ""  # noqa: RUF001
        return f"{name} {fname}{stats}" if fname else name
    if name == "MultiEdit":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        edits = tool_input.get("edits", [])
        if isinstance(edits, list):
            adds, rems = compute_multi_edit_stats(edits)
        else:
            adds, rems = 0, 0
        stats = f"  +{adds} −{rems}" if adds or rems else ""  # noqa: RUF001
        return f"{name} {fname}{stats}" if fname else name
    if name == "Write":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        return f"{name} {fname}  (new)" if fname else name
    if name == "Read":
        fp = str(tool_input.get("file_path", ""))
        fname = PurePosixPath(fp).name if fp else ""
        return f"{name} {fname}" if fname else name
    if name == "Bash":
        cmd = str(tool_input.get("command", ""))
        # Show first meaningful segment, strip long pipes.
        short = cmd.split("|")[0].strip()[:50]
        return f"{name} {short}" if short else name
    if name == "Grep":
        pattern = str(tool_input.get("pattern", ""))
        return f"{name} /{pattern[:30]}/" if pattern else name
    if name == "Glob":
        pattern = str(tool_input.get("pattern", ""))
        return f"{name} {pattern[:40]}" if pattern else name
    if name == "Agent":
        desc = str(tool_input.get("description", ""))
        return f"{name} {desc[:40]}" if desc else name
    if name == "WebFetch":
        url = str(tool_input.get("url", ""))
        return f"fetch {_shorten_url(url)}" if url else name
    if name == "WebSearch":
        query = str(tool_input.get("query", ""))
        return f'search "{query[:40]}"' if query else name
    return name


def _format_ask_user_question(tool_input: dict[str, object]) -> str:
    """Format an AskUserQuestion tool call as readable markdown."""
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return ""
    parts: list[str] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question", ""))
        if not text:
            continue
        parts.append(f"**{text}**")
        options = q.get("options")
        if isinstance(options, list):
            for i, opt in enumerate(options, 1):
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label", ""))
                desc = str(opt.get("description", ""))
                line = f"{i}. **{label}**"
                if desc:
                    line += f" — {desc}"
                parts.append(line)
        parts.append("")  # blank line between questions
    return "\n".join(parts).strip()


class ConversationWidget(DragScrollMixin, Widget):
    """Scrollable conversation with in-place animated tail."""

    DEFAULT_CSS = f"""
    ConversationWidget {{
        layout: vertical;
        height: 1fr;
    }}
    ConversationWidget #history {{
        height: 1fr;
    }}
    ConversationWidget #tail {{
        height: auto;
        text-align: center;
    }}
    ConversationWidget #tail.streaming {{
        border-left: tall {_TEAL_DIM_HEX};
        background: {_SURFACE_HEX};
        padding: 1 1 1 2;
        text-align: left;
    }}
    ConversationWidget #queue-indicator {{
        height: auto;
        width: auto;
        background: transparent;
    }}
    ConversationWidget .thinking {{
        padding-left: 4;
        margin: 0;
    }}
    ConversationWidget .tool-activity {{
        padding-left: 4;
        margin: 0;
    }}
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._stream_buffer: str = ""
        self._stream_uuid: str = ""
        self._stream_dirty: bool = False
        self._stream_timer: Timer | None = None
        self._working: bool = False
        self._working_phase: float = 0.0
        self._working_timer: Timer | None = None
                # Buffered text — held until we know if it's narration (followed
        # by tool call) or a response (followed by turn complete/stream).
        self._pending_text: str | None = None
        # Accumulated non-streamed assistant text within the current turn.
        # Captures ClouSupervisorText rendered to history so the persistence
        # boundary (ClouTurnContentReady) includes them.
        self._turn_text: str = ""
        # Startup state — tool/thinking noise is suppressed until the
        # supervisor's greeting arrives, creating a clean experiential arc
        # from "system orienting" to "system present."
        self._initializing: bool = True
        self._init_drag_scroll()
        # Disclosure collapse timer — shared across all _EditDisclosure widgets.
        self._disclosure_timer: Timer | None = None
        # Agent disclosures awaiting their tool result.
        self._pending_agents: dict[str, _AgentDisclosure] = {}

    def compose(self) -> ComposeResult:
        from clou.ui.widgets.command_palette import CommandPalette

        with VerticalScroll(id="history"):
            yield Static("", id="tail")
        yield Static("", id="queue-indicator")
        yield CommandPalette(id="command-palette")
        yield WakeIndicator(id="wake-indicator")
        yield PromptInput(id="user-input")

    def on_mount(self) -> None:
        """Start in the initializing state — wave provides the pulse."""
        self.add_class("initializing")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_tail(self) -> None:
        """Reset #tail to idle — clear content and streaming class."""
        tail = self.query_one("#tail", Static)
        tail.remove_class("streaming")
        tail.update("")

    def _append(self, renderable: Text | Markdown | str, extra_classes: str = "") -> None:
        """Mount a message widget before #tail and scroll to bottom."""
        history = self.query_one("#history", VerticalScroll)
        tail = self.query_one("#tail", Static)
        css = f"msg {extra_classes}".strip()
        history.mount(Static(renderable, classes=css), before=tail)
        history.scroll_end(animate=False)

    def _append_markdown(self, source: str, extra_classes: str = "") -> None:
        """Mount a Markdown message that re-renders on resize."""
        history = self.query_one("#history", VerticalScroll)
        tail = self.query_one("#tail", Static)
        css = f"msg {extra_classes}".strip()
        history.mount(_MarkdownMessage(source, classes=css), before=tail)
        history.scroll_end(animate=False)

    def _build_edit_summary(
        self, name: str, tool_input: dict[str, object], summary: str,
    ) -> Text:
        """Build styled Text for edit tool summaries with colored stats."""
        # Parse the summary to find the +N -M part
        text = Text()
        text.append("\u25b8 ", style=_DIM_HEX)
        if name == "Edit":
            fp = str(tool_input.get("file_path", ""))
            fname = PurePosixPath(fp).name if fp else name
            old = str(tool_input.get("old_string", ""))
            new = str(tool_input.get("new_string", ""))
            adds, rems = compute_edit_stats(old, new)
            text.append(f"Edit {fname}", style=_DIM_HEX)
            if adds or rems:
                text.append("  ", style="")
                text.append(f"+{adds}", style=_GREEN_HEX)
                text.append(" ", style="")
                text.append(f"−{rems}", style=_ROSE_HEX)  # noqa: RUF001
        elif name == "MultiEdit":
            fp = str(tool_input.get("file_path", ""))
            fname = PurePosixPath(fp).name if fp else name
            edits = tool_input.get("edits", [])
            if isinstance(edits, list):
                adds, rems = compute_multi_edit_stats(edits)
            else:
                adds, rems = 0, 0
            text.append(f"MultiEdit {fname}", style=_DIM_HEX)
            if adds or rems:
                text.append("  ", style="")
                text.append(f"+{adds}", style=_GREEN_HEX)
                text.append(" ", style="")
                text.append(f"−{rems}", style=_ROSE_HEX)  # noqa: RUF001
        elif name == "Write":
            fp = str(tool_input.get("file_path", ""))
            fname = PurePosixPath(fp).name if fp else name
            text.append(f"Write {fname}", style=_DIM_HEX)
            text.append("  (new)", style=_GREEN_HEX)
        return text

    def _append_edit(
        self,
        name: str,
        tool_input: dict[str, object],
        styled_summary: Text,
    ) -> None:
        """Mount an _EditDisclosure widget for edit tool calls."""
        diff_body: Text | None = None
        if name == "Edit":
            old = str(tool_input.get("old_string", ""))
            new = str(tool_input.get("new_string", ""))
            if old or new:
                diff_body = render_inline_diff(old, new)
        elif name == "MultiEdit":
            edits = tool_input.get("edits", [])
            if isinstance(edits, list):
                combined = Text()
                for i, edit in enumerate(edits):
                    if not isinstance(edit, dict):
                        continue
                    old = str(edit.get("old_string", ""))
                    new = str(edit.get("new_string", ""))
                    if old or new:
                        if i > 0 and combined.plain:
                            combined.append("\n")
                        combined.append_text(render_inline_diff(old, new))
                if combined.plain:
                    diff_body = combined
        history = self.query_one("#history", VerticalScroll)
        tail = self.query_one("#tail", Static)
        widget = _EditDisclosure(
            styled_summary, diff_body, classes="msg tool-activity",
        )
        history.mount(widget, before=tail)
        history.scroll_end(animate=False)
        self._ensure_disclosure_timer()

    # ------------------------------------------------------------------
    # Disclosure lifecycle timer
    # ------------------------------------------------------------------

    def _ensure_disclosure_timer(self) -> None:
        """Start the disclosure collapse timer if not already running."""
        if self._disclosure_timer is None:
            self._disclosure_timer = self.set_interval(
                0.5, self._disclosure_tick,  # 2 Hz
            )

    def _disclosure_tick(self) -> None:
        """Check disclosure widgets for lifecycle transitions and prune stale ones."""
        now = time.monotonic()
        any_active = False
        to_remove: list[_EditDisclosure] = []
        for widget in self.query(_EditDisclosure):
            if not widget._pinned and widget._expanded:
                any_active = True
            if widget.update_lifecycle(now):
                widget.refresh()
            # Prune collapsed unpinned widgets after they've been invisible long enough.
            if (
                not widget._pinned
                and not widget._expanded
                and widget._collapsed_at > 0
                and (now - widget._collapsed_at) >= _DISCLOSURE_PRUNE
            ):
                to_remove.append(widget)
        for widget in to_remove:
            widget.remove()
        # Keep the timer running while there are widgets waiting to be pruned.
        has_pending_prune = any(
            not w._pinned and not w._expanded and w._collapsed_at > 0
            for w in self.query(_EditDisclosure)
        )
        if not any_active and not has_pending_prune and self._disclosure_timer is not None:
            self._disclosure_timer.stop()
            self._disclosure_timer = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def add_user_message(self, text: str, *, queued: bool = False) -> None:
        """Append a user message and start the working indicator."""
        self._flush_pending_text()
        self._flush_activity_line()
        self._turn_text = ""
        history = self.query_one("#history", VerticalScroll)
        tail = self.query_one("#tail", Static)
        history.mount(
            _UserMessage(text, queued=queued, classes="msg"), before=tail,
        )
        history.scroll_end(animate=False)
        self._start_working()

    def reset_turn_state(self) -> None:
        """Atomically clear all turn state — single reset point for external callers."""
        self._stop_working()
        self._flush_activity_line()
        self._pending_text = None
        self._turn_text = ""
        self._stop_timer()
        self._stream_buffer = ""
        self._stream_uuid = ""
        self._stream_dirty = False
        self._clear_tail()
        # Clear stale "queued" badges — if the worker died, these messages
        # will never be delivered so the label is a lie.
        for um in self.query(_UserMessage):
            um.mark_active()

    def add_error_message(self, text: str) -> None:
        """Append an error message to the conversation history."""
        self._append(Text(f"\n{text}\n", style=f"bold {_ROSE_HEX}"))

    def add_command_output(self, renderable: Text | str) -> None:
        """Append slash command output — lighter than supervisor text."""
        self._append(Text(""))  # breathing room
        if isinstance(renderable, str):
            renderable = Text(renderable, style=_DIM_HEX)
        self._append(renderable)

    def add_command_error(self, text: str) -> None:
        """Append a brief dim error for bad slash commands."""
        self._append(Text(f"  {text}", style=_DIM_HEX))

    # ------------------------------------------------------------------
    # Working indicator (animated in-place via #tail)
    # ------------------------------------------------------------------

    def _start_working(self) -> None:
        """Begin breathing indicator in #tail — three dots pulsing in teal."""
        self._working = True
        self._working_phase = 0.0
        tail = self.query_one("#tail", Static)
        tail.update(_breathing_text(0.0))
        if self._working_timer is None:
            self._working_timer = self.set_interval(
                1.0 / _BREATH_FPS, self._working_tick
            )
        self.query_one("#history", VerticalScroll).scroll_end(animate=False)

    def _stop_working(self) -> None:
        """Stop the breathing indicator."""
        self._working = False
        if self._working_timer is not None:
            self._working_timer.stop()
            self._working_timer = None

    def _working_tick(self) -> None:
        """Advance the breathing animation one frame."""
        if not self._working:
            return
        self._working_phase += 1.0 / _BREATH_FPS
        tail = self.query_one("#tail", Static)
        tail.update(_breathing_text(self._working_phase))

    # ------------------------------------------------------------------
    # Stream debounce + animation tick
    # ------------------------------------------------------------------

    def _ensure_timer(self) -> None:
        """Start the stream flush timer if not already running."""
        if self._stream_timer is None:
            self._stream_timer = self.set_interval(
                _STREAM_FLUSH_INTERVAL, self._tick
            )

    def _stop_timer(self) -> None:
        """Cancel the stream flush timer if running."""
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None

    def _tick(self) -> None:
        """Flush accumulated stream content to #tail."""
        if self._stream_dirty:
            self._stream_dirty = False
            tail = self.query_one("#tail", Static)
            tail.update(Markdown(self._stream_buffer))
            self.query_one("#history", VerticalScroll).scroll_end(animate=False)

    def on_unmount(self) -> None:
        """Clean up timers on widget removal."""
        self._stop_timer()
        self._working = False
        if self._working_timer is not None:
            self._working_timer.stop()
            self._working_timer = None
        if self._disclosure_timer is not None:
            self._disclosure_timer.stop()
            self._disclosure_timer = None

    # ------------------------------------------------------------------
    # Startup lifecycle
    # ------------------------------------------------------------------

    def _end_initializing(self) -> None:
        """The system is present — wave stops, gold prompt appears."""
        if not self._initializing:
            return
        self._initializing = False
        self.remove_class("initializing")
        # Promote startup activity into scrollable history before hiding wake.
        try:
            wake = self.query_one(WakeIndicator)
            for text, _birth in wake._lines:
                self._append(Text(f"  {text}", style=_DIM_HEX), "tool-activity")
            wake.stop()
        except LookupError:
            pass
        try:
            self.query_one("#user-input", PromptInput).set_ready()
        except LookupError:
            pass

    # ------------------------------------------------------------------
    # Pending text buffer
    # ------------------------------------------------------------------

    def _flush_pending_text(self) -> None:
        """Flush buffered supervisor text as a normal response.

        Called when we learn the buffered text was NOT narration — i.e.
        it was followed by turn complete, streaming, or another supervisor
        message rather than a tool call.
        """
        if self._pending_text is None:
            return
        text = self._pending_text
        self._pending_text = None
        self._stop_working()
        self._write_horizon()
        self._append_markdown(text)
        self._turn_text += ("\n\n" + text) if self._turn_text else text
        self._clear_tail()

    def _flush_activity_line(self) -> None:
        """No-op — retained for call-site compatibility."""

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _write_horizon(self) -> None:
        """Composed void between turns — silence, not a line.

        The left-edge color language (teal/gold) provides the structural
        separation. The void provides breathing room — intentional
        emptiness that holds the turns apart without drawing the eye.
        """
        # The margin on _MarkdownMessage / _UserMessage already creates
        # separation. No Rule needed — the void is the boundary.
        pass

    def on_clou_supervisor_text(self, msg: ClouSupervisorText) -> None:
        """Append assistant text to history, or buffer as candidate status.

        Short text while working with no status yet might be the agent
        narrating its intent ("Let me read the file…").  We buffer it
        and wait: if a tool call follows, it was narration → promote to
        breathing status.  If anything else follows (turn complete,
        streaming, another text), it was a real response → flush to
        history.
        """
        self._end_initializing()
        # Track whether we're mid-turn — flush may stop working, but
        # the intent to keep working persists until turn complete.
        was_working = self._working
        # Any previously buffered text is a response — flush it.
        self._flush_pending_text()
        # Short text while working → candidate narration (e.g. "Let me
        # search for…").  Don't flush the activity line — narration
        # precedes a tool call in the same message, so the tool burst
        # should continue compacting.
        if was_working and len(msg.text) < 200:
            first_line = msg.text.strip().splitlines()[0] if msg.text.strip() else ""
            if first_line:
                # If there's already buffered narration that wasn't consumed
                # by a tool call, it was a real response — flush it.
                if self._pending_text is not None:
                    self._flush_activity_line()
                    self._flush_pending_text()
                self._pending_text = msg.text
                if not self._working:
                    self._start_working()
                return
        # Not narration — this is a real response. Freeze the activity line.
        self._flush_activity_line()
        self._stop_working()
        self._write_horizon()
        self._append_markdown(msg.text)
        self._turn_text += ("\n\n" + msg.text) if self._turn_text else msg.text
        self._clear_tail()
        if was_working:
            # Restart — more work may follow.  Turn complete stops it.
            self._start_working()

    def on_clou_stream_chunk(self, msg: ClouStreamChunk) -> None:
        """Accumulate streaming tokens and mark dirty for next flush."""
        self._flush_pending_text()
        self._flush_activity_line()
        if msg.uuid != self._stream_uuid:
            self._stream_buffer = ""
            self._stream_uuid = msg.uuid
            # Streaming begins — pulse yields to content, tail adopts
            # the assistant visual language for the live preview.
            self._stop_working()
            self.query_one("#tail", Static).add_class("streaming")
        self._stream_buffer += msg.text
        if len(self._stream_buffer) > _MAX_STREAM_BUFFER:
            self._stream_buffer = self._stream_buffer[-_MAX_STREAM_BUFFER:]
        self._stream_dirty = True
        self._ensure_timer()

    def on_clou_turn_complete(self, msg: ClouTurnComplete) -> None:
        """Move accumulated stream buffer to history and clear tail."""
        self._end_initializing()
        self._flush_pending_text()
        self._flush_activity_line()
        self._stop_working()
        self._stop_timer()
        # Compute completed content: stream buffer takes precedence;
        # _turn_text captures non-streamed ClouSupervisorText.
        if self._turn_text and self._stream_buffer:
            completed = self._turn_text + "\n\n" + self._stream_buffer
        else:
            completed = self._stream_buffer or self._turn_text
        if self._stream_buffer:
            self._write_horizon()
            self._append_markdown(self._stream_buffer)
        self._stream_buffer = ""
        self._stream_uuid = ""
        self._stream_dirty = False
        self._turn_text = ""
        self._clear_tail()
        # Notify app for persistence — typed contract, no private state access.
        if completed:
            self.post_message(ClouTurnContentReady(completed))

    def on_clou_thinking(self, msg: ClouThinking) -> None:
        """Append dimmed thinking block — flows into wake during startup."""
        if self._initializing:
            try:
                first_line = msg.text.splitlines()[0]
                self.query_one(WakeIndicator).add_line(first_line)
            except (LookupError, IndexError):
                pass
            return
        for line in msg.text.splitlines():
            self._append(Text(line, style=f"italic {_DIM_HEX}"), "thinking")

    def on_clou_tool_use(self, msg: ClouToolUse) -> None:
        """Append compact tool-use indicator — shimmers in wake during startup."""
        glyph = _tool_glyph(msg.name)
        if self._initializing:
            summary = _tool_summary(msg.name, msg.tool_input)
            try:
                self.query_one(WakeIndicator).add_line(f"{glyph} {summary}")
            except LookupError:
                pass
            return
        # Tool call confirms any buffered text was narration — keep as status.
        if self._pending_text is not None:
            self._pending_text = None  # consumed as narration
        summary = _tool_summary(msg.name, msg.tool_input)
        # Ensure the breathing indicator stays alive during tool bursts.
        # It may have been stopped by a flushed supervisor text mid-turn.
        # Don't echo tool names into the status — they're already visible
        # as activity lines in the chat.  Only agent narration (set in
        # on_clou_supervisor_text) belongs in the breathing status.
        if not self._working:
            self._start_working()
        # AskUserQuestion carries structured questions — surface them as
        # visible conversation content so the user can read and respond.
        if msg.name == "AskUserQuestion":
            self._flush_activity_line()
            self._stop_working()
            md = _format_ask_user_question(msg.tool_input)
            if md:
                self._write_horizon()
                self._append_markdown(md)
                self._clear_tail()
            return
        # ask_user is a gate tool — questions are already in the model's
        # text output.  Suppress the activity line and stop the working
        # indicator so the input field feels inviting.
        if msg.name in ("ask_user", "mcp__clou__ask_user"):
            self._flush_activity_line()
            self._stop_working()
            self._clear_tail()
            return
        # Agent tool calls get their own collapsible disclosure widget.
        if msg.name == "Agent":
            self._flush_activity_line()
            desc = str(msg.tool_input.get("description", "agent"))
            disclosure = _AgentDisclosure(
                msg.tool_use_id, desc, classes="msg agent-disclosure",
            )
            history = self.query_one("#history", VerticalScroll)
            tail = self.query_one("#tail", Static)
            history.mount(disclosure, before=tail)
            history.scroll_end(animate=False)
            if msg.tool_use_id:
                self._pending_agents[msg.tool_use_id] = disclosure
            return
        if msg.name in ("Edit", "MultiEdit", "Write"):
            styled = self._build_edit_summary(msg.name, msg.tool_input, summary)
            self._append_edit(msg.name, msg.tool_input, styled)
        else:
            self._append(
                Text(f"{glyph} {summary}", style=_DIM_HEX),
                "tool-activity",
            )

    def on_clou_tool_result(self, msg: ClouToolResult) -> None:
        """Route tool results — agent results fill disclosures, errors whisper.

        Agent results (matched by tool_use_id) are routed to their
        ``_AgentDisclosure`` widget.  All other tool results follow the
        existing path: only errors render as brief dim whispers.
        """
        if self._initializing:
            return
        # Agent results → fill the matching disclosure widget.
        disclosure = self._pending_agents.pop(msg.tool_use_id, None)
        if disclosure is not None:
            disclosure.complete(msg.content, msg.is_error)
            return
        if msg.is_error:
            self._flush_activity_line()
            content = _strip_ansi(msg.content)
            brief = content.splitlines()[0][:60] if content else ""
            self._append(
                Text(f"\u2717 {brief}", style=_DIM_HEX),
                "tool-activity",
            )

    def on_clou_processing_started(self, msg: ClouProcessingStarted) -> None:
        """User message picked up by model — already shown at submit time.

        Transition the queued message to active and ensure the working
        indicator is alive.
        """
        # Find the first still-queued _UserMessage (FIFO order matches
        # the queue — the earliest queued message was dequeued first).
        for um in self.query(_UserMessage):
            if um._queued:
                um.mark_active()
                break
        if not self._working:
            self._start_working()

    # ------------------------------------------------------------------
    # Queue indicator
    # ------------------------------------------------------------------

    def update_queue_count(self, count: int) -> None:
        """Update the queue indicator with the number of pending messages."""
        try:
            indicator = self.query_one("#queue-indicator", Static)
            if count > 0:
                indicator.update(
                    Text(f"  · {count} queued", style=f"italic {_DIM_HEX}")
                )
            else:
                indicator.update("")
        except LookupError:
            pass
