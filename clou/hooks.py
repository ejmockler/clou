"""Hook enforcement for Clou's orchestrator.

Three enforcement mechanisms:
1. Write boundary enforcement (PreToolUse) — each tier can only write
   to specific .clou/ paths.
2. Artifact validation (PostToolUse) — after any tier writes to a
   golden context artifact, validate its form.  compose.py gets AST
   validation (structural tier); formed artifacts (e.g. intents.md)
   get ArtifactForm validation (narrative tier, DB-14).
3. Transcript capture (PostToolUse) — coordinator-tier only, captures
   all subagent tool calls into TranscriptStore for UI inspection.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clou.harness import HarnessTemplate

_log = logging.getLogger(__name__)

from clou.graph import validate, validate_coverage

type HookCallback = Callable[..., Awaitable[dict[str, Any]]]

WRITE_PERMISSIONS: dict[str, list[str]] = {
    "supervisor": [
        "project.md",
        "roadmap.md",
        "requests.md",
        "understanding.md",
        "memory.md",
        "milestones/*/milestone.md",
        "milestones/*/intents.md",
        "milestones/*/requirements.md",
        # escalations/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_file_escalation so code owns the
        # EscalationForm schema (DB-21 remolding).  Direct Write is
        # hook-denied.
        # judgments/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_write_judgment so code owns the
        # JudgmentForm schema. Direct Write is hook-denied (DB-14).
        "active/supervisor.md",
    ],
    "coordinator": [
        "milestones/*/compose.py",
        # status.md and active/coordinator.md are protocol artifacts —
        # the coordinator writes them via MCP tools (clou_write_checkpoint,
        # clou_update_status) which bypass hook enforcement.  Direct Write
        # is denied to force tool usage.
        "milestones/*/decisions.md",
        # escalations/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_file_escalation so code owns the
        # EscalationForm schema (DB-21 remolding).  Direct Write is
        # hook-denied.
        # judgments/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_write_judgment so code owns the
        # JudgmentForm schema. Direct Write is hook-denied (DB-14).
        "milestones/*/phases/*/phase.md",
    ],
    "worker": [
        "milestones/*/phases/*/execution.md",
        # execution-*.md is intentionally absent.  Those are
        # coordinator-generated failure shards, written in-process by
        # Python (bypassing the hook).  Granting workers permission to
        # write them was a live drift vector: a worker with stale
        # briefing could freeform a slug and the hook would allow it,
        # reintroducing the slug-drift class of bug.
        # judgments/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_write_judgment so code owns the
        # JudgmentForm schema. Direct Write is hook-denied (DB-14).
    ],
    "verifier": [
        "milestones/*/phases/verification/execution.md",
        "milestones/*/phases/verification/artifacts/*",
        "milestones/*/handoff.md",
        # judgments/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_write_judgment so code owns the
        # JudgmentForm schema. Direct Write is hook-denied (DB-14).
    ],
    "brutalist": [
        # assessment.md intentionally absent — writes go through
        # clou_write_assessment so code owns structure (no freeform
        # ## Phase: X / ## Classification Summary section drift).
        # judgments/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_write_judgment so code owns the
        # JudgmentForm schema. Direct Write is hook-denied (DB-14).
    ],
    "assess-evaluator": [
        # assessment.md intentionally absent — classifications go
        # through clou_append_classifications.  decisions.md stays
        # freeform (narrative prose, no structural validation).
        # judgments/*.md intentionally absent — writes go through
        # mcp__clou_coordinator__clou_write_judgment so code owns the
        # JudgmentForm schema. Direct Write is hook-denied (DB-14).
        "milestones/*/decisions.md",
    ],
}

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

# Read-only tools that carry a path argument but never materialise a
# file at that path.  Passed through without fail-closed treatment (F10).
# Everything NOT in this set and NOT in _WRITE_TOOLS is assumed to be a
# potential writer when it carries a .clou/-targeted path.
_READ_ONLY_TOOLS = frozenset({
    "Read",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "WebFetch",
    "WebSearch",
})

# Tools that carry a file/notebook path but are not in the classic
# Write/Edit/MultiEdit set.  On macOS/APFS and other case-insensitive
# filesystems, ``.CLOU/foo`` resolves to the same inode as ``.clou/foo``,
# so any tool that materialises a file against a path must run the same
# .clou/ containment check — fail-closed for unenumerated writers (F10).
#
# F15: the enumeration must widen as the SDK surface grows.  The three
# original keys (``file_path``, ``path``, ``notebook_path``) do not
# cover tools that use ``output_path``, ``target``, ``dest``,
# ``destination``, or ``to`` — the common SDK conventions for "where
# should I write this?".  Any future tool that lands with one of those
# keys and isn't in ``_WRITE_TOOLS`` would have slipped past
# containment.  The hook body also performs a last-ditch scan of every
# string value in ``tool_input`` for unknown tools (see the
# ``_extract_any_clou_path`` helper) so unfamiliar path-shape keys
# still funnel into the escalation-and-tier gate.
_PATH_KEYS = (
    "file_path",
    "path",
    "notebook_path",
    "output_path",
    "target",
    "dest",
    "destination",
    "to",
)

# Argument-schema fragment embedded in PreToolUse deny reasons for
# escalation writes (F32).  The agent that hits the hook must know how
# to retry via the MCP tool without a second round-trip to introspect
# tool definitions.  Kept as a module constant so tests can assert on
# a specific shape.
_ESCALATION_TOOL_SCHEMA_HINT = (
    "Required arguments: classification (str), issue (str), title (str). "
    "Optional: context (str), evidence (str), recommendation (str), "
    "options (array of {label: str, description: str})."
)

# Tier-aware escalation deny reason (F17).  The original module-level
# constant named only ``clou_file_escalation`` — correct for coordinator
# and worker tiers, but misleading for the supervisor: in the DB-21
# remolding supervisors OWN resolution (clou_resolve_escalation) AND
# FILE new escalations (clou_file_escalation, per F4).  A supervisor
# that hits the deny branch for a direct Write on ``escalations/x.md``
# needs both tool names in the message so it can choose the correct
# retry path instead of guessing.  Kept as a small helper so callers
# can pass the closure-captured ``tier`` and so tests can assert on
# the tier-dependent shape.
def _escalation_deny_reason(tier: str) -> str:
    """Return the PreToolUse deny reason for escalation writes.

    ``tier == "supervisor"`` → name both the filing tool
    (clou_file_escalation, F4 gave the supervisor filing authority) AND
    the resolution tool (clou_resolve_escalation, supervisor-only).
    Any other tier → name clou_file_escalation only (that is the only
    MCP pathway available to coordinator/worker tiers).
    """
    if tier == "supervisor":
        return (
            "Escalation files must be written via "
            "mcp__clou_coordinator__clou_file_escalation "
            "(to file a new escalation) or "
            "mcp__clou_supervisor__clou_resolve_escalation "
            "(to resolve an existing one) — "
            "direct Write is denied to preserve the "
            "EscalationForm schema (DB-21 remolding). "
            + _ESCALATION_TOOL_SCHEMA_HINT
        )
    return (
        "Escalation files must be written via "
        "mcp__clou_coordinator__clou_file_escalation — "
        "direct Write is denied to preserve the "
        "EscalationForm schema (DB-21 remolding). "
        + _ESCALATION_TOOL_SCHEMA_HINT
    )


def _judgment_deny_reason(tier: str) -> str:
    """Return the PreToolUse deny reason for judgment writes.

    Judgments are per-cycle ORIENT artifacts with a strict
    ``JudgmentForm`` schema (next_action, rationale, evidence_paths,
    expected_artifact, cycle).  The sole legal writer is
    ``mcp__clou_coordinator__clou_write_judgment``; direct
    ``Write``/``Edit``/``MultiEdit`` is hook-denied so code owns the
    schema (DB-14 ArtifactForm pattern / DB-21 remolding).

    Kept tier-aware, mirroring ``_escalation_deny_reason`` (M41 F17),
    so future per-tier reasoning (e.g. a non-coordinator tier getting
    told "this tool is not available at your tier") can land here
    without a second round-trip.  At M36 every tier sees the same
    message — only the coordinator tier surfaces the tool, but every
    tier benefits from the actionable naming so drifted agents get
    immediate self-correction.
    """
    return (
        "Judgment files must be written via "
        "mcp__clou_coordinator__clou_write_judgment — direct Write "
        "is denied to preserve the JudgmentForm schema (DB-14 "
        "ArtifactForm / DB-21 remolding). Required fields: "
        "next_action, rationale, evidence_paths, expected_artifact, "
        "cycle."
    )


def _proposal_deny_reason(tier: str) -> str:
    """Return the PreToolUse deny reason for milestone proposal writes.

    C3: coordinator files proposals via the MCP tool; supervisor
    dispositions them via a separate MCP tool.  Direct ``Write`` is
    denied to preserve the ``MilestoneProposalForm`` schema and to
    keep the authority boundary (coordinator proposes, supervisor
    decides) enforced structurally, not by prompt convention.
    """
    if tier == "supervisor":
        return (
            "Milestone proposal files must be managed via "
            "mcp__clou_supervisor__clou_list_proposals "
            "(to review filed proposals) or "
            "mcp__clou_supervisor__clou_dispose_proposal "
            "(to accept / reject / mark superseded) — direct Write "
            "is denied to preserve the MilestoneProposalForm schema "
            "(DB-21 remolding). Only the MCP tool enforces evidence "
            "+ scope + dependency invariants."
        )
    return (
        "Milestone proposal files must be written via "
        "mcp__clou_coordinator__clou_propose_milestone — direct "
        "Write is denied to preserve the MilestoneProposalForm "
        "schema (DB-21 remolding). Use this tool for cross-cutting "
        "architectural work that falls outside the current milestone's "
        "scope; the supervisor dispositions proposals separately."
    )

# Heuristic patterns for detecting Bash commands that mutate .clou/ paths.
# These close the gap where shell redirects and polyglot interpreters bypass
# Write/Edit hook enforcement.  Not a sandbox — a motivated caller can still
# craft a bypass — but defense-in-depth against the common mutation surfaces.
# The MCP tool clou_remove_artifact is the blessed deletion pathway for
# supervisors; all other tiers rely on Write/Edit for any .clou/ mutation.
#
# ``\.clou\b`` matches ``.clou``, ``.clou/``, ``.clou/foo``, but NOT
# ``.cloud`` / ``.cloudy`` / etc., so we catch bare-directory operations
# (``rm -rf .clou``) as well as path-prefixed ones.
#
# Compiled with ``re.IGNORECASE`` (F9): case-insensitive filesystems
# (macOS HFS+/APFS default, Windows NTFS default) resolve ``.CLOU/foo``
# to the same inode as ``.clou/foo``, so a case-sensitive pattern let
# ``echo x > .CLOU/milestones/m/escalations/f.md`` slip through.
# Interpreter list is DEFENSE-IN-DEPTH, not a sandbox (F23 — a
# motivated caller can still bypass by composing ``.clou`` from
# non-literal fragments, or by launching an interpreter whose name we
# don't enumerate yet).  The `\.clou` literal check on the command
# string is the primary gate; the interpreter alternation adds another
# layer so ``<generator> | bash < .clou/...`` pipelines are still
# refused when ``.clou`` appears anywhere in the pipeline.
_BASH_CLOU_WRITE_RE = re.compile(
    r">{1,2}\s*\S*\.clou\b"                            # redirect to .clou[...]
    r"|\b(?:tee|mv|rm|cp|chmod|touch|install|dd|ln|rsync|tar|xargs)\b.*\.clou\b"  # file-mutating + extractors (verb before .clou)
    r"|\bcd\b.*\.clou\b"                               # cd into .clou (relative mutations evade path-based check)
    r"|\.clou\b.*\|.*\b(?:xargs|tee)\b"                # .clou ... | xargs/tee (pipeline deletion)
    r"|\bsed\b.*-i.*\.clou\b"                          # sed in-place
    r"|\bfind\b.*\.clou\b.*-(?:delete|exec)\b"         # find -delete / find -exec
    # Interpreter alternation (F9 defense-in-depth).  Ordering matters:
    # the longer variants (python3, pwsh, powershell, tclsh, nodejs) MUST
    # precede shorter prefixes (python, pw?, node) so the regex commits
    # to the longer match first.  The leading anchor
    # ``(?:^|[\s;&|(])`` requires the interpreter token to sit at a
    # COMMAND boundary --- not just any word boundary --- because a bare
    # ``\b`` would let short flags like ``-r`` or ``-R`` match the
    # single-letter R interpreter (``grep -r`` shouldn't deny).  The
    # trailing ``\b`` still bounds the interpreter against word
    # characters on the right.  See F9 cycle-2 test
    # ``test_bash_r_interpreter_word_boundary``.
    r"|(?:^|[\s;&|(])(?:python3|python2|python"
    r"|nodejs|node"
    r"|powershell|pwsh"
    r"|bash|zsh|dash|ksh|fish|sh"
    r"|perl|ruby|bun|deno"
    r"|awk|lua|php|tclsh|swift|julia|R)\b.*\.clou\b"   # interpreters (F9: inclusive shell list)
    r"|(?:^|[\s;&|(])[a-z_][a-z0-9_]*=\S*\.clou\b",    # VAR=.clou/... (variable-inlined write target, F2)
    re.IGNORECASE,
)


def _bash_targets_clou(command: str) -> bool:
    """Heuristic: does this Bash command appear to write to .clou/ paths?

    Casefolds the command before matching so obfuscation via case
    (``.CLOU``, ``.Clou``) does not bypass the heuristic on
    case-insensitive filesystems (F9).
    """
    return bool(_BASH_CLOU_WRITE_RE.search(command.casefold()))


# Judgment-targeted Bash detection (M36).  The generic ``_bash_targets_clou``
# already denies shell redirects into ``.clou/`` but with a generic message.
# When a command touches a judgments path specifically, we want the deny
# reason to name ``mcp__clou_coordinator__clou_write_judgment`` so drifted
# agents get self-corrective advice instead of the fallback
# "Use the Write or Edit tool instead" (which is itself not permitted for
# judgments).  Pattern anchors on the ``judgments/`` segment anywhere after
# ``.clou/`` AND an ``.md`` extension; casefolded command input means the
# literal ``judgments`` / ``.md`` substrings stay lowercase.
_BASH_CLOU_JUDGMENT_RE = re.compile(
    r"\.clou\b.*?/judgments/[^/\s'\"]+\.md\b",
    re.IGNORECASE,
)


def _bash_targets_judgment(command: str) -> bool:
    """Heuristic: does the Bash command appear to touch a judgments/*.md path?

    Used only after ``_bash_targets_clou`` has already matched, to pick
    the judgment-specific deny reason.  Casefolds for the same reason
    as ``_bash_targets_clou`` — ``.CLOU/.../JUDGMENTS/FOO.MD`` must
    match on case-insensitive filesystems.
    """
    return bool(_BASH_CLOU_JUDGMENT_RE.search(command.casefold()))


# M49a: worker probe-pattern suppression.
#
# The M36 trace (cycle 3, implementer aa99644215fb6c0a4) showed a
# 113-min worker session that consumed 164 tool uses on backgrounded
# pytest invocations + helper poll-for-output subprocesses that never
# returned visible output.  Three complementary rules:
#
# 1. PreToolUse deny on ``Bash(command~=pytest, run_in_background=True)``
#    from worker tier — forces foreground pytest with tight -k selection
#    or explicit status writes to execution.md.
# 2. PreToolUse deny on ``Task`` from worker tier — DB-10 stigmergy-only
#    defense-in-depth; workers communicate via the filesystem.
# 3. PostToolUse duplicate-signature tracker — inject a <system-reminder>
#    on the third near-duplicate Bash command in a session, instructing
#    the worker to abort the probe loop and write an interim summary.
#
# The threshold (3) aligns with ``_STALENESS_THRESHOLD`` in
# ``coordinator.py:120`` and DB-17's ``consecutive_zero_valid`` >= 2
# convergence pattern — Clou's architecture uses 3 as its convergence
# threshold throughout.  Session-scoped, not time-windowed: M36 showed
# steady-state thrash (9+ near-duplicates across 113 min), not a burst.
#
# Pre-anchor is a true shell command boundary, NOT bare whitespace.
# A whitespace pre-anchor would false-positive on ``grep pytest src/``
# (pytest as an argument, not a command).  Boundaries are: string
# start, ``;`` / ``&`` / ``&&`` / ``|`` / ``||`` / ``(``.  The
# ``[;&|(]`` character class matches any of the single chars; `&&`
# and `||` are handled via the character class matching the final
# char of the repeat (the engine greedily consumes the first char
# and then `[;&|(]` matches the second — works for both ``&&`` and
# ``&``).  Optional whitespace between the boundary and ``pytest``
# permits spaced forms (``&& pytest``) as well as compact (``&&pytest``).
#
# Covers direct ``pytest`` + the common Python runner prefixes:
# ``python`` / ``python3`` / ``python3.12``... with ``-m pytest``,
# and the tool wrappers (``uv run pytest``, ``poetry run pytest``,
# ``pipenv run pytest``, ``pdm run pytest``, ``hatch run pytest``,
# ``rye run pytest``, ``tox -e <env>``).  Bare ``tox`` also matches
# — the worker's foreground alternative is to run the same tox env
# without ``run_in_background=True``.  ``python<major>.<minor>`` via
# ``python\d+(?:\.\d+)?`` so ``python3.13 -m pytest`` matches too.
_PYTEST_RE = re.compile(
    r"(?:^|[;&|(])\s*"
    r"(?:"
        r"pytest"
        r"|python\d*(?:\.\d+)?\s+-m\s+pytest"
        r"|(?:uv|poetry|pipenv|pdm|hatch|rye)\s+run\s+pytest"
        r"|tox(?:\s+-e\s+\S+)?"
    r")\b",
    re.IGNORECASE,
)

# Paths that vary per-invocation but whose presence in a command
# otherwise indicates the "same" probe.  Normalized to a placeholder
# so ``pytest /tmp/abc/test_x.py`` and ``pytest /tmp/xyz/test_x.py``
# hash to the same signature.
_TRANSIENT_PATH_RE = re.compile(
    r"(?:/private)?/tmp/[A-Za-z0-9_.\-/]+"
    r"|(?:/private)?/var/folders/[A-Za-z0-9_.\-/]+",
)

_WORKER_DUPLICATE_THRESHOLD = 3


def _bash_is_pytest(command: str) -> bool:
    """Heuristic: does this Bash command invoke pytest?

    Matches ``pytest ...``, ``python -m pytest ...``, and
    ``python3 -m pytest ...``.  Used only to scope the worker
    run-in-background deny to the specific pathology surfaced in M36
    (backgrounded pytest without visible output); any other worker
    Bash usage is unaffected.
    """
    return bool(_PYTEST_RE.search(command))


def _normalize_bash_for_hash(command: str) -> str:
    """Canonicalize a Bash command for duplicate-signature detection.

    Collapses runs of whitespace and replaces transient path fragments
    (``/tmp/...``, ``/var/folders/...``) with a placeholder so commands
    that differ only in per-invocation temp paths hash identically.
    Returns the canonical string (callers hash at their layer).
    """
    normalized = _TRANSIENT_PATH_RE.sub("<tmp>", command.strip())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _effective_tier(
    input_data: dict[str, Any],
    lead_tier: str,
    agent_tier_map: dict[str, str] | None,
) -> str:
    """Return the effective tier for a tool call.

    Mirrors the ad-hoc ``reason_tier`` derivation used by the
    escalation / judgment / proposal deny branches: when ``agent_type``
    is present and maps to a known subagent tier, return that tier;
    otherwise return the lead tier.  Extracted as a helper for M49a's
    new deny branches and the Bash duplicate-signature hook.
    """
    agent_type = input_data.get("agent_type")
    if (
        isinstance(agent_type, str)
        and agent_tier_map
        and agent_type in agent_tier_map
    ):
        return agent_tier_map[agent_type]
    return lead_tier


@dataclass(frozen=True, slots=True)
class HookConfig:
    """A hook configuration pairing a matcher pattern with callbacks."""

    matcher: str | None
    hooks: list[HookCallback]


def _extract_file_path(tool_input: dict[str, Any]) -> str | None:
    """Extract file_path from a tool's input dict."""
    path: object = tool_input.get("file_path")
    if isinstance(path, str):
        return path
    return None


def _extract_any_path(tool_input: dict[str, Any]) -> str | None:
    """Extract the first present path-like field from a tool's input dict.

    Covers file_path (Write/Edit/MultiEdit), path (generic), and
    notebook_path (NotebookEdit).  Used by the fail-closed dispatcher
    (F10) so unenumerated write tools that carry a path argument still
    hit the same .clou/ containment gate.
    """
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_clou_path(file_path: str, project_dir: Path) -> str | None:
    """If file_path is inside .clou/, return the relative path. Else None.

    Relative paths are anchored against *project_dir*, not the process
    CWD (F7).  The SDK spawns supervisor/coordinator sessions with
    ``cwd=work_dir`` which may differ from *project_dir* (e.g. when the
    user invokes clou from a subdirectory or a sibling project that
    shares the global ``.clou/`` workspace).  Anchoring against
    ``Path.cwd()`` — the pre-DB-21 behaviour — let a supervisor whose
    CWD was outside ``project_dir`` bypass containment with a relative
    path like ``.clou/milestones/m/escalations/x.md`` that resolved to
    a sibling directory and fell through as "outside .clou/".

    Symlink handling (F11).  ``Path.resolve()`` follows symlinks, so a
    pre-existing symlink inside ``.clou/`` whose target escapes the
    tree would make the resolved path look "outside .clou/" and fall
    through as ``None`` — allowing a Write against the symlink to
    materialise arbitrary files via the link target.  The fix is two
    layers:
    (1) Anchor containment on the REQUESTED path (before ``resolve()``)
        — if the agent handed us ``.clou/x``, we answer based on
        ``.clou/x`` regardless of what ``resolve()`` would do.
    (2) Reject if any resolved segment of the path (including the
        file itself) is a symlink whose target lies outside ``.clou/``.
        This mirrors ``clou_remove_artifact``'s ``target.is_symlink()``
        rejection at ``orchestrator.py:688``.  The semantics: "any
        symlink inside ``.clou/`` that escapes ``.clou/`` is a
        containment break"; we return ``None`` to force the caller's
        fail-closed branch.
    Paths with no symlinks present behave exactly as before.
    """
    clou_dir = project_dir / ".clou"
    path_obj = Path(file_path)
    if not path_obj.is_absolute():
        path_obj = project_dir / path_obj

    # Build the REQUESTED absolute path without dereferencing symlinks.
    # ``Path.absolute()`` collapses ``..`` via ``os.path.normpath`` only
    # on POSIX — use the explicit form so ``milestones/../project.md``
    # is caught before being handed to resolve().
    import os

    requested_abs = Path(os.path.normpath(str(path_obj)))
    clou_abs = Path(os.path.normpath(str(clou_dir)))

    # Layer 1: compare the requested path against the requested .clou/
    # prefix.  This catches the common case and protects against
    # symlink-escape tricks where resolve() would hand back a path
    # outside .clou/.
    try:
        relative = requested_abs.relative_to(clou_abs)
    except ValueError:
        # Fall back to resolved-form containment only when the requested
        # path doesn't lie under the requested clou_dir.  This keeps
        # backwards compatibility with callers who passed a path through
        # a legitimate symlink that happens to land inside the real
        # .clou/ tree (rare but historically supported).
        try:
            resolved = path_obj.resolve()
            relative = resolved.relative_to(clou_dir.resolve())
        except (ValueError, OSError):
            return None

    # Layer 2: refuse to follow symlinks inside .clou/.  Walk the
    # requested path from .clou/ downward — if any segment is a symlink
    # AND its target escapes .clou/, return None so the hook's
    # fail-closed branch fires.
    current = clou_abs
    for segment in relative.parts:
        current = current / segment
        if current.is_symlink():
            try:
                link_target = current.resolve()
                link_target.relative_to(clou_dir.resolve())
            except (ValueError, OSError):
                # Symlink whose target is outside .clou/ — containment
                # break.  Return None, which the hook treats as "not
                # inside .clou/" and the fail-closed path picks up from
                # there.
                return None

    return str(relative)


def _make_pre_hook(
    tier: str,
    project_dir: Path,
    *,
    milestone: str | None = None,
    agent_tier_map: dict[str, str] | None = None,
    permissions: dict[str, list[str]] | None = None,
) -> HookCallback:
    """Create a PreToolUse hook callback for write boundary enforcement.

    When *agent_tier_map* is provided, subagent writes are enforced against
    the subagent's own tier permissions (looked up via ``agent_type`` in the
    hook input).  This closes the gap where ``AgentDefinition`` has no
    ``hooks`` field — the coordinator's hook enforces on behalf of its
    subagents using the correct tier.
    """
    lead_scoped = _scoped_permissions(tier, milestone, permissions)

    # Pre-compute scoped permissions for each subagent tier.
    sub_scoped: dict[str, list[str]] = {}
    if agent_tier_map:
        for agent_type, sub_tier in agent_tier_map.items():
            sub_scoped[agent_type] = _scoped_permissions(
                sub_tier, milestone, permissions,
            )

    _ALLOW: dict[str, Any] = {
        "hookSpecificOutput": {"hookEventName": "PreToolUse"},
    }

    async def pre_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name: object = input_data.get("tool_name")
        if not isinstance(tool_name, str):
            return _ALLOW

        # M49a: Task tool denied from worker tier (DB-10 stigmergy-only
        # defense-in-depth).  Worker ``AgentSpec.tools`` does not list
        # Task today, but SDK default-tool leakage has surfaced in the
        # wild (M36 showed 22+ short-ID task_ids swept at worker
        # session end — those were background Bash, not Task, but the
        # mechanism for Task invocation from a worker session would
        # look identical to the coordinator's TaskStartedMessage
        # stream).  Close the hypothetical leak here so workers cannot
        # escape stigmergic coordination.
        if tool_name == "Task":
            effective_tier = _effective_tier(
                input_data, tier, agent_tier_map,
            )
            if effective_tier == "worker":
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "Workers may not invoke the Task tool "
                            "(DB-10 stigmergy-only). Coordinate via "
                            "the filesystem: write status to "
                            "phases/{phase}/execution.md and let the "
                            "coordinator route the next cycle from "
                            "your summary."
                        ),
                    },
                }

        # Bash commands that write to .clou/ bypass Write/Edit hooks.
        # Deny them so the agent uses the proper gated tools instead.
        if tool_name == "Bash":
            tool_input: object = input_data.get("tool_input")
            if isinstance(tool_input, dict):
                cmd = tool_input.get("command", "")
                if isinstance(cmd, str) and _bash_targets_clou(cmd):
                    # Judgment-specific deny reason (M36).  When the command
                    # targets a ``judgments/*.md`` path under ``.clou/``,
                    # surface the actionable MCP-tool name so the agent
                    # doesn't retry with Write (which is ALSO denied by the
                    # judgment-specific Write branch below).  Escalation
                    # retains generic Bash deny — escalation bash tests
                    # assert deny-only without reason shape (M41 F9).
                    if _bash_targets_judgment(cmd):
                        agent_type = input_data.get("agent_type")
                        if (
                            isinstance(agent_type, str)
                            and agent_tier_map
                            and agent_type in agent_tier_map
                        ):
                            reason_tier = agent_tier_map[agent_type]
                        else:
                            reason_tier = tier
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": (
                                    _judgment_deny_reason(reason_tier)
                                ),
                            },
                        }
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                "Shell commands that write to .clou/ are not "
                                "allowed. Use the Write or Edit tool instead."
                            ),
                        },
                    }
                # M49a: deny backgrounded pytest from worker tier.
                # Rationale: M36 cycle-3 implementer burned 113 min on
                # a Bash(run_in_background=True) pytest + helper-poll
                # pattern that never yielded visible output in the
                # session (22+ helper subprocesses got swept at
                # session end with tool_uses=0, duration_ms=0).  Force
                # foreground pytest with a narrow ``-k`` selector so
                # test output is actually read; if the test run is
                # genuinely long, the worker should write an interim
                # ``phases/{phase}/execution.md`` summary and let the
                # coordinator route the next cycle.
                if isinstance(cmd, str) and _bash_is_pytest(cmd):
                    bg = tool_input.get("run_in_background")
                    effective_tier = _effective_tier(
                        input_data, tier, agent_tier_map,
                    )
                    if bg is True and effective_tier == "worker":
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": (
                                    "Backgrounded pytest from worker "
                                    "tier is denied. The "
                                    "background+poll-for-output "
                                    "pattern does not converge in "
                                    "this sandbox (see M36 cycle-3 "
                                    "trace). Run pytest in the "
                                    "foreground with a narrow ``-k`` "
                                    "selector, or write an interim "
                                    "summary to "
                                    "phases/{phase}/execution.md and "
                                    "let the coordinator route the "
                                    "next cycle."
                                ),
                            },
                        }
            return _ALLOW

        # Read-only tools (Read, Glob, Grep, LS, etc.) never materialise
        # a file at their path argument.  They pass through without
        # containment treatment.
        if tool_name in _READ_ONLY_TOOLS:
            return _ALLOW

        tool_input = input_data.get("tool_input")
        if not isinstance(tool_input, dict):
            return _ALLOW

        # Extract any path-like field — file_path (Write/Edit/MultiEdit),
        # path (generic), or notebook_path (NotebookEdit).  Unenumerated
        # tools that still carry a path must hit the same containment gate
        # (F10) — closed enumeration on _WRITE_TOOLS used to let
        # NotebookEdit and any future SDK write tool fall through and
        # materialise a file inside .clou/ unchecked.
        any_path = _extract_any_path(tool_input)
        if any_path is None:
            return _ALLOW

        relative = _is_clou_path(any_path, project_dir)
        if relative is None:
            # Writes outside .clou/ are always allowed regardless of tool.
            return _ALLOW

        # Escalation files must go through the MCP writer tool.  This
        # check fires BEFORE the tier-scoped permission match so every
        # tier — including those without any prior grant — receives the
        # actionable error naming the MCP tool (R10).  Uses
        # ``_strict_segment_match`` instead of ``fnmatch`` (F22): fnmatch
        # allows ``*`` to cross ``/`` boundaries, so a nested path would
        # over-match the same primitive that the allowlist uses — keeping
        # allowlist vs denylist glob semantics consistent is part of
        # R6 permission-audit consistency.
        if (
            _strict_segment_match(relative, "milestones/*/escalations/*.md")
            or _strict_segment_match(
                relative, "milestones/*/escalations/*/*.md",
            )
        ):
            # F17: the lead tier may differ from the subagent tier when
            # the hook fires on behalf of a subagent (agent_type set).
            # Prefer the subagent's tier for the reason so the message
            # names tools the subagent can actually reach; fall back to
            # the lead tier otherwise.
            agent_type = input_data.get("agent_type")
            if (
                isinstance(agent_type, str)
                and agent_tier_map
                and agent_type in agent_tier_map
            ):
                reason_tier = agent_tier_map[agent_type]
            else:
                reason_tier = tier
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _escalation_deny_reason(
                        reason_tier,
                    ),
                },
            }

        # M36 (DB-14): judgments are per-cycle ORIENT artifacts with a
        # strict schema (next_action, rationale, evidence_paths,
        # expected_artifact, cycle).  Direct Write is denied across
        # every tier — the sole legal writer is
        # ``mcp__clou_coordinator__clou_write_judgment``.  Check fires
        # BEFORE the tier-scoped permission match so even a tier with a
        # hypothetical broad ``milestones/**/*`` grant (none exists
        # today, but workers historically have had generous scopes
        # during EXECUTE) receives the actionable error naming the
        # MCP tool.  Uses ``_strict_segment_match`` for the same R6
        # allowlist-vs-denylist glob parity reason as the escalation
        # branch (M41 F22): a prefix-absorbed path like
        # ``archive/milestones/m1/judgments/foo.md`` should NOT trigger
        # the judgment-specific deny — it falls through to the tier
        # match instead, keeping the deny scoped to the canonical
        # layout ``milestones/*/judgments/*.md``.
        if _strict_segment_match(relative, "milestones/*/judgments/*.md"):
            agent_type = input_data.get("agent_type")
            if (
                isinstance(agent_type, str)
                and agent_tier_map
                and agent_type in agent_tier_map
            ):
                reason_tier = agent_tier_map[agent_type]
            else:
                reason_tier = tier
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _judgment_deny_reason(
                        reason_tier,
                    ),
                },
            }

        # C3: milestone proposals must go through the MCP writer tool.
        # Mirrors the escalation pattern: explicit deny with an
        # actionable error naming the MCP tools, independent of
        # tier-scoped permissions.  Proposals live at
        # ``.clou/proposals/*.md`` (project-scoped -- not under any
        # milestone -- because they're about milestones that don't
        # exist yet).
        if _strict_segment_match(relative, "proposals/*.md"):
            agent_type = input_data.get("agent_type")
            if (
                isinstance(agent_type, str)
                and agent_tier_map
                and agent_type in agent_tier_map
            ):
                reason_tier = agent_tier_map[agent_type]
            else:
                reason_tier = tier
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _proposal_deny_reason(
                        reason_tier,
                    ),
                },
            }

        # Fail-closed for unenumerated tools that target .clou/ (F10).
        # Known write tools proceed to the tier-scoped permission match;
        # every other tool name with a path in .clou/ is denied here.
        if tool_name not in _WRITE_TOOLS:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Tool {tool_name!r} is not an authorised writer for "
                        f".clou/{relative}. Use Write/Edit/MultiEdit (tier "
                        "permissions apply) or an MCP tool that owns this "
                        "artifact's schema."
                    ),
                },
            }

        # Determine which tier's permissions to enforce.
        # If agent_type is present (subagent context), enforce that tier's
        # permissions.  Unknown agent types are DENIED (fail-closed) — not
        # granted the lead agent's broader permissions.
        agent_type = input_data.get("agent_type")
        if isinstance(agent_type, str) and agent_type and sub_scoped:
            # Subagent context with a configured tier map — enforce per-tier.
            if agent_type in sub_scoped:
                scoped = sub_scoped[agent_type]
                effective_tier = agent_tier_map[agent_type]  # type: ignore[index]
            else:
                # Unknown subagent type — block all .clou/ writes.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Unknown agent type '{agent_type}' cannot write "
                            f"to .clou/{relative}"
                        ),
                    },
                }
        else:
            # No agent_type, no tier map, or non-string — lead agent's permissions.
            scoped = lead_scoped
            effective_tier = tier

        if any(fnmatch.fnmatch(relative, p) for p in scoped):
            return _ALLOW

        permitted = "\n".join(f"  - .clou/{p}" for p in scoped[:8])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{effective_tier} tier cannot write to .clou/{relative}\n"
                    f"Permitted paths:\n{permitted}"
                ),
            },
        }

    return pre_hook


def _make_post_hook(
    project_dir: Path,
    template: HarnessTemplate | None = None,
) -> HookCallback:
    """Create a PostToolUse hook for golden context artifact validation.

    Two enforcement mechanisms in one hook:
    1. **compose.py** — AST validation via ``graph.validate`` (structural tier).
    2. **Formed artifacts** — ``validate_artifact_form`` against the
       ``ArtifactForm`` from the template (narrative tier, DB-14).

    Returns ``additionalContext`` with specific errors so the agent
    gets immediate feedback and can fix the artifact in-session.
    This is LLM-Modulo applied to golden context writes.
    """
    from clou.validation import validate_artifact_form

    # Pre-compute artifact forms lookup: filename → ArtifactForm.
    _artifact_forms: dict[str, object] = {}
    if template is not None:
        for name, form in template.artifact_forms.items():
            _artifact_forms[f"{name}.md"] = form

    _PASS: dict[str, Any] = {
        "hookSpecificOutput": {"hookEventName": "PostToolUse"},
    }

    async def post_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name: object = input_data.get("tool_name")
        if not isinstance(tool_name, str) or tool_name not in _WRITE_TOOLS:
            return _PASS

        tool_input: object = input_data.get("tool_input")
        if not isinstance(tool_input, dict):
            return _PASS

        file_path = _extract_file_path(tool_input)
        if file_path is None:
            return _PASS

        resolved = Path(file_path).resolve()
        clou_dir = project_dir / ".clou"
        try:
            relative = resolved.relative_to(clou_dir.resolve())
        except ValueError:
            return _PASS

        # --- compose.py: AST validation (structural tier) ---
        if resolved.name == "compose.py":
            try:
                source = resolved.read_text()
            except OSError:
                _log.warning("PostToolUse: cannot read %s for validation", resolved)
                return _PASS
            # Read intents.md early — used for both structural validation
            # (width proportionality) and intent coverage checking.
            intents_path = resolved.parent / "intents.md"
            intents_source_text: str | None = None
            if intents_path.is_file():
                try:
                    intents_source_text = intents_path.read_text()
                except OSError:
                    _log.warning(
                        "PostToolUse: cannot read %s", intents_path,
                    )

            all_results = validate(
                source, intents_source=intents_source_text,
            )
            # Separate structural errors from advisory warnings.
            # Advisory warnings are informational and should not
            # trigger "Fix the call graph." rejection.
            # Severity is carried by ValidationResult, not inferred
            # from message substrings.
            errors = [r for r in all_results if r.severity == "error"]
            advisories = [r for r in all_results if r.severity == "advisory"]
            if errors:
                error_list = "\n".join(r.message for r in errors)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            "Composition errors:\n"
                            + error_list
                            + "\nFix the call graph."
                        ),
                    }
                }

            # Structural validation passed — check intent coverage.
            if intents_source_text is not None:
                coverage_results = validate_coverage(
                    source, intents_source_text,
                )
                # Separate coverage errors from advisory feedback.
                # Advisory messages (untraced tasks, missing docstrings)
                # should not trigger "Intent-coverage errors." rejection.
                # Severity is carried by ValidationResult.
                coverage_errors = [
                    r for r in coverage_results
                    if r.severity == "error"
                ]
                if coverage_errors:
                    error_list = "\n".join(
                        f"- {r.message}" for r in coverage_errors
                    )
                    ctx = (
                        "Intent-coverage errors:\n"
                        + error_list
                        + "\nVerify compose.py covers all intents."
                    )
                    # Append topology advisories if any.
                    if advisories:
                        adv_list = "\n".join(
                            f"- {a.message}" for a in advisories
                        )
                        ctx += (
                            "\n\nTopology advisories:\n"
                            + adv_list
                        )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": ctx,
                        }
                    }

            # No structural errors, no coverage errors.
            # Surface topology advisories as non-blocking feedback.
            if advisories:
                adv_list = "\n".join(f"- {a.message}" for a in advisories)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            "Topology advisories (non-blocking):\n"
                            + adv_list
                            + "\nConsider whether wider decomposition "
                            "is appropriate for this milestone's scope."
                        ),
                    }
                }

            return _PASS

        # --- Formed artifacts: ArtifactForm validation (narrative tier) ---
        filename = resolved.name
        form = _artifact_forms.get(filename)
        if form is None:
            return _PASS

        try:
            content = resolved.read_text()
        except OSError:
            _log.warning("PostToolUse: cannot read %s for form validation", resolved)
            return _PASS

        findings = validate_artifact_form(content, form, str(relative))
        if not findings:
            return _PASS

        warning_list = "\n".join(f"- {f.message}" for f in findings)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Artifact form warnings for {filename}:\n"
                    + warning_list
                    + f"\n\nExpected form: each criterion follows "
                    + f"'{form.criterion_template or 'no template'}'. "
                    + "Rewrite criteria as observable outcomes."
                ),
            }
        }

    return post_hook


def _make_transcript_hook() -> HookCallback:
    """Create a PostToolUse hook that captures tool calls to TranscriptStore.

    Uses ``matcher=None`` to capture ALL tool calls (reads, writes, shell,
    etc.), not just write tools like the existing PostToolUse hook.

    Only subagent tool calls are recorded (those with a truthy ``agent_id``
    in the hook input).  The coordinator's own tool calls do not carry
    ``agent_id`` and are silently skipped.
    """
    from clou.transcript import (
        TranscriptEntry,
        get_store,
        truncate_input,
        truncate_output,
    )

    _PASS: dict[str, Any] = {
        "hookSpecificOutput": {"hookEventName": "PostToolUse"},
    }

    async def transcript_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # Only capture subagent tool calls (agent_id present).
        agent_id = input_data.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return _PASS

        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        tool_response = input_data.get("tool_response")

        # Normalize tool_response to string and truncate.
        if tool_response is None:
            response_str = ""
        elif isinstance(tool_response, str):
            response_str = tool_response
        else:
            response_str = str(tool_response)

        response_str = truncate_output(response_str)

        import time

        entry = TranscriptEntry(
            tool_name=str(tool_name),
            tool_input=truncate_input(tool_input) if isinstance(tool_input, dict) else {},
            tool_response=response_str,
            timestamp=time.monotonic(),
            tool_use_id=str(tool_use_id or ""),
        )

        store = get_store()
        store.record(agent_id, entry)
        return _PASS

    return transcript_hook


def _make_bash_duplicate_hook(
    tier: str,
    agent_tier_map: dict[str, str] | None = None,
) -> HookCallback:
    """PostToolUse: track near-duplicate Bash commands from worker tier.

    Complements the PreToolUse pytest-background deny by catching the
    broader "same command N times" probe pattern that isn't pytest-
    specific (polling for file contents, sleeping and re-reading, etc.).
    On the third near-duplicate signature within a session, return
    ``additionalContext`` with a ``<system-reminder>`` telling the
    worker to abort the probe loop and write an interim summary.

    Session-scoped via a closure-captured dict (one coordinator cycle
    = one hook factory invocation = one fresh tracker). The threshold
    (``_WORKER_DUPLICATE_THRESHOLD = 3``) aligns with the
    ``_STALENESS_THRESHOLD`` and DB-17 convergence patterns.  No time
    window — M36 showed 9+ near-duplicates across 113 min, a
    steady-state thrash that a time window would miss.
    """
    # Per-subagent-instance command-signature counts; survives across
    # tool calls within one coordinator cycle's SDK session.  Keyed by
    # ``agent_id`` (instance identifier — the SDK injects this into
    # PostToolUse input, same field the transcript hook uses at
    # ``_make_transcript_hook``) rather than ``agent_type`` (role
    # label).  Two concurrent implementers share a type but have
    # distinct agent_ids; keying by id prevents sibling poisoning
    # where Worker A's probe count leaks into Worker B's reminders.
    # Fallback chain: agent_id -> agent_type -> "_lead" so
    # pre-SDK-0.1 hook inputs (which may omit agent_id) still track
    # cleanly, just at role granularity.  Signatures are hashed via
    # ``_normalize_bash_for_hash`` so per-invocation temp paths don't
    # bump the count.
    _counts: dict[str, dict[str, int]] = {}

    _PASS: dict[str, Any] = {
        "hookSpecificOutput": {"hookEventName": "PostToolUse"},
    }

    async def duplicate_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return _PASS

        effective_tier = _effective_tier(
            input_data, tier, agent_tier_map,
        )
        if effective_tier != "worker":
            return _PASS

        tool_input = input_data.get("tool_input")
        if not isinstance(tool_input, dict):
            return _PASS
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            return _PASS

        sig = _normalize_bash_for_hash(cmd)
        agent_id_raw = input_data.get("agent_id")
        agent_type_raw = input_data.get("agent_type")
        if isinstance(agent_id_raw, str) and agent_id_raw:
            agent_key = agent_id_raw
        elif isinstance(agent_type_raw, str) and agent_type_raw:
            agent_key = agent_type_raw
        else:
            agent_key = "_lead"
        bucket = _counts.setdefault(agent_key, {})
        bucket[sig] = bucket.get(sig, 0) + 1
        count = bucket[sig]

        if count >= _WORKER_DUPLICATE_THRESHOLD:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "<system-reminder>\n"
                        f"You have issued {count} near-duplicate Bash "
                        "commands in this session "
                        "(normalized signatures match). If you are in "
                        "a probe loop — running tests repeatedly, "
                        "polling for output that isn't materializing, "
                        "waiting for a background process that already "
                        "finished — abort the loop now. Write an "
                        "interim summary to "
                        "phases/{phase}/execution.md describing what "
                        "you learned, what's still unknown, and what "
                        "the next concrete action would be. The "
                        "coordinator will route the next cycle from "
                        "your summary; you do not need to resolve "
                        "every unknown in one session.\n"
                        "</system-reminder>"
                    ),
                },
            }

        return _PASS

    return duplicate_hook


def _make_halt_reminder_hook() -> HookCallback:
    """PostToolUse: inject a system-reminder after a successful
    ``clou_halt_trajectory`` invocation.

    M49b Layer 2 of the mid-cycle abort defence.  After the halt
    tool returns success, the coordinator LLM has up to
    ``_MAX_TURNS=200`` turns remaining in the current cycle — enough
    to dispatch more workers, write contradictory checkpoints, or
    continue probing the broken trajectory.  This hook injects a
    ``<system-reminder>`` via ``additionalContext`` telling the
    coordinator that its ONLY remaining action is to checkpoint
    and exit.  Prompt contract (Layer 1) names the same rule;
    this hook tightens it at the SDK boundary.

    Fires only on the exact tool name
    ``mcp__clou_coordinator__clou_halt_trajectory``.  Skips on any
    other tool name, on error responses (``is_error=True``), and on
    non-dict tool_input / tool_response payloads.  Reminder text is
    constant across invocations (idempotent — multiple halt filings
    within one cycle produce identical reminders; the SDK
    multiplexes turns).
    """
    _PASS: dict[str, Any] = {
        "hookSpecificOutput": {"hookEventName": "PostToolUse"},
    }
    _REMINDER_TEXT = (
        "<system-reminder>\n"
        "You have just filed a trajectory halt via "
        "clou_halt_trajectory.  Your ONLY remaining action in this "
        "cycle MUST be to call clou_write_checkpoint with "
        "cycle_outcome=HALTED_PENDING_REVIEW and next_step=HALTED, "
        "then exit the cycle.  Do NOT dispatch further workers.  Do "
        "NOT invoke other tools.  Do NOT attempt rework 'for one "
        "more try' — that is exactly the pathology the halt exists "
        "to prevent.  The supervisor will consult the user and "
        "dispose the halt via clou_resolve_escalation; the next "
        "dispatch restores the correct downstream state per the "
        "user's choice (continue-as-is / re-scope / abandon).\n"
        "</system-reminder>"
    )

    async def halt_reminder_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            input_data.get("tool_name")
            != "mcp__clou_coordinator__clou_halt_trajectory"
        ):
            return _PASS

        # Don't nag about a failed tool call — the coordinator
        # should retry or fall through to another path.  Only
        # fire when the halt actually landed.
        tool_response = input_data.get("tool_response")
        if isinstance(tool_response, dict) and tool_response.get("is_error"):
            return _PASS

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": _REMINDER_TEXT,
            },
        }

    return halt_reminder_hook


def _scoped_permissions(
    tier: str,
    milestone: str | None,
    permissions: dict[str, list[str]] | None = None,
) -> list[str]:
    """Return write permission patterns, optionally scoped to a milestone.

    When *milestone* is provided, ``milestones/*`` patterns are narrowed to
    ``milestones/{milestone}`` — preventing a coordinator from writing to
    another milestone's golden context.

    When *permissions* is provided, uses that dict instead of the
    module-level ``WRITE_PERMISSIONS`` (for template-driven configuration).
    """
    patterns = (permissions or WRITE_PERMISSIONS).get(tier, [])
    if milestone is None:
        return patterns

    scoped: list[str] = []
    for p in patterns:
        if p.startswith("milestones/*/"):
            scoped.append(p.replace("milestones/*/", f"milestones/{milestone}/", 1))
        else:
            scoped.append(p)
    return scoped


#: Maps AgentDefinition keys to their enforcement tier.
#: Used by the coordinator's PreToolUse hook to enforce subagent-specific
#: write boundaries (since AgentDefinition has no ``hooks`` field).
AGENT_TIER_MAP: dict[str, str] = {
    "implementer": "worker",
    "verifier": "verifier",
    "brutalist": "brutalist",
    "assess-evaluator": "assess-evaluator",
}


# ---------------------------------------------------------------------------
# Orchestrator cleanup scope — separate from agent tier write permissions.
# These patterns define which .clou/ root-level files the orchestrator is
# allowed to delete when processing handoff.md obsolete-file flags.
# ---------------------------------------------------------------------------

CLEANUP_SCOPE: tuple[str, ...] = (
    "*.example",    # example/template files
    "*.bak",        # backup files
    "*.old",        # renamed-aside files
)


def is_cleanup_allowed(relative_path: str) -> bool:
    """Check if a flagged path is within the orchestrator's cleanup scope.

    Args:
        relative_path: Path relative to ``.clou/`` (e.g., ``"roadmap.py.example"``).

    Returns:
        True if the path matches :data:`CLEANUP_SCOPE` patterns and is a
        root-level file (no directory separators).
    """
    if not relative_path:
        return False
    # Only root-level files — no nested paths.
    if "/" in relative_path or "\\" in relative_path:
        return False
    return any(fnmatch.fnmatch(relative_path, pat) for pat in CLEANUP_SCOPE)


# ---------------------------------------------------------------------------
# Supervisor cleanup scope — intermediate artifacts the supervisor may
# remove via the clou_remove_artifact MCP tool.
#
# Distinct from CLEANUP_SCOPE (orchestrator / root-level) and from
# WRITE_PERMISSIONS (write-auth ≠ delete-auth).  Protocol artifacts —
# milestone.md, intents.md, requirements.md, compose.py, status.md,
# handoff.md, decisions.md, phase.md, and root-level golden context —
# are deliberately out of scope: they encode decisions and history that
# must remain immutable.
# ---------------------------------------------------------------------------

SUPERVISOR_CLEANUP_SCOPE: tuple[str, ...] = (
    "milestones/*/phases/*/execution.md",       # worker execution (default)
    "milestones/*/phases/*/execution-*.md",     # worker execution (parallel)
    "milestones/*/phases/*/artifacts/*",        # verifier artifacts
    "milestones/*/assessment.md",               # brutalist report (supersedable)
    "milestones/*/escalations/*.md",            # stale escalations
)


def _strict_segment_match(path: str, pattern: str) -> bool:
    """Left-anchored, segment-aware glob match.

    Unlike :func:`fnmatch.fnmatch`, this treats ``*`` as matching a
    single path component (no ``/`` crossing).  Unlike
    ``PurePosixPath.match`` (which is right-anchored and lets prefix
    components slip in), this is left-anchored: segment counts must
    be equal, and every segment must match its corresponding pattern
    segment.

    Empty segments (leading ``/``, trailing ``/``, consecutive ``//``)
    are rejected defensively (F10).  ``fnmatch.fnmatchcase("", "*")``
    returns ``True`` — without this guard a malformed path like
    ``milestones//escalations/foo.md`` would match
    ``milestones/*/escalations/*.md`` where the empty middle segment
    silently absorbs the ``*``.  This matches the same guard
    ``supervisor_cleanup_allowed`` already applied, keeping the two
    neighbour call sites of this helper semantically identical.
    """
    p_parts = path.split("/")
    pat_parts = pattern.split("/")
    if len(p_parts) != len(pat_parts):
        return False
    if "" in p_parts:
        return False
    return all(
        fnmatch.fnmatchcase(pp, patp)
        for pp, patp in zip(p_parts, pat_parts)
    )


def supervisor_cleanup_allowed(relative_path: str) -> bool:
    """Check if a path is within the supervisor's cleanup scope.

    Args:
        relative_path: Path relative to ``.clou/`` (e.g.,
            ``"milestones/foo/phases/bar/execution-baz.md"``).

    Returns:
        True if the path matches :data:`SUPERVISOR_CLEANUP_SCOPE`.

    Uses :func:`_strict_segment_match` for left-anchored, segment-aware
    globbing — ``*`` matches a single path component, and prefix
    components cannot sneak a path into scope.  Example:
    ``archive/milestones/m1/assessment.md`` does NOT match
    ``milestones/*/assessment.md`` (prefix ``archive/``);
    ``milestones/m1/deep/nested/assessment.md`` does NOT match
    (too many segments).  This keeps the scope tight against future
    artifact-layout evolution (``.clou/archive/``, etc.).

    Unlike :func:`is_cleanup_allowed`, nested paths are permitted —
    intermediate artifacts live inside milestone subdirectories.
    Callers must resolve paths absolutely and confirm ``.clou/``
    containment before invoking this helper; ``..`` segments and
    backslash separators are rejected defensively here too.
    """
    if not relative_path:
        return False
    if "\\" in relative_path:
        return False
    parts = relative_path.split("/")
    if ".." in parts:
        return False
    # Reject empty segments (leading /, trailing /, consecutive //).
    # fnmatch("", "*") is True, so without this an empty segment in a
    # malformed path slips into scope.
    if "" in parts:
        return False
    return any(
        _strict_segment_match(relative_path, pat)
        for pat in SUPERVISOR_CLEANUP_SCOPE
    )


def build_hooks(
    tier: str,
    project_dir: Path,
    *,
    milestone: str | None = None,
    template: HarnessTemplate | None = None,
) -> dict[str, list[HookConfig]]:
    """Build tier-specific hooks for write boundary enforcement.

    Args:
        tier: Agent tier (supervisor, coordinator, worker, verifier).
        project_dir: Project root directory.
        milestone: When provided, write boundary patterns are scoped to this
            milestone — ``milestones/*`` becomes ``milestones/{milestone}``.
        template: When provided, write permissions and agent tier map are
            derived from the template instead of module-level constants.

    Returns a hooks dict in the format expected by ClaudeAgentOptions:
    {
        "PreToolUse": [HookConfig(matcher=..., hooks=[callback])],
        "PostToolUse": [HookConfig(matcher=..., hooks=[callback])],
    }
    """
    # Derive permissions and tier map from template or module-level constants.
    if template is not None:
        from clou.harness import template_agent_tier_map

        permissions: dict[str, list[str]] | None = template.write_permissions
        agent_map = (
            template_agent_tier_map(template) if tier == "coordinator" else None
        )
    else:
        permissions = None  # use WRITE_PERMISSIONS default
        agent_map = AGENT_TIER_MAP if tier == "coordinator" else None

    # matcher=None routes ALL tool names through the hook body so it can
    # exercise the fail-closed `tool_name not in _WRITE_TOOLS` branch and
    # the escalation-path deny on every tool, including NotebookEdit and
    # any future SDK write tool (F3).  A narrower regex (e.g.
    # "Write|Edit|MultiEdit|Bash") would bypass the hook for tools whose
    # names fell outside the alternation, re-opening the drift surface
    # F10's fail-closed branch was added to close.  The hook body itself
    # early-allows read-only tools via `_READ_ONLY_TOOLS`, so the cost is
    # one dict lookup per tool call.
    pre_matcher = None
    post_matcher = "Write|Edit|MultiEdit"
    hooks: dict[str, list[HookConfig]] = {
        "PreToolUse": [
            HookConfig(
                matcher=pre_matcher,
                hooks=[
                    _make_pre_hook(
                        tier,
                        project_dir,
                        milestone=milestone,
                        agent_tier_map=agent_map,
                        permissions=permissions,
                    )
                ],
            ),
        ],
        # Artifact validation for every tier that writes to .clou/.
        # compose.py → AST (structural); formed artifacts → ArtifactForm
        # (narrative, DB-14).  Immediate feedback in the agent's session.
        "PostToolUse": [
            HookConfig(
                matcher=post_matcher,
                hooks=[_make_post_hook(project_dir, template=template)],
            ),
        ],
    }

    # Transcript capture: coordinator-only, matcher=None captures ALL tools.
    # Additive alongside the artifact validation PostToolUse hook above.
    if tier == "coordinator":
        hooks["PostToolUse"].append(
            HookConfig(
                matcher=None,
                hooks=[_make_transcript_hook()],
            ),
        )
        # M49a: worker probe-pattern detection.  Tracks near-duplicate
        # Bash signatures from worker subagents within this cycle's
        # session; emits a <system-reminder> on the third repetition.
        # Scoped to coordinator-tier hook setup because worker tool
        # calls flow through the coordinator's SDK session as
        # subagents; agent_tier_map resolves the effective tier.
        hooks["PostToolUse"].append(
            HookConfig(
                matcher=None,
                hooks=[_make_bash_duplicate_hook(tier, agent_map)],
            ),
        )
        # M49b Layer 2: halt-tool reminder.  After
        # clou_halt_trajectory returns success, inject a
        # <system-reminder> telling the coordinator its only legal
        # next action is checkpoint-and-exit.  Prompt contract
        # (Layer 1) makes the same demand; this hook tightens it at
        # the SDK boundary.  Coordinator-tier only because the halt
        # verb is coordinator-tier only.
        hooks["PostToolUse"].append(
            HookConfig(
                matcher=None,
                hooks=[_make_halt_reminder_hook()],
            ),
        )

    return hooks


def to_sdk_hooks(
    hook_configs: dict[str, list[HookConfig]],
) -> Any:
    """Convert internal HookConfig to SDK HookMatcher.

    Returns a hooks dict compatible with ClaudeAgentOptions.hooks.
    Uses Any return because our HookCallback type is intentionally
    broader than the SDK's union type for testability.
    """
    from typing import cast

    from claude_agent_sdk import HookMatcher

    return {
        event: [
            HookMatcher(matcher=cfg.matcher, hooks=cast(Any, cfg.hooks))
            for cfg in configs
        ]
        for event, configs in hook_configs.items()
    }
