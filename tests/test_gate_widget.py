"""Tests for clou.ui.widgets.gate — the live question surface."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from clou.ui.widgets.gate import GateWidget


class GateApp(App[None]):
    """Minimal host app for mounting GateWidget."""

    def compose(self) -> ComposeResult:
        yield GateWidget(id="user-gate")


# ---------------------------------------------------------------------------
# Mounting and default state
# ---------------------------------------------------------------------------


class TestMounting:
    @pytest.mark.asyncio
    async def test_mounts_inactive(self) -> None:
        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            assert widget.is_active is False
            assert widget.question == ""
            assert widget.choices is None

    @pytest.mark.asyncio
    async def test_inactive_widget_hidden(self) -> None:
        """Inactive widget does not have the 'active' CSS class."""
        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            assert not widget.has_class("active")


# ---------------------------------------------------------------------------
# show() / hide() lifecycle
# ---------------------------------------------------------------------------


class TestShowHide:
    @pytest.mark.asyncio
    async def test_show_activates_widget(self) -> None:
        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            widget.show("Which matters more?", ["Speed", "Safety"])
            await pilot.pause()
            assert widget.is_active is True
            assert widget.question == "Which matters more?"
            assert widget.choices == ["Speed", "Safety"]
            assert widget.has_class("active")

    @pytest.mark.asyncio
    async def test_show_without_choices(self) -> None:
        """Open-ended questions have no choices list."""
        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            widget.show("Tell me what you're thinking.", None)
            await pilot.pause()
            assert widget.is_active is True
            assert widget.choices is None

    @pytest.mark.asyncio
    async def test_hide_deactivates_widget(self) -> None:
        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            widget.show("Question?", ["A", "B"])
            await pilot.pause()
            widget.hide()
            await pilot.pause()
            assert widget.is_active is False
            assert widget.question == ""
            assert widget.choices is None
            assert not widget.has_class("active")

    @pytest.mark.asyncio
    async def test_show_replaces_previous_question(self) -> None:
        """Successive show() calls replace the prior question."""
        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            widget.show("First?", ["yes", "no"])
            await pilot.pause()
            widget.show("Second?", ["foo", "bar", "baz"])
            await pilot.pause()
            assert widget.question == "Second?"
            assert widget.choices == ["foo", "bar", "baz"]

    @pytest.mark.asyncio
    async def test_rendered_text_contains_question_and_choices(self) -> None:
        """Rendered content includes the question and numbered choices."""
        from textual.widgets import Static

        async with GateApp().run_test() as pilot:
            widget = pilot.app.query_one(GateWidget)
            widget.show("What next?", ["Continue", "Stop"])
            await pilot.pause()
            q_static = widget.query_one("#gate-question", Static)
            c_static = widget.query_one("#gate-choices", Static)
            q_text = q_static.render()
            c_text = c_static.render()
            assert "What next?" in q_text.plain
            assert "Continue" in c_text.plain
            assert "Stop" in c_text.plain
            assert "1" in c_text.plain
            assert "2" in c_text.plain


# ---------------------------------------------------------------------------
# resolve_input() — number-to-label resolution
# ---------------------------------------------------------------------------


class TestResolveInput:
    def _widget(self, choices: list[str] | None) -> GateWidget:
        w = GateWidget()
        w._choices = choices
        return w

    def test_resolves_bare_number_to_label(self) -> None:
        w = self._widget(["Speed", "Safety", "Other"])
        assert w.resolve_input("2") == "Safety"

    def test_resolves_first_choice(self) -> None:
        w = self._widget(["Speed", "Safety"])
        assert w.resolve_input("1") == "Speed"

    def test_resolves_last_choice(self) -> None:
        w = self._widget(["A", "B", "C"])
        assert w.resolve_input("3") == "C"

    def test_resolves_with_leading_and_trailing_whitespace(self) -> None:
        w = self._widget(["A", "B"])
        assert w.resolve_input("  2  ") == "B"

    def test_zero_returns_raw(self) -> None:
        """0 is out of range (choices are 1-indexed)."""
        w = self._widget(["A", "B"])
        assert w.resolve_input("0") == "0"

    def test_out_of_range_returns_raw(self) -> None:
        w = self._widget(["A", "B"])
        assert w.resolve_input("99") == "99"

    def test_negative_returns_raw(self) -> None:
        w = self._widget(["A", "B"])
        assert w.resolve_input("-1") == "-1"

    def test_non_numeric_returns_raw(self) -> None:
        w = self._widget(["A", "B"])
        assert w.resolve_input("some free text") == "some free text"

    def test_number_with_trailing_text_returns_raw(self) -> None:
        """'2 because...' is free-form, not a bare number."""
        w = self._widget(["A", "B"])
        assert w.resolve_input("2 because reasons") == "2 because reasons"

    def test_no_choices_returns_raw(self) -> None:
        """Open-ended questions pass input through unchanged."""
        w = self._widget(None)
        assert w.resolve_input("1") == "1"
        assert w.resolve_input("hello") == "hello"

    def test_empty_choices_returns_raw(self) -> None:
        w = self._widget([])
        assert w.resolve_input("1") == "1"

    def test_empty_input_with_choices_returns_raw(self) -> None:
        w = self._widget(["A", "B"])
        assert w.resolve_input("") == ""

    def test_leading_zero_resolves(self) -> None:
        """'01' parses as int 1 → first choice."""
        w = self._widget(["A", "B"])
        assert w.resolve_input("01") == "A"
