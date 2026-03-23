"""Tests for clou.ui.messages — all Clou message types."""

from __future__ import annotations

from pathlib import Path

from textual.message import Message

from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouBreathEvent,
    ClouCoordinatorComplete,
    ClouCoordinatorSpawned,
    ClouCycleComplete,
    ClouDagUpdate,
    ClouEscalationArrived,
    ClouEscalationResolved,
    ClouHandoff,
    ClouMetrics,
    ClouRateLimit,
    ClouStatusUpdate,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
    Mode,
)

# ---------------------------------------------------------------------------
# Mode enum
# ---------------------------------------------------------------------------


class TestMode:
    def test_has_exactly_four_members(self) -> None:
        assert len(Mode) == 4

    def test_member_names(self) -> None:
        assert {m.name for m in Mode} == {"DIALOGUE", "BREATH", "DECISION", "HANDOFF"}

    def test_values_are_distinct(self) -> None:
        values = [m.value for m in Mode]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# All message types are Message subclasses
# ---------------------------------------------------------------------------

ALL_MESSAGE_CLASSES = [
    ClouSupervisorText,
    ClouThinking,
    ClouStreamChunk,
    ClouToolUse,
    ClouToolResult,
    ClouTurnComplete,
    ClouRateLimit,
    ClouStatusUpdate,
    ClouCoordinatorSpawned,
    ClouBreathEvent,
    ClouAgentSpawned,
    ClouAgentProgress,
    ClouAgentComplete,
    ClouCycleComplete,
    ClouCoordinatorComplete,
    ClouDagUpdate,
    ClouEscalationArrived,
    ClouEscalationResolved,
    ClouHandoff,
    ClouMetrics,
]


class TestMessageSubclass:
    def test_all_inherit_from_message(self) -> None:
        for cls in ALL_MESSAGE_CLASSES:
            assert issubclass(cls, Message), f"{cls.__name__} is not a Message"


# ---------------------------------------------------------------------------
# Supervisor / Dialogue messages
# ---------------------------------------------------------------------------


class TestClouSupervisorText:
    def test_construction_and_attrs(self) -> None:
        msg = ClouSupervisorText(text="hello", model="opus")
        assert msg.text == "hello"
        assert msg.model == "opus"


class TestClouThinking:
    def test_construction_and_attrs(self) -> None:
        msg = ClouThinking(text="reasoning")
        assert msg.text == "reasoning"


class TestClouStreamChunk:
    def test_construction_and_attrs(self) -> None:
        msg = ClouStreamChunk(text="tok", uuid="abc-123")
        assert msg.text == "tok"
        assert msg.uuid == "abc-123"


class TestClouToolUse:
    def test_construction_and_attrs(self) -> None:
        inp = {"file_path": "/tmp/x"}
        msg = ClouToolUse(name="Write", tool_input=inp)
        assert msg.name == "Write"
        assert msg.tool_input == inp


class TestClouToolResult:
    def test_construction_and_attrs(self) -> None:
        msg = ClouToolResult(tool_use_id="tu_1", content="ok", is_error=False)
        assert msg.tool_use_id == "tu_1"
        assert msg.content == "ok"
        assert msg.is_error is False


class TestClouTurnComplete:
    def test_construction_and_attrs(self) -> None:
        msg = ClouTurnComplete(
            input_tokens=100, output_tokens=50, cost_usd=0.01, duration_ms=1200
        )
        assert msg.input_tokens == 100
        assert msg.output_tokens == 50
        assert msg.cost_usd == 0.01
        assert msg.duration_ms == 1200

    def test_cost_can_be_none(self) -> None:
        msg = ClouTurnComplete(
            input_tokens=0, output_tokens=0, cost_usd=None, duration_ms=0
        )
        assert msg.cost_usd is None


class TestClouRateLimit:
    def test_construction_and_attrs(self) -> None:
        msg = ClouRateLimit(status="throttled", resets_at=1700000000)
        assert msg.status == "throttled"
        assert msg.resets_at == 1700000000

    def test_resets_at_can_be_none(self) -> None:
        msg = ClouRateLimit(status="ok", resets_at=None)
        assert msg.resets_at is None


# ---------------------------------------------------------------------------
# Coordinator / Breath messages
# ---------------------------------------------------------------------------


class TestClouStatusUpdate:
    def test_construction_and_attrs(self) -> None:
        msg = ClouStatusUpdate(cycle_type="PLAN", cycle_num=3, phase="design")
        assert msg.cycle_type == "PLAN"
        assert msg.cycle_num == 3
        assert msg.phase == "design"

    def test_empty_phase(self) -> None:
        msg = ClouStatusUpdate(cycle_type="EXECUTE", cycle_num=1, phase="")
        assert msg.phase == ""


class TestClouCoordinatorSpawned:
    def test_construction_and_attrs(self) -> None:
        msg = ClouCoordinatorSpawned(milestone="m1")
        assert msg.milestone == "m1"


class TestClouBreathEvent:
    def test_construction_and_attrs(self) -> None:
        msg = ClouBreathEvent(text="phase:plan", cycle_type="PLAN", phase="plan")
        assert msg.text == "phase:plan"
        assert msg.cycle_type == "PLAN"
        assert msg.phase == "plan"

    def test_phase_can_be_none(self) -> None:
        msg = ClouBreathEvent(text="x", cycle_type="EXECUTE", phase=None)
        assert msg.phase is None


class TestClouAgentSpawned:
    def test_construction_and_attrs(self) -> None:
        msg = ClouAgentSpawned(task_id="t1", description="implement foo")
        assert msg.task_id == "t1"
        assert msg.description == "implement foo"


class TestClouAgentProgress:
    def test_construction_and_attrs(self) -> None:
        msg = ClouAgentProgress(
            task_id="t1", last_tool="Edit", total_tokens=500, tool_uses=3
        )
        assert msg.task_id == "t1"
        assert msg.last_tool == "Edit"
        assert msg.total_tokens == 500
        assert msg.tool_uses == 3

    def test_last_tool_can_be_none(self) -> None:
        msg = ClouAgentProgress(
            task_id="t1", last_tool=None, total_tokens=0, tool_uses=0
        )
        assert msg.last_tool is None


class TestClouAgentComplete:
    def test_construction_and_attrs(self) -> None:
        msg = ClouAgentComplete(task_id="t1", status="completed", summary="done")
        assert msg.task_id == "t1"
        assert msg.status == "completed"
        assert msg.summary == "done"


class TestClouCycleComplete:
    def test_construction_and_attrs(self) -> None:
        msg = ClouCycleComplete(
            cycle_num=2,
            cycle_type="EXECUTE",
            next_step="ASSESS",
            phase_status={"plan": "done"},
        )
        assert msg.cycle_num == 2
        assert msg.cycle_type == "EXECUTE"
        assert msg.next_step == "ASSESS"
        assert msg.phase_status == {"plan": "done"}


class TestClouCoordinatorComplete:
    def test_construction_and_attrs(self) -> None:
        msg = ClouCoordinatorComplete(milestone="m1", result="completed")
        assert msg.milestone == "m1"
        assert msg.result == "completed"


# ---------------------------------------------------------------------------
# Escalation messages
# ---------------------------------------------------------------------------


class TestClouEscalationArrived:
    def test_construction_and_attrs(self) -> None:
        p = Path("/tmp/escalation.md")
        options: list[dict[str, object]] = [
            {"label": "approve", "value": "yes"},
            {"label": "reject", "value": "no"},
        ]
        msg = ClouEscalationArrived(
            path=p, classification="credentials", issue="need key", options=options
        )
        assert msg.path == p
        assert msg.classification == "credentials"
        assert msg.issue == "need key"
        assert msg.options == options

    def test_accepts_path_str_str_list_dict(self) -> None:
        """Verify the documented signature: Path, str, str, list[dict]."""
        msg = ClouEscalationArrived(
            path=Path("."),
            classification="c",
            issue="i",
            options=[{"a": "b"}],
        )
        assert isinstance(msg.path, Path)
        assert isinstance(msg.classification, str)
        assert isinstance(msg.issue, str)
        assert isinstance(msg.options, list)


class TestClouEscalationResolved:
    def test_construction_and_attrs(self) -> None:
        p = Path("/tmp/escalation.md")
        msg = ClouEscalationResolved(path=p, disposition="approved")
        assert msg.path == p
        assert msg.disposition == "approved"


# ---------------------------------------------------------------------------
# Handoff messages
# ---------------------------------------------------------------------------


class TestClouHandoff:
    def test_construction_and_attrs(self) -> None:
        p = Path("/tmp/handoff.md")
        msg = ClouHandoff(milestone="m1", handoff_path=p)
        assert msg.milestone == "m1"
        assert msg.handoff_path == p


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestClouMetrics:
    def test_construction_and_attrs(self) -> None:
        msg = ClouMetrics(
            tier="supervisor",
            milestone=None,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.05,
        )
        assert msg.tier == "supervisor"
        assert msg.milestone is None
        assert msg.input_tokens == 1000
        assert msg.output_tokens == 500
        assert msg.cost_usd == 0.05

    def test_with_milestone(self) -> None:
        msg = ClouMetrics(
            tier="coordinator",
            milestone="m1",
            input_tokens=0,
            output_tokens=0,
            cost_usd=None,
        )
        assert msg.milestone == "m1"
        assert msg.cost_usd is None
