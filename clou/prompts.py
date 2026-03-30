"""Prompt loading and construction for Clou's orchestrator.

Public API:
    load_prompt(tier, project_dir, **kwargs) -> str
    build_cycle_prompt(project_dir, milestone, cycle_type, read_set, ...) -> str
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clou.harness import HarnessTemplate

#: Bundled prompt templates shipped with the package — global, not per-project.
_BUNDLED_PROMPTS = Path(__file__).parent / "_prompts"


def load_prompt(tier: str, project_dir: Path, **kwargs: str) -> str:
    """Load and parameterize a tier's system prompt (identity + invariants only).

    Prompts are global — loaded from the bundled ``_prompts/`` directory,
    not from the project's ``.clou/prompts/``.  The full protocol is in
    separate files the agent reads during execution.
    """
    prompt_path = _BUNDLED_PROMPTS / f"{tier}-system.xml"
    prompt = prompt_path.read_text()

    # Inject instance-specific context ({{milestone}}, {{phase}}, etc.)
    for key, value in kwargs.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    return prompt


def _compute_layers(
    tasks: list[dict[str, str]],
    deps: dict[str, list[str]],
) -> list[list[str]]:
    """Group tasks into topological layers for parallel dispatch.

    Layer 0 contains tasks with no dependencies.  Layer N contains tasks
    whose dependencies are all in layers < N.  Tasks in the same layer can
    be dispatched simultaneously via ``gather()``.
    """
    task_names = {t["name"] for t in tasks}
    # in-degree per task (only count deps that are in our task set)
    in_degree: dict[str, int] = {
        name: sum(1 for d in deps.get(name, []) if d in task_names)
        for name in task_names
    }
    # Reverse map: dependency -> list of dependents
    dependents: dict[str, list[str]] = {name: [] for name in task_names}
    for name in task_names:
        for dep in deps.get(name, []):
            if dep in task_names:
                dependents[dep].append(name)

    layers: list[list[str]] = []
    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)

    while queue:
        layer = sorted(queue)  # deterministic ordering
        layers.append(layer)
        next_queue: deque[str] = deque()
        for name in layer:
            for dep in dependents[name]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.append(dep)
        queue = next_queue

    return layers


def build_cycle_prompt(
    project_dir: Path,
    milestone: str,
    cycle_type: str,
    read_set: list[str],
    validation_errors: Sequence[object] | None = None,
    template: HarnessTemplate | None = None,
    dag_data: tuple[list[dict[str, str]], dict[str, list[str]]] | None = None,
    working_tree_state: str | None = None,
    current_phase: str | None = None,
) -> str:
    """Construct targeted prompt for a single cycle.

    The system_prompt contains identity + invariants only (~800-1,200 tokens).
    This initial query provides: cycle type, protocol file pointer, and
    golden context file pointers. The coordinator reads the protocol file
    as its first action, then the golden context files.
    """
    milestone_prefix = f".clou/milestones/{milestone}"
    file_list = "\n".join(
        f"- .clou/{f}" if f == "project.md" else f"- {milestone_prefix}/{f}"
        for f in read_set
    )
    protocol_file = str(_BUNDLED_PROMPTS / f"coordinator-{cycle_type.lower()}.md")

    # Resolved write paths — the exact paths the agent must use.
    # Protocol files say WHAT to write; the cycle prompt says WHERE.
    write_paths = [
        f"- {milestone_prefix}/active/coordinator.md  (checkpoint)",
        f"- {milestone_prefix}/status.md  (progress journal)",
    ]
    phase_name = current_phase or "{phase}"
    if cycle_type == "PLAN":
        write_paths += [
            f"- {milestone_prefix}/compose.py  (task graph)",
            f"- {milestone_prefix}/decisions.md  (judgment log)",
            f"- {milestone_prefix}/phases/{{phase}}/phase.md  (phase specs — one per phase you create)",
        ]
    elif cycle_type == "EXECUTE":
        write_paths += [
            f"- {milestone_prefix}/phases/{phase_name}/execution.md  (agent results)",
        ]
    elif cycle_type in ("ASSESS", "VERIFY"):
        write_paths += [
            f"- {milestone_prefix}/decisions.md  (judgment log)",
        ]
        if cycle_type == "VERIFY":
            write_paths += [
                f"- {milestone_prefix}/phases/verification/execution.md  (perceptual record)",
            ]
    elif cycle_type == "EXIT":
        write_paths += [
            f"- {milestone_prefix}/handoff.md  (prepared handoff)",
        ]
    write_list = "\n".join(write_paths)

    prompt = (
        f"This cycle: {cycle_type}.\n\n"
        f"Read your protocol file first:\n- {protocol_file}\n\n"
        f"Then read these golden context files:\n{file_list}\n\n"
        f"Write your state to these exact paths:\n{write_list}\n\n"
        f"Execute the {cycle_type} protocol."
    )

    if dag_data is not None and cycle_type == "EXECUTE":
        tasks_list, deps_dict = dag_data
        layers = _compute_layers(tasks_list, deps_dict)
        prompt += (
            "\n\n## DAG Context\n"
            "The following dependency graph was extracted from compose.py.\n"
            "Use this for dispatch decisions \u2014 do not re-derive from source.\n\n"
            f"Tasks: {json.dumps(tasks_list)}\n"
            f"Dependencies: {json.dumps(deps_dict)}\n"
            f"Layers: {json.dumps(layers)}"
        )

    if template:
        prompt += f"\n\nActive harness: {template.name}."
        if cycle_type in ("ASSESS", "VERIFY") and template.quality_gates:
            gate_names = [g.mcp_server for g in template.quality_gates]
            prompt += f"\nQuality gates: {', '.join(gate_names)}."

    if validation_errors:
        error_list = "\n".join(f"  - {e}" for e in validation_errors)
        prompt += (
            f"\n\nWARNING: Previous cycle produced malformed golden context. "
            f"Specific errors:\n{error_list}\n"
            f"Ensure all golden context writes conform to schema."
        )

    if working_tree_state:
        if validation_errors:
            # Recovery context — failed cycle left code behind.
            prompt += (
                f"\n\nENVIRONMENT: The working tree has uncommitted changes "
                f"from the previous failed cycle:\n{working_tree_state}\n"
                f"These code changes may be valid work. "
                f"Verify before proceeding — incorporate, fix, or discard."
            )
        else:
            # Proactive context — agent should see the codebase state.
            prompt += (
                f"\n\nENVIRONMENT: Working tree state:\n{working_tree_state}"
            )

    return prompt
