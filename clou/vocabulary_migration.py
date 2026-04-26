"""One-shot rewrite of legacy cycle-type tokens in persisted artifacts.

M50 I1 vocabulary canonicalization: the punctuated legacy tokens
``EXECUTE (rework)`` and ``EXECUTE (additional verification)`` were
replaced by the structured identifiers ``EXECUTE_REWORK`` and
``EXECUTE_VERIFY``.  ``parse_checkpoint`` rejects the legacy forms at
read time (the rejection is the signal that persisted state drifted),
but rejecting alone leaves checkpoints + decisions logs + judgment
files quietly defaulting to ``PLAN`` until a coordinator session
overwrites them.  The migration helper here closes that gap: it scans
the on-disk ``.clou/`` tree and rewrites the legacy tokens to the
structured identifiers atomically per file.

Scope (per phase.md / requirements.md):
    .clou/milestones/*/active/coordinator.md
    .clou/milestones/*/decisions.md
    .clou/milestones/*/judgments/*.md
    .clou/prompts/coordinator-*.md  (mirror of ``clou/_prompts/``;
                                     written once at ``clou_init`` and
                                     subject to the same drift class)

The helper is idempotent — running it twice on already-migrated files
is a no-op and returns zero counts.  Atomicity is per-file via the
shared ``_atomic_write`` helper (mirror of
``clou.coordinator._atomic_write``): a tmp-file rename swap that
cannot leave a half-written artifact under signal interruption.

Cycle 2 rework — field-anchored rewriting (F2/F11/F23/F27/F34):
the first cycle used a context-blind ``str.replace`` over the whole
file body.  That corrupted prose that quoted the legacy token (e.g.
coordinator-orient.md's "``EXECUTE (rework)`` is rejected at parse
time" doc note, or decisions.md entries describing the rename).  The
rework uses anchored regex patterns that match ONLY:

* Structured checkpoint field lines: ``next_step: VALUE``,
  ``pre_orient_next_step: VALUE``, ``pre_halt_next_step: VALUE`` at
  start-of-line with a colon separator.  Matches checkpoints under
  ``active/coordinator.md``.
* Judgment preamble: ``**Next action:** VALUE`` (with optional
  colon-placement and whitespace variants).  Matches
  ``judgments/*.md``.
* Prompt routing directives embedded in templates: ``next_step:
  EXECUTE (rework)`` fragments that appear VERBATIM (outside backticks)
  as copyable user-ready routing guidance.

Prose mentions of the legacy token (quoted strings, backtick-wrapped
identifiers, narrative prose explaining the rename) are left alone.
Prose corruption at ``EXECUTE (rework)`` was F11/F27's concrete proof
of the cycle-1 bug: re-running the migration on coordinator-orient.md
would mangle the legacy-rejection note into self-contradictory prose.

Public API:
    migrate_legacy_tokens(clou_dir: Path) -> dict[str, int | list]
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path

from clou.recovery_checkpoint import _LEGACY_NEXT_STEPS

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field-anchored patterns
# ---------------------------------------------------------------------------

#: Checkpoint structured field pattern.  Matches ``next_step:``,
#: ``pre_orient_next_step:``, ``pre_halt_next_step:`` at start-of-line
#: with optional leading whitespace, a colon-then-whitespace separator,
#: the legacy token value (captured), and end-of-line.  The token must
#: be the WHOLE value; a narrative line like ``next_step: EXECUTE
#: (rework) was requested`` does NOT match because the regex requires
#: the token to consume to end-of-line.
#: M50 I1 cycle-4 rework (F13): ``none`` joins the VALUE
#: alternation so the migration sweeps
#: ``next_step: none`` / ``pre_orient_next_step: none`` /
#: ``pre_halt_next_step: none`` to their structured replacements
#: on disk.  The value is added case-sensitively (exact-match
#: ``none`` only, not ``None`` or ``NONE``) consistent with the
#: cycle-3 "no silent coerce" invariant — non-canonical casings
#: fall through to ``parse_checkpoint``'s rejection path where
#: the user sees an actionable ``Unknown next_step`` warning.
#: Mapping: ``"none" -> "COMPLETE"`` per ``_LEGACY_NEXT_STEPS``.
#:
#: M50 I1 cycle-5 ASSESS / cycle-2 EXECUTE_REWORK (F2/F3 revert):
#: the cycle-4 F13 expansion above SHOULD NOT apply ``none -> COMPLETE``
#: to the stash-field prefixes ``pre_orient_next_step`` and
#: ``pre_halt_next_step``.  Stash slots are RESTORATION TARGETS, not
#: current dispatch values; the parser drops legacy stash tokens
#: (including ``none``) to empty at recovery_checkpoint.py:380-386
#: (pre_orient) and :409-415 (pre_halt) — the canonical
#: stash-handling rule for legacy tokens is "drop the stash" not
#: "rewrite to COMPLETE".  Rewriting the stash field on disk to
#: ``: COMPLETE`` would silently terminate any milestone whose
#: pre-M50 ORIENT-exit / halt-resume restoration target was the
#: legacy ``none`` (parse → COMPLETE → ORIENT-exit restoration
#: routes ``next_step <- COMPLETE`` → milestone terminates).
#:
#: The regex itself stays unchanged (one alternation surface, one
#: idiom) to keep the existing parametrised stash-field-coverage
#: tests valid; the rewrite-vs-skip discrimination happens in
#: :func:`_replace_token` by inspecting the matched prefix and
#: leaving stash-field ``none`` matches as their original text
#: (an idempotent no-op for that single match).  This is "path (b)"
#: from the supervisor's re-scope brief; the alternative ("path (a)",
#: split into two regexes) was ruled mechanically heavier without
#: a behavioural difference — both paths produce the same matched
#: rewrite set.  Choice documented for ASSESS traceability.
_CHECKPOINT_FIELD_RE = re.compile(
    r"(?m)^(?P<prefix>[ \t]*(?P<field>next_step|pre_orient_next_step|"
    r"pre_halt_next_step):[ \t]*)(?P<value>"
    r"EXECUTE \(rework\)|EXECUTE \(additional verification\)|none)"
    r"(?P<suffix>[ \t]*)$",
)

#: Stash-field prefix names that participate in the
#: :data:`_CHECKPOINT_FIELD_RE` alternation but for which the legacy
#: value ``none`` MUST NOT be rewritten to ``COMPLETE``.  See the
#: regex docstring above for rationale (parser drops the stash to
#: empty for legacy tokens; migration must agree round-trip).
_STASH_FIELD_NAMES: frozenset[str] = frozenset({
    "pre_orient_next_step",
    "pre_halt_next_step",
})

#: Judgment preamble pattern.  Matches ``**Next action:** VALUE`` with
#: both ``:`` placements (``**Next action:**`` vs ``**Next action**:``)
#: per :func:`clou.judgment._parse_next_action`'s regex.  VALUE must
#: consume to end-of-line (the whole field value is the token).
#:
#: M50 I1 cycle-4 rework (F2/F19): the prefix label MUST be matched
#: case-INSENSITIVELY, because production's
#: :func:`clou.judgment._parse_next_action` compiles its own
#: ``_NEXT_ACTION_RE`` with ``re.MULTILINE | re.IGNORECASE`` (see
#: ``clou/judgment.py`` line 142-145).  A judgment with
#: ``**next action:** EXECUTE (rework)`` or
#: ``**NEXT ACTION:** EXECUTE (rework)`` is parser-valid legacy
#: drift; cycle-3 dropped the ``re.IGNORECASE`` flag entirely and
#: the migration became asymmetric — parser could read those
#: judgments, ``validate_judgment_fields`` would reject them, but
#: the migration wouldn't rewrite them.  That trifecta wedges a
#: milestone that paused mid-rework.
#:
#: The fix uses scoped flags: ``(?im)`` makes the whole pattern
#: case-INSENSITIVE so the PREFIX label matches all casing
#: variations, then ``(?-i:...)`` around the VALUE group disables
#: IGNORECASE for that span.  Only the canonical uppercase legacy
#: tokens (``EXECUTE (rework)`` / ``EXECUTE (additional
#: verification)``) can match the value — lowercase typos like
#: ``execute (rework)`` fall through, which is exactly the
#: desired behavior (the cycle-3 closure on F1's KeyError regression
#: — lowercase values MUST NOT match lest they feed a lowercased
#: string into the uppercase-keyed ``_LEGACY_NEXT_STEPS`` dict).
#:
#: Case-sensitivity invariant across the three anchored regexes is
#: NOT a blanket "no IGNORECASE" rule — it is a VALUE-group-specific
#: rule: the capture group named ``value`` MUST be case-sensitive
#: on the canonical uppercase legacy tokens.  See the structural
#: invariant test in ``tests/test_vocabulary_migration.py``.
_JUDGMENT_NEXT_ACTION_RE = re.compile(
    r"(?im)^(?P<prefix>[ \t]*\*\*Next\s+action:?\*\*[ \t]*:?[ \t]*)"
    r"(?-i:(?P<value>EXECUTE \(rework\)|EXECUTE \(additional verification\)))"
    r"(?P<suffix>[ \t]*)$",
)

#: Prompt-mirror routing directive pattern.  Matches the routing
#: guidance shape produced by the bundled prompts:
#: ``next_step: EXECUTE (rework)`` (typically inside ``<example>`` or
#: an indented code-block).  The regex requires the whole pattern to be
#: the line content after optional leading whitespace — prose mentions
#: of the legacy token wrapped in backticks (e.g.
#: ```` ``EXECUTE (rework)`` ````) do NOT match this anchor because
#: backticks appear BEFORE the token.
_PROMPT_ROUTING_RE = re.compile(
    r"(?m)^(?P<prefix>[ \t]*next_step:[ \t]*)"
    r"(?P<value>EXECUTE \(rework\)|EXECUTE \(additional verification\))"
    r"(?P<suffix>[ \t]*)$",
)


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* via tmp-file rename (atomic on POSIX).

    Mirror of ``clou.coordinator._atomic_write``.  Kept local to
    ``vocabulary_migration`` so the migration module has no
    coordinator-runtime dependency (the migration runs at session
    start before the coordinator's run loop builds out its own
    helpers, and standalone scripts can import it without dragging
    the SDK).
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


def _replace_token(match: re.Match[str]) -> str:
    """Return the structured replacement for a matched legacy token.

    Preserves the matched prefix (e.g. ``next_step: ``) and suffix
    (trailing whitespace) so only the VALUE field is rewritten.

    M50 I1 cycle-5 ASSESS / cycle-2 EXECUTE_REWORK (F2/F3 revert):
    the cycle-4 F13 expansion swept ``none`` through every prefix in
    :data:`_CHECKPOINT_FIELD_RE`'s alternation; the resulting on-disk
    state for stash fields (``pre_orient_next_step: COMPLETE`` /
    ``pre_halt_next_step: COMPLETE``) is semantically wrong because
    the parser's canonical handling of legacy stash tokens is
    "drop the stash to empty", not "coerce to COMPLETE".  Migration
    must agree with the parser round-trip or pre-M50 milestones with
    ``pre_orient_next_step: none`` on disk get silently terminated.

    Discrimination rule: when the matched prefix names a stash field
    AND the matched value is ``none``, return the original match text
    unchanged (an idempotent no-op for that single match — the parser
    will drop the stash on the next read).  ``next_step: none`` is
    still rewritten to ``next_step: COMPLETE`` (the cycle-4 F13a
    intent for the next_step field stands).  Punctuated legacy tokens
    (``EXECUTE (rework)`` / ``EXECUTE (additional verification)``)
    are still rewritten in every prefix slot — those tokens are
    canonical-vocabulary drift, not stash-restoration targets.
    """
    # ``field`` group is only present on _CHECKPOINT_FIELD_RE matches
    # (judgment + prompt-routing regexes do not name fields by prefix
    # because their alternation is single-prefix).  ``groupdict().get``
    # tolerates the absent group for those regexes.
    field = match.groupdict().get("field")
    value = match.group("value")
    if field in _STASH_FIELD_NAMES and value == "none":
        return match.group(0)
    structured = _LEGACY_NEXT_STEPS[value]
    return f"{match.group('prefix')}{structured}{match.group('suffix')}"


def _anchored_rewrite(content: str, patterns: list[re.Pattern[str]]) -> str:
    """Apply field-anchored substitution.  Prose is not touched.

    Each pattern is an anchored regex over the file content.  The
    rewrite preserves the non-value portion of the match (prefix and
    suffix) so whitespace around the field is preserved byte-for-byte.
    """
    result = content
    for pattern in patterns:
        result = pattern.sub(_replace_token, result)
    return result


def _rewrite_one(
    path: Path,
    patterns: list[re.Pattern[str]],
) -> tuple[bool, Exception | None]:
    """Rewrite legacy cycle-type tokens in a single file via anchored regex.

    *patterns* is the list of field-anchored regexes to apply.  The
    migration dispatches patterns based on artifact family (checkpoint
    field regex for ``coordinator.md``, judgment regex for judgment
    files, etc.) so an unrelated pattern does not fire on the wrong
    file shape.

    Returns ``(rewritten, failure)``:

    * ``(True, None)`` — file content changed and was atomically
      rewritten.
    * ``(False, None)`` — file content unchanged (no matches) — an
      idempotent no-op.
    * ``(False, exc)`` — read or write failed; the file was left
      untouched and the caller should record it in the failure list.

    Content-hash guard: a SHA-256 fingerprint of the original content
    is computed before and after the regex substitution.  If the hash
    is unchanged (pattern matched but substituted to the same text, or
    the file truly had no legacy tokens), the atomic write is skipped
    and ``(False, None)`` is returned.  Belt-and-suspenders against a
    pattern regression silently overwriting a clean file.
    """
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning(
            "vocabulary_migration: cannot read %s; skipping: %s",
            path, exc,
        )
        return False, exc

    original_hash = hashlib.sha256(original.encode("utf-8")).digest()
    rewritten = _anchored_rewrite(original, patterns)
    rewritten_hash = hashlib.sha256(rewritten.encode("utf-8")).digest()

    if rewritten_hash == original_hash:
        return False, None

    # Belt-and-suspenders: verify the content actually changed at
    # string-equality level (hash collision is vanishingly unlikely
    # but the idempotency contract is load-bearing).
    if rewritten == original:
        return False, None

    try:
        _atomic_write(path, rewritten)
    except OSError as exc:
        _log.warning(
            "vocabulary_migration: cannot write %s; leaving original: %s",
            path, exc,
        )
        return False, exc
    return True, None


def migrate_legacy_tokens(clou_dir: Path) -> dict[str, object]:
    """Sweep persisted ``.clou/`` artifacts and rewrite legacy cycle-type tokens.

    Scans four artifact families under ``{clou_dir}``:

    * ``milestones/*/active/coordinator.md`` — the live checkpoints.
      Field-anchored on ``next_step: / pre_orient_next_step: /
      pre_halt_next_step:`` lines.
    * ``milestones/*/decisions.md`` — the running decision logs.
      Field-anchored on the same checkpoint-style fields — any rework
      routing tradeoff in decisions.md that quotes a checkpoint field
      will use those names; prose mentions are left alone.
    * ``milestones/*/judgments/*.md`` — per-cycle ORIENT judgments.
      Field-anchored on ``**Next action:** VALUE`` preambles.
    * ``prompts/coordinator-*.md`` — the per-project prompt mirror
      written once at ``clou_init``; the bundled
      ``clou/_prompts/coordinator-*.md`` files are the source of truth
      and have already been updated to the structured tokens, but the
      copied mirror does not auto-resync — it falls under the same
      one-shot rewrite as the milestone artifacts.  Field-anchored on
      the routing-directive shape (``next_step: EXECUTE (rework)``) so
      narrative prose explaining the rename is left alone.

    Returns a dict with per-family counts PLUS a ``failed`` list of
    paths that raised during read/write:

    .. code-block:: python

        {
            "checkpoints": N, "decisions": N, "judgments": N,
            "prompts": N,
            "failed": [Path, ...],  # empty on full-success
        }

    Failed paths are also surfaced via a distinct
    ``vocabulary_migration.partial_failure`` telemetry event at the
    calling site (see ``coordinator.py:run_coordinator``).  Aggregate
    counts alone cannot distinguish "nothing needed migration" from
    "half the files failed"; the ``failed`` list closes that gap.

    Missing directories are tolerated — when ``{clou_dir}/milestones``
    or ``{clou_dir}/prompts`` does not exist the helper skips that
    family (fresh install with no milestones yet, or no prompt mirror
    materialised).
    """
    counts: dict[str, object] = {
        "checkpoints": 0,
        "decisions": 0,
        "judgments": 0,
        "prompts": 0,
        "failed": [],
    }
    failed: list[Path] = counts["failed"]  # type: ignore[assignment]

    # Patterns by artifact family.  Checkpoint-style field regex
    # applies to both checkpoints and decisions (which sometimes quote
    # the structured field verbatim for routing context).  Judgment
    # regex applies only to judgment files.  Prompt regex applies only
    # to the mirror.
    checkpoint_patterns = [_CHECKPOINT_FIELD_RE]
    decisions_patterns = [_CHECKPOINT_FIELD_RE]
    judgment_patterns = [_JUDGMENT_NEXT_ACTION_RE]
    prompt_patterns = [_PROMPT_ROUTING_RE]

    milestones_dir = clou_dir / "milestones"
    if milestones_dir.is_dir():
        for milestone_path in sorted(milestones_dir.iterdir()):
            if not milestone_path.is_dir():
                continue

            # Active checkpoint.
            cp_path = milestone_path / "active" / "coordinator.md"
            if cp_path.is_file():
                rewritten, failure = _rewrite_one(cp_path, checkpoint_patterns)
                if rewritten:
                    counts["checkpoints"] = int(counts["checkpoints"]) + 1
                if failure is not None:
                    failed.append(cp_path)

            # Decisions log.
            dec_path = milestone_path / "decisions.md"
            if dec_path.is_file():
                rewritten, failure = _rewrite_one(dec_path, decisions_patterns)
                if rewritten:
                    counts["decisions"] = int(counts["decisions"]) + 1
                if failure is not None:
                    failed.append(dec_path)

            # Per-cycle judgment files.
            judgments_dir = milestone_path / "judgments"
            if judgments_dir.is_dir():
                for jpath in sorted(judgments_dir.glob("*.md")):
                    rewritten, failure = _rewrite_one(jpath, judgment_patterns)
                    if rewritten:
                        counts["judgments"] = int(counts["judgments"]) + 1
                    if failure is not None:
                        failed.append(jpath)

    # Per-project prompt mirror.  The bundle in ``clou/_prompts/`` is
    # the source of truth; ``clou_init`` copies it to
    # ``.clou/prompts/`` once with ``_write_if_missing`` semantics.
    # Drift-class same as the milestone artifacts: the mirror does not
    # auto-resync, so the migration runs exactly once over it.
    prompts_dir = clou_dir / "prompts"
    if prompts_dir.is_dir():
        for ppath in sorted(prompts_dir.glob("coordinator-*.md")):
            rewritten, failure = _rewrite_one(ppath, prompt_patterns)
            if rewritten:
                counts["prompts"] = int(counts["prompts"]) + 1
            if failure is not None:
                failed.append(ppath)

    return counts
