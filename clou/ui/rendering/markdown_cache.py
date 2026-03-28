"""Markdown rendering with LRU cache.

Renders Markdown source to Rich ``Text`` via a ``Console.capture()``
roundtrip.  Results are cached by ``(source, width)`` so terminal
resize only re-renders messages whose available width changed.
"""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

_md_cache: dict[tuple[str, int], Text] = {}
_MD_CACHE_MAX = 200  # ~200 messages × avg 2KB = manageable


def md_to_text(source: str, width: int) -> Text:
    """Render Markdown to styled Text — preserves formatting, enables selection."""
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
        for evict_key in list(_md_cache)[: _MD_CACHE_MAX // 4]:
            _md_cache.pop(evict_key, None)
    return result
