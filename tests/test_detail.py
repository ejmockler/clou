"""Tests for clou.ui.screens.detail — DetailScreen."""

from __future__ import annotations

from textual.widgets import RichLog

from clou.ui.screens.detail import DetailScreen


class TestDetailScreen:
    """Tests for DetailScreen compose output."""

    def test_richlog_markup_is_false(self) -> None:
        """The RichLog in DetailScreen must have markup=False since content
        is plain text from _format_costs()."""
        screen = DetailScreen(title="Test", content="hello")
        widgets = list(screen.compose())
        rich_logs = [w for w in widgets if isinstance(w, RichLog)]
        assert len(rich_logs) == 1
        assert rich_logs[0].markup is False
