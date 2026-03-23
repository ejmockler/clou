"""Bridge — SDK message to Clou message routing.

Translates Claude Agent SDK message types into Clou's Textual message
types.  Uses duck-typing throughout so SDK classes need not be imported
(keeps the module testable without a live SDK install).

Public API:
    extract_coordinator_status(msg_content, cycle_type) -> str | None
    extract_stream_text(event)                          -> str | None
    parse_escalation(path)                              -> dict
    route_supervisor_message(msg, post)                 -> None
    route_coordinator_message(msg, milestone, cycle_type, post) -> None
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clou.ui.messages import (
    ClouAgentComplete,
    ClouAgentProgress,
    ClouAgentSpawned,
    ClouBreathEvent,
    ClouMetrics,
    ClouRateLimit,
    ClouStreamChunk,
    ClouSupervisorText,
    ClouThinking,
    ClouToolResult,
    ClouToolUse,
    ClouTurnComplete,
)

_log = logging.getLogger(__name__)

# Strip ANSI escape sequences from untrusted text before posting to the UI.
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


def _strip_ansi(text: str | None) -> str:
    """Strip ANSI escape sequences from text."""
    if text is None:
        return ""
    result = _ANSI_ESCAPE_RE.sub('', text)
    # Belt-and-suspenders: strip any surviving ESC bytes (e.g. from
    # consecutive-ESC edge cases where regex left-to-right scan skips one).
    # Raw ESC (0x1B) has no legitimate purpose in display text.
    if '\x1b' in result:
        result = result.replace('\x1b', '')
    return result


# Re-usable pattern for numbered list items in escalation Options section.
_OPTION_RE = re.compile(
    r"^\s*(\d+)\.\s*\*\*(.+?)\*\*[:\s]*(.*)$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Coordinator status extraction
# ---------------------------------------------------------------------------


def extract_coordinator_status(
    msg_content: list[Any],
    cycle_type: str,
) -> str | None:
    """Extract a curated status line from coordinator content blocks.

    *msg_content* is the ``.content`` list from an AssistantMessage (a list
    of content blocks).  Returns ``None`` for messages that should not become
    breath events.
    """
    for block in msg_content:
        # ToolUseBlock-like: has .name and .input
        if hasattr(block, "name") and hasattr(block, "input"):
            name: str = _strip_ansi(block.name)
            tool_input: dict[str, Any] = (
                block.input if isinstance(block.input, dict) else {}
            )

            if name in ("Write", "Edit"):
                file_path: str = _strip_ansi(tool_input.get("file_path", ""))
                if "compose.py" in file_path:
                    return "compose.py updated"
                if "execution.md" in file_path:
                    return None
                if "phase.md" in file_path:
                    phase = Path(file_path).parent.name
                    return f"phase:{phase} spec written"
                if "decisions.md" in file_path:
                    return "decision logged"
                if "status.md" in file_path:
                    return None
                return None

            if name == "Agent":
                desc: str = tool_input.get("description", "")
                return f"dispatching {desc[:50]}"

            if name.startswith("mcp__brutalist__"):
                tool_short = name.replace("mcp__brutalist__", "")
                return f"brutalist {tool_short}"

            return None

        # TextBlock-like: has .text
        if hasattr(block, "text"):
            text_lower: str = block.text.lower()
            if "phase complete" in text_lower or "moving to" in text_lower:
                return _extract_transition_summary(block.text)

    return None


def _extract_transition_summary(text: str) -> str:
    """Pull a short summary from a phase-transition text block."""
    for line in text.splitlines():
        lower = line.lower()
        if "phase complete" in lower or "moving to" in lower:
            return line.strip()[:80]
    return text.strip()[:80]


# ---------------------------------------------------------------------------
# Stream text extraction
# ---------------------------------------------------------------------------


def extract_stream_text(event: dict[str, Any]) -> str | None:
    """Extract a text delta from a raw StreamEvent dict.

    Returns ``None`` for non-text events.
    """
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return None
    text = delta.get("text")
    if isinstance(text, str):
        return text
    return None


# ---------------------------------------------------------------------------
# Escalation parsing
# ---------------------------------------------------------------------------


def parse_escalation(path: Path) -> dict[str, Any]:
    """Parse an escalation markdown file into structured data.

    Expected sections: ``## Classification``, ``## Issue``,
    ``## Options``, ``## Recommendation``.
    """
    raw = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in raw.splitlines():
        if line.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[3:].strip().lower()
            current_lines = []
        else:
            current_lines.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()

    options: list[dict[str, str]] = []
    for match in _OPTION_RE.finditer(sections.get("options", "")):
        options.append(
            {
                "label": match.group(2).strip(),
                "description": match.group(3).strip(),
            }
        )

    return {
        "classification": sections.get("classification", ""),
        "issue": sections.get("issue", ""),
        "options": options,
        "recommendation": sections.get("recommendation", ""),
    }


# ---------------------------------------------------------------------------
# Supervisor message routing
# ---------------------------------------------------------------------------


def route_supervisor_message(
    msg: Any,
    post: Callable[[Any], object],
) -> None:
    """Route a supervisor SDK message to the appropriate Clou message.

    *post* is a callable that accepts a ``Message`` and posts it
    (e.g. ``app.post_message``).
    """
    # AssistantMessage — has .content list
    if hasattr(msg, "content") and isinstance(msg.content, list):
        # Surface error field (auth failures, billing errors, etc.)
        error = getattr(msg, "error", None)
        if error:
            model = _strip_ansi(getattr(msg, "model", "") or "")
            post(ClouSupervisorText(text=_strip_ansi(str(error)), model=model))
        for block in msg.content:
            if hasattr(block, "text") and not hasattr(block, "thinking"):
                model = _strip_ansi(getattr(msg, "model", "") or "")
                post(ClouSupervisorText(text=_strip_ansi(block.text), model=model))
            elif hasattr(block, "thinking"):
                post(ClouThinking(text=_strip_ansi(block.thinking)))
            elif hasattr(block, "name") and hasattr(block, "input"):
                tool_input = block.input if isinstance(block.input, dict) else {}
                post(ClouToolUse(name=_strip_ansi(block.name), tool_input=tool_input))
            elif hasattr(block, "tool_use_id") and hasattr(block, "content"):
                content = block.content
                if isinstance(content, list):
                    content = str(content)
                post(ClouToolResult(
                    tool_use_id=_strip_ansi(getattr(block, "tool_use_id", "")),
                    content=_strip_ansi(content if isinstance(content, str) else ""),
                    is_error=bool(getattr(block, "is_error", False)),
                ))
        return

    # StreamEvent — has .event dict
    if hasattr(msg, "event") and isinstance(msg.event, dict):
        text = extract_stream_text(msg.event)
        if text:
            post(ClouStreamChunk(text=_strip_ansi(text), uuid=_strip_ansi(getattr(msg, "uuid", ""))))
        return

    # ResultMessage — has .usage dict
    if hasattr(msg, "usage") and isinstance(getattr(msg, "usage", None), dict):
        usage: dict[str, Any] = msg.usage
        post(
            ClouTurnComplete(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=getattr(msg, "total_cost_usd", None),
                duration_ms=getattr(msg, "duration_ms", 0),
            )
        )
        return

    # RateLimitEvent — has .rate_limit_info
    if hasattr(msg, "rate_limit_info"):
        rli = msg.rate_limit_info
        post(
            ClouRateLimit(
                status=getattr(rli, "status", ""),
                resets_at=getattr(rli, "resets_at", None),
            )
        )
        return


# ---------------------------------------------------------------------------
# Coordinator message routing
# ---------------------------------------------------------------------------


def route_coordinator_message(
    msg: Any,
    milestone: str,
    cycle_type: str,
    post: Callable[[Any], object],
) -> None:
    """Route a coordinator SDK message to breath-mode Clou messages.

    *post* is a callable that accepts a ``Message`` and posts it.
    """
    # AssistantMessage — has .content list
    if hasattr(msg, "content") and isinstance(msg.content, list):
        # Surface error field (auth failures, billing errors, etc.)
        error = getattr(msg, "error", None)
        if error:
            err_text = _strip_ansi(str(error))
            post(ClouBreathEvent(
                text=err_text, cycle_type=cycle_type, phase=None,
            ))
        text = extract_coordinator_status(msg.content, cycle_type)
        if text:
            post(ClouBreathEvent(
                text=_strip_ansi(text),
                cycle_type=cycle_type,
                phase=None,
            ))
        return

    # TaskStartedMessage — has .task_id + .description (no .status, no .last_tool_name)
    if (
        hasattr(msg, "task_id")
        and hasattr(msg, "description")
        and not hasattr(msg, "status")
        and not hasattr(msg, "last_tool_name")
    ):
        post(ClouAgentSpawned(task_id=_strip_ansi(msg.task_id), description=_strip_ansi(msg.description)))
        return

    # TaskProgressMessage — has .task_id + .last_tool_name
    if hasattr(msg, "task_id") and hasattr(msg, "last_tool_name"):
        usage = getattr(msg, "usage", {}) or {}
        post(
            ClouAgentProgress(
                task_id=_strip_ansi(msg.task_id),
                last_tool=_strip_ansi(msg.last_tool_name),
                total_tokens=usage.get("total_tokens", 0),
                tool_uses=usage.get("tool_uses", 0),
            )
        )
        return

    # TaskNotificationMessage — has .task_id + .status + .summary
    if hasattr(msg, "task_id") and hasattr(msg, "status") and hasattr(msg, "summary"):
        post(
            ClouAgentComplete(
                task_id=_strip_ansi(msg.task_id),
                status=_strip_ansi(msg.status),
                summary=_strip_ansi(msg.summary),
            )
        )
        return

    # ResultMessage — has .usage dict
    if hasattr(msg, "usage") and isinstance(getattr(msg, "usage", None), dict):
        usage = msg.usage
        post(
            ClouMetrics(
                tier="coordinator",
                milestone=milestone,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=getattr(msg, "total_cost_usd", None),
            )
        )
        return
