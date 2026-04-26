"""Custom MCP tool definitions for the Clou orchestrator.

Public API:
    clou_spawn_coordinator(project_dir, milestone) -> str
    clou_create_milestone(project_dir, milestone, milestone_content, requirements_content) -> str
    clou_status(project_dir) -> str
    clou_init(project_dir, project_name, description) -> str
"""

from __future__ import annotations

import logging
from pathlib import Path

from clou.prompts import _BUNDLED_PROMPTS

log = logging.getLogger("clou")

# F7 / F13 / F21 (cycle 2): import the single-source enums from the
# schema module.  Prior to cycle 2 the repository carried three
# independent definitions (``clou.escalation.VALID_DISPOSITION_STATUSES``,
# ``clou.supervisor_tools.RESOLUTION_STATUSES``, and a local
# ``_OPEN_ESCALATION_STATUSES`` frozenset here) that disagreed about
# which tokens are valid.  ``clou.escalation`` now owns the single
# source of truth; this module imports from it.
from clou.escalation import (
    OPEN_DISPOSITION_STATUSES,
    VALID_DISPOSITION_STATUSES,
)

# F13 (cycle 2): cap the per-status listing to avoid O(N*M) parse work
# on projects with large escalation archives.  500 is well above the
# operational threshold (the median milestone ships with ~5
# escalations) while still bounding the worst-case work.  Excess
# entries are reported as a single "...and N more" line per milestone
# section rather than being silently dropped — drift must stay
# visible.
MAX_ESCALATIONS_PER_STATUS: int = 500

# F13 (cycle 2): truncate the bytes fed to the parser so a single
# pathological file (e.g. an accidentally committed log) cannot stall
# startup.  64 KiB is two orders of magnitude above the largest real
# escalation in-tree (the longest file at the time of writing is
# ~4 KiB).  Truncated files still parse through the tolerant parser;
# the tail is simply ignored.
_ESCALATION_MAX_BYTES: int = 64 * 1024

_STATUS_MAX_CHARS = 8_000  # cap total output to avoid bloating context


def _write_if_missing(path: Path, content: str) -> bool:
    """Write *content* to *path* only if the file does not already exist.

    Returns True if written, False if skipped.
    """
    if path.exists():
        return False
    path.write_text(content)
    return True


async def clou_spawn_coordinator(project_dir: Path, milestone: str) -> str:
    """Spawn a coordinator session for a milestone.

    Validates the milestone directory exists, then returns a status message.
    The orchestrator intercepts the return value to actually run the coordinator.
    """
    milestone_md = project_dir / ".clou" / "milestones" / milestone / "milestone.md"
    if not milestone_md.exists():
        msg = f"Milestone file not found: {milestone_md}"
        raise ValueError(msg)
    status = project_dir / ".clou" / "milestones" / milestone / "status.md"
    return f"Coordinator for '{milestone}' requested. Read {status} for results."


async def clou_create_milestone(
    project_dir: Path,
    milestone: str,
    milestone_content: str,
    requirements_content: str,
    intents_content: str = "",
) -> str:
    """Create a new milestone directory with milestone.md, intents.md, and requirements.md.

    The supervisor calls this after converging with the user — the milestone
    name and content come from the convergence dialogue.

    ``intents_content`` holds observable outcomes (DB-14).  When empty the
    file is still created as a placeholder so downstream read sets don't
    encounter a missing file.

    Raises ValueError if the milestone directory already exists.
    """
    ms_dir = project_dir / ".clou" / "milestones" / milestone
    if ms_dir.exists():
        msg = f"Milestone '{milestone}' already exists"
        raise ValueError(msg)
    ms_dir.mkdir(parents=True)
    (ms_dir / "milestone.md").write_text(milestone_content)
    (ms_dir / "intents.md").write_text(intents_content)
    (ms_dir / "requirements.md").write_text(requirements_content)
    return (
        f"Created milestone '{milestone}' with "
        f"{ms_dir / 'milestone.md'}, {ms_dir / 'intents.md'}, "
        f"and {ms_dir / 'requirements.md'}"
    )


def _read_escalation_bounded(esc_file: Path) -> str:
    """Read *esc_file* truncated to :data:`_ESCALATION_MAX_BYTES`.

    F13 (cycle 2): returns the first :data:`_ESCALATION_MAX_BYTES` bytes
    decoded as UTF-8 (invalid sequences are replaced, not raised on).
    Callers that need the full content go directly through
    :class:`pathlib.Path` — this helper exists solely to bound the work
    done for operational status listings.
    """
    with esc_file.open("rb") as fh:
        data = fh.read(_ESCALATION_MAX_BYTES)
    return data.decode("utf-8", errors="replace")


async def clou_status(project_dir: Path) -> str:
    """Read current Clou status: active milestones, open escalations.

    F30 (cycle 1) / F13 / F21 (cycle 2):
    - Each escalation file is read via :func:`_read_escalation_bounded`
      so an oversized file cannot stall the status call (the tail is
      truncated at :data:`_ESCALATION_MAX_BYTES`).
    - :func:`clou.escalation.parse_escalation` never raises, but
      :class:`OSError` / :class:`UnicodeDecodeError` from the READ stage
      are narrow-caught and reported with a ``(read-error)`` marker.
      Every other exception propagates — parser regressions must not
      hide behind a broad ``except``.
    - Files whose parsed ``disposition_status`` is in
      :data:`clou.escalation.OPEN_DISPOSITION_STATUSES` surface under
      ``## Open Escalations``.  Files whose raw token is outside
      :data:`clou.escalation.VALID_DISPOSITION_STATUSES` surface with
      an ``(unknown: <raw>)`` marker — drift must be visible, NEVER
      suppressed (F21).
    - The per-milestone listing caps at
      :data:`MAX_ESCALATIONS_PER_STATUS`; excess entries collapse to a
      single "...and N more" line so large archives remain bounded.
    """
    # Late import to keep ``clou.tools`` lightweight — the escalation
    # module brings regex compilation and dataclasses that the init /
    # create-milestone tools don't need.
    from clou.escalation import parse_escalation

    clou_dir = project_dir / ".clou"
    if not clou_dir.is_dir():
        return "No .clou/ directory found. Run clou_init first."

    sections: list[str] = []

    # Roadmap
    roadmap = clou_dir / "roadmap.md"
    if roadmap.exists():
        sections.append(roadmap.read_text())

    # Open escalations — parse disposition and filter to actionable set.
    milestones_dir = clou_dir / "milestones"
    if milestones_dir.is_dir():
        escalations: list[str] = []
        for ms_dir in sorted(milestones_dir.iterdir()):
            esc_dir = ms_dir / "escalations"
            if not esc_dir.is_dir():
                continue
            lines_for_ms = _collect_milestone_escalations(
                ms_dir, esc_dir, parse_escalation,
            )
            escalations.extend(lines_for_ms)
        if escalations:
            sections.append("## Open Escalations\n" + "\n".join(escalations))

    if not sections:
        return "No status information available."

    result = "\n\n".join(sections)
    if len(result) > _STATUS_MAX_CHARS:
        return result[:_STATUS_MAX_CHARS] + "\n\n… (truncated)"
    return result


def _collect_milestone_escalations(
    ms_dir: Path,
    esc_dir: Path,
    parse_escalation,
    *,
    max_entries: int | None = None,
) -> list[str]:
    """Collect listing entries for one milestone's escalations directory.

    Separated from :func:`clou_status` so the per-file read/parse loop
    can be tested in isolation.  Returns the markdown-list lines
    ready to be joined under ``## Open Escalations``.  When the count
    exceeds *max_entries* the listing truncates with an ``"...and N
    more"`` suffix so the supervisor still sees that additional files
    exist on disk.

    ``max_entries`` defaults to :data:`MAX_ESCALATIONS_PER_STATUS` at
    call time (not at definition time) so tests can patch the module
    constant without having to thread the value through ``clou_status``.
    """
    if max_entries is None:
        max_entries = MAX_ESCALATIONS_PER_STATUS
    entries: list[str] = []
    truncated_extra = 0
    # ``sorted(iterdir())`` is O(N log N) on the listing; we still want
    # deterministic ordering for operational stability.  The more
    # expensive work (read+parse) is capped below.
    for esc_file in sorted(esc_dir.iterdir()):
        if not esc_file.is_file():
            continue
        if len(entries) >= max_entries:
            truncated_extra += 1
            continue
        # F13: narrow exception on READ only.  Parser regressions must
        # propagate — a broad ``except`` at DEBUG level is how a
        # systemic parser regression lands silently in CI (the original
        # cycle-1 F13 finding).  If the file exists but read fails
        # (EIO, permission, non-UTF8), emit a distinct marker and move
        # on; we are a status surface, not a recovery path.
        try:
            content = _read_escalation_bounded(esc_file)
        except (OSError, UnicodeDecodeError):
            log.warning(
                "clou_status could not read %s", esc_file, exc_info=True,
            )
            entries.append(f"- {ms_dir.name}/{esc_file.name} (read-error)")
            continue
        form = parse_escalation(content)
        # ``parse_escalation`` never raises (contract in
        # ``clou/escalation.py``).  It CAN return a minimal form when
        # the input is unparseable prose; in that case we still surface
        # the file (default disposition_status is ``"open"``) but log a
        # warning so operators can chase down the drift.  F13: elevate
        # from ``log.debug`` — empty-form parse results are a
        # regression signal, not a benign edge case.
        if not form.title and not form.classification and not form.issue:
            log.warning(
                "clou_status parsed empty form for %s (%d bytes) — "
                "file exists but yields no structured fields; likely "
                "drift",
                esc_file,
                len(content),
            )
        status = (form.disposition_status or "open").strip().lower()
        raw = (
            (getattr(form, "disposition_status_raw", "") or status)
            .strip()
            .lower()
        )
        # F21 partial: if the raw token is outside the canonical enum,
        # surface "unknown: <raw>" rather than silently hide the file.
        # The canonical ``disposition_status`` has been coerced to
        # ``"open"`` by the parser, so the drift signal would otherwise
        # be lost.
        if raw and raw not in VALID_DISPOSITION_STATUSES:
            entries.append(
                f"- {ms_dir.name}/{esc_file.name} (unknown: {raw})"
            )
            continue
        # Resolved / overridden: historical decision records, not
        # operational state.  Skip under "Open Escalations".
        if status not in OPEN_DISPOSITION_STATUSES:
            continue
        entries.append(f"- {ms_dir.name}/{esc_file.name}")
    if truncated_extra:
        entries.append(
            f"- ...and {truncated_extra} more in {ms_dir.name}/escalations"
        )
    return entries


async def clou_init(
    project_dir: Path,
    project_name: str,
    description: str = "",
) -> str:
    """Initialize the .clou/ directory structure for a new project.

    Idempotent — safe to run on an existing .clou/ directory.  Missing
    directories and files are created; existing files are never overwritten.
    Bundled prompt files are copied to .clou/prompts/ so agents can read
    their protocols at project-relative paths.

    The supervisor calls this AFTER converging with the user — project_name
    and description come from the convergence dialogue, not from upfront
    parameters.  Description is optional for the initial scaffold; the
    supervisor writes the full project.md content separately.
    """
    clou_dir = project_dir / ".clou"

    (clou_dir / "milestones").mkdir(parents=True, exist_ok=True)
    (clou_dir / "active").mkdir(exist_ok=True)
    (clou_dir / "prompts").mkdir(exist_ok=True)

    # Copy bundled prompt files so agents can read protocols at
    # .clou/prompts/<file>.  Uses _write_if_missing so per-project
    # customizations survive re-init.
    for src in sorted(_BUNDLED_PROMPTS.iterdir()):
        if src.is_file() and src.name not in ("__init__.py",):
            _write_if_missing(clou_dir / "prompts" / src.name, src.read_text())

    # Write structural files if missing.  The supervisor will overwrite
    # project.md with full content after convergence — this just ensures
    # the file exists for the template: field.
    project_content = f"# {project_name}\n"
    if description:
        project_content += f"\n{description}\n"
    _write_if_missing(clou_dir / "project.md", project_content)
    _write_if_missing(
        clou_dir / "roadmap.md",
        "# Roadmap\n\n## Milestones\n",
    )
    _write_if_missing(
        clou_dir / "requests.md",
        "# Requests\n",
    )

    return f"Initialized .clou/ for '{project_name}'"
