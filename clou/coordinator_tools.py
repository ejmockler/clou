"""MCP tools for the coordinator session — protocol artifact writers.

These tools replace freeform Write calls for protocol artifacts.
The coordinator provides values; code handles format.  This eliminates
the validation→self-heal→retry loop caused by LLM format drift.

Narrative artifacts (decisions.md, assessment.md) remain freeform.

Public API:
    build_coordinator_mcp_server(project_dir, milestone) -> MCP server
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from clou.escalation import (
    HALT_OPTION_LABELS,
    VALID_CLASSIFICATIONS,
    EscalationForm,
    EscalationOption,
    render_escalation,
)
from clou.proposal import (
    VALID_SCOPES,
    MilestoneProposalForm,
    proposals_dir,
    render_proposal,
    slugify_title,
)
from clou.golden_context import (
    _extract_phase_names,
    assemble_execution,
    render_checkpoint,
    render_execution_summary,
    render_execution_task,
    render_status,
    render_status_from_checkpoint,
    sanitize_phase,
)
from clou.recovery import validate_milestone_name
from clou.recovery_checkpoint import (
    AcceptanceVerdict,
    Checkpoint,
    parse_checkpoint,
)

_log = logging.getLogger(__name__)


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* via tmp-file rename (atomic on POSIX).

    M36 F29: checkpoint writes must be all-or-nothing.  A signal
    between ``open()`` and flush leaves a truncated file that
    ``parse_checkpoint`` silently defaults to ``cycle=0, step=PLAN`` —
    indistinguishable from a legitimate fresh milestone.  Mirror of
    :func:`clou.supervisor_tools._atomic_write` and the private
    counterpart in :mod:`clou.coordinator`.  Kept local to avoid a
    circular import from :mod:`clou.supervisor_tools` (which imports
    transitively from coordinator-tier surfaces).
    """
    parent = target.parent
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
                pass
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _coerce_json_object(value: Any, *, param: str) -> dict[str, Any] | None:
    """Return *value* as a dict, JSON-parsing a string if necessary.

    The SDK's simple schema shorthand maps Python ``dict`` to JSON Schema
    ``"string"`` (claude_agent_sdk/__init__.py:288), so the LLM may send a
    JSON-encoded string.  Full JSON Schemas fix the advertisement, but we
    still accept strings defensively so one drift does not crash the tool.
    Returns None for None/empty input.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{param}: expected JSON object, got unparseable string: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"{param}: expected JSON object, got {type(parsed).__name__}"
            )
        return parsed
    raise ValueError(
        f"{param}: expected object or JSON string, got {type(value).__name__}"
    )


def _coerce_json_array(value: Any, *, param: str) -> list[Any]:
    """Return *value* as a list, JSON-parsing a string if necessary.

    Mirrors :func:`_coerce_json_object` for list-typed parameters.
    Returns an empty list for None/empty input.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{param}: expected JSON array, got unparseable string: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError(
                f"{param}: expected JSON array, got {type(parsed).__name__}"
            )
        return parsed
    raise ValueError(
        f"{param}: expected array or JSON string, got {type(value).__name__}"
    )


def _render_and_write_status(
    ms_dir: Path,
    milestone: str,
    checkpoint: Checkpoint,
    phase_names: list[str] | None = None,
) -> Path:
    """Render status.md from a Checkpoint and write it to disk.

    Returns the path written.  This is the single code path for all
    status.md writes -- checkpoint tool and status tool both call this.
    """
    content = render_status_from_checkpoint(
        milestone=milestone,
        checkpoint=checkpoint,
        phase_names=phase_names or _extract_phase_names(ms_dir),
    )
    path = ms_dir / "status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ``sanitize_phase`` enforces ``[a-zA-Z0-9][a-zA-Z0-9_-]*``.  When
# deriving a slug from a free-form title we need to strip everything
# else first.  Keeping this local rather than extending ``sanitize_phase``
# avoids widening that function's surface for an unrelated caller.
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_COLLAPSE_RE = re.compile(r"-{2,}")

# Maximum slug length --- applied to both derived slugs AND user-supplied
# slugs (F19) to keep filesystem paths bounded.  50 chars matches the
# derived-slug behavior so there is a single cap across both paths.
_SLUG_MAX_LEN = 50

# Reserved slugs used by the in-process recovery writer
# (``clou/recovery_escalation.py``).  Coordinator-tool callers that
# happen to pick the same title must not shadow these files, so the
# handler auto-suffixes them (F17).
_RECOVERY_RESERVED_SLUGS: frozenset[str] = frozenset({
    "cycle-limit",
    "agent-crash",
    "validation-failure",
    "staleness",
    # M49a: coordinator-filed trajectory halt.  Reserved so two halts
    # filed in the same second suffix cleanly rather than overwriting.
    "trajectory-halt",
})


def _derive_slug(title: str) -> str:
    """Derive a sanitize_phase-compatible slug from a free-form title.

    Normalises Unicode via NFKD + ASCII fold (so ``Café`` becomes
    ``cafe`` rather than collapsing to the fallback), lowercases, maps
    whitespace to ``-``, drops any character outside ``[a-z0-9-]``,
    collapses runs of ``-``, trims leading/trailing ``-``, and
    truncates to :data:`_SLUG_MAX_LEN` characters.  Falls back to
    ``"escalation"`` if the result is empty — downstream
    ``sanitize_phase`` requires a leading alphanumeric, so we never
    return a bare hyphen or empty string.
    """
    # NFKD + ASCII fold: ``Café`` -> ``Cafe``, ``élève`` -> ``eleve``,
    # ``ülk`` -> ``ulk`` (F25).  Without this step, titles in non-ASCII
    # scripts collapse to the fallback slug and collide with every
    # other such title filed in the same second.
    normalized = unicodedata.normalize("NFKD", title)
    ascii_bytes = normalized.encode("ascii", "ignore")
    folded = ascii_bytes.decode("ascii")
    lowered = folded.strip().lower()
    # Treat whitespace and underscores as word separators.
    hyphenated = re.sub(r"[\s_]+", "-", lowered)
    stripped = _SLUG_STRIP_RE.sub("", hyphenated)
    collapsed = _SLUG_COLLAPSE_RE.sub("-", stripped).strip("-")
    truncated = collapsed[:_SLUG_MAX_LEN].rstrip("-")
    return truncated or "escalation"


class _SlugSuffixExhausted(Exception):
    """Raised internally when suffix retries exceed :data:`_SUFFIX_MAX`.

    F22 (cycle 2): the MCP tool handler catches this and returns a
    structured ``is_error`` payload so the LLM sees the same error
    surface as validation failures (``_coerce_json_array``, etc.) rather
    than an unstructured ``RuntimeError`` bubbling through the MCP
    transport.  Kept module-private because the only consumer is the
    handler in this file.
    """


# Maximum ``-N`` suffix before surrendering.  1000 attempts in the same
# second + slug combination is well past pathological.
_SUFFIX_MAX = 999


def _exclusive_write(
    *,
    esc_dir: Path,
    base_stem: str,
    content: str,
    derived_slug: str,
    reserved: bool,
) -> tuple[Path, str]:
    """Exclusive-create an escalation file, suffixing ``-N`` on collision.

    Used by ``clou_file_escalation`` to ensure that two second-precision
    timestamps with the same slug never silently destroy the earlier
    file.  Tries the canonical ``{base_stem}.md`` path first; on
    ``FileExistsError`` falls back to ``{base_stem}-1.md``,
    ``{base_stem}-2.md``, ..., up to :data:`_SUFFIX_MAX`.

    *reserved* signals that the slug is one of the in-process recovery
    writer's fixed slugs (``cycle-limit``, ``agent-crash``,
    ``validation-failure``, ``staleness``).  The separation of concern
    is that coordinator-filed escalations must not STOMP recovery-filed
    ones --- NOT that coordinator and recovery writes must never share
    a canonical name (F16, cycle 2).  Because the exclusive-create path
    already refuses to overwrite an existing file, canonical-first-with-
    collision-fallback satisfies the no-stomping contract for both
    reserved and non-reserved slugs.  The flag is preserved in the
    signature so that any future differentiation (e.g. telemetry on
    reserved-slug collisions) can hang off it without another ABI
    change.

    Returns ``(path, slug)``: the actual written path and the slug
    component of its filename (either *derived_slug* or
    ``{derived_slug}-{N}``).

    Raises :class:`_SlugSuffixExhausted` when the suffix space is
    exhausted.  Callers in this module catch that and return a
    structured MCP tool error (F22, cycle 2).
    """
    # ``reserved`` is accepted but deliberately does not alter behavior:
    # the canonical-first-with-collision-fallback path satisfies the
    # no-stomping contract for reserved slugs too (see docstring).
    del reserved

    # Pass 1: attempt the canonical ``{base_stem}.md`` path.
    primary_path = esc_dir / f"{base_stem}.md"
    try:
        with open(primary_path, mode="x", encoding="utf-8") as fh:
            fh.write(content)
        return primary_path, derived_slug
    except FileExistsError:
        pass

    # Pass 2: fall back to ``{base_stem}-N.md`` for increasing N.
    suffix = 1
    while True:
        candidate_slug = f"{derived_slug}-{suffix}"
        candidate_path = esc_dir / f"{base_stem}-{suffix}.md"
        try:
            with open(candidate_path, mode="x", encoding="utf-8") as fh:
                fh.write(content)
            return candidate_path, candidate_slug
        except FileExistsError:
            suffix += 1
            if suffix > _SUFFIX_MAX:
                raise _SlugSuffixExhausted(
                    f"exhausted slug suffix range for slug "
                    f"{derived_slug!r} (tried 1..{_SUFFIX_MAX})"
                )


def _build_coordinator_tools(
    project_dir: Path,
    milestone: str,
) -> list[Any]:
    """Build the coordinator's ``SdkMcpTool`` list (testable seam).

    Exposes the decorated tools so unit tests can invoke handlers
    directly via ``tool.handler(args)`` without routing through the
    MCP transport.
    """
    validate_milestone_name(milestone)
    ms_dir = project_dir / ".clou" / "milestones" / milestone

    @tool(
        "clou_write_checkpoint",
        "Write the coordinator checkpoint (active/coordinator.md). "
        "Use this instead of writing the file directly — the tool "
        "guarantees correct format for cycle control flow.",
        {
            "type": "object",
            "properties": {
                "cycle": {"type": "integer"},
                "step": {"type": "string"},
                "next_step": {"type": "string"},
                "current_phase": {"type": "string"},
                "phases_completed": {"type": "integer"},
                "phases_total": {"type": "integer"},
                "cycle_outcome": {
                    "type": "string",
                    "description": (
                        "Cycle outcome classification: ADVANCED (default), "
                        "INCONCLUSIVE, INTERRUPTED, or FAILED."
                    ),
                },
                "valid_findings": {
                    "type": "integer",
                    "description": (
                        "Number of valid+security findings from this ASSESS "
                        "cycle. Set to 0 when no actionable findings. "
                        "Omit for non-ASSESS cycles."
                    ),
                },
            },
            "required": [
                "cycle", "step", "next_step",
                "current_phase", "phases_completed", "phases_total",
            ],
        },
    )
    async def write_checkpoint_tool(args: dict[str, Any]) -> dict[str, Any]:
        # Construct the Checkpoint once and use it for both the
        # checkpoint file and the status.md side-effect.  This avoids
        # a phantom Checkpoint that omits retry counter fields.
        #
        # Convergence tracking: if valid_findings is provided (ASSESS
        # cycle), compute consecutive_zero_valid from the previous
        # checkpoint.  This is structured state — no markdown parsing.
        valid_findings = args.get("valid_findings", -1)
        consecutive_zero_valid = 0
        # M36 I1 (F2 rework): preserve pre_orient_next_step across the
        # LLM-driven rewrite. The coordinator agent doesn't see this
        # field in its tool schema, but the checkpoint must not lose
        # it — the ORIENT-exit restoration depends on it surviving
        # every MCP-mediated rewrite. Read the prior value; pass it
        # through unchanged. Only the orchestrator's explicit
        # restoration block clears this field.
        # M49b B6: same wipe-class preservation for pre_halt_next_step.
        # The coordinator's MCP tool can't write or clear it; only the
        # supervisor's clou_dispose_halt clears it after disposition.
        # M49b E1 (closes B9/F1): on the HALTED transition (LLM writes
        # next_step=HALTED per the coordinator-assess prompt contract
        # after clou_halt_trajectory returns), stash the PRIOR
        # next_step into pre_halt_next_step so continue-as-is can
        # actually restore it.  Without this stash, the two-writer
        # seam (LLM + engine halt gate) produces silent stash loss —
        # by the time the gate fires on the next cycle boundary, the
        # checkpoint already says HALTED, the gate refuses to re-stash
        # HALTED, and the supervisor's continue-as-is falls back to
        # ORIENT.  Scope: only the FIRST HALTED write carries the
        # stash; a no-op HALTED-on-HALTED rewrite preserves the
        # existing stash (don't overwrite with HALTED — would raise).
        # M36 F5 (round-4): retry counters (validation_retries,
        # readiness_retries, crash_retries, staleness_count) must be
        # preserved across coordinator-initiated checkpoint writes.
        # Previously omitted → defaulted to 0 → every MCP write reset
        # the retry ceilings.  Same wipe-class as pre_orient_next_step
        # preservation; apply the same prev_cp-read-and-pass-through
        # pattern.  The LLM doesn't see these fields in its tool
        # schema; only the orchestrator mutates them.
        pre_orient_next_step = ""
        pre_halt_next_step = ""
        validation_retries = 0
        readiness_retries = 0
        crash_retries = 0
        staleness_count = 0
        # M52 F32/F33/F38: verdict gating.  ``prev_cp.last_acceptance_verdict``
        # is the source of truth for advance authorisation.  The LLM
        # never touches this field — it is engine-managed and persists
        # across MCP-mediated rewrites via passthrough.  ``last_verdict``
        # holds the inherited verdict to write back; ``advance_claimed``
        # determines whether the gate runs.
        last_verdict: AcceptanceVerdict | None = None
        prev_cp: Checkpoint | None = None
        prev_cp_path = ms_dir / "active" / "coordinator.md"
        if prev_cp_path.exists():
            prev_cp = parse_checkpoint(prev_cp_path.read_text())
            pre_orient_next_step = prev_cp.pre_orient_next_step
            pre_halt_next_step = prev_cp.pre_halt_next_step
            validation_retries = prev_cp.validation_retries
            readiness_retries = prev_cp.readiness_retries
            crash_retries = prev_cp.crash_retries
            staleness_count = prev_cp.staleness_count
            last_verdict = prev_cp.last_acceptance_verdict
            # M49b E1: HALTED transition stash.
            _incoming_next_step = args["next_step"]
            if (
                _incoming_next_step == "HALTED"
                and prev_cp.next_step != "HALTED"
                and not pre_halt_next_step
            ):
                pre_halt_next_step = prev_cp.next_step
            if valid_findings >= 0:
                if valid_findings == 0:
                    consecutive_zero_valid = prev_cp.consecutive_zero_valid + 1
                # else: reset to 0 (valid findings found).

        # M52 F33: verdict-gate validation.  An advance claim
        # (phases_completed greater than prev_cp.phases_completed) must
        # pass the gate before this tool will write the new
        # checkpoint.  See requirements.md (R2) and milestone.md
        # ("Two-writer race resolution") for the architecture.
        #
        # No-prev-cp case (first write): there is nothing to advance
        # from, so the gate is N/A — the LLM is establishing initial
        # state, not claiming progress.  Subsequent writes (prev_cp
        # exists) get the strict gate.
        new_phases_completed = args.get("phases_completed", 0)
        prev_phases_completed = (
            prev_cp.phases_completed if prev_cp is not None else 0
        )
        if prev_cp is not None and new_phases_completed > prev_phases_completed:
            # F33: single-phase increment (no skip).
            if new_phases_completed != prev_phases_completed + 1:
                from clou import telemetry
                telemetry.event(
                    "phase_advance.refused",
                    milestone=milestone,
                    reason="non_unit_increment",
                    prev=prev_phases_completed,
                    requested=new_phases_completed,
                )
                return {
                    "error": (
                        "phases_completed must increment by exactly 1; "
                        f"prev={prev_phases_completed}, "
                        f"requested={new_phases_completed}"
                    ),
                    "reason": "non_unit_increment",
                }
            # F40/F41: bootstrap / migration grace.  When prev_cp has
            # no verdict (first-ever advance OR pre-M52 checkpoint),
            # allow the advance once and emit a migration telemetry
            # event so operators can see the grace fire.  After this
            # write, the new checkpoint inherits ``None`` (no verdict
            # was supplied to install), and the next advance attempt
            # will hit the strict path with a non-None verdict only if
            # the engine has populated one in the interim.
            if prev_cp.last_acceptance_verdict is None:
                from clou import telemetry
                telemetry.event(
                    "migration.last_acceptance_verdict",
                    milestone=milestone,
                    cycle=prev_cp.cycle,
                    phase=prev_cp.current_phase,
                    new_phases_completed=new_phases_completed,
                )
                _log.warning(
                    "clou_write_checkpoint: bootstrap advance for %r "
                    "(prev_cp.last_acceptance_verdict is None); "
                    "subsequent cycles require strict verdict gating",
                    milestone,
                )
            else:
                # F33 strict path: prev_cp's verdict must authorise the advance.
                v = prev_cp.last_acceptance_verdict
                if v.decision != "Advance":
                    from clou import telemetry
                    telemetry.event(
                        "phase_advance.refused",
                        milestone=milestone,
                        reason="verdict_not_advance",
                        verdict_decision=v.decision,
                        verdict_phase=v.phase,
                    )
                    return {
                        "error": (
                            f"phases_completed advance refused: "
                            f"prev_cp.last_acceptance_verdict.decision="
                            f"{v.decision!r}, must be 'Advance'"
                        ),
                        "reason": "verdict_not_advance",
                    }
                if v.phase != prev_cp.current_phase:
                    from clou import telemetry
                    telemetry.event(
                        "phase_advance.refused",
                        milestone=milestone,
                        reason="verdict_phase_mismatch",
                        verdict_phase=v.phase,
                        current_phase=prev_cp.current_phase,
                    )
                    return {
                        "error": (
                            f"phases_completed advance refused: "
                            f"prev_cp.last_acceptance_verdict.phase="
                            f"{v.phase!r} does not match "
                            f"prev_cp.current_phase="
                            f"{prev_cp.current_phase!r}"
                        ),
                        "reason": "verdict_phase_mismatch",
                    }
                from clou import telemetry
                telemetry.event(
                    "phase_advance.accepted",
                    milestone=milestone,
                    phase=v.phase,
                    content_sha=v.content_sha,
                    new_phases_completed=new_phases_completed,
                )

        cp = Checkpoint(
            cycle=args["cycle"],
            step=args["step"],
            next_step=args["next_step"],
            current_phase=args.get("current_phase", ""),
            phases_completed=args.get("phases_completed", 0),
            phases_total=args.get("phases_total", 0),
            validation_retries=validation_retries,
            readiness_retries=readiness_retries,
            crash_retries=crash_retries,
            staleness_count=staleness_count,
            cycle_outcome=args.get("cycle_outcome", "ADVANCED"),
            valid_findings=valid_findings,
            consecutive_zero_valid=consecutive_zero_valid,
            pre_orient_next_step=pre_orient_next_step,
            pre_halt_next_step=pre_halt_next_step,
            last_acceptance_verdict=last_verdict,
        )
        content = render_checkpoint(
            cycle=cp.cycle,
            step=cp.step,
            next_step=cp.next_step,
            current_phase=cp.current_phase,
            phases_completed=cp.phases_completed,
            phases_total=cp.phases_total,
            validation_retries=cp.validation_retries,
            readiness_retries=cp.readiness_retries,
            crash_retries=cp.crash_retries,
            staleness_count=cp.staleness_count,
            cycle_outcome=cp.cycle_outcome,
            valid_findings=cp.valid_findings,
            consecutive_zero_valid=cp.consecutive_zero_valid,
            pre_orient_next_step=cp.pre_orient_next_step,
            pre_halt_next_step=cp.pre_halt_next_step,
            last_acceptance_verdict=cp.last_acceptance_verdict,
        )
        path = ms_dir / "active" / "coordinator.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        # M36 F29 (round-4): atomic write closes the truncation window
        # between ``open()`` and flush.  Coordinator-initiated writes
        # now go through the same tmp+rename path as the ORIENT-exit
        # restoration and session-start rewrite in run_coordinator.
        _atomic_write(path, content)

        # Side-effect: derive and write status.md from the same
        # Checkpoint.  This makes status.md a render-only view.
        status_path = _render_and_write_status(ms_dir, milestone, cp)

        return {
            "written": str(path),
            "status_written": str(status_path),
            "next_step": cp.next_step,
        }

    @tool(
        "clou_update_status",
        "Re-render status.md from the current checkpoint. "
        "Parameters are accepted for backward compatibility but "
        "status.md content is derived from the checkpoint.",
        {
            "type": "object",
            "properties": {
                "phase": {"type": "string"},
                "cycle": {"type": "integer"},
                "next_step": {"type": "string"},
                "phase_progress": {
                    "type": "object",
                    "description": (
                        "Map of phase name -> status "
                        "(pending/in_progress/completed/failed). "
                        "Ignored -- derived from checkpoint."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "notes": {"type": "string"},
            },
            "required": ["phase", "cycle", "next_step", "phase_progress", "notes"],
        },
    )
    async def update_status_tool(args: dict[str, Any]) -> dict[str, Any]:
        # Read the current checkpoint and re-render status.md from it.
        # If no checkpoint exists, fall back to the args provided by
        # the coordinator (backward compatibility for first PLAN cycle
        # before any checkpoint has been written).
        checkpoint_path = ms_dir / "active" / "coordinator.md"
        if checkpoint_path.exists():
            cp = parse_checkpoint(
                checkpoint_path.read_text(encoding="utf-8")
            )
            if args.get("notes"):
                _log.info(
                    "clou_update_status: notes parameter is deprecated "
                    "(status.md is now derived from checkpoint)"
                )
            status_path = _render_and_write_status(ms_dir, milestone, cp)
        else:
            # No checkpoint yet -- fall back to direct render for the
            # initial PLAN cycle.
            phase_progress = _coerce_json_object(
                args.get("phase_progress"), param="phase_progress"
            )
            if phase_progress is not None:
                phase_progress = {
                    str(k): str(v) for k, v in phase_progress.items()
                }
            # MCP schema declares next_step as required (see tool decorator
            # above).  Read it directly — no empty-string default.  Under
            # the post-F20 contract, render_status rejects empty strings
            # with ValueError; a silent "" default here would mask the
            # contract violation.  A KeyError here means MCP violated its
            # own schema, which is a bug we want to surface loudly.
            content = render_status(
                milestone=milestone,
                phase=args["phase"],
                cycle=args["cycle"],
                next_step=args["next_step"],
                phase_progress=phase_progress,
                notes=args.get("notes", ""),
            )
            status_path = ms_dir / "status.md"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(content, encoding="utf-8")
        return {"written": str(status_path)}

    @tool(
        "clou_write_execution",
        "Write a complete execution.md for a phase, including summary "
        "and task entries. Use this instead of writing execution.md "
        "directly — the tool guarantees ## Summary and ## Tasks structure.",
        {
            "type": "object",
            "properties": {
                "phase": {"type": "string"},
                "status": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "description": "List of task entries for this phase.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "status": {"type": "string"},
                            "files_changed": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "tests": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                    },
                },
                "failures": {"type": "string"},
                "blockers": {"type": "string"},
            },
            "required": ["phase", "status", "tasks", "failures", "blockers"],
        },
    )
    async def write_execution_tool(args: dict[str, Any]) -> dict[str, Any]:
        tasks_items = _coerce_json_array(args.get("tasks"), param="tasks")
        tasks_raw: list[dict[str, Any]] = []
        for i, item in enumerate(tasks_items):
            coerced = _coerce_json_object(item, param=f"tasks[{i}]")
            if coerced is None:
                # A null or empty-string item in a protocol array is
                # malformed input, not "skip this slot".  Silent drop
                # would corrupt task counts and vanish evidence.
                raise ValueError(
                    f"tasks[{i}]: expected object, got null/empty"
                )
            files_raw = coerced.get("files_changed")
            if files_raw is not None:
                coerced["files_changed"] = _coerce_json_array(
                    files_raw, param=f"tasks[{i}].files_changed"
                )
            tasks_raw.append(coerced)
        tasks_completed = sum(1 for t in tasks_raw if t.get("status") == "completed")
        tasks_failed = sum(1 for t in tasks_raw if t.get("status") == "failed")
        tasks_in_progress = sum(1 for t in tasks_raw if t.get("status") == "in_progress")

        summary = render_execution_summary(
            status=args.get("status", "in_progress"),
            tasks_total=len(tasks_raw),
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            tasks_in_progress=tasks_in_progress,
            failures=args.get("failures", "none"),
            blockers=args.get("blockers", "none"),
        )

        task_blocks = []
        for i, t in enumerate(tasks_raw, 1):
            task_blocks.append(render_execution_task(
                task_id=i,
                name=t.get("name", f"Task {i}"),
                status=t.get("status", "pending"),
                files_changed=t.get("files_changed"),
                tests=t.get("tests", ""),
                notes=t.get("notes", ""),
            ))

        content = assemble_execution(summary, task_blocks)

        phase = sanitize_phase(args["phase"])
        path = ms_dir / "phases" / phase / "execution.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"written": str(path), "task_count": len(tasks_raw)}

    @tool(
        "clou_brief_worker",
        "Construct the canonical worker briefing for a compose.py "
        "function.  The briefing contains the deterministic "
        "execution.md path computed by code — the coordinator calls "
        "this and pipes the returned text directly into the Task "
        "tool's prompt, removing any LLM-owned slug construction "
        "from worker dispatch.  Optional intent_ids append a per-"
        "intent structuring instruction; extra_reads append "
        "additional context files to the worker's read list.",
        {
            "type": "object",
            "properties": {
                "function_name": {
                    "type": "string",
                    "description": (
                        "The compose.py function name the worker will "
                        "implement.  Also the phase slug — one "
                        "function per phase directory."
                    ),
                },
                "intent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Intent IDs (e.g. I1, I3) from compose.py "
                        "docstrings.  When provided, the briefing "
                        "includes per-intent structuring guidance."
                    ),
                },
                "extra_reads": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional additional files the worker should "
                        "read, each as a path string relative to "
                        "project root (e.g. 'src/logger.ts')."
                    ),
                },
            },
            "required": ["function_name"],
        },
    )
    async def brief_worker_tool(args: dict[str, Any]) -> dict[str, Any]:
        from clou.shard import canonical_execution_path

        fn_raw = args.get("function_name")
        if not isinstance(fn_raw, str) or not fn_raw:
            return {
                "content": [{
                    "type": "text",
                    "text": "function_name must be a non-empty string",
                }],
                "is_error": True,
            }

        # sanitize_phase doubles as the function-name validator: a phase
        # directory's name is always the function name.  Drift between
        # "what the coordinator briefs" and "where workers actually land"
        # can't open up here because the tool resolves the path from the
        # validated name.
        try:
            fn = sanitize_phase(fn_raw)
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid function_name: {exc}",
                }],
                "is_error": True,
            }

        intent_ids_raw = _coerce_json_array(
            args.get("intent_ids"), param="intent_ids",
        )
        intent_ids: list[str] = []
        for i, item in enumerate(intent_ids_raw):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"intent_ids[{i}]: expected non-empty string"
                )
            intent_ids.append(item.strip())

        extra_reads_raw = _coerce_json_array(
            args.get("extra_reads"), param="extra_reads",
        )
        extra_reads: list[str] = []
        for i, item in enumerate(extra_reads_raw):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"extra_reads[{i}]: expected non-empty string"
                )
            extra_reads.append(item.strip())

        execution_rel = canonical_execution_path(fn)

        lines: list[str] = [
            f"You are implementing `{fn}` for milestone "
            f"'{milestone}', phase '{fn}'.",
            "",
            "Read your protocol file: .clou/prompts/worker.md",
            "",
            "Then read these files:",
            f"- .clou/milestones/{milestone}/compose.py — find your "
            f"function signature `{fn}`. Your criteria are in the "
            "docstring.",
            f"- .clou/milestones/{milestone}/phases/{fn}/phase.md",
            "- .clou/project.md — coding conventions",
        ]
        for extra in extra_reads:
            lines.append(f"- {extra}")
        lines += [
            "",
            "Write results to:",
            f"- .clou/milestones/{milestone}/{execution_rel}",
            "",
            "Write execution.md incrementally as you complete work.",
        ]
        if intent_ids:
            lines += [
                "",
                f"Your task addresses these intents: {', '.join(intent_ids)}",
                "Structure your execution.md with per-intent sections:",
                "## {intent_id}: {one-line description}",
                "Status: [implemented | in-progress | blocked]",
                "{details}",
            ]

        briefing = "\n".join(lines)
        return {"content": [{"type": "text", "text": briefing}]}

    @tool(
        "clou_write_assessment",
        "Write assessment.md from structured findings.  Use this "
        "instead of Write — code owns the canonical ## Summary / "
        "## Tools Invoked / ## Findings structure; you own the "
        "values.  Called by the brutalist to produce the initial "
        "assessment after a quality-gate run.  For evaluator "
        "classifications, use clou_append_classifications.",
        {
            "type": "object",
            "properties": {
                "phase_name": {
                    "type": "string",
                    "description": (
                        "Header label — for single-phase cycles pass "
                        "the phase slug; for multi-phase layers pass "
                        "a layer identifier (e.g. 'Layer 1 Cycle 2')."
                    ),
                },
                "summary": {
                    "type": "object",
                    "description": (
                        "status (completed|degraded|blocked), "
                        "tools_invoked, findings counts, "
                        "phase_evaluated.  For degraded gate runs also "
                        "pass internal_reviewers and gate_error."
                    ),
                },
                "findings": {
                    "type": "array",
                    "description": (
                        "Findings list.  Each entry: number, title, "
                        "severity (critical|major|minor), source_tool, "
                        "source_models, affected_files, finding_text, "
                        "context, optional phase for multi-phase layers."
                    ),
                },
                "tools": {
                    "type": "array",
                    "description": (
                        "Tool invocations.  Each: tool, domain, "
                        "status, optional note."
                    ),
                },
            },
            "required": ["phase_name", "summary"],
        },
    )
    async def write_assessment_tool(args: dict[str, Any]) -> dict[str, Any]:
        from clou.assessment import (
            AssessmentForm,
            AssessmentSummary,
            Finding,
            ToolInvocation,
            VALID_SEVERITIES,
            VALID_STATUSES,
            render_assessment,
        )

        phase_name = args.get("phase_name", "")
        if not isinstance(phase_name, str) or not phase_name.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "phase_name must be a non-empty string",
                }],
                "is_error": True,
            }

        summary_raw = _coerce_json_object(
            args.get("summary"), param="summary",
        )
        if summary_raw is None:
            return {
                "content": [{
                    "type": "text",
                    "text": "summary is required (status, findings counts, etc.)",
                }],
                "is_error": True,
            }

        status_raw = str(summary_raw.get("status", "")).lower().strip()
        if status_raw not in VALID_STATUSES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"summary.status must be one of "
                        f"{sorted(VALID_STATUSES)}, got {status_raw!r}"
                    ),
                }],
                "is_error": True,
            }

        def _int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        summary = AssessmentSummary(
            status=status_raw,  # type: ignore[arg-type]
            tools_invoked=_int(summary_raw.get("tools_invoked", 0)),
            findings_total=_int(summary_raw.get("findings_total", 0)),
            findings_critical=_int(
                summary_raw.get("findings_critical", 0),
            ),
            findings_major=_int(summary_raw.get("findings_major", 0)),
            findings_minor=_int(summary_raw.get("findings_minor", 0)),
            phase_evaluated=str(
                summary_raw.get("phase_evaluated", ""),
            ).strip(),
            internal_reviewers=(
                _int(summary_raw["internal_reviewers"])
                if "internal_reviewers" in summary_raw
                else None
            ),
            gate_error=(
                str(summary_raw["gate_error"]).strip()
                if "gate_error" in summary_raw
                else None
            ),
        )

        tools_raw = _coerce_json_array(args.get("tools"), param="tools")
        tools: list[ToolInvocation] = []
        for i, item in enumerate(tools_raw):
            coerced = _coerce_json_object(item, param=f"tools[{i}]")
            if coerced is None:
                continue
            tool_name = str(coerced.get("tool", "")).strip()
            if not tool_name:
                raise ValueError(
                    f"tools[{i}]: 'tool' field is required"
                )
            tools.append(ToolInvocation(
                tool=tool_name,
                domain=(
                    str(coerced["domain"]).strip()
                    if "domain" in coerced
                    else None
                ),
                status=str(coerced.get("status", "invoked")).strip(),
                note=str(coerced.get("note", "")).strip(),
            ))

        findings_raw = _coerce_json_array(
            args.get("findings"), param="findings",
        )
        findings: list[Finding] = []
        for i, item in enumerate(findings_raw):
            coerced = _coerce_json_object(item, param=f"findings[{i}]")
            if coerced is None:
                raise ValueError(
                    f"findings[{i}]: expected object, got null/empty"
                )
            severity = str(coerced.get("severity", "")).lower().strip()
            if severity not in VALID_SEVERITIES:
                raise ValueError(
                    f"findings[{i}].severity must be one of "
                    f"{sorted(VALID_SEVERITIES)}, got {severity!r}"
                )
            affected = _coerce_json_array(
                coerced.get("affected_files"),
                param=f"findings[{i}].affected_files",
            )
            source_models = _coerce_json_array(
                coerced.get("source_models"),
                param=f"findings[{i}].source_models",
            )
            phase_opt = coerced.get("phase")
            findings.append(Finding(
                number=_int(coerced.get("number", i + 1), i + 1),
                title=str(coerced.get("title", "")).strip(),
                severity=severity,  # type: ignore[arg-type]
                source_tool=str(coerced.get("source_tool", "")).strip(),
                source_models=tuple(
                    str(m).strip() for m in source_models if str(m).strip()
                ),
                affected_files=tuple(
                    str(p).strip() for p in affected if str(p).strip()
                ),
                finding_text=str(coerced.get("finding_text", "")).strip(),
                context=str(coerced.get("context", "")).strip(),
                phase=(str(phase_opt).strip() if phase_opt else None),
            ))

        form = AssessmentForm(
            phase_name=phase_name.strip(),
            summary=summary,
            tools=tuple(tools),
            findings=tuple(findings),
        )
        content = render_assessment(form)
        path = ms_dir / "assessment.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "written": str(path),
            "finding_count": len(findings),
            "status": summary.status,
        }

    @tool(
        "clou_append_classifications",
        "Append evaluator classifications to assessment.md.  Reads "
        "the existing (possibly-drifted) file, parses it into "
        "structured form, merges the classifications (last-writer-"
        "wins per finding), and re-renders to canonical form.  This "
        "is the evaluator's amendment pathway — no Write required, "
        "no drift possible.",
        {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "description": (
                        "Classifications list.  Each entry: "
                        "finding_number (int matching a Finding.number), "
                        "classification "
                        "(valid|security|architectural|noise|"
                        "next-layer|out-of-milestone|convergence), "
                        "action, reasoning."
                    ),
                },
            },
            "required": ["classifications"],
        },
    )
    async def append_classifications_tool(
        args: dict[str, Any],
    ) -> dict[str, Any]:
        from clou.assessment import (
            Classification,
            VALID_CLASSIFICATIONS,
            merge_classifications,
            parse_assessment,
            render_assessment,
        )

        path = ms_dir / "assessment.md"
        if not path.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "assessment.md does not exist — the brutalist "
                        "must run clou_write_assessment first"
                    ),
                }],
                "is_error": True,
            }

        raw = _coerce_json_array(
            args.get("classifications"), param="classifications",
        )
        if not raw:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "classifications must be a non-empty array"
                    ),
                }],
                "is_error": True,
            }

        classifications: list[Classification] = []
        for i, item in enumerate(raw):
            coerced = _coerce_json_object(
                item, param=f"classifications[{i}]",
            )
            if coerced is None:
                raise ValueError(
                    f"classifications[{i}]: expected object, got null"
                )
            kind = str(coerced.get("classification", "")).lower().strip()
            if kind not in VALID_CLASSIFICATIONS:
                raise ValueError(
                    f"classifications[{i}].classification must be one "
                    f"of {sorted(VALID_CLASSIFICATIONS)}, got {kind!r}"
                )
            try:
                number = int(coerced.get("finding_number"))
            except (TypeError, ValueError):
                raise ValueError(
                    f"classifications[{i}].finding_number must be an int"
                )
            classifications.append(Classification(
                finding_number=number,
                classification=kind,  # type: ignore[arg-type]
                action=str(coerced.get("action", "")).strip(),
                reasoning=str(coerced.get("reasoning", "")).strip(),
            ))

        existing = parse_assessment(path.read_text(encoding="utf-8"))
        merged = merge_classifications(existing, classifications)
        content = render_assessment(merged)
        path.write_text(content, encoding="utf-8")
        return {
            "written": str(path),
            "classification_count": len(merged.classifications),
        }

    @tool(
        "clou_file_escalation",
        "File an agent-to-agent escalation for this milestone.  "
        "Use ONLY for TRUE in-milestone blockers that require a human "
        "decision you cannot make (e.g., requirements ambiguity, "
        "phase-3 crash budget exhausted).  CROSS-CUTTING architectural "
        "findings must go through `clou_propose_milestone` instead --- "
        "escalations are the rare fallback, not the default for "
        "out-of-scope work.  The tool constructs an EscalationForm "
        "from your parameters, renders canonical markdown via "
        "render_escalation, and writes to "
        ".clou/milestones/{milestone}/escalations/{timestamp}-{slug}.md. "
        "Direct Write to escalations/*.md is denied by hook.  Returns "
        "the written path.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "classification": {
                    "type": "string",
                    "description": (
                        "e.g. blocking | degraded | informational | "
                        "noise — open set. For cross-cutting "
                        "architectural findings use "
                        "clou_propose_milestone instead; escalations "
                        "are reserved for in-milestone blockers."
                    ),
                },
                "context": {"type": "string"},
                "issue": {"type": "string"},
                "evidence": {"type": "string"},
                "options": {
                    "type": "array",
                    "description": (
                        "List of {label, description} objects.  Must "
                        "have at least one entry with a non-empty label."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
                "recommendation": {"type": "string"},
                "slug": {
                    "type": "string",
                    "description": (
                        "Optional.  If absent, derived from title via "
                        "sanitize_phase-style slugification."
                    ),
                },
            },
            "required": [
                "title", "classification", "issue", "options",
            ],
        },
    )
    async def file_escalation_tool(args: dict[str, Any]) -> dict[str, Any]:
        # ----- Required string fields -------------------------------------
        title = args.get("title", "")
        if not isinstance(title, str) or not title.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "title must be a non-empty string",
                }],
                "is_error": True,
            }
        classification = args.get("classification", "")
        if not isinstance(classification, str) or not classification.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "classification must be a non-empty string",
                }],
                "is_error": True,
            }
        issue = args.get("issue", "")
        if not isinstance(issue, str) or not issue.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "issue must be a non-empty string",
                }],
                "is_error": True,
            }

        # ----- Options (required, non-empty, coerce SDK string shorthand) -
        # F18: every validation failure returns a structured is_error
        # payload (no raised ValueError) so the LLM sees a uniform
        # error surface it can self-correct from.  Missing-object and
        # missing-label errors previously raised; now they structured.
        try:
            options_raw = _coerce_json_array(
                args.get("options"), param="options",
            )
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid options: {exc}",
                }],
                "is_error": True,
            }
        options: list[EscalationOption] = []
        for i, item in enumerate(options_raw):
            try:
                coerced = _coerce_json_object(item, param=f"options[{i}]")
            except ValueError as exc:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"invalid options[{i}]: {exc}",
                    }],
                    "is_error": True,
                }
            if coerced is None:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"options[{i}] must be a "
                            f"{{label, description}} object, got "
                            "null/empty"
                        ),
                    }],
                    "is_error": True,
                }
            label = str(coerced.get("label", "")).strip()
            if not label:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"options[{i}].label must be a non-empty "
                            "string"
                        ),
                    }],
                    "is_error": True,
                }
            description = str(coerced.get("description", "")).strip()
            options.append(EscalationOption(
                label=label, description=description,
            ))
        if not options:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "options must contain at least one "
                        "{label, description} entry"
                    ),
                }],
                "is_error": True,
            }

        # ----- Slug: supplied wins, else derive from title ---------------
        # F19: apply the 50-char cap to supplied slugs too.  Without
        # the cap, sanitize_phase accepts arbitrarily long slugs which
        # can exceed filesystem NAME_MAX (typically 255 bytes including
        # the timestamp prefix and ``.md`` suffix).
        supplied_slug = args.get("slug")
        if supplied_slug is not None and str(supplied_slug).strip():
            try:
                slug = sanitize_phase(str(supplied_slug).strip())
            except ValueError as exc:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"invalid slug: {exc}",
                    }],
                    "is_error": True,
                }
            # Cap after sanitize_phase --- the regex is the shape
            # check; the cap is a length check.  rstrip trailing
            # separators in case the truncation landed on one.
            if len(slug) > _SLUG_MAX_LEN:
                slug = slug[:_SLUG_MAX_LEN].rstrip("-_")
        else:
            slug = _derive_slug(title)

        # F27 / F13: classification is an OPEN-SET string (see the
        # module docstring in clou/escalation.py).  VALID_CLASSIFICATIONS
        # is advisory --- used for docs and for the soft-warning
        # feedback loop here.  If the LLM picks a classification outside
        # the hint set we still accept it (the remolding's tolerant-
        # parser contract), but return a ``warnings`` list naming the
        # canonical values so the caller can self-correct on a future
        # file.  Paired with F14 on the parse side; see escalation.py
        # module docstring for the rationale.
        normalized_classification = classification.strip().lower()
        warnings: list[str] = []
        if normalized_classification not in VALID_CLASSIFICATIONS:
            warnings.append(
                "classification "
                f"{normalized_classification!r} is not in the "
                "canonical set; accepted values are "
                f"{list(VALID_CLASSIFICATIONS)}.  Proceeding with the "
                "open-set string --- supply one of the canonical "
                "values if you want machine-routable classification."
            )

        # ----- Build the form and render ---------------------------------
        now = datetime.now(UTC)
        ts = now.strftime("%Y%m%d-%H%M%S")
        form = EscalationForm(
            title=title.strip(),
            classification=classification.strip(),
            filed=now.isoformat(),
            context=str(args.get("context", "")).strip(),
            issue=issue.strip(),
            evidence=str(args.get("evidence", "")).strip(),
            options=tuple(options),
            recommendation=str(args.get("recommendation", "")).strip(),
            disposition_status="open",
        )
        content = render_escalation(form)

        # ----- Write to disk --------------------------------------------
        # F17: exclusive-create with -1, -2 suffix retry on collision.
        # Two drivers: (1) parallel agents picking the same title
        # within the same second (slug truncation + NFKD fold amplify
        # collision probability), and (2) the in-process recovery
        # writer holding the same slug (``cycle-limit``,
        # ``agent-crash``, ``validation-failure``, ``staleness``).
        # F16 (cycle 2): canonical-first-with-collision-fallback is the
        # same policy for reserved and non-reserved slugs --- the
        # exclusive-create contract already prevents stomping, so there
        # is no reason to pre-suffix a reserved slug when the canonical
        # filename is free (doing so produced the misleading
        # ``cycle-limit-1.md`` sibling adjacent to unrelated
        # recovery-filed ``cycle-limit.md``).  The ``reserved`` flag is
        # kept on the call for documentation/telemetry hooks.
        # F22 (cycle 2): surface suffix exhaustion as a structured tool
        # error so the LLM sees a uniform error surface and can retry
        # with a different title or wait out the same-second collision.
        esc_dir = ms_dir / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        base_stem = f"{ts}-{slug}"
        try:
            path, written_slug = _exclusive_write(
                esc_dir=esc_dir,
                base_stem=base_stem,
                content=content,
                derived_slug=slug,
                reserved=slug in _RECOVERY_RESERVED_SLUGS,
            )
        except _SlugSuffixExhausted as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"escalation write failed: {exc}.  Try a "
                        "different title or wait for the next second; "
                        f"the suffix space is exhausted at "
                        f"N>{_SUFFIX_MAX}."
                    ),
                }],
                "is_error": True,
            }
        result: dict[str, Any] = {
            "written": str(path),
            "slug": written_slug,
            "classification": form.classification,
        }
        if warnings:
            result["warnings"] = warnings
        return result

    @tool(
        "clou_halt_trajectory",
        "Halt this milestone pending supervisor trajectory review "
        "(M49a/b).  Files a trajectory_halt escalation that gates "
        "engine dispatch pending supervisor review, at "
        ".clou/milestones/{milestone}/escalations/{ts}-trajectory-halt.md "
        "with three pre-populated Options: continue-as-is / re-scope / "
        "abandon.  Once M49b's engine gate is live, the pre-cycle halt "
        "check exits run_coordinator cleanly when any open "
        "trajectory_halt escalation exists for this milestone; the "
        "supervisor disposes via clou_dispose_halt (M49b: engine-gated "
        "— rewrites the checkpoint atomically out of HALTED; "
        "clou_resolve_escalation is refused for this classification) "
        "after consulting the user.  Invoke ONLY when the evaluator has "
        "classified a finding as trajectory-breakdown (F28-class: same "
        "findings re-surface across cycles with file mtimes confirming "
        "zero production change AND anti-fix pattern flagged by "
        "brutalist).  Do not use for individual in-milestone blockers "
        "--- use clou_file_escalation.  Do not use for cross-cutting "
        "architectural findings --- use clou_propose_milestone.",
        {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Short machine label.  Recommended: "
                        "anti_convergence | scope_mismatch | "
                        "irreducible_blocker (open set --- unknown "
                        "values accepted).  Lands in the escalation "
                        "title and telemetry."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Free-text explanation of WHY trajectory is "
                        "broken --- cite the meta-finding and its "
                        "structure (re-surface counts, mtimes, "
                        "anti-fix pattern).  Lands in the "
                        "escalation's Issue section."
                    ),
                },
                "evidence_paths": {
                    "type": "array",
                    "description": (
                        "Paths (or path:line references) to evidence "
                        "files: assessment.md section anchors, "
                        "decisions.md cycle blocks, telemetry log "
                        "ranges.  Rendered as a bullet list into the "
                        "Evidence section."
                    ),
                    "items": {"type": "string"},
                },
                "proposal_ref": {
                    "type": "string",
                    "description": (
                        "Optional.  Path or slug of a related "
                        "milestone proposal (via "
                        "clou_propose_milestone).  Surfaces in the "
                        "Recommendation so the supervisor can link the "
                        "halt to follow-up work."
                    ),
                },
                "cycle_num": {
                    "type": "integer",
                    "description": (
                        "Current cycle number when halt is filed.  "
                        "Surfaced in the title and telemetry."
                    ),
                },
            },
            "required": [
                "reason", "rationale", "evidence_paths", "cycle_num",
            ],
        },
    )
    async def halt_trajectory_tool(args: dict[str, Any]) -> dict[str, Any]:
        # ----- Required string: reason ------------------------------------
        reason_raw = args.get("reason", "")
        if not isinstance(reason_raw, str) or not reason_raw.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "reason must be a non-empty string",
                }],
                "is_error": True,
            }
        reason = reason_raw.strip()

        # ----- Required string: rationale --------------------------------
        rationale_raw = args.get("rationale", "")
        if not isinstance(rationale_raw, str) or not rationale_raw.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "rationale must be a non-empty string",
                }],
                "is_error": True,
            }
        rationale = rationale_raw.strip()

        # ----- Required array: evidence_paths ---------------------------
        try:
            raw_paths = _coerce_json_array(
                args.get("evidence_paths"), param="evidence_paths",
            )
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid evidence_paths: {exc}",
                }],
                "is_error": True,
            }
        # Schema advertises items as strings; reject dicts/numbers/bools
        # with a structured error rather than coercing via str(), which
        # would render ``{'path': 'x'}`` as ``"{'path': 'x'}"`` in the
        # evidence bullet list (garbage-for-supervisor).  A permissive
        # handler that accepts any JSON item here masks an LLM-side
        # schema misreading and surfaces the mistake as a dispatched
        # halt with malformed evidence.
        evidence_paths: list[str] = []
        for i, p in enumerate(raw_paths):
            if not isinstance(p, str):
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"evidence_paths[{i}] must be a string, "
                            f"got {type(p).__name__!r}"
                        ),
                    }],
                    "is_error": True,
                }
            s = p.strip()
            if s:
                evidence_paths.append(s)
        if not evidence_paths:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "evidence_paths must contain at least one "
                        "non-empty path --- halt without evidence is "
                        "not routable for supervisor review"
                    ),
                }],
                "is_error": True,
            }

        # ----- Required int: cycle_num ----------------------------------
        cycle_num = args.get("cycle_num")
        if not isinstance(cycle_num, int) or isinstance(cycle_num, bool):
            return {
                "content": [{
                    "type": "text",
                    "text": "cycle_num must be an integer",
                }],
                "is_error": True,
            }

        # ----- Optional: proposal_ref -----------------------------------
        proposal_ref_raw = args.get("proposal_ref")
        proposal_ref = (
            str(proposal_ref_raw).strip()
            if isinstance(proposal_ref_raw, str) else ""
        )

        # ----- Build canonical form -------------------------------------
        # Options are pre-populated: the coordinator does not pick
        # between them (that is the supervisor's disposition via
        # clou_dispose_halt — M49b D5).  Labels derive from
        # HALT_OPTION_LABELS (single source of truth, M49b D4) so a
        # future label change does not drift the coordinator-built
        # escalation away from the supervisor's accepted-choice set.
        _HALT_OPTION_DESCRIPTIONS: dict[str, str] = {
            "continue-as-is": (
                "Re-dispatch with current scope.  Use when the "
                "trajectory breakdown is a one-time misclassification "
                "and the existing plan should run another cycle."
            ),
            "re-scope": (
                "Route to PLAN with new scope.  Use when the current "
                "phase target cannot resolve the findings --- a "
                "different phase owner or approach is needed."
            ),
            "abandon": (
                "Route to EXIT; milestone outcome "
                "escalated_trajectory.  Use when the milestone itself "
                "is not recoverable within current constraints and "
                "should be closed."
            ),
        }
        options = tuple(
            EscalationOption(
                label=_label,
                description=_HALT_OPTION_DESCRIPTIONS[_label],
            )
            for _label in HALT_OPTION_LABELS
        )
        evidence_text = "\n".join(f"- `{p}`" for p in evidence_paths)
        recommendation_text = (
            "The coordinator has diagnosed a trajectory breakdown and "
            "defers disposition to the supervisor.  Options are "
            "pre-populated below; the supervisor disposes via "
            "`clou_dispose_halt` (M49b: engine-gated escalation — "
            "rewrites the checkpoint atomically out of HALTED, not "
            "the standard `clou_resolve_escalation` path) after "
            "consulting the user."
        )
        if proposal_ref:
            recommendation_text += (
                f"\n\nRelated proposal: `{proposal_ref}`."
            )

        now = datetime.now(UTC)
        ts = now.strftime("%Y%m%d-%H%M%S")
        form = EscalationForm(
            title=f"Trajectory halt: {reason} (cycle {cycle_num})",
            classification="trajectory_halt",
            filed=now.isoformat(),
            context=(
                f"Coordinator detected trajectory breakdown at cycle "
                f"{cycle_num}. Reason: {reason}."
            ),
            issue=rationale,
            evidence=evidence_text,
            options=options,
            recommendation=recommendation_text,
            disposition_status="open",
        )
        content = render_escalation(form)

        # ----- Write via exclusive-create (reserved slug) ---------------
        esc_dir = ms_dir / "escalations"
        esc_dir.mkdir(parents=True, exist_ok=True)
        slug = "trajectory-halt"
        base_stem = f"{ts}-{slug}"
        try:
            path, written_slug = _exclusive_write(
                esc_dir=esc_dir,
                base_stem=base_stem,
                content=content,
                derived_slug=slug,
                reserved=True,
            )
        except _SlugSuffixExhausted as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"trajectory halt write failed: {exc}.  Retry "
                        "in the next second or abort the halt attempt."
                    ),
                }],
                "is_error": True,
            }

        # ----- Telemetry ------------------------------------------------
        from clou import telemetry as _telemetry
        _telemetry.event(
            "trajectory_halt.filed",
            milestone=milestone,
            reason=reason,
            evidence_path_count=len(evidence_paths),
            proposal_ref=proposal_ref or None,
            cycle_num=cycle_num,
        )

        return {
            "written": str(path),
            "slug": written_slug,
            "classification": "trajectory_halt",
        }

    @tool(
        "clou_propose_milestone",
        "Propose a follow-up milestone to the supervisor.  Use this "
        "when you identify cross-cutting architectural work that falls "
        "outside the current milestone's scope but should be sequenced "
        "onto the roadmap.  The supervisor reads proposals on startup "
        "and decides whether to crystallize (via clou_create_milestone), "
        "reject, or defer.  Authority preserved: coordinator proposes, "
        "supervisor decides.  Use this instead of filing an architectural "
        "escalation for cross-cutting findings -- escalations are for "
        "in-milestone decisions; proposals are for out-of-milestone work. "
        "Writes to .clou/proposals/{timestamp}-{slug}.md.",
        {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Short, imperative proposal title (e.g., "
                        "'Close perception-layer gaps')."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Why this milestone is needed; what failure or "
                        "pattern surfaced the need.  Concrete evidence "
                        "goes in cross_cutting_evidence; this field is "
                        "the narrative."
                    ),
                },
                "cross_cutting_evidence": {
                    "type": "string",
                    "description": (
                        "File paths, telemetry event IDs, escalation "
                        "references, or other concrete evidence the "
                        "supervisor can verify.  Demonstrates the "
                        "proposal is not speculative."
                    ),
                },
                "estimated_scope": {
                    "type": "string",
                    "enum": list(VALID_SCOPES),
                    "description": (
                        "Rough sizing against existing milestone "
                        "history.  Honest undersell preferred over "
                        "overclaim."
                    ),
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Milestone slugs this proposal would sequence "
                        "after.  Empty if independent."
                    ),
                },
                "independent_of": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Milestone slugs explicitly identified as "
                        "parallelizable (candidate for layered "
                        "dispatch).  Use sparingly -- sequential is "
                        "the default."
                    ),
                },
                "recommendation": {
                    "type": "string",
                    "description": (
                        "Optional concrete recommendation (e.g., 'fold "
                        "into ORIENT arc as acceptance criteria' or "
                        "'defer until after M45 adjudication')."
                    ),
                },
                "cycle_num": {
                    "type": "integer",
                    "description": (
                        "The coordinator's current cycle number.  Used "
                        "for traceability in the 'Filed by' metadata."
                    ),
                },
            },
            "required": [
                "title", "rationale", "cross_cutting_evidence", "cycle_num",
            ],
        },
    )
    async def propose_milestone_tool(args: dict[str, Any]) -> dict[str, Any]:
        # ----- Required strings -----------------------------------------
        title = args.get("title", "")
        if not isinstance(title, str) or not title.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "title must be a non-empty string",
                }],
                "is_error": True,
            }
        rationale = args.get("rationale", "")
        if not isinstance(rationale, str) or not rationale.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": "rationale must be a non-empty string",
                }],
                "is_error": True,
            }
        evidence = args.get("cross_cutting_evidence", "")
        if not isinstance(evidence, str) or not evidence.strip():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "cross_cutting_evidence must be a non-empty "
                        "string — proposals require concrete evidence, "
                        "not speculation."
                    ),
                }],
                "is_error": True,
            }
        # ----- Required integer -----------------------------------------
        cycle_num_raw = args.get("cycle_num")
        if not isinstance(cycle_num_raw, int):
            return {
                "content": [{
                    "type": "text",
                    "text": "cycle_num must be an integer",
                }],
                "is_error": True,
            }

        # ----- Optional fields ------------------------------------------
        scope_raw = args.get("estimated_scope", "day")
        if scope_raw not in VALID_SCOPES:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"estimated_scope must be one of {list(VALID_SCOPES)!r}, "
                        f"got {scope_raw!r}"
                    ),
                }],
                "is_error": True,
            }
        depends_on_raw = args.get("depends_on", [])
        if not isinstance(depends_on_raw, list) or not all(
            isinstance(s, str) for s in depends_on_raw
        ):
            return {
                "content": [{
                    "type": "text",
                    "text": "depends_on must be a list of strings",
                }],
                "is_error": True,
            }
        independent_of_raw = args.get("independent_of", [])
        if not isinstance(independent_of_raw, list) or not all(
            isinstance(s, str) for s in independent_of_raw
        ):
            return {
                "content": [{
                    "type": "text",
                    "text": "independent_of must be a list of strings",
                }],
                "is_error": True,
            }
        recommendation = args.get("recommendation", "")
        if not isinstance(recommendation, str):
            recommendation = ""

        # ----- Build form, render, write -------------------------------
        form = MilestoneProposalForm(
            title=title.strip(),
            filed_by_milestone=milestone,
            filed_by_cycle=cycle_num_raw,
            rationale=rationale.strip(),
            cross_cutting_evidence=evidence.strip(),
            estimated_scope=scope_raw,  # type: ignore[arg-type]
            depends_on=tuple(s.strip() for s in depends_on_raw if s.strip()),
            independent_of=tuple(
                s.strip() for s in independent_of_raw if s.strip()
            ),
            recommendation=recommendation.strip(),
        )

        proposals = proposals_dir(project_dir / ".clou")
        proposals.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = slugify_title(title) or "untitled"

        try:
            content = render_proposal(form)
        except ValueError as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"proposal render failed: {e}",
                }],
                "is_error": True,
            }

        # C3-review: TOCTOU-safe exclusive create using O_EXCL, mirroring
        # the escalation tool's _exclusive_write pattern.  The prior
        # implementation used path.exists() + write_text which races
        # under concurrent coordinator dispatch: two proposers in the
        # same second with the same slug could observe the same free
        # path and one silently overwrite the other.
        base_stem = f"{timestamp}-{slug}"
        try:
            path, written_slug = _exclusive_write(
                esc_dir=proposals,
                base_stem=base_stem,
                content=content,
                derived_slug=slug,
                reserved=False,
            )
        except _SlugSuffixExhausted as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"proposal slug suffix exhausted: {exc}.  Retry "
                        "with a different title."
                    ),
                }],
                "is_error": True,
            }
        except OSError as exc:
            # Disk full, permission error, etc.  Surface as a structured
            # MCP error rather than letting it propagate as an untyped
            # transport failure.  Zero-escalations: this is a producer
            # -side failure; the coordinator should retry or log to
            # decisions.md, never escalate.
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"proposal write failed: {exc}.  Check disk "
                        "space and .clou/proposals/ permissions."
                    ),
                }],
                "is_error": True,
            }
        return {
            "written": str(path),
            "slug": written_slug,
            "filed_by_milestone": milestone,
            "filed_by_cycle": cycle_num_raw,
        }

    @tool(
        "clou_write_judgment",
        "Write a typed judgment artifact for the current ORIENT cycle.  "
        "Required fields: next_action (cycle-type vocabulary --- one of "
        "VALID_NEXT_ACTIONS from clou.judgment), rationale, "
        "evidence_paths (non-empty list of file paths consulted), "
        "expected_artifact (one-line description of what the next cycle "
        "should produce), cycle (1-based integer matching the current "
        "coordinator cycle).  Validates the structured form and on "
        "failure returns a structured is_error payload instead of "
        "raising --- the coordinator LLM can inspect the error message "
        "and self-correct.  Writes canonical markdown to "
        ".clou/milestones/{milestone}/judgments/cycle-{NN}-judgment.md; "
        "direct Write to that path is denied by hook, so this tool is "
        "the only authorised writer.",
        {
            "type": "object",
            "properties": {
                "next_action": {
                    "type": "string",
                    "description": (
                        "Next cycle action the coordinator should "
                        "dispatch (e.g. PLAN, EXECUTE, ASSESS, VERIFY, "
                        "EXIT, ORIENT).  Must match one of "
                        "clou.judgment.VALID_NEXT_ACTIONS."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Why this next_action is justified by the "
                        "evidence.  Free-form prose."
                    ),
                },
                "evidence_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Non-empty list of file paths the coordinator "
                        "consulted to form this judgment (e.g. "
                        "intents.md, status.md, phase execution.md "
                        "files).  JSON-string shorthand also accepted."
                    ),
                },
                "expected_artifact": {
                    "type": "string",
                    "description": (
                        "One-line description of the artifact the next "
                        "cycle is expected to produce.  Free-form prose."
                    ),
                },
                "cycle": {
                    "type": "integer",
                    "description": (
                        "Current cycle number (1-based).  Determines "
                        "the judgments/cycle-{NN}-judgment.md filename."
                    ),
                },
            },
            "required": [
                "next_action", "rationale", "evidence_paths",
                "expected_artifact", "cycle",
            ],
        },
    )
    async def write_judgment_tool(args: dict[str, Any]) -> dict[str, Any]:
        # Importing inside the handler (not at module top) mirrors the
        # assessment/escalation tools' pattern: the judgment module is a
        # light import but the phase contract says handler imports only,
        # so the coordinator_tools module never inadvertently takes a
        # hard dependency on clou.judgment at import time.
        from clou.judgment import (
            JUDGMENT_PATH_TEMPLATE,
            JudgmentForm,
            render_judgment,
            validate_judgment_fields,
        )

        # ----- evidence_paths (JSON-shorthand coercion) ----------------
        try:
            evidence_paths_raw = _coerce_json_array(
                args.get("evidence_paths"), param="evidence_paths",
            )
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": f"invalid evidence_paths: {exc}",
                }],
                "is_error": True,
            }
        # Schema advertises items as strings; reject dicts/numbers/bools
        # with a structured error rather than coercing via ``str(p)``,
        # which would silently render ``{'path': 'x'}`` as a literal
        # ``"{'path': 'x'}"`` and the malformed payload would land in
        # the supervisor's evidence list.  Same validation pattern as
        # ``clou_halt_trajectory`` (line ~1465).  Closes Task #26.
        evidence_paths_list: list[str] = []
        for i, p in enumerate(evidence_paths_raw):
            if not isinstance(p, str):
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"evidence_paths[{i}] must be a string, "
                            f"got {type(p).__name__!r}"
                        ),
                    }],
                    "is_error": True,
                }
            s = p.strip()
            if s:
                evidence_paths_list.append(s)
        evidence_paths = tuple(evidence_paths_list)

        # ----- cycle (positive integer) --------------------------------
        # Validated BEFORE building the form so malformed cycle inputs
        # never reach the filesystem path-format step.  ``bool`` is a
        # subclass of ``int`` in Python; reject it explicitly so
        # ``cycle=True`` doesn't format to ``cycle-01-judgment.md``.
        cycle_raw = args.get("cycle")
        if isinstance(cycle_raw, bool) or not isinstance(cycle_raw, int):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "cycle must be a positive integer (got "
                        f"{type(cycle_raw).__name__})"
                    ),
                }],
                "is_error": True,
            }
        if cycle_raw < 1:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"cycle must be a positive integer, got {cycle_raw}"
                    ),
                }],
                "is_error": True,
            }

        # ----- Build form and validate --------------------------------
        form = JudgmentForm(
            next_action=str(args.get("next_action", "")).strip(),
            rationale=str(args.get("rationale", "")).strip(),
            evidence_paths=evidence_paths,
            expected_artifact=str(args.get("expected_artifact", "")).strip(),
        )
        try:
            validate_judgment_fields(form)
        except ValueError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": str(exc),
                }],
                "is_error": True,
            }

        # ----- Render and write --------------------------------------
        relative = JUDGMENT_PATH_TEMPLATE.format(cycle=cycle_raw)
        path = ms_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(render_judgment(form), encoding="utf-8")
        except OSError as exc:
            # Surface disk/permission failures as a structured tool error
            # mirroring clou_propose_milestone's OSError branch.
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"judgment write failed: {exc}.  Check disk "
                        f"space and {path.parent} permissions."
                    ),
                }],
                "is_error": True,
            }

        return {
            "written": str(path),
            "next_action": form.next_action,
            "evidence_path_count": len(evidence_paths),
        }

    return [
        write_checkpoint_tool,
        update_status_tool,
        write_execution_tool,
        brief_worker_tool,
        write_assessment_tool,
        append_classifications_tool,
        file_escalation_tool,
        halt_trajectory_tool,
        propose_milestone_tool,
        write_judgment_tool,
    ]


def build_coordinator_mcp_server(
    project_dir: Path,
    milestone: str,
) -> Any:
    """Build an in-process MCP server with protocol artifact tools.

    Scoped to a single milestone — all writes target that milestone's
    directory.  The coordinator session gets this alongside template
    MCP servers (e.g. brutalist).
    """
    return create_sdk_mcp_server(
        "clou_coordinator",
        tools=_build_coordinator_tools(project_dir, milestone),
    )
