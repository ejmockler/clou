"""Clou message types — events that flow between the SDK bridge and UI widgets.

Every message inherits from ``textual.message.Message`` and stores its
constructor arguments as typed instance attributes.
"""

from __future__ import annotations

from pathlib import Path

from textual.message import Message

from clou.ui.mode import Mode

# Re-export so ``from clou.ui.messages import Mode`` keeps working.
__all__ = ["Mode"]

# ---------------------------------------------------------------------------
# Supervisor / Dialogue messages
# ---------------------------------------------------------------------------


class ClouSupervisorText(Message):
    """Supervisor assistant text for the conversation."""

    def __init__(self, text: str, model: str) -> None:
        self.text = text
        self.model = model
        super().__init__()


class ClouThinking(Message):
    """Model thinking/reasoning block."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ClouStreamChunk(Message):
    """Partial streaming token for live rendering."""

    def __init__(self, text: str, uuid: str) -> None:
        self.text = text
        self.uuid = uuid
        super().__init__()


class ClouToolUse(Message):
    """Supervisor is using a tool."""

    def __init__(
        self, name: str, tool_input: dict[str, object], tool_use_id: str = "",
    ) -> None:
        self.name = name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id
        super().__init__()


class ClouToolResult(Message):
    """Tool result returned."""

    def __init__(self, tool_use_id: str, content: str, is_error: bool) -> None:
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error
        super().__init__()


class ClouTurnComplete(Message):
    """Supervisor turn finished."""

    def __init__(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        duration_ms: int,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.duration_ms = duration_ms
        super().__init__()


class ClouTurnContentReady(Message):
    """Completed assistant content ready for persistence.

    Posted by ConversationWidget after computing the turn's content
    (streamed or non-streamed).  ClouApp persists it to session/history
    without reaching into widget internals.
    """

    def __init__(self, content: str) -> None:
        self.content = content
        super().__init__()


class ClouProcessingStarted(Message):
    """A queued user message is now being processed by the model."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ClouRateLimit(Message):
    """Rate limit state change."""

    def __init__(self, status: str, resets_at: int | None) -> None:
        self.status = status
        self.resets_at = resets_at
        super().__init__()


# ---------------------------------------------------------------------------
# Coordinator / Breath messages
# ---------------------------------------------------------------------------


class ClouStatusUpdate(Message):
    """Coordinator cycle status for the status bar."""

    def __init__(self, cycle_type: str, cycle_num: int, phase: str) -> None:
        self.cycle_type = cycle_type
        self.cycle_num = cycle_num
        self.phase = phase
        super().__init__()


class ClouCoordinatorSpawned(Message):
    """Coordinator session started for a milestone."""

    def __init__(self, milestone: str) -> None:
        self.milestone = milestone
        super().__init__()


class ClouBreathEvent(Message):
    """Curated status line from coordinator activity."""

    def __init__(self, text: str, cycle_type: str, phase: str | None) -> None:
        self.text = text
        self.cycle_type = cycle_type
        self.phase = phase
        super().__init__()


class ClouAgentSpawned(Message):
    """Agent team member dispatched."""

    def __init__(self, task_id: str, description: str) -> None:
        self.task_id = task_id
        self.description = description
        super().__init__()


class ClouAgentProgress(Message):
    """Agent team member working."""

    def __init__(
        self,
        task_id: str,
        last_tool: str | None,
        total_tokens: int,
        tool_uses: int,
    ) -> None:
        self.task_id = task_id
        self.last_tool = last_tool
        self.total_tokens = total_tokens
        self.tool_uses = tool_uses
        super().__init__()


class ClouAgentComplete(Message):
    """Agent team member finished."""

    def __init__(self, task_id: str, status: str, summary: str) -> None:
        self.task_id = task_id
        self.status = status
        self.summary = summary
        super().__init__()


class ClouCycleComplete(Message):
    """Coordinator cycle finished."""

    def __init__(
        self,
        cycle_num: int,
        cycle_type: str,
        next_step: str,
        phase_status: dict[str, object],
    ) -> None:
        self.cycle_num = cycle_num
        self.cycle_type = cycle_type
        self.next_step = next_step
        self.phase_status = phase_status
        super().__init__()


class ClouCoordinatorComplete(Message):
    """Coordinator finished for milestone."""

    def __init__(self, milestone: str, result: str) -> None:
        self.milestone = milestone
        self.result = result
        super().__init__()


# ---------------------------------------------------------------------------
# Escalation messages
# ---------------------------------------------------------------------------


class ClouEscalationArrived(Message):
    """Escalation requires user decision."""

    def __init__(
        self,
        path: Path,
        classification: str,
        issue: str,
        options: list[dict[str, object]],
    ) -> None:
        self.path = path
        self.classification = classification
        self.issue = issue
        self.options = options
        super().__init__()


class ClouEscalationResolved(Message):
    """User resolved an escalation."""

    def __init__(self, path: Path, disposition: str) -> None:
        self.path = path
        self.disposition = disposition
        super().__init__()


# ---------------------------------------------------------------------------
# Handoff messages
# ---------------------------------------------------------------------------


class ClouDagUpdate(Message):
    """Updated task graph for DAG viewer."""

    def __init__(self, tasks: list[dict[str, str]], deps: dict[str, list[str]]) -> None:
        self.tasks = tasks
        self.deps = deps
        super().__init__()


class ClouHandoff(Message):
    """Milestone handoff ready."""

    def __init__(self, milestone: str, handoff_path: Path) -> None:
        self.milestone = milestone
        self.handoff_path = handoff_path
        super().__init__()


# ---------------------------------------------------------------------------
# Metrics (continuous)
# ---------------------------------------------------------------------------


class ClouMetrics(Message):
    """Updated token/cost metrics."""

    def __init__(
        self,
        tier: str,
        milestone: str | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
    ) -> None:
        self.tier = tier
        self.milestone = milestone
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        super().__init__()
