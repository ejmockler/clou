"""EscalationModal — structured decision card for user escalations.

When an escalation arrives during autonomous work, the breath gathers focus
and this modal surfaces as a decision card. The user deliberates, selects
an option, and the breath resumes.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from rich.markup import escape as _escape_markup
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from clou.ui.messages import ClouEscalationResolved
from clou.ui.theme import PALETTE

_log = logging.getLogger(__name__)

# Semantic hex colors from the palette for inline Rich markup.
_TEXT_BRIGHT_HEX = PALETTE["text-bright"].to_hex()
_TEXT_HEX = PALETTE["text"].to_hex()
_TEXT_DIM_HEX = PALETTE["text-dim"].to_hex()
_TEXT_MUTED_HEX = PALETTE["text-muted"].to_hex()
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_SURFACE_OVERLAY_HEX = PALETTE["surface-overlay"].to_hex()
_ORANGE_HEX = PALETTE["accent-orange"].to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_SURFACE_RAISED_HEX = PALETTE["surface-raised"].to_hex()

# Classification keywords that indicate a blocking escalation.
_BLOCKING_KEYWORDS = frozenset({"blocking", "critical", "fatal", "error"})


def _is_blocking(classification: str) -> bool:
    """Return True if the classification suggests a blocking escalation."""
    lower = classification.lower()
    return any(
        re.search(r'(?<![a-z])(?<!non-)(?<!non\s)(?<!un)' + kw + r'(?![a-z])', lower)
        for kw in _BLOCKING_KEYWORDS
    )


class _OptionItem(Static):
    """A single selectable option within the escalation card."""

    def __init__(
        self,
        label: str,
        description: str,
        recommended: bool = False,
        *,
        classes: str | None = None,
    ) -> None:
        super().__init__(classes=classes)
        self.label = label
        self.description = description
        self.recommended = recommended

    def render(self) -> str:
        """Render the option as Rich markup."""
        safe_label = _escape_markup(self.label)
        safe_desc = _escape_markup(self.description)
        if self.has_class("selected"):
            marker = "\u25b8"  # ▸
            label_color = _GOLD_HEX if self.recommended else _TEXT_BRIGHT_HEX
            rec_tag = f"  [{_GOLD_HEX}](recommended)[/]" if self.recommended else ""
            return (
                f"[{_TEXT_BRIGHT_HEX}]{marker}[/] "
                f"[bold {label_color}]{safe_label}[/]{rec_tag}\n"
                f"  [{_TEXT_DIM_HEX}]{safe_desc}[/]"
            )
        marker = "\u25b9"  # ▹
        label_color = _GOLD_HEX if self.recommended else _TEXT_HEX
        rec_tag = f"  [{_GOLD_HEX}](recommended)[/]" if self.recommended else ""
        return (
            f"[{_TEXT_DIM_HEX}]{marker}[/] "
            f"[{label_color}]{safe_label}[/]{rec_tag}\n"
            f"  [{_TEXT_MUTED_HEX}]{safe_desc}[/]"
        )


class EscalationModal(ModalScreen[str | None]):
    """Decision card modal for coordinator escalations.

    Surfaces a structured card with classification, issue description,
    and selectable options. Enter resolves, Esc dismisses.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Move up", show=False),
        Binding("down", "cursor_down", "Move down", show=False),
        Binding("enter", "select_option", "Select", show=False),
        Binding("escape", "dismiss_modal", "Dismiss", show=False),
    ]

    DEFAULT_CSS = f"""
    EscalationModal {{
        align: center middle;
    }}

    #escalation-card {{
        max-width: 80;
        max-height: 70vh;
        width: auto;
        height: auto;
        padding: 1 2;
        border: round {_ORANGE_HEX};
        background: {_SURFACE_OVERLAY_HEX};
    }}

    #escalation-card.blocking {{
        border: round {_ROSE_HEX};
    }}

    #escalation-header {{
        height: auto;
        width: 1fr;
        content-align: left top;
    }}

    #escalation-body {{
        height: auto;
        width: 1fr;
        margin: 1 0;
    }}

    #escalation-options {{
        height: auto;
        width: 1fr;
        margin: 1 0 0 0;
    }}

    .escalation-option {{
        height: auto;
        width: 1fr;
        margin: 0 0 1 0;
    }}

    .escalation-option.selected {{
        background: {_SURFACE_RAISED_HEX};
        border-left: thick {_GOLD_HEX};
    }}

    #escalation-footer {{
        height: auto;
        width: 1fr;
    }}
    """

    def __init__(
        self,
        path: Path,
        classification: str,
        issue: str,
        options: list[dict[str, object]],
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.path = path
        self.classification = classification
        self.issue = issue
        self.options = options
        self._selected_index: int = 0

    def compose(self) -> ComposeResult:
        blocking = _is_blocking(self.classification)
        icon = "\u2715" if blocking else "\u26a0"  # ✕ or ⚠

        card = Vertical(id="escalation-card")
        if blocking:
            card.add_class("blocking")

        with card:
            safe_cls = _escape_markup(self.classification)
            yield Static(
                f"[bold {_TEXT_BRIGHT_HEX}]{icon} {safe_cls}[/]",
                id="escalation-header",
            )
            yield Static(
                f"[{_TEXT_HEX}]{_escape_markup(self.issue)}[/]",
                id="escalation-body",
            )
            with Vertical(id="escalation-options"):
                for i, opt in enumerate(self.options):
                    label = str(opt.get("label", ""))
                    description = str(opt.get("description", ""))
                    recommended = bool(opt.get("recommended", False))
                    item = _OptionItem(
                        label=label,
                        description=description,
                        recommended=recommended,
                        classes="escalation-option",
                    )
                    if i == 0:
                        item.add_class("selected")
                    yield item
            yield Static(
                f"[{_TEXT_MUTED_HEX}]Enter: select  Esc: dismiss[/]",
                id="escalation-footer",
            )

    # ------------------------------------------------------------------
    # Selection management
    # ------------------------------------------------------------------

    def _update_selection(self, new_index: int) -> None:
        """Move the visual selection to *new_index*."""
        items = self.query(".escalation-option")
        if not items:
            return
        count = len(items)
        new_index = new_index % count
        items[self._selected_index].remove_class("selected")
        items[new_index].add_class("selected")
        self._selected_index = new_index

    def action_cursor_up(self) -> None:
        self._update_selection(self._selected_index - 1)

    def action_cursor_down(self) -> None:
        self._update_selection(self._selected_index + 1)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def action_select_option(self) -> None:
        self._resolve()

    def action_dismiss_modal(self) -> None:
        self.post_message(ClouEscalationResolved(path=self.path, disposition="dismissed"))
        self.dismiss(None)

    def _resolve(self) -> None:
        """Write disposition to the escalation file and dismiss."""
        if not self.options:
            self.post_message(ClouEscalationResolved(path=self.path, disposition="dismissed"))
            self.dismiss(None)
            return
        selected = self.options[self._selected_index]
        label = str(selected.get("label", "")).replace("\n", " ").strip()

        # Validate that the path is under the project's .clou/escalations/.
        resolved = self.path.resolve()
        try:
            clou_esc = (self.app._project_dir / ".clou" / "escalations").resolve()  # type: ignore[union-attr]
            path_ok = resolved.is_relative_to(clou_esc)
        except (AttributeError, TypeError):
            # Fallback: check parts contain .clou and escalations in order.
            parts = resolved.parts
            path_ok = ".clou" in parts and "escalations" in parts
        if not path_ok:
            _log.warning(
                "Refusing escalation write: path %s is not under .clou/escalations/",
                resolved,
            )
        else:
            # Append disposition to the escalation markdown file.
            try:
                timestamp = datetime.now(tz=UTC).isoformat()
                disposition_block = (
                    f"\n\n## Disposition\n\n"
                    f"**Selected:** {label}\n"
                    f"**Timestamp:** {timestamp}\n"
                )
                with open(resolved, "a", encoding="utf-8") as f:
                    f.write(disposition_block)
            except OSError:
                _log.debug(
                    "Could not write disposition to %s", self.path, exc_info=True
                )

        self.post_message(ClouEscalationResolved(path=self.path, disposition=label))
        self.dismiss(label)
