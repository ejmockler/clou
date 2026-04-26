"""Tests for custom MCP tool definitions."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clou.tools import clou_create_milestone, clou_init, clou_spawn_coordinator, clou_status

# ---------------------------------------------------------------------------
# clou_spawn_coordinator
# ---------------------------------------------------------------------------


def test_spawn_coordinator_returns_status(tmp_path: Path) -> None:
    ms_dir = tmp_path / ".clou" / "milestones" / "m01-auth"
    ms_dir.mkdir(parents=True)
    (ms_dir / "milestone.md").write_text("# Auth milestone")

    result = asyncio.run(clou_spawn_coordinator(tmp_path, "m01-auth"))
    assert "Coordinator for 'm01-auth' requested" in result
    assert "status.md" in result


def test_spawn_coordinator_missing_milestone_raises(tmp_path: Path) -> None:
    (tmp_path / ".clou" / "milestones" / "m01-auth").mkdir(parents=True)

    with pytest.raises(ValueError, match="Milestone file not found"):
        asyncio.run(clou_spawn_coordinator(tmp_path, "m01-auth"))


def test_spawn_coordinator_no_milestone_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Milestone file not found"):
        asyncio.run(clou_spawn_coordinator(tmp_path, "m99-nope"))


# ---------------------------------------------------------------------------
# clou_status
# ---------------------------------------------------------------------------


def test_status_no_clou_dir(tmp_path: Path) -> None:
    result = asyncio.run(clou_status(tmp_path))
    assert result == "No .clou/ directory found. Run clou_init first."


def test_status_empty_clou_dir(tmp_path: Path) -> None:
    (tmp_path / ".clou").mkdir()
    result = asyncio.run(clou_status(tmp_path))
    assert result == "No status information available."


def test_status_with_roadmap(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "roadmap.md").write_text("# Roadmap\n\n## Milestones\n")

    result = asyncio.run(clou_status(tmp_path))
    assert "# Roadmap" in result


def test_status_with_escalations(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    ms_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    ms_dir.mkdir(parents=True)
    (ms_dir / "esc-001.md").write_text("blocked on API key")

    result = asyncio.run(clou_status(tmp_path))
    assert "## Open Escalations" in result
    assert "m01-auth/esc-001.md" in result


def test_status_with_roadmap_and_escalations(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "roadmap.md").write_text("# Roadmap\n\n## Milestones\n")

    ms_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    ms_dir.mkdir(parents=True)
    (ms_dir / "esc-001.md").write_text("issue")

    result = asyncio.run(clou_status(tmp_path))
    assert "# Roadmap" in result
    assert "## Open Escalations" in result


def test_status_multiple_milestones_sorted(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    for ms in ("m02-db", "m01-auth"):
        esc_dir = clou_dir / "milestones" / ms / "escalations"
        esc_dir.mkdir(parents=True)
        (esc_dir / "esc-001.md").write_text("issue")

    result = asyncio.run(clou_status(tmp_path))
    m01_pos = result.index("m01-auth")
    m02_pos = result.index("m02-db")
    assert m01_pos < m02_pos


def test_status_ignores_milestone_without_escalation_dir(
    tmp_path: Path,
) -> None:
    clou_dir = tmp_path / ".clou"
    (clou_dir / "milestones" / "m01-auth").mkdir(parents=True)

    result = asyncio.run(clou_status(tmp_path))
    assert result == "No status information available."


# F30 — parse disposition and filter by status.  Resolved/overridden
# escalations are historical decision records; surfacing them as "Open
# Escalations" drowns the supervisor in false urgency.  The filter
# keeps open / investigating / deferred visible and hides resolved /
# overridden.  Parse failures surface with "(parse-error)" so drift
# is visible, matching the passive breath-event policy from F29.


_CANONICAL_ESC = (
    "# Escalation: blocker\n\n"
    "**Classification:** blocking\n"
    "**Filed:** 2026-04-21\n\n"
    "## Context\nctx\n\n"
    "## Issue\niss\n\n"
    "## Evidence\nev\n\n"
    "## Options\n1. **A** --- one\n2. **B** --- two\n\n"
    "## Recommendation\nrec\n\n"
    "## Disposition\nstatus: {status}\n"
)


def test_status_surfaces_open_escalation(tmp_path: Path) -> None:
    """F30: a canonical open escalation is listed."""
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "open.md").write_text(_CANONICAL_ESC.format(status="open"))

    result = asyncio.run(clou_status(tmp_path))
    assert "## Open Escalations" in result
    assert "m01-auth/open.md" in result


def test_status_hides_resolved_escalation(tmp_path: Path) -> None:
    """F30: resolved escalations are historical, not operational."""
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "resolved.md").write_text(
        _CANONICAL_ESC.format(status="resolved")
    )

    result = asyncio.run(clou_status(tmp_path))
    # Either there's no Open Escalations section, or the resolved file
    # is not in it.
    assert "m01-auth/resolved.md" not in result


def test_status_hides_overridden_escalation(tmp_path: Path) -> None:
    """F30: overridden escalations are also historical."""
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "overridden.md").write_text(
        _CANONICAL_ESC.format(status="overridden")
    )

    result = asyncio.run(clou_status(tmp_path))
    assert "m01-auth/overridden.md" not in result


def test_status_surfaces_investigating_and_deferred(tmp_path: Path) -> None:
    """F30: investigating and deferred remain visible."""
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "investigating.md").write_text(
        _CANONICAL_ESC.format(status="investigating")
    )
    (esc_dir / "deferred.md").write_text(
        _CANONICAL_ESC.format(status="deferred")
    )

    result = asyncio.run(clou_status(tmp_path))
    assert "## Open Escalations" in result
    assert "m01-auth/investigating.md" in result
    assert "m01-auth/deferred.md" in result


def test_status_mixes_open_hidden_and_visible(tmp_path: Path) -> None:
    """F30: resolved is hidden while open escalations surface."""
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "open.md").write_text(_CANONICAL_ESC.format(status="open"))
    (esc_dir / "resolved.md").write_text(
        _CANONICAL_ESC.format(status="resolved")
    )

    result = asyncio.run(clou_status(tmp_path))
    assert "m01-auth/open.md" in result
    assert "m01-auth/resolved.md" not in result


def test_status_parser_exception_propagates(tmp_path: Path) -> None:
    """F13 (cycle 2): parser regressions must NOT hide behind a broad
    ``except`` at DEBUG level — the cycle-1 F30 handling caught every
    ``Exception`` and logged at ``debug``, so a systematic parser
    regression would have landed silently in CI.  The cycle-2 contract
    narrows the catch to the read stage only; if
    :func:`clou.escalation.parse_escalation` raises (which violates its
    contract — the parser MUST NOT raise), the exception propagates so
    operators see the drift immediately.
    """
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)

    target = esc_dir / "drifted.md"
    target.write_text("literally anything; we patch parse_escalation")

    from unittest.mock import patch

    with patch(
        "clou.escalation.parse_escalation",
        side_effect=RuntimeError("synthetic parse failure"),
    ):
        with pytest.raises(RuntimeError, match="synthetic parse failure"):
            asyncio.run(clou_status(tmp_path))


def test_status_default_status_is_open(tmp_path: Path) -> None:
    """F30: absent disposition defaults to open — the coordinator has
    not yet filled in a resolution and the supervisor needs to see it.
    """
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    # No ## Disposition section at all.
    (esc_dir / "fresh.md").write_text(
        "# Escalation: fresh\n\n"
        "**Classification:** blocking\n\n"
        "## Issue\nx\n\n"
        "## Options\n1. **A** --- a\n"
    )

    result = asyncio.run(clou_status(tmp_path))
    assert "m01-auth/fresh.md" in result


# ---------------------------------------------------------------------------
# F13 / F21 (cycle 2) — clou_status hardening
# ---------------------------------------------------------------------------


def test_status_surfaces_unknown_disposition_with_marker(
    tmp_path: Path,
) -> None:
    """F21 partial: a file whose raw disposition token is outside
    ``VALID_DISPOSITION_STATUSES`` (e.g. ``status: closed``) must
    surface with ``(unknown: <raw>)`` — NOT be silently hidden.
    Tolerance on read must not become suppression on display.
    """
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "closed.md").write_text(
        _CANONICAL_ESC.format(status="closed")
    )

    result = asyncio.run(clou_status(tmp_path))
    assert "m01-auth/closed.md" in result, (
        "unknown-disposition files must NOT disappear from status"
    )
    assert "(unknown: closed)" in result, (
        "the raw drifted token must be visible so operators can "
        "chase it down"
    )


def test_status_unknown_status_not_treated_as_open(tmp_path: Path) -> None:
    """F21: unknown statuses get their own marker — they must not
    count as ``open`` AND they must not be silently hidden as
    ``resolved``.  The marker line is the only place they appear.
    """
    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "legacy.md").write_text(
        _CANONICAL_ESC.format(status="pending")
    )
    # Also add a real open file so we can prove the unknown one is
    # NOT filed under the default open bucket.
    (esc_dir / "open.md").write_text(_CANONICAL_ESC.format(status="open"))

    result = asyncio.run(clou_status(tmp_path))
    # The unknown file is surfaced with its marker.
    assert "m01-auth/legacy.md (unknown: pending)" in result
    # The open file is surfaced without a marker (it's genuinely open).
    lines = result.splitlines()
    open_lines = [
        line for line in lines
        if "m01-auth/open.md" in line and "(unknown" not in line
    ]
    assert open_lines, "genuine open file must still surface"


def test_status_read_error_narrow_exception(tmp_path: Path) -> None:
    """F13: OSError / UnicodeDecodeError from the READ stage are
    caught and surfaced with ``(read-error)`` — but the catch does NOT
    widen to other exceptions.  Prior to cycle 2 the handler was
    ``except Exception`` at DEBUG, hiding real regressions.
    """
    from unittest.mock import patch

    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "unreadable.md").write_text("whatever")

    # Patch the bounded reader to raise OSError — the listing still
    # completes and surfaces the file with a read-error marker.
    with patch(
        "clou.tools._read_escalation_bounded",
        side_effect=OSError("permission denied"),
    ):
        result = asyncio.run(clou_status(tmp_path))

    assert "m01-auth/unreadable.md" in result
    assert "(read-error)" in result


def test_status_read_error_unicode_decode_caught(tmp_path: Path) -> None:
    """F13: UnicodeDecodeError on read is narrow-caught; listing
    survives with a ``(read-error)`` marker."""
    from unittest.mock import patch

    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "bad-encoding.md").write_text("whatever")

    with patch(
        "clou.tools._read_escalation_bounded",
        side_effect=UnicodeDecodeError(
            "utf-8", b"\xff\xfe", 0, 1, "invalid start byte",
        ),
    ):
        result = asyncio.run(clou_status(tmp_path))

    assert "m01-auth/bad-encoding.md" in result
    assert "(read-error)" in result


def test_status_runtime_error_from_read_propagates(tmp_path: Path) -> None:
    """F13: narrow catch means a RuntimeError from the read stage
    propagates — the handler only swallows OSError and
    UnicodeDecodeError.  Any other exception must surface.
    """
    from unittest.mock import patch

    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "anything.md").write_text("content")

    with patch(
        "clou.tools._read_escalation_bounded",
        side_effect=RuntimeError("unexpected"),
    ):
        with pytest.raises(RuntimeError, match="unexpected"):
            asyncio.run(clou_status(tmp_path))


def test_status_truncates_oversized_files(tmp_path: Path) -> None:
    """F13: oversized escalation files are truncated before parsing,
    so a single 10MB log accidentally committed as an escalation
    cannot stall ``clou_status``.  We assert by checking that the
    read helper respects the byte cap.
    """
    from clou.tools import _ESCALATION_MAX_BYTES, _read_escalation_bounded

    clou_dir = tmp_path / ".clou"
    esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
    esc_dir.mkdir(parents=True)

    # Write a 100 KiB file — well above the 64 KiB cap.
    oversized = esc_dir / "huge.md"
    huge_content = "# Escalation: big\n\n" + ("a" * 100_000)
    oversized.write_text(huge_content)
    assert oversized.stat().st_size > _ESCALATION_MAX_BYTES

    content = _read_escalation_bounded(oversized)
    assert len(content) <= _ESCALATION_MAX_BYTES, (
        "read helper must truncate to _ESCALATION_MAX_BYTES"
    )
    # End-to-end: clou_status still returns cleanly; the file surfaces
    # in the listing.
    result = asyncio.run(clou_status(tmp_path))
    assert "m01-auth/huge.md" in result


def test_status_caps_listing_with_and_more_suffix(tmp_path: Path) -> None:
    """F13: per-milestone listing caps at MAX_ESCALATIONS_PER_STATUS.
    Excess entries collapse to a single ``...and N more`` suffix so
    drift remains visible (the count is surfaced) but the work stays
    bounded.
    """
    # Stub the cap to a small number for test speed.
    import clou.tools as tools

    original_cap = tools.MAX_ESCALATIONS_PER_STATUS
    try:
        tools.MAX_ESCALATIONS_PER_STATUS = 3

        clou_dir = tmp_path / ".clou"
        esc_dir = clou_dir / "milestones" / "m01-auth" / "escalations"
        esc_dir.mkdir(parents=True)

        # Create 5 open escalations — 2 more than the cap.
        for i in range(5):
            (esc_dir / f"open-{i:03d}.md").write_text(
                _CANONICAL_ESC.format(status="open")
            )

        result = asyncio.run(clou_status(tmp_path))

        # The cap was 3; the remaining 2 collapse to a suffix.
        assert "...and 2 more" in result, (
            "excess entries must collapse to a single '...and N more' "
            "line; got listing:\n" + result
        )
    finally:
        tools.MAX_ESCALATIONS_PER_STATUS = original_cap


# ---------------------------------------------------------------------------
# clou_init
# ---------------------------------------------------------------------------


def test_init_creates_structure(tmp_path: Path) -> None:
    result = asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))
    assert result == "Initialized .clou/ for 'MyProject'"

    clou_dir = tmp_path / ".clou"
    assert (clou_dir / "milestones").is_dir()
    assert (clou_dir / "active").is_dir()
    assert (clou_dir / "prompts").is_dir()
    assert (clou_dir / "project.md").is_file()
    assert (clou_dir / "roadmap.md").is_file()
    assert (clou_dir / "requests.md").is_file()


def test_init_project_md_content(tmp_path: Path) -> None:
    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    content = (tmp_path / ".clou" / "project.md").read_text()
    assert content == "# MyProject\n\nA cool project\n"


def test_init_roadmap_md_content(tmp_path: Path) -> None:
    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    content = (tmp_path / ".clou" / "roadmap.md").read_text()
    assert content == "# Roadmap\n\n## Milestones\n"


def test_init_requests_md_created(tmp_path: Path) -> None:
    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    content = (tmp_path / ".clou" / "requests.md").read_text()
    assert content == "# Requests\n"


def test_init_idempotent_repairs_partial(tmp_path: Path) -> None:
    """Init on existing .clou/ fills in missing pieces."""
    (tmp_path / ".clou").mkdir()
    result = asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))
    assert result == "Initialized .clou/ for 'MyProject'"

    # Directories and files should now exist
    clou_dir = tmp_path / ".clou"
    assert (clou_dir / "milestones").is_dir()
    assert (clou_dir / "active").is_dir()
    assert (clou_dir / "prompts").is_dir()
    assert (clou_dir / "project.md").is_file()
    assert (clou_dir / "roadmap.md").is_file()
    assert (clou_dir / "requests.md").is_file()


def test_init_does_not_overwrite(tmp_path: Path) -> None:
    clou_dir = tmp_path / ".clou"
    clou_dir.mkdir()
    (clou_dir / "project.md").write_text("original")

    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))
    assert (clou_dir / "project.md").read_text() == "original"


def test_init_copies_prompt_files(tmp_path: Path) -> None:
    """Init copies all bundled prompt files to .clou/prompts/."""
    from clou.prompts import _BUNDLED_PROMPTS

    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    prompts_dir = tmp_path / ".clou" / "prompts"
    copied = sorted(f.name for f in prompts_dir.iterdir() if f.is_file())

    expected = sorted(
        f.name
        for f in _BUNDLED_PROMPTS.iterdir()
        if f.is_file() and f.name != "__init__.py"
    )
    # 17 pre-M36 prompts + 1 coordinator-orient.md prompt added in
    # M36 = 18 bundled prompts.
    assert len(expected) == 18
    assert copied == expected


def test_init_prompt_content_matches_bundled(tmp_path: Path) -> None:
    """Copied prompt files match the bundled originals."""
    from clou.prompts import _BUNDLED_PROMPTS

    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    for src in _BUNDLED_PROMPTS.iterdir():
        if src.is_file() and src.name != "__init__.py":
            copied = tmp_path / ".clou" / "prompts" / src.name
            assert copied.read_text() == src.read_text()


def test_init_does_not_overwrite_customized_prompts(tmp_path: Path) -> None:
    """Re-running init preserves per-project prompt customizations."""
    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    custom = tmp_path / ".clou" / "prompts" / "worker.md"
    custom.write_text("customized")

    # Second init should not overwrite
    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))
    assert custom.read_text() == "customized"


def test_init_load_prompt_works_without_init(tmp_path: Path) -> None:
    """load_prompt reads from bundled prompts, no project init needed."""
    from clou.prompts import load_prompt

    # Should work even without clou_init — prompts are global.
    prompt = load_prompt("supervisor", tmp_path)
    assert isinstance(prompt, str)
    assert len(prompt) > 0


# ---------------------------------------------------------------------------
# clou_create_milestone
# ---------------------------------------------------------------------------


def test_create_milestone_happy_path(tmp_path: Path) -> None:
    (tmp_path / ".clou" / "milestones").mkdir(parents=True)
    result = asyncio.run(
        clou_create_milestone(
            tmp_path, "m01-auth", "# Auth", "# Requirements\n",
            intents_content="When user logs in, they see a dashboard\n",
        )
    )
    ms_dir = tmp_path / ".clou" / "milestones" / "m01-auth"
    assert ms_dir.is_dir()
    assert (ms_dir / "milestone.md").read_text() == "# Auth"
    assert (ms_dir / "intents.md").read_text() == "When user logs in, they see a dashboard\n"
    assert (ms_dir / "requirements.md").read_text() == "# Requirements\n"
    assert "m01-auth" in result


def test_create_milestone_duplicate_raises(tmp_path: Path) -> None:
    ms_dir = tmp_path / ".clou" / "milestones" / "m01-auth"
    ms_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(
            clou_create_milestone(tmp_path, "m01-auth", "# Auth", "# Req")
        )


def test_create_milestone_parents_created(tmp_path: Path) -> None:
    """mkdir(parents=True) handles missing .clou/milestones/."""
    result = asyncio.run(
        clou_create_milestone(tmp_path, "m01-auth", "# Auth", "# Req")
    )
    assert (tmp_path / ".clou" / "milestones" / "m01-auth" / "milestone.md").is_file()
    assert "m01-auth" in result


def test_create_milestone_content_preserved(tmp_path: Path) -> None:
    """Verify exact content written matches input, including whitespace."""
    content_ms = "# My Milestone\n\nDetailed description.\n"
    content_req = "# Requirements\n\n- Req 1\n- Req 2\n"
    content_int = "When user opens the app, they see a welcome screen\n"
    asyncio.run(
        clou_create_milestone(tmp_path, "m01-auth", content_ms, content_req, content_int)
    )
    ms_dir = tmp_path / ".clou" / "milestones" / "m01-auth"
    assert (ms_dir / "milestone.md").read_text() == content_ms
    assert (ms_dir / "intents.md").read_text() == content_int
    assert (ms_dir / "requirements.md").read_text() == content_req


def test_create_milestone_does_not_create_subdirs(tmp_path: Path) -> None:
    """Tool creates milestone.md, intents.md, and requirements.md only."""
    asyncio.run(
        clou_create_milestone(tmp_path, "m01-auth", "# Auth", "# Req")
    )
    ms_dir = tmp_path / ".clou" / "milestones" / "m01-auth"
    children = sorted(f.name for f in ms_dir.iterdir())
    assert children == ["intents.md", "milestone.md", "requirements.md"]
