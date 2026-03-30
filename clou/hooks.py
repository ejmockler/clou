"""Hook enforcement for Clou's orchestrator.

Two enforcement mechanisms:
1. Write boundary enforcement (PreToolUse) — each tier can only write
   to specific .clou/ paths.
2. Artifact validation (PostToolUse) — after any tier writes to a
   golden context artifact, validate its form.  compose.py gets AST
   validation (structural tier); formed artifacts (e.g. intents.md)
   get ArtifactForm validation (narrative tier, DB-14).
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clou.harness import HarnessTemplate

_log = logging.getLogger(__name__)

from clou.graph import validate

type HookCallback = Callable[..., Awaitable[dict[str, Any]]]

WRITE_PERMISSIONS: dict[str, list[str]] = {
    "supervisor": [
        "project.md",
        "roadmap.md",
        "requests.md",
        "understanding.md",
        "milestones/*/milestone.md",
        "milestones/*/intents.md",
        "milestones/*/requirements.md",
        "milestones/*/escalations/*.md",
        "active/supervisor.md",
    ],
    "coordinator": [
        "milestones/*/compose.py",
        "milestones/*/status.md",
        "milestones/*/decisions.md",
        "milestones/*/escalations/*.md",
        "milestones/*/phases/*/phase.md",
        "milestones/*/active/coordinator.md",
    ],
    "worker": [
        "milestones/*/phases/*/execution.md",
    ],
    "verifier": [
        "milestones/*/phases/verification/execution.md",
        "milestones/*/phases/verification/artifacts/*",
        "milestones/*/handoff.md",
    ],
    "assessor": [
        "milestones/*/assessment.md",
    ],
}

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


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
        if not isinstance(tool_name, str) or tool_name not in _WRITE_TOOLS:
            return _ALLOW

        tool_input: object = input_data.get("tool_input")
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
            errors = validate(source)
            if errors:
                error_list = "\n".join(errors)
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
    "assessor": "assessor",
}


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

    matcher = "Write|Edit|MultiEdit"
    hooks: dict[str, list[HookConfig]] = {
        "PreToolUse": [
            HookConfig(
                matcher=matcher,
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
                matcher=matcher,
                hooks=[_make_post_hook(project_dir, template=template)],
            ),
        ],
    }

    return hooks
