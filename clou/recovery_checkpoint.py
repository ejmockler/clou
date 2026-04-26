"""Checkpoint parsing and cycle determination.

Handles Checkpoint dataclass, ConvergenceState, parse_checkpoint(),
determine_next_cycle(), assess_convergence(), read_cycle_count(),
read_cycle_outcome(), and milestone name validation.

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_log = logging.getLogger(__name__)

#: Cycle-type to memory-pattern-type mapping for filtered retrieval (DB-18 I6).
#: Cycle types not listed here receive no memory.md content.
_MEMORY_TYPE_FILTERS: dict[str, list[str]] = {
    "PLAN": ["decomposition", "cost-calibration", "debt"],
    "ASSESS": ["quality-gate", "escalation"],
}

#: Milestone names must be lowercase alphanumeric with hyphens.
_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Phase names must be lowercase alphanumeric with hyphens or underscores.
#: Whitelist prevents path traversal via .., /, \, null bytes, etc. (F9).
_PHASE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_milestone_name(name: str) -> None:
    """Raise ValueError if *name* is not a valid milestone slug."""
    if not _MILESTONE_RE.match(name):
        msg = f"Invalid milestone name: {name!r} (must match [a-z0-9][a-z0-9-]*)"
        raise ValueError(msg)


# Keep the old private name for internal callers.
_validate_milestone = validate_milestone_name


class CycleOutcome(str, Enum):
    """Typed cycle outcome for checkpoint staleness tracking.

    ADVANCED: phase advancement or successful cycle completion.
    INCONCLUSIVE: cycle could not complete (tools unavailable, etc).
    INTERRUPTED: timeout/sleep/network interruption.
    FAILED: genuine crash or persistent error.
    HALTED_PENDING_REVIEW: M49b — engine halt gate fired; the
        coordinator exited cleanly pending supervisor disposition
        of an open trajectory_halt escalation.  Staleness-class
        semantics match INCONCLUSIVE/INTERRUPTED: counter resets,
        does not accumulate.  The supervisor resolves the halt
        escalation + writes the appropriate downstream checkpoint
        on disposition (continue-as-is / re-scope / abandon).
    """

    ADVANCED = "ADVANCED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    HALTED_PENDING_REVIEW = "HALTED_PENDING_REVIEW"


#: Consecutive zero-accept ASSESS cycles required to declare convergence.
_CONVERGENCE_THRESHOLD = 2

#: Valid next_step values in coordinator checkpoints.
#: ORIENT is the observation-first cycle prepended to every session
#: (M36). It joins the vocabulary because ``determine_next_cycle`` and
#: ``parse_checkpoint`` both gate on this set — once dispatch rewrites
#: ``next_step`` to ``"ORIENT"`` at session start, the parser must
#: accept it rather than downgrade it to PLAN.
#: HALTED is the next_step written when the engine's trajectory-halt
#: gate fires (M49b).  ``parse_checkpoint`` must accept it or halted
#: checkpoints silently coerce to PLAN on re-parse, losing the halt
#: signal (brutalist Issue A).  Supervisor disposition overwrites
#: this value on resolution.
#: M50 I1 cycle-3 rework (F4/F15): ``'none'`` is NOT in the validated
#: vocabulary.  ``parse_checkpoint`` coerces legacy ``next_step: none``
#: inputs to ``COMPLETE`` before vocabulary validation (see below), so
#: ``'none'`` never reaches ``determine_next_cycle``'s match arm or
#: downstream consumers.  Keeping ``'none'`` OUT of the set means
#: ``validate_checkpoint`` / ``render_checkpoint`` both reject it on
#: writes — preventing the cycle-2 asymmetric round-trip where
#: ``render(Checkpoint(next_step='none'))`` emitted literal
#: ``next_step: none`` to disk while parse coerced that file back to
#: ``COMPLETE``.  Two distinct persisted representations now cannot
#: map to one in-memory state; the parse-coercion is a one-way legacy-
#: input tolerance, not a bidirectional membership claim.
_VALID_NEXT_STEPS = frozenset(
    {
        "PLAN",
        "EXECUTE",
        "EXECUTE_REWORK",
        "EXECUTE_VERIFY",
        "ASSESS",
        "REPLAN",
        "VERIFY",
        "EXIT",
        "COMPLETE",
        "ORIENT",
        "HALTED",
    }
)

#: M50 I1 vocabulary canonicalization — legacy tokens rejected at
#: parse time with an actionable warning.  See parse_checkpoint /
#: validate_judgment_fields; the migration helper
#: :func:`migrate_legacy_tokens` rewrites persisted artifacts.
#:
#: M50 I1 cycle-4 rework (F13): ``"none"`` joins the two punctuated
#: legacy tokens as a one-way parse-coercion legacy mapping.  The
#: coordinator's early exit template historically wrote
#: ``next_step: none`` to signal milestone completion; cycle-3's
#: vocabulary narrowing (F4/F15) replaced the canonical vocabulary
#: with ``COMPLETE`` but left the persisted tokens on disk unbounded.
#: Adding ``"none": "COMPLETE"`` here lets
#: :func:`migrate_legacy_tokens` sweep the persisted form to
#: ``COMPLETE`` via the shared anchored-regex infrastructure.
#:
#: The parse-time coercion at :func:`parse_checkpoint` is retained
#: as a belt-and-suspenders backstop for externally-authored files
#: (rare after migration runs once, but tolerant of pre-migration
#: backups or paused-milestone restores).
_LEGACY_NEXT_STEPS: dict[str, str] = {
    "EXECUTE (rework)": "EXECUTE_REWORK",
    "EXECUTE (additional verification)": "EXECUTE_VERIFY",
    "none": "COMPLETE",
}

#: Cycle types that dispatch an EXECUTE-family phase (plain execute,
#: rework execute, verify execute).  Consumers that switch on the
#: cycle-type string use :func:`is_execute_family` (below) to
#: recognise the family without needing to know the canonical token
#: list.  M50 I1 cycle-2 rework (F5/F12): ``determine_next_cycle``
#: preserves the structured discriminator (``EXECUTE_REWORK`` /
#: ``EXECUTE_VERIFY``) in its return value so telemetry payloads
#: carry the canonical token; downstream consumers that want
#: "this is an EXECUTE phase" semantics call ``is_execute_family``.
_EXECUTE_FAMILY: frozenset[str] = frozenset(
    {"EXECUTE", "EXECUTE_REWORK", "EXECUTE_VERIFY"}
)

#: M52 F32/F33/F38: phase-acceptance verdict decisions.  Stored in
#: ``Checkpoint.last_acceptance_verdict`` (the wire-format field is
#: ``last_acceptance_verdict: <phase>|<decision>|<content_sha>``).
#: ``Advance`` is the only decision that permits ``phases_completed``
#: to increment; ``GateDeadlock`` records the gate's rejection so the
#: supervisor can triage.
_VALID_VERDICT_DECISIONS: frozenset[str] = frozenset(
    {"Advance", "GateDeadlock"},
)


def is_execute_family(cycle_type: str) -> bool:
    """Return True iff *cycle_type* dispatches an EXECUTE-family phase.

    M50 I1 cycle-2 rework.  Downstream callers (``prompts.py``,
    ``coordinator.py``) check ``cycle_type == "EXECUTE"`` in several
    places to decide whether to include the DAG context, capture the
    working tree state, or commit at phase completion.  After the
    structured-token preservation fix, ``determine_next_cycle`` may
    return ``EXECUTE_REWORK`` / ``EXECUTE_VERIFY`` instead of
    collapsing to ``"EXECUTE"``.  This helper recognises all three
    via the canonical ``_EXECUTE_FAMILY`` set so consumers do not have
    to enumerate the tokens.
    """
    return cycle_type in _EXECUTE_FAMILY


def is_rework_requested(checkpoint: "Checkpoint") -> bool:
    """Return True iff *checkpoint* records an active EXECUTE_REWORK request.

    M50 I1 cycle-2 rework (F6/F8/F33/F35).  The coordinator's
    rework-detection logic reads from ``checkpoint.next_step`` (the
    scheduled step) with a fallback to ``pre_orient_next_step`` (the
    stashed step when ORIENT is pending or live).  The effective
    dispatch target is whichever is non-ORIENT:

    - Iteration 1 (session-start rewrite): ``next_step == "ORIENT"``
      with ``pre_orient_next_step == "EXECUTE_REWORK"`` — the stash
      is the source of truth.
    - Iteration 2+ (ORIENT already consumed): the stash was copied
      back to ``next_step`` by the ORIENT-exit restoration block, so
      ``next_step == "EXECUTE_REWORK"`` and the stash is empty.
    - Non-ORIENT trajectories: ``next_step`` is authoritative
      directly.

    The helper encapsulates this precedence so tests that exercise
    production behaviour do not have to re-derive the effective step
    from local Python literals.  Callers that want this verdict
    mid-coordinator should always use this helper rather than
    inlining the fallback logic; keeping the effective-step rule in
    one place ensures ORIENT-stash semantics evolve as a unit.
    """
    effective = checkpoint.next_step
    if effective == "ORIENT" and checkpoint.pre_orient_next_step:
        effective = checkpoint.pre_orient_next_step
    return effective == "EXECUTE_REWORK"


#: Alias exported for callers that want to reference the cycle name
#: symbolically (mirrors how other cycle types are hard-coded as
#: string literals — kept as a constant for readability in
#: ``run_coordinator``'s first-iteration branch).
ORIENT_PROTOCOL = "ORIENT"


@dataclass(frozen=True, slots=True)
class AcceptanceVerdict:
    """Persisted phase-acceptance gate verdict (M52 F32, F33, F35, F38).

    Wire format: ``<phase>|<decision>|<content_sha>`` (single line in
    the checkpoint envelope under ``last_acceptance_verdict``).  The
    pipe is unambiguous because ``_PHASE_RE`` forbids ``|`` in phase
    names.

    ``decision`` is one of ``"Advance"`` / ``"GateDeadlock"`` —
    enforced by ``parse_checkpoint``'s wire-format reader.

    ``content_sha`` ties the verdict to a specific ``execution.md``
    body sha (Advance path) or carries the empty string for deadlocks
    where no parseable body is available (parse_error /
    missing_artifact_type).
    """

    phase: str
    decision: str  # "Advance" or "GateDeadlock"
    content_sha: str = ""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Parsed coordinator checkpoint state."""

    cycle: int = 0
    step: str = "PLAN"
    next_step: str = "PLAN"
    current_phase: str = ""
    phases_completed: int = 0
    phases_total: int = 0
    # Retry counters -- persisted so they survive process restarts.
    validation_retries: int = 0
    readiness_retries: int = 0
    crash_retries: int = 0
    staleness_count: int = 0
    # Typed cycle outcome -- drives staleness classification.
    cycle_outcome: str = "ADVANCED"
    # ASSESS convergence tracking (structured, not markdown-parsed).
    # -1 = not an ASSESS cycle; 0+ = count of valid+security findings.
    valid_findings: int = -1
    # Consecutive ASSESS cycles with zero valid findings.
    # Coordinator increments when valid_findings==0, resets when >0.
    consecutive_zero_valid: int = 0
    # M36 I1 (F2 rework): session-start ORIENT dispatch stashes the
    # pre-ORIENT next_step here so the ORIENT-exit restoration path
    # (F1 rework) can restore dispatch trajectory after ORIENT
    # completes. Typed — not a trailing markdown line — so every
    # parse/render round-trip (_persist_retry_counters, escalation
    # reset, self-heal, clou_write_checkpoint) preserves it.
    # Empty string = no pending ORIENT restoration; non-empty =
    # ORIENT is live and will be restored next iteration.
    pre_orient_next_step: str = ""
    # M49b B6: pre-halt next_step stash.  When the engine halt gate
    # fires, the prior ``next_step`` (whatever the engine was about
    # to dispatch — PLAN, EXECUTE, ASSESS, etc.) is stashed here so
    # the supervisor's ``clou_dispose_halt`` tool can restore it
    # under the ``continue-as-is`` choice.  Empty string = no halt
    # restoration pending (the common case).  Non-empty = a halt
    # was filed and the supervisor has not yet dispositioned it.
    # Cleared by ``clou_dispose_halt`` once the new ``next_step``
    # is written, so future halts on the same milestone start clean.
    pre_halt_next_step: str = ""
    # M52 F32/F33/F38: phase-acceptance gate verdict, persisted in the
    # checkpoint envelope so the verdict-gate validation in
    # ``clou_write_checkpoint`` can refuse phase advances without an
    # ``Advance`` verdict for the right phase.  ``None`` means no
    # verdict has been recorded yet (fresh milestone, pre-M52
    # checkpoint via the migration shim, or an engine path that has
    # not yet written one).  See F40 (bootstrap rule) and F41
    # (migration shim).
    last_acceptance_verdict: AcceptanceVerdict | None = None


@dataclass(frozen=True, slots=True)
class ConvergenceState:
    """Result of analyzing decisions.md for ASSESS cycle convergence."""

    consecutive_zero_accepts: int
    total_assess_cycles: int
    converged: bool


# Matches "## Cycle N — Brutalist Assessment" or similar ASSESS headers.
_ASSESS_HEADER_RE = re.compile(r"(?m)^## Cycle\s+\d+\s*[—–-]\s*(.+)$")


def _safe_int(value: str, default: int = 0) -> int:
    """Convert a string to a non-negative int, returning *default* on failure.

    Handles malformed agent-written values gracefully (e.g. ``"boom"``).
    """
    try:
        result = int(value)
        return max(result, 0)  # Non-negative
    except (ValueError, TypeError):
        return default


def parse_checkpoint(content: str) -> Checkpoint:
    """Parse a coordinator checkpoint markdown file and extract structured state.

    Extracts ``key: value`` pairs from lines and maps them to Checkpoint fields.
    Missing fields fall back to dataclass defaults.

    Runs DB-12 checkpoint validation first and logs warnings for any errors.
    Validation errors do NOT prevent parsing -- the orchestrator's
    ``validate_golden_context()`` call at cycle boundaries is the enforcement
    point.  This log provides early visibility.
    """
    from clou.validation import validate_checkpoint

    errors = validate_checkpoint(content)
    for err in errors:
        _log.warning("Checkpoint validation: %s", err)

    fields: dict[str, str] = {}
    # M36 F2: use ``[ \t]*`` (literal horizontal whitespace) not ``\s*``
    # to prevent the separator from greedily consuming newlines — an
    # empty value like ``current_phase: \n`` must parse as ``""``, not
    # swallow the following line.  Also use ``.*`` instead of ``.+`` so
    # empty values are captured (not skipped).
    for match in re.finditer(r"(?m)^(\w[\w_]*):[ \t]*(.*)$", content):
        fields[match.group(1)] = match.group(2).strip()

    next_step = fields.get("next_step", "PLAN")
    # M50 I1 cycle-4 rework (F12): narrow the 'none' coercion to
    # exact-match and emit a warning log.  The cycle-3 narrowing of
    # the canonical vocabulary (F4/F15) removed ``"none"`` from
    # ``_VALID_NEXT_STEPS``, but left behind a case-insensitive
    # ``next_step.lower() == "none"`` check that silently coerced
    # ``next_step: None`` / ``next_step: NONE`` (human-debugger
    # conventions) to ``COMPLETE`` with no audit trail.  That
    # violates the "no silent coerce" contract.  The fix:
    #
    #   * Exact-match ``"none"`` only — every other vocabulary check
    #     is case-sensitive, so ``"none"`` joins the discipline.
    #   * WARN log names the legacy token, the structured
    #     replacement, and points at the migration helper.  The
    #     warning is the signal that persisted state drifted; the
    #     migration is the cleanup.
    #   * Non-canonical casings (``"None"``, ``"NONE"``, ``"NoNe"``)
    #     fall through to the ``_VALID_NEXT_STEPS`` rejection branch
    #     below, which emits its own ``Unknown next_step`` warning
    #     and defaults to PLAN.  The user sees a clear error rather
    #     than a silent milestone termination.
    if next_step == "none":
        _log.warning(
            "parse_checkpoint: coercing legacy next_step: 'none' -> "
            "'COMPLETE'.  Run clou.vocabulary_migration."
            "migrate_legacy_tokens to rewrite persisted tokens; "
            "the one-way parse coercion is a legacy-input tolerance, "
            "not a bidirectional vocabulary member.",
        )
        next_step = "COMPLETE"
    # M50 I1: reject punctuated legacy tokens with an actionable
    # message naming the structured replacement.  We MUST NOT silently
    # coerce to the new form — the rejection is the signal that
    # documented, persisted state drifted.  The one-shot migration
    # helper (:func:`migrate_legacy_tokens`) is the canonical way to
    # rewrite these on disk.
    if next_step in _LEGACY_NEXT_STEPS:
        _replacement = _LEGACY_NEXT_STEPS[next_step]
        _log.warning(
            "legacy cycle-type token %r --- use %r "
            "(run clou.vocabulary_migration.migrate_legacy_tokens to "
            "rewrite persisted artifacts); defaulting to PLAN",
            next_step, _replacement,
        )
        next_step = "PLAN"
    elif next_step not in _VALID_NEXT_STEPS:
        _log.warning("Unknown next_step %r --- defaulting to PLAN", next_step)
        next_step = "PLAN"

    # Backward-compatible: missing cycle_outcome defaults to "ADVANCED"
    # (existing checkpoints without the field were successful cycles).
    raw_outcome = fields.get("cycle_outcome", "ADVANCED")
    try:
        cycle_outcome = CycleOutcome(raw_outcome).value
    except ValueError:
        _log.warning("Unknown cycle_outcome %r --- defaulting to ADVANCED", raw_outcome)
        cycle_outcome = CycleOutcome.ADVANCED.value

    # valid_findings: -1 means "not an ASSESS cycle" (or missing field).
    # Can't use _safe_int here — it clamps negatives to 0 but -1 is a
    # valid sentinel meaning "not an ASSESS cycle".
    try:
        valid_findings = int(fields.get("valid_findings", "-1"))
    except (ValueError, TypeError):
        valid_findings = -1

    # M36 I1 (F2 rework): pre_orient_next_step is a typed field.
    # Accept it from the parsed ``key: value`` scan.  Unknown values
    # fall back to empty (meaning: no ORIENT restoration pending).
    # Validate against the same vocabulary used for ``next_step`` so
    # a bogus value doesn't silently round-trip; empty is the
    # canonical "no pending restoration" signal.
    # M36 F2 (round-4): drop ORIENT symmetrically with HALTED — the
    # ORIENT-exit restoration would "restore" next_step=ORIENT over
    # itself, producing a sink state.  Mirror guard in
    # golden_context.render_checkpoint.
    raw_pre_orient = fields.get("pre_orient_next_step", "").strip()
    # M50 I1: legacy punctuated tokens in pre_orient_next_step are
    # rejected identically to next_step — no silent coerce.  Drop to
    # empty so the ORIENT-exit restoration path does not silently
    # consume a malformed stash.
    if raw_pre_orient in _LEGACY_NEXT_STEPS:
        _log.warning(
            "legacy cycle-type token in pre_orient_next_step %r --- "
            "use %r; dropping stash",
            raw_pre_orient, _LEGACY_NEXT_STEPS[raw_pre_orient],
        )
        raw_pre_orient = ""
    elif raw_pre_orient and (
        raw_pre_orient not in _VALID_NEXT_STEPS
        or raw_pre_orient == "HALTED"
        or raw_pre_orient == "ORIENT"
    ):
        _log.warning(
            "Unknown or self-referential pre_orient_next_step %r --- "
            "dropping (HALTED/ORIENT would loop via ORIENT-exit "
            "restoration)",
            raw_pre_orient,
        )
        raw_pre_orient = ""

    # M49b B6: pre_halt_next_step mirror of pre_orient_next_step.
    # Stashed by the engine halt gate; consumed by clou_dispose_halt
    # under the ``continue-as-is`` choice.  Same validation rules.
    # HALTED is intentionally excluded — stashing HALTED would create
    # a restoration loop (gate → stash HALTED → dispose → restore
    # HALTED → gate again).  Drop with warning if seen.
    raw_pre_halt = fields.get("pre_halt_next_step", "").strip()
    # M50 I1: legacy punctuated tokens in pre_halt_next_step are
    # rejected identically to next_step — no silent coerce.
    if raw_pre_halt in _LEGACY_NEXT_STEPS:
        _log.warning(
            "legacy cycle-type token in pre_halt_next_step %r --- "
            "use %r; dropping stash",
            raw_pre_halt, _LEGACY_NEXT_STEPS[raw_pre_halt],
        )
        raw_pre_halt = ""
    elif raw_pre_halt and (
        raw_pre_halt not in _VALID_NEXT_STEPS or raw_pre_halt == "HALTED"
    ):
        _log.warning(
            "Unknown or self-referential pre_halt_next_step %r --- "
            "dropping",
            raw_pre_halt,
        )
        raw_pre_halt = ""

    # M52 F38/F41: parse ``last_acceptance_verdict`` wire format.
    # Wire format: ``last_acceptance_verdict: <phase>|<decision>|<sha>``
    # or the literal ``none`` sentinel for absent verdicts.  Pre-M52
    # checkpoints (F41 migration shim): the field is missing from the
    # serialised text → ``None``.  The bootstrap rule lives in
    # ``clou_write_checkpoint``; the parser stays pure.
    raw_verdict = fields.get("last_acceptance_verdict", "").strip()
    last_acceptance_verdict: AcceptanceVerdict | None = None
    if raw_verdict and raw_verdict != "none":
        parts = raw_verdict.split("|")
        if len(parts) == 3:
            v_phase, v_decision, v_sha = parts
            if v_decision in _VALID_VERDICT_DECISIONS and _PHASE_RE.match(
                v_phase,
            ):
                last_acceptance_verdict = AcceptanceVerdict(
                    phase=v_phase,
                    decision=v_decision,
                    content_sha=v_sha,
                )
            else:
                _log.warning(
                    "parse_checkpoint: malformed last_acceptance_verdict "
                    "%r — phase or decision invalid; treating as None",
                    raw_verdict,
                )
        else:
            _log.warning(
                "parse_checkpoint: malformed last_acceptance_verdict "
                "%r — expected three pipe-separated parts; "
                "treating as None",
                raw_verdict,
            )

    return Checkpoint(
        cycle=_safe_int(fields.get("cycle", "0")),
        step=fields.get("step", "PLAN"),
        next_step=next_step,
        current_phase=fields.get("current_phase", fields.get("phase", "")),
        phases_completed=_safe_int(fields.get("phases_completed", "0")),
        phases_total=_safe_int(fields.get("phases_total", "0")),
        validation_retries=_safe_int(fields.get("validation_retries", "0")),
        readiness_retries=_safe_int(fields.get("readiness_retries", "0")),
        crash_retries=_safe_int(fields.get("crash_retries", "0")),
        staleness_count=_safe_int(fields.get("staleness_count", "0")),
        cycle_outcome=cycle_outcome,
        valid_findings=valid_findings,
        consecutive_zero_valid=_safe_int(
            fields.get("consecutive_zero_valid", "0"),
        ),
        pre_orient_next_step=raw_pre_orient,
        pre_halt_next_step=raw_pre_halt,
        last_acceptance_verdict=last_acceptance_verdict,
    )


def assess_convergence(
    checkpoint: Checkpoint | None = None,
    *,
    decisions_content: str | None = None,
    threshold: int = _CONVERGENCE_THRESHOLD,
) -> ConvergenceState:
    """Check whether ASSESS cycles have converged (zero valid findings).

    **Primary path** (structured): reads ``consecutive_zero_valid`` from the
    checkpoint.  No markdown parsing — immune to LLM formatting variance.

    **Fallback path** (legacy): if the checkpoint lacks convergence data
    (pre-existing milestones), falls back to scanning decisions.md for
    ``### Valid:`` / ``### Security:`` headings (the actionable classifications).

    Convergence is reached when consecutive zero-valid-finding ASSESS
    cycles meet or exceed *threshold*.
    """
    # --- Primary: structured checkpoint path ---
    # Use structured path only when the checkpoint has convergence data
    # (valid_findings >= 0 means this checkpoint was written by an ASSESS
    # cycle that included the structured convergence fields).
    if checkpoint is not None and checkpoint.valid_findings >= 0:
        return ConvergenceState(
            consecutive_zero_accepts=checkpoint.consecutive_zero_valid,
            total_assess_cycles=-1,  # Not tracked in checkpoint.
            converged=checkpoint.consecutive_zero_valid >= threshold,
        )

    # --- Fallback: parse decisions.md (backward compat) ---
    if decisions_content is None:
        return ConvergenceState(
            consecutive_zero_accepts=0,
            total_assess_cycles=0,
            converged=False,
        )

    headers = list(_ASSESS_HEADER_RE.finditer(decisions_content))

    assess_blocks: list[str] = []
    for i, match in enumerate(headers):
        label = match.group(1).strip().lower()
        if (
            "assess" not in label
            and "quality gate" not in label
            and "brutalist" not in label
        ):
            continue
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(decisions_content)
        assess_blocks.append(decisions_content[start:end])

    consecutive = 0
    for block in assess_blocks:
        # Match actionable classifications: Valid and Security create rework.
        if re.search(r"(?m)^### (Valid|Security):", block):
            break
        consecutive += 1

    return ConvergenceState(
        consecutive_zero_accepts=consecutive,
        total_assess_cycles=len(assess_blocks),
        converged=consecutive >= threshold,
    )


def _filter_memory_for_cycle(
    memory_path: Path,
    cycle_type: str,
    milestones_dir: Path,
    *,
    milestone: str = "",
    cycle_num: int = 0,
) -> str | None:
    """Return filtered memory.md content for *cycle_type*, or None.

    Only includes active patterns whose type is listed in
    ``_MEMORY_TYPE_FILTERS[cycle_type]``.  Patterns with status
    ``fading`` or ``archived`` are excluded.  Returns ``None`` when
    *cycle_type* has no filter entry or when no patterns match.

    When *milestone* is provided, emits a ``memory.patterns_retrieved``
    telemetry event listing the patterns that passed filtering (M35 I1).
    """
    allowed_types = _MEMORY_TYPE_FILTERS.get(cycle_type)
    if allowed_types is None:
        return None

    if not memory_path.exists():
        return None

    # Lazy import to avoid circular dependency
    # (recovery_compaction imports _safe_int from this module).
    from clou.recovery_compaction import (
        _parse_memory,
        _render_memory,
        retrieve_patterns,
    )

    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError:
        return None

    patterns = _parse_memory(content)
    if not patterns:
        return None

    # Gather milestone names for recency scoring.
    all_milestones: list[str] = []
    if milestones_dir.is_dir():
        all_milestones = sorted(
            d.name for d in milestones_dir.iterdir() if d.is_dir()
        )

    # Collect patterns across all allowed types, excluding fading.
    matched: list["MemoryPattern"] = []  # type: ignore[name-defined]
    for type_name in allowed_types:
        retrieved = retrieve_patterns(
            patterns,
            type_filter=type_name,
            all_milestones=all_milestones or None,
        )
        # retrieve_patterns excludes archived/invalidated but allows
        # fading -- we must additionally exclude fading per DB-18 I6.
        matched.extend(p for p in retrieved if p.status == "active")

    if not matched:
        return None

    # M35 I1: emit telemetry event listing retrieved patterns.
    # Only emit when milestone is non-empty to avoid orphaned events (F2).
    if milestone:
        from clou import telemetry

        telemetry.event(
            "memory.patterns_retrieved",
            milestone=milestone,
            cycle_num=cycle_num,
            cycle_type=cycle_type,
            pattern_count=len(matched),
            patterns=[
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                }
                for p in matched
            ],
        )

    return _render_memory(matched)


def score_read_set(
    base_set: list[str],
    telemetry_path: Path | None = None,
    milestone: str | None = None,
    cycle_type: str | None = None,
) -> list[str]:
    """Score and optionally prune a read set based on historical reference density.

    DB-20 Step 3 infrastructure. Currently a pass-through — scoring logic
    will be added once telemetry data accumulates across milestones.
    """
    if telemetry_path is not None and telemetry_path.exists():
        _log.debug(
            "score_read_set: telemetry exists (%s, cycle=%s, %d files) — pass-through",
            milestone, cycle_type, len(base_set),
        )
    return list(base_set)


def determine_next_cycle(
    checkpoint_path: Path,
    milestone: str,
    *,
    decisions_path: Path | None = None,
) -> tuple[str, list[str]]:
    """Read checkpoint and determine the next cycle type and read set.

    When *decisions_path* is provided and the checkpoint requests rework
    (``EXECUTE_REWORK``), convergence is checked: if the last N consecutive
    ASSESS cycles had zero accepted findings, rework is skipped and the
    cycle advances to VERIFY.
    """
    # checkpoint_path: .clou/milestones/{ms}/active/coordinator.md
    # active/ -> ms/ -> milestones/ -> .clou/
    _clou_dir = checkpoint_path.parent.parent.parent.parent
    _milestones_dir = _clou_dir / "milestones"
    _memory_path = _clou_dir / "memory.md"
    _filtered_path = checkpoint_path.parent / "_filtered_memory.md"

    _plan_set = ["milestone.md", "intents.md", "requirements.md", "project.md"]

    # DB-18 I6: filtered memory retrieval replaces raw memory.md inclusion.
    # Write filtered content to active/_filtered_memory.md for the prompt
    # builder to read as a milestone-relative path.
    # Boundary directory for symlink / resolved-path checks (F7).
    _milestone_dir = checkpoint_path.parent.parent

    def _maybe_add_filtered_memory(
        read_set: list[str], cycle_type: str,
        *, _cycle_num: int = 1,
    ) -> None:
        filtered = _filter_memory_for_cycle(
            _memory_path, cycle_type, _milestones_dir,
            milestone=milestone,
            cycle_num=_cycle_num,
        )
        if filtered is not None:
            # F7: Symlink and boundary validation before write (matches
            # coordinator.py assess_summary.md guard).
            if _filtered_path.is_symlink():
                _log.warning(
                    "Refusing to write symlink: %s",
                    _filtered_path,
                )
                return
            if _filtered_path.exists() and not str(
                _filtered_path.resolve()
            ).startswith(str(_milestone_dir.resolve()) + "/"):
                _log.warning(
                    "_filtered_memory.md resolved outside milestone "
                    "boundary: %s",
                    _filtered_path.resolve(),
                )
                return
            _filtered_path.parent.mkdir(parents=True, exist_ok=True)
            _filtered_path.write_text(filtered, encoding="utf-8")
            read_set.append("active/_filtered_memory.md")
        else:
            # Remove stale file from a previous cycle type to prevent
            # leaking PLAN-cycle memory into an EXECUTE cycle (F1).
            try:
                # F7: Symlink and boundary validation before unlink.
                if _filtered_path.is_symlink():
                    _log.warning(
                        "Refusing to unlink symlink: %s",
                        _filtered_path,
                    )
                elif _filtered_path.exists() and not str(
                    _filtered_path.resolve()
                ).startswith(str(_milestone_dir.resolve()) + "/"):
                    _log.warning(
                        "_filtered_memory.md resolved outside milestone "
                        "boundary: %s",
                        _filtered_path.resolve(),
                    )
                else:
                    _filtered_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not checkpoint_path.exists():
        _maybe_add_filtered_memory(_plan_set, "PLAN", _cycle_num=1)
        return "PLAN", _plan_set

    checkpoint = parse_checkpoint(checkpoint_path.read_text())

    match checkpoint.next_step:
        case "ORIENT":
            # M36 I1: ORIENT is the observation-first cycle prepended
            # to every coordinator session. The adaptive read set is
            # intents.md, status.md, every phases/*/execution.md that
            # currently exists (glob resolved at call time so later
            # ORIENT cycles on crash re-entry see the execution.md
            # files written by earlier EXECUTE cycles), and the diff
            # file the orchestrator drops at
            # active/git-diff-stat.txt before each iteration.
            # No memory filter — observation cycles don't need the
            # pattern filter, and run_coordinator re-captures the
            # git diff every cycle regardless.
            _maybe_add_filtered_memory([], "ORIENT", _cycle_num=checkpoint.cycle + 1)
            orient_set: list[str] = ["intents.md", "status.md"]
            phases_dir = _milestone_dir / "phases"
            if phases_dir.is_dir():
                for exec_file in sorted(phases_dir.glob("*/execution.md")):
                    # Use milestone-relative path so build_cycle_prompt
                    # routes through the same `.clou/milestones/{ms}`
                    # prefix as the other cycle types.
                    rel = exec_file.relative_to(_milestone_dir).as_posix()
                    if rel not in orient_set:
                        orient_set.append(rel)
            # Always include the diff file path — the reader tolerates
            # missing / empty content. Listing the path unconditionally
            # keeps the read-set composition deterministic (no
            # branch-dependent size) so telemetry comparisons are
            # stable across cycles.
            orient_set.append("active/git-diff-stat.txt")
            return "ORIENT", orient_set
        case "PLAN":
            _maybe_add_filtered_memory(_plan_set, "PLAN", _cycle_num=checkpoint.cycle + 1)
            return "PLAN", _plan_set
        case "EXECUTE" | "EXECUTE_REWORK" | "EXECUTE_VERIFY":
            # No memory for EXECUTE -- clean up stale filtered file.
            _maybe_add_filtered_memory([], "EXECUTE", _cycle_num=checkpoint.cycle + 1)
            # Defense-in-depth: reject path traversal in current_phase.
            if not _PHASE_RE.match(checkpoint.current_phase):
                _log.warning(
                    "Invalid current_phase %r --- defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
            # Convergence override: if the coordinator requested rework
            # but ASSESS has converged (zero valid findings for N
            # consecutive rounds), skip rework and advance to VERIFY.
            # Primary: structured checkpoint field (no markdown parsing).
            # Fallback: decisions.md parsing (pre-existing milestones).
            # M50 I1: exact-token equality against the structured
            # rework token; the legacy substring match would have
            # spuriously fired on any cycle name containing "rework".
            if checkpoint.next_step == "EXECUTE_REWORK":
                conv = assess_convergence(
                    checkpoint,
                    decisions_content=(
                        decisions_path.read_text()
                        if decisions_path is not None and decisions_path.exists()
                        else None
                    ),
                )
                if conv.converged:
                    _log.info(
                        "ASSESS converged: %d consecutive zero-accept "
                        "rounds --- overriding rework, advancing to VERIFY",
                        conv.consecutive_zero_accepts,
                    )
                    return "VERIFY", [
                        "status.md",
                        "intents.md",
                        "compose.py",
                    ]
            # Read compose.py to find co-layer tasks for gather() groups.
            compose_path = checkpoint_path.parent.parent / "compose.py"
            execute_set = ["status.md", "compose.py"]
            if compose_path.exists():
                try:
                    from clou.graph import get_colayer_tasks
                    source = compose_path.read_text(encoding="utf-8")
                    peers = get_colayer_tasks(source, checkpoint.current_phase)
                    for task_name in peers:
                        execute_set.append(f"phases/{task_name}/phase.md")
                except Exception:
                    execute_set.append(
                        f"phases/{checkpoint.current_phase}/phase.md"
                    )
            else:
                execute_set.append(
                    f"phases/{checkpoint.current_phase}/phase.md"
                )
            # M50 I1 cycle-2 rework (F5/F12): preserve the structured
            # rework/verify discriminator in the returned cycle_type so
            # telemetry payloads carry ``EXECUTE_REWORK``/``EXECUTE_VERIFY``
            # verbatim.  The prior collapse to plain ``"EXECUTE"`` meant
            # ``cycle.judgment`` / ``quality_gate.decision`` / every
            # ``cycle_type=cycle_type`` call site saw the generic string;
            # downstream metrics could not distinguish rework from
            # verification from plain execute.  The structured token
            # returned here flows into the rest of ``coordinator.py``'s
            # telemetry emissions unchanged.
            if checkpoint.next_step in ("EXECUTE_REWORK", "EXECUTE_VERIFY"):
                return checkpoint.next_step, execute_set
            return "EXECUTE", execute_set
        case "REPLAN":
            # No memory for REPLAN -- clean up stale filtered file.
            _maybe_add_filtered_memory([], "REPLAN", _cycle_num=checkpoint.cycle + 1)
            # Re-decomposition: ASSESS detected repeated rework failure
            # on the same task and requested re-planning (ADaPT, §9).
            # Read set mirrors PLAN but adds execution artifacts for
            # the failed phase so the coordinator can see what broke.
            # Defense-in-depth: reject path traversal in current_phase.
            if not _PHASE_RE.match(checkpoint.current_phase):
                _log.warning(
                    "Invalid current_phase %r --- defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", _plan_set
            replan_set = list(_plan_set)
            replan_set.extend([
                "compose.py",
                "decisions.md",
            ])
            if checkpoint.current_phase:
                replan_set.append(
                    f"phases/{checkpoint.current_phase}/phase.md"
                )
                replan_set.append(
                    f"phases/{checkpoint.current_phase}/execution.md"
                )
                # Include any coordinator-generated failure shards
                # (``execution-{slug}.md``).  Post-remolding, these are
                # the only shard files that should exist — worker success
                # paths all converge on the canonical ``execution.md``.
                # Recovery surfaces them so the next ASSESS sees the
                # failure context.
                milestone_dir = checkpoint_path.parent.parent
                phase_dir = milestone_dir / "phases" / checkpoint.current_phase
                if phase_dir.is_dir():
                    for shard in sorted(phase_dir.glob("execution-*.md")):
                        rel = f"phases/{checkpoint.current_phase}/{shard.name}"
                        if rel not in replan_set:
                            replan_set.append(rel)
            return "REPLAN", replan_set
        case "ASSESS":
            # Defense-in-depth: reject path traversal in current_phase.
            if not _PHASE_RE.match(checkpoint.current_phase):
                _log.warning(
                    "Invalid current_phase %r --- defaulting to PLAN",
                    checkpoint.current_phase,
                )
                return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
            # Read compose.py to find co-layer tasks for gather() groups.
            compose_path = checkpoint_path.parent.parent / "compose.py"
            milestone_dir = checkpoint_path.parent.parent
            assess_read: list[str] = []
            if compose_path.exists():
                try:
                    from clou.graph import get_colayer_tasks
                    source = compose_path.read_text(encoding="utf-8")
                    peers = get_colayer_tasks(source, checkpoint.current_phase)
                    for task_name in peers:
                        assess_read.append(
                            f"phases/{task_name}/execution.md"
                        )
                        # Include any coordinator-generated failure shards
                # (``execution-{slug}.md``).  Post-remolding, these are
                # the only shard files that should exist — worker success
                # paths all converge on the canonical ``execution.md``.
                # Recovery surfaces them so the next ASSESS sees the
                # failure context.
                        phase_dir = milestone_dir / "phases" / task_name
                        if phase_dir.is_dir():
                            for shard in sorted(phase_dir.glob("execution-*.md")):
                                rel = f"phases/{task_name}/{shard.name}"
                                if rel not in assess_read:
                                    assess_read.append(rel)
                except Exception:
                    assess_read.append(
                        f"phases/{checkpoint.current_phase}/execution.md"
                    )
            else:
                assess_read.append(
                    f"phases/{checkpoint.current_phase}/execution.md"
                )
            # Always glob failure shards for current phase (fallback covers no-compose case).
            cur_phase_dir = milestone_dir / "phases" / checkpoint.current_phase
            if cur_phase_dir.is_dir():
                for shard in sorted(cur_phase_dir.glob("execution-*.md")):
                    rel = f"phases/{checkpoint.current_phase}/{shard.name}"
                    if rel not in assess_read:
                        assess_read.append(rel)
            assess_read += ["requirements.md", "decisions.md", "assessment.md"]
            # DB-18 I6: include filtered memory for ASSESS cycles.
            _maybe_add_filtered_memory(assess_read, "ASSESS", _cycle_num=checkpoint.cycle + 1)
            return "ASSESS", assess_read
        case "VERIFY":
            _maybe_add_filtered_memory([], "VERIFY", _cycle_num=checkpoint.cycle + 1)
            return "VERIFY", [
                "status.md",
                "intents.md",
                "compose.py",
            ]
        case "EXIT":
            _maybe_add_filtered_memory([], "EXIT", _cycle_num=checkpoint.cycle + 1)
            return "EXIT", [
                "status.md",
                "handoff.md",
                "decisions.md",
            ]
        case "COMPLETE":
            _maybe_add_filtered_memory([], "COMPLETE", _cycle_num=checkpoint.cycle + 1)
            return "COMPLETE", []
        case "HALTED":
            # M49b C1 (closes B5/L1): the pre-dispatch halt gate at
            # ``coordinator.py:1044+`` exits ``run_coordinator`` BEFORE
            # this function is called when an open engine-gated
            # escalation exists.  Reaching this case means the
            # supervisor resolved the escalation but did NOT rewrite
            # ``next_step`` away from HALTED — a B6 supervisor-contract
            # violation.  Raise so the inconsistency is visible rather
            # than silently coercing to PLAN (the previous fall-through
            # behaviour, which erased the halt context and resumed from
            # PLAN with the original phase lost).
            _maybe_add_filtered_memory([], "HALTED", _cycle_num=checkpoint.cycle + 1)
            raise RuntimeError(
                "determine_next_cycle reached with next_step=HALTED but "
                "no open engine-gated escalation. The supervisor "
                "disposition flow MUST rewrite next_step on resolution; "
                "see clou/escalation.py::ENGINE_GATED_CLASSIFICATIONS "
                "and the supervisor re-entry contract (M49b B6)."
            )


def read_cycle_count(checkpoint_path: Path) -> int:
    """Read the cycle count from the checkpoint file.

    Returns 0 if the file does not exist OR is unreadable
    (OSError / UnicodeDecodeError).

    M50 I1 cycle-1 round-2 rework (F1 — CRITICAL): the M49b halt-gate
    (``_apply_halt_gate``) and the F4 vocabulary-migration partial-
    failure halt-filing block both call this helper to label the
    halt with a cycle number.  Both call sites operate on a checkpoint
    path that may have just been added to
    ``migrate_legacy_tokens``'s ``failed`` list — the same file is the
    one we are halting because we could not read or write it.  Re-
    raising the underlying ``OSError`` / ``UnicodeDecodeError`` here
    crashes the engine BEFORE the halt is filed, defeating the very
    safety net the halt-gate exists to provide.

    Defensive contract: on any read failure, return ``0`` as the
    sentinel cycle number.  The label "cycle 0" on a halt escalation
    is acceptable — operators correlate the halt with the file path
    and the timestamp, not the cycle number.  The crash-free guarantee
    is the load-bearing invariant.
    """
    if not checkpoint_path.exists():
        return 0
    try:
        return parse_checkpoint(checkpoint_path.read_text()).cycle
    except (OSError, UnicodeDecodeError):
        _log.warning(
            "read_cycle_count: cannot read %s; "
            "returning sentinel cycle=0",
            checkpoint_path,
            exc_info=True,
        )
        return 0


def read_cycle_outcome(project_dir: Path, milestone: str = "") -> str:
    """Read the outcome of the last completed cycle from the checkpoint.

    Returns the ``next_step`` field which indicates what should happen next.
    Returns ``"PLAN"`` if no checkpoint exists.

    When *milestone* is provided, reads the milestone-scoped checkpoint
    (``milestones/{milestone}/active/coordinator.md``).
    """
    if milestone:
        checkpoint_path = (
            project_dir / ".clou" / "milestones" / milestone / "active" / "coordinator.md"
        )
    else:
        # Legacy fallback -- root-scoped path.
        checkpoint_path = project_dir / ".clou" / "active" / "coordinator.md"
    if not checkpoint_path.exists():
        return "PLAN"
    return parse_checkpoint(checkpoint_path.read_text()).next_step
