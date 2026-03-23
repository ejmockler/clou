"""Resumption context builder — three-tier reconstruction.

Given a session transcript, builds the optimal context for resuming a
conversation. Implements the three-tier architecture from §4b:

  Tier 1: Last N turns verbatim (recent context).
  Tier 2: Observation-masked summary of older turns (compressed middle).
  Tier 3: Golden context from disk (structured facts — always fresh).

The resumption prompt is injected as the first message to a new supervisor
session, replacing the normal greeting/checkpoint flow.
"""

from __future__ import annotations

from pathlib import Path

from clou.session import SessionEntry, SessionInfo, read_transcript

#: Number of recent turns to include verbatim (Tier 1).
_VERBATIM_TAIL = 20

#: Maximum characters for observation-masked content per tool entry.
_TOOL_CONTENT_CAP = 200


def build_resumption_context(
    project_dir: Path,
    session_id: str,
    *,
    verbatim_tail: int = _VERBATIM_TAIL,
) -> str:
    """Build the resumption prompt from a prior session transcript.

    Returns a string suitable for injecting as the first supervisor query.
    """
    entries = read_transcript(project_dir, session_id)
    if not entries:
        return ""

    # Separate header from messages.
    info = SessionInfo.from_entry(entries[0])
    messages = [e for e in entries if e.role != "system"]

    if not messages:
        return ""

    # --- Tier 3: Golden context (always loaded fresh by the supervisor) ---
    # We don't embed it here — the supervisor reads .clou/ files as its
    # first action. We just tell it to do so.

    # --- Tier 1: Last N turns verbatim ---
    tail = messages[-verbatim_tail:]
    older = messages[:-verbatim_tail] if len(messages) > verbatim_tail else []

    # --- Tier 2: Observation-masked summary of older turns ---
    summary_lines: list[str] = []
    if older:
        summary_lines.append(
            f"The previous session had {len(older)} earlier turns "
            f"(summarized below) followed by {len(tail)} recent turns "
            f"(shown verbatim)."
        )
        summary_lines.append("")
        summary_lines.append("### Earlier conversation summary")
        summary_lines.append("")
        summary_lines.extend(_summarize_turns(older))
        summary_lines.append("")

    # --- Assemble the prompt ---
    parts: list[str] = [
        "[SYSTEM: Resuming session from prior conversation.]",
        "",
        f"Session ID: {info.session_id}",
        f"Model: {info.model}",
        f"Original messages: {len(messages)}",
        "",
        "Read your protocol file: .clou/prompts/supervisor.md",
        "Then read .clou/project.md and .clou/roadmap.md for current project state.",
        "",
    ]

    if summary_lines:
        parts.extend(summary_lines)

    parts.append("### Recent conversation (verbatim)")
    parts.append("")
    for entry in tail:
        role_label = {"user": "User", "assistant": "Clou", "tool": "Tool"}.get(
            entry.role, entry.role.title()
        )
        content = entry.content
        # Observation masking: truncate tool outputs (Tier 2 principle).
        if entry.role == "tool" and len(content) > _TOOL_CONTENT_CAP:
            content = content[:_TOOL_CONTENT_CAP] + "... [truncated]"
        parts.append(f"**{role_label}:** {content}")
        parts.append("")

    parts.append(
        "Continue the conversation naturally. The user is resuming — "
        "acknowledge briefly that you're picking up where you left off, "
        "then proceed."
    )

    return "\n".join(parts)


def _summarize_turns(entries: list[SessionEntry]) -> list[str]:
    """Create an observation-masked summary of conversation turns.

    Preserves the action chain (what the user asked, what the assistant
    decided) while compressing tool outputs — following the JetBrains
    NeurIPS 2025 finding that observation masking outperforms LLM
    summarization.
    """
    lines: list[str] = []
    for entry in entries:
        if entry.role == "user":
            lines.append(f"- **User asked:** {_truncate(entry.content, 300)}")
        elif entry.role == "assistant":
            lines.append(f"- **Clou responded:** {_truncate(entry.content, 300)}")
        elif entry.role == "tool":
            # Observation masking: preserve the action, compress the output.
            lines.append(f"- **Tool output:** {_truncate(entry.content, 100)}")
    return lines


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit characters, adding ellipsis."""
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
