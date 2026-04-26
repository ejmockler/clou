"""MCP tools for the supervisor session --- escalation resolution.

The supervisor tier is the disposition owner for escalation files: a
coordinator files an escalation via
``mcp__clou_coordinator__clou_file_escalation``, and the supervisor
closes it by writing ONLY the ``## Disposition`` section.  Direct
Write to ``escalations/*.md`` is hook-denied for every tier (DB-21
remolding, milestone 41), so the supervisor resolution path must go
through an MCP tool.

This module mirrors ``clou.coordinator_tools`` --- one tool builder
per tier --- to keep the wiring surface small.  The functions here
are exposed on an in-process ``clou_supervisor`` MCP server that the
supervisor session can mount alongside the existing ``clou`` server.

Public API:
    build_supervisor_mcp_server(project_dir) -> MCP server

Wiring note:
    The server is returned already configured; mounting it is the
    orchestrator's job (``clou/orchestrator.py``).  The orchestrator
    owns ``mcp_servers`` and ``allowed_tools`` for the supervisor
    session --- see the existing ``clou`` server for the pattern.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from clou.escalation import (
    ENGINE_GATED_CLASSIFICATIONS,
    HALT_OPTION_LABELS,
    OPEN_DISPOSITION_STATUSES,
    VALID_DISPOSITION_STATUSES,
    _escape_field,
    find_last_disposition_span,
    parse_escalation,
)
from clou.golden_context import render_checkpoint
from clou.proposal import (
    VALID_STATUSES as VALID_PROPOSAL_STATUSES,
    parse_proposal,
    proposals_dir,
    render_proposal,
)
from clou.recovery import validate_milestone_name
from clou.recovery_checkpoint import _VALID_NEXT_STEPS, parse_checkpoint

_log = logging.getLogger(__name__)


# F7 (cycle 2): single-source enum.  Historical name retained for
# backwards-compat callers (tests import ``RESOLUTION_STATUSES``); the
# value is aliased to :data:`clou.escalation.VALID_DISPOSITION_STATUSES`
# so the supervisor tool and the schema module can never disagree.
RESOLUTION_STATUSES: frozenset[str] = frozenset(VALID_DISPOSITION_STATUSES)


def _find_disposition_span(text: str) -> tuple[int, int] | None:
    """Locate the LAST ``## Disposition`` section in *text*.

    F2 (cycle 2, security): routed through
    :func:`clou.escalation.find_last_disposition_span` so the writer
    (supervisor resolution) and the reader (``_parse_disposition`` and
    ``parse_latest_disposition``) agree on which Disposition block wins.
    Prior cycles had the writer taking the FIRST match while the reader
    took the LAST --- a splice could then destroy Options / Recommendation
    while leaving the terminal Disposition untouched, producing silent
    data loss.

    Returns ``(heading_start, section_end)`` or None if no
    ``## Disposition`` heading is present.
    """
    return find_last_disposition_span(text)


# F1 (cycle 2, security): reject notes that contain literal preamble
# lookalikes.  A supervisor-authored ``notes`` string containing
# ``\n**Status:** resolved`` could land INSIDE the Disposition section
# and be indistinguishable from a real preamble key when legacy parsers
# inspect it.  We reject both direct ``status:`` lines (which would be
# picked up by :func:`clou.escalation._parse_disposition` before the
# real line) and ``**Status:**`` bold keys for the same reason.


def _render_disposition_block(status: str, notes: str) -> str:
    """Render the canonical ``## Disposition`` block.

    ``status:`` always appears on the first line after the heading
    (matching :func:`clou.escalation.render_escalation`).  Notes are
    escaped via :func:`clou.escalation._escape_field` so any heading
    markers (``## Disposition``, ``# Escalation``) embedded in
    supervisor-authored notes are defanged --- a drifted supervisor
    cannot forge a second Disposition block inside the first.

    F1 (cycle 2): the escape also normalises ``\r\n`` and ``\r`` to
    ``\n`` before the heading regex fires, so a bare CR inside notes
    cannot resurrect a heading after Python's universal-newline read
    translates the CR.
    """
    lines = ["## Disposition", f"status: {status}"]
    trimmed_notes = notes.rstrip()
    if trimmed_notes:
        # F1 (cycle 2, security): escape heading markers and CR-normalize
        # before the block lands in the file.
        lines.append(_escape_field(trimmed_notes))
    return "\n".join(lines)


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* via tmp-file rename (atomic on POSIX).

    F6 (cycle 2, security): uses
    :func:`tempfile.NamedTemporaryFile` to generate a unique temp file
    per call (``mkstemp`` under the hood).  This closes two prior
    vulnerabilities:

    1. The old path used ``target.name + ".tmp"`` as a guessable name
       and, if an unrelated file with that exact name existed, deleted
       it unconditionally before the write.  A developer workspace or
       concurrent tool run with a ``.tmp`` file of the same name lost
       that file.  ``NamedTemporaryFile(prefix=...)`` generates a
       per-call unique suffix (``.{name}.XXXXXXXX.tmp``) so collisions
       don't happen.
    2. Two concurrent invocations targeting the same file used to race
       on the single ``.tmp`` path --- one's in-flight write would get
       unlinked by the other.  Unique temp names eliminate the race.

    The tmp file is placed in the same directory as the target so
    ``os.replace`` stays on one filesystem (cross-device rename falls
    back to copy+unlink, which is NOT atomic).  ``delete=False`` keeps
    the file on disk so we can ``os.replace`` it into place.
    ``fsync`` is best-effort --- we try it so the rename is durable
    across power loss, but swallow ``OSError`` for filesystems that
    don't support it.
    """
    parent = target.parent
    # Unique per-call temp name inside the target's directory so
    # ``os.replace`` stays on one filesystem.  ``prefix=f".{name}."``
    # keeps the temp file hidden in ``ls`` output.  The suffix includes
    # ``.tmp`` as a debugging aid if we crash before the rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, mode="w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # Some filesystems (tmpfs, NFS-lite) don't implement
                # fsync --- that's acceptable for the unit-test surface.
                pass
        os.replace(str(tmp_path), str(target))
    except Exception:
        # Clean up OUR temp file only --- never touch pre-existing files.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _build_supervisor_tools(project_dir: Path) -> list[Any]:
    """Build the supervisor's ``SdkMcpTool`` list (testable seam).

    Tools are returned as a list so unit tests can invoke handlers
    directly via ``tool.handler(args)`` without routing through the
    MCP transport.  Follows the same pattern as
    :func:`clou.coordinator_tools._build_coordinator_tools`.
    """

    @tool(
        "clou_resolve_escalation",
        "Resolve (or otherwise disposition) an existing escalation "
        "file.  Parses the file, replaces ONLY the ``## Disposition`` "
        "section with the provided status and notes, and writes the "
        "result atomically.  Every byte above ``## Disposition`` is "
        "preserved verbatim --- this is the supervisor's amendment "
        "path for legacy escalation files that must not be "
        "canonicalised (R7).  Use this instead of direct Write; "
        "Write to escalations/*.md is denied by the PreToolUse hook.",
        {
            "type": "object",
            "properties": {
                "milestone": {
                    "type": "string",
                    "description": (
                        "Milestone slug (e.g. '41-escalation-remolding'). "
                        "The escalation file lives under "
                        ".clou/milestones/{milestone}/escalations/."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Escalation file name (e.g. "
                        "'20260420-120000-cycle-limit.md').  Must not "
                        "contain path separators --- the tool resolves "
                        "the path from milestone + filename and rejects "
                        "traversal."
                    ),
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Disposition status.  One of: open | "
                        "investigating | deferred | resolved | "
                        "overridden.  'resolved' and 'overridden' are "
                        "the terminal states consumed by the DB-15 D5 "
                        "cycle-count-reset logic."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": (
                        "Free-form disposition notes (optional).  "
                        "Preserved verbatim under the status line.  "
                        "Leave empty to clear prior notes."
                    ),
                },
            },
            "required": ["milestone", "filename", "status"],
        },
    )
    async def resolve_escalation_tool(args: dict[str, Any]) -> dict[str, Any]:
        # ----- milestone ------------------------------------------------
        milestone = args.get("milestone", "")
        if not isinstance(milestone, str) or not milestone.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "milestone must be a non-empty string",
                }],
                "is_error": True,
            }
        try:
            validate_milestone_name(milestone.strip())
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid milestone: {exc}",
                }],
                "is_error": True,
            }
        milestone = milestone.strip()

        # ----- filename ------------------------------------------------
        filename = args.get("filename", "")
        if not isinstance(filename, str) or not filename.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "filename must be a non-empty string",
                }],
                "is_error": True,
            }
        filename = filename.strip()
        # F12 (cycle 2): reject control characters BEFORE ``Path()`` touches
        # the value.  ``\x00`` raises ``ValueError`` from ``Path.resolve``;
        # ``\n``/``\r``/``\t`` are log-injection vectors and produce
        # filenames that most shells and editors mis-render.  Surface
        # every rejection as a structured ``is_error`` payload so the
        # MCP caller can self-correct.
        if "\x00" in filename:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "filename must not contain null bytes "
                        "(embedded \\x00)"
                    ),
                }],
                "is_error": True,
            }
        if any(ch in filename for ch in ("\n", "\r", "\t")):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "filename must not contain embedded "
                        "newlines, carriage returns, or tabs"
                    ),
                }],
                "is_error": True,
            }
        if "/" in filename or "\\" in filename or filename.startswith("."):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "filename must not contain path separators "
                        "or start with '.'; supply the bare filename "
                        "(e.g. '20260420-120000-cycle-limit.md')"
                    ),
                }],
                "is_error": True,
            }
        if not filename.endswith(".md"):
            return {
                "content": [{
                    "type": "text",
                    "text": "filename must end with .md",
                }],
                "is_error": True,
            }

        # ----- status --------------------------------------------------
        status = args.get("status", "")
        if not isinstance(status, str) or not status.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "status must be a non-empty string",
                }],
                "is_error": True,
            }
        status = status.strip().lower()
        # F7 (cycle 2): validate against the canonical enum in the schema
        # module (imported above as ``VALID_DISPOSITION_STATUSES``).
        # Prior cycles maintained a LOCAL ``RESOLUTION_STATUSES`` set that
        # could drift from the schema definition; both names now resolve
        # to the same underlying tuple.
        if status not in VALID_DISPOSITION_STATUSES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"status must be one of "
                        f"{list(VALID_DISPOSITION_STATUSES)}, "
                        f"got {status!r}"
                    ),
                }],
                "is_error": True,
            }

        # ----- notes ---------------------------------------------------
        notes_raw = args.get("notes", "")
        if notes_raw is None:
            notes_raw = ""
        if not isinstance(notes_raw, str):
            return {
                "content": [{
                    "type": "text",
                    "text": "notes must be a string",
                }],
                "is_error": True,
            }
        notes = notes_raw.strip()

        # ----- Resolve target path + containment check ----------------
        # F12 (cycle 2): wrap resolve() in try/except.  The explicit
        # null-byte/control-char rejection above catches the common case
        # but ``Path.resolve`` can also raise ``ValueError`` for
        # pathologically malformed inputs, and ``OSError`` on filesystem
        # errors (e.g. permission denied on an intermediate dir).  Every
        # failure becomes a structured ``is_error`` payload so the MCP
        # transport never sees an unhandled exception.
        try:
            esc_dir = (
                project_dir
                / ".clou"
                / "milestones"
                / milestone
                / "escalations"
            ).resolve()
            target = (esc_dir / filename).resolve()
            target.relative_to(esc_dir)
        except ValueError:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"filename resolves outside "
                        f"escalations/: {filename!r}"
                    ),
                }],
                "is_error": True,
            }
        except OSError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"filesystem error resolving "
                        f"escalation path: {exc}"
                    ),
                }],
                "is_error": True,
            }
        if not target.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"escalation not found: "
                        f".clou/milestones/{milestone}/"
                        f"escalations/{filename}"
                    ),
                }],
                "is_error": True,
            }
        if not target.is_file():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"target is not a regular file: "
                        f"{filename!r}"
                    ),
                }],
                "is_error": True,
            }

        # M49b D2 (closes B7/F2): refuse engine-gated classifications.
        # ``clou_resolve_escalation`` only writes the disposition file —
        # it does NOT rewrite the milestone checkpoint.  For
        # engine-gated classifications (currently ``trajectory_halt``)
        # the checkpoint also needs to be transitioned out of HALTED;
        # the supervisor MUST use ``clou_dispose_halt`` for that.
        # Without this guard, a supervisor that follows the prompt's
        # default disposition path leaves the milestone wedged: the
        # engine halt gate sees the resolved escalation and falls
        # through, but the checkpoint still says HALTED, and
        # determine_next_cycle (M49b C1) raises on next session.
        # We parse defensively — a malformed escalation falls through
        # to the splice path's error handling rather than failing here.
        try:
            _form_for_check = parse_escalation(
                target.read_text(encoding="utf-8"),
            )
        except Exception:  # noqa: BLE001 — drift-tolerant peek
            _form_for_check = None
        if (
            _form_for_check is not None
            and _form_for_check.classification
            in ENGINE_GATED_CLASSIFICATIONS
        ):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"escalation classification "
                        f"{_form_for_check.classification!r} is "
                        f"engine-gated.  Use ``clou_dispose_halt`` "
                        f"instead — it resolves the escalation AND "
                        f"rewrites the milestone checkpoint atomically. "
                        f"``clou_resolve_escalation`` alone leaves the "
                        f"milestone wedged in HALTED state."
                    ),
                }],
                "is_error": True,
            }

        # ----- Read + splice + atomic write ----------------------------
        original = target.read_text(encoding="utf-8")
        span = _find_disposition_span(original)
        new_block = _render_disposition_block(status, notes)

        if span is None:
            # No ``## Disposition`` section yet --- append one.  The
            # legacy body above is preserved byte-for-byte; we insert a
            # blank-line separator only if the original doesn't already
            # end with a newline.
            prefix = original
            if not prefix.endswith("\n"):
                prefix = prefix + "\n"
            if not prefix.endswith("\n\n"):
                prefix = prefix + "\n"
            updated = prefix + new_block + "\n"
        else:
            start, end = span
            # Byte-for-byte preservation of the legacy body above the
            # Disposition heading (R7 no-rewrite invariant).  ``start``
            # is the index of the first ``#`` of ``## Disposition``, so
            # ``original[:start]`` is the entire prefix including the
            # trailing newline the heading sits on.
            prefix = original[:start]
            suffix = original[end:]
            # Ensure the new block has a trailing newline so the file
            # shape stays consistent.
            updated = prefix + new_block
            if suffix:
                # Maintain separation between the new block and any
                # trailing content (unusual but possible).
                if not new_block.endswith("\n"):
                    updated = updated + "\n"
                updated = updated + suffix
            else:
                updated = updated + "\n"

        _atomic_write(target, updated)
        return {
            "written": str(target),
            "status": status,
            "milestone": milestone,
            "filename": filename,
        }

    @tool(
        "clou_list_proposals",
        "List milestone proposals filed by coordinators.  Proposals "
        "are cross-cutting work that a coordinator identified as "
        "out-of-scope for its milestone but worth sequencing onto the "
        "roadmap.  The supervisor reads this list and decides whether "
        "to crystallize (via clou_create_milestone), reject, or defer. "
        "Under the zero-escalations principle, coordinators propose "
        "follow-up milestones instead of escalating architectural "
        "decisions upward.  Returns structured proposal data; the "
        "supervisor uses this to orient the roadmap.",
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Optional filter: 'open' (default) returns "
                        "proposals awaiting decision.  Use 'accepted', "
                        "'rejected', 'superseded', or 'all' to see other "
                        "states."
                    ),
                },
            },
            "required": [],
        },
    )
    async def list_proposals_tool(args: dict[str, Any]) -> dict[str, Any]:
        status_filter = args.get("status", "open")
        if not isinstance(status_filter, str) or not status_filter.strip():
            status_filter = "open"
        status_filter = status_filter.strip().lower()

        _dir = proposals_dir(project_dir / ".clou")
        if not _dir.is_dir():
            return {"proposals": []}

        proposals: list[dict[str, Any]] = []
        for path in sorted(_dir.glob("*.md")):
            try:
                form = parse_proposal(path.read_text(encoding="utf-8"))
            except Exception:
                # Drift-tolerant: a malformed proposal file shouldn't
                # block the supervisor from seeing the rest.  Surface
                # the filename so the supervisor can investigate.
                proposals.append({
                    "filename": path.name,
                    "parse_error": True,
                })
                continue
            if status_filter != "all" and form.status != status_filter:
                continue
            proposals.append({
                "filename": path.name,
                "title": form.title,
                "filed_by_milestone": form.filed_by_milestone,
                "filed_by_cycle": form.filed_by_cycle,
                "estimated_scope": form.estimated_scope,
                "depends_on": list(form.depends_on),
                "independent_of": list(form.independent_of),
                "rationale": form.rationale,
                "cross_cutting_evidence": form.cross_cutting_evidence,
                "recommendation": form.recommendation,
                "status": form.status,
                "disposition": form.disposition,
            })

        return {"proposals": proposals, "count": len(proposals)}

    @tool(
        "clou_dispose_proposal",
        "Disposition a filed milestone proposal.  Parses the file via "
        "parse_proposal, updates ONLY the status + disposition fields, "
        "and rewrites via render_proposal -- no other bytes change. "
        "Statuses: 'accepted' (supervisor crystallized via "
        "clou_create_milestone), 'rejected' (decided not to act), "
        "'superseded' (another proposal/milestone covered the ground). "
        "Use this instead of direct Write/Edit -- Write to proposals/*.md "
        "is hook-denied to preserve the MilestoneProposalForm schema.",
        {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Proposal file name within .clou/proposals/ "
                        "(e.g. '20260421-120000-close-perception.md'). "
                        "No path separators -- the tool resolves from "
                        "the proposals directory and rejects traversal."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": list(VALID_PROPOSAL_STATUSES),
                    "description": (
                        "New disposition status.  'open' is the initial "
                        "filed state; use 'accepted' / 'rejected' / "
                        "'superseded' to transition."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": (
                        "Optional disposition notes preserved under "
                        "'## Disposition' (e.g., 'crystallized as M48' "
                        "or 'covered by M40 scope')."
                    ),
                },
            },
            "required": ["filename", "status"],
        },
    )
    async def dispose_proposal_tool(args: dict[str, Any]) -> dict[str, Any]:
        filename = args.get("filename", "")
        if not isinstance(filename, str) or not filename.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "filename must be a non-empty string",
                }],
                "is_error": True,
            }
        # Reject path traversal: the filename must be a bare basename.
        if (
            "/" in filename
            or "\\" in filename
            or filename in (".", "..")
            or filename.startswith(".")
        ):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "filename must be a bare proposal basename "
                        "(no path separators, no leading dot)"
                    ),
                }],
                "is_error": True,
            }
        status = args.get("status", "")
        if status not in VALID_PROPOSAL_STATUSES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"status must be one of "
                        f"{list(VALID_PROPOSAL_STATUSES)!r}, got "
                        f"{status!r}"
                    ),
                }],
                "is_error": True,
            }
        notes = args.get("notes", "")
        if not isinstance(notes, str):
            notes = ""

        _dir = proposals_dir(project_dir / ".clou")
        target = _dir / filename
        if not target.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"proposal file not found: {target}. "
                        "Use clou_list_proposals to see available files."
                    ),
                }],
                "is_error": True,
            }

        try:
            form = parse_proposal(target.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — parser is drift-tolerant
            return {
                "content": [{
                    "type": "text",
                    "text": f"proposal parse failed: {exc}",
                }],
                "is_error": True,
            }

        from dataclasses import replace

        updated = replace(
            form,
            status=status,  # type: ignore[arg-type]
            disposition=notes.strip(),
        )
        try:
            content = render_proposal(updated)
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"proposal render failed: {exc}",
                }],
                "is_error": True,
            }
        _atomic_write(target, content)
        return {
            "written": str(target),
            "status": status,
            "filename": filename,
            "title": form.title,
        }

    # M49b B6: choice → default next_step mapping for clou_dispose_halt.
    # ``continue-as-is`` consults the checkpoint's ``pre_halt_next_step``
    # stash first; falls back to ORIENT for re-observation if no stash.
    # ``re-scope`` routes to PLAN (re-plan with new scope).  ``abandon``
    # routes to EXIT (milestone exit).  Supervisor may override via the
    # ``next_step`` arg, but HALTED is rejected (would loop).
    _HALT_DEFAULT_NEXT_STEP: dict[str, str] = {
        "re-scope": "PLAN",
        "abandon": "EXIT",
    }
    # M49b D4 (closes B7/F5): valid choices derive from the single
    # source of truth (clou.escalation.HALT_OPTION_LABELS), shared
    # with the coordinator-side handler that builds the escalation.
    _HALT_VALID_CHOICES = frozenset(HALT_OPTION_LABELS)

    @tool(
        "clou_dispose_halt",
        "Disposition an open engine-gated trajectory-halt escalation "
        "AND rewrite the milestone checkpoint to clear the halt.  This "
        "is the supervisor's path out of the M49b halt-pending-review "
        "state.  Resolving the escalation alone (via "
        "clou_resolve_escalation) is NOT sufficient: the engine's "
        "next-cycle dispatch reads ``next_step`` from the checkpoint, "
        "and the halt gate left it as ``HALTED`` — without rewriting "
        "the checkpoint, the next session would crash on "
        "determine_next_cycle's HALTED case (M49b C1).  This tool "
        "performs both writes atomically from the supervisor's "
        "perspective: escalation file first (via the same disposition-"
        "splice path as clou_resolve_escalation), then checkpoint.\n\n"
        "Choices:\n"
        "  continue-as-is — restore the next_step that was active "
        "before the halt fired (read from the checkpoint's "
        "pre_halt_next_step stash; falls back to ORIENT for "
        "re-observation if the stash is empty).\n"
        "  re-scope — clear the halt and route to PLAN so the "
        "coordinator re-plans with the supervisor's new scope (capture "
        "the new scope in the disposition notes).\n"
        "  abandon — clear the halt and route to EXIT so the milestone "
        "exits cleanly with outcome ``escalated_trajectory``.\n\n"
        "The supervisor may override the default next_step via the "
        "optional ``next_step`` argument (any value in the engine's "
        "valid next-step vocabulary except HALTED, which would loop).",
        {
            "type": "object",
            "properties": {
                "milestone": {
                    "type": "string",
                    "description": (
                        "Milestone slug.  The escalation file lives at "
                        ".clou/milestones/{milestone}/escalations/{filename}."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Escalation file name (e.g. "
                        "'20260423-070000-trajectory-halt.md').  Must "
                        "not contain path separators."
                    ),
                },
                "choice": {
                    "type": "string",
                    "description": (
                        "Supervisor's disposition choice.  One of: "
                        "continue-as-is | re-scope | abandon.  Maps to "
                        "the trajectory-halt escalation's ``## Options``."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": (
                        "Resolution explanation.  For ``re-scope``, "
                        "include the new scope direction.  For "
                        "``abandon``, include the rationale.  Free-form."
                    ),
                },
                "next_step": {
                    "type": "string",
                    "description": (
                        "Optional override for the new checkpoint "
                        "next_step.  Any value in the engine's vocabulary "
                        "(PLAN, EXECUTE, ASSESS, REPLAN, VERIFY, EXIT, "
                        "COMPLETE, ORIENT) except HALTED.  Defaults: "
                        "continue-as-is → pre_halt_next_step stash or "
                        "ORIENT; re-scope → PLAN; abandon → EXIT."
                    ),
                },
            },
            "required": ["milestone", "filename", "choice", "notes"],
        },
    )
    async def dispose_halt_tool(args: dict[str, Any]) -> dict[str, Any]:
        # ----- milestone --------------------------------------------------
        milestone = args.get("milestone", "")
        if not isinstance(milestone, str) or not milestone.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "milestone must be a non-empty string",
                }],
                "is_error": True,
            }
        try:
            validate_milestone_name(milestone.strip())
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid milestone: {exc}",
                }],
                "is_error": True,
            }
        milestone = milestone.strip()

        # ----- filename ---------------------------------------------------
        filename = args.get("filename", "")
        if (
            not isinstance(filename, str)
            or not filename.strip()
            or "/" in filename
            or "\\" in filename
            or filename.strip().startswith(".")
        ):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "filename must be a bare file name "
                        "(no path separators, no leading dot)"
                    ),
                }],
                "is_error": True,
            }
        filename = filename.strip()

        # ----- choice -----------------------------------------------------
        choice = args.get("choice", "")
        if choice not in _HALT_VALID_CHOICES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"choice must be one of "
                        f"{sorted(_HALT_VALID_CHOICES)!r}, got "
                        f"{choice!r}"
                    ),
                }],
                "is_error": True,
            }

        # ----- notes ------------------------------------------------------
        notes = args.get("notes", "")
        if not isinstance(notes, str):
            notes = ""

        # ----- next_step override (optional) ------------------------------
        next_step_override = args.get("next_step", "")
        if next_step_override:
            if (
                not isinstance(next_step_override, str)
                or next_step_override not in _VALID_NEXT_STEPS
                or next_step_override == "HALTED"
            ):
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"next_step override invalid: "
                            f"{next_step_override!r}.  Must be in the "
                            f"engine vocabulary, NOT 'HALTED'."
                        ),
                    }],
                    "is_error": True,
                }

        # ----- Locate + verify the escalation file -----------------------
        ms_dir = project_dir / ".clou" / "milestones" / milestone
        esc_path = ms_dir / "escalations" / filename
        if not esc_path.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": f"escalation not found: {esc_path}",
                }],
                "is_error": True,
            }
        try:
            form = parse_escalation(esc_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{
                    "type": "text",
                    "text": f"escalation parse failed: {exc}",
                }],
                "is_error": True,
            }
        if form.classification not in ENGINE_GATED_CLASSIFICATIONS:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"escalation classification "
                        f"{form.classification!r} is not engine-gated; "
                        f"use clou_resolve_escalation instead.  "
                        f"clou_dispose_halt is for ENGINE_GATED_"
                        f"CLASSIFICATIONS only "
                        f"({sorted(ENGINE_GATED_CLASSIFICATIONS)!r})."
                    ),
                }],
                "is_error": True,
            }
        if form.disposition_status not in OPEN_DISPOSITION_STATUSES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"escalation already in terminal disposition "
                        f"({form.disposition_status!r}); nothing to "
                        f"dispose."
                    ),
                }],
                "is_error": True,
            }

        # ----- Compose escalation disposition splice (do NOT write yet) --
        original = esc_path.read_text(encoding="utf-8")
        span = _find_disposition_span(original)
        composed_notes = f"Choice: {choice}\n\n{notes}".rstrip()
        new_block = _render_disposition_block("resolved", composed_notes)
        if span is None:
            prefix = original
            if not prefix.endswith("\n"):
                prefix = prefix + "\n"
            if not prefix.endswith("\n\n"):
                prefix = prefix + "\n"
            esc_updated = prefix + new_block + "\n"
        else:
            start, end = span
            prefix = original[:start]
            suffix = original[end:]
            esc_updated = prefix + new_block
            if suffix:
                if not new_block.endswith("\n"):
                    esc_updated = esc_updated + "\n"
                esc_updated = esc_updated + suffix
            else:
                esc_updated = esc_updated + "\n"

        # ----- Compose checkpoint rewrite (do NOT write yet) -------------
        cp_path = ms_dir / "active" / "coordinator.md"
        new_next_step: str | None = None
        cp_updated: str | None = None
        if cp_path.exists():
            cp = parse_checkpoint(cp_path.read_text(encoding="utf-8"))
            if next_step_override:
                new_next_step = next_step_override
            elif choice == "continue-as-is":
                # Restore from stash; fall back to ORIENT for safe
                # re-observation if no stash exists.  Note that the
                # session-start ORIENT dispatch in run_coordinator will
                # ALSO interpose ORIENT before the restored value
                # actually drives dispatch — see clou/coordinator.py
                # session-start block.
                new_next_step = cp.pre_halt_next_step or "ORIENT"
            else:
                new_next_step = _HALT_DEFAULT_NEXT_STEP[choice]
            cp_updated = render_checkpoint(
                cycle=cp.cycle,
                step=cp.step,
                next_step=new_next_step,
                current_phase=cp.current_phase,
                phases_completed=cp.phases_completed,
                phases_total=cp.phases_total,
                validation_retries=cp.validation_retries,
                readiness_retries=cp.readiness_retries,
                crash_retries=cp.crash_retries,
                staleness_count=cp.staleness_count,
                # Clear the halt outcome — milestone is runnable
                # again from the supervisor's perspective.
                cycle_outcome="ADVANCED",
                valid_findings=cp.valid_findings,
                consecutive_zero_valid=cp.consecutive_zero_valid,
                # Preserve any pending ORIENT restoration.
                pre_orient_next_step=cp.pre_orient_next_step,
                # Clear the halt-restoration stash now that we've
                # consumed it (or chose to override).
                pre_halt_next_step="",
                # M52 F38: halt-disposition is orthogonal to the gate
                # verdict; inherit (the milestone resumes against
                # whatever verdict the prior cycle established).
                last_acceptance_verdict=cp.last_acceptance_verdict,
            )

        # ----- Commit order: checkpoint FIRST, escalation SECOND ---------
        # M49b D1 (closes B7/F1): the previous order (escalation first,
        # checkpoint second) created an unrecoverable wedge if the
        # process died between the two writes — escalation appeared
        # resolved but checkpoint still said HALTED, so the engine's
        # halt gate did NOT re-fire on next session and the
        # determine_next_cycle HALTED case (M49b C1) raised.  The
        # supervisor's retry rejected ("already terminal disposition")
        # and only a hand-edit could recover.
        #
        # Checkpoint-first ordering is crash-safe: if a crash happens
        # between writes, the next session sees a runnable next_step
        # PLUS an open engine-gated escalation — the halt gate fires,
        # re-stashes pre_halt_next_step, re-writes next_step=HALTED.
        # Idempotent replay.  The supervisor re-invokes
        # clou_dispose_halt and it succeeds normally.  Both writes
        # use _atomic_write (tmp + rename) for durability.
        cp_written = False
        if cp_updated is not None:
            _atomic_write(cp_path, cp_updated)
            cp_written = True
        else:
            _log.warning(
                "clou_dispose_halt: no checkpoint at %s; will resolve "
                "escalation but engine state cannot be transitioned. "
                "The supervisor may need to seed a new checkpoint.",
                cp_path,
            )
        _atomic_write(esc_path, esc_updated)

        return {
            "escalation_written": str(esc_path),
            "checkpoint_written": str(cp_path) if cp_written else "",
            "milestone": milestone,
            "filename": filename,
            "choice": choice,
            "next_step": new_next_step or "",
        }

    return [
        resolve_escalation_tool,
        list_proposals_tool,
        dispose_proposal_tool,
        dispose_halt_tool,
    ]


def build_supervisor_mcp_server(project_dir: Path) -> Any:
    """Build an in-process MCP server with supervisor-tier tools.

    Currently a single tool (``clou_resolve_escalation``); the module
    exists to give the supervisor tier a first-class MCP surface,
    matching the coordinator-tier pattern.

    The orchestrator mounts this server on the supervisor session
    alongside the existing ``clou`` server --- see
    ``clou.orchestrator.run_supervisor`` for the wiring point.
    """
    return create_sdk_mcp_server(
        "clou_supervisor",
        tools=_build_supervisor_tools(project_dir),
    )


# ---------------------------------------------------------------------------
# Public re-exports --- VALID_DISPOSITION_STATUSES is conceptually the
# schema's status enum; callers that want to enforce it on their side
# can import from here without reaching into the escalation module.
# ---------------------------------------------------------------------------

__all__ = [
    "RESOLUTION_STATUSES",
    "VALID_DISPOSITION_STATUSES",
    "build_supervisor_mcp_server",
]
