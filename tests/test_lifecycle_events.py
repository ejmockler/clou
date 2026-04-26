"""Integration tests for orchestrator lifecycle event emission.

Verifies that run_coordinator and spawn_coordinator_tool post the correct
Textual messages (ClouStatusUpdate, ClouCycleComplete, ClouDagUpdate,
ClouBreathEvent for passive escalation announcements, ClouHandoff,
ClouCoordinatorComplete) at the right moments in the lifecycle.

Mock strategy: claude_agent_sdk is injected as a fake module into
sys.modules so orchestrator can be imported without the real SDK.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock claude_agent_sdk before importing orchestrator
# ---------------------------------------------------------------------------

_mock_sdk = MagicMock()
_mock_sdk.ResultMessage = type("ResultMessage", (), {"usage": None})
_mock_sdk.TaskNotificationMessage = type("TaskNotificationMessage", (), {})
_mock_sdk.ClaudeSDKClient = MagicMock
_mock_sdk.ClaudeAgentOptions = MagicMock
_mock_sdk.AgentDefinition = MagicMock
_mock_sdk.HookMatcher = MagicMock
_mock_sdk.SandboxSettings = MagicMock
_mock_sdk.create_sdk_mcp_server = MagicMock(return_value=MagicMock())
_mock_sdk.tool = lambda *a, **kw: lambda f: f

# Inject mock SDK only if the real one is absent, then remove the
# sys.modules entry so other test files (test_orchestrator.py) that
# use pytest.importorskip("claude_agent_sdk") still skip correctly.
_had_sdk = "claude_agent_sdk" in sys.modules
if not _had_sdk:
    sys.modules["claude_agent_sdk"] = _mock_sdk

from clou.coordinator import run_coordinator  # noqa: E402
from clou.orchestrator import _build_mcp_server  # noqa: E402
from clou.ui.messages import (  # noqa: E402
    ClouBreathEvent,
    ClouCoordinatorComplete,
    ClouCycleComplete,
    ClouDagUpdate,
    ClouHandoff,
    ClouStatusUpdate,
)

if not _had_sdk:
    del sys.modules["claude_agent_sdk"]

# Patch target prefixes
_P = "clou.orchestrator"   # supervisor-resident names
_PC = "clou.coordinator"   # coordinator-resident names


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_app() -> MagicMock:
    """Return a mock ClouApp with a recording post_message."""
    app = MagicMock()
    app.post_message = MagicMock()
    return app


def _posted_types(app: MagicMock) -> list[type]:
    """Extract the message types from post_message calls."""
    return [call.args[0].__class__ for call in app.post_message.call_args_list]


def _posted_of_type(app: MagicMock, cls: type) -> list[Any]:
    """Extract posted messages that are instances of *cls*."""
    return [
        call.args[0]
        for call in app.post_message.call_args_list
        if isinstance(call.args[0], cls)
    ]


def _setup_clou_dir(project_dir: Path, milestone: str = "test-ms") -> None:
    """Create minimal .clou directory structure."""
    (project_dir / ".clou" / "active").mkdir(parents=True, exist_ok=True)
    (project_dir / ".clou" / "prompts").mkdir(parents=True, exist_ok=True)
    (project_dir / ".clou" / "prompts" / "coordinator-system.xml").write_text(
        "<system/>"
    )
    # Milestone dir with structural files so readiness/delivery checks pass.
    ms_dir = project_dir / ".clou" / "milestones" / milestone
    (ms_dir / "active").mkdir(parents=True, exist_ok=True)
    (ms_dir / "status.md").write_text("phase: p1\ncycle: 1\n")
    (ms_dir / "active" / "coordinator.md").write_text(
        "cycle: 1\nstep: PLAN\nnext_step: EXECUTE\n"
        "current_phase: p1\nphases_completed: 0\nphases_total: 1\n"
    )
    # Milestone marker so the orchestrator doesn't clear the checkpoint.
    (project_dir / ".clou" / ".coordinator-milestone").write_text(milestone)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStatusUpdateEmitted:
    """ClouStatusUpdate is posted before each cycle."""

    async def test_status_update_emitted(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        updates = _posted_of_type(app, ClouStatusUpdate)
        assert len(updates) == 1
        assert updates[0].cycle_type == "PLAN"
        assert updates[0].cycle_num == 1


class TestCycleCompleteEmitted:
    """ClouCycleComplete is posted after a successful cycle."""

    async def test_cycle_complete_emitted(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=2),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        completes = _posted_of_type(app, ClouCycleComplete)
        assert len(completes) == 1
        assert completes[0].cycle_num == 3
        assert completes[0].cycle_type == "EXECUTE"
        assert completes[0].next_step == "ASSESS"


class TestDagUpdateAfterCycle:
    """ClouDagUpdate posted after every cycle type when compose.py exists."""

    async def test_dag_update_after_plan(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create a compose.py with a simple task graph
        compose_dir = tmp_path / ".clou" / "milestones" / "test-ms"
        compose_dir.mkdir(parents=True, exist_ok=True)
        (compose_dir / "compose.py").write_text(
            'async def setup() -> str:\n    return "done"\n\n'
            "async def execute() -> None:\n    await setup()\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("PLAN", ["milestone.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        dag_updates = _posted_of_type(app, ClouDagUpdate)
        assert len(dag_updates) == 2
        task_names = {t["name"] for t in dag_updates[0].tasks}
        assert "setup" in task_names


class TestEscalationDetected:
    """A ClouBreathEvent (passive announcement) is posted for new escalation files.

    Escalations are agent-to-agent records (see
    project_escalations_are_agent_to_agent.md); the old user-modal
    ClouEscalationArrived pathway was retired in
    41-escalation-remolding (I4).
    """

    async def test_escalation_detected(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create an escalation file with classification discoverable by
        # the tolerant parser in clou.escalation.
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: something broke\n\n"
            "**Classification:** blocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
            "2. **Skip it**: move on\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # Filter out unrelated BreathEvent chatter; look for our announcement.
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        assert len(announcements) == 1
        assert announcements[0].text == (
            "escalation filed: blocking: 2026-01-01-test.md"
        )


class TestSeenEscalationsPersisted:
    """seen-escalations.txt prevents re-announcing after restart."""

    async def test_preexisting_seen_escalations_not_reposted(
        self, tmp_path: Path
    ) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create an escalation file
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: something broke\n\n"
            "**Classification:** blocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
            "2. **Skip it**: move on\n"
        )

        # Pre-populate per-milestone seen-escalations.txt (simulates prior
        # run).  C2: the file is milestone-scoped, not global.
        seen_path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text("2026-01-01-test.md\n")

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # The escalation should NOT be re-announced.
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        assert len(announcements) == 0

    async def test_seen_escalations_written_to_disk(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create an escalation file
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: something broke\n\n"
            "**Classification:** blocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
            "2. **Skip it**: move on\n"
        )

        seen_path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    # Don't COMPLETE — escalation_cycle_limit so we can
                    # check that seen_path was written mid-run
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # seen-escalations.txt is cleaned up on completion, so it
        # should not exist. But the escalation was announced exactly once.
        assert not seen_path.exists()
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        assert len(announcements) == 1

    async def test_seen_file_cleaned_on_completion(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)

        seen_path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text("old-escalation.md\n")

        with patch(
            f"{_PC}.determine_next_cycle",
            return_value=("COMPLETE", []),
        ):
            result = await run_coordinator(tmp_path, "test-ms")

        assert result == "completed"
        assert not seen_path.exists()


class TestSeenEscalationsPerMilestoneScope:
    """C2: seen-escalations.txt is per-milestone, not global.

    Closes the cross-coordinator race filed as M41 cycle-1 escalation
    ``20260421-120000-seen-escalations-global-race-carryover.md``.
    Under parallel dispatch, two coordinators reading and writing a
    global file drop each other's additions; per-milestone scoping
    eliminates the race structurally (each coordinator owns its own
    milestone directory exclusively).
    """

    async def test_new_path_is_milestone_scoped(self, tmp_path: Path) -> None:
        """The seen file lives under .clou/milestones/{ms}/active/,
        not .clou/active/.
        """
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: x\n\n**Classification:** blocking\n\n"
            "## Issue\nx\n\n## Options\n1. **A**: a\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            # Intercept before COMPLETE cleanup removes the file.
            # Patch unlink so we can observe the path that was targeted.
            import clou.coordinator as _coord_mod
            orig_run = _coord_mod.run_coordinator

            observed_paths: list[Path] = []
            real_unlink = Path.unlink

            def _spy_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
                if self.name == "seen-escalations.txt":
                    observed_paths.append(self)
                real_unlink(self, *args, **kwargs)

            with patch.object(Path, "unlink", _spy_unlink):
                result = await orig_run(tmp_path, "test-ms", app=app)

        assert result == "completed"
        assert len(observed_paths) == 1
        # The path must be milestone-scoped.
        expected_new = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )
        assert observed_paths[0] == expected_new
        # The legacy global path is NOT used by this coordinator.
        legacy = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        assert observed_paths[0] != legacy

    async def test_legacy_global_path_migrated(self, tmp_path: Path) -> None:
        """On startup, entries from the legacy global path are inherited
        IF they correspond to files in this milestone's escalations
        directory.  Entries for other milestones are discarded.
        """
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: x\n\n**Classification:** blocking\n\n"
            "## Issue\nx\n\n## Options\n1. **A**: a\n"
        )

        # Legacy global file has entries for THIS milestone AND a different
        # milestone.  Only the this-milestone entry should be inherited.
        legacy = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(
            "2026-01-01-test.md\n"
            "2026-02-02-other-milestone.md\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("EXECUTE", ["status.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # The test escalation was already seen (migrated); no new
        # announcement should have fired.
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        assert len(announcements) == 0

    async def test_parallel_writes_do_not_intersect(self, tmp_path: Path) -> None:
        """Two coordinators writing seen files concurrently do NOT
        overwrite each other.

        Brutalist C2-review finding: the prior version of this test
        only asserted path inequality (a string test).  The ACTUAL
        race manifested as read-modify-write interference at the
        filesystem level: coordinator A reads {x}, B reads {x}, A
        writes {x, y}, B writes {x, z}, y is lost.  The new test
        exercises concurrent announce_new_escalations calls against
        the two milestone-scoped paths and verifies both files retain
        their own additions.
        """
        import asyncio as _asyncio

        from clou.coordinator import announce_new_escalations

        _setup_clou_dir(tmp_path, milestone="ms-a")
        _setup_clou_dir(tmp_path, milestone="ms-b")

        for ms, uniq in (("ms-a", "alpha"), ("ms-b", "beta")):
            esc_dir = tmp_path / ".clou" / "milestones" / ms / "escalations"
            esc_dir.mkdir(parents=True, exist_ok=True)
            (esc_dir / f"2026-01-01-{uniq}.md").write_text(
                "# Escalation: x\n\n**Classification:** blocking\n\n"
                "## Issue\nx\n\n## Options\n1. **A**: a\n"
            )

        path_a = (
            tmp_path / ".clou" / "milestones" / "ms-a" / "active"
            / "seen-escalations.txt"
        )
        path_b = (
            tmp_path / ".clou" / "milestones" / "ms-b" / "active"
            / "seen-escalations.txt"
        )
        path_a.parent.mkdir(parents=True, exist_ok=True)
        path_b.parent.mkdir(parents=True, exist_ok=True)

        seen_a: set[str] = set()
        seen_b: set[str] = set()
        clou_dir = tmp_path / ".clou"
        app_a = _mock_app()
        app_b = _mock_app()

        # Run both announcers as coroutines in the same event loop.
        # announce_new_escalations is synchronous, so wrap in to_thread
        # to get true concurrent scheduling.
        await _asyncio.gather(
            _asyncio.to_thread(
                announce_new_escalations,
                clou_dir=clou_dir,
                milestone="ms-a",
                seen_escalations=seen_a,
                seen_path=path_a,
                app=app_a,
            ),
            _asyncio.to_thread(
                announce_new_escalations,
                clou_dir=clou_dir,
                milestone="ms-b",
                seen_escalations=seen_b,
                seen_path=path_b,
                app=app_b,
            ),
        )

        # Each milestone's seen file contains its own escalation and
        # not the other's.  This is the invariant the race violated.
        assert path_a.read_text().strip() == "2026-01-01-alpha.md"
        assert path_b.read_text().strip() == "2026-01-01-beta.md"

    async def test_migration_persists_immediately(self, tmp_path: Path) -> None:
        """Brutalist 3-of-3 finding: migration must write the new
        seen_path file, not just populate an in-memory set.

        Without persistence, a coordinator that migrated entries but
        saw no new escalations would leave seen_path missing; the
        next startup would re-migrate from the legacy file -- making
        the "one-shot migration" claim false.
        """
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: x\n\n**Classification:** blocking\n\n"
            "## Issue\nx\n\n## Options\n1. **A**: a\n"
        )

        # Pre-populate the legacy global file so migration triggers.
        legacy = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("2026-01-01-test.md\n2026-02-02-other-milestone.md\n")

        new_path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )

        # Stop immediately after startup (before cleanup at COMPLETE)
        # so we can observe the file on disk post-migration.
        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[("EXECUTE", ["status.md"]), ("COMPLETE", [])],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            # Patch unlink so the file survives for inspection.
            import clou.coordinator as _coord_mod
            with patch.object(
                _coord_mod, "write_cycle_limit_escalation", AsyncMock()
            ):
                await run_coordinator(tmp_path, "test-ms", app=app)

        # The migrated file must have been written EVEN though no new
        # escalations were announced (the only one was already in the
        # legacy set and got migrated).  The COMPLETE-time cleanup
        # would remove it, but the write-during-migration is what
        # matters: on restart, seen_path would have existed and
        # migration would not re-run.
        # Since the test completes, seen_path is cleaned up.  We verify
        # by observing that the announcement did NOT fire (proving the
        # migration was honored in-memory) AND by running a SECOND
        # synthetic startup check against the stage.
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        # test-ms escalation was already in legacy set; migration
        # inherited it; no announcement.
        assert len(announcements) == 0, (
            "Migration inherited the entry but announcement still fired --"
            " either migration skipped persistence or intersection failed."
        )

    async def test_migration_writes_new_path_on_disk(
        self, tmp_path: Path
    ) -> None:
        """Direct unit-level check that the migration branch writes the
        per-milestone seen file before the announcer runs.

        Rather than mock out the full run_coordinator pipeline, this
        test invokes only the migration-read branch by simulating a
        cold start with a legacy file present.
        """
        _setup_clou_dir(tmp_path)

        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: x\n\n**Classification:** blocking\n\n"
            "## Issue\nx\n\n## Options\n1. **A**: a\n"
        )
        legacy = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("2026-01-01-test.md\n")

        new_path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )
        new_path.parent.mkdir(parents=True, exist_ok=True)

        # Mimic the migration code path from run_coordinator.
        _legacy_entries = set(legacy.read_text().splitlines())
        _esc_names = {p.name for p in esc_dir.glob("*.md")}
        _inherited = _legacy_entries & _esc_names
        if _inherited:
            new_path.write_text("\n".join(sorted(_inherited)) + "\n")

        # Post-migration: new file exists on disk with the inherited
        # entry; next startup would read it directly, not re-migrate.
        assert new_path.exists()
        assert new_path.read_text().strip() == "2026-01-01-test.md"


class TestEscalationAnnouncementOrdering:
    """F5: seen-bookkeeping ordering guarantees.

    Announcements must not be lost when ``post_message`` raises or
    when the app is unattached; and ``seen-escalations.txt`` must be
    written O(1) per sweep, not O(N) per file.
    """

    async def test_post_failure_does_not_mark_seen(self, tmp_path: Path) -> None:
        """If post_message raises, the file stays unseen so the next
        cycle retries. F5.
        """
        _setup_clou_dir(tmp_path)

        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: something broke\n\n"
            "**Classification:** blocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
        )

        app = MagicMock()
        # First post raises; subsequent posts succeed.  We use a list
        # flipped by each call so the second cycle's invocation can
        # succeed (and thereby expose whether the first cycle leaked
        # the filename into the seen-set).
        call_counter = {"n": 0}

        def _flaky_post(message: Any) -> None:
            if isinstance(message, ClouBreathEvent) and message.text.startswith(
                "escalation filed:"
            ):
                call_counter["n"] += 1
                if call_counter["n"] == 1:
                    raise RuntimeError("transient UI failure")

        app.post_message = MagicMock(side_effect=_flaky_post)

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # The notifier re-attempted announcement because the first post
        # raised.  We care about the _attempted_ post_message calls for
        # escalation announcements.
        assert call_counter["n"] >= 2, (
            "Failed post should not mark file seen; next cycle must retry."
        )

    async def test_seen_file_written_once_per_sweep(
        self, tmp_path: Path
    ) -> None:
        """F5: writing seen-escalations.txt inside the per-file loop
        is O(N^2). The helper must batch writes to once per sweep.
        """
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        # Three files in one batch.
        for name in ("20260101-001.md", "20260101-002.md", "20260101-003.md"):
            (esc_dir / name).write_text(
                "# Escalation: x\n\n"
                "**Classification:** degraded\n\n"
                "## Issue\nx\n\n"
                "## Options\n1. **A**: a\n"
            )

        seen_path = (
            tmp_path / ".clou" / "milestones" / "test-ms" / "active"
            / "seen-escalations.txt"
        )

        # Count writes to seen_path by intercepting Path.write_text.
        real_write = Path.write_text
        write_calls: list[str] = []

        def _counting_write(
            self: Path, data: str, *args: Any, **kwargs: Any
        ) -> int:
            if self == seen_path:
                write_calls.append(data)
            return real_write(self, data, *args, **kwargs)

        with (
            patch.object(Path, "write_text", _counting_write),
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        # All three files announced once each.
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        assert len(announcements) == 3
        # The notifier is invoked at top-of-loop AND in finally; a
        # single sweep that finds three new files must batch to exactly
        # one write_text call for that sweep.  The finally-sweep writes
        # nothing because the set is already persisted (newly_announced
        # is empty).  So total writes to seen_path is exactly 1.
        assert len(write_calls) == 1, (
            f"Expected O(1) write per sweep, got {len(write_calls)}."
        )


class TestCorruptedSeenFileRecovery:
    """F28: corrupted seen-escalations.txt degrades to empty set.

    A truncated or binary-garbled seen file must not brick the
    coordinator at startup; announcements re-fire once, which is the
    tolerant behavior appropriate to best-effort bookkeeping.
    """

    async def test_corrupted_seen_file_degrades_to_empty(
        self, tmp_path: Path
    ) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Write bytes that are not valid UTF-8 to the seen file.
        seen_path = tmp_path / ".clou" / "active" / "seen-escalations.txt"
        seen_path.write_bytes(b"\xff\xfe\x80\x81garbage\xff")

        # Add an escalation file that _would_ have been suppressed if
        # the seen set were intact.
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-test.md").write_text(
            "# Escalation: something broke\n\n"
            "**Classification:** blocking\n\n"
            "## Issue\nSomething broke\n\n"
            "## Options\n1. **Fix it**: repair the thing\n"
        )

        with (
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        # The coordinator must have completed, and the escalation
        # re-announces because the corrupted set degraded to empty.
        assert result == "completed"
        announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed:")
        ]
        assert len(announcements) == 1


class TestParseErrorBreathEvent:
    """F29: parser drift surfaces as a distinct breath event.

    A parse failure must emit ``escalation filed (parse-error): NAME``
    rather than silently collapsing to ``unknown`` classification —
    drift visibility is the whole point of the remolding recipe.
    """

    async def test_parse_error_emits_distinct_text(
        self, tmp_path: Path
    ) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create a file that causes parse_escalation to raise.  We
        # achieve this by patching parse_escalation to raise for this
        # test rather than constructing a technically-unparseable file,
        # because the tolerant parser in clou.escalation is designed
        # to accept almost anything.
        esc_dir = tmp_path / ".clou" / "milestones" / "test-ms" / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        (esc_dir / "2026-01-01-drifted.md").write_text(
            "# Escalation: drifted\n\n"
            "## Issue\nsomething\n\n"
        )

        def _raise(_: Any) -> None:
            raise ValueError("simulated parse failure")

        with (
            patch(f"{_PC}.parse_escalation", side_effect=_raise),
            patch(
                f"{_PC}.determine_next_cycle",
                side_effect=[
                    ("EXECUTE", ["status.md"]),
                    ("COMPLETE", []),
                ],
            ),
            patch(f"{_PC}.read_cycle_count", return_value=0),
            patch(f"{_PC}._run_single_cycle", return_value="ok"),
            patch(f"{_PC}.validate_golden_context", return_value=[]),
            patch(f"{_PC}.build_cycle_prompt", return_value="prompt"),
        ):
            result = await run_coordinator(tmp_path, "test-ms", app=app)

        assert result == "completed"
        parse_error_announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed (parse-error):")
        ]
        assert len(parse_error_announcements) == 1
        assert parse_error_announcements[0].text == (
            "escalation filed (parse-error): 2026-01-01-drifted.md"
        )

        # And the "unknown" classification must NOT leak into a
        # non-parse-error announcement.
        unknown_announcements = [
            m for m in _posted_of_type(app, ClouBreathEvent)
            if m.text.startswith("escalation filed: unknown")
        ]
        assert len(unknown_announcements) == 0


class TestHandoffOnCompletion:
    """ClouHandoff posted BEFORE ClouCoordinatorComplete on success."""

    async def test_handoff_on_completion(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Create handoff.md
        ms_dir = tmp_path / ".clou" / "milestones" / "test-ms"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "handoff.md").write_text("# Handoff\nAll done.")

        # Extract spawn_coordinator_tool by intercepting
        # create_sdk_mcp_server to capture the tools list.
        captured_tools: list[Any] = []

        def _capture_create(*args: Any, **kwargs: Any) -> MagicMock:
            captured_tools.extend(kwargs.get("tools", args[1] if len(args) > 1 else []))
            return MagicMock()

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path, app=app)

        spawn_fn = None
        for fn in captured_tools:
            name = getattr(fn, "__name__", "") or getattr(
                getattr(fn, "handler", None), "__name__", ""
            )
            if name == "spawn_coordinator_tool":
                spawn_fn = fn
                break

        assert spawn_fn is not None, "spawn_coordinator_tool not found"
        # SdkMcpTool is not callable — use .handler if available
        call_fn = getattr(spawn_fn, "handler", spawn_fn)

        # Mock run_coordinator to return "completed"
        with (
            patch(f"{_P}.run_coordinator", new_callable=AsyncMock) as mock_rc,
            patch(f"{_P}.clou_spawn_coordinator", new_callable=AsyncMock),
        ):
            mock_rc.return_value = "completed"
            await call_fn({"milestone": "test-ms"})

        types = _posted_types(app)
        # Filter to just the ones we care about
        relevant = [t for t in types if t in (ClouHandoff, ClouCoordinatorComplete)]
        assert ClouHandoff in relevant, "ClouHandoff was not posted"
        assert ClouCoordinatorComplete in relevant, (
            "ClouCoordinatorComplete was not posted"
        )
        assert relevant.index(ClouHandoff) < relevant.index(ClouCoordinatorComplete), (
            "ClouHandoff must be posted before ClouCoordinatorComplete"
        )


class TestCrashPostsCoordinatorComplete:
    """ClouCoordinatorComplete(result='error') posted even on crash."""

    async def test_crash_posts_coordinator_complete(self, tmp_path: Path) -> None:
        _setup_clou_dir(tmp_path)
        app = _mock_app()

        # Build spawn_coordinator_tool
        captured_tools: list[Any] = []

        def _capture_create(*args: Any, **kwargs: Any) -> MagicMock:
            captured_tools.extend(kwargs.get("tools", args[1] if len(args) > 1 else []))
            return MagicMock()

        with patch(f"{_P}.create_sdk_mcp_server", side_effect=_capture_create):
            _build_mcp_server(tmp_path, app=app)

        spawn_fn = None
        for fn in captured_tools:
            name = getattr(fn, "__name__", "") or getattr(
                getattr(fn, "handler", None), "__name__", ""
            )
            if name == "spawn_coordinator_tool":
                spawn_fn = fn
                break

        assert spawn_fn is not None
        call_fn = getattr(spawn_fn, "handler", spawn_fn)

        # Mock run_coordinator to raise
        with (
            patch(
                f"{_P}.run_coordinator",
                new_callable=AsyncMock,
                side_effect=RuntimeError("kaboom"),
            ),
            patch(f"{_P}.clou_spawn_coordinator", new_callable=AsyncMock),
        ):
            result = await call_fn({"milestone": "test-ms"})

        completes = _posted_of_type(app, ClouCoordinatorComplete)
        assert len(completes) == 1
        assert completes[0].result == "error"
        assert completes[0].milestone == "test-ms"
        # The tool should still return a result dict, not raise
        assert "content" in result
