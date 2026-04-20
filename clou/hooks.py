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
        "milestones/*/escalations/*.md",
        "active/supervisor.md",
    ],
    "coordinator": [
        "milestones/*/compose.py",
        # status.md and active/coordinator.md are protocol artifacts —
        # the coordinator writes them via MCP tools (clou_write_checkpoint,
        # clou_update_status) which bypass hook enforcement.  Direct Write
        # is denied to force tool usage.
        "milestones/*/decisions.md",
        "milestones/*/escalations/*.md",
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
    ],
    "verifier": [
        "milestones/*/phases/verification/execution.md",
        "milestones/*/phases/verification/artifacts/*",
        "milestones/*/handoff.md",
    ],
    "brutalist": [
        # assessment.md intentionally absent — writes go through
        # clou_write_assessment so code owns structure (no freeform
        # ## Phase: X / ## Classification Summary section drift).
    ],
    "assess-evaluator": [
        # assessment.md intentionally absent — classifications go
        # through clou_append_classifications.  decisions.md stays
        # freeform (narrative prose, no structural validation).
        "milestones/*/decisions.md",
    ],
}

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

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
_BASH_CLOU_WRITE_RE = re.compile(
    r">{1,2}\s*\S*\.clou\b"                            # redirect to .clou[...]
    r"|\b(?:tee|mv|rm|cp|chmod|touch|install|dd|ln|rsync|tar|xargs)\b.*\.clou\b"  # file-mutating + extractors (verb before .clou)
    r"|\bcd\b.*\.clou\b"                               # cd into .clou (relative mutations evade path-based check)
    r"|\.clou\b.*\|.*\b(?:xargs|tee)\b"                # .clou ... | xargs/tee (pipeline deletion)
    r"|\bsed\b.*-i.*\.clou\b"                          # sed in-place
    r"|\bfind\b.*\.clou\b.*-(?:delete|exec)\b"         # find -delete / find -exec
    r"|\b(?:python|python3|python2|node|nodejs|perl|ruby|bun|deno)\b.*\.clou\b",  # interpreters
)


def _bash_targets_clou(command: str) -> bool:
    """Heuristic: does this Bash command appear to write to .clou/ paths?"""
    return bool(_BASH_CLOU_WRITE_RE.search(command))


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


def _is_clou_path(file_path: str, project_dir: Path) -> str | None:
    """If file_path is inside .clou/, return the relative path. Else None."""
    clou_dir = project_dir / ".clou"
    try:
        resolved = Path(file_path).resolve()
        relative = resolved.relative_to(clou_dir.resolve())
    except ValueError:
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

        # Bash commands that write to .clou/ bypass Write/Edit hooks.
        # Deny them so the agent uses the proper gated tools instead.
        if tool_name == "Bash":
            tool_input: object = input_data.get("tool_input")
            if isinstance(tool_input, dict):
                cmd = tool_input.get("command", "")
                if isinstance(cmd, str) and _bash_targets_clou(cmd):
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
            return _ALLOW

        if tool_name not in _WRITE_TOOLS:
            return _ALLOW

        tool_input = input_data.get("tool_input")
        if not isinstance(tool_input, dict):
            return _ALLOW

        file_path = _extract_file_path(tool_input)
        if file_path is None:
            return _ALLOW

        relative = _is_clou_path(file_path, project_dir)
        if relative is None:
            # Writes outside .clou/ are always allowed.
            return _ALLOW

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
    """
    p_parts = path.split("/")
    pat_parts = pattern.split("/")
    if len(p_parts) != len(pat_parts):
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

    pre_matcher = "Write|Edit|MultiEdit|Bash"
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
