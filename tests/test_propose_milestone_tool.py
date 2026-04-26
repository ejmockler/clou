"""Integration tests for the clou_propose_milestone MCP tool.

Verifies the coordinator->milestone-authority pathway: coordinator
writes typed proposals to .clou/proposals/; supervisor reads them
on startup and decides whether to crystallize via the existing
clou_create_milestone flow.

Under the zero-escalations principle, architectural escalations for
cross-cutting work become proposals -- coordinator has proposal
authority; supervisor retains milestone-creation authority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from clou.coordinator_tools import _build_coordinator_tools
from clou.proposal import parse_proposal


def _get_tool(project_dir: Path, milestone: str, name: str) -> object:
    for t in _build_coordinator_tools(project_dir, milestone):
        if getattr(t, "name", "") == name:
            return t
    raise AssertionError(f"{name!r} not exposed by coordinator tools")


class TestProposeTool:
    """Happy-path + validation contracts."""

    @pytest.fixture
    def setup(self, tmp_path: Path) -> dict[str, object]:
        clou = tmp_path / ".clou"
        (clou / "milestones" / "test-ms").mkdir(parents=True)
        tool = _get_tool(tmp_path, "test-ms", "clou_propose_milestone")
        return {"project_dir": tmp_path, "clou": clou, "tool": tool}

    async def test_writes_proposal_file(self, setup: dict[str, object]) -> None:
        tool = setup["tool"]
        clou: Path = setup["clou"]  # type: ignore[assignment]
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "Close perception gaps",
            "rationale": "Three failures today share one root cause.",
            "cross_cutting_evidence": (
                "telemetry t=15167s pytest-hang; t=23303s staleness."
            ),
            "estimated_scope": "multi-day",
            "cycle_num": 3,
        })
        # Happy-path returns the written path.
        assert "is_error" not in result or not result["is_error"]
        assert "written" in result
        path = Path(result["written"])
        assert path.exists()
        assert path.parent == clou / "proposals"

        # Filename shape: timestamp-slug.md
        assert path.suffix == ".md"
        assert "close-perception-gaps" in path.name

        # Roundtrip: the file parses back to an equivalent form.
        form = parse_proposal(path.read_text())
        assert form.title == "Close perception gaps"
        assert form.filed_by_milestone == "test-ms"
        assert form.filed_by_cycle == 3
        assert form.estimated_scope == "multi-day"
        assert form.status == "open"  # default

    async def test_rejects_empty_title(self, setup: dict[str, object]) -> None:
        tool = setup["tool"]
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "cycle_num": 1,
        })
        assert result.get("is_error") is True
        assert "title" in result["content"][0]["text"]

    async def test_rejects_speculative_proposal_missing_evidence(
        self, setup: dict[str, object]
    ) -> None:
        """Evidence is required -- not optional -- so coordinators can't
        file hand-wavy speculation that the supervisor has no way to
        verify.
        """
        tool = setup["tool"]
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "x",
            "rationale": "r",
            "cross_cutting_evidence": "",
            "cycle_num": 1,
        })
        assert result.get("is_error") is True
        assert "evidence" in result["content"][0]["text"]

    async def test_rejects_invalid_scope(
        self, setup: dict[str, object]
    ) -> None:
        tool = setup["tool"]
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "x",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "estimated_scope": "eternity",
            "cycle_num": 1,
        })
        assert result.get("is_error") is True
        assert "estimated_scope" in result["content"][0]["text"]

    async def test_rejects_missing_cycle_num(
        self, setup: dict[str, object]
    ) -> None:
        tool = setup["tool"]
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "x",
            "rationale": "r",
            "cross_cutting_evidence": "e",
        })
        assert result.get("is_error") is True
        assert "cycle_num" in result["content"][0]["text"]

    async def test_depends_on_list_preserved(
        self, setup: dict[str, object]
    ) -> None:
        tool = setup["tool"]
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "x",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "depends_on": ["36-orient-cycle-prefix", "37-orient-gating"],
            "cycle_num": 1,
        })
        assert "written" in result
        form = parse_proposal(Path(result["written"]).read_text())
        assert form.depends_on == (
            "36-orient-cycle-prefix", "37-orient-gating",
        )

    async def test_second_call_same_title_different_filename(
        self, setup: dict[str, object]
    ) -> None:
        """Collision-safe filename via suffix when two proposals share
        title + second-precision timestamp.
        """
        tool = setup["tool"]

        def _call() -> dict[str, object]:
            import asyncio

            return asyncio.get_event_loop().run_until_complete(
                tool.handler({  # type: ignore[attr-defined]
                    "title": "same title",
                    "rationale": "r",
                    "cross_cutting_evidence": "e",
                    "cycle_num": 1,
                })
            )

        # Two consecutive calls within the same second should not
        # raise; the second should land on a suffix-differentiated
        # filename.
        r1 = await tool.handler({  # type: ignore[attr-defined]
            "title": "same title",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "cycle_num": 1,
        })
        r2 = await tool.handler({  # type: ignore[attr-defined]
            "title": "same title",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "cycle_num": 1,
        })
        assert r1.get("is_error") is not True
        assert r2.get("is_error") is not True
        assert r1["written"] != r2["written"]


class TestProposalDirectoryShape:
    """Proposals live at .clou/proposals/ (project-scoped), not under
    any milestone.  This is because a proposal is ABOUT a future
    milestone that doesn't exist yet -- it has no milestone dir to
    live in.
    """

    async def test_directory_is_project_scoped(self, tmp_path: Path) -> None:
        tool = _get_tool(tmp_path, "test-ms", "clou_propose_milestone")
        result = await tool.handler({  # type: ignore[attr-defined]
            "title": "x",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "cycle_num": 1,
        })
        written = Path(result["written"])
        # Parent is .clou/proposals/, not .clou/milestones/{ms}/proposals/
        assert written.parent == tmp_path / ".clou" / "proposals"
        assert "milestones" not in written.parts


class TestProposeToolExclusiveCreate:
    """C3-review fix: brutalist 3-of-3 flagged TOCTOU race in the
    previous path.exists() + write_text() pattern.  Replaced with
    _exclusive_write using O_EXCL.  These tests verify the atomic-
    create semantics and that failed writes surface as structured
    errors rather than untyped transport exceptions.
    """

    async def test_pre_existing_exact_file_falls_back_to_suffix(
        self, tmp_path: Path
    ) -> None:
        """If the canonical {timestamp}-{slug}.md already exists
        (e.g., from a prior clock-second collision), the new proposal
        lands on a -N suffix without overwriting.
        """
        from clou.proposal import proposals_dir
        _dir = proposals_dir(tmp_path / ".clou")
        _dir.mkdir(parents=True)

        tool = _get_tool(tmp_path, "test-ms", "clou_propose_milestone")

        # File two proposals with the SAME title twice in quick succession.
        r1 = await tool.handler({  # type: ignore[attr-defined]
            "title": "x", "rationale": "r", "cross_cutting_evidence": "e",
            "cycle_num": 1,
        })
        r2 = await tool.handler({  # type: ignore[attr-defined]
            "title": "x", "rationale": "r", "cross_cutting_evidence": "e",
            "cycle_num": 1,
        })
        assert r1["written"] != r2["written"]
        # Both files exist; neither overwrote the other.
        assert Path(r1["written"]).exists()
        assert Path(r2["written"]).exists()

    async def test_failed_write_returns_structured_error_not_exception(
        self, tmp_path: Path
    ) -> None:
        """If the proposals dir is read-only, the tool should return
        an is_error dict, not let OSError propagate through the MCP
        transport as an unhandled exception.
        """
        import os
        from clou.proposal import proposals_dir

        _dir = proposals_dir(tmp_path / ".clou")
        _dir.mkdir(parents=True)

        tool = _get_tool(tmp_path, "test-ms", "clou_propose_milestone")

        # Make the proposals directory read-only.  _exclusive_write's
        # open(mode="x") will raise OSError (not FileExistsError).
        os.chmod(_dir, 0o500)
        try:
            result = await tool.handler({  # type: ignore[attr-defined]
                "title": "x",
                "rationale": "r",
                "cross_cutting_evidence": "e",
                "cycle_num": 1,
            })
            # The handler must catch the OSError and return a structured
            # error, not let it propagate.
            assert result.get("is_error") is True
            assert "write" in result["content"][0]["text"].lower() or (
                "permission" in result["content"][0]["text"].lower()
            )
        finally:
            os.chmod(_dir, 0o700)


class TestListProposalsTool:
    """Supervisor-side consumer: clou_list_proposals reads the directory
    and returns structured proposal data for the supervisor to orient.
    """

    def _get_supervisor_tool(self, project_dir: Path, name: str) -> object:
        from clou.supervisor_tools import _build_supervisor_tools
        for t in _build_supervisor_tools(project_dir):
            if getattr(t, "name", "") == name:
                return t
        raise AssertionError(f"{name!r} not exposed")

    async def _file_proposal(
        self, tmp_path: Path, milestone: str, **kwargs: object
    ) -> Path:
        tool = _get_tool(tmp_path, milestone, "clou_propose_milestone")
        defaults = {
            "title": "t",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "cycle_num": 1,
        }
        defaults.update(kwargs)
        result = await tool.handler(defaults)  # type: ignore[attr-defined]
        return Path(result["written"])

    async def test_empty_directory_returns_empty_list(
        self, tmp_path: Path
    ) -> None:
        list_tool = self._get_supervisor_tool(tmp_path, "clou_list_proposals")
        result = await list_tool.handler({})  # type: ignore[attr-defined]
        assert result == {"proposals": []}

    async def test_returns_proposals_with_default_open_filter(
        self, tmp_path: Path
    ) -> None:
        await self._file_proposal(tmp_path, "ms-a", title="First")
        await self._file_proposal(tmp_path, "ms-b", title="Second")
        list_tool = self._get_supervisor_tool(tmp_path, "clou_list_proposals")
        result = await list_tool.handler({})  # type: ignore[attr-defined]
        titles = sorted(p["title"] for p in result["proposals"])
        assert titles == ["First", "Second"]
        assert result["count"] == 2
        # filed_by_milestone is preserved so supervisor can see which
        # coordinator filed what.
        filers = {p["filed_by_milestone"] for p in result["proposals"]}
        assert filers == {"ms-a", "ms-b"}

    async def test_filter_by_status(self, tmp_path: Path) -> None:
        await self._file_proposal(tmp_path, "ms-a", title="Open one")
        # Manually mark one as accepted (simulates post-crystallization
        # state).
        p = await self._file_proposal(tmp_path, "ms-b", title="Accepted one")
        text = p.read_text()
        p.write_text(text.replace("status: open", "status: accepted"))

        list_tool = self._get_supervisor_tool(tmp_path, "clou_list_proposals")

        open_only = await list_tool.handler({"status": "open"})  # type: ignore[attr-defined]
        assert [p["title"] for p in open_only["proposals"]] == ["Open one"]

        all_of_them = await list_tool.handler({"status": "all"})  # type: ignore[attr-defined]
        assert len(all_of_them["proposals"]) == 2

        accepted_only = await list_tool.handler({"status": "accepted"})  # type: ignore[attr-defined]
        assert [p["title"] for p in accepted_only["proposals"]] == [
            "Accepted one",
        ]

    async def test_malformed_file_surfaces_with_parse_error_flag(
        self, tmp_path: Path
    ) -> None:
        """Drift-tolerant: one malformed proposal file doesn't hide the
        others.  The filename surfaces with parse_error: True so the
        supervisor can investigate.
        """
        from clou.proposal import proposals_dir
        _dir = proposals_dir(tmp_path / ".clou")
        _dir.mkdir(parents=True)
        # Malformed: missing required structure.
        (_dir / "20260421-000000-malformed.md").write_text("???")
        await self._file_proposal(tmp_path, "ms-a", title="Well-formed")

        list_tool = self._get_supervisor_tool(tmp_path, "clou_list_proposals")
        result = await list_tool.handler({"status": "all"})  # type: ignore[attr-defined]
        # Well-formed and malformed both appear.
        by_name = {p["filename"]: p for p in result["proposals"]}
        assert "20260421-000000-malformed.md" in by_name
        # Parse-error entries may or may not surface as such (the parser
        # is drift-tolerant and returns a nominally-valid form with
        # empty fields).  Either a parse_error flag or an empty-title
        # proposal is acceptable.
        malformed = by_name["20260421-000000-malformed.md"]
        assert malformed.get("parse_error") is True or malformed.get("title") == ""


class TestDisposeProposalTool:
    """C3-review: supervisor closes proposals through an MCP tool so
    status transitions don't drift the schema.  Replaces the
    test-observed `text.replace("status: open", "status: accepted")`
    that the brutalist flagged as re-introducing drift.
    """

    def _get_supervisor_tool(self, project_dir: Path, name: str) -> object:
        from clou.supervisor_tools import _build_supervisor_tools
        for t in _build_supervisor_tools(project_dir):
            if getattr(t, "name", "") == name:
                return t
        raise AssertionError(f"{name!r} not exposed")

    async def _file_proposal(self, tmp_path: Path, **kwargs: object) -> Path:
        tool = _get_tool(tmp_path, "test-ms", "clou_propose_milestone")
        defaults = {
            "title": "test",
            "rationale": "r",
            "cross_cutting_evidence": "e",
            "cycle_num": 1,
        }
        defaults.update(kwargs)
        result = await tool.handler(defaults)  # type: ignore[attr-defined]
        return Path(result["written"])

    async def test_transitions_status_to_accepted(self, tmp_path: Path) -> None:
        path = await self._file_proposal(tmp_path, title="Accept me")
        dispose = self._get_supervisor_tool(tmp_path, "clou_dispose_proposal")
        result = await dispose.handler({  # type: ignore[attr-defined]
            "filename": path.name,
            "status": "accepted",
            "notes": "crystallized as M48",
        })
        assert result.get("is_error") is not True
        # Re-parse: status + disposition updated, other fields preserved.
        from clou.proposal import parse_proposal
        reparsed = parse_proposal(path.read_text())
        assert reparsed.status == "accepted"
        assert "crystallized as M48" in reparsed.disposition
        assert reparsed.title == "Accept me"  # preserved

    async def test_rejects_unknown_status(self, tmp_path: Path) -> None:
        path = await self._file_proposal(tmp_path)
        dispose = self._get_supervisor_tool(tmp_path, "clou_dispose_proposal")
        result = await dispose.handler({  # type: ignore[attr-defined]
            "filename": path.name,
            "status": "deferred",  # not in VALID_STATUSES
        })
        assert result.get("is_error") is True

    async def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        dispose = self._get_supervisor_tool(tmp_path, "clou_dispose_proposal")
        for evil in (
            "../../../etc/passwd",
            "../escape.md",
            "/absolute/path.md",
            ".hidden",
            "..",
        ):
            result = await dispose.handler({  # type: ignore[attr-defined]
                "filename": evil,
                "status": "accepted",
            })
            assert result.get("is_error") is True, f"allowed {evil!r}"

    async def test_rejects_missing_file(self, tmp_path: Path) -> None:
        dispose = self._get_supervisor_tool(tmp_path, "clou_dispose_proposal")
        result = await dispose.handler({  # type: ignore[attr-defined]
            "filename": "20260101-000000-nonexistent.md",
            "status": "accepted",
        })
        assert result.get("is_error") is True

    async def test_round_trip_preserves_bytes_except_disposition(
        self, tmp_path: Path
    ) -> None:
        """Transitions MUST NOT mangle other fields.  The DB-21
        principle: rendering from the parsed form is byte-stable for
        the preserved fields.
        """
        path = await self._file_proposal(
            tmp_path,
            title="Preserved title",
            rationale="Preserved rationale text.",
            cross_cutting_evidence="Preserved evidence.",
            depends_on=["ms-a", "ms-b"],
            recommendation="Preserved rec.",
        )
        dispose = self._get_supervisor_tool(tmp_path, "clou_dispose_proposal")
        await dispose.handler({  # type: ignore[attr-defined]
            "filename": path.name,
            "status": "superseded",
            "notes": "M50 covers the ground.",
        })

        from clou.proposal import parse_proposal
        reparsed = parse_proposal(path.read_text())
        assert reparsed.title == "Preserved title"
        assert reparsed.rationale == "Preserved rationale text."
        assert reparsed.cross_cutting_evidence == "Preserved evidence."
        assert reparsed.depends_on == ("ms-a", "ms-b")
        assert reparsed.recommendation == "Preserved rec."
        assert reparsed.status == "superseded"
        assert "M50 covers the ground" in reparsed.disposition
