"""Diff rendering — unified diff to styled Rich Text."""

from __future__ import annotations

from rich.text import Text

from clou.ui.theme import PALETTE

_GREEN_HEX = PALETTE["accent-green"].to_hex()
_ROSE_HEX = PALETTE["accent-rose"].to_hex()
_GOLD_HEX = PALETTE["accent-gold"].to_hex()
_DIM_HEX = PALETTE["text-dim"].to_hex()
_MUTED_HEX = PALETTE["text-muted"].to_hex()


def render_diff(diff_text: str) -> Text:
    """Parse a unified diff string into styled Rich Text.

    Returns an empty ``Text`` if *diff_text* is empty.
    """
    result = Text()
    for i, line in enumerate(diff_text.splitlines()):
        if i > 0:
            result.append("\n")
        if line.startswith("diff ") or line.startswith("index "):
            result.append(line, style=_MUTED_HEX)
        elif line.startswith("---") or line.startswith("+++"):
            result.append(line, style=f"bold {_GOLD_HEX}")
        elif line.startswith("@@"):
            result.append(line, style=_MUTED_HEX)
        elif line.startswith("+"):
            result.append(line, style=_GREEN_HEX)
        elif line.startswith("-"):
            result.append(line, style=_ROSE_HEX)
        else:
            result.append(line, style=_DIM_HEX)
    return result
