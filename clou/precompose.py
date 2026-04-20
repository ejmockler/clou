"""Pre-composition of ASSESS cycle context.

Reads raw milestone artifacts (compose.py, execution.md(s), requirements.md,
assessment.md, decisions.md) for a given phase layer and produces a single
structured summary at ``active/assess_summary.md``.  The summary is always
smaller in tokens than the sum of its inputs, reducing the coordinator's
read set from 5+ files to one pre-composed document.

DB-20 Step 1 --- compositionality reduction.

Public API:
    precompose_assess_context(milestone_dir, phase_name, co_layer_tasks) -> Path

Security note --- information aggregation (F23):
    This module intentionally aggregates content from multiple milestone
    artifacts into a single summary file.  In the current threat model
    (local filesystem, single-user), this is acceptable.  If the system
    moves to a multi-tenant or networked model, the aggregated summary
    becomes a higher-value target than any individual artifact and should
    be protected accordingly (access controls, encryption at rest, or
    scoped summaries per tenant).
"""

from __future__ import annotations

import ast
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger(__name__)

#: Regex for intent IDs (I1, I2, ...) in compose.py docstrings.
_INTENT_RE = re.compile(r"\bI\d+\b")

#: Valid phase / task names: lowercase alphanumeric, hyphens, underscores.
#: Matches the pattern used in recovery_checkpoint.py.
_SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_name(name: str, label: str) -> None:
    """Raise ValueError if *name* is not a safe filesystem component.

    Rejects path separators, traversal components (``..``), and names
    that do not match ``_SAFE_NAME_RE``.
    """
    if not name or not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid {label}: {name!r} -- must match {_SAFE_NAME_RE.pattern}"
        )


def _validate_within_boundary(path: Path, boundary: Path) -> None:
    """Raise ValueError if *path* resolves outside *boundary*.

    Also rejects symlinks so that a malicious link cannot escape the
    milestone directory.
    """
    if path.is_symlink():
        raise ValueError(
            f"Symlink detected: {path} -- refusing to follow"
        )
    resolved = path.resolve()
    boundary_resolved = boundary.resolve()
    if not str(resolved).startswith(str(boundary_resolved) + "/") and resolved != boundary_resolved:
        raise ValueError(
            f"Path {path} resolves outside milestone boundary {boundary}"
        )


# ---------------------------------------------------------------------------
# Safe I/O helpers
# ---------------------------------------------------------------------------


def _safe_read(path: Path, boundary: Path) -> str | None:
    """Read *path* with symlink and boundary checks.  Returns *None* on missing."""
    if not path.is_file():
        return None
    _validate_within_boundary(path, boundary)
    return path.read_text(encoding="utf-8")


def _safe_write(path: Path, content: str, boundary: Path) -> None:
    """Write *content* to *path* with symlink and boundary checks."""
    # Validate boundary BEFORE creating any directories (F9).
    if path.exists():
        _validate_within_boundary(path, boundary)
    # Pre-validate that the resolved target will be within boundary.
    # Path.resolve() works on non-existent paths (Python 3.6+), resolving
    # ``..`` and symlink components without requiring the file to exist.
    resolved = path.resolve()
    boundary_resolved = boundary.resolve()
    if not str(resolved).startswith(str(boundary_resolved) + "/") and resolved != boundary_resolved:
        raise ValueError(
            f"Write target {path} resolves outside milestone boundary {boundary}"
        )
    # Now safe to create directories.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal extraction helpers
# ---------------------------------------------------------------------------


def _extract_task_criteria(
    compose_source: str,
    task_names: list[str],
) -> list[dict[str, str]]:
    """Extract task name, docstring criteria, and intent IDs from compose.py.

    Returns a list of dicts with keys: name, criteria, intents.
    Only returns entries for *task_names* that appear as async function defs.
    """
    try:
        tree = ast.parse(compose_source)
    except SyntaxError:
        _log.warning("compose.py has syntax errors; skipping criteria extraction")
        return []

    results: list[dict[str, str]] = []
    name_set = set(task_names)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in name_set:
            doc = ast.get_docstring(node) or ""
            intents = ", ".join(dict.fromkeys(_INTENT_RE.findall(doc)))
            results.append({
                "name": node.name,
                "criteria": doc.strip(),
                "intents": intents or "none",
            })
    return results


def _extract_execution_summary(
    phase_dir: Path,
    task_name: str,
    boundary: Path,
) -> tuple[str, int]:
    """Read execution.md (and shards) for a task, returning compressed summary.

    Returns ``(summary_text, raw_char_count)`` where *raw_char_count* is the
    total characters read from disk (for token-reduction accounting).

    Extracts the Summary section, per-intent sections (## I1, ## I2, ...),
    and per-task outcome blocks (### T{N}: ...).  Falls back to the first
    120 lines if no Summary heading is found.
    """
    texts: list[str] = []
    raw_chars = 0

    # Main execution.md
    exec_md = phase_dir / "execution.md"
    content = _safe_read(exec_md, boundary)
    if content is not None and content:
        texts.append(content)
        raw_chars += len(content)

    # Coordinator-generated failure shards, if any.  Post-remolding,
    # worker success paths converge on execution.md; execution-*.md
    # files are only written in-process by _write_failure_shard when
    # the coordinator terminates a task (timeout / budget).  Surface
    # them so ASSESS sees the failure context alongside the canonical
    # execution.md.
    if phase_dir.is_dir():
        for shard in sorted(phase_dir.glob("execution-*.md")):
            shard_content = _safe_read(shard, boundary)
            if shard_content is not None and shard_content:
                texts.append(shard_content)
                raw_chars += len(shard_content)

    if not texts:
        return f"No execution artifacts found for {task_name}.", 0

    combined = "\n\n".join(texts)

    # Try to extract just the Summary section + task entries.
    summary_parts: list[str] = []
    in_summary = False
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## summary") or stripped.lower().startswith("# summary"):
            in_summary = True
            continue
        if in_summary:
            if stripped.startswith("## ") or stripped.startswith("# "):
                in_summary = False
            else:
                summary_parts.append(line)

    # Extract per-intent sections (## I1: ..., ## I2: ...) -- F8
    # Keep heading + status line + evidence lines (bold, list items).
    intent_blocks: list[str] = []
    in_intent = False
    current_intent: list[str] = []
    for line in combined.splitlines():
        stripped = line.strip()
        if re.match(r"^##\s+I\d+", stripped):
            if current_intent:
                intent_blocks.append("\n".join(current_intent))
            current_intent = [line]
            in_intent = True
        elif in_intent:
            if stripped.startswith("## ") or stripped.startswith("# "):
                intent_blocks.append("\n".join(current_intent))
                current_intent = []
                in_intent = False
            else:
                # Keep status lines, evidence references; trim narratives
                low = stripped.lower()
                if (
                    low.startswith("status:")
                    or stripped.startswith("**")
                    or stripped.startswith("- ")
                ):
                    current_intent.append(line)
    if current_intent:
        intent_blocks.append("\n".join(current_intent))

    # Also extract task status blocks (### T{N}: ...)
    task_blocks: list[str] = []
    in_task = False
    current_block: list[str] = []
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped.startswith("### T") and ":" in stripped:
            if current_block:
                task_blocks.append("\n".join(current_block))
            current_block = [line]
            in_task = True
        elif in_task:
            if stripped.startswith("### ") or stripped.startswith("## "):
                task_blocks.append("\n".join(current_block))
                current_block = []
                in_task = False
            else:
                # Keep status, files, tests lines; trim verbose notes
                if stripped.startswith("**") or stripped.startswith("- "):
                    current_block.append(line)
    if current_block:
        task_blocks.append("\n".join(current_block))

    # Compose compressed output
    parts: list[str] = []
    if summary_parts:
        parts.append("Summary:\n" + "\n".join(summary_parts).strip())
    if intent_blocks:
        parts.append("Intent evidence:\n" + "\n\n".join(intent_blocks))
    if task_blocks:
        parts.append("Task outcomes:\n" + "\n\n".join(task_blocks))

    if parts:
        return "\n\n".join(parts), raw_chars

    # Fallback: first 120 lines of combined text (still compressed vs raw).
    lines = combined.splitlines()[:120]
    return "\n".join(lines), raw_chars


def _extract_prior_assessment(milestone_dir: Path) -> tuple[str, int]:
    """Read assessment.md and extract prior findings.

    Returns ``(text, raw_char_count)``.
    Returns ``('First assessment for this phase.', 0)`` if missing or empty.
    """
    assessment_path = milestone_dir / "assessment.md"
    content = _safe_read(assessment_path, milestone_dir)
    if content is None or not content.strip():
        return "First assessment for this phase.", 0

    return content.strip(), len(content)


def _extract_recent_decisions(
    milestone_dir: Path,
    max_entries: int = 3,
) -> tuple[str, int]:
    """Read decisions.md and return the last *max_entries* decision entries.

    Returns ``(text, raw_char_count)``.
    Returns ``('No prior decisions.', 0)`` if missing or empty.
    """
    decisions_path = milestone_dir / "decisions.md"
    content = _safe_read(decisions_path, milestone_dir)
    if content is None:
        return "No prior decisions.", 0
    text = content.strip()
    if not text:
        return "No prior decisions.", 0

    raw_chars = len(content)

    # Split by top-level headings (## Cycle ...)
    entries: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and current:
            entries.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append("\n".join(current))

    # Take last N entries
    recent = entries[-max_entries:]
    return "\n\n".join(recent), raw_chars


def _read_requirements(milestone_dir: Path) -> tuple[str, int]:
    """Read requirements.md verbatim.

    Returns ``(text, raw_char_count)``.  Placeholder if missing.
    """
    req_path = milestone_dir / "requirements.md"
    content = _safe_read(req_path, milestone_dir)
    if content is None:
        return "No requirements.md found.", 0
    text = content.strip()
    if not text:
        return "requirements.md is empty.", len(content)
    return text, len(content)


def _char_count_tokens(text_or_count: str | int) -> int:
    """Rough token estimate: 4 chars ~ 1 token.

    Accepts either a string (len computed) or an int (char count directly).
    """
    n = text_or_count if isinstance(text_or_count, int) else len(text_or_count)
    return n // 4


def _strip_headings(text: str) -> str:
    """Replace Markdown heading markers in artifact content with bold text.

    Prevents artifact content from injecting top-level headings that could
    be confused with structural sections of the summary.
    """
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # Count heading level and convert to bold
            hashes = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[hashes:].strip()
            lines.append(f"**{rest}**")
        else:
            lines.append(line)
    return "\n".join(lines)


def _escape_html_comments(text: str) -> str:
    """Escape HTML comment delimiters in artifact content.

    Prevents artifact content from injecting ``<!-- /source -->`` or
    ``<!-- source: ... -->`` markers that would forge provenance boundaries
    in the pre-composed summary (F4).
    """
    # Replace opening and closing HTML comment delimiters with harmless text.
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def _sanitize_artifact(text: str) -> str:
    """Apply all artifact sanitization: strip headings and escape HTML comments.

    Combines ``_strip_headings`` and ``_escape_html_comments`` in a single
    call for use on all artifact content inserted between provenance markers.
    """
    return _escape_html_comments(_strip_headings(text))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def precompose_assess_context(
    milestone_dir: Path,
    phase_name: str,
    co_layer_tasks: list[str],
) -> Path:
    """Pre-compose ASSESS context into a summary file.

    Reads raw artifacts and writes a structured summary to
    ``milestone_dir / "active" / "assess_summary.md"``.
    Returns the path to the summary file.

    Raises :class:`ValueError` if *phase_name* or any entry in
    *co_layer_tasks* contains path traversal components or does not
    match the safe-name pattern.
    """
    # --- Input validation (F19) ---
    _validate_name(phase_name, "phase_name")
    for task in co_layer_tasks:
        _validate_name(task, "co_layer_tasks entry")

    timestamp = datetime.now(UTC).isoformat()
    input_chars = 0

    # 1. Read compose.py -> extract task criteria for co-layer tasks
    compose_path = milestone_dir / "compose.py"
    compose_source = _safe_read(compose_path, milestone_dir) or ""
    if compose_source:
        input_chars += len(compose_source)

    task_criteria = _extract_task_criteria(compose_source, co_layer_tasks)

    # 2. Read execution.md(s) for each co-layer task (F1: cached reads)
    execution_summaries: dict[str, str] = {}
    for task_name in co_layer_tasks:
        phase_dir = milestone_dir / "phases" / task_name
        summary, exec_chars = _extract_execution_summary(
            phase_dir, task_name, milestone_dir,
        )
        execution_summaries[task_name] = summary
        input_chars += exec_chars

    # 3. Read requirements.md (verbatim) -- F1: single read
    requirements_text, req_chars = _read_requirements(milestone_dir)
    input_chars += req_chars

    # 4. Read assessment.md (prior findings) -- F1: single read
    prior_assessment, assess_chars = _extract_prior_assessment(milestone_dir)
    input_chars += assess_chars

    # 5. Read decisions.md (recent entries) -- F1: single read
    recent_decisions, dec_chars = _extract_recent_decisions(milestone_dir)
    input_chars += dec_chars

    # 6. Produce summary with provenance boundaries (F21)
    sections: list[str] = []

    # Header
    sections.append(
        f"# ASSESS Context Summary\n"
        f"Generated: {timestamp}\n"
        f"Phase: {phase_name}\n"
        f"Co-layer tasks: {', '.join(co_layer_tasks)}"
    )

    # Task Criteria & Execution -- content fenced with provenance labels
    task_section_parts: list[str] = ["## Task Criteria & Execution"]
    criteria_by_name = {tc["name"]: tc for tc in task_criteria}
    for task_name in co_layer_tasks:
        tc = criteria_by_name.get(task_name, {
            "name": task_name,
            "criteria": "No criteria found in compose.py.",
            "intents": "none",
        })
        exec_summary = execution_summaries.get(
            task_name, f"No execution data for {task_name}."
        )
        # Sanitize all artifact content: strip headings + escape HTML
        # comments to prevent provenance boundary forgery (F4, F5, F21).
        fenced_exec = _sanitize_artifact(exec_summary)
        fenced_criteria = _sanitize_artifact(tc["criteria"])
        task_section_parts.append(
            f"### {task_name}\n"
            f"**Criteria:** {fenced_criteria}\n"
            f"**Intents:** {tc['intents']}\n"
            f"**Execution summary:**\n"
            f"<!-- source: phases/{task_name}/execution*.md -->\n"
            f"{fenced_exec}\n"
            f"<!-- /source -->"
        )
    sections.append("\n\n".join(task_section_parts))

    # Requirements -- provenance label, sanitized (F5: heading strip + F4: HTML escape)
    fenced_requirements = _sanitize_artifact(requirements_text)
    sections.append(
        f"## Requirements\n"
        f"<!-- source: requirements.md -->\n"
        f"{fenced_requirements}\n"
        f"<!-- /source -->"
    )

    # Prior Assessment -- provenance label, fully sanitized (F4, F21)
    fenced_assessment = _sanitize_artifact(prior_assessment)
    sections.append(
        f"## Prior Assessment\n"
        f"<!-- source: assessment.md -->\n"
        f"{fenced_assessment}\n"
        f"<!-- /source -->"
    )

    # Recent Decisions -- provenance label, fully sanitized (F4, F21)
    fenced_decisions = _sanitize_artifact(recent_decisions)
    sections.append(
        f"## Recent Decisions\n"
        f"<!-- source: decisions.md -->\n"
        f"{fenced_decisions}\n"
        f"<!-- /source -->"
    )

    summary_text = "\n\n".join(sections) + "\n"

    # Log reduction ratio
    output_chars = len(summary_text)
    input_tokens = _char_count_tokens(input_chars)
    output_tokens = _char_count_tokens(output_chars)
    if input_tokens > 0:
        ratio = round(output_tokens / input_tokens, 2)
        _log.info(
            "precompose: %d input tokens -> %d output tokens (ratio %.2f)",
            input_tokens,
            output_tokens,
            ratio,
        )
    else:
        _log.info("precompose: no input files found, summary is %d tokens", output_tokens)

    # Write summary (F20: symlink + boundary check)
    summary_path = milestone_dir / "active" / "assess_summary.md"
    _safe_write(summary_path, summary_text, milestone_dir)

    return summary_path
