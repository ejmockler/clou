"""Transcript enrichment -- populate ToolInvocations from TranscriptStore.

For completed agents, replaces the TaskState's tool_invocations list with
entries rebuilt from the full transcript captured by the PostToolUse hook.
Active agents are left untouched (live view unchanged).

Public API:
    enrich_invocations  -- enrich a TaskState's tool_invocations in place
"""

from __future__ import annotations

from clou.transcript import get_store
from clou.ui.rendering.tool_summary import tool_summary
from clou.ui.task_graph import TaskState, ToolInvocation, categorize_tool

# Statuses that indicate the agent is no longer running.
_COMPLETED_STATUSES = frozenset({"complete", "completed", "failed", "aborted"})


def enrich_invocations(task_name: str, state: TaskState) -> None:
    """Enrich a TaskState's tool_invocations from TranscriptStore.

    For completed agents, replaces tool_invocations with enriched entries
    built from the full transcript.  For active agents, does nothing
    (live enrichment is out of scope).

    If the transcript has fewer entries than existing invocations
    (shouldn't happen, but defensive), the existing invocations are kept.
    """
    if state.status not in _COMPLETED_STATUSES:
        return  # Only enrich completed agents

    store = get_store()
    entries = store.get_by_task(task_name)
    if not entries and ":" in task_name:
        # Fallback: unmapped agents use synthetic keys like "describe:agent-123".
        # The suffix after the last ":" is the agent_id used by the capture hook.
        agent_id = task_name.rsplit(":", 1)[1]
        entries = store.get_entries(agent_id)
    if not entries:
        return  # No transcript data -- keep existing invocations

    # Defensive: if transcript has fewer entries than existing, keep existing.
    if len(entries) < len(state.tool_invocations):
        return

    enriched: list[ToolInvocation] = []
    for entry in entries:
        input_summary = tool_summary(entry.tool_name, entry.tool_input)
        enriched.append(ToolInvocation(
            name=entry.tool_name,
            timestamp=entry.timestamp,
            category=categorize_tool(entry.tool_name),
            input_summary=input_summary,
            output_summary=entry.tool_response,  # Already truncated by capture hook
        ))

    state.tool_invocations = enriched
