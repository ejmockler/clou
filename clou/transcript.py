"""Agent transcript store -- shared data layer for tool call capture.

Records TranscriptEntry objects keyed by agent_id. The capture hook
(PostToolUse) writes entries; the UI enrichment layer reads them to
populate real input/output data on completed-agent detail screens.

Public API:
    TranscriptEntry   -- per-tool-call transcript dataclass
    TranscriptStore   -- bounded per-agent storage with task mapping
    get_store         -- module-level singleton accessor
    reset_store       -- reset singleton (testing / cycle boundaries)
    strip_ansi        -- remove ANSI escape sequences from text
    truncate_output   -- strip ANSI then truncate tool response text
    truncate_input    -- strip ANSI and truncate string values in tool_input
    MAX_OUTPUT_LENGTH -- character limit for truncated output
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_OUTPUT_LENGTH: int = 2000  # Characters

# ANSI escape sequence pattern -- mirrors clou.ui.bridge._ANSI_ESCAPE_RE.
# Comprehensive per ECMA-48: CSI, OSC, DCS/PM/APC/SOS, charset, Fe/Fs/Fp/nF, 8-bit C1.
_ANSI_ESCAPE_RE = re.compile(
    # CSI: ESC [ (params)(intermediates) final byte 0x40-0x7E
    r'\x1b\[[\x20-\x3f]*[\x40-\x7e]'
    # OSC: ESC ] ... (BEL | ESC\ | 0x9C)
    r'|\x1b\][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)'
    # DCS/PM/APC/SOS: ESC P/^/_/X ... through ST
    r'|\x1b[P^_X][^\x1b\x9c]*(?:\x1b\\|\x9c)?'
    # Charset designation: ESC ( X, ESC ) X
    r'|\x1b[()].?'
    # Other 2-byte ESC sequences (Fe, Fs, Fp, nF)
    r'|\x1b[\x20-\x2f]*[\x30-\x7e]'
    # 8-bit C1 control codes (0x80-0x9F)
    r'|[\x80-\x9f]'
    # Lone trailing ESC (incomplete sequence at end of string)
    r'|\x1b$'
)


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from *text*.

    Uses the same comprehensive regex as ``clou.ui.bridge._strip_ansi``,
    with a belt-and-suspenders pass removing any surviving ESC bytes.
    """
    result = _ANSI_ESCAPE_RE.sub('', text)
    if '\x1b' in result:
        result = result.replace('\x1b', '')
    return result


# ---------------------------------------------------------------------------
# Output truncation
# ---------------------------------------------------------------------------


def truncate_output(text: str, max_length: int = MAX_OUTPUT_LENGTH) -> str:
    """Truncate tool output with '... (truncated)' indicator.

    ANSI escape sequences are stripped **before** length measurement so that
    invisible control codes do not consume the character budget.

    Returns the original string unchanged when it fits within *max_length*.
    Empty strings pass through untouched.
    """
    text = strip_ansi(text)
    if len(text) <= max_length:
        return text
    return text[:max_length] + "... (truncated)"


def truncate_input(tool_input: dict[str, object]) -> dict[str, object]:
    """Return a shallow copy of *tool_input* with string values sanitized.

    ANSI escape sequences are stripped and long strings are truncated.
    Non-string values pass through unchanged.  Uses :func:`truncate_output`
    with the default ``MAX_OUTPUT_LENGTH`` limit for each string value.
    """
    return {
        k: truncate_output(v) if isinstance(v, str) else v
        for k, v in tool_input.items()
    }


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TranscriptEntry:
    """A single tool call transcript captured from a PostToolUse hook."""

    tool_name: str  # "Read", "Edit", "Bash", etc.
    tool_input: dict[str, object] = field(default_factory=dict)
    tool_response: str = ""  # Tool response text (truncated)
    timestamp: float = field(default_factory=time.monotonic)
    tool_use_id: str = ""  # SDK's tool_use_id for correlation


# ---------------------------------------------------------------------------
# TranscriptStore
# ---------------------------------------------------------------------------


class TranscriptStore:
    """Bounded per-agent storage for tool call transcripts.

    Entries are recorded by agent_id and can be looked up either by
    agent_id directly or by task_name through a registered mapping.
    """

    MAX_ENTRIES_PER_AGENT: int = 500  # Consistent with MAX_TOOL_HISTORY

    def __init__(self) -> None:
        self._entries: dict[str, list[TranscriptEntry]] = {}
        self._agent_to_task: dict[str, str] = {}
        self._task_to_agents: dict[str, list[str]] = {}

    def record(self, agent_id: str, entry: TranscriptEntry) -> None:
        """Append a transcript entry for the given agent. Bounded."""
        entries = self._entries.setdefault(agent_id, [])
        entries.append(entry)
        if len(entries) > self.MAX_ENTRIES_PER_AGENT:
            self._entries[agent_id] = entries[-self.MAX_ENTRIES_PER_AGENT :]

    def get_entries(self, agent_id: str) -> list[TranscriptEntry]:
        """Return all entries for the given agent_id (copy)."""
        return list(self._entries.get(agent_id, []))

    def register_task_mapping(self, agent_id: str, task_name: str) -> None:
        """Map an agent_id to a task_name for UI lookup."""
        self._agent_to_task[agent_id] = task_name
        agents = self._task_to_agents.setdefault(task_name, [])
        if agent_id not in agents:
            agents.append(agent_id)

    def get_by_task(self, task_name: str) -> list[TranscriptEntry]:
        """Look up entries by task_name (via registered mapping).

        Returns entries from all agent_ids mapped to this task_name,
        sorted by timestamp (chronological).  Returns an empty list for
        unmapped task names.
        """
        agent_ids = self._task_to_agents.get(task_name, [])
        result: list[TranscriptEntry] = []
        for aid in agent_ids:
            result.extend(self._entries.get(aid, []))
        result.sort(key=lambda e: e.timestamp)
        return result

    def get_latest_entry(self, agent_id: str) -> TranscriptEntry | None:
        """Return the most recent entry for *agent_id*, or ``None``."""
        entries = self._entries.get(agent_id)
        if not entries:
            return None
        return entries[-1]

    def clear(self) -> None:
        """Clear all entries and mappings. Called between cycles."""
        self._entries.clear()
        self._agent_to_task.clear()
        self._task_to_agents.clear()

    def agent_ids(self) -> list[str]:
        """Return all agent_ids with recorded entries."""
        return list(self._entries.keys())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: TranscriptStore | None = None


def get_store() -> TranscriptStore:
    """Return the module-level TranscriptStore singleton (auto-creating)."""
    global _store
    if _store is None:
        _store = TranscriptStore()
    return _store


def reset_store() -> None:
    """Reset the singleton (for testing and cycle boundaries)."""
    global _store
    _store = None
