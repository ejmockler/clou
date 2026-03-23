"""Tests for clou.ui.widgets.handoff — handoff document parser and renderer."""

from __future__ import annotations

from clou.ui.widgets.handoff import (
    HandoffWidget,
    SectionType,
    parse_handoff,
    render_handoff,
)

# ---------------------------------------------------------------------------
# Sample markdown
# ---------------------------------------------------------------------------

SAMPLE_HANDOFF = """\
# Handoff: auth-system

## Summary
Brief description of what was accomplished.

## Running Services
- http://localhost:3000 — frontend dev server
- http://localhost:8080 — API server

## Walk-through
1. Open the browser to http://localhost:3000
2. Click "Login" and use test credentials
3. Verify the dashboard loads with sample data

## Verification Results
- \u2705 All 47 tests passing
- \u2705 Type checking clean
- \u274c Lighthouse score 72 (target: 80)

## Known Limitations
- OAuth flow not implemented (stubbed)
- Mobile responsive layout incomplete

## Files Changed
- src/auth/login.py (new)
- src/api/routes.py (modified)
"""


# ---------------------------------------------------------------------------
# parse_handoff
# ---------------------------------------------------------------------------


class TestParseHandoff:
    """Tests for the markdown parser."""

    def test_extracts_all_sections(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        # Title section + 6 named sections
        assert len(sections) >= 6

    def test_summary_section_present(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        types = [s.section_type for s in sections]
        assert SectionType.SUMMARY in types

    def test_running_services_section(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        types = [s.section_type for s in sections]
        assert SectionType.RUNNING_SERVICES in types

    def test_walkthrough_section(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        types = [s.section_type for s in sections]
        assert SectionType.WALKTHROUGH in types

    def test_verification_section(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        types = [s.section_type for s in sections]
        assert SectionType.VERIFICATION in types

    def test_limitations_section(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        types = [s.section_type for s in sections]
        assert SectionType.LIMITATIONS in types

    def test_files_changed_section(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        types = [s.section_type for s in sections]
        assert SectionType.FILES_CHANGED in types

    def test_section_ordering_preserved(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        # Filter to named sections (skip title-only summary).
        named = [s for s in sections if s.title]
        titles = [s.title for s in named]
        assert titles.index("Summary") < titles.index("Running Services")
        assert titles.index("Running Services") < titles.index("Walk-through")

    def test_section_body_content(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        summary = next(
            s
            for s in sections
            if s.section_type == SectionType.SUMMARY and s.title == "Summary"
        )
        assert "accomplished" in summary.body

    def test_url_detection_in_services(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        services = next(
            s for s in sections if s.section_type == SectionType.RUNNING_SERVICES
        )
        assert "http://localhost:3000" in services.body
        assert "http://localhost:8080" in services.body

    def test_pass_fail_indicators(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        verification = next(
            s for s in sections if s.section_type == SectionType.VERIFICATION
        )
        assert "\u2705" in verification.body
        assert "\u274c" in verification.body

    def test_empty_markdown(self) -> None:
        sections = parse_handoff("")
        assert sections == []

    def test_only_title(self) -> None:
        sections = parse_handoff("# Handoff: test\n")
        assert len(sections) == 1
        assert sections[0].title == "Handoff: test"

    def test_unknown_section_type(self) -> None:
        text = "## Some Custom Section\nContent here."
        sections = parse_handoff(text)
        assert len(sections) == 1
        assert sections[0].section_type == SectionType.UNKNOWN

    def test_malformed_markdown_no_crash(self) -> None:
        text = "### Not a section\nRandom text\n---\nMore text"
        sections = parse_handoff(text)
        # Should parse without raising.
        assert isinstance(sections, list)


# ---------------------------------------------------------------------------
# render_handoff
# ---------------------------------------------------------------------------


class TestRenderHandoff:
    """Tests for Rich Text rendering."""

    def test_render_produces_text(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        result = render_handoff(sections)
        assert len(result.plain) > 0

    def test_render_contains_section_titles(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        result = render_handoff(sections)
        plain = result.plain
        assert "Summary" in plain
        assert "Running Services" in plain

    def test_render_contains_urls(self) -> None:
        sections = parse_handoff(SAMPLE_HANDOFF)
        result = render_handoff(sections)
        assert "http://localhost:3000" in result.plain

    def test_render_empty_sections(self) -> None:
        result = render_handoff([])
        assert result.plain == ""


# ---------------------------------------------------------------------------
# HandoffWidget
# ---------------------------------------------------------------------------


class TestHandoffWidget:
    """Tests for the widget itself."""

    def test_construction_empty(self) -> None:
        w = HandoffWidget()
        assert w._content == ""
        assert w._sections == []

    def test_construction_with_content(self) -> None:
        w = HandoffWidget(content=SAMPLE_HANDOFF)
        assert len(w._sections) >= 6

    def test_parse_handoff_populates_sections(self) -> None:
        """Manually assigning parsed content populates sections."""
        w = HandoffWidget()
        w._content = SAMPLE_HANDOFF
        w._sections = parse_handoff(SAMPLE_HANDOFF)
        assert len(w._sections) >= 6

    def test_parse_handoff_replaces_sections(self) -> None:
        """Re-parsing with new content replaces existing sections."""
        w = HandoffWidget(content=SAMPLE_HANDOFF)
        new_text = "## Summary\nNew content."
        w._content = new_text
        w._sections = parse_handoff(new_text)
        assert len(w._sections) == 1
        assert "New content" in w._sections[0].body
