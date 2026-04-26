"""Tests for the supervisor-tier MCP tool ``clou_resolve_escalation``.

The supervisor tier is the disposition owner for escalation files: a
coordinator files via ``clou_file_escalation``, the supervisor closes
via ``clou_resolve_escalation``.  Direct Write to
``escalations/*.md`` is denied for every tier (DB-21 remolding,
milestone 41), so this tool is the supervisor's sanctioned path.

F3 coverage:
    - happy path (updates existing Disposition section)
    - legacy-body byte-preservation above ``## Disposition`` (R7)
    - atomic tmp-rename write (no tmp file leaks on success)
    - status enum validation (invalid rejected)
    - milestone / filename validation (traversal rejected, .md enforced)
    - missing file handled (structured is_error, not raised)
    - missing Disposition section → appended (legacy files)
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture: mirrors tests/test_coordinator_tools.py::coord_tools so
# handlers can be invoked directly without the MCP transport.
# ---------------------------------------------------------------------------


def _find_tool(tools: list, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found")


@pytest.fixture
def sup_tools(tmp_path: Path):
    pytest.importorskip("claude_agent_sdk")
    from clou.supervisor_tools import _build_supervisor_tools

    (tmp_path / ".clou" / "milestones" / "test-ms" / "escalations").mkdir(
        parents=True,
    )
    return tmp_path, _build_supervisor_tools(tmp_path)


def _seed_escalation(
    tmp_path: Path,
    filename: str,
    body: str,
) -> Path:
    """Write *body* to the test milestone's escalations dir and return path."""
    esc_dir = (
        tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
    )
    path = esc_dir / filename
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestResolveEscalationToolSchema:
    """JSON Schema advertised to the LLM for clou_resolve_escalation."""

    def test_tool_is_exposed(self, sup_tools) -> None:
        _, tools = sup_tools
        # Supervisor currently exposes resolve_escalation and
        # list_proposals (C3 coordinator->supervisor pathway).
        tool_names = {getattr(t, "name", "") for t in tools}
        assert "clou_resolve_escalation" in tool_names
        t = _find_tool(tools, "clou_resolve_escalation")
        assert t is not None

    def test_schema_shape(self, sup_tools) -> None:
        _, tools = sup_tools
        t = _find_tool(tools, "clou_resolve_escalation")
        schema = t.input_schema
        assert schema["type"] == "object"
        required = set(schema["required"])
        # notes is optional, status + milestone + filename are required.
        assert required == {"milestone", "filename", "status"}
        for field in ("milestone", "filename", "status", "notes"):
            assert field in schema["properties"]
            assert schema["properties"][field]["type"] == "string"


# ---------------------------------------------------------------------------
# Happy path + legacy preservation
# ---------------------------------------------------------------------------


class TestResolveEscalationHappyPath:

    @pytest.mark.asyncio
    async def test_updates_existing_disposition(self, sup_tools) -> None:
        """Resolve a canonical escalation → Disposition section updated."""
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        body = (
            "# Escalation: Example\n"
            "\n"
            "**Filed:** 2026-04-21T12:00:00+00:00\n"
            "**Classification:** blocking\n"
            "\n"
            "## Context\nctx body\n"
            "\n"
            "## Issue\nissue body\n"
            "\n"
            "## Options\n- **A:** first\n\n"
            "## Disposition\nstatus: open\n"
        )
        _seed_escalation(tmp_path, "20260421-120000-example.md", body)

        result = await handler({
            "milestone": "test-ms",
            "filename": "20260421-120000-example.md",
            "status": "resolved",
            "notes": "Picked option A.",
        })
        assert "is_error" not in result or not result["is_error"]
        written = Path(result["written"])
        assert written.exists()
        text = written.read_text(encoding="utf-8")

        # New disposition block appears with correct status and notes.
        assert "## Disposition" in text
        assert "status: resolved" in text
        assert "Picked option A." in text
        # Old ``status: open`` line is gone.
        assert "status: open" not in text
        # Prior sections still intact.
        assert "## Context" in text
        assert "ctx body" in text
        assert "## Issue" in text
        assert "## Options" in text

    @pytest.mark.asyncio
    async def test_legacy_body_byte_preserved(self, sup_tools) -> None:
        """R7: bytes above ``## Disposition`` preserved exactly.

        Legacy escalation files may have non-canonical headings, extra
        whitespace, or custom sections above Disposition.  The resolve
        tool must NOT rewrite them — byte-for-byte preservation is the
        whole point of routing through this tool instead of re-rendering.
        """
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        # Intentionally non-canonical: custom heading style, odd
        # whitespace, legacy fields that the canonical renderer would
        # drop.  Every byte above ## Disposition must survive.
        legacy = (
            "# Legacy Escalation Format\n"
            "\n"
            "Filed: 2025-12-01\n"
            "Legacy-Priority: urgent\n"
            "\n"
            "### Free-form Section\n"
            "Some paragraph with  double  spaces  and  trailing  whitespace.   \n"
            "\n"
            "### Another Free-form\n"
            "- item 1\n"
            "- item 2 with *markdown*\n"
            "\n"
            "## Disposition\nstatus: open\n"
        )
        path = _seed_escalation(tmp_path, "legacy-old.md", legacy)
        # Record the exact prefix bytes (everything up to ``## Disposition``).
        split_marker = "## Disposition"
        legacy_prefix = legacy[: legacy.index(split_marker)]

        await handler({
            "milestone": "test-ms",
            "filename": "legacy-old.md",
            "status": "overridden",
            "notes": "Legacy record closed.",
        })
        after = path.read_text(encoding="utf-8")
        # Prefix is byte-identical.
        assert after[: len(legacy_prefix)] == legacy_prefix
        # Disposition section updated.
        assert "status: overridden" in after
        assert "Legacy record closed." in after

    @pytest.mark.asyncio
    async def test_missing_disposition_appended(self, sup_tools) -> None:
        """Legacy file without ``## Disposition`` → section appended."""
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        body = (
            "# No Disposition Yet\n"
            "\n"
            "Some body content.\n"
        )
        path = _seed_escalation(tmp_path, "no-disp.md", body)
        result = await handler({
            "milestone": "test-ms",
            "filename": "no-disp.md",
            "status": "resolved",
            "notes": "Closed.",
        })
        assert "is_error" not in result or not result["is_error"]
        text = path.read_text(encoding="utf-8")
        assert "## Disposition" in text
        assert "status: resolved" in text
        assert text.startswith("# No Disposition Yet")

    @pytest.mark.asyncio
    async def test_notes_optional(self, sup_tools) -> None:
        """Notes may be omitted; disposition still written with status."""
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        _seed_escalation(
            tmp_path, "no-notes.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        result = await handler({
            "milestone": "test-ms",
            "filename": "no-notes.md",
            "status": "resolved",
        })
        assert "is_error" not in result or not result["is_error"]
        text = Path(result["written"]).read_text(encoding="utf-8")
        assert "status: resolved" in text


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


class TestResolveEscalationAtomicWrite:

    @pytest.mark.asyncio
    async def test_no_tmp_file_after_success(self, sup_tools) -> None:
        """Successful resolve leaves no ``.tmp`` artefact behind."""
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        path = _seed_escalation(
            tmp_path, "atomic.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        await handler({
            "milestone": "test-ms",
            "filename": "atomic.md",
            "status": "resolved",
        })
        # The tmp file lives next to target and must be gone after
        # os.replace(tmp, target).
        tmp_file = path.with_name(path.name + ".tmp")
        assert not tmp_file.exists()
        # Target exists and is updated.
        assert path.exists()
        assert "status: resolved" in path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_unrelated_tmp_file_preserved(
        self, sup_tools,
    ) -> None:
        """F6 (cycle 2): an unrelated ``.tmp`` file is NEVER deleted.

        Prior cycles used a fixed ``{name}.tmp`` path and unconditionally
        deleted it before every write.  That anti-pattern lost any
        unrelated file sitting at that exact name (a concurrent tool
        run, a developer workspace artefact, a leftover from another
        process).  F6 replaces the fixed name with
        :func:`tempfile.NamedTemporaryFile` so each call gets a unique
        temp file --- the pre-existing ``{name}.tmp`` MUST survive.
        """
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        path = _seed_escalation(
            tmp_path, "stale.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        # Plant an unrelated {name}.tmp file.  Under F6 this must
        # survive --- the atomic writer never touches files it didn't
        # create.
        unrelated_tmp = path.with_name(path.name + ".tmp")
        unrelated_tmp.write_text(
            "unrelated file content", encoding="utf-8",
        )

        result = await handler({
            "milestone": "test-ms",
            "filename": "stale.md",
            "status": "resolved",
        })
        assert "is_error" not in result or not result["is_error"]
        # F6: unrelated tmp survived.  The atomic writer uses a unique
        # ``NamedTemporaryFile`` path (``.{name}.XXXXXXXX.tmp``) so no
        # collision with the caller-chosen ``{name}.tmp`` path.
        assert unrelated_tmp.exists()
        assert (
            unrelated_tmp.read_text(encoding="utf-8")
            == "unrelated file content"
        )
        # Target was updated.
        assert "status: resolved" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Validation surface — all failures are structured is_error
# ---------------------------------------------------------------------------


class TestResolveEscalationValidation:

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, sup_tools) -> None:
        """Status outside the resolution set → structured is_error."""
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        _seed_escalation(
            tmp_path, "x.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        result = await handler({
            "milestone": "test-ms",
            "filename": "x.md",
            "status": "bogus-status",
        })
        assert result.get("is_error")
        assert "status" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_empty_status_rejected(self, sup_tools) -> None:
        """Empty status → structured is_error."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "x.md",
            "status": "",
        })
        assert result.get("is_error")
        assert "status" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_valid_statuses_accepted(self, sup_tools) -> None:
        """All statuses in RESOLUTION_STATUSES are accepted."""
        from clou.supervisor_tools import RESOLUTION_STATUSES

        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        for i, s in enumerate(sorted(RESOLUTION_STATUSES)):
            _seed_escalation(
                tmp_path, f"s-{i}.md",
                "# E\n\n## Disposition\nstatus: open\n",
            )
            result = await handler({
                "milestone": "test-ms",
                "filename": f"s-{i}.md",
                "status": s,
            })
            assert "is_error" not in result or not result["is_error"], (
                f"status {s!r} unexpectedly rejected"
            )

    @pytest.mark.asyncio
    async def test_empty_milestone_rejected(self, sup_tools) -> None:
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "",
            "filename": "x.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        assert "milestone" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_invalid_milestone_rejected(self, sup_tools) -> None:
        """``validate_milestone_name`` rejects path traversal / odd chars."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "../evil",
            "filename": "x.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        assert "milestone" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_filename_with_separator_rejected(
        self, sup_tools,
    ) -> None:
        """Filenames containing path separators are rejected."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        for bad in ("a/b.md", "..\\x.md", "./relative.md"):
            result = await handler({
                "milestone": "test-ms",
                "filename": bad,
                "status": "resolved",
            })
            assert result.get("is_error"), f"{bad!r} should be rejected"

    @pytest.mark.asyncio
    async def test_filename_must_end_with_md(self, sup_tools) -> None:
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "x.txt",
            "status": "resolved",
        })
        assert result.get("is_error")
        assert ".md" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, sup_tools) -> None:
        """Target file doesn't exist → structured is_error."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "does-not-exist.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        assert "not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_notes_wrong_type_rejected(self, sup_tools) -> None:
        """Notes must be a string if supplied."""
        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        _seed_escalation(
            tmp_path, "bad-notes.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        result = await handler({
            "milestone": "test-ms",
            "filename": "bad-notes.md",
            "status": "resolved",
            "notes": 123,  # type: ignore[arg-type]
        })
        assert result.get("is_error")
        assert "notes" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Disposition span parser — exercised through the public handler but the
# unit behaviour is worth a small, direct check.
# ---------------------------------------------------------------------------


class TestFindDispositionSpan:

    def test_locates_heading(self) -> None:
        from clou.supervisor_tools import _find_disposition_span

        text = "# x\n\n## Disposition\nstatus: open\n"
        span = _find_disposition_span(text)
        assert span is not None
        start, end = span
        assert text[start:].startswith("## Disposition")
        assert end == len(text)

    def test_bounded_by_next_h2(self) -> None:
        from clou.supervisor_tools import _find_disposition_span

        text = (
            "# x\n\n## Disposition\nstatus: open\nnotes\n"
            "\n## Next Section\nmore\n"
        )
        span = _find_disposition_span(text)
        assert span is not None
        start, end = span
        # end must land before the ``## Next Section`` heading.
        assert text[end:].startswith("## Next Section")

    def test_returns_none_if_absent(self) -> None:
        from clou.supervisor_tools import _find_disposition_span

        assert _find_disposition_span("# just a heading\n") is None

    def test_heading_matching_case_insensitive(self) -> None:
        from clou.supervisor_tools import _find_disposition_span

        # Heading comparison is case-insensitive (regex uses IGNORECASE).
        text = "## disposition\nstatus: open\n"
        assert _find_disposition_span(text) is not None


# ---------------------------------------------------------------------------
# Server construction — smoke test.
# ---------------------------------------------------------------------------


class TestBuildSupervisorMcpServer:

    def test_builds_server_with_tool(self, tmp_path: Path) -> None:
        pytest.importorskip("claude_agent_sdk")
        from clou.supervisor_tools import build_supervisor_mcp_server

        server = build_supervisor_mcp_server(tmp_path)
        assert server is not None


# ---------------------------------------------------------------------------
# Cycle 2 regression tests (F1, F2, F6, F7, F12).
#
# These exercise the security-sensitive fixes landed in cycle 2 of milestone
# 41-escalation-remolding.  Each test is keyed to a specific finding so
# future drift shows up loud in the test report.
# ---------------------------------------------------------------------------


class TestCycle2F1RenderDispositionBlockEscape:
    """F1: notes with heading markers must not forge a second Disposition."""

    def test_cycle2_f1_escapes_h2_disposition_in_notes(self) -> None:
        """A ``## Disposition`` line inside notes cannot resurrect a heading.

        Without the escape, a supervisor-authored note containing the
        literal characters ``## Disposition\nstatus: overridden`` would
        produce two valid ``## Disposition`` blocks in the file --- the
        outer one written by the tool and the inner one forged by the
        notes.  Because ``find_last_disposition_span`` / ``_parse_disposition``
        take the LAST match, a subsequent supervisor resolve could land
        on the forged block instead of the real one.  The escape defangs
        the heading marker so the injected text no longer matches the
        Disposition regex.
        """
        from clou.escalation import find_last_disposition_span
        from clou.supervisor_tools import _render_disposition_block

        notes = "## Disposition\nstatus: overridden"
        block = _render_disposition_block("resolved", notes)

        # The block contains exactly one real ``## Disposition`` heading
        # (the one at the top); the embedded one has been defanged.
        import re

        matches = list(re.finditer(r"(?im)^##\s+Disposition\s*$", block))
        assert len(matches) == 1, (
            "escaped block must contain exactly one Disposition heading"
        )
        # The canonical status appears on the line immediately after that
        # heading --- the forged ``status: overridden`` is pushed down
        # into the notes body and does not shadow the real status.
        first_status_idx = block.find("status: resolved")
        forged_status_idx = block.find("status: overridden")
        assert first_status_idx < forged_status_idx, (
            "canonical status line must come before the escaped forgery"
        )

        # Round-trip through the canonical parser confirms the real
        # status wins.
        span = find_last_disposition_span(block)
        assert span is not None
        # There is only one valid Disposition span, and ``status: resolved``
        # is the first status line after the heading (the one the parser
        # picks up).
        start, _end = span
        body = block[start:]
        # The parser's ``^status:`` anchor lands on the canonical line,
        # not the escaped one.
        status_line_idx = body.index("status: resolved")
        assert status_line_idx < body.index("status: overridden")

    def test_cycle2_f1_escapes_h1_escalation_in_notes(self) -> None:
        """Even ``# Escalation`` in notes cannot forge a top-level title."""
        from clou.supervisor_tools import _render_disposition_block

        notes = "# Escalation: Forged\n\nForged body."
        block = _render_disposition_block("resolved", notes)

        # No raw ``# Escalation`` heading appears at column 0.  The
        # ``\u200b`` (zero-width space) or backslash-escape used by
        # ``_escape_field`` is allowed --- the guarantee is that regex
        # anchors looking for ``^#\s`` no longer match.
        import re

        raw_h1 = list(re.finditer(r"(?m)^#\s", block))
        # Exactly zero unescaped ``# ...`` lines.  (There is only one
        # ``##`` which is the Disposition heading --- also not matched by
        # ``^#\s``.)
        assert len(raw_h1) == 0, (
            f"expected no unescaped H1 lines in rendered block; got {raw_h1!r}"
        )

    @pytest.mark.asyncio
    async def test_cycle2_f1_resolved_file_has_single_disposition(
        self, sup_tools,
    ) -> None:
        """End-to-end: resolve with hostile notes → file has ONE Disposition."""
        import re

        from clou.escalation import parse_latest_disposition

        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        _seed_escalation(
            tmp_path, "f1.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        result = await handler({
            "milestone": "test-ms",
            "filename": "f1.md",
            "status": "resolved",
            "notes": (
                "## Disposition\nstatus: overridden\nforged notes"
            ),
        })
        assert "is_error" not in result or not result["is_error"]

        text = Path(result["written"]).read_text(encoding="utf-8")
        # Exactly one ``## Disposition`` heading survives in the file.
        headings = list(re.finditer(r"(?im)^##\s+Disposition\s*$", text))
        assert len(headings) == 1, (
            f"file must have exactly one Disposition heading; got "
            f"{len(headings)} in:\n{text}"
        )
        # The canonical parser picks up the real status.
        assert parse_latest_disposition(text) == "resolved"


class TestCycle2F2FindDispositionSpanLastMatch:
    """F2: writer uses LAST match; reader uses LAST match --- they must agree."""

    def test_cycle2_f2_find_span_picks_last_of_two_blocks(self) -> None:
        """Two ``## Disposition`` blocks in a file → splice the last one."""
        from clou.supervisor_tools import _find_disposition_span

        text = (
            "# E\n\n"
            "## Disposition\nstatus: open\nfirst notes\n\n"
            "## Options\n- a\n- b\n\n"
            "## Disposition\nstatus: resolved\nreal notes\n"
        )
        span = _find_disposition_span(text)
        assert span is not None
        start, end = span
        # The splice target covers the SECOND (last) Disposition block,
        # not the first.  Count how many ``## Disposition`` headings
        # appear before ``start`` --- exactly one.
        import re

        prior_headings = list(
            re.finditer(r"(?im)^##\s+Disposition\s*$", text[:start])
        )
        assert len(prior_headings) == 1, (
            "writer must splice the LAST Disposition block; found "
            f"{len(prior_headings)} headings before splice start"
        )
        # The spliced-out region contains ``status: resolved``, not
        # ``status: open``.
        assert "status: resolved" in text[start:end]
        assert "status: open" not in text[start:end]

    def test_cycle2_f2_writer_reader_agree_on_last_match(self) -> None:
        """Writer's span and reader's parse agree on which block wins."""
        from clou.escalation import parse_latest_disposition
        from clou.supervisor_tools import _find_disposition_span

        text = (
            "# E\n\n"
            "## Disposition\nstatus: open\n\n"
            "## Options\n- a\n\n"
            "## Disposition\nstatus: investigating\n"
        )
        span = _find_disposition_span(text)
        assert span is not None
        start, _end = span
        # The reader picks ``investigating`` (the last block); the
        # writer would splice starting at ``start`` --- the last block's
        # heading.  Both agree.
        assert parse_latest_disposition(text) == "investigating"
        # The span's start lands on the LAST Disposition heading.
        assert text[start:].startswith("## Disposition")
        # And there is no further Disposition heading after ``start``.
        import re

        later = list(
            re.finditer(
                r"(?im)^##\s+Disposition\s*$", text[start + 1 :]
            )
        )
        assert len(later) == 0

    @pytest.mark.asyncio
    async def test_cycle2_f2_resolve_updates_last_disposition(
        self, sup_tools,
    ) -> None:
        """End-to-end: resolve on a file with two Dispositions updates the last."""
        from clou.escalation import parse_latest_disposition

        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        body = (
            "# E\n\n"
            "## Disposition\nstatus: open\nfirst\n\n"
            "## Options\n- a\n\n"
            "## Disposition\nstatus: investigating\nsecond\n"
        )
        path = _seed_escalation(tmp_path, "f2.md", body)

        result = await handler({
            "milestone": "test-ms",
            "filename": "f2.md",
            "status": "resolved",
            "notes": "closed",
        })
        assert "is_error" not in result or not result["is_error"]

        text = path.read_text(encoding="utf-8")
        # The FIRST Disposition block is preserved unmodified (writer
        # spliced only the last one).
        assert "status: open" in text
        assert "first" in text
        # The SECOND Disposition block was replaced with the new one.
        assert "status: resolved" in text
        assert "status: investigating" not in text
        assert parse_latest_disposition(text) == "resolved"


class TestCycle2F6AtomicWriteUniqueTempNames:
    """F6: atomic writer uses unique temp names and never unlinks collateral."""

    def test_cycle2_f6_temp_name_is_unique_per_call(
        self, tmp_path: Path,
    ) -> None:
        """Two calls in quick succession get distinct temp names."""
        from unittest import mock

        from clou.supervisor_tools import _atomic_write

        target = tmp_path / "target.md"
        target.write_text("initial", encoding="utf-8")

        observed_temp_names: list[str] = []

        real_mkstemp = __import__("tempfile").mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            observed_temp_names.append(name)
            return fd, name

        with mock.patch(
            "clou.supervisor_tools.tempfile.mkstemp",
            side_effect=capturing_mkstemp,
        ):
            _atomic_write(target, "v1")
            _atomic_write(target, "v2")

        assert len(observed_temp_names) == 2
        # Each call got a unique temp name.
        assert observed_temp_names[0] != observed_temp_names[1]
        # Each temp name contains the target's name as a prefix (so
        # crash-debugging is easier) and lives in the same directory.
        for name in observed_temp_names:
            assert str(tmp_path) in name
            assert ".target.md." in name
            assert name.endswith(".tmp")

    def test_cycle2_f6_never_unlinks_unrelated_tmp(
        self, tmp_path: Path,
    ) -> None:
        """An unrelated ``{name}.tmp`` file at the fixed legacy path survives."""
        from clou.supervisor_tools import _atomic_write

        target = tmp_path / "stale.md"
        target.write_text("initial content", encoding="utf-8")

        # Plant the legacy ``{name}.tmp`` path as if a prior crash left it.
        legacy_tmp = target.with_name(target.name + ".tmp")
        legacy_tmp.write_text("unrelated collateral", encoding="utf-8")

        _atomic_write(target, "new content")

        # The legacy tmp path survives with its content intact.
        assert legacy_tmp.exists()
        assert (
            legacy_tmp.read_text(encoding="utf-8")
            == "unrelated collateral"
        )
        # And the target was updated.
        assert target.read_text(encoding="utf-8") == "new content"

    def test_cycle2_f6_failed_write_cleans_only_own_temp(
        self, tmp_path: Path,
    ) -> None:
        """If ``os.replace`` fails, only OUR temp file is cleaned up."""
        from unittest import mock

        from clou.supervisor_tools import _atomic_write

        target = tmp_path / "fail.md"
        target.write_text("initial", encoding="utf-8")

        legacy_tmp = target.with_name(target.name + ".tmp")
        legacy_tmp.write_text("collateral", encoding="utf-8")

        captured_temp_names: list[str] = []
        real_mkstemp = __import__("tempfile").mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            captured_temp_names.append(name)
            return fd, name

        with mock.patch(
            "clou.supervisor_tools.tempfile.mkstemp",
            side_effect=capturing_mkstemp,
        ):
            with mock.patch(
                "clou.supervisor_tools.os.replace",
                side_effect=OSError("simulated replace failure"),
            ):
                with pytest.raises(OSError, match="simulated"):
                    _atomic_write(target, "new")

        # Our own temp file was cleaned up.
        assert len(captured_temp_names) == 1
        our_tmp = Path(captured_temp_names[0])
        assert not our_tmp.exists(), (
            "our tempfile must be unlinked on failed replace"
        )
        # The unrelated tmp was NEVER touched.
        assert legacy_tmp.exists()
        assert legacy_tmp.read_text(encoding="utf-8") == "collateral"
        # The original target was not modified.
        assert target.read_text(encoding="utf-8") == "initial"


class TestCycle2F7CanonicalDispositionStatusSet:
    """F7: supervisor validates against canonical ``VALID_DISPOSITION_STATUSES``."""

    def test_cycle2_f7_resolution_statuses_is_canonical(self) -> None:
        """``RESOLUTION_STATUSES`` is an alias, not a divergent local copy."""
        from clou.escalation import VALID_DISPOSITION_STATUSES
        from clou.supervisor_tools import RESOLUTION_STATUSES

        # Same underlying set of values --- frozen-set or tuple equality
        # both work via set() normalization.
        assert set(RESOLUTION_STATUSES) == set(VALID_DISPOSITION_STATUSES)
        # And it's not a random superset --- it's exactly the canonical
        # enum, no added states, no dropped states.
        assert set(RESOLUTION_STATUSES) == {
            "open", "investigating", "deferred", "resolved", "overridden",
        }

    @pytest.mark.asyncio
    async def test_cycle2_f7_all_canonical_statuses_accepted(
        self, sup_tools,
    ) -> None:
        """Every status in ``VALID_DISPOSITION_STATUSES`` is accepted."""
        from clou.escalation import VALID_DISPOSITION_STATUSES

        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        for i, s in enumerate(VALID_DISPOSITION_STATUSES):
            _seed_escalation(
                tmp_path, f"canon-{i}.md",
                "# E\n\n## Disposition\nstatus: open\n",
            )
            result = await handler({
                "milestone": "test-ms",
                "filename": f"canon-{i}.md",
                "status": s,
            })
            assert "is_error" not in result or not result["is_error"], (
                f"canonical status {s!r} unexpectedly rejected by the tool"
            )

    @pytest.mark.asyncio
    async def test_cycle2_f7_error_message_lists_canonical_set(
        self, sup_tools,
    ) -> None:
        """Error text on invalid status enumerates the canonical choices.

        The caller needs to self-correct; we must echo back the exact
        valid-status list the schema would accept, not a subset, not a
        superset, not a hard-coded list that can drift.
        """
        from clou.escalation import VALID_DISPOSITION_STATUSES

        tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        _seed_escalation(
            tmp_path, "reject.md",
            "# E\n\n## Disposition\nstatus: open\n",
        )
        result = await handler({
            "milestone": "test-ms",
            "filename": "reject.md",
            "status": "not-a-real-status",
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"]
        for s in VALID_DISPOSITION_STATUSES:
            assert s in msg, (
                f"error message must list canonical status {s!r}; "
                f"got: {msg!r}"
            )


class TestCycle2F12FilenameControlCharacters:
    """F12: filename with null-byte / embedded control chars → structured error."""

    @pytest.mark.asyncio
    async def test_cycle2_f12_null_byte_filename_rejected(
        self, sup_tools,
    ) -> None:
        """``\x00`` in filename → structured ``is_error``, no exception.

        ``Path.resolve`` raises ``ValueError`` on null bytes, which the
        MCP transport would surface as an uncaught exception.  The
        handler catches it explicitly and returns a structured error
        payload that names the offending character class.
        """
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "bad\x00name.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"]
        assert "null byte" in msg.lower() or "\\x00" in msg

    @pytest.mark.asyncio
    async def test_cycle2_f12_embedded_newline_filename_rejected(
        self, sup_tools,
    ) -> None:
        """Embedded ``\\n`` in filename is rejected before Path sees it."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "bad\nname.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"].lower()
        assert "newline" in msg or "embedded" in msg

    @pytest.mark.asyncio
    async def test_cycle2_f12_embedded_cr_filename_rejected(
        self, sup_tools,
    ) -> None:
        """Embedded ``\\r`` in filename is rejected (log-injection defence)."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "bad\rname.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"].lower()
        assert "carriage return" in msg or "embedded" in msg

    @pytest.mark.asyncio
    async def test_cycle2_f12_embedded_tab_filename_rejected(
        self, sup_tools,
    ) -> None:
        """Embedded ``\\t`` in filename is rejected (editor mis-render defence)."""
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler
        result = await handler({
            "milestone": "test-ms",
            "filename": "bad\tname.md",
            "status": "resolved",
        })
        assert result.get("is_error")
        msg = result["content"][0]["text"].lower()
        assert "tab" in msg or "embedded" in msg

    @pytest.mark.asyncio
    async def test_cycle2_f12_control_char_never_raises(
        self, sup_tools,
    ) -> None:
        """No control-char filename produces an unhandled exception.

        Every input path in the handler is wrapped so the MCP transport
        always receives a structured ``is_error`` payload, never a raised
        exception --- a raised exception would break the tool-calling
        contract the LLM relies on.
        """
        _, tools = sup_tools
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        hostile_filenames = [
            "a\x00b.md",
            "a\nb.md",
            "a\rb.md",
            "a\tb.md",
            "a\x01b.md",  # additional control chars also cannot crash
        ]
        for fn in hostile_filenames:
            # This call must not raise --- every failure becomes a
            # structured error response.
            result = await handler({
                "milestone": "test-ms",
                "filename": fn,
                "status": "resolved",
            })
            assert isinstance(result, dict)
            assert result.get("is_error"), (
                f"hostile filename {fn!r} should have produced an error"
            )


# ===========================================================================
# M49b D2 — clou_resolve_escalation MUST refuse engine-gated
# classifications.  Without this guard the supervisor's prompt-default
# disposition path (which currently names clou_resolve_escalation, not
# clou_dispose_halt — see D5) leaves the milestone wedged.
# ===========================================================================


class TestResolveEscalationRejectsEngineGated:

    @pytest.mark.asyncio
    async def test_rejects_trajectory_halt_with_helpful_error(
        self, sup_tools,
    ) -> None:
        from clou.escalation import (
            EscalationForm,
            EscalationOption,
            render_escalation,
        )

        tmp_path, tools = sup_tools
        ms = tmp_path / ".clou" / "milestones" / "test-ms"
        esc_path = ms / "escalations" / "halt.md"
        esc_path.write_text(
            render_escalation(
                EscalationForm(
                    title="halt",
                    classification="trajectory_halt",
                    issue="x",
                    options=(EscalationOption(label="continue-as-is"),),
                    disposition_status="open",
                )
            ),
            encoding="utf-8",
        )
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "status": "resolved",
            "notes": "x",
        })
        assert result.get("is_error"), result
        text = result["content"][0]["text"]
        # Error must name the right tool so the supervisor (or its
        # prompt) can route correctly.
        assert "clou_dispose_halt" in text
        # And explain why this matters (avoid silent misdirection).
        assert "wedged" in text.lower() or "halt" in text.lower()

    @pytest.mark.asyncio
    async def test_blocking_classification_still_resolvable(
        self, sup_tools,
    ) -> None:
        """Regression: D2's guard must NOT touch the existing happy
        path for non-engine-gated classifications."""
        from clou.escalation import (
            EscalationForm,
            EscalationOption,
            render_escalation,
        )

        tmp_path, tools = sup_tools
        ms = tmp_path / ".clou" / "milestones" / "test-ms"
        esc_path = ms / "escalations" / "blocker.md"
        esc_path.write_text(
            render_escalation(
                EscalationForm(
                    title="blocker",
                    classification="blocking",
                    issue="x",
                    options=(EscalationOption(label="resolve"),),
                    disposition_status="open",
                )
            ),
            encoding="utf-8",
        )
        handler = _find_tool(tools, "clou_resolve_escalation").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "status": "resolved",
            "notes": "ok",
        })
        assert not result.get("is_error"), result


# ===========================================================================
# M49b B6 — clou_dispose_halt: supervisor's path out of the halt-pending-
# review state.  Resolves the engine-gated escalation AND rewrites the
# milestone checkpoint so the next coordinator session can run.
# ===========================================================================


def _seed_halt_escalation_and_checkpoint(
    tmp_path: Path,
    *,
    pre_halt_next_step: str = "EXECUTE",
) -> tuple[Path, Path]:
    """Set up a milestone with both a halted checkpoint and an open
    trajectory_halt escalation file.  Returns (escalation_path,
    checkpoint_path)."""
    from clou.escalation import (
        EscalationForm,
        EscalationOption,
        render_escalation,
    )
    from clou.golden_context import render_checkpoint

    ms = tmp_path / ".clou" / "milestones" / "test-ms"
    (ms / "escalations").mkdir(parents=True, exist_ok=True)
    (ms / "active").mkdir(parents=True, exist_ok=True)

    esc_path = ms / "escalations" / "20260423-070000-halt.md"
    esc_path.write_text(
        render_escalation(
            EscalationForm(
                title="Trajectory halt: anti_convergence (cycle 3)",
                classification="trajectory_halt",
                issue="findings re-surface across rounds",
                options=(
                    EscalationOption(label="continue-as-is"),
                    EscalationOption(label="re-scope"),
                    EscalationOption(label="abandon"),
                ),
                disposition_status="open",
            )
        ),
        encoding="utf-8",
    )

    cp_path = ms / "active" / "coordinator.md"
    cp_path.write_text(
        render_checkpoint(
            cycle=3,
            step="ASSESS",
            next_step="HALTED",
            current_phase="phase_a",
            phases_completed=2,
            phases_total=3,
            cycle_outcome="HALTED_PENDING_REVIEW",
            pre_halt_next_step=pre_halt_next_step,
        ),
        encoding="utf-8",
    )
    return esc_path, cp_path


class TestDisposeHaltSchema:
    """Schema-level checks for clou_dispose_halt."""

    def test_tool_is_exposed(self, sup_tools) -> None:
        _, tools = sup_tools
        names = {getattr(t, "name", "") for t in tools}
        assert "clou_dispose_halt" in names

    def test_schema_required_fields(self, sup_tools) -> None:
        _, tools = sup_tools
        t = _find_tool(tools, "clou_dispose_halt")
        schema = t.input_schema
        assert schema["type"] == "object"
        # next_step is optional (override only); the other four are required.
        assert set(schema["required"]) == {
            "milestone", "filename", "choice", "notes",
        }
        for field in ("milestone", "filename", "choice", "notes", "next_step"):
            assert field in schema["properties"]


class TestDisposeHaltHappyPath:
    """Each disposition choice produces the correct downstream state."""

    @pytest.mark.asyncio
    async def test_continue_as_is_restores_pre_halt_stash(
        self, sup_tools,
    ) -> None:
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(
            tmp_path, pre_halt_next_step="EXECUTE",
        )
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "Trajectory looks fine on closer inspection.",
        })

        assert not result.get("is_error"), result
        assert result["choice"] == "continue-as-is"
        assert result["next_step"] == "EXECUTE"

        # Checkpoint reflects the restoration + cleared halt state.
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "EXECUTE"
        assert cp.cycle_outcome == "ADVANCED"
        assert cp.pre_halt_next_step == ""
        # Other fields preserved.
        assert cp.cycle == 3
        assert cp.current_phase == "phase_a"

    @pytest.mark.asyncio
    async def test_continue_as_is_falls_back_to_orient_when_no_stash(
        self, sup_tools,
    ) -> None:
        """If the halt gate fired without stashing (defensive case),
        continue-as-is routes to ORIENT for re-observation rather than
        crashing or coercing to PLAN."""
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(
            tmp_path, pre_halt_next_step="",
        )
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "Resume.",
        })

        assert not result.get("is_error"), result
        assert result["next_step"] == "ORIENT"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "ORIENT"
        assert cp.cycle_outcome == "ADVANCED"

    @pytest.mark.asyncio
    async def test_re_scope_routes_to_plan(self, sup_tools) -> None:
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(tmp_path)
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "re-scope",
            "notes": "New scope: focus on the failing observation cycle only.",
        })

        assert not result.get("is_error"), result
        assert result["next_step"] == "PLAN"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "PLAN"
        assert cp.cycle_outcome == "ADVANCED"
        # Stash cleared even though we didn't consume it.
        assert cp.pre_halt_next_step == ""

    @pytest.mark.asyncio
    async def test_abandon_routes_to_exit(self, sup_tools) -> None:
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(tmp_path)
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "abandon",
            "notes": "Work is no longer relevant; exit milestone.",
        })

        assert not result.get("is_error"), result
        assert result["next_step"] == "EXIT"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "EXIT"

    @pytest.mark.asyncio
    async def test_supervisor_can_override_next_step(self, sup_tools) -> None:
        """For unusual cases — supervisor passes an explicit next_step
        that diverges from the choice's default mapping."""
        from clou.recovery_checkpoint import parse_checkpoint

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(tmp_path)
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "re-scope",
            "notes": "Re-scope but go straight to ASSESS, not PLAN.",
            "next_step": "ASSESS",
        })

        assert not result.get("is_error"), result
        assert result["next_step"] == "ASSESS"
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.next_step == "ASSESS"

    @pytest.mark.asyncio
    async def test_resolved_escalation_has_choice_in_notes(
        self, sup_tools,
    ) -> None:
        """The disposition notes embed the choice so the audit trail
        records which option the supervisor picked."""
        tmp_path, tools = sup_tools
        esc_path, _cp = _seed_halt_escalation_and_checkpoint(tmp_path)
        handler = _find_tool(tools, "clou_dispose_halt").handler

        await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "abandon",
            "notes": "Detailed reason for abandoning.",
        })

        body = esc_path.read_text(encoding="utf-8")
        assert "## Disposition" in body
        assert "status: resolved" in body
        assert "Choice: abandon" in body
        assert "Detailed reason" in body


class TestDisposeHaltErrorPaths:
    """Defensive validation."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_choice(self, sup_tools) -> None:
        tmp_path, tools = sup_tools
        esc_path, _ = _seed_halt_escalation_and_checkpoint(tmp_path)
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "make-it-go-away",
            "notes": "x",
        })
        assert result.get("is_error"), result

    @pytest.mark.asyncio
    async def test_rejects_halted_as_override(self, sup_tools) -> None:
        """HALTED override would create a restoration loop (gate fires
        again next iteration)."""
        tmp_path, tools = sup_tools
        esc_path, _ = _seed_halt_escalation_and_checkpoint(tmp_path)
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "x",
            "next_step": "HALTED",
        })
        assert result.get("is_error"), result

    @pytest.mark.asyncio
    async def test_rejects_non_engine_gated_classification(
        self, sup_tools,
    ) -> None:
        """``clou_dispose_halt`` is for engine-gated halts only.  A
        regular ``blocking`` escalation should be routed to
        ``clou_resolve_escalation``."""
        from clou.escalation import (
            EscalationForm,
            EscalationOption,
            render_escalation,
        )

        tmp_path, tools = sup_tools
        ms = tmp_path / ".clou" / "milestones" / "test-ms"
        esc_path = ms / "escalations" / "blocking.md"
        esc_path.write_text(
            render_escalation(
                EscalationForm(
                    title="Just a blocker",
                    classification="blocking",
                    issue="needs decision",
                    options=(EscalationOption(label="resolve"),),
                    disposition_status="open",
                )
            ),
            encoding="utf-8",
        )
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "abandon",
            "notes": "x",
        })
        assert result.get("is_error"), result
        text = result["content"][0]["text"]
        assert "engine-gated" in text or "ENGINE_GATED" in text

    @pytest.mark.asyncio
    async def test_rejects_already_resolved_escalation(
        self, sup_tools,
    ) -> None:
        from clou.escalation import (
            EscalationForm,
            EscalationOption,
            render_escalation,
        )

        tmp_path, tools = sup_tools
        ms = tmp_path / ".clou" / "milestones" / "test-ms"
        esc_path = ms / "escalations" / "already-resolved.md"
        esc_path.write_text(
            render_escalation(
                EscalationForm(
                    title="halt",
                    classification="trajectory_halt",
                    issue="x",
                    options=(EscalationOption(label="continue-as-is"),),
                    disposition_status="resolved",
                )
            ),
            encoding="utf-8",
        )
        handler = _find_tool(tools, "clou_dispose_halt").handler

        result = await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "abandon",
            "notes": "x",
        })
        assert result.get("is_error"), result

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_in_filename(
        self, sup_tools,
    ) -> None:
        _tmp_path, tools = sup_tools
        handler = _find_tool(tools, "clou_dispose_halt").handler
        for hostile in ("../escape.md", "../../etc/passwd", ".hidden.md"):
            result = await handler({
                "milestone": "test-ms",
                "filename": hostile,
                "choice": "abandon",
                "notes": "x",
            })
            assert result.get("is_error"), (
                f"hostile filename {hostile!r} should have errored"
            )


class TestDisposeHaltClosesTheHaltGate:
    """End-to-end: after disposition, the halt-gate scan returns None
    (escalation is in terminal disposition) so the next coordinator
    session can dispatch normally."""

    @pytest.mark.asyncio
    async def test_resolved_halt_no_longer_matches_gate(
        self, sup_tools,
    ) -> None:
        from clou.escalation import find_open_engine_gated_escalation

        tmp_path, tools = sup_tools
        esc_path, _cp = _seed_halt_escalation_and_checkpoint(tmp_path)
        esc_dir = esc_path.parent

        # Sanity: gate sees the open halt before disposition.
        match_before = find_open_engine_gated_escalation(esc_dir)
        assert match_before is not None
        assert match_before[0] == esc_path

        handler = _find_tool(tools, "clou_dispose_halt").handler
        await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "trajectory ok",
        })

        # After disposition: gate finds nothing.
        match_after = find_open_engine_gated_escalation(esc_dir)
        assert match_after is None


# ===========================================================================
# M49b D1 — crash-between-writes safety (closes B7/F1).  Verifies the
# checkpoint-first ordering: if the second write (escalation) crashes,
# the first write (checkpoint) is replay-safe — the halt gate re-fires
# on next session, re-stashes, and the supervisor can retry cleanly.
# ===========================================================================


class TestDisposeHaltCrashBetweenWritesIsReplaySafe:

    @pytest.mark.asyncio
    async def test_checkpoint_first_ordering_survives_escalation_crash(
        self, sup_tools, monkeypatch,
    ) -> None:
        """Inject an OSError on the SECOND atomic write (the
        escalation).  After the crash:
          * checkpoint reflects ADVANCED + restored next_step (first
            write succeeded)
          * escalation file is still in OPEN disposition (second write
            never landed)

        This is the crash-safe state we want: the next coordinator
        session sees a runnable next_step PLUS an open engine-gated
        escalation, so the halt gate at the loop top re-fires and
        re-writes next_step=HALTED with a fresh stash.  Idempotent
        replay — supervisor re-invokes dispose_halt and it succeeds.

        The PREVIOUS order (escalation first, checkpoint second)
        produced the inverse failure: escalation resolved but
        checkpoint still HALTED, so the gate did NOT re-fire and
        determine_next_cycle's HALTED case raised RuntimeError on
        next session — an unrecoverable wedge."""
        from clou.escalation import (
            find_open_engine_gated_escalation,
            parse_escalation,
        )
        from clou.recovery_checkpoint import parse_checkpoint
        import clou.supervisor_tools as sup_module

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(
            tmp_path, pre_halt_next_step="EXECUTE",
        )

        # Patch _atomic_write to raise on the SECOND call (escalation
        # write).  The first call (checkpoint write) succeeds.  Use a
        # call-count closure to keep the wrapping stable for nested
        # writes inside the helper.
        real_atomic = sup_module._atomic_write
        calls = {"n": 0}

        def boomy_atomic(target, content):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated crash between writes")
            real_atomic(target, content)

        monkeypatch.setattr(sup_module, "_atomic_write", boomy_atomic)

        handler = _find_tool(tools, "clou_dispose_halt").handler
        with pytest.raises(OSError, match="simulated crash"):
            await handler({
                "milestone": "test-ms",
                "filename": esc_path.name,
                "choice": "continue-as-is",
                "notes": "x",
            })

        # Checkpoint: first write succeeded → ADVANCED + EXECUTE.
        cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
        assert cp.cycle_outcome == "ADVANCED"
        assert cp.next_step == "EXECUTE"
        assert cp.pre_halt_next_step == ""

        # Escalation: second write never landed → still open.
        form = parse_escalation(esc_path.read_text(encoding="utf-8"))
        assert form.disposition_status == "open"

        # Replay: gate re-fires on next session because escalation is open.
        match = find_open_engine_gated_escalation(esc_path.parent)
        assert match is not None, (
            "halt gate must re-fire on replay (escalation still open) so "
            "the supervisor can retry dispose_halt cleanly"
        )

    @pytest.mark.asyncio
    async def test_both_writes_use_atomic_primitive(
        self, sup_tools, monkeypatch,
    ) -> None:
        """Verify both the checkpoint AND escalation writes go through
        _atomic_write — pre-D1 the checkpoint was written via plain
        write_text(), which is not durable across power loss."""
        import clou.supervisor_tools as sup_module

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(tmp_path)

        atomic_targets: list[str] = []
        real_atomic = sup_module._atomic_write

        def tracking_atomic(target, content):  # type: ignore[no-untyped-def]
            atomic_targets.append(str(target))
            real_atomic(target, content)

        monkeypatch.setattr(sup_module, "_atomic_write", tracking_atomic)

        handler = _find_tool(tools, "clou_dispose_halt").handler
        await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "x",
        })

        # Both target paths should appear in the atomic-write list.
        assert str(cp_path) in atomic_targets
        assert str(esc_path) in atomic_targets

    @pytest.mark.asyncio
    async def test_checkpoint_written_before_escalation(
        self, sup_tools, monkeypatch,
    ) -> None:
        """Pin the ordering invariant: checkpoint must be written
        first so a crash-between leaves a replay-safe state."""
        import clou.supervisor_tools as sup_module

        tmp_path, tools = sup_tools
        esc_path, cp_path = _seed_halt_escalation_and_checkpoint(tmp_path)

        atomic_targets: list[str] = []
        real_atomic = sup_module._atomic_write

        def tracking_atomic(target, content):  # type: ignore[no-untyped-def]
            atomic_targets.append(str(target))
            real_atomic(target, content)

        monkeypatch.setattr(sup_module, "_atomic_write", tracking_atomic)

        handler = _find_tool(tools, "clou_dispose_halt").handler
        await handler({
            "milestone": "test-ms",
            "filename": esc_path.name,
            "choice": "continue-as-is",
            "notes": "x",
        })

        # Find the indices of each path in the call sequence.
        cp_idx = atomic_targets.index(str(cp_path))
        esc_idx = atomic_targets.index(str(esc_path))
        assert cp_idx < esc_idx, (
            "checkpoint must be written BEFORE escalation; reversing "
            "this ordering re-introduces the crash-between wedge "
            "(B7/F1)"
        )
