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
    """Init copies all 14 bundled prompt files to .clou/prompts/."""
    from clou.prompts import _BUNDLED_PROMPTS

    asyncio.run(clou_init(tmp_path, "MyProject", "A cool project"))

    prompts_dir = tmp_path / ".clou" / "prompts"
    copied = sorted(f.name for f in prompts_dir.iterdir() if f.is_file())

    expected = sorted(
        f.name
        for f in _BUNDLED_PROMPTS.iterdir()
        if f.is_file() and f.name != "__init__.py"
    )
    assert len(expected) == 14
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
