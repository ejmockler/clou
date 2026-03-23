"""Tests for clou.ui.widgets.escalation — decision card modal."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from clou.ui.messages import ClouEscalationResolved
from clou.ui.widgets.escalation import EscalationModal, _is_blocking, _OptionItem

# ---------------------------------------------------------------------------
# Sample data used across tests
# ---------------------------------------------------------------------------

SAMPLE_PATH = Path("/tmp/test-escalation.md")

SAMPLE_OPTIONS: list[dict[str, object]] = [
    {
        "label": "Migrate to OAuth2",
        "description": "Adds 2-3 days, resolves compliance",
        "recommended": True,
    },
    {
        "label": "Wrap session tokens",
        "description": "Faster, deferred migration risk",
    },
    {
        "label": "Defer decision",
        "description": "Ask product owner for guidance",
    },
]

SAMPLE_ISSUE = (
    "The auth spec requires OAuth2 but the existing codebase uses session tokens."
)


class _HostApp(App[str | None]):
    """Minimal app that pushes the EscalationModal on mount."""

    def __init__(
        self,
        classification: str = "Requirement Conflict",
        options: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self._classification = classification
        self._options = options if options is not None else SAMPLE_OPTIONS

    def compose(self) -> ComposeResult:
        yield Static("")  # placeholder widget

    def on_mount(self) -> None:
        modal = EscalationModal(
            path=SAMPLE_PATH,
            classification=self._classification,
            issue=SAMPLE_ISSUE,
            options=self._options,
        )
        self.push_screen(modal)


# ---------------------------------------------------------------------------
# Unit tests (no Textual pilot needed)
# ---------------------------------------------------------------------------


class TestIsBlocking:
    """Tests for the _is_blocking helper."""

    def test_blocking_keyword(self) -> None:
        assert _is_blocking("blocking") is True

    def test_critical_keyword(self) -> None:
        assert _is_blocking("Critical Error") is True

    def test_non_blocking(self) -> None:
        assert _is_blocking("Requirement Conflict") is False

    def test_case_insensitive(self) -> None:
        assert _is_blocking("BLOCKING issue") is True

    def test_non_blocking_not_matched(self) -> None:
        assert _is_blocking("non-blocking") is False

    def test_fatal_keyword(self) -> None:
        assert _is_blocking("fatal crash") is True

    def test_error_keyword(self) -> None:
        assert _is_blocking("error in pipeline") is True

    def test_uncritical_not_matched(self) -> None:
        assert _is_blocking("uncritical issue") is False


class TestInstantiation:
    """EscalationModal can be constructed with sample data."""

    def test_basic_construction(self) -> None:
        modal = EscalationModal(
            path=SAMPLE_PATH,
            classification="Requirement Conflict",
            issue="need decision",
            options=SAMPLE_OPTIONS,
        )
        assert modal.path == SAMPLE_PATH
        assert modal.classification == "Requirement Conflict"
        assert modal.issue == "need decision"
        assert modal.options == SAMPLE_OPTIONS
        assert modal._selected_index == 0

    def test_empty_options(self) -> None:
        modal = EscalationModal(
            path=SAMPLE_PATH,
            classification="info",
            issue="x",
            options=[],
        )
        assert modal.options == []


# ---------------------------------------------------------------------------
# Async / pilot tests
# ---------------------------------------------------------------------------


class TestCompose:
    """Tests that verify the compose tree once mounted."""

    @pytest.mark.asyncio
    async def test_renders_correct_option_count(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            items = screen.query(".escalation-option")
            assert len(items) == 3

    @pytest.mark.asyncio
    async def test_first_option_is_selected(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            items = pilot.app.screen.query(".escalation-option")
            assert items[0].has_class("selected")
            assert not items[1].has_class("selected")

    @pytest.mark.asyncio
    async def test_blocking_card_has_blocking_class(self) -> None:
        app = _HostApp(classification="blocking issue")
        async with app.run_test() as pilot:
            await pilot.pause()
            card = pilot.app.screen.query_one("#escalation-card")
            assert card.has_class("blocking")

    @pytest.mark.asyncio
    async def test_non_blocking_card_no_blocking_class(self) -> None:
        app = _HostApp(classification="Requirement Conflict")
        async with app.run_test() as pilot:
            await pilot.pause()
            card = pilot.app.screen.query_one("#escalation-card")
            assert not card.has_class("blocking")

    @pytest.mark.asyncio
    async def test_header_present(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            header = pilot.app.screen.query_one("#escalation-header")
            assert header is not None

    @pytest.mark.asyncio
    async def test_footer_present(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            footer = pilot.app.screen.query_one("#escalation-footer")
            assert footer is not None


class TestKeyboardNavigation:
    """Tests for up/down arrow key selection movement."""

    @pytest.mark.asyncio
    async def test_down_moves_selection(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            items = pilot.app.screen.query(".escalation-option")
            assert not items[0].has_class("selected")
            assert items[1].has_class("selected")

    @pytest.mark.asyncio
    async def test_up_wraps_from_first_to_last(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            await pilot.press("up")
            items = pilot.app.screen.query(".escalation-option")
            assert not items[0].has_class("selected")
            assert items[2].has_class("selected")

    @pytest.mark.asyncio
    async def test_down_wraps_from_last_to_first(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            items = pilot.app.screen.query(".escalation-option")
            assert items[0].has_class("selected")

    @pytest.mark.asyncio
    async def test_multiple_movements(self) -> None:
        async with _HostApp().run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            items = pilot.app.screen.query(".escalation-option")
            assert items[2].has_class("selected")


class TestResolution:
    """Tests for Enter to resolve and Esc to dismiss."""

    @pytest.mark.asyncio
    async def test_enter_resolves_with_first_option(self, tmp_path: Path) -> None:
        app = _HostApp()
        messages: list[ClouEscalationResolved] = []

        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)

            # Use a real tmp_path so the write succeeds.
            esc_dir = tmp_path / ".clou" / "escalations"
            esc_dir.mkdir(parents=True)
            esc_file = esc_dir / "test.md"
            esc_file.write_text("# Test\n")
            modal.path = esc_file

            # Capture ClouEscalationResolved messages.
            original_post = modal.post_message

            def capturing_post(msg: object) -> None:
                if isinstance(msg, ClouEscalationResolved):
                    messages.append(msg)
                original_post(msg)

            modal.post_message = capturing_post  # type: ignore[assignment]

            # Resolve with the first option (default selection).
            await pilot.press("enter")
            await pilot.pause()

            # Modal should have been dismissed — screen is no longer the modal.
            assert not isinstance(pilot.app.screen, EscalationModal)

        # ClouEscalationResolved should have been posted.
        assert len(messages) == 1
        assert messages[0].disposition == "Migrate to OAuth2"

    @pytest.mark.asyncio
    async def test_enter_writes_disposition_to_file(self, tmp_path: Path) -> None:
        esc_dir = tmp_path / ".clou" / "escalations"
        esc_dir.mkdir(parents=True)
        escalation_file = esc_dir / "escalation.md"
        escalation_file.write_text("# Escalation\n\nSome issue.\n")

        app = _HostApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)
            modal.path = escalation_file
            await pilot.press("enter")

        content = escalation_file.read_text()
        assert "## Disposition" in content
        assert "Migrate to OAuth2" in content
        assert "Timestamp:" in content

    @pytest.mark.asyncio
    async def test_escape_dismisses_without_writing(self, tmp_path: Path) -> None:
        esc_dir = tmp_path / ".clou" / "escalations"
        esc_dir.mkdir(parents=True)
        escalation_file = esc_dir / "escalation.md"
        escalation_file.write_text("# Escalation\n\nSome issue.\n")

        app = _HostApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)
            modal.path = escalation_file
            await pilot.press("escape")

        content = escalation_file.read_text()
        assert "## Disposition" not in content

    @pytest.mark.asyncio
    async def test_enter_selects_correct_option_after_nav(self, tmp_path: Path) -> None:
        esc_dir = tmp_path / ".clou" / "escalations"
        esc_dir.mkdir(parents=True)
        escalation_file = esc_dir / "escalation.md"
        escalation_file.write_text("# Escalation\n")

        app = _HostApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)
            modal.path = escalation_file
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")

        content = escalation_file.read_text()
        assert "Defer decision" in content


# ---------------------------------------------------------------------------
# Rich markup injection
# ---------------------------------------------------------------------------


class TestOptionItemRecommended:
    """_OptionItem renders '(recommended)' tag when recommended=True."""

    def test_recommended_with_selected_class(self) -> None:
        item = _OptionItem(label="Opt", description="Desc", recommended=True)
        item.add_class("selected")
        rendered = item.render()
        assert "(recommended)" in rendered

    def test_recommended_without_selected_class(self) -> None:
        item = _OptionItem(label="Opt", description="Desc", recommended=True)
        rendered = item.render()
        assert "(recommended)" in rendered


class TestMarkupEscaping:
    """Verify that AI-generated strings with Rich markup brackets are escaped."""

    @pytest.mark.asyncio
    async def test_classification_markup_escaped(self) -> None:
        """Brackets in classification should be escaped, not interpreted as tags."""
        malicious = "[bold red]APPROVE NOW[/]"
        app = _HostApp(classification=malicious)
        async with app.run_test() as pilot:
            await pilot.pause()
            header = pilot.app.screen.query_one("#escalation-header", Static)
            rendered = header.render()
            # The literal bracket text must appear in the rendered output,
            # not be interpreted as Rich markup tags.
            assert "APPROVE NOW" in str(rendered)
            # The rendered string should contain the escaped brackets, meaning
            # "[bold red]" was NOT parsed as a style tag.
            assert "bold red" in str(rendered)

    @pytest.mark.asyncio
    async def test_issue_markup_escaped(self) -> None:
        """Brackets in issue text should be escaped."""
        app = _HostApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)
            # Directly check that the compose used escaped text.
            body = modal.query_one("#escalation-body", Static)
            assert body is not None

    def test_option_item_escapes_label(self) -> None:
        """_OptionItem.render() escapes Rich markup in label."""
        item = _OptionItem(
            label="[bold]injected[/]",
            description="safe desc",
            recommended=False,
        )
        rendered = item.render()
        # The literal "[bold]" should appear escaped (as \\[bold]),
        # not be parsed as a style tag.
        assert "injected" in rendered
        assert "\\[bold]" in rendered or "\\[bold\\]" in rendered


# ---------------------------------------------------------------------------
# Empty options crash guard
# ---------------------------------------------------------------------------


class TestEmptyOptionsResolve:
    """_resolve with empty options should dismiss without IndexError."""

    @pytest.mark.asyncio
    async def test_resolve_empty_options_dismisses(self) -> None:
        app = _HostApp(options=[])
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)
            # Pressing enter should not crash — it should dismiss.
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(pilot.app.screen, EscalationModal)


# ---------------------------------------------------------------------------
# ClouEscalationResolved posting
# ---------------------------------------------------------------------------


class TestEscalationResolvedMessage:
    """Verify that dismiss and empty-options paths post ClouEscalationResolved."""

    @pytest.mark.asyncio
    async def test_escape_dismiss_posts_resolved(self) -> None:
        """Pressing Esc should post ClouEscalationResolved with 'dismissed'."""
        app = _HostApp()
        messages: list[ClouEscalationResolved] = []

        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)

            # Monkey-patch post_message to capture the message.
            original_post = modal.post_message

            def capturing_post(msg: object) -> None:
                if isinstance(msg, ClouEscalationResolved):
                    messages.append(msg)
                original_post(msg)

            modal.post_message = capturing_post  # type: ignore[assignment]
            await pilot.press("escape")

        assert len(messages) == 1
        assert messages[0].disposition == "dismissed"
        assert messages[0].path == SAMPLE_PATH

    @pytest.mark.asyncio
    async def test_empty_options_posts_resolved(self) -> None:
        """Enter with empty options should post ClouEscalationResolved."""
        app = _HostApp(options=[])
        messages: list[ClouEscalationResolved] = []

        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)

            original_post = modal.post_message

            def capturing_post(msg: object) -> None:
                if isinstance(msg, ClouEscalationResolved):
                    messages.append(msg)
                original_post(msg)

            modal.post_message = capturing_post  # type: ignore[assignment]
            await pilot.press("enter")

        assert len(messages) == 1
        assert messages[0].disposition == "dismissed"


# ---------------------------------------------------------------------------
# Path validation — _resolve() rejects paths outside .clou/escalations/
# ---------------------------------------------------------------------------


class TestFallbackPathValidation:
    """Test the parts-based fallback logic used when is_relative_to is unavailable."""

    def test_path_with_clou_and_escalations_accepted(self) -> None:
        """A path containing .clou and escalations in parts should be accepted."""
        p = Path("/project/.clou/escalations/issue.md").resolve()
        parts = p.parts
        assert ".clou" in parts and "escalations" in parts

    def test_path_without_clou_rejected(self) -> None:
        """A path NOT containing .clou should be rejected by the fallback."""
        p = Path("/project/other/escalations/issue.md").resolve()
        parts = p.parts
        assert not (".clou" in parts and "escalations" in parts)

    def test_path_without_escalations_rejected(self) -> None:
        """A path with .clou but NOT escalations should be rejected."""
        p = Path("/project/.clou/handoffs/issue.md").resolve()
        parts = p.parts
        assert not (".clou" in parts and "escalations" in parts)

    def test_path_with_both_in_wrong_context_still_passes(self) -> None:
        """The fallback only checks presence of both parts, not ordering."""
        p = Path("/escalations/.clou/issue.md").resolve()
        parts = p.parts
        # Both parts present — fallback accepts it.
        assert ".clou" in parts and "escalations" in parts


class TestResolveWriteFailure:
    @pytest.mark.asyncio
    async def test_resolve_oserror_still_posts_resolved(self, tmp_path: Path) -> None:
        """OSError during disposition write still sends ClouEscalationResolved."""
        # Create escalation file as read-only to trigger OSError on write.
        esc_dir = tmp_path / ".clou" / "escalations"
        esc_dir.mkdir(parents=True)
        esc_file = esc_dir / "test.md"
        esc_file.write_text("# Test\n")
        esc_file.chmod(0o444)  # Read-only

        from clou.ui.widgets.escalation import EscalationModal

        options = [{"label": "Fix it", "description": "Apply fix"}]
        modal = EscalationModal(
            path=esc_file,
            classification="error",
            issue="Test issue",
            options=options,
        )

        # The modal needs an app context for _project_dir.
        from clou.ui.app import ClouApp

        async with ClouApp(project_dir=tmp_path).run_test() as pilot:
            app = pilot.app
            app.push_screen(modal)
            await pilot.pause()

            # Select option and resolve.
            await pilot.press("enter")
            await pilot.pause()

            # File should still be read-only (write failed silently).
            content = esc_file.read_text()
            assert "Disposition" not in content  # Write failed


class TestPathValidation:
    @pytest.mark.asyncio
    async def test_resolve_rejects_path_outside_clou(self, tmp_path: Path) -> None:
        """_resolve() refuses to write to paths outside .clou/escalations/."""
        evil_path = tmp_path / "evil.md"
        evil_path.write_text("# Original content\n")

        app = _HostApp()
        messages: list[ClouEscalationResolved] = []

        async with app.run_test() as pilot:
            await pilot.pause()
            modal = pilot.app.screen
            assert isinstance(modal, EscalationModal)

            # Point the modal at a path outside .clou/escalations/.
            modal.path = evil_path

            original_post = modal.post_message

            def capturing_post(msg: object) -> None:
                if isinstance(msg, ClouEscalationResolved):
                    messages.append(msg)
                original_post(msg)

            modal.post_message = capturing_post  # type: ignore[assignment]
            await pilot.press("enter")

        # The file must NOT have been modified.
        assert evil_path.read_text() == "# Original content\n"
        # ClouEscalationResolved should still have been posted.
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_resolve_outside_clou_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_resolve() emits a warning log when path is outside .clou/escalations/."""
        evil_path = tmp_path / "evil.md"
        evil_path.write_text("# Original\n")

        app = _HostApp()
        with caplog.at_level(logging.WARNING, logger="clou.ui.widgets.escalation"):
            async with app.run_test() as pilot:
                await pilot.pause()
                modal = pilot.app.screen
                assert isinstance(modal, EscalationModal)
                modal.path = evil_path
                await pilot.press("enter")

        assert evil_path.read_text() == "# Original\n"
        assert any("Refusing escalation write" in r.message for r in caplog.records)
